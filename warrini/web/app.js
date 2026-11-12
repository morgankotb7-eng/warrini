/* Warrini editor. Vanilla JS, no build step, no network beyond this local server. */
"use strict";

const TOKEN = document.querySelector('meta[name="warrini-token"]').content;
const ICONS = "/static/icons.svg";
const MAX_TEXT = 600;
const CPS = 13; // speech characters per second (estimate)
const THEMES = [
  ["navy", "كحلي"],
  ["charcoal", "فحمي"],
  ["forest", "أخضر"],
  ["sand", "رملي"],
];

const state = {
  status: null,
  slug: null,
  guide: null,
  current: null, // step id
  tool: "select", // select | focus | redact
  sel: null, // {kind: "focus"|"redact", index}
  tab: "step",
  history: [],
  future: [],
  saveTimer: 0,
  saving: false,
  pendingSave: false,
  dirty: false,
  lastCoalesce: "",
  spotPreview: true,
  audio: null,
  jobTimer: 0,
};

/* ------------------------------------------------------------------ helpers */

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "text") el.textContent = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function icon(name, cls) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("aria-hidden", "true");
  if (cls) svg.setAttribute("class", cls);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `${ICONS}#${name}`);
  svg.append(use);
  return svg;
}

const $ = (sel, root = document) => root.querySelector(sel);

async function api(method, path, body, raw) {
  const opts = { method, headers: { "X-Warrini-Token": TOKEN }, credentials: "same-origin" };
  if (raw) {
    opts.body = raw;
    opts.headers["Content-Type"] = "application/octet-stream";
  } else if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw Object.assign(new Error("البرنامج مش شغال. شغّل warrini تاني."), { status: 0 });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || "حصلت مشكلة"), { status: res.status, data });
  return data;
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const x = [...b].map((v) => v.toString(16).padStart(2, "0")).join("");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}

function ago(iso) {
  const then = Date.parse(iso);
  if (!then) return "";
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return "دلوقتي";
  const m = Math.round(s / 60);
  if (m < 60) return m === 1 ? "من دقيقة" : m === 2 ? "من دقيقتين" : `من ${m} دقيقة`;
  const hr = Math.round(m / 60);
  if (hr < 24) return hr === 1 ? "من ساعة" : hr === 2 ? "من ساعتين" : `من ${hr} ساعة`;
  const d = Math.round(hr / 24);
  if (d === 1) return "امبارح";
  if (d < 7) return d === 2 ? "من يومين" : `من ${d} أيام`;
  return new Date(then).toLocaleDateString("ar-EG-u-nu-latn", { day: "numeric", month: "long", year: "numeric" });
}

function stepsWord(n) {
  if (n === 0) return "مفيش خطوات لسه";
  if (n === 1) return "خطوة واحدة";
  if (n === 2) return "خطوتين";
  return `${n} ${n <= 10 ? "خطوات" : "خطوة"}`;
}

function seconds(text) {
  return text ? Math.max(1, Math.round(text.length / CPS)) : 0;
}

function fmtDuration(s) {
  s = Math.round(s);
  const m = Math.floor(s / 60);
  return m ? `${m}:${String(s % 60).padStart(2, "0")} دقيقة` : `${s} ثانية`;
}

