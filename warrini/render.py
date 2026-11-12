"""Render a guide to MP4: voice → timeline → captions → frames piped into ffmpeg."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import as_file, files
from pathlib import Path

from ehky import compose

from . import captions, narrate, timeline
from .consts import FPS, FRAME_H, FRAME_W
from .frames import frames
from .guide import GuideError, check_renderable, clean

QUALITY = {"high": (1.0, 18), "fast": (2 / 3, 23)}
KEEP_OUTPUTS = 3


class RenderError(RuntimeError):
    """Something broke while rendering. Arabic message, technical detail in `detail`."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@dataclass
class Progress:
    stage: str = "prepare"  # prepare | voice | images | video | finish
    percent: float = 0.0
    eta: float | None = None
    started: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class Result:
    video: Path
    srt: Path
    duration: float
    frames: int


def tools() -> tuple[str, str]:
    try:
        ffmpeg, ffprobe = compose.require_tools()
    except RuntimeError as exc:
        raise RenderError("ffmpeg مش متسطب. شغّل «warrini doctor» عشان تعرف تسطّبه إزاي.", str(exc)) from exc
    if not compose.has_libass(ffmpeg):
        raise RenderError(
            "نسخة ffmpeg اللي عندك مش بتعرف تكتب نص على الفيديو (ناقصها libass). شغّل «warrini doctor».",
            ffmpeg,
        )
    return ffmpeg, ffprobe


def ffmpeg_args(ffmpeg: str, width: int, height: int, crf: int, duration: float, out: Path) -> list[str]:
    """Pure: the encode command. Runs with cwd = the render folder (relative names, no escaping)."""
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-i",
        "narration.wav",
        "-filter_complex",
        "[0:v]ass=filename=subs.ass:fontsdir=fonts,format=yuv420p[v];[1:a]apad[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out),
    ]


