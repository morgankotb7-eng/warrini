import shutil
import subprocess
import wave

import pytest
from ehky import tts

from warrini import narrate as nr
from warrini.guide import GuideError

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")


@pytest.mark.parametrize(
    ("voice", "voicetut_ok", "expected"),
    [
        ({"engine": "auto"}, True, "voicetut"),
        ({"engine": "auto", "allow_online": True}, False, "edge"),
        ({"engine": "edge", "allow_online": True}, True, "edge"),
        ({"engine": "voicetut"}, True, "voicetut"),
        ({"engine": "fake"}, False, "fake"),
    ],
)
def test_choose_engine(voice, voicetut_ok, expected):
    assert nr.choose_engine(voice, voicetut_ok) == expected


@pytest.mark.parametrize(
    ("voice", "voicetut_ok", "match"),
    [
        ({"engine": "auto"}, False, "متسطب"),  # offline by default: never silently goes online
        ({"engine": "edge"}, True, "مقفول"),
        ({"engine": "voicetut", "allow_online": True}, False, "متسطب"),
    ],
)
def test_choose_engine_refuses(voice, voicetut_ok, match):
    with pytest.raises(GuideError, match=match):
        nr.choose_engine(voice, voicetut_ok)


def test_cache_key_uses_only_relevant_fields():
    base = {"speaker": "Tareq", "voice": "ar-EG-ShakirNeural", "rate": "+0%"}
    k = nr.cache_key("voicetut", base, "اهلا  بيك")
    assert k == nr.cache_key("voicetut", {**base, "voice": "ar-EG-SalmaNeural"}, "اهلا بيك")  # edge field ignored
    assert k != nr.cache_key("voicetut", {**base, "speaker": "Other"}, "اهلا بيك")
    assert k != nr.cache_key("edge", base, "اهلا بيك")
    e = nr.cache_key("edge", base, "x")
    assert e == nr.cache_key("edge", {**base, "speaker": "Other"}, "x")
    assert e != nr.cache_key("edge", {**base, "rate": "+10%"}, "x")


def test_fake_clip_is_cached_and_touched(tmp_path):
    narrator = nr.Narrator(cache_dir=tmp_path)
    clip = narrator.clip("fake", {}, "اضغط على زرار Save")
    assert clip.path.is_file() and clip.duration == pytest.approx(len("اضغط على زرار Save") / 13, abs=0.01)
    assert clip.words and clip.words[-1].end == pytest.approx(clip.duration)
    again = narrator.clip("fake", {}, "اضغط   على زرار Save")
    assert again.path == clip.path
    with pytest.raises(GuideError):
        narrator.clip("fake", {}, "   ")


def test_corrupt_cache_entry_is_dropped(tmp_path):
    narrator = nr.Narrator(cache_dir=tmp_path)
    clip = narrator.clip("fake", {}, "نص")
    clip.path.write_bytes(b"not a wav")
    assert narrator.cached("fake", {}, "نص") is None
    assert not clip.path.exists()


def test_evict_removes_oldest_first(tmp_path):
    import os
    import time

    narrator = nr.Narrator(cache_dir=tmp_path)
    clips = [narrator.clip("fake", {}, f"جملة رقم {i} " * 3) for i in range(3)]
    for i, clip in enumerate(clips):
        stamp = time.time() - 100 + i
        os.utime(clip.path, (stamp, stamp))
    size = clips[2].path.stat().st_size + clips[2].path.with_suffix(".json").stat().st_size
    narrator.limit = size
    assert narrator.evict() == 2
    assert not clips[0].path.exists() and not clips[1].path.exists() and clips[2].path.exists()


def _tone(path, seconds):
    nr.write_silence(path, seconds)
    return nr.Clip(path, seconds, [tts.Word("كلمة", 0.1, 0.4)], True)


def test_assemble_places_clips_exactly(tmp_path):
    a = _tone(tmp_path / "a.wav", 1.0)
    out = tmp_path / "all.wav"
    words = nr.assemble([(0.5, a), (2.0, a)], 4.0, out)
    assert nr.wav_duration(out) == pytest.approx(4.0, abs=1 / nr.RATE)
    assert [round(w.start, 6) for w in words] == [0.6, 2.1]
    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)


def test_assemble_rejects_foreign_formats(tmp_path):
    bad = tmp_path / "bad.wav"
    with wave.open(str(bad), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00" * 400)
    with pytest.raises(ValueError):
        nr.assemble([(0, nr.Clip(bad, 0.01, [], False))], 1.0, tmp_path / "o.wav")


def test_estimated_words_spacing():
    words = nr.estimated_words(["اهلا بيك", "يلا بينا"], [1.0, 1.0], 0.5)
    assert words[0].start == 0 and words[2].start == pytest.approx(1.5)


@needs_ffmpeg
def test_edge_path_converts_mp3(tmp_path, monkeypatch):
    def fake_edge(text, voice, out, rate):
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=300:d=1.5", str(out)], check=True
        )
        return tts.Narration([], exact=False)

    monkeypatch.setattr(tts, "synthesize_edge", fake_edge)
    narrator = nr.Narrator(ffmpeg=FFMPEG, cache_dir=tmp_path)
    clip = narrator.clip("edge", {"voice": "ar-EG-ShakirNeural", "rate": "+0%"}, "افتح الإعدادات")
    assert clip.duration == pytest.approx(1.5, abs=0.06)
    assert clip.words and not clip.exact  # empty boundaries → estimated words


@needs_ffmpeg
def test_loudness_normalize(tmp_path):
    src = tmp_path / "quiet.wav"
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=3", "-af", "volume=0.05"]
        + ["-ar", "48000", "-ac", "1", str(src)],
        check=True,
    )
    dst = tmp_path / "loud.wav"
    nr.loudness_normalize(src, dst, FFMPEG)
    with wave.open(str(dst)) as w:
        assert w.getframerate() == 48000
    assert nr.wav_duration(dst) == pytest.approx(3.0, abs=0.05)
    silent = tmp_path / "silent.wav"
    nr.write_silence(silent, 1.0)
    nr.loudness_normalize(silent, tmp_path / "s2.wav", FFMPEG)
    assert nr.wav_duration(tmp_path / "s2.wav") == pytest.approx(1.0)