function fmtSize(bytes) {
  return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} ميجا` : `${Math.max(1, Math.round(bytes / 1024))} كيلو`;
}

function naturalKey(name) {
  const western = name.replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).toLowerCase();
  return western.split(/(\d+)/).map((p) => (/^\d+$/.test(p) ? p.padStart(12, "0") : p)).join("");
}

/* ------------------------------------------------------------------ toasts & modals */

function toast(message, opts = {}) {
  const box = $("#toasts");
  const el = h("div", { class: `toast${opts.error ? " error" : ""}`, role: opts.error ? "alert" : "status" }, message);
  if (opts.action) {
    el.append(
      h("button", { class: "btn btn-sm", type: "button", onclick: () => { opts.action.run(); el.remove(); } }, opts.action.label),
    );
  }
  box.append(el);
  setTimeout(() => el.remove(), opts.ms || (opts.action ? 7000 : 3500));
  return el;
}

let lastFocus = null;
function openModal({ title, body, foot, wide, onClose, dismissable = true }) {
  closeModal();
  lastFocus = document.activeElement;
  const close = () => { closeModal(); if (onClose) onClose(); };
  const dialog = h(
    "div",
    { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-labelledby": "modal-title" },
    h("div", { class: "modal-head" },
      h("h2", { id: "modal-title" }, title),
      dismissable ? h("button", { class: "btn btn-ghost icon-btn", type: "button", "aria-label": "اقفل", onclick: close }, icon("x")) : null),
    h("div", { class: "modal-body" }, body),
    foot ? h("div", { class: "modal-foot" }, foot) : null,
  );
  const backdrop = h("div", { class: "backdrop", onmousedown: (e) => { if (dismissable && e.target === backdrop) close(); } }, dialog);
  backdrop.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && dismissable) { e.stopPropagation(); close(); }
    if (e.key === "Tab") trapFocus(e, dialog);
  });
  $("#modal-root").append(backdrop);
  const first = dialog.querySelector("[autofocus]") || dialog.querySelector("input, textarea, button:not(.icon-btn)") || dialog;
  setTimeout(() => first.focus(), 30);
  return { close, dialog };
}

function closeModal() {
  const root = $("#modal-root");
  if (!root.firstChild) return;
  root.replaceChildren();
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
}

function trapFocus(e, root) {
  const items = [...root.querySelectorAll("button, [href], input, textarea, select, video, [tabindex]:not([tabindex='-1'])")].filter((x) => !x.disabled && x.offsetParent);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
  else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
}

function confirmModal({ title, message, ok, danger }) {
  return new Promise((resolve) => {
    const m = openModal({
      title,
      body: h("p", { class: "help" }, message),
      foot: [
        h("button", { class: "btn", type: "button", onclick: () => { m.close(); resolve(false); } }, "لأ، خلّيه"),
        h("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, type: "button", autofocus: true, onclick: () => { m.close(); resolve(true); } }, ok),
      ],
      onClose: () => resolve(false),
    });
  });
}

/* ------------------------------------------------------------------ status, health, privacy */

async function loadStatus() {
  state.status = await api("GET", "/api/status");
}

function renderHealth() {
  const st = state.status;
  const el = $("#health");
  const issues = [];
  if (!st.ffmpeg) issues.push(["ffmpeg مش متسطب على الجهاز، ومن غيره مش هقدر أعمل الفيديو.", "شغّل warrini doctor في الـ terminal وهيقولك تسطّبه إزاي."]);
  else if (!st.libass) issues.push(["نسخة ffmpeg اللي عندك مش بتكتب نص على الفيديو (ناقصها libass).", "شغّل warrini doctor وهيقولك النسخة الصح."]);
  if (!st.voicetut && state.guide && !state.guide.voice.allow_online) {
    issues.push(["الصوت المصري المحلي مش متسطب.", "يا إما تسطّبه (تفاصيل في warrini doctor)، يا إما تفعّل الصوت الأونلاين من تبويب «الدليل»."]);
  }
  if (!issues.length) { el.hidden = true; return; }
  el.replaceChildren(icon("alert"), h("div", {}, ...issues.map(([a, b]) => h("div", {}, h("b", {}, a), " ", b))));
  el.hidden = false;
}

function renderPrivacy() {
  const el = $("#privacy");
  if (!state.guide) { el.hidden = true; return; }
  const online = state.guide.voice.allow_online && !state.status.voicetut;
  const usesEdge = state.guide.voice.allow_online && (state.guide.voice.engine === "edge" || !state.status.voicetut);
  el.className = `privacy ${usesEdge || online ? "online" : "local"}`;
  el.replaceChildren(
    icon(usesEdge ? "globe" : "lock"),
    h("span", { class: "privacy-text" }, usesEdge ? "الصوت بيتعمل أونلاين" : "كله على جهازك"),
  );
  el.title = usesEdge ? "نص الخطوات بيتبعت لـ Microsoft عشان يتحوّل لصوت. الصور والفيديو فاضلين على جهازك." : "مفيش أي حاجة بتطلع من جهازك: الصور والصوت والفيديو.";
  el.hidden = false;
}

/* ------------------------------------------------------------------ router */

function route() {
  const match = location.hash.match(/^#\/g\/([a-z0-9-]{1,60})$/);
  stopAudio();
  if (match) openEditor(match[1]);
  else showHome();
}

async function leaveEditor() {
  if (state.slug && (state.dirty || state.saving)) await flushSave();
  state.slug = null;
  state.guide = null;
  state.history = [];
  state.future = [];
}

/* ------------------------------------------------------------------ home */

async function showHome() {
  await leaveEditor();
  document.title = "ورّيني";
  document.body.classList.remove("is-editor");
  $("#crumbs").replaceChildren();
  $("#make-video").hidden = true;
  $("#save-state").textContent = "";
  $("#privacy").hidden = true;
  renderHealth();
  const main = $("#main");
  main.replaceChildren(h("div", { class: "loading-page" }, h("span", { class: "spinner" })));
  let guides;
  try {
    guides = (await api("GET", "/api/guides")).guides;
  } catch (err) {
    main.replaceChildren(h("div", { class: "loading-page" }, err.message));
    return;
  }
  const head = h("div", { class: "home-head" },
    h("div", {}, h("h1", {}, "الأدلة بتاعتك"), h("p", {}, "كل دليل بيتحوّل لفيديو قصير بصوت مصري، وكله بيتعمل على جهازك.")),
    h("div", { class: "home-actions" },
      h("button", { class: "btn btn-primary btn-lg", type: "button", onclick: () => newGuideModal() }, icon("plus"), "دليل جديد")));
  if (!guides.length) {
    main.replaceChildren(h("div", { class: "home" }, head, emptyHome()));
    return;
  }
  const grid = h("div", { class: "grid" }, guides.map(guideCard));
  main.replaceChildren(h("div", { class: "home" }, head, grid));
}

function emptyHome() {
  return h("section", { class: "empty" },
    hero(),
    h("h2", {}, "اعمل أول دليل فيديو"),
    h("p", {}, "اكتب خطوات الشغل زي ما بتشرحها لزميلك، وحط لقطة شاشة لكل خطوة. ورّيني هيعمل فيديو بصوت مصري، بيقرّب على المكان المهم ويغطّي البيانات الحساسة."),
    h("button", { class: "btn btn-primary btn-lg", type: "button", onclick: () => newGuideModal() }, icon("plus"), "يلا نبدأ"),
    h("div", { class: "how" },
      h("div", {}, h("b", {}, h("span", { class: "num" }, "1"), "اكتب الخطوات"), h("span", {}, "أو الصقها من ملف الـ SOP مرة واحدة.")),
      h("div", {}, h("b", {}, h("span", { class: "num" }, "2"), "حط الصور"), h("span", {}, "الصق لقطة الشاشة وحدد الزرار المهم والحاجات اللي تتغطى.")),
      h("div", {}, h("b", {}, h("span", { class: "num" }, "3"), "اعمل الفيديو"), h("span", {}, "صوت ونص وحركة كاميرا، جاهز تبعته على طول."))));
}

function hero() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 120 120");
  svg.setAttribute("class", "hero-art");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML =
    '<rect x="10" y="22" width="100" height="70" rx="12" fill="#12224a"/>' +
    '<rect x="20" y="32" width="80" height="46" rx="6" fill="#1d3470"/>' +
    '<rect x="58" y="56" width="34" height="14" rx="4" fill="none" stroke="#f5c845" stroke-width="3"/>' +
    '<rect x="28" y="40" width="26" height="6" rx="3" fill="#94a3b8"/>' +
    '<rect x="28" y="50" width="18" height="6" rx="3" fill="#64748b"/>' +
    '<path d="M50 100h20" stroke="#cbd5e1" stroke-width="6" stroke-linecap="round"/>' +
    '<circle cx="96" cy="26" r="13" fill="#f5c845"/><path d="M92 20l9 6-9 6z" fill="#12224a"/>';
  return svg;
}

function guideCard(g) {
  const open = () => { location.hash = `#/g/${g.slug}`; };
  const thumb = h("div", { class: "thumb" }, g.thumb ? h("img", { src: g.thumb, alt: "", loading: "lazy" }) : icon("images"));
  return h("article", { class: "card" },
    h("button", { class: "card-open", type: "button", onclick: open, "aria-label": `افتح ${g.title || "دليل من غير عنوان"}` },
      thumb,
      h("div", { class: "card-body" },
        h("h2", { class: "card-title" }, g.title || "دليل من غير عنوان"),
        h("div", { class: "card-meta" }, `${stepsWord(g.steps)} · اتعدّل ${ago(g.updated_at)}`))),
    h("div", { class: "card-tools" },
      g.video ? h("button", { class: "btn btn-sm icon-btn", type: "button", "aria-label": "شغّل آخر فيديو", title: "شغّل آخر فيديو", onclick: () => playModal(g.title, g.video, g.slug) }, icon("play")) : null,
      h("button", { class: "btn btn-sm icon-btn btn-danger", type: "button", "aria-label": "امسح الدليل", title: "امسح الدليل", onclick: () => deleteGuide(g) }, icon("trash"))));
}

async function deleteGuide(g) {
  const ok = await confirmModal({
    title: "تمسح الدليل ده؟",
    message: `«${g.title || "دليل من غير عنوان"}» هيتنقل لفولدر .trash جوه فولدر ورّيني، وتقدر ترجّعه من هناك لو احتجته.`,
    ok: "امسح",
    danger: true,
  });
  if (!ok) return;
  try {
    await api("DELETE", `/api/guides/${g.slug}`);
    toast("اتمسح");
    showHome();
  } catch (err) {
    toast(err.message, { error: true });
  }
}

function playModal(title, url, slug) {
  openModal({
    title: title || "الفيديو",
    wide: true,
    body: h("video", { class: "player", src: url, controls: true, autoplay: true, playsinline: true }),
    foot: [
      h("button", { class: "btn", type: "button", onclick: () => api("POST", `/api/guides/${slug}/open-folder`).catch((e) => toast(e.message, { error: true })) }, icon("folder"), "افتح الفولدر"),
      h("a", { class: "btn btn-primary", href: url, download: "" }, icon("download"), "نزّل الفيديو"),
    ],
  });
}

function newGuideModal() {
  const title = h("input", { type: "text", id: "ng-title", maxlength: "120", placeholder: "مثلاً: إزاي تغيّر باسورد الـ VPN", autofocus: true, dir: "rtl" });
  const steps = h("textarea", { id: "ng-steps", rows: "6", dir: "rtl", placeholder: "1. افتح الـ portal من المتصفح\n2. ادخل على VPN\n3. دوس Reset password" });
  const err = h("div", { class: "help", role: "alert" });
  const create = async () => {
    if (!title.value.trim()) { err.textContent = "اكتب عنوان للدليل"; title.focus(); return; }
    btn.disabled = true;
    try {
      const { slug, guide } = await api("POST", "/api/guides", { title: title.value.trim() });
      if (steps.value.trim()) {
        const parsed = (await api("POST", "/api/import/text", { text: steps.value })).steps;
        guide.steps = parsed.map((text) => newStep(text));
        await api("PUT", `/api/guides/${slug}`, { guide });
      }
      m.close();
      location.hash = `#/g/${slug}`;
    } catch (e) {
      err.textContent = e.message;
      btn.disabled = false;
    }
  };
  const btn = h("button", { class: "btn btn-primary", type: "button", onclick: create }, "اعمل الدليل");
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });
  const m = openModal({
    title: "دليل جديد",
    body: [
      h("div", { class: "field" }, h("label", { for: "ng-title" }, "عنوان الدليل"), title),
      h("div", { class: "field" },
        h("label", { for: "ng-steps" }, "الخطوات ", h("span", { class: "help" }, "(اختياري)")),
        steps,
        h("div", { class: "help" }, "الصق الخطوات من ملف الـ SOP: كل سطر مترقّم أو عليه شَرطة بيبقى خطوة لوحده. الصور تحطها بعدين.")),
      err,
    ],
    foot: [h("button", { class: "btn", type: "button", onclick: () => m.close() }, "إلغاء"), btn],
  });
}

