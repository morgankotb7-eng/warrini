# Warrini (ورّيني) — design draft v1

Status: draft for review. Owner: Mohamed Kotb. Builds on ehky v0.2.0 (github.com/morgankotb7-eng/ehky, MIT).

## 1. Overview

Warrini turns a written work procedure (a title plus numbered steps, each with Egyptian Arabic text and an optional screenshot) into a short narrated how-to video: 1920x1080 MP4, Egyptian Arabic voice, Arabic captions, the camera zooming toward the part of the screen that matters, an animated highlight ring, a step badge, and sensitive areas blurred out. Everything runs on the user's machine, free, open source (MIT), no account, no API key.

Two ways to use it:
- A local web app (`warrini`), opened in the browser: a polished RTL Arabic editor.
- A CLI (`warrini render guide.json`) for scripting and CI.

## 2. Problem

IT service desks, HR, finance and operations teams in Egypt write SOPs in Word with screenshots ("افتح الـ portal، دوس على Reset password، ..."). Staff do not read them; they call the service desk instead. Short videos work much better, but recording and editing a narrated video per procedure takes hours, needs a calm voice and editing skill, and every UI change means re-recording. Existing tools that turn steps into narrated videos (Guidde, Trupeer, Scribe) are paid, cloud-based (company screenshots leave the network), and none speaks Egyptian dialect.

## 3. Goals

G1. From steps + screenshots to a finished video in under 5 minutes of user effort for a 6-step guide.
G2. Video quality that looks professionally edited: smooth (no zoom jitter), consistent typography, correct Arabic shaping and direction, clean audio.
G3. Fully local by default: with the local voice (VoiceTut Tareq) no text or image leaves the machine. Visible "everything stays on your device" state.
G4. Editing one step re-renders fast: narration of unchanged steps is cached.
G5. Redaction is reliable: a blurred region never shows original pixels in any frame.
G6. Works on Windows, macOS and Linux with Python 3.11–3.13 and ffmpeg (with libass).
G7. Zero cost at runtime. No paid service of any kind.

## 4. Non-goals (v1)

- Screen recording / browser extension that captures clicks automatically.
- AI that writes the steps from screenshots (needs an LLM; cost/privacy). Possible later with a local model.
- Importing .docx SOPs (v1.1 candidate). v1 imports a folder of screenshots and plain text / Markdown numbered lists.
- Multi-user, accounts, cloud hosting, sharing links.
- Vertical (9:16) output. v1 is 16:9 only: screenshots are landscape.
- Translations to other languages / other voices beyond what ehky offers.
- PDF handout export (v1.1 candidate).

## 5. Users and journeys

Persona: Service desk team lead (non-developer comfortable installing a Python tool once) and team members who only use the browser UI.

J1 — First guide
1. Runs `warrini`. Browser opens on the Home screen: recent guides + "دليل جديد".
2. Types the title ("إزاي تغيّر الباسورد بتاعة الـ VPN").
3. Adds step 1: pastes a screenshot with Ctrl+V (or drag-drop / file picker), writes the narration text.
4. Draws a focus box on the screenshot (the button to click). Draws a blur box over the username.
5. Clicks "اسمع" on the step: hears it in Tareq's voice within seconds.
6. Repeats for the other steps; reorders by drag.
7. Clicks "اعمل الفيديو": progress bar with stage + percentage; then plays the video inline, "افتح الفولدر" and "حمّل".

J2 — UI changed in one step: opens guide, replaces step 3 screenshot, re-renders. Only step 3 is affected; voice comes from cache.

J3 — Quick import: "استورد فولدر صور" → one step per image (sorted by natural filename order), text empty, user fills text. Or paste a numbered list → one step per line.

J4 — CLI: `warrini render guides/vpn/guide.json -o vpn.mp4 --engine voicetut` in a script.

## 6. Technical plan

### 6.1 Stack

