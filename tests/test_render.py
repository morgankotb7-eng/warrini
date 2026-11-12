import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from warrini import captions, frames, layers, narrate, render, timeline
from warrini import guide as g

FFMPEG = shutil.which("ffmpeg")


def _libass() -> bool:
    if FFMPEG is None:
        return False
    from ehky import compose

    return compose.has_libass(FFMPEG)


needs_libass = pytest.mark.skipif(not _libass(), reason="ffmpeg with libass not installed")
REDACT = {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.3}


def checker(path, size=(800, 450)):
    img = Image.new("RGB", size, (235, 238, 245))
    draw = ImageDraw.Draw(img)
    x0, y0 = int(REDACT["x"] * size[0]), int(REDACT["y"] * size[1])
    x1, y1 = int((REDACT["x"] + REDACT["w"]) * size[0]), int((REDACT["y"] + REDACT["h"]) * size[1])
    for x in range(x0, x1, 16):
        for y in range(y0, y1, 16):
            if (x // 16 + y // 16) % 2:
                draw.rectangle((x, y, min(x + 15, x1 - 1), min(y + 15, y1 - 1)), fill=(255, 0, 255))
    draw.rounded_rectangle((560, 330, 760, 400), radius=8, fill=(37, 99, 235))
    img.save(path)


@pytest.fixture
def sample(tmp_path):
    store = g.Store(tmp_path / "root")
    slug, guide = store.create("Printer setup")
    folder = store.folder(slug)
    image = f"images/{g.new_id()}.png"
    checker(folder / image)
    guide["steps"] = [
        {
            "text": "VPN بتاعك هيتغير لما تدوس على Reset",
            "image": image,
            "focus": {"x": 0.7, "y": 0.73, "w": 0.25, "h": 0.16},
        },
        {"text": "نفس الشاشة، الزرار التاني", "image": image, "focus": {"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.1}},
        {"text": "خطوة من غير صورة"},
    ]
    guide = store.save(slug, guide)
    return store, slug, guide


def test_redact_removes_original_pixels(tmp_path):
    checker(tmp_path / "c.png")
    img = layers.open_screenshot(tmp_path / "c.png")
    covered = layers.redact(img, [REDACT])
    x0, y0, x1, y1 = (int(v) for v in (0.2 * 800, 0.2 * 450, 0.6 * 800, 0.5 * 450))
    region = covered.crop((x0 + 4, y0 + 4, x1 - 4, y1 - 4)).convert("L")
    assert ImageStat.Stat(region).stddev[0] < 4 and region.getextrema()[0] > 150
    original = img.crop((x0 + 4, y0 + 4, x1 - 4, y1 - 4)).convert("L")
    assert ImageStat.Stat(original).stddev[0] > 50  # control: the pattern was there


def test_no_frame_shows_redacted_pixels(sample):
    store, slug, guide = sample
    folder = store.folder(slug)
    segs = timeline.build(guide, {s["id"]: 1.0 for s in guide["steps"]})

    def magenta(data_guide):
        """Per frame: does any pixel look like the magenta pattern under the cover?"""
        out = []
        for data in frames.frames(data_guide, folder, segs, 1 / 6):
            r, gr, b = Image.frombytes("RGB", (320, 180), data).split()
            mask = ImageChops.multiply(
                ImageChops.multiply(r.point(lambda v: 255 if v > 150 else 0), b.point(lambda v: 255 if v > 150 else 0)),
                gr.point(lambda v: 255 if v < 110 else 0),
            )
            out.append(mask.getextrema()[1] > 0)
        return out

    control = magenta(guide)
    assert len(control) == timeline.total_frames(segs) and any(control)
    covered = {**guide, "steps": [{**s, "redact": [REDACT]} if s["image"] else s for s in guide["steps"]]}
    assert not any(magenta(covered))  # the pattern never appears, zoomed or not


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"\x89PNG\r\n\x1a\nxxxx", "PNG"),
        (b"\xff\xd8\xff\xe0", "JPEG"),
        (b"RIFF1234WEBPVP8 ", "WEBP"),
        (b"GIF89a", None),
    ],
)
def test_sniff(data, kind):
    assert layers.sniff(data) == kind