function newStep(text = "", image = null) {
  return { id: uuid(), text, image, focus: null, redact: [], review: false, pause_after: 0.6 };
}

/* ------------------------------------------------------------------ editor: load & save */

async function openEditor(slug) {
  if (state.slug === slug && state.guide) { renderEditor(); return; }
  await leaveEditor();
  const main = $("#main");
  main.replaceChildren(h("div", { class: "loading-page" }, h("span", { class: "spinner" })));
  try {
    const data = await api("GET", `/api/guides/${slug}`);
    state.slug = slug;
    state.guide = data.guide;
  } catch (err) {
    main.replaceChildren(h("div", { class: "loading-page" }, h("div", {}, h("p", {}, err.status === 404 ? "الدليل ده مش موجود." : err.message), h("a", { class: "btn", href: "#/" }, "ارجع للأدلة"))));
    return;
  }
  state.current = state.guide.steps[0] ? state.guide.steps[0].id : null;
  state.tool = "select";
  state.sel = null;
  state.tab = "step";
  state.dirty = false;
  setSaveState("ok", "محفوظ");
  renderEditor();
  checkJobOnOpen();
}

function setSaveState(kind, text) {
  const el = $("#save-state");
  el.className = `save-state ${kind}`;
  el.textContent = text;
}

function snapshot() {
  return JSON.stringify({ guide: state.guide, current: state.current });
}

/** Change the guide. `coalesce` merges rapid edits (typing, dragging) into one undo step. */
function commit(fn, opts = {}) {
  const key = opts.coalesce || "";
  if (!key || key !== state.lastCoalesce) {
    state.history.push(snapshot());
    if (state.history.length > 100) state.history.shift();
    state.future = [];
  }
  state.lastCoalesce = key;
  fn(state.guide);
  state.dirty = true;
  setSaveState("busy", "بيحفظ…");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(save, 800);
  if (opts.render !== false) renderEditor(opts.keepFocus);
}

function undo() {
  if (!state.history.length) return;
  state.future.push(snapshot());
  restore(state.history.pop());
}

function redo() {
  if (!state.future.length) return;
  state.history.push(snapshot());
  restore(state.future.pop());
}

function restore(snap) {
  const data = JSON.parse(snap);
  const rev = state.guide.rev;
  state.guide = { ...data.guide, rev };
  state.current = data.current;
  state.sel = null;
  state.lastCoalesce = "";
  state.dirty = true;
  setSaveState("busy", "بيحفظ…");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(save, 500);
  renderEditor();
}

async function save(force = false) {
  if (!state.slug) return;
  if (state.saving) { state.pendingSave = true; return; }
  state.saving = true;
  state.dirty = false;
  const slug = state.slug;
  const sent = JSON.parse(JSON.stringify(state.guide));
  try {
    const res = await api("PUT", `/api/guides/${slug}`, { guide: sent, force });
    if (state.slug === slug && state.guide) {
      state.guide.rev = res.guide.rev;
      state.guide.updated_at = res.guide.updated_at;
      // the server may have fixed ids or clamped boxes: adopt them when nothing changed meanwhile
      if (!state.dirty && !state.pendingSave) state.guide = { ...res.guide };
    }
    if (!state.dirty && !state.pendingSave) setSaveState("ok", "محفوظ");
  } catch (err) {
    state.dirty = true;
    if (err.status === 409) {
      conflictModal(err.data.current);
    } else {
      setSaveState("bad", "مش محفوظ");
      toast(`${err.message} — هجرب أحفظ تاني`, { error: true });
      clearTimeout(state.saveTimer);
      state.saveTimer = setTimeout(save, 4000);
    }
  } finally {
    state.saving = false;
    if (state.pendingSave) {
      state.pendingSave = false;
      save();
    }
  }
}

async function flushSave() {
  clearTimeout(state.saveTimer);
  for (let i = 0; i < 50 && (state.saving || state.dirty || state.pendingSave); i++) {
    if (!state.saving) await save();
    else await new Promise((r) => setTimeout(r, 100));
  }
  return !state.dirty;
}

function conflictModal(current) {
  setSaveState("bad", "فيه تعارض");
  const m = openModal({
    title: "الدليل اتعدّل من مكان تاني",
    dismissable: false,
    body: h("p", { class: "help" }, "غالباً الدليل ده مفتوح في tab تانية واتعدّل هناك. تحب تفتح النسخة اللي اتحفظت، ولا تحفظ اللي قدامك فوقها؟"),
    foot: [
      h("button", { class: "btn", type: "button", onclick: () => { m.close(); state.guide = current; state.dirty = false; state.history = []; setSaveState("ok", "محفوظ"); renderEditor(); } }, "افتح النسخة المحفوظة"),
      h("button", { class: "btn btn-primary", type: "button", onclick: () => { m.close(); save(true); } }, "احفظ اللي قدامي"),
    ],
  });
}

/* ------------------------------------------------------------------ editor: layout */

function currentStep() {
  return state.guide.steps.find((s) => s.id === state.current) || null;
}

function stepIndex(id = state.current) {
  return state.guide.steps.findIndex((s) => s.id === id);
}

function renderEditor(keepFocus) {
  if (!state.guide) return;
  const active = document.activeElement;
  const focusId = keepFocus && active && active.id;
  const caret = focusId && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
  document.title = `${state.guide.title || "دليل"} — ورّيني`;
  $("#crumbs").replaceChildren(
    h("a", { href: "#/", class: "btn btn-ghost btn-sm" }, "الأدلة"),
    icon("chevron"),
    h("strong", {}, state.guide.title || "دليل من غير عنوان"),
  );
  const make = $("#make-video");
  make.hidden = false;
  make.disabled = !state.guide.steps.length;
  renderHealth();
  renderPrivacy();
  const main = $("#main");
  document.body.classList.add("is-editor");
  main.replaceChildren(h("div", { class: "editor" }, renderRail(), renderStage(), renderInspector()));
  if (focusId) {
    const el = document.getElementById(focusId);
    if (el) {
      el.focus();
      if (caret && el.setSelectionRange) el.setSelectionRange(caret[0], caret[1]);
    }
  }
}

/* ------------------------------------------------------------------ rail */

function renderRail() {
  const g = state.guide;
  const title = h("input", {
    class: "title-input", id: "guide-title", type: "text", value: g.title, maxlength: "120", dir: "rtl",
    "aria-label": "عنوان الدليل", placeholder: "عنوان الدليل",
    oninput: (e) => commit((d) => { d.title = e.target.value; }, { coalesce: "title", render: false }),
    onchange: () => renderEditor(true),
  });
  const list = h("ol", { class: "steps", "aria-label": "الخطوات" }, g.steps.map((s, i) => stepItem(s, i)));
  const fileInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", multiple: true, hidden: true, onchange: (e) => importImages([...e.target.files]) });
  return h("aside", { class: "rail", "aria-label": "الخطوات" },
    h("div", { class: "pane-head" }, title, h("div", { class: "subtle" }, `${stepsWord(g.steps.length)} · حوالي ${fmtDuration(estimateTotal())}`)),
    h("div", { class: "pane-body" }, g.steps.length ? list : h("p", { class: "help" }, "مفيش خطوات لسه. ضيف خطوة أو الصق لقطة شاشة على طول بـ Ctrl+V.")),
    h("div", { class: "pane-foot" },
      h("div", { class: "rail-add" },
        h("button", { class: "btn btn-primary", type: "button", onclick: () => addStep() }, icon("plus"), "خطوة جديدة"),
        h("button", { class: "btn icon-btn", type: "button", title: "استورد صور (كل صورة خطوة)", "aria-label": "استورد صور، كل صورة خطوة", onclick: () => fileInput.click() }, icon("images")),
        h("button", { class: "btn icon-btn", type: "button", title: "الصق خطوات من نص", "aria-label": "الصق خطوات من نص", onclick: importTextModal }, icon("list"))),
      fileInput));
}

