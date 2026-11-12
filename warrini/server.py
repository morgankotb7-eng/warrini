"""The local web app. Binds to 127.0.0.1 only.

Security model
- The browser is opened on /launch?code=<per-run code>. That sets an HttpOnly, SameSite=Strict
  cookie holding the per-run secret and redirects to /. Every other request needs the cookie,
  so other local users and websites cannot read guides, screenshots or videos.
- Every state-changing request also needs the X-Warrini-Token header (the page gets it from a
  <meta> tag), and a matching Origin when the browser sends one: no cross-site writes.
- The Host header must be 127.0.0.1:<port> or localhost:<port> (blocks DNS rebinding).
- Strict CSP (no inline script or style), nosniff, no-referrer, frame-ancestors none.
- Paths come from validated slugs and server-generated file names only.
"""

from __future__ import annotations

import contextlib
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ehky import tts

from . import __version__, layers, narrate, render, system
from .guide import ConflictError, GuideError, Store, check_renderable, clean, new_id, parse_steps, write_atomic
from .timeline import estimate_seconds

DEFAULT_PORT = 8765
MAX_JSON = 1024 * 1024
COOKIE = "warrini_session"
WEB = files("warrini").joinpath("web")
FONTS = files("warrini").joinpath("assets/fonts")
STATIC = {"app.js": "text/javascript", "app.css": "text/css", "constants.json": "application/json"}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "media-src 'self' blob:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
FILE_RE = re.compile(r"^/files/([a-z0-9-]{1,60})/(images|out)/([A-Za-z0-9_.-]{1,120})$")
AUDIO_RE = re.compile(r"^/audio/([0-9a-f]{64})\.wav$")
GUIDE_RE = re.compile(r"^/api/guides/([a-z0-9-]{1,60})(/[a-z-]+)?$")


