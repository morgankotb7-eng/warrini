"""Narration: one voice clip per segment, cached on disk, assembled into one WAV.

Engines
- voicetut: the local Egyptian voice (Tareq). Runs in a long-lived worker process that loads
  the model once; cancelling a render kills the worker.
- edge: Microsoft's online voice (opt-in per guide, sends the text to Microsoft).
- fake: silence of the estimated length, for tests and dry runs.

Every clip is stored as 48 kHz mono 16-bit WAV next to a JSON file with word timings.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import multiprocessing as mp
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ehky import __version__ as EHKY_VERSION
from ehky import text as ehky_text
from ehky import tts

from .guide import GuideError, write_atomic
from .system import NO_WINDOW
from .timeline import estimate_seconds

RATE = 48_000
NARRATE_VERSION = 1
VOICETUT_REPO = "mohammedaly22/VoiceTut-TTS"
VOICETUT_REVISION = "b36bdf4416864d84f47ca77ce3decf34f6ab16cc"
VOICETUT_STEPS = 16
CHUNK_GAP = 0.28  # silence between chunks of one segment, seconds
CACHE_LIMIT = 1024**3

VOICETUT_SETUP = (
    "الصوت المصري المحلي مش متسطب. على Windows نزّل برنامج التسطيب من صفحة الـ Releases، "
    'أو سطّبه بالأمر: pip install "warrini[voicetut] @ git+https://github.com/morgankotb7-eng/warrini"'
)


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Clip:
    path: Path
    duration: float
    words: list[tts.Word]
    exact: bool


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (
        os.environ.get("LOCALAPPDATA") if os.name == "nt" else str(Path.home() / ".cache")
    )
    return Path(base or Path.home() / ".cache") / "warrini" / "voice"


# --------------------------------------------------------------------------- engine choice


def choose_engine(voice: dict, voicetut_ok: bool | None = None) -> str:
    """Resolve the engine for a guide. Online use needs the guide's explicit opt-in."""
    if os.environ.get("WARRINI_VOICE") == "fake":  # tests and demos: silent clips, no voice model
        return "fake"
    ok = tts.voicetut_available() if voicetut_ok is None else voicetut_ok
    engine = voice.get("engine", "auto")
    online = bool(voice.get("allow_online"))
    if engine == "fake":
        return "fake"
    if engine == "voicetut" or (engine == "auto" and ok):
        if not ok:
            raise GuideError(VOICETUT_SETUP)
        return "voicetut"
    if not online:
        if engine == "edge":
            raise GuideError("الصوت الأونلاين مقفول للدليل ده. فعّل «اسمح بالصوت الأونلاين» من إعدادات الصوت.")
        raise GuideError(VOICETUT_SETUP + " — أو فعّل الصوت الأونلاين للدليل ده.")
    return "edge"