function estimateTotal() {
  const g = state.guide;
  let total = 2.5 + 2;
  for (const s of g.steps) total += 0.5 + Math.max(seconds(s.text), 0.7) + Math.max(s.pause_after, 0.6);
  return total;
}

function stepItem(step, i) {
  const li = h("li", {
    class: "step", draggable: "true", tabindex: "0", "data-id": step.id,
    "aria-current": step.id === state.current ? "true" : "false",
    "aria-label": `الخطوة ${i + 1}: ${step.text || "من غير كلام"}`,
    onclick: () => select(step.id),
    onkeydown: (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(step.id); }
      if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); moveStep(step.id, e.key === "ArrowUp" ? -1 : 1); }
      if (!e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        const next = state.guide.steps[i + (e.key === "ArrowUp" ? -1 : 1)];
        if (next) { select(next.id); setTimeout(() => { const el = document.querySelector(`.step[data-id="${next.id}"]`); if (el) el.focus(); }); }
      }
    },
  },
  h("span", { class: "num" }, String(i + 1)),
  h("span", { class: "step-thumb" }, step.image ? h("img", { src: `/files/${state.slug}/${step.image}`, alt: "", loading: "lazy" }) : icon("text")),
  h("span", { class: `step-text${step.text ? "" : " empty-text"}` }, step.text || "لسه مكتبتش الكلام"),
  step.review || !step.text ? h("span", { class: "step-flag", title: step.review ? "محتاجة مراجعة" : "ناقصها كلام" }) : null);
  li.addEventListener("dragstart", (e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/warrini-step", step.id); li.classList.add("dragging"); });
  li.addEventListener("dragend", () => li.classList.remove("dragging"));
  li.addEventListener("dragover", (e) => {
    if (![...e.dataTransfer.types].includes("text/warrini-step")) return;
    e.preventDefault();
    const r = li.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    li.classList.toggle("drop-before", before);
    li.classList.toggle("drop-after", !before);
  });
  li.addEventListener("dragleave", () => li.classList.remove("drop-before", "drop-after"));
  li.addEventListener("drop", (e) => {
    const id = e.dataTransfer.getData("text/warrini-step");
    if (!id) return;
    e.preventDefault();
    const before = li.classList.contains("drop-before");
    li.classList.remove("drop-before", "drop-after");
    if (id === step.id) return;
    commit((d) => {
      const from = d.steps.findIndex((s) => s.id === id);
      const [moved] = d.steps.splice(from, 1);
      let to = d.steps.findIndex((s) => s.id === step.id);
      if (!before) to += 1;
      d.steps.splice(to, 0, moved);
    });
  });
  return li;
}

function select(id) {
  if (state.current === id) return;
  state.current = id;
  state.sel = null;
  state.lastCoalesce = "";
  stopAudio();
  renderEditor();
}

function addStep(after = state.current, step = newStep()) {
  commit((d) => {
    const i = d.steps.findIndex((s) => s.id === after);
    d.steps.splice(i < 0 ? d.steps.length : i + 1, 0, step);
  });
  state.current = step.id;
  state.tab = "step";
  renderEditor();
  setTimeout(() => { const t = $("#step-text"); if (t && !step.image) t.focus(); });
  return step;
}

function moveStep(id, delta) {
  const i = stepIndex(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= state.guide.steps.length) return;
  commit((d) => { const [s] = d.steps.splice(i, 1); d.steps.splice(j, 0, s); });
  setTimeout(() => { const el = document.querySelector(`.step[data-id="${id}"]`); if (el) el.focus(); });
}

function duplicateStep() {
  const s = currentStep();
  if (!s) return;
  addStep(s.id, { ...JSON.parse(JSON.stringify(s)), id: uuid() });
}

function deleteStep() {
  const s = currentStep();
  if (!s) return;
  const i = stepIndex();
  commit((d) => { d.steps.splice(i, 1); });
  const next = state.guide.steps[Math.min(i, state.guide.steps.length - 1)];
  state.current = next ? next.id : null;
  renderEditor();
  toast(`اتمسحت الخطوة ${i + 1}`, { action: { label: "رجّعها", run: undo } });
}

/* ------------------------------------------------------------------ images */

async function uploadImage(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) throw new Error("الصور المسموحة PNG أو JPG أو WebP بس");
  if (file.size > 25 * 1024 * 1024) throw new Error("الصورة أكبر من 25 ميجا");
  return api("PUT", `/api/guides/${state.slug}/images`, undefined, file);
}

async function setImage(file) {
  const step = currentStep();
  const working = toast("بيرفع الصورة…");
  try {
    const res = await uploadImage(file);
    working.remove();
    if (!step) {
      addStep(state.current, newStep("", res.image));
      return;
    }
    const replacing = Boolean(step.image);
    commit(() => {
      step.image = res.image;
      if (replacing && step.redact.length) step.review = true;
      if (!replacing) { step.focus = null; step.redact = []; }
    });
    if (replacing && step.redact.length) toast("الصورة اتغيرت: راجع أماكن التغطية قبل ما تعمل الفيديو");
  } catch (err) {
    working.remove();
    toast(err.message, { error: true });
  }
}

async function importImages(files) {
  const images = files.filter((f) => /^image\/(png|jpeg|webp)$/.test(f.type)).sort((a, b) => (naturalKey(a.name) < naturalKey(b.name) ? -1 : 1));
  const skipped = files.length - images.length;
  if (!images.length) { toast("مفيش صور ينفع أستوردها", { error: true }); return; }
  const progress = toast(`بيرفع ${images.length} صورة…`, { ms: 600000 });
  const created = [];
  for (const [n, file] of images.entries()) {
    progress.firstChild.textContent = `بيرفع الصورة ${n + 1} من ${images.length}…`;
    try {
      const res = await uploadImage(file);
      created.push(newStep("", res.image));
    } catch (err) {
      toast(`${file.name}: ${err.message}`, { error: true });
    }
  }
  progress.remove();
  if (!created.length) return;
  commit((d) => {
    const i = d.steps.findIndex((s) => s.id === state.current);
    d.steps.splice(i < 0 ? d.steps.length : i + 1, 0, ...created);
  });
  state.current = created[0].id;
  renderEditor();
  toast(`اتضاف ${stepsWord(created.length)}${skipped ? ` (اتجاهلت ${skipped} ملف مش صور)` : ""}. اكتب كلام كل خطوة.`);
}

function importTextModal() {
  const area = h("textarea", { id: "imp-text", rows: "10", dir: "rtl", autofocus: true, placeholder: "1. افتح Outlook\n2. دوس File\n3. اختار Account Settings" });
  const err = h("div", { class: "help", role: "alert" });
  const go = async () => {
    try {
      const steps = (await api("POST", "/api/import/text", { text: area.value })).steps;
      if (!steps.length) { err.textContent = "ملقيتش خطوات في النص ده"; return; }
      const created = steps.map((t) => newStep(t));
      commit((d) => {
        const i = d.steps.findIndex((s) => s.id === state.current);
        d.steps.splice(i < 0 ? d.steps.length : i + 1, 0, ...created);
      });
      state.current = created[0].id;
      m.close();
      renderEditor();
      toast(`اتضاف ${stepsWord(created.length)}`);
    } catch (e) {
      err.textContent = e.message;
    }
  };
  const m = openModal({
    title: "الصق خطوات من نص",
    body: [h("div", { class: "field" }, h("label", { for: "imp-text" }, "الخطوات"), area, h("div", { class: "help" }, "كل سطر مترقّم (1. أو ١- أو 2)) أو عليه شَرطة بيبقى خطوة. السطر اللي من غير ترقيم بيتضم للخطوة اللي قبله.")), err],
    foot: [h("button", { class: "btn", type: "button", onclick: () => m.close() }, "إلغاء"), h("button", { class: "btn btn-primary", type: "button", onclick: go }, "ضيف الخطوات")],
  });
}