- Python 3.11+, packaged with setuptools like ehky. Console script `warrini`.
- Dependencies: `ehky @ git+https://github.com/morgankotb7-eng/ehky@v0.2.0` (voice, ASS captions, ffmpeg helpers), `Pillow>=10,<13` (image compositing). Optional extra `voicetut` passes through to `ehky[voicetut]`.
- External: ffmpeg + ffprobe with libass (same requirement as ehky; reuse `compose.require_tools`, `compose.has_libass`).
- Front end: one HTML file + one JS file + one CSS file, no build step, no framework, no CDN (works offline). Served from package data.
- Font: Cairo (SIL OFL 1.1) bundled as package data (`warrini/assets/fonts/Cairo-*.ttf` + `OFL.txt`) so video and UI look identical on every OS; ffmpeg `ass` filter gets `fontsdir` pointing there. UI loads it via @font-face from the local server.

### 6.2 Reuse from ehky (confirmed by reading ehky v0.2.0 source)

- `ehky.tts`: `Word`, `Narration`, `resolve_engine`, `voicetut_available`, `synthesize_voicetut(sentences, speaker, out)`, `synthesize_edge(text, voice, out, rate, attempts)`, `validate_voice/speaker/rate`, `DEFAULT_SPEAKER="Tareq"`, `DEFAULT_VOICE="ar-EG-ShakirNeural"`.
- `ehky.subs`: `group_lines(words, max_chars, max_pause)`, `events(lines, highlight, hold)`, `HIGHLIGHT`. Warrini writes its own ASS header/styles (needs extra styles for title card, badge, step title) but reuses grouping and per-word highlight event generation.
- `ehky.text`: `normalize`, `validate`, `split_lines` for narration text cleanup (CONFIRM exact signatures when implementing; read text.py).
- `ehky.compose`: `probe_duration`, `require_tools`, `has_libass`, `run`, `_esc_filter_path` logic (private: copy the 4-line function instead of importing a private name).
- Not reused: `compose.build_ffmpeg_args` (built for a different layout), `pipeline` (different input model), `ui` (different app), but the UI security model is copied.

### 6.3 Guide format (on disk)

A guide is a folder:

```
<guides_root>/<slug>/
  guide.json
  images/<uuid4>.<png|jpg|webp>     # files written by warrini only, original bytes kept
  out/<slug>.mp4                    # last render
  out/<slug>.srt                    # captions sidecar
```

`guides_root` default: `~/Warrini` (Windows: `%USERPROFILE%\Warrini`), override `--root`.

`guide.json` schema (version 1), validated on load and before render:

```json
{
  "schema": 1,
  "title": "string 1..120 chars",
  "intro": "optional narration for the title card, 0..400 chars (default: the title)",
  "outro": "optional narration for the end card, 0..300 chars (default: none, silent 2.0 s end card)",
  "theme": "navy | charcoal | forest | sand",
  "voice": {"engine": "voicetut | edge | auto", "speaker": "Tareq", "voice": "ar-EG-ShakirNeural", "rate": "+0%"},
  "steps": [
    {
      "id": "uuid4",
      "text": "narration, 1..600 chars",
      "image": "images/<uuid4>.png or null",
      "focus": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0} ,
      "redact": [{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}],
      "pause_after": 0.6
    }
  ],
  "updated_at": "ISO-8601"
}
```

Rules: rectangles are normalized to the image (0..1, w,h > 0.01, clamped to inside the image); `focus` optional (null = whole image, no zoom); `redact` max 20 per step; max 60 steps; `pause_after` 0.2..3.0. Image files: PNG/JPEG/WebP only, ≤ 25 MB, ≤ 50 megapixels, decoded with Pillow inside a try (decompression-bomb guard: `Image.MAX_IMAGE_PIXELS = 50_000_000`). Saves are atomic (write `guide.json.tmp`, `os.replace`).

### 6.4 Video composition

Timeline:
- Title card: 2.5 s minimum or intro narration length + 0.8 s. Shows title (Cairo Bold), small line "خطوات: N", theme background.
- Each step: lead-in 0.5 s (camera starts moving, no voice) + narration length + `pause_after`. Step with no image: a text card (step badge + text large).
- End card: outro narration length + 0.8 s, or 2.0 s silent. Shows "✓ خلصت" style check icon (drawn shape, not emoji) and the title.
- Transitions: 0.35 s crossfade between segments (done in the frame renderer, not ffmpeg xfade, so timing stays exact).