def cache_key(engine: str, voice: dict, text: str) -> str:
    if engine == "voicetut":
        fields = [voice.get("speaker", tts.DEFAULT_SPEAKER), VOICETUT_REVISION, str(VOICETUT_STEPS)]
    elif engine == "edge":
        fields = [voice.get("voice", tts.DEFAULT_VOICE), voice.get("rate", "+0%")]
    else:
        fields = []
    raw = "\x1f".join([engine, EHKY_VERSION, str(NARRATE_VERSION), *fields, ehky_text.normalize(text)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- audio helpers


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as src:
        return src.getnframes() / src.getframerate()


def to_standard_wav(src: Path, dst: Path, ffmpeg: str) -> None:
    """Any audio → 48 kHz mono 16-bit WAV."""
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(RATE)]
        + ["-c:a", "pcm_s16le", str(dst)],
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not convert the voice clip: {proc.stderr.strip()[-500:]}")


def write_silence(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(RATE)
        dst.writeframes(b"\x00\x00" * round(seconds * RATE))


def estimated_words(chunks: list[str], durations: list[float], gap: float) -> list[tts.Word]:
    words: list[tts.Word] = []
    cursor = 0.0
    for chunk, length in zip(chunks, durations, strict=True):
        words.extend(tts.estimate_words(chunk, cursor, cursor + length))
        cursor += length + gap
    return words


# --------------------------------------------------------------------------- voicetut worker


def _worker_main(requests, results) -> None:  # pragma: no cover - runs in a child process
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    model = None
    while True:
        job = requests.get()
        if job is None:
            return
        try:
            if model is None:
                from huggingface_hub import snapshot_download
                from voicetut_tts import VoiceTutTTS

                results.put(("phase", "download"))
                local = snapshot_download(VOICETUT_REPO, revision=VOICETUT_REVISION)
                results.put(("phase", "load"))  # loading also fetches the audio tokenizer the first time
                model = VoiceTutTTS.from_pretrained(local)
                results.put(("loaded", None))
            if job == "warm":
                results.put(("done", []))
                continue
            chunks, speaker, out = job
            results.put(("phase", "speak"))
            durations = []
            with tempfile.TemporaryDirectory(prefix="warrini-vt-") as tmp, wave.open(out, "wb") as dst:
                for i, chunk in enumerate(chunks):
                    clip = os.path.join(tmp, f"{i}.wav")
                    model.synthesize(chunk, speaker=speaker, num_step=VOICETUT_STEPS, output=clip)
                    with wave.open(clip, "rb") as src:
                        if i == 0:
                            dst.setparams(src.getparams())
                        rate = src.getframerate()
                        width = src.getsampwidth() * src.getnchannels()
                        if i:
                            dst.writeframes(b"\x00" * int(CHUNK_GAP * rate) * width)
                        dst.writeframes(src.readframes(src.getnframes()))
                        durations.append(src.getnframes() / rate)
                    results.put(("chunk", i + 1))
            results.put(("done", durations))
        except Exception as exc:  # noqa: BLE001 - report every failure to the parent
            results.put(("error", f"{type(exc).__name__}: {exc}"))


# the model files fetched the first time (VoiceTut + the OmniVoice audio tokenizer), about 3.2 GB
VOICE_REPOS = ("models--mohammedaly22--VoiceTut-TTS", "models--eustlb--higgs-audio-v2-tokenizer")
VOICE_DOWNLOAD_MB = 3200


def hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(home) / "hub"


def voice_bytes_on_disk() -> int:
    """How much of the voice model is downloaded so far, partial files included.

    Snapshot entries are often links to shared blob files: each real file is counted once.
    """
    seen: set[str] = set()
    total = 0
    for repo in VOICE_REPOS:
        root = hf_hub_dir() / repo
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                real = os.path.realpath(path)
                if real in seen or not os.path.isfile(real):
                    continue
                seen.add(real)
                total += os.path.getsize(real)
            except OSError:
                continue
    return total


class VoiceTutWorker:
    """One model in memory for the whole session. Thread-safe; one request at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc = None
        self._requests = None
        self._results = None
        self.phase = "idle"  # idle | download | load | ready | speak | error
        self.error = ""
        self.loaded = False

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        ctx = mp.get_context("spawn")
        self._requests, self._results = ctx.Queue(), ctx.Queue()
        self._proc = ctx.Process(target=_worker_main, args=(self._requests, self._results), daemon=True)
        self._proc.start()

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        self.loaded = False
        self.phase = "idle"
        if proc is not None and proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()

    def warm(self) -> None:
        """Download and load the model ahead of time, so the first «Listen» is quick."""
        with contextlib.suppress(Exception):  # the status already holds any error
            self._run("warm", None, lambda: False, lambda *_: None)

    def synthesize(
        self,
        chunks: list[str],
        speaker: str,
        out: Path,
        cancelled: Callable[[], bool],
        progress: Callable[[str, float], None],
    ) -> list[float]:
        return self._run((chunks, speaker, str(out)), len(chunks), cancelled, progress)

    def _run(self, job, count, cancelled, progress) -> list[float]:
        with self._lock:
            self._ensure()
            self._requests.put(job)
            while True:
                if cancelled():
                    self.stop()
                    raise Cancelled
                if not self._proc.is_alive():
                    self._proc = None
                    self.phase, self.error = "error", "الصوت المحلي وقف فجأة (ممكن الذاكرة مش كفاية)"
                    self.loaded = False
                    raise RuntimeError(self.error)
                try:
                    kind, value = self._results.get(timeout=0.25)
                except queue.Empty:
                    continue
                if kind == "phase":
                    if value == "speak" or not self.loaded:
                        self.phase = value
                elif kind == "loaded":
                    self.loaded = True
                    self.phase = "ready"
                    progress("loaded", 0)
                elif kind == "chunk":
                    progress("chunk", value / (count or 1))
                elif kind == "done":
                    self.phase = "ready"
                    return value
                else:
                    self.phase, self.error = "error", f"الصوت المحلي فشل: {value}"
                    raise RuntimeError(self.error)


# --------------------------------------------------------------------------- narrator


class Narrator:
    """Turns texts into cached clips. Safe to share between the UI and a render."""

    def __init__(self, ffmpeg: str = "ffmpeg", cache_dir: Path | None = None, limit: int = CACHE_LIMIT) -> None:
        self.ffmpeg = ffmpeg
        self.cache_dir = Path(cache_dir or default_cache_dir())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.limit = limit
        self.worker = VoiceTutWorker()
        self._key_locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _key_lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def cached(self, engine: str, voice: dict, text: str) -> Clip | None:
        key = cache_key(engine, voice, text)
        wav, meta = self.cache_dir / f"{key}.wav", self.cache_dir / f"{key}.json"
        if not (wav.is_file() and meta.is_file()):
            return None
        try:
            info = json.loads(meta.read_text("utf-8"))
            duration = wav_duration(wav)
            if abs(duration - info["duration"]) > 0.01:
                raise ValueError("length mismatch")
            words = [tts.Word(w, s, e) for w, s, e in info["words"]]
        except (OSError, ValueError, KeyError, TypeError, wave.Error, json.JSONDecodeError):
            wav.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            return None
        for path in (wav, meta):
            os.utime(path)
        return Clip(wav, duration, words, bool(info.get("exact")))

    def clip(
        self,
        engine: str,
        voice: dict,
        text: str,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[str, float], None] = lambda *_: None,
    ) -> Clip:
        text = ehky_text.normalize(text)
        if not text:
            raise GuideError("مفيش كلام يتقال")
        key = cache_key(engine, voice, text)
        with self._key_lock(key):
            hit = self.cached(engine, voice, text)
            if hit is not None:
                return hit
            with tempfile.TemporaryDirectory(prefix="warrini-voice-") as tmp:
                raw = Path(tmp) / "raw"
                std = Path(tmp) / "std.wav"
                words, exact = self._synthesize(engine, voice, text, raw, std, cancelled, progress)
                duration = wav_duration(std)
                if not words:
                    words = tts.estimate_words(text, 0.0, duration)
                    exact = False
                meta = {"duration": duration, "exact": exact, "words": [[w.text, w.start, w.end] for w in words]}
                write_atomic(self.cache_dir / f"{key}.wav", std.read_bytes())
                write_atomic(self.cache_dir / f"{key}.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        return Clip(self.cache_dir / f"{key}.wav", duration, words, exact)

    def _synthesize(self, engine, voice, text, raw, std, cancelled, progress) -> tuple[list[tts.Word], bool]:
        if engine == "fake":
            write_silence(std, max(estimate_seconds(text), 0.3))
            return [], False
        if engine == "edge":
            mp3 = raw.with_suffix(".mp3")
            narration = tts.synthesize_edge(text, voice["voice"], mp3, voice.get("rate", "+0%"))
            if cancelled():
                raise Cancelled
            to_standard_wav(mp3, std, self.ffmpeg)
            return narration.words, narration.exact
        if engine == "voicetut":
            chunks = ehky_text.split_lines(text)
            src = raw.with_suffix(".wav")
            durations = self.worker.synthesize(chunks, voice["speaker"], src, cancelled, progress)
            to_standard_wav(src, std, self.ffmpeg)
            return estimated_words(chunks, durations, CHUNK_GAP), False
        raise ValueError(f"unknown engine {engine!r}")

    def evict(self) -> int:
        """Delete the least recently used clips until the cache fits the limit. Returns files removed."""
        pairs = []
        for wav in self.cache_dir.glob("*.wav"):
            meta = wav.with_suffix(".json")
            size = wav.stat().st_size + (meta.stat().st_size if meta.is_file() else 0)
            pairs.append((wav.stat().st_mtime, size, wav, meta))
        total = sum(p[1] for p in pairs)
        removed = 0
        for _mtime, size, wav, meta in sorted(pairs, key=lambda p: p[0]):
            if total <= self.limit:
                break
            wav.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            total -= size
            removed += 1
        return removed

    def voice_status(self) -> dict:
        w = self.worker
        info = {"phase": w.phase, "error": w.error, "total_mb": VOICE_DOWNLOAD_MB, "downloaded_mb": None}
        if w.phase in ("download", "load"):
            info["downloaded_mb"] = round(voice_bytes_on_disk() / 1048576)
        return info

    def close(self) -> None:
        self.worker.stop()


# --------------------------------------------------------------------------- assembly


def assemble(placements: list[tuple[float, Clip]], total: float, out: Path) -> list[tts.Word]:
    """Place clips at absolute times on a silent track of `total` seconds. Returns global words."""
    frames = round(total * RATE)
    buffer = bytearray(frames * 2)
    words: list[tts.Word] = []
    for at, clip in placements:
        with wave.open(str(clip.path), "rb") as src:
            if (src.getframerate(), src.getnchannels(), src.getsampwidth()) != (RATE, 1, 2):
                raise ValueError("clip is not 48 kHz mono 16-bit")
            data = src.readframes(src.getnframes())
        start = round(at * RATE) * 2
        end = min(start + len(data), len(buffer))
        buffer[start:end] = data[: end - start]
        words.extend(tts.Word(w.text, w.start + at, w.end + at) for w in clip.words)
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(RATE)
        dst.writeframes(bytes(buffer))
    return words


def loudness_normalize(src: Path, dst: Path, ffmpeg: str) -> None:
    """Two-pass EBU R128 normalization (linear), so every guide sounds equally loud."""
    target = "I=-16:TP=-1.5:LRA=11"
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(src), "-af", f"loudnorm={target}:print_format=json"]
        + ["-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=600,
        creationflags=NO_WINDOW,
    )
    stats = _loudnorm_stats(probe.stderr)
    if stats is None or stats["input_i"] in ("-inf", "inf"):  # silent track: nothing to normalize
        shutil.copyfile(src, dst)
        return
    measured = ":".join(
        f"{name}={stats[key]}"
        for name, key in (
            ("measured_I", "input_i"),
            ("measured_TP", "input_tp"),
            ("measured_LRA", "input_lra"),
            ("measured_thresh", "input_thresh"),
            ("offset", "target_offset"),
        )
    )
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af"]
        + [f"loudnorm={target}:{measured}:linear=true,aresample={RATE}", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        capture_output=True,
        text=True,
        timeout=600,
        creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg loudness pass failed: {proc.stderr.strip()[-500:]}")


def _loudnorm_stats(stderr: str) -> dict | None:
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