/* ------------------------------------------------------------------ stage */

function renderStage() {
  const step = currentStep();
  const tools = h("div", { class: "segmented", role: "group", "aria-label": "أدوات الصورة" },
    toolBtn("select", "cursor", "تحديد", "V"),
    toolBtn("focus", "target", "مكان التركيز", "F"),
    toolBtn("redact", "redact", "تغطية", "B"));
  const hints = {
    select: "دوس على مربع عشان تحركه أو تكبّره. Delete بيمسحه.",
    focus: "ارسم مربع حوالين الزرار أو المكان اللي الكاميرا تقرّب عليه.",
    redact: "ارسم مربع على أي بيانات حساسة: هتتغطى في الفيديو.",
  };
  const bar = h("div", { class: "toolbar" },
    step && step.image ? tools : h("span", { class: "help" }, step ? "الخطوة دي من غير صورة" : ""),
    step && step.image ? h("button", { class: "btn btn-ghost btn-sm", type: "button", "aria-pressed": String(state.spotPreview), title: "معاينة التعتيم حوالين مكان التركيز", onclick: () => { state.spotPreview = !state.spotPreview; renderEditor(); } }, icon("eye"), state.spotPreview ? "التعتيم ظاهر" : "التعتيم مخفي") : null,
    step && step.image ? h("span", { class: "tool-hint" }, icon("info"), hints[state.tool]) : null);
  const parts = [bar];
  if (step && step.review) {
    parts.push(h("div", { class: "review", role: "alert" }, icon("alert"),
      "الصورة اتغيرت. اتأكد إن مربعات التغطية لسه على البيانات الحساسة.",
      h("button", { class: "btn btn-sm", type: "button", onclick: () => commit(() => { step.review = false; }) }, icon("check"), "تمام، راجعتها")));
  }
  const wrap = h("div", { class: "canvas-wrap" });
  if (!step) {
    wrap.append(dropZone(true));
  } else if (step.image) {
    wrap.append(shotView(step));
  } else {
    wrap.append(dropZone(false, step));
  }
  enableFileDrop(wrap);
  parts.push(wrap);
  return h("section", { class: "stage", "aria-label": "الصورة" }, parts);
}

function toolBtn(name, ico, label, key) {
  return h("button", { type: "button", "aria-pressed": String(state.tool === name), title: `${label} (${key})`, onclick: () => setTool(name) }, icon(ico), label);
}

function setTool(name) {
  state.tool = name;
  if (name !== "select") state.sel = null;
  renderEditor();
}

function dropZone(noStep, step) {
  const input = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", hidden: true, onchange: (e) => { if (e.target.files[0]) setImage(e.target.files[0]); } });
  if (noStep) {
    return h("div", { class: "drop" }, h("div", {},
      h("div", { class: "drop-icon" }, icon("plus")),
      h("h3", {}, "ابدأ بأول خطوة"),
      h("p", {}, "الصق لقطة شاشة بـ ", h("kbd", {}, "Ctrl+V"), " أو اسحب صور هنا، أو ضيف خطوة من غير صورة."),
      h("div", { class: "row" },
        h("button", { class: "btn btn-primary", type: "button", onclick: () => addStep() }, icon("plus"), "خطوة جديدة"),
        h("button", { class: "btn", type: "button", onclick: importTextModal }, icon("list"), "الصق خطوات من نص"))));
  }
  const card = h("div", { class: "drop" }, h("div", {},
    h("div", { class: "drop-icon" }, icon("image")),
    h("h3", {}, "حط لقطة الشاشة بتاعة الخطوة دي"),
    h("p", {}, "الصقها بـ ", h("kbd", {}, "Ctrl+V"), " أو اسحبها هنا. ولو سبتها من غير صورة، الكلام هيظهر على كارت."),
    h("div", { class: "row" },
      h("button", { class: "btn btn-primary", type: "button", onclick: () => input.click() }, icon("upload"), "اختار صورة"),
      input)));
  return card;
}

function enableFileDrop(zone) {
  let overlay = null;
  zone.addEventListener("dragover", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    if (!overlay) { overlay = h("div", { class: "overlay-drop" }, "سيب الصور هنا"); zone.append(overlay); }
  });
  zone.addEventListener("dragleave", (e) => { if (overlay && !zone.contains(e.relatedTarget)) { overlay.remove(); overlay = null; } });
  zone.addEventListener("drop", (e) => {
    if (overlay) { overlay.remove(); overlay = null; }
    const files = [...(e.dataTransfer.files || [])];
    if (!files.length) return;
    e.preventDefault();
    const step = currentStep();
    if (files.length === 1 && step && !step.image) setImage(files[0]);
    else importImages(files);
  });
}

/* the screenshot with draggable boxes; all coordinates are fractions of the image (0..1) */
function shotView(step) {
  const img = h("img", { src: `/files/${state.slug}/${step.image}`, alt: "لقطة الشاشة بتاعة الخطوة", draggable: "false" });
  const shot = h("div", { class: `shot tool-${state.tool}` }, img);
  const boxes = [];
  if (step.focus) boxes.push(boxEl(step, "focus", 0, step.focus));
  step.redact.forEach((r, i) => boxes.push(boxEl(step, "redact", i, r)));
  shot.append(...boxes);
  shot.addEventListener("pointerdown", (e) => startDraw(e, shot, step));
  return shot;
}

function boxEl(step, kind, index, r) {
  const selected = state.sel && state.sel.kind === kind && state.sel.index === index;
  const el = h("div", {
    class: `box ${kind}${selected ? " selected" : ""}${kind === "focus" && !state.spotPreview ? " no-spot" : ""}`,
    role: "button", tabindex: "0",
    "aria-label": kind === "focus" ? "مكان التركيز" : `تغطية ${index + 1}`,
  });
  place(el, r);
  if (kind === "redact") el.append(icon("lock"));
  if (selected) {
    el.append(h("span", { class: "box-tag" }, kind === "focus" ? "مكان التركيز" : "تغطية"));
    for (const c of ["nw", "ne", "sw", "se"]) el.append(h("span", { class: `handle ${c}`, "data-corner": c }));
  }
  el.addEventListener("pointerdown", (e) => startMove(e, step, kind, index));
  el.addEventListener("focus", () => { if (!selected) { state.sel = { kind, index }; } });
  return el;
}

function place(el, r) {
  el.style.left = `${r.x * 100}%`;
  el.style.top = `${r.y * 100}%`;
  el.style.width = `${r.w * 100}%`;
  el.style.height = `${r.h * 100}%`;
}

function relPoint(e, shot) {
  const r = shot.getBoundingClientRect();
  return { x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) };
}

function startDraw(e, shot, step) {
  if (e.button !== 0 || e.target.closest(".box")) return;
  if (state.tool === "select") { if (state.sel) { state.sel = null; renderEditor(); } return; }
  e.preventDefault();
  const kind = state.tool;
  if (kind === "redact" && step.redact.length >= 20) { toast("أقصى عدد مربعات تغطية 20", { error: true }); return; }
  const start = relPoint(e, shot);
  const ghost = h("div", { class: `box ${kind}${kind === "focus" && !state.spotPreview ? " no-spot" : ""}` });
  if (kind === "redact") ghost.append(icon("lock"));
  shot.append(ghost);
  shot.setPointerCapture(e.pointerId);
  let rect = { x: start.x, y: start.y, w: 0, h: 0 };
  const move = (ev) => {
    const p = relPoint(ev, shot);
    rect = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
    place(ghost, rect);
  };
  const up = () => {
    shot.removeEventListener("pointermove", move);
    shot.removeEventListener("pointerup", up);
    shot.removeEventListener("pointercancel", up);
    ghost.remove();
    if (rect.w < 0.012 || rect.h < 0.012) return;
    const clean = roundRect(rect);
    commit(() => {
      if (kind === "focus") step.focus = clean;
      else step.redact.push(clean);
    });
    state.sel = { kind, index: kind === "focus" ? 0 : step.redact.length - 1 };
    state.tool = "select";
    renderEditor();
  };
  shot.addEventListener("pointermove", move);
  shot.addEventListener("pointerup", up);
  shot.addEventListener("pointercancel", up);
}

