"""`warrini doctor`: check every dependency and print the exact fix for this OS."""

from __future__ import annotations

import platform
import shutil

from ehky import tts

from . import __version__, system

OK, BAD, WARN = "✔", "✘", "!"


def ffmpeg_fix() -> str:
    system = platform.system()
    if system == "Windows":
        return "winget install Gyan.FFmpeg   (أو: choco install ffmpeg) وبعدين افتح terminal جديد"
    if system == "Darwin":
        return (
            "brew install ffmpeg — ولو doctor قال إن libass ناقصة: "
            "brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg"
        )
    return "sudo apt install ffmpeg   (Debian/Ubuntu)   أو   sudo dnf install ffmpeg"


def run() -> int:
    print(f"ورّيني {__version__} — Python {platform.python_version()} على {platform.system()}")
    problems = 0
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        print(f"{OK} ffmpeg: {ffmpeg}")
        if system.has_libass(ffmpeg):
            print(f"{OK} libass: موجودة (النص العربي على الفيديو)")
        else:
            problems += 1
            print(f"{BAD} libass ناقصة في نسخة ffmpeg دي. الحل: {ffmpeg_fix()}")
    else:
        problems += 1
        print(f"{BAD} ffmpeg مش متسطب. الحل: {ffmpeg_fix()}")
    if tts.voicetut_available():
        print(f"{OK} الصوت المصري المحلي (طارق): متسطب")
    else:
        print(
            f"{WARN} الصوت المصري المحلي مش متسطب. تقدر تسطّبه مرة واحدة:\n"
            '    pip install "warrini[voicetut] @ git+https://github.com/morgankotb7-eng/warrini"\n'
            "  على Windows أسهل طريقة برنامج التسطيب من صفحة الـ Releases (الصوت جاهز فيه).\n"
            "  أول مرة بينزّل الموديل (حوالي 3 جيجا). لو الشبكة وراها proxy، ظبط HTTPS_PROXY.\n"
            "  أو استخدم الصوت الأونلاين من إعدادات الدليل (بيبعت النص لـ Microsoft)."
        )
    print("كله تمام، شغّل: warrini" if not problems else f"فيه {problems} مشكلة لازم تتحل الأول.")
    return 0 if not problems else 2
