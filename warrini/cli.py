"""Command line: `warrini` opens the app; `render`, `import-folder` and `doctor` for scripts."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__, doctor, narrate, render, system
from .guide import GuideError, Store, clean, natural_key, new_step
from .layers import ImageProblem, open_screenshot

EXIT_USER, EXIT_TOOL, EXIT_RENDER = 1, 2, 3


def default_root() -> Path:
    return Path(os.environ.get("WARRINI_HOME") or Path.home() / "Warrini")


def _utf8_console() -> None:
    """UTF-8 output; without a console (pythonw, the installed app) write to a log file instead."""
    if sys.stdout is None or sys.stderr is None:
        from .system import state_dir

        log = open(state_dir() / "warrini.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    system.use_bundled_ffmpeg()
    parser = argparse.ArgumentParser(
        prog="warrini",
        description="ورّيني: حوّل خطوات الشغل لفيديو قصير بصوت مصري. مجاني وبيشتغل على جهازك.",
    )
    parser.add_argument("--version", action="version", version=f"warrini {__version__}")
    parser.add_argument("--root", type=Path, default=None, help="فولدر الأدلة (الافتراضي ~/Warrini)")
    sub = parser.add_subparsers(dest="cmd")

    ui = sub.add_parser("ui", help="افتح البرنامج في المتصفح (ده الافتراضي)")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")

    r = sub.add_parser("render", help="اعمل فيديو من guide.json")
    r.add_argument("guide", type=Path, help="مسار guide.json أو فولدر الدليل")
    r.add_argument("-o", "--output", type=Path, default=None)
    r.add_argument("--quality", choices=list(render.QUALITY), default="high")
    r.add_argument("--engine", choices=["auto", "voicetut", "edge", "fake"], default=None)
    r.add_argument("--allow-online", action="store_true", help="اسمح بالصوت الأونلاين (بيبعت النص لـ Microsoft)")
    r.add_argument("--offline", action="store_true", help="امنع أي صوت أونلاين حتى لو الدليل سامح بيه")

    imp = sub.add_parser("import-folder", help="اعمل دليل من فولدر صور (كل صورة خطوة)")
    imp.add_argument("folder", type=Path)
    imp.add_argument("--title", required=True)

    sub.add_parser("doctor", help="اتأكد إن كل حاجة متسطبة صح")

    args = parser.parse_args(argv)
    root = args.root or default_root()
    try:
        if args.cmd in (None, "ui"):
            from .server import serve

            serve(root, getattr(args, "port", 8765), open_browser=not getattr(args, "no_browser", False))
            return 0
        if args.cmd == "doctor":
            return doctor.run()
        if args.cmd == "import-folder":
            return cmd_import(root, args.folder, args.title)
        if args.cmd == "render":
            return cmd_render(args)
    except GuideError as exc:
        print(f"خطأ: {exc}", file=sys.stderr)
        return EXIT_USER
    except render.RenderError as exc:
        print(f"خطأ: {exc}", file=sys.stderr)
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        return EXIT_TOOL if "ffmpeg" in str(exc) and "متسطب" in str(exc) else EXIT_RENDER
    except KeyboardInterrupt:
        print("\nاتلغى.", file=sys.stderr)
        return 130
    return 0


def cmd_import(root: Path, folder: Path, title: str) -> int:
    if not folder.is_dir():
        raise GuideError(f"الفولدر مش موجود: {folder}")
    images = sorted(
        (p for p in folder.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}),
        key=lambda p: natural_key(p.name),
    )
    if not images:
        raise GuideError("ملقيتش صور PNG أو JPG أو WebP في الفولدر ده")
    store = Store(root)
    slug, guide = store.create(title)
    target = store.folder(slug)
    steps, skipped = [], 0
    for path in images:
        try:
            img = open_screenshot(path)
        except ImageProblem as exc:
            print(f"اتجاهلت {path.name}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        step = new_step(path.stem.replace("_", " ").replace("-", " "))
        name = f"{step['id']}.png"
        img.save(target / "images" / name, "PNG")
        step["image"] = f"images/{name}"
        steps.append(step)
    guide["steps"] = steps
    store.save(slug, guide)
    print(f"تمام: «{title}» فيه {len(steps)} خطوة ({skipped} صورة اتجاهلت). افتح البرنامج واكتب كلام كل خطوة.")
    print(target)
    return 0


def cmd_render(args) -> int:
    path = args.guide
    if path.is_dir():
        path = path / "guide.json"
    if not path.is_file():
        raise GuideError(f"الملف مش موجود: {path}")
    try:
        guide = clean(json.loads(path.read_text("utf-8")))
    except json.JSONDecodeError as exc:
        raise GuideError("ملف guide.json مش JSON سليم") from exc
    if args.allow_online:
        guide["voice"]["allow_online"] = True
    if args.offline:
        guide["voice"]["allow_online"] = False
    if args.engine:
        guide["voice"]["engine"] = args.engine if args.engine != "fake" else "auto"
    engine = "fake" if args.engine == "fake" else narrate.choose_engine(guide["voice"])
    folder = path.parent
    out = args.output or folder / "out" / f"{folder.name}.mp4"
    narrator = narrate.Narrator(ffmpeg=shutil.which("ffmpeg") or "ffmpeg")
    last = [0.0]

    def progress(p: render.Progress) -> None:
        now = time.monotonic()
        if now - last[0] < 0.5 and p.percent < 100:
            return
        last[0] = now
        eta = f" — فاضل {round(p.eta)} ث" if p.eta else ""
        print(f"\r{p.stage:<7} {p.percent:5.1f}%{eta}      ", end="", flush=True)

    try:
        result = render.render(guide, folder, out, narrator, quality=args.quality, engine=engine, on_progress=progress)
    finally:
        narrator.close()
    print(f"\nتمام: {result.video}  ({result.duration:.1f} ثانية)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