def test_open_screenshot_rejects_bad_files(tmp_path):
    (tmp_path / "x.png").write_bytes(b"\x89PNG\r\n\x1a\nnope")
    with pytest.raises(layers.ImageProblem):
        layers.open_screenshot(tmp_path / "x.png")
    Image.new("RGB", (9000, 6000)).save(tmp_path / "big.png", optimize=False, compress_level=1)
    with pytest.raises(layers.ImageProblem, match="كبيرة"):
        layers.open_screenshot(tmp_path / "big.png")
    Image.new("RGB", (40, 40)).save(tmp_path / "a.gif")
    with pytest.raises(layers.ImageProblem):
        layers.open_screenshot(tmp_path / "a.gif")
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(tmp_path / "t.png")
    assert layers.open_screenshot(tmp_path / "t.png").getpixel((5, 5)) == (255, 255, 255)


def test_ass_has_rtl_marks_styles_and_escaping(sample):
    _, _, guide = sample
    guide = {**guide, "title": "Reset {\\b1}VPN"}
    segs = timeline.build(guide, {s["id"]: 2.0 for s in guide["steps"]})
    words = [narrate.tts.Word("VPN", segs[1].start_s + 0.5, segs[1].start_s + 0.9)]
    ass, cues = captions.build(guide, segs, words, exact=True)
    for style in ("Caption", "Title", "TitleSub", "StepLabel", "BadgeDisc", "BadgeNum", "CardText", "Kicker"):
        assert f"Style: {style}," in ass
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    for line in dialogues:
        text = line.split(",,0,0,0,,", 1)[1]
        assert "\\p1" in text or "\u200f" in text  # every text event carries a right-to-left mark
        start, end = line.split(",")[1:3]
        assert end > start
    assert "{\\b1}" not in ass and "(\\b1)" not in ass
    assert "الخطوة ١ من ٣" in ass and "٣ خطوات" in ass
    assert len(cues) == 1 and cues[0][2] == "VPN"
    assert "CardText" in ass  # step 3 has no image → text card


def test_steps_label():
    assert [captions.steps_label(n) for n in (1, 2, 3, 10, 11)] == [
        "خطوة واحدة",
        "خطوتين",
        "٣ خطوات",
        "١٠ خطوات",
        "١١ خطوة",
    ]


def test_srt_format():
    text = captions.srt([(0.0, 1.5, "اهلا"), (61.25, 62.0, "VPN")])
    assert text.startswith("﻿1\n00:00:00,000 --> 00:00:01,850\n‏اهلا")
    assert "2\n00:01:01,250 --> 00:01:02,350" in text


def test_ffmpeg_args_are_relative_and_complete():
    args = render.ffmpeg_args("ffmpeg", 320, 180, 23, 12.5, render.Path("/abs/out.mp4.part"))
    joined = " ".join(args)
    assert "ass=filename=subs.ass:fontsdir=fonts" in joined
    assert "-s 320x180" in joined and "-t 12.500" in joined and args[-1] == "/abs/out.mp4.part"


@needs_libass
def test_end_to_end_render(sample, tmp_path):
    store, slug, guide = sample
    out = tmp_path / "out" / "guide.mp4"
    seen = []
    result = render.render(
        guide,
        store.folder(slug),
        out,
        narrate.Narrator(ffmpeg=FFMPEG, cache_dir=tmp_path / "cache"),
        quality="fast",
        scale=1 / 6,
        engine="fake",
        on_progress=lambda p: seen.append((p.stage, p.percent)),
    )
    assert result.video == out and out.is_file() and result.srt.is_file()
    assert not list(out.parent.glob("*.part"))
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(probe.stdout)
    assert float(info["format"]["duration"]) == pytest.approx(result.duration, abs=0.1)
    kinds = {s["codec_type"] for s in info["streams"]}
    assert kinds == {"video", "audio"}
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (320, 180)
    percents = [p for _, p in seen]
    assert percents == sorted(percents) and percents[-1] == 100


@needs_libass
def test_cancel_keeps_previous_video(sample, tmp_path):
    store, slug, guide = sample
    out = tmp_path / "guide.mp4"
    out.write_bytes(b"previous good video")
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 40

    with pytest.raises(narrate.Cancelled):
        render.render(
            guide,
            store.folder(slug),
            out,
            narrate.Narrator(ffmpeg=FFMPEG, cache_dir=tmp_path / "cache"),
            scale=1 / 6,
            engine="fake",
            cancelled=cancelled,
        )
    assert out.read_bytes() == b"previous good video"
    assert not list(tmp_path.glob("*.part"))


def test_render_refuses_invalid_guide(sample, tmp_path):
    store, slug, guide = sample
    guide["steps"][1]["text"] = ""
    with pytest.raises(g.GuideError, match="الخطوة 2"):
        render.render(
            guide, store.folder(slug), tmp_path / "x.mp4", narrate.Narrator(cache_dir=tmp_path / "c"), engine="fake"
        )
