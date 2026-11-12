"""The guide model: schema, validation, storage, slugs and import parsers.

A guide is a folder holding guide.json and an images/ folder. guide.json is plain data (dicts),
cleaned by `clean()` on every save and checked by `check_renderable()` before a render.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ehky import text as ehky_text
from ehky import tts

from .consts import DEFAULT_THEME, LIMITS, THEMES

SCHEMA = 1
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
IMAGE_RE = re.compile(r"^images/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.png$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
ENGINES = ("auto", "voicetut", "edge")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_TO_AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_create_lock = threading.Lock()


class GuideError(ValueError):
    """A problem the user can fix. The message is Arabic and shown as is."""


def new_id() -> str:
    return str(uuid.uuid4())


def to_western_digits(value: str) -> str:
    return value.translate(_AR_DIGITS)


def to_arabic_digits(value: str | int) -> str:
    return str(value).translate(_TO_AR)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --------------------------------------------------------------------------- cleaning


def _text(value: object, limit: int) -> str:
    return ehky_text.normalize(str(value or ""))[:limit]


def _number(value: object, default: float, low: float, high: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(max(number, low), high)


def clean_rect(value: object) -> dict | None:
    """Clamp a normalized rectangle into the image. None when unusable."""
    if not isinstance(value, dict):
        return None
    try:
        x, y, w, h = (float(value[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if any(v != v for v in (x, y, w, h)):
        return None
    x = min(max(x, 0.0), 0.99)
    y = min(max(y, 0.0), 0.99)
    w = round(min(w, 1.0 - x), 6)
    h = round(min(h, 1.0 - y), 6)
    if w <= LIMITS["min_rect"] or h <= LIMITS["min_rect"]:
        return None
    return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}


def _clean_voice(value: object) -> dict:
    data = value if isinstance(value, dict) else {}
    engine = data.get("engine") if data.get("engine") in ENGINES else "auto"
    voice = {
        "engine": engine,
        "speaker": tts.DEFAULT_SPEAKER,
        "voice": tts.DEFAULT_VOICE,
        "rate": "+0%",
        "allow_online": bool(data.get("allow_online", False)),
    }
    for key, check in (("speaker", tts.validate_speaker), ("voice", tts.validate_voice), ("rate", tts.validate_rate)):
        with contextlib.suppress(ValueError):
            voice[key] = check(str(data.get(key, voice[key])))
    return voice


def _clean_step(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    step_id = str(value.get("id", ""))
    if not UUID_RE.match(step_id):
        step_id = new_id()
    image = value.get("image")
    image = image if isinstance(image, str) and IMAGE_RE.match(image) else None
    redact = []
    for item in value.get("redact") or []:
        rect = clean_rect(item)
        if rect is not None:
            redact.append(rect)
    return {
        "id": step_id,
        "text": _text(value.get("text"), LIMITS["step_text"]),
        "image": image,
        "focus": clean_rect(value.get("focus")) if image else None,
        "redact": redact[: LIMITS["redact"]] if image else [],
        "review": bool(value.get("review", False)) and image is not None,
        "pause_after": _number(value.get("pause_after"), 0.6, LIMITS["pause_min"], LIMITS["pause_max"]),
    }


def clean(data: object) -> dict:
    """Lenient: whatever the editor sends becomes a valid guide (unknown keys dropped)."""
    if not isinstance(data, dict):
        raise GuideError("بيانات الدليل مش مفهومة")
    if data.get("schema", SCHEMA) != SCHEMA:
        raise GuideError("نسخة ملف الدليل دي مش مدعومة")
    steps = [s for s in (_clean_step(v) for v in data.get("steps") or []) if s is not None]
    if len(steps) > LIMITS["steps"]:
        raise GuideError(f"أقصى عدد خطوات {LIMITS['steps']}")
    seen: set[str] = set()
    for step in steps:  # ids must be unique (duplicated steps from an old client)
        if step["id"] in seen:
            step["id"] = new_id()
        seen.add(step["id"])
    intro = data.get("intro")
    outro = data.get("outro")
    return {
        "schema": SCHEMA,
        "title": _text(data.get("title"), LIMITS["title"]),
        "intro": _text(intro, LIMITS["intro"]) or None if intro is not None else None,
        "outro": _text(outro, LIMITS["outro"]) or None if outro is not None else None,
        "theme": data.get("theme") if data.get("theme") in THEMES else DEFAULT_THEME,
        "voice": _clean_voice(data.get("voice")),
        "steps": steps,
        "rev": int(data["rev"]) if isinstance(data.get("rev"), int) and data["rev"] >= 0 else 0,
        "updated_at": str(data.get("updated_at") or now_iso()),
    }


def check_renderable(guide: dict, folder: Path | None = None) -> None:
    """Strict: what a render needs. Raises GuideError naming the step to fix."""
    if not guide["title"]:
        raise GuideError("اكتب عنوان للدليل الأول")
    if not guide["steps"]:
        raise GuideError("الدليل مفيهوش خطوات")
    for number, step in enumerate(guide["steps"], 1):
        if not step["text"]:
            raise GuideError(f"الخطوة {number} مفيهاش كلام يتقال")
        if step["review"]:
            raise GuideError(f"الخطوة {number}: الصورة اتغيرت، راجع أماكن التغطية وأكّدها الأول")
        if step["image"] and folder is not None and not (folder / step["image"]).is_file():
            raise GuideError(f"صورة الخطوة {number} مش موجودة")


# --------------------------------------------------------------------------- storage


def slug_for(title: str, taken: set[str]) -> str:
    """ASCII words of the title, else guide-<n>. Never changes after creation."""
    words = re.findall(r"[a-z0-9]+", to_western_digits(title).lower())
    base = "-".join(words)[:40].strip("-")
    if not base or base in WINDOWS_RESERVED or not SLUG_RE.match(base):
        n = 1
        while f"guide-{n}" in taken:
            n += 1
        return f"guide-{n}"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug)) and len(slug) <= 60 and slug not in WINDOWS_RESERVED


def _replace(src: Path, dst: Path, attempts: int = 10) -> None:
    """os.replace with retries: on Windows an antivirus or a reader can hold the file briefly."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        _replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class Store:
    """All guides live under one root folder."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, slug: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(slug, threading.Lock())

    def folder(self, slug: str) -> Path:
        if not valid_slug(slug):
            raise GuideError("اسم الدليل مش صحيح")
        path = (self.root / slug).resolve()
        if path.parent != self.root:
            raise GuideError("اسم الدليل مش صحيح")
        return path

    def slugs(self) -> list[str]:
        return sorted(
            p.name for p in self.root.iterdir() if p.is_dir() and valid_slug(p.name) and (p / "guide.json").is_file()
        )

    def create(self, title: str) -> tuple[str, dict]:
        with _create_lock:
            taken = {p.name for p in self.root.iterdir()}
            slug = slug_for(title, taken)
            folder = self.root / slug
            (folder / "images").mkdir(parents=True)
            (folder / "out").mkdir()
            guide = clean({"title": title, "steps": []})
            write_atomic(folder / "guide.json", _dump(guide))
        return slug, guide

    def load(self, slug: str) -> dict:
        path = self.folder(slug) / "guide.json"
        if not path.is_file():
            raise FileNotFoundError(slug)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GuideError("ملف الدليل باظ ومش قادر أقراه") from exc
        return clean(data)

    def save(self, slug: str, data: dict, force: bool = False) -> dict:
        """Optimistic concurrency on the integer rev. Returns the stored guide (rev + 1)."""
        with self.lock(slug):
            current = self.load(slug)
            guide = clean(data)
            if not force and guide["rev"] != current["rev"]:
                raise ConflictError(current)
            guide["rev"] = current["rev"] + 1
            guide["updated_at"] = now_iso()
            write_atomic(self.folder(slug) / "guide.json", _dump(guide))
            return guide

    def delete(self, slug: str) -> None:
        folder = self.folder(slug)
        trash = self.root / ".trash"
        trash.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        _replace(folder, trash / f"{slug}-{stamp}")

    def orphans(self, slug: str, guide: dict, older_than: float = 24 * 3600) -> list[Path]:
        """Images no step uses any more (kept a day so undo stays safe)."""
        used = {s["image"] for s in guide["steps"] if s["image"]}
        cutoff = time.time() - older_than
        images = self.folder(slug) / "images"
        return [p for p in images.glob("*.png") if f"images/{p.name}" not in used and p.stat().st_mtime < cutoff]


class ConflictError(Exception):
    def __init__(self, current: dict) -> None:
        super().__init__("guide changed elsewhere")
        self.current = current


def _dump(guide: dict) -> bytes:
    return json.dumps(guide, ensure_ascii=False, indent=2).encode("utf-8")


# --------------------------------------------------------------------------- import

_MARKER = re.compile(r"^\s*(?:[0-9٠-٩۰-۹]+\s*[.)\-–:]|[-•*▪●])\s*")


def parse_steps(raw: str) -> list[str]:
    """Numbered or bulleted lines → step texts. Unmarked lines continue the previous step."""
    steps: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = _MARKER.match(line)
        body = ehky_text.normalize(line[match.end() :] if match else line)
        if not body:
            continue
        if match or not steps:
            steps.append(body)
        else:
            steps[-1] = f"{steps[-1]} {body}"
    if len(steps) > LIMITS["steps"]:
        raise GuideError(f"لقيت {len(steps)} خطوة، والأقصى {LIMITS['steps']}")
    return [s[: LIMITS["step_text"]] for s in steps]


def natural_key(name: str) -> list:
    parts = re.split(r"(\d+)", to_western_digits(name).lower())
    return [int(p) if p.isdigit() else p for p in parts]


def new_step(text: str = "", image: str | None = None) -> dict:
    return _clean_step({"id": new_id(), "text": text, "image": image})  # type: ignore[return-value]