Frame rendering (Python, Pillow), why: ffmpeg `zoompan`/`crop` round to integer pixels and make slow zooms jitter. Rendering frames with `Image.transform(..., Image.AFFINE, resample=BICUBIC)` gives sub-pixel motion. Frames (RGB, 1920x1080, 30 fps) are piped raw to ffmpeg stdin (`-f rawvideo -pix_fmt rgb24 -s 1920x1080 -r 30 -i -`). ffmpeg adds the ASS captions/text layer (libass does all Arabic shaping) and the audio, and encodes H.264 (`-preset veryfast -crf 18`, yuv420p, AAC 160k, +faststart).

Per-step frame content:
1. Background: theme gradient (pre-rendered once per render).
2. Screenshot "card": redacted screenshot (see 6.5) fitted into a 1600x900 safe box, rounded corners 18 px, soft drop shadow (pre-rendered once per step).
3. Camera: start at full card view; ease (cubic in-out) during the first 1.2 s of narration to a zoom that frames `focus` with 18% padding, max zoom 2.4x, min 1.0x; hold; zoom back out during the last 0.6 s before the transition only if the next step uses a different image (else crossfade from zoomed state).
4. Highlight ring: rounded rectangle around `focus` (in screen coordinates after camera transform), 6 px stroke in theme accent, appears with a 0.4 s scale-in (1.15 → 1.0) and fade, then a slow pulse (opacity 0.85–1.0, 1.6 s period). Drawn on a 2x supersampled RGBA layer then downscaled for anti-aliasing; cached per (step, scale bucket) to keep render fast.
5. Dim outside focus: 35% dark overlay outside the ring (spotlight) after the zoom settles.
6. Step badge and step text title: drawn by libass (ASS events with their own styles), top-right (RTL start side): circle with step number "١"… (Arabic-Indic digits in video, chosen for the Egyptian audience; captions use the same) and "الخطوة ٢ من ٦".
7. Captions: bottom, centered, max 2 lines, current word highlighted (ehky style), semi-opaque box behind text for readability over screenshots.
8. Progress bar: 6 px bar at the bottom edge showing step progress (drawn in Pillow).

Performance target: ≤ 1.5x real time on a 4-core laptop (a 60 s video renders in ≤ 90 s). Optimizations: static layers pre-rendered per segment; per frame only the affine transform + overlay composite; when the camera is still, reuse the previous frame bytes. Measured in a benchmark test (not a CI gate; logged).

### 6.5 Redaction

Applied once per step to the decoded source image before any scaling/camera work: for each rectangle, pixelate (downscale to 1/24, upscale NEAREST) then Gaussian blur radius 12, then a 20% solid overlay in the theme color. The original image file on disk is not modified. Because every frame is derived from the redacted image, no frame can show original pixels. Test: render a step whose redacted area holds a high-contrast checkerboard; decode sampled frames with ffmpeg and assert the region's variance is below a threshold in all of them.

### 6.6 Narration and audio

- One TTS call per segment (intro, each step, outro), so each segment's timing is exact.
- Cache: `~/.cache/warrini/tts/<sha256(engine|speaker|voice|rate|normalized_text)>.wav` + `.json` (word timings). Engine version in the key (ehky version string). Cache size cap 1 GB, LRU by mtime on startup.
- Edge MP3 output is converted to WAV 48 kHz mono s16 with ffmpeg; VoiceTut WAV resampled the same way. All segments then assembled in Python (`wave` module) with exact silence padding, giving one `narration.wav` and a word list with global times for captions.
- Loudness: final mix passes through ffmpeg `loudnorm=I=-16:TP=-1.5:LRA=11` (single pass) so all guides sound equally loud.
- Privacy: if engine resolves to `edge`, the UI shows a clear notice "الصوت ده بيبعت النص لـ Microsoft عشان يتولّد" and a setting "خلي كله على الجهاز" (default ON when VoiceTut is installed) that forbids edge. The CLI has `--offline` to refuse edge.
- Failure: if TTS fails for a step, render stops with an Arabic error naming the step number; partial cache stays valid.

