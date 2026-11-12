"""OS glue: bundled ffmpeg, hidden console windows, per-user state folder, single instance."""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# On Windows, a program started without a console (pythonw, the installed app) would flash a
# black window for every ffmpeg call. This flag keeps them hidden.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def use_bundled_ffmpeg() -> None:
    """The Windows installer ships ffmpeg next to Python: <app>/python, <app>/ffmpeg."""
    candidates = [
        Path(sys.executable).resolve().parent.parent / "ffmpeg",
        Path(sys.executable).resolve().parent / "ffmpeg",
    ]
    for folder in candidates:
        exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        if (folder / exe).is_file():
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            return


def find_tools() -> tuple[str | None, str | None]:
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


@functools.cache
def has_libass(ffmpeg: str) -> bool:
    """True when this ffmpeg build can draw text (libass). Cached: the answer never changes in a run."""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == ["ass"] for line in result.stdout.splitlines() if line.strip())


def state_dir() -> Path:
    """Per-user folder for the log and the running-instance file (not shared with other users)."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = base / "Warrini"
    path.mkdir(parents=True, exist_ok=True)
    return path


def running_file() -> Path:
    return state_dir() / "running.json"


def write_running(url: str, port: int) -> None:
    path = running_file()
    path.write_text(json.dumps({"url": url, "port": port, "pid": os.getpid()}), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def clear_running() -> None:
    try:
        data = json.loads(running_file().read_text("utf-8"))
        if data.get("pid") == os.getpid():
            running_file().unlink()
    except (OSError, ValueError):
        pass


def existing_instance() -> str | None:
    """If Warrini is already running for this user, return its launch URL."""
    import http.client
    from urllib.parse import urlparse

    try:
        data = json.loads(running_file().read_text("utf-8"))
        url = str(data["url"])
        parsed = urlparse(url)
        if parsed.hostname != "127.0.0.1":
            return None
        conn = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=2)
        conn.request("GET", f"{parsed.path}?{parsed.query}", headers={"Host": f"127.0.0.1:{parsed.port}"})
        status = conn.getresponse().status
        conn.close()
        return url if status == 303 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