function roundRect(r) {
  const k = 1e5;
  return { x: Math.round(r.x * k) / k, y: Math.round(r.y * k) / k, w: Math.round(r.w * k) / k, h: Math.round(r.h * k) / k };
}

function getBox(step, kind, index) {
  return kind === "focus" ? step.focus : step.redact[index];
}

function setBox(step, kind, index, r) {
  if (kind === "focus") step.focus = r;
  else step.redact[index] = r;
}

function startMove(e, step, kind, index) {
  if (e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  const el = e.currentTarget;
  const shot = el.parentElement;
  const corner = e.target.dataset ? e.target.dataset.corner : null;
  const wasSelected = state.sel && state.sel.kind === kind && state.sel.index === index;
  if (!wasSelected) {
    state.sel = { kind, index };
    state.tool = "select";
    renderEditor();
    return;
  }
  const start = relPoint(e, shot);
  const orig = { ...getBox(step, kind, index) };
  shot.setPointerCapture(e.pointerId);
  let moved = false;
  const move = (ev) => {
    const p = relPoint(ev, shot);
    const dx = p.x - start.x, dy = p.y - start.y;
    let r;
    if (!corner) {
      r = { x: Math.min(Math.max(orig.x + dx, 0), 1 - orig.w), y: Math.min(Math.max(orig.y + dy, 0), 1 - orig.h), w: orig.w, h: orig.h };
    } else {
      let x0 = orig.x, y0 = orig.y, x1 = orig.x + orig.w, y1 = orig.y + orig.h;
      if (corner.includes("w")) x0 = Math.min(Math.max(orig.x + dx, 0), x1 - 0.015);
      if (corner.includes("e")) x1 = Math.max(Math.min(orig.x + orig.w + dx, 1), x0 + 0.015);
      if (corner.includes("n")) y0 = Math.min(Math.max(orig.y + dy, 0), y1 - 0.015);
      if (corner.includes("s")) y1 = Math.max(Math.min(orig.y + orig.h + dy, 1), y0 + 0.015);
      r = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    }
    moved = true;
    place(el, r);
    el._rect = r;
  };
  const up = () => {
    shot.removeEventListener("pointermove", move);
    shot.removeEventListener("pointerup", up);
    shot.removeEventListener("pointercancel", up);
    if (moved && el._rect) commit(() => setBox(step, kind, index, roundRect(el._rect)));
  };
  shot.addEventListener("pointermove", move);
  shot.addEventListener("pointerup", up);
  shot.addEventListener("pointercancel", up);
}

function nudgeSelection(dx, dy) {
  const step = currentStep();
  if (!step || !state.sel) return;
  const r = getBox(step, state.sel.kind, state.sel.index);
  if (!r) return;
  commit(() => setBox(step, state.sel.kind, state.sel.index, roundRect({
    x: Math.min(Math.max(r.x + dx, 0), 1 - r.w), y: Math.min(Math.max(r.y + dy, 0), 1 - r.h), w: r.w, h: r.h,
  })), { coalesce: `nudge-${state.sel.kind}-${state.sel.index}` });
}

function deleteSelection() {
  const step = currentStep();
  if (!step || !state.sel) return;
  const { kind, index } = state.sel;
  commit(() => { if (kind === "focus") step.focus = null; else step.redact.splice(index, 1); });
  state.sel = null;
  renderEditor();
}

/* ------------------------------------------------------------------ inspector */

function renderInspector() {
  const tabs = h("div", { class: "tabs", role: "tablist" },
    tabBtn("step", "الخطوة"),
    tabBtn("guide", "الدليل"),
    tabBtn("keys", "الاختصارات"));
  let body;
  if (state.tab === "guide") body = guidePanel();
  else if (state.tab === "keys") body = keysPanel();
  else body = stepPanel();
  return h("aside", { class: "inspector", "aria-label": "الإعدادات" }, tabs, h("div", { class: "pane-body", role: "tabpanel" }, body));
}

function tabBtn(name, label) {
  return h("button", { type: "button", role: "tab", "aria-selected": String(state.tab === name), onclick: () => { state.tab = name; renderEditor(); } }, label);
}

function stepPanel() {
  const step = currentStep();
  if (!step) return h("p", { class: "help" }, "اختار خطوة من على اليمين، أو ضيف خطوة جديدة.");
  const i = stepIndex();
  const count = h("span", { dir: "ltr" }, `${step.text.length} / ${MAX_TEXT}`);
  const est = h("span", {}, step.text ? `حوالي ${seconds(step.text)} ثانية كلام` : "");
  const text = h("textarea", {
    id: "step-text", dir: "rtl", maxlength: String(MAX_TEXT), rows: "6",
    placeholder: "اكتب اللي هيتقال، زي ما بتشرح لزميلك: «دوس على زرار Reset password اللي تحت على الشمال».",
    oninput: (e) => {
      const value = e.target.value;
      commit(() => { step.text = value; }, { coalesce: `text-${step.id}`, render: false });
      count.textContent = `${value.length} / ${MAX_TEXT}`;
      est.textContent = value ? `حوالي ${seconds(value)} ثانية كلام` : "";
      const item = document.querySelector(`.step[data-id="${step.id}"] .step-text`);
      if (item) { item.textContent = value || "لسه مكتبتش الكلام"; item.classList.toggle("empty-text", !value); }
    },
  });
  text.value = step.text;
  const pauseOut = h("output", { for: "pause" }, `${step.pause_after.toFixed(1)} ثانية`);
  const pause = h("input", {
    id: "pause", type: "range", min: "0.2", max: "3", step: "0.1", value: String(step.pause_after),
    oninput: (e) => { const v = Number(e.target.value); pauseOut.textContent = `${v.toFixed(1)} ثانية`; commit(() => { step.pause_after = v; }, { coalesce: `pause-${step.id}`, render: false }); },
  });
  const replace = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", hidden: true, onchange: (e) => { if (e.target.files[0]) setImage(e.target.files[0]); } });
  return [
    h("div", { class: "field" },
      h("label", { for: "step-text" }, `هيتقال إيه في الخطوة ${i + 1}؟`),
      text,
      h("div", { class: "meta-row" }, est, count)),
    listenBlock(step),
    h("div", { class: "field" },
      h("label", { for: "pause" }, "سكتة بعد الخطوة"),
      pause,
      h("div", { class: "meta-row" }, h("span", { class: "help" }, "وقت يستوعب فيه اللي بيتفرج قبل الخطوة اللي بعدها"), pauseOut)),
    step.image ? h("div", { class: "section" },
      h("h3", {}, "الصورة"),
      h("div", { class: "kbd-list" },
        h("div", {}, h("span", {}, "مكان التركيز"), h("b", {}, step.focus ? "متحدد" : "مش متحدد (الصورة كلها)")),
        h("div", {}, h("span", {}, "مربعات التغطية"), h("b", {}, String(step.redact.length)))),
      h("div", { class: "row-btns" },
        h("button", { class: "btn btn-sm", type: "button", onclick: () => replace.click() }, icon("upload"), "غيّر الصورة"),
        h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: () => commit(() => { step.image = null; step.focus = null; step.redact = []; step.review = false; }) }, icon("x"), "شيل الصورة"),
        replace)) : null,
    h("div", { class: "section" },
      h("h3", {}, "الخطوة"),
      h("div", { class: "row-btns" },
        h("button", { class: "btn btn-sm icon-btn", type: "button", "aria-label": "طلّع الخطوة لفوق", title: "لفوق (Alt+↑)", disabled: i === 0, onclick: () => moveStep(step.id, -1) }, icon("up")),
        h("button", { class: "btn btn-sm icon-btn", type: "button", "aria-label": "نزّل الخطوة لتحت", title: "لتحت (Alt+↓)", disabled: i === state.guide.steps.length - 1, onclick: () => moveStep(step.id, 1) }, icon("down")),
        h("button", { class: "btn btn-sm", type: "button", onclick: duplicateStep }, icon("copy"), "كرّر"),
        h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: deleteStep }, icon("trash"), "امسح"))),
  ];
}