### 6.7 Web app

Server: stdlib `ThreadingHTTPServer` bound to 127.0.0.1, random free port, opens the browser. Security model copied from ehky ui: Host header must be `127.0.0.1:<port>` or `localhost:<port>`; every API call needs header `X-Warrini-Token` with a per-run random token embedded in the served page; JSON bodies ≤ 1 MB; image uploads ≤ 25 MB raw bytes via `PUT /api/guides/<slug>/images` (body = bytes, server sniffs magic bytes, renames to uuid4); strict CSP (`default-src 'self'; img-src 'self' blob: data:; media-src 'self'; style-src 'self'; script-src 'self'`); `X-Content-Type-Options: nosniff`; slugs validated `^[a-z0-9-]{1,60}$` and resolved paths must stay inside guides_root.

API (JSON):
- `GET /api/guides` → list (slug, title, steps count, updated_at, thumbnail url).
- `POST /api/guides` {title} → create; slug from transliterated/ASCII-safe title or `guide-<n>`.
- `GET/PUT /api/guides/<slug>` → load / save whole guide (optimistic concurrency: `updated_at` must match, else 409).
- `DELETE /api/guides/<slug>` → moves folder to `<root>/.trash/`.
- `PUT /api/guides/<slug>/images` → {image path, width, height}.
- `GET /files/<slug>/images/<name>`, `GET /files/<slug>/out/<name>` (range requests supported for video seeking).
- `POST /api/guides/<slug>/preview-voice` {step_id} → synthesizes (cached) and returns audio URL.
- `POST /api/guides/<slug>/render` → starts job; one render at a time globally (409 if busy). `GET /api/jobs/current` → {state, stage, percent, eta_s, error}. `POST /api/jobs/current/cancel`.
- `POST /api/import/text` {text} → steps parsed from numbered/bulleted lines.
- `GET /api/status` → ffmpeg ok, libass ok, voicetut available, engine that will be used, offline mode.

UI (Arabic RTL, Cairo, Western digits in the UI per arabic-rtl skill default; Arabic-Indic digits only inside the video):
- Home: grid of guide cards with thumbnail, title, step count, last edit; "دليل جديد"; "استورد".
- Editor, three columns (RTL order): steps rail (thumbnails, numbers, drag to reorder, add/duplicate/delete with undo toast) | canvas (screenshot, tool bar: select, focus box, blur box; boxes are draggable/resizable with handles; keyboard nudge) | inspector (step text with character count and estimated seconds, pause slider, "اسمع" button, voice settings in guide panel).
- Live preview: canvas shows a still of how the step will look (focus spotlight, ring, badge) using the same geometry math as the renderer (shared numbers documented in one JSON constants file served to the UI).
- Autosave 800 ms after the last change; status pill "محفوظ" / "بيحفظ…" / "مش محفوظ — جرّب تاني".
- Render panel: stages (الصوت، الصور، الفيديو) with percent and ETA, cancel; result: inline player, "افتح الفولدر", file size, duration.
- Health banner on first run if ffmpeg/libass/voice missing, with exact fix commands per OS.
- Empty, loading and error states designed for every screen. Keyboard: Ctrl+V paste image into current step (or new step if current has an image), Ctrl+Z/Ctrl+Shift+Z undo/redo in editor (in-memory history of 100 states), Delete removes selected box.
- Accessibility: all controls reachable by keyboard, visible focus ring, labels on icon buttons, contrast ≥ 4.5:1.

### 6.8 CLI

```
warrini                         # opens the web app (default)
warrini ui [--root DIR] [--port N] [--no-browser]
warrini render GUIDE.json [-o OUT.mp4] [--engine auto|voicetut|edge] [--offline] [--quality fast|high]
warrini import-folder DIR --title T [--root DIR]   # creates a guide, one step per image
warrini doctor                  # checks ffmpeg, libass, fonts, voice
warrini --version
```
Exit codes: 0 ok, 1 user/validation error (Arabic message), 2 missing tool, 3 render failure.

### 6.9 Package layout