class Job:
    """At most one render at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.state = "idle"  # idle | running | done | error | cancelled
        self.slug = ""
        self.stage = ""
        self.percent = 0.0
        self.eta: float | None = None
        self.error = ""
        self.detail = ""
        self.video = ""
        self.duration = 0.0
        self.size = 0
        self.thread: threading.Thread | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "state": self.state,
                "slug": self.slug,
                "stage": self.stage,
                "percent": self.percent,
                "eta": None if self.eta is None else round(self.eta),
                "error": self.error,
                "detail": self.detail,
                "video": self.video,
                "duration": round(self.duration, 1),
                "size": self.size,
            }


class App:
    def __init__(self, root: Path, port: int, narrator: narrate.Narrator | None = None) -> None:
        self.store = Store(root)
        self.port = port
        self.secret = secrets.token_urlsafe(32)
        self.launch_code = secrets.token_urlsafe(24)
        self.narrator = narrator or narrate.Narrator(ffmpeg=shutil.which("ffmpeg") or "ffmpeg")
        self.job = Job()
        self.page = WEB.joinpath("index.html").read_text(encoding="utf-8")
        self.stop = lambda: None  # set by serve(): shuts the server down

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        ffmpeg = shutil.which("ffmpeg")
        voicetut = tts.voicetut_available()
        return {
            "version": __version__,
            "ffmpeg": bool(ffmpeg),
            "libass": bool(ffmpeg) and system.has_libass(ffmpeg),
            "voicetut": voicetut,
            "speakers": tts.VOICETUT_SPEAKERS,
            "edge_voices": tts.EDGE_VOICES,
            "root": str(self.store.root),
            "platform": sys.platform,
        }

    # ------------------------------------------------------------------ guides

    def summary(self, slug: str) -> dict | None:
        try:
            guide = self.store.load(slug)
        except (GuideError, FileNotFoundError):
            return None
        first = next((s["image"] for s in guide["steps"] if s["image"]), None)
        videos = sorted((self.store.folder(slug) / "out").glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            "slug": slug,
            "title": guide["title"],
            "steps": len(guide["steps"]),
            "updated_at": guide["updated_at"],
            "thumb": f"/api/guides/{slug}/thumb.png?rev={guide['rev']}" if first else None,
            "video": f"/files/{slug}/out/{videos[0].name}" if videos else None,
            "theme": guide["theme"],
        }

    def thumb(self, slug: str) -> bytes | None:
        """Small redacted preview of the first screenshot (never the original pixels)."""
        guide = self.store.load(slug)
        step = next((s for s in guide["steps"] if s["image"]), None)
        if step is None:
            return None
        img = layers.open_screenshot(self.store.folder(slug) / step["image"])
        img = layers.redact(img, step["redact"])
        img.thumbnail((640, 360))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    def save_image(self, slug: str, data: bytes) -> dict:
        if not data:
            raise GuideError("الملف فاضي")
        if layers.sniff(data) is None:
            raise layers.ImageProblem("الصور المسموحة PNG أو JPG أو WebP بس")
        img = layers.open_screenshot(io.BytesIO(data))
        name = f"{new_id()}.png"
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=False, compress_level=6)
        write_atomic(self.store.folder(slug) / "images" / name, buf.getvalue())
        return {"image": f"images/{name}", "width": img.width, "height": img.height}

    def open_guide(self, slug: str) -> dict:
        guide = self.store.load(slug)
        for orphan in self.store.orphans(slug, guide):
            with contextlib.suppress(OSError):
                orphan.unlink()
        return guide

    # ------------------------------------------------------------------ voice preview

    def preview_voice(self, slug: str, body: dict) -> dict:
        guide = self.store.load(slug)
        voice = {**guide["voice"], **{k: v for k, v in (body.get("voice") or {}).items() if k in guide["voice"]}}
        voice = clean({"voice": voice})["voice"]
        text = str(body.get("text", ""))
        engine = narrate.choose_engine(voice)
        clip = self.narrator.clip(engine, voice, text)
        return {"url": f"/audio/{clip.path.stem}.wav", "duration": round(clip.duration, 2), "engine": engine}

    # ------------------------------------------------------------------ render job

    def start_render(self, slug: str, quality: str) -> dict:
        job = self.job
        with job.lock:
            if job.state == "running":
                raise BusyError
            guide = self.store.load(slug)
            folder = self.store.folder(slug)
            check_renderable(guide, folder)
            narrate.choose_engine(guide["voice"])  # fail fast with a clear message
            job.state, job.slug, job.stage, job.percent, job.eta = "running", slug, "prepare", 0.0, None
            job.error = job.detail = job.video = ""
            job.duration, job.size = 0.0, 0
            job.cancel.clear()
        out = folder / "out" / f"{slug}-r{guide['rev']}{'-fast' if quality == 'fast' else ''}.mp4"

        def progress(p: render.Progress) -> None:
            with job.lock:
                job.stage, job.percent, job.eta = p.stage, p.percent, p.eta

        def run() -> None:
            try:
                result = render.render(
                    guide,
                    folder,
                    out,
                    self.narrator,
                    quality=quality,
                    cancelled=job.cancel.is_set,
                    on_progress=progress,
                )
                render.prune_outputs(folder / "out", slug)
                with job.lock:
                    job.state, job.percent = "done", 100.0
                    job.video = f"/files/{slug}/out/{result.video.name}"
                    job.duration = result.duration
                    job.size = result.video.stat().st_size
            except narrate.Cancelled:
                with job.lock:
                    job.state = "cancelled"
            except (GuideError, layers.ImageProblem) as exc:
                with job.lock:
                    job.state, job.error = "error", str(exc)
            except render.RenderError as exc:
                with job.lock:
                    job.state, job.error, job.detail = "error", str(exc), exc.detail[-1500:]
            except Exception as exc:  # noqa: BLE001 - the UI must always get an answer
                with job.lock:
                    job.state, job.error, job.detail = "error", "حصلت مشكلة غير متوقعة", f"{type(exc).__name__}: {exc}"

        job.thread = threading.Thread(target=run, name="warrini-render", daemon=True)
        job.thread.start()
        return job.snapshot()

    def open_folder(self, slug: str) -> None:
        path = self.store.folder(slug) / "out"
        path.mkdir(exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class BusyError(Exception):
    pass


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def make_handler(app: App):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "warrini"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            return

        # ---------------------------------------------------------- responses

        def _headers(self, status: int, kind: str, length: int, extra: dict | None = None, csp: str = CSP) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", csp)
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _send(self, status: int, body: bytes, kind: str, extra: dict | None = None, csp: str = CSP) -> None:
            self._headers(status, kind, len(body), extra, csp)
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data: object) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _error(self, status: int, message: str, **extra: object) -> None:
            self._json(status, {"error": message, **extra})

        # ---------------------------------------------------------- guards

        def _cookie_ok(self) -> bool:
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE and secrets.compare_digest(value, app.secret):
                    return True
            return False

        def _guard(self, write: bool) -> bool:
            if self.headers.get("Host", "") not in allowed_hosts:
                self._error(HTTPStatus.FORBIDDEN, "bad host")
                return False
            path = urlparse(self.path).path
            if path == "/launch":
                return True
            if not self._cookie_ok():
                if path == "/" and self.command == "GET":
                    self._locked()
                else:
                    self._error(HTTPStatus.FORBIDDEN, "افتح البرنامج من جديد")
                return False
            if write:
                origin = self.headers.get("Origin")
                if origin is not None and origin not in allowed_origins:
                    self._error(HTTPStatus.FORBIDDEN, "bad origin")
                    return False
                given = self.headers.get("X-Warrini-Token", "")
                if not secrets.compare_digest(given, app.secret):
                    self._error(HTTPStatus.FORBIDDEN, "missing token")
                    return False
            return True

        def _locked(self) -> None:
            csp = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
            self._send(HTTPStatus.FORBIDDEN, LOCKED_PAGE.encode("utf-8"), "text/html; charset=utf-8", csp=csp)

        def _body(self, limit: int) -> bytes:
            if self.headers.get("Transfer-Encoding"):
                raise HttpError(HTTPStatus.LENGTH_REQUIRED, "length required")
            raw = self.headers.get("Content-Length")
            if raw is None:
                raise HttpError(HTTPStatus.LENGTH_REQUIRED, "length required")
            if not raw.isdigit():
                raise HttpError(HTTPStatus.BAD_REQUEST, "bad length")
            length = int(raw)
            if length > limit:
                raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "الملف أكبر من المسموح")
            return self.rfile.read(length)

        def _json_body(self) -> dict:
            try:
                data = json.loads(self._body(MAX_JSON) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HttpError(HTTPStatus.BAD_REQUEST, "bad json") from exc
            if not isinstance(data, dict):
                raise HttpError(HTTPStatus.BAD_REQUEST, "bad json")
            return data

        # ---------------------------------------------------------- dispatch

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            if not self._guard(write=False):
                return
            self._wrap(self._get)

        def do_POST(self) -> None:  # noqa: N802
            if self._guard(write=True):
                self._wrap(self._post)

        def do_PUT(self) -> None:  # noqa: N802
            if self._guard(write=True):
                self._wrap(self._put)

        def do_DELETE(self) -> None:  # noqa: N802
            if self._guard(write=True):
                self._wrap(self._delete)

        def _wrap(self, handler) -> None:
            try:
                handler(urlparse(self.path))
            except HttpError as exc:
                self._error(exc.status, str(exc))
            except ConflictError as exc:
                self._error(HTTPStatus.CONFLICT, "الدليل اتعدّل من مكان تاني", current=exc.current)
            except BusyError:
                self._error(HTTPStatus.CONFLICT, "فيه فيديو بيتعمل دلوقتي، استنى لما يخلص")
            except FileNotFoundError:
                self._error(HTTPStatus.NOT_FOUND, "مش موجود")
            except layers.ImageProblem as exc:
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc))
            except GuideError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
            except (ConnectionError, TimeoutError):
                pass
            except Exception as exc:  # noqa: BLE001
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "حصلت مشكلة غير متوقعة", detail=f"{type(exc).__name__}: {exc}"
                )

        def _get(self, url) -> None:
            path = url.path
            if path == "/launch":
                code = (parse_qs(url.query).get("code") or [""])[0]
                if not secrets.compare_digest(code, app.launch_code):
                    self._locked()
                    return
                cookie = f"{COOKIE}={app.secret}; HttpOnly; SameSite=Strict; Path=/"
                self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
                return
            if path == "/":
                page = app.page.replace("{{TOKEN}}", app.secret).replace("{{VERSION}}", __version__)
                self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                self._static(path[len("/static/") :])
                return
            if path == "/api/status":
                self._json(HTTPStatus.OK, app.status())
                return
            if path == "/api/guides":
                items = [s for s in (app.summary(slug) for slug in app.store.slugs()) if s]
                items.sort(key=lambda s: s["updated_at"], reverse=True)
                self._json(HTTPStatus.OK, {"guides": items})
                return
            if path == "/api/voice-status":
                self._json(HTTPStatus.OK, app.narrator.voice_status())
                return
            if path == "/api/job":
                self._json(HTTPStatus.OK, app.job.snapshot())
                return
            match = GUIDE_RE.match(path)
            if match:
                slug, sub = match.group(1), match.group(2)
                if sub is None:
                    self._json(HTTPStatus.OK, {"slug": slug, "guide": app.open_guide(slug)})
                    return
            if re.match(r"^/api/guides/[a-z0-9-]{1,60}/thumb\.png$", path):
                data = app.thumb(path.split("/")[3])
                if data is None:
                    raise FileNotFoundError
                self._send(HTTPStatus.OK, data, "image/png")
                return
            match = FILE_RE.match(path)
            if match:
                slug, kind, name = match.groups()
                if name.startswith("."):
                    raise FileNotFoundError
                folder = app.store.folder(slug) / kind
                target = (folder / name).resolve()
                if target.parent != folder.resolve() or not target.is_file():
                    raise FileNotFoundError
                self._file(target)
                return
            match = AUDIO_RE.match(path)
            if match:
                target = app.narrator.cache_dir / f"{match.group(1)}.wav"
                if not target.is_file():
                    raise FileNotFoundError
                self._file(target)
                return
            raise FileNotFoundError

        def _post(self, url) -> None:
            path = url.path
            if path == "/api/guides":
                body = self._json_body()
                slug, guide = app.store.create(str(body.get("title", "")).strip() or "دليل جديد")
                self._json(HTTPStatus.CREATED, {"slug": slug, "guide": guide})
                return
            if path == "/api/import/text":
                steps = parse_steps(str(self._json_body().get("text", "")))
                self._json(HTTPStatus.OK, {"steps": steps})
                return
            if path == "/api/shutdown":
                app.job.cancel.set()
                self._json(HTTPStatus.OK, {"ok": True})
                threading.Thread(target=app.stop, daemon=True).start()
                return
            if path == "/api/job/cancel":
                app.job.cancel.set()
                self._json(HTTPStatus.OK, app.job.snapshot())
                return
            match = GUIDE_RE.match(path)
            if match and match.group(2) == "/voice":
                self._json(HTTPStatus.OK, app.preview_voice(match.group(1), self._json_body()))
                return
            if match and match.group(2) == "/render":
                quality = self._json_body().get("quality")
                quality = quality if quality in render.QUALITY else "high"
                self._json(HTTPStatus.ACCEPTED, app.start_render(match.group(1), quality))
                return
            if match and match.group(2) == "/open-folder":
                app.open_folder(match.group(1))
                self._json(HTTPStatus.OK, {"ok": True})
                return
            if path == "/api/estimate":
                self._json(HTTPStatus.OK, {"seconds": estimate_seconds(str(self._json_body().get("text", "")))})
                return
            raise FileNotFoundError

        def _put(self, url) -> None:
            match = GUIDE_RE.match(url.path)
            if match and match.group(2) is None:
                body = self._json_body()
                slug = match.group(1)
                app.store.folder(slug)
                if not (app.store.folder(slug) / "guide.json").is_file():
                    raise FileNotFoundError
                guide = app.store.save(slug, body.get("guide") or {}, force=bool(body.get("force")))
                self._json(HTTPStatus.OK, {"guide": guide})
                return
            if match and match.group(2) == "/images":
                slug = match.group(1)
                if not (app.store.folder(slug) / "guide.json").is_file():
                    raise FileNotFoundError
                data = self._body(25 * 1024 * 1024)
                self._json(HTTPStatus.CREATED, app.save_image(slug, data))
                return
            raise FileNotFoundError

        def _delete(self, url) -> None:
            match = GUIDE_RE.match(url.path)
            if match and match.group(2) is None:
                if app.job.snapshot()["state"] == "running" and app.job.slug == match.group(1):
                    raise BusyError
                app.store.delete(match.group(1))
                self._json(HTTPStatus.OK, {"ok": True})
                return
            raise FileNotFoundError

        # ---------------------------------------------------------- files

        def _static(self, name: str) -> None:
            if name in STATIC:
                data = WEB.joinpath(name).read_bytes()
                self._send(HTTPStatus.OK, data, f"{STATIC[name]}; charset=utf-8")
                return
            if name == "icons.svg":
                self._send(HTTPStatus.OK, WEB.joinpath(name).read_bytes(), "image/svg+xml")
                return
            if re.match(r"^fonts/Cairo-(Regular|SemiBold|Bold)\.ttf$", name):
                self._send(HTTPStatus.OK, FONTS.joinpath(name[6:]).read_bytes(), "font/ttf")
                return
            raise FileNotFoundError

        def _file(self, path: Path) -> None:
            """Serve a file with single-range support (video seeking needs 206)."""
            size = path.stat().st_size
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            status = HTTPStatus.OK
            extra = {"Accept-Ranges": "bytes"}
            if rng:
                parsed = parse_range(rng, size)
                if parsed is None:
                    self._headers(
                        HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                        "text/plain",
                        0,
                        {"Content-Range": f"bytes */{size}"},
                    )
                    return
                start, end = parsed
                status = HTTPStatus.PARTIAL_CONTENT
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            length = end - start + 1
            self._headers(status, kind, length, extra)
            if self.command == "HEAD":
                return
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    return Handler


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """`bytes=a-b`, `bytes=a-` or `bytes=-n`. One range only. None = not satisfiable."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or size == 0:
        return None
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":
        n = int(last)
        if n == 0:
            return None
        return max(size - n, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


LOCKED_PAGE = (
    '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>ورّيني</title>'
    '<body style="font-family:system-ui;background:#f5f6f8;color:#0f172a;display:grid;'
    'place-items:center;height:100vh;margin:0"><div style="max-width:440px;text-align:center;line-height:1.8">'
    '<h1 style="font-size:22px">افتح ورّيني من جديد</h1>'
    "<p>عشان الأمان، الصفحة دي بتفتح بس من الرابط اللي البرنامج بيفتحه. "
    'شغّل <b dir="ltr">warrini</b> تاني وهيفتحلك المتصفح لوحده.</p></div></body></html>'
)


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("no free port")


def serve(root: Path, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    already = system.existing_instance()
    if already:  # one copy per user: just bring the open one back
        if open_browser:
            webbrowser.open(already)
        print(f"ورّيني شغال بالفعل: {already}")
        return
    port = free_port(port)
    app = App(root, port)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    app.stop = server.shutdown
    url = f"http://127.0.0.1:{port}/launch?code={app.launch_code}"
    system.write_running(url, port)
    print(f"ورّيني شغال: {url}")
    print(f"الأدلة محفوظة في: {app.store.root}")
    print("اقفل البرنامج من زرار «اقفل البرنامج» أو بـ Ctrl+C")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    if tts.voicetut_available() and os.environ.get("WARRINI_VOICE") != "fake":
        # get the local voice ready in the background (first run: downloads the model once)
        threading.Thread(target=app.narrator.worker.warm, name="warrini-voice-warm", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nتمام، قفلت.")
    finally:
        app.job.cancel.set()
        if app.job.thread is not None:
            app.job.thread.join(30)
        server.server_close()
        app.narrator.close()
        system.clear_running()