function listenBlock(step) {
  const bars = h("div", { class: "wave", "aria-hidden": "true" });
  const heights = [8, 14, 22, 12, 26, 18, 10, 20, 28, 16, 9, 24, 13, 19, 27, 11, 17, 23, 8, 15, 21, 12, 25, 14, 10, 18, 22, 9];
  heights.forEach((v, k) => { const bar = h("i"); bar.style.height = `${v}px`; bar.style.animationDelay = `${(k % 7) * 90}ms`; bars.append(bar); });
  const playing = state.audio && state.audio.stepId === step.id;
  if (playing) bars.classList.add(state.audio.loading ? "loading" : "playing");
  const label = playing ? (state.audio.loading ? "بيجهّز الصوت…" : "وقّف") : "اسمع";
  const btn = h("button", { class: "btn", type: "button", disabled: !step.text.trim(), onclick: () => (playing ? stopAudio() : listen(step)) },
    playing && state.audio.loading ? h("span", { class: "spinner" }) : icon(playing ? "stop" : "play"), label);
  return h("div", { class: "field" }, h("span", { class: "label" }, "الصوت"), h("div", { class: "listen" }, btn, bars),
    !state.status.voicetut && !state.guide.voice.allow_online ? h("div", { class: "help" }, "عشان تسمع، سطّب الصوت المحلي أو فعّل الصوت الأونلاين من تبويب «الدليل».") : null);
}

async function listen(step) {
  stopAudio();
  state.audio = { stepId: step.id, loading: true, el: null };
  renderEditor(true);
  const slow = setTimeout(() => { if (state.audio && state.audio.loading) toast("أول مرة الصوت المحلي بياخد وقت عشان يحمّل الموديل… استنى شوية", { ms: 8000 }); }, 4000);
  try {
    const res = await api("POST", `/api/guides/${state.slug}/voice`, { text: step.text });
    clearTimeout(slow);
    if (!state.audio || state.audio.stepId !== step.id) return;
    const audio = new Audio(res.url);
    state.audio = { stepId: step.id, loading: false, el: audio };
    audio.addEventListener("ended", () => { stopAudio(); renderEditor(true); });
    await audio.play();
    renderEditor(true);
  } catch (err) {
    clearTimeout(slow);
    state.audio = null;
    renderEditor(true);
    toast(err.message, { error: true });
  }
}

function stopAudio() {
  if (state.audio && state.audio.el) state.audio.el.pause();
  const had = Boolean(state.audio);
  state.audio = null;
  return had;
}

function guidePanel() {
  const g = state.guide;
  const st = state.status;
  const theme = h("div", { class: "swatches", role: "radiogroup", "aria-label": "شكل الفيديو" },
    THEMES.map(([name, label]) => h("button", {
      class: `swatch sw-${name}`, type: "button", role: "radio", "aria-checked": String(g.theme === name), "aria-label": label, title: label,
      onclick: () => commit((d) => { d.theme = name; }),
    })));
  const intro = h("textarea", { id: "intro", dir: "rtl", rows: "3", maxlength: "400", placeholder: g.title || "",
    oninput: (e) => commit((d) => { d.intro = e.target.value.trim() ? e.target.value : null; }, { coalesce: "intro", render: false }) });
  intro.value = g.intro || "";
  const outro = h("textarea", { id: "outro", dir: "rtl", rows: "2", maxlength: "300", placeholder: "مثلاً: لو قابلتك أي مشكلة كلّم الـ Service Desk على 1234.",
    oninput: (e) => commit((d) => { d.outro = e.target.value.trim() ? e.target.value : null; }, { coalesce: "outro", render: false }) });
  outro.value = g.outro || "";
  const online = h("input", { type: "checkbox", id: "online", checked: g.voice.allow_online,
    onchange: (e) => commit((d) => { d.voice.allow_online = e.target.checked; }) });
  const voiceNote = st.voicetut
    ? h("div", { class: "note ok" }, icon("lock"), h("div", {}, h("b", {}, "صوت طارق المصري شغال على جهازك."), " مفيش أي نص بيطلع برّه."))
    : h("div", { class: "note warn" }, icon("alert"), h("div", {}, h("b", {}, "الصوت المحلي مش متسطب."), " سطّبه مرة واحدة (التفاصيل في ", h("span", { class: "ltr" }, "warrini doctor"), ")، أو فعّل الصوت الأونلاين تحت."));
  return [
    h("div", { class: "field" }, h("span", { class: "label", id: "theme-label" }, "شكل الفيديو"), theme),
    h("div", { class: "field" }, h("label", { for: "intro" }, "الجملة اللي في الأول"), intro, h("div", { class: "help" }, "لو سبتها فاضية، هيتقال عنوان الدليل.")),
    h("div", { class: "field" }, h("label", { for: "outro" }, "الجملة اللي في الآخر ", h("span", { class: "help" }, "(اختياري)")), outro),
    h("div", { class: "section" },
      h("h3", {}, "الصوت"),
      voiceNote,
      h("label", { class: "switch", for: "online" }, online, h("span", {},
        h("b", {}, "اسمح بالصوت الأونلاين"), h("br"),
        h("span", { class: "help" }, "بيستخدم صوت Microsoft المصري. نص الخطوات بيتبعت لـ Microsoft عشان يتحوّل لصوت، والصور والفيديو فاضلين على جهازك."))),
      g.voice.allow_online && st.voicetut ? h("div", { class: "field" },
        h("label", { for: "engine" }, "أنهي صوت؟"),
        h("select", { id: "engine", onchange: (e) => commit((d) => { d.voice.engine = e.target.value; }) },
          h("option", { value: "auto", selected: g.voice.engine !== "edge" }, "المحلي (طارق)"),
          h("option", { value: "edge", selected: g.voice.engine === "edge" }, "الأونلاين (Microsoft)"))) : null),
    h("div", { class: "section" },
      h("h3", {}, "مكان الملفات"),
      h("div", { class: "help ltr" }, `${st.root}`),
      h("button", { class: "btn btn-sm", type: "button", onclick: () => api("POST", `/api/guides/${state.slug}/open-folder`).catch((e) => toast(e.message, { error: true })) }, icon("folder"), "افتح فولدر الفيديوهات")),
  ];
}

function keysPanel() {
  const rows = [
    ["Ctrl+V", "الصق لقطة شاشة"],
    ["V / F / B", "تحديد / مكان التركيز / تغطية"],
    ["Delete", "امسح المربع المتحدد"],
    ["أسهم الكيبورد", "حرّك المربع المتحدد (مع Shift أسرع)"],
    ["Alt + ↑ / ↓", "حرّك الخطوة في الترتيب"],
    ["Ctrl+Z / Ctrl+Y", "تراجع / إعادة"],
    ["Ctrl+Enter", "اعمل الفيديو"],
  ];
  return h("div", { class: "kbd-list" }, rows.map(([k, label]) => h("div", {}, h("span", {}, label), h("kbd", {}, k))));
}

/* ------------------------------------------------------------------ render */