```
warrini/
  pyproject.toml  README.md  README.ar.md  LICENSE  CHANGELOG.md
  warrini/__init__.py          # __version__ = "0.1.0"
  warrini/__main__.py
  warrini/cli.py
  warrini/guide.py             # schema, load/save/validate, slug, import parsers
  warrini/geometry.py          # fit, camera keyframes, easing, focus framing (pure)
  warrini/timeline.py          # segments, durations, global word times (pure)
  warrini/narrate.py           # TTS per segment, cache, wav assembly
  warrini/layers.py            # Pillow: background, card, redaction, ring, spotlight, progress
  warrini/frames.py            # frame generator (yields bytes)
  warrini/captions.py          # ASS document: styles + events (badge, titles, captions)
  warrini/render.py            # orchestrates, ffmpeg argv (pure builder) + process, progress, cancel
  warrini/server.py            # HTTP server, API, security
  warrini/doctor.py
  warrini/web/index.html  app.js  app.css  constants.json  icons.svg
  warrini/assets/fonts/Cairo-Regular.ttf  Cairo-Bold.ttf  OFL.txt
  tests/ ...
  .github/workflows/ci.yml
  docs/designs/design.md (approved version of this file)
  docs/demo.mp4  docs/screenshot.png
  examples/vpn-password/guide.json + images (fictional UI, made by us)
```

## 7. Alternatives considered

- ffmpeg-only composition (zoompan, overlay, drawbox): rejected, integer rounding makes slow zooms jitter and drawbox has no anti-aliasing; also hard to test.
- Remotion / Motion Canvas (Node + headless Chrome): beautiful, but heavy install (Chrome download, Node), slower, and Remotion's license is not plain open source for companies. Rejected for a tool non-developers install.
- Electron/Tauri desktop app: nicer packaging, but large build/sign burden on 3 OS. Local web app now; a packaged installer can come later.
- Pillow for Arabic text: needs libraqm/FriBiDi, missing from some Windows setups → broken shaping. All text goes through libass instead.
- Cloud TTS with better voices (ElevenLabs etc.): costs money and sends company text out. Rejected by constraint.
- Extending ehky itself: different input model and UI; keeping ehky small and focused is better. Warrini depends on it.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| VoiceTut not installed (needs PyTorch, ~GBs) | `auto` falls back to edge with a visible privacy notice; doctor explains install; offline mode refuses edge with a clear message. |
| VoiceTut slow on CPU | Per-step cache; "اسمع" previews only one step; progress shows voice stage separately. |
| ffmpeg without libass (some Windows builds) | doctor detects (`has_libass`); render refuses with fix instructions. |
| Render speed with Python frames | Pre-rendered static layers, still-frame reuse, benchmark logged; quality "fast" uses 1280x720 preview. |
| Large screenshots / decompression bombs | Size and pixel caps, Pillow MAX_IMAGE_PIXELS, magic-byte sniffing. |
| Local web server attacked from a malicious website (CSRF / DNS rebinding) | 127.0.0.1 bind, Host check, per-run token header, no CORS, CSP. |
| Path traversal via slug/image names | Strict regex + resolved-path containment check, server-generated file names. |
| Arabic-Indic digits in ASS on some fonts | Cairo bundled; tested in render test by checking libass runs with the font (fontsdir). |
| Concurrent edits (two tabs) | `updated_at` optimistic concurrency → 409 → UI offers reload. |
| ehky API change | Pinned to tag v0.2.0. |
| Redaction leak | Applied before all transforms; frame-sampling test. |
| Disk filling with cache | 1 GB LRU cap. |

## 9. Implementation phases

Each phase ends green on CI (ruff + pytest on Linux/Windows/macOS, Python 3.11/3.12/3.13).

P1 — Core model (pure, no ffmpeg): `guide.py` (schema, validation, atomic save, slug, text import, folder import), `geometry.py`, `timeline.py`. Tests: schema accept/reject table; rectangle clamping; focus framing keeps the focus box fully inside the frame with padding for 30 random boxes; easing endpoints; timeline durations sum; slug rules; numbered-list parser (Arabic and Western digits, "١-", "1)", "-", "•").