def _copy_fonts(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in files("warrini").joinpath("assets/fonts").iterdir():
        if item.name.endswith(".ttf"):
            with as_file(item) as path:
                shutil.copyfile(path, dst / item.name)


def _snapshot(guide: dict, folder: Path, work: Path) -> Path:
    """Copy the images the guide uses, so edits during the render cannot change it."""
    snap = work / "guide"
    (snap / "images").mkdir(parents=True)
    for step in guide["steps"]:
        if step["image"] and not (snap / step["image"]).exists():
            shutil.copyfile(folder / step["image"], snap / step["image"])
    return snap


def _finish(part: Path, final: Path, attempts: int = 10) -> Path:
    for attempt in range(attempts):
        try:
            os.replace(part, final)
            return final
        except PermissionError:
            if attempt == attempts - 1:
                stamped = final.with_name(f"{final.stem}-{time.strftime('%Y%m%d-%H%M%S')}{final.suffix}")
                os.replace(part, stamped)
                return stamped
            time.sleep(0.2)
    return final


def prune_outputs(out_dir: Path, slug: str, keep: int = KEEP_OUTPUTS) -> None:
    videos = sorted(out_dir.glob(f"{slug}-r*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in videos[keep:]:
        for path in (old, old.with_suffix(".srt")):
            with contextlib.suppress(PermissionError):  # still open in a player; next render tries again
                path.unlink(missing_ok=True)


def render(
    guide_data: dict,
    folder: Path,
    out: Path,
    narrator: narrate.Narrator,
    quality: str = "high",
    scale: float | None = None,
    engine: str | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[Progress], None] = lambda _p: None,
    log: Callable[[str], None] = lambda _m: None,
) -> Result:
    """Render one guide. `out` is the final .mp4 path; the .srt goes next to it."""
    guide = clean(guide_data)
    check_renderable(guide, folder)
    ffmpeg, _ = tools()
    narrator.ffmpeg = ffmpeg
    if scale is None:
        scale, crf = QUALITY[quality]
    else:
        crf = QUALITY[quality][1]
    width, height = round(FRAME_W * scale) // 2 * 2, round(FRAME_H * scale) // 2 * 2
    scale = width / FRAME_W
    engine = engine or narrate.choose_engine(guide["voice"])
    progress = Progress()

    def report(stage: str, percent: float, eta: float | None = None) -> None:
        progress.stage, progress.percent, progress.eta = stage, round(min(max(percent, 0), 100), 1), eta
        on_progress(progress)

    def check() -> None:
        if cancelled():
            raise narrate.Cancelled

    report("prepare", 0)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="warrini-render-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        snap = _snapshot(guide, folder, work)

        # 1) voice: 0-25 %
        texts: list[tuple[str, str]] = [("title", guide["intro"] or guide["title"])]
        texts += [(s["id"], s["text"]) for s in guide["steps"]]
        if guide["outro"]:
            texts.append(("end", guide["outro"]))
        clips: dict[str, narrate.Clip] = {}
        missing = [t for t in texts if narrator.cached(engine, guide["voice"], t[1]) is None]
        log(f"voice: {engine}, {len(texts)} clips, {len(missing)} to make")
        done = 0
        for key, text in texts:
            check()
            new = narrator.cached(engine, guide["voice"], text) is None

            def sub(_kind: str, frac: float, base=done) -> None:
                report("voice", 25 * (base + frac) / max(len(missing), 1))

            try:
                clips[key] = narrator.clip(engine, guide["voice"], text, cancelled, sub)
            except (narrate.Cancelled, GuideError):
                raise
            except Exception as exc:  # noqa: BLE001
                where = "العنوان" if key == "title" else "الختام" if key == "end" else _step_name(guide, key)
                raise RenderError(f"الصوت فشل في {where}", str(exc)) from exc
            if new:
                done += 1
                report("voice", 25 * done / max(len(missing), 1))
        report("voice", 25)

        # 2) timeline, audio, captions: 25-30 %
        segments = timeline.build(guide, {k: c.duration for k, c in clips.items()})
        total = timeline.total_frames(segments)
        duration = total / FPS
        placements = []
        for seg in segments:
            key = "title" if seg.kind == "title" else "end" if seg.kind == "end" else guide["steps"][seg.index]["id"]
            if key in clips:
                placements.append((seg.start_s + seg.voice_at, clips[key]))
        words = narrate.assemble(placements, duration, work / "raw.wav")
        narrate.loudness_normalize(work / "raw.wav", work / "narration.wav", ffmpeg)
        exact = all(c.exact for c in clips.values())
        ass, cues = captions.build(guide, segments, words, exact)
        (work / "subs.ass").write_text(ass, encoding="utf-8")
        _copy_fonts(work / "fonts")
        report("images", 30)
        check()

        # 3) frames → ffmpeg: 30-99 %
        part = out.with_name(out.name + ".part")
        args = ffmpeg_args(ffmpeg, width, height, crf, duration, part.resolve())
        log_path = work / "ffmpeg.log"
        started = time.monotonic()
        written = 0
        with open(log_path, "wb") as errlog:
            proc = subprocess.Popen(args, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errlog)
            try:
                for data in frames(guide, snap, segments, scale, cancelled):
                    try:
                        proc.stdin.write(data)
                    except (BrokenPipeError, OSError) as exc:
                        raise RenderError("ffmpeg وقف فجأة", _tail(log_path)) from exc
                    written += 1
                    if written % 15 == 0:
                        elapsed = time.monotonic() - started
                        rate = written / elapsed if elapsed > 0 else 0
                        eta = (total - written) / rate if rate > 0 and elapsed > 2 else None
                        report("video", 30 + 69 * written / total, eta)
                check()
                proc.stdin.close()
                code = proc.wait(timeout=600)
            except BaseException:
                _stop(proc)
                _remove(part)
                raise
        if code != 0 or written != total:
            _remove(part)
            raise RenderError("ffmpeg معرفش يكمّل الفيديو", _tail(log_path))

        report("finish", 99)
        final = _finish(part, out)
        srt_path = final.with_suffix(".srt")
        srt_path.write_text(captions.srt(cues), encoding="utf-8")
    narrator.evict()
    report("finish", 100)
    log(f"done: {final} ({duration:.1f}s, {total} frames, {time.monotonic() - progress.started:.1f}s)")
    return Result(final, srt_path, duration, total)


def _step_name(guide: dict, step_id: str) -> str:
    for i, step in enumerate(guide["steps"], 1):
        if step["id"] == step_id:
            return f"الخطوة {i}"
    return "خطوة"


def _stop(proc: subprocess.Popen) -> None:
    """Close ffmpeg's input and let it exit on its own first.

    Killing it straight away is not enough on Windows: package-manager shims (Chocolatey)
    start the real ffmpeg as a child, and only a clean end of input makes that child exit
    and release the output file.
    """
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(15)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


def _remove(path: Path, attempts: int = 25) -> None:
    """Delete a file that a just-stopped process may still hold for a moment (Windows)."""
    for _ in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.2)


def _tail(path: Path, chars: int = 2000) -> str:
    try:
        return path.read_text("utf-8", errors="replace")[-chars:]
    except OSError:
        return ""