async function makeVideo() {
  if (!state.guide) return;
  const g = state.guide;
  const problems = [];
  if (!g.title.trim()) problems.push("اكتب عنوان للدليل.");
  g.steps.forEach((s, i) => {
    if (!s.text.trim()) problems.push(`الخطوة ${i + 1} مفيهاش كلام يتقال.`);
    if (s.review) problems.push(`الخطوة ${i + 1}: راجع مربعات التغطية بعد تغيير الصورة.`);
  });
  if (!g.steps.length) problems.push("ضيف خطوة واحدة على الأقل.");
  if (problems.length) {
    openModal({
      title: "فاضل حاجات قبل الفيديو",
      body: h("ul", { class: "help" }, problems.slice(0, 8).map((p) => h("li", {}, p))),
      foot: [h("button", { class: "btn btn-primary", type: "button", onclick: () => { closeModal(); const bad = g.steps.find((s) => !s.text.trim() || s.review); if (bad) select(bad.id); } }, "تمام")],
    });
    return;
  }
  let quality = "high";
  const choice = (value, title, desc) => h("button", { class: "choice", type: "button", role: "radio", "aria-checked": String(quality === value), onclick: (e) => {
    quality = value;
    e.currentTarget.parentElement.querySelectorAll(".choice").forEach((c) => c.setAttribute("aria-checked", String(c === e.currentTarget)));
  } }, h("b", {}, title), h("span", {}, desc));
  const m = openModal({
    title: "اعمل الفيديو",
    body: [
      h("div", { class: "choices", role: "radiogroup", "aria-label": "الجودة" },
        choice("high", "جودة عالية", "1080p — للنشر والإرسال"),
        choice("fast", "معاينة سريعة", "720p — أسرع عشان تراجع")),
      h("p", { class: "help" }, `المدة المتوقعة حوالي ${fmtDuration(estimateTotal())}. الفيديو هيتحفظ في فولدر الدليل.`),
    ],
    foot: [
      h("button", { class: "btn", type: "button", onclick: () => m.close() }, "إلغاء"),
      h("button", { class: "btn btn-primary", type: "button", autofocus: true, onclick: async (e) => {
        e.currentTarget.disabled = true;
        const saved = await flushSave();
        if (!saved) { toast("مقدرتش أحفظ آخر تعديلات، جرّب تاني", { error: true }); m.close(); return; }
        try {
          await api("POST", `/api/guides/${state.slug}/render`, { quality });
          progressModal();
        } catch (err) {
          m.close();
          openModal({ title: "مقدرتش أبدأ الفيديو", body: h("div", { class: "error-box" }, err.message) });
        }
      } }, icon("film"), "يلا"),
    ],
  });
}

const STAGES = [
  ["voice", "الصوت"],
  ["images", "التجهيز"],
  ["video", "الفيديو"],
];

function progressModal(initial) {
  clearInterval(state.jobTimer);
  const bar = h("i");
  const pct = h("span", {}, "0%");
  const eta = h("span", {});
  const stages = h("ol", { class: "stages" });
  const status = h("div", { role: "status", "aria-live": "polite" },
    stages, h("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, bar), h("div", { class: "bar-meta" }, pct, eta));
  const cancel = h("button", { class: "btn", type: "button", onclick: async () => { cancel.disabled = true; cancel.textContent = "بيوقف…"; await api("POST", "/api/job/cancel").catch(() => {}); } }, "إلغاء");
  const m = openModal({ title: "بيعمل الفيديو", body: status, foot: [cancel], dismissable: false });
  const update = (job) => {
    const order = STAGES.map((s) => s[0]);
    const idx = job.stage === "finish" ? 3 : Math.max(0, order.indexOf(job.stage));
    stages.replaceChildren(...STAGES.map(([, label], k) => h("li", { class: k < idx ? "done" : k === idx ? "active" : "" },
      h("span", { class: "dot" }, k < idx ? icon("check") : null), label)));
    bar.style.width = `${job.percent}%`;
    bar.parentElement.setAttribute("aria-valuenow", String(Math.round(job.percent)));
    pct.textContent = `${Math.round(job.percent)}%`;
    eta.textContent = job.eta != null ? `فاضل حوالي ${fmtDuration(job.eta)}` : job.stage === "voice" ? "بيجهّز الصوت…" : "";
  };
  if (initial) update(initial);
  const poll = async () => {
    let job;
    try { job = await api("GET", "/api/job"); } catch { return; }
    if (job.state === "running") { update(job); return; }
    clearInterval(state.jobTimer);
    m.close();
    if (job.state === "done") resultModal(job);
    else if (job.state === "cancelled") toast("اتلغى. الفيديو اللي قبله زي ما هو.");
    else if (job.state === "error") errorModal(job);
  };
  state.jobTimer = setInterval(poll, 500);
  poll();
}

function resultModal(job) {
  openModal({
    title: "الفيديو جاهز",
    wide: true,
    body: [
      h("video", { class: "player", src: job.video, controls: true, autoplay: true, playsinline: true }),
      h("div", { class: "result-meta" }, h("span", {}, icon("film"), ` ${fmtDuration(job.duration)}`), h("span", {}, fmtSize(job.size)), h("span", {}, "معاه ملف ترجمة .srt")),
    ],
    foot: [
      h("button", { class: "btn", type: "button", onclick: () => api("POST", `/api/guides/${job.slug}/open-folder`).catch((e) => toast(e.message, { error: true })) }, icon("folder"), "افتح الفولدر"),
      h("a", { class: "btn btn-primary", href: job.video, download: "" }, icon("download"), "نزّل الفيديو"),
    ],
  });
}

function errorModal(job) {
  openModal({
    title: "الفيديو ماكملش",
    body: h("div", { class: "error-box", role: "alert" }, h("b", {}, job.error),
      job.detail ? h("details", {}, h("summary", {}, "تفاصيل للدعم الفني"), h("pre", {}, job.detail)) : null),
    foot: [h("button", { class: "btn btn-primary", type: "button", onclick: closeModal }, "تمام")],
  });
}

async function checkJobOnOpen() {
  try {
    const job = await api("GET", "/api/job");
    if (job.state === "running" && job.slug === state.slug) progressModal(job);
  } catch { /* ignore */ }
}

/* ------------------------------------------------------------------ global keys & paste */

function typing(el) {
  return el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.tagName === "SELECT" || el.isContentEditable);
}

document.addEventListener("keydown", (e) => {
  if ($("#modal-root").firstChild || !state.guide) return;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key === "Enter") { e.preventDefault(); makeVideo(); return; }
  if (typing(document.activeElement)) return;
  if (mod && (e.key === "z" || e.key === "Z") && !e.shiftKey) { e.preventDefault(); undo(); return; }
  if (mod && (e.key === "y" || ((e.key === "z" || e.key === "Z") && e.shiftKey))) { e.preventDefault(); redo(); return; }
  if (mod || e.altKey) return;
  const step = currentStep();
  if (step && step.image) {
    if (e.key === "v" || e.key === "V") { setTool("select"); return; }
    if (e.key === "f" || e.key === "F") { setTool("focus"); return; }
    if (e.key === "b" || e.key === "B") { setTool("redact"); return; }
  }
  if (state.sel && (e.key === "Delete" || e.key === "Backspace")) { e.preventDefault(); deleteSelection(); return; }
  if (state.sel && e.key.startsWith("Arrow")) {
    e.preventDefault();
    const d = e.shiftKey ? 0.02 : 0.004;
    // arrows move in screen direction regardless of RTL page direction
    nudgeSelection(e.key === "ArrowLeft" ? -d : e.key === "ArrowRight" ? d : 0, e.key === "ArrowUp" ? -d : e.key === "ArrowDown" ? d : 0);
    return;
  }
  if (e.key === "Escape" && state.sel) { state.sel = null; renderEditor(); }
});

document.addEventListener("paste", (e) => {
  if (!state.guide || $("#modal-root").firstChild) return;
  const items = [...(e.clipboardData ? e.clipboardData.items : [])];
  const file = items.filter((i) => i.kind === "file" && /^image\//.test(i.type)).map((i) => i.getAsFile())[0];
  if (!file) return;
  e.preventDefault();
  const step = currentStep();
  if (step && !step.image) setImage(file);
  else {
    const fresh = addStep(state.current, newStep());
    state.current = fresh.id;
    setImage(file);
  }
});

window.addEventListener("beforeunload", (e) => {
  if (state.dirty || state.saving) { e.preventDefault(); e.returnValue = ""; }
});

$("#make-video").addEventListener("click", makeVideo);
$("#go-home").addEventListener("click", () => { location.hash = "#/"; });
window.addEventListener("hashchange", route);

(async function start() {
  try {
    await loadStatus();
  } catch (err) {
    $("#main").replaceChildren(h("div", { class: "loading-page" }, err.message));
    return;
  }
  route();
})();