P2 — Narration: `narrate.py` with cache and WAV assembly; TTS behind an interface so tests use a fake engine that writes silence of estimated length. Tests: cache hit/miss keys, assembly offsets exact to 1 ms, offline mode refuses edge.

P3 — Render: `layers.py`, `frames.py`, `captions.py`, `render.py`. Tests: ffmpeg argv builder snapshot; ASS output contains RTL styles and Encoding -1; tiny end-to-end render (320x180 via a test-only scale factor, fake TTS) → probe duration within ±0.1 s of the timeline; redaction checkerboard test; cancel stops ffmpeg and removes partial file.

P4 — Server + web app: `server.py`, web files. Tests: security (bad Host → 403, missing token → 403, traversal → 404, oversize → 413, non-image bytes → 415), API round trip, 409 on stale save, one render at a time. Manual + Playwright screenshot pass at 1280/1440/1920 widths with long Arabic strings (not in CI).

P5 — Polish and ship: doctor, README (Arabic + English), example guide with fictional screenshots, demo video rendered by Warrini itself, CHANGELOG, v0.1.0 tag and GitHub release.

## 10. Open questions (CONFIRM)

- Q1: ehky `text.py` exact function signatures (read before P2).
- Q2: Is ehky installable as a git dependency on Windows CI without extra steps (git is present on GitHub runners: yes; on user machines: README tells them to install from the GitHub URL, which needs git — alternative: publish ehky to PyPI). Decide in P5.
- Q3: Cairo font files: download from the official Google Fonts GitHub repo (OFL) at build time of the repo, commit into package data.

---

## 11. Decisions after review (authoritative — where this section conflicts with sections 1–10, this section wins)

Three fresh reviews (comprehension, hostile design, implementation readiness) produced ~80 findings. Accepted decisions:

### 11.1 Rendering and performance
- D1. Frames come from a per-segment **2x canvas** (background + card + shadow, built from the full-resolution redacted source). Each frame is one `Image.resize((W,H), BILINEAR|BICUBIC, box=<float viewport>)` (measured 7–21 ms vs 120 ms for AFFINE). The canvas is padded so the box never leaves it.
- D2. Camera = viewport rect in 1920x1080 card space. Target: focus mapped to screen, padded `0.18*max(fw,fh)`, expanded to 16:9, `z=clamp(1920/pw,1.0,2.4)`, centred on focus then shifted inside card bounds. Edges interpolated with cubic in-out easing. Move window = segment start .. +1.2 s (covers 0.5 s lead-in + first 0.7 s of speech).
- D3. **No pulse.** Ring + spotlight appear after the camera settles: 0.4 s scale-in (1.12→1.0) + fade, drawn per frame on a small 2x crop; afterwards static. Static hold frames are byte-identical and reused (written again without recomputing). Ring: focus on screen outset 10 px, radius 14, stroke 6, theme accent. Spotlight 35% black outside the ring.
- D4. Layout (1920x1080, scaled by a `scale` factor for fast/test): card box x 180..1740 y 125..905; badge row y 30..110; caption band 930..1040; progress bar y 1074..1080. All constants in `warrini/web/constants.json`, shared by renderer and editor preview.
- D5. Timeline: every segment duration rounded **up** to whole frames at 30 fps; audio padded to the same boundaries; one timing table drives geometry, audio and captions. Narration starts at segment start + 0.5 s (steps) / 0.4 s (title, end). Tail = `max(pause_after, 0.6)`. Crossfade 0.35 s sits at the end of the tail (segments butt, total length unchanged). Zoom-out (only when the next step has a different image or different redactions) runs in `[b-0.95, b-0.35]`; ring/spotlight fade out over 0.2 s before it. Same image + same redactions → no zoom-out, no fade, camera continues. Title card `max(2.5, 0.4+n+0.8)`; end card `0.4+n+0.8` or 2.0 s silent.
- D6. Quality: `high` 1920x1080 crf 18 (default), `fast` 1280x720 crf 23 (UI "معاينة سريعة"); tests use scale 1/6 (320x180). ASS PlayRes always 1920x1080.
- D7. Benchmark: ms/frame logged; CI gate on Linux only at ≤ 60 ms/frame for the 320x180 test scale × factor — gate only on a regression of 2x vs a stored baseline (CI machines vary).

### 11.2 Text and captions (libass)
- D8. **Every ASS event starts with U+200F (RLM)** so lines that begin with an English word ("VPN", "Outlook") stay right-to-left. Render test checks pixel position of a coloured English first word.
- D9. Captions: one line only (`group_lines(words, max_chars_for(1920, 50, margin=120), 0.6)`), opaque box (BorderStyle 3, box colour in OutlineColour). Per-word highlight only when narration timings are exact (Edge); VoiceTut (estimated timings) → whole-line captions, no word highlight.
- D10. Digits: captions keep text exactly as typed. Arabic-Indic digits only in badge, "الخطوة ٢ من ٦", "خطوات: ٦".
- D11. Styles (Cairo; static Regular/SemiBold/Bold instances made with fontTools from Google Fonts Cairo variable font, OFL; SHA-256 in `assets/fonts/SOURCES.txt`): Badge (vector circle `\p1` r38 at 1810,70 + digit Bold 46), StepLabel 34 at `\an6\pos(1750,70)`, Title Bold 84 `\an5\pos(960,470)`, TitleSub 40 `\an5\pos(960,590)`, Caption Bold 50 MarginV 50, CardText Bold 60 `\an5\pos(960,520)`. Encoding -1 everywhere.
- D12. Text-only step card: lines broken in Python with explicit `\N` (≤ 34 chars/line, ≤ 5 lines, extra text narration-only, display capped 200 chars); bottom captions suppressed on that step.
- D13. ffmpeg runs with `cwd` = render temp dir, fonts copied to `fonts/`, ASS written as `subs.ass` → no path escaping problems on any OS.
- D14. All user strings go through an ASS escape (`\`→removed, `{}`→`()`, newlines → space).

### 11.3 Voice
- D15. **Offline by default, always.** Edge (online) is opt-in per guide (`voice.allow_online: true`), shown with a privacy notice; preview and CLI obey it. `engine=auto` + offline + no VoiceTut → clear Arabic error with install steps (exit 2).
- D16. VoiceTut runs in a **long-lived worker process** (`multiprocessing`, spawn) that loads the model once; requests go over a queue; progress per sentence; **cancel kills the worker**. Model revision pinned via `revision=` hf kwarg (CONFIRM it is honoured) and part of the cache key. First run downloads the model — doctor and README say so, including proxy env vars.
- D17. Edge: always written to `.mp3` then converted to 48 kHz mono s16 WAV; empty word list → `estimate_words`. Runs in a thread (it uses `asyncio.run`).
- D18. Cache: key = sha256(resolved engine, engine-specific fields only, model revision / ehky version, `NARRATE_VERSION`, `normalize(text)`). WAV then JSON written tmp→fsync→`os.replace`; WAV without JSON = miss; header/length checked on read, bad entries deleted; hits touch mtime; LRU eviction to 1 GB after each render; per-key lock.
- D19. Loudness: narration WAV is normalized **two-pass** `loudnorm` (`linear=true`, I=-16, TP=-1.5) then `aresample=48000`, before the video encode.
- D20. Segment length for VoiceTut = WAV length after trimming ehky's trailing sentence gap. `ehky.text.normalize` cleans text; VoiceTut input chunks = `text.split_lines`.

### 11.4 Redaction and images
- D21. Default redaction = **opaque fill**: median colour of a 6 px ring just outside the box, rounded 6 px corners, subtle 2% noise. No blur (pixelated text can be recovered). Boxes rounded **outward** (+2 px).
- D22. Upload pipeline: sniff magic bytes (PNG/JPEG/WebP), `Image.open` → check size ≤ 50 MP and single frame **before** `load()`, `ImageOps.exif_transpose`, convert to RGB, store as PNG under a uuid4 name (strips EXIF/GPS). Return width/height of the upright image.
- D23. Replacing a step's screenshot marks its redactions `"review": true`; render refuses until the user confirms them in the editor.
- D24. Thumbnails and the editor canvas show the **redacted** preview (server endpoint renders it); originals stay in `images/` and README says so. Orphan images (unreferenced, > 24 h) are deleted when a guide is opened.
- D25. Test: checkerboard under a redaction; sample every 5th frame + all zoom frames; inside-box luma std-dev < 4 versus > 100 without redaction.

### 11.5 Server and storage
- D26. Auth: browser opens `/launch?code=<one-time>`; server sets an `HttpOnly; SameSite=Strict; Path=/` cookie with the per-run secret and redirects to `/`. Every request (pages, API, media) needs the cookie; every non-GET API call also needs header `X-Warrini-Token` (value from a `<meta>` tag; strict CSP, no inline script/style) against CSRF. Host header check stays. Fixed default port 8765 with fallback to a free port.
- D27. Range requests (single + suffix, 206/416, `Accept-Ranges`) for videos; outputs are versioned `out/<slug>-<rev>.mp4` (+ `.srt`), keep last 3, so no file is ever replaced while playing.
- D28. ffmpeg via `Popen(stdin=PIPE, stderr=<log file>, cwd=tmp)`, `-progress` not needed (we count frames written); `BrokenPipeError`/`OSError` on write → error with the last 2000 chars of the log. Cancel: close stdin, terminate, wait 5 s, kill; delete `.part`; previous video untouched.
- D29. Guide revision: integer `rev`, server increments; client keeps one save in flight and coalesces edits; 409 → "reload / keep mine" dialog. Render snapshots guide.json + images into the temp dir at start.
- D30. Validation: save allows empty text and 0 steps; render requires ≥ 1 step and non-empty text (error names the step). Unknown keys dropped. Rect clamp: x,y to [0,0.99], `w=min(w,1-x)`, `h=min(h,1-y)`, reject ≤ 0.01. Defaults: theme `navy`, pause_after 0.6, voice engine `auto`, speaker Tareq, voice ar-EG-ShakirNeural, rate +0%, allow_online false, intro null (= title narrated), outro null (= silent end card).
- D31. Slugs: ASCII `[a-z0-9]+` runs of the lowercased title joined by `-`, ≤ 40 chars; else `guide-<n>` smallest free; collisions `-2`…; never changes; Windows reserved names rejected; creation under a lock. Delete → `.trash/<slug>-<timestamp>`.
- D32. Settings in `<root>/.warrini/settings.json` (port, default theme, quality).
- D33. Body limits: missing/invalid Content-Length → 411/400; JSON ≤ 1 MB; images ≤ 25 MB.
- D34. Import text: marker regex `^\s*([0-9٠-٩]+[.)\-–]|[-•*])\s+`; unmarked lines join the previous step; > 60 steps → error. Folder import (CLI and UI multi-file upload): natural sort with Arabic-Indic digits converted.
- D35. New endpoints: `POST /api/open-folder` (opens the out folder with `os.startfile` / `open` / `xdg-open`, path fixed server-side), `GET /audio/<sha>.wav` for previews, `GET /api/guides/<slug>/preview/<step_id>.png` (redacted still preview), `POST /api/settings`. Preview-voice takes the current text in the body, not the saved one.
- D36. SRT sidecar written by `captions.py` from the same lines (UTF-8 BOM).

### 11.6 Scope, install, persona
- D37. Persona clarified: every user runs Warrini on their own machine (no shared server).
- D38. ehky stays a git dependency for v0.1.0 (publishing to PyPI needs an account action by the owner); README documents `pip install git+https://...`. A Windows portable zip (embedded Python + ffmpeg with libass + fonts + `warrini.bat`) is phase P6.
- D39. macOS: Homebrew ffmpeg may lack libass; `doctor` detects and prints a working alternative; CI end-to-end render runs where libass is present and is skipped with an explicit reason otherwise (never silently).
- D40. Licences to verify before release: VoiceTut weights and OmniVoice; edge-tts is an unofficial endpoint — documented as optional online voice.

### 11.7 Rejected findings
- R1. HMAC-signed media URLs: replaced by the SameSite=Strict HttpOnly cookie (D26), simpler and equally safe for a localhost app.
- R2. Making the benchmark a hard CI gate: CI runners vary too much; regression-only gate (D7).
