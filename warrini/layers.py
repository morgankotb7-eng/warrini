"""Pillow drawing: backgrounds, the screenshot card, redaction, spotlight, ring and progress.

No text is drawn here; every letter goes through libass (captions.py), which shapes Arabic
correctly on every OS. Sizes are given in frame space and multiplied by a scale factor.
"""

from __future__ import annotations

import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from . import geometry as geo
from .consts import LIMITS, C, hex_rgb

CARD = C["card"]
RING = C["ring"]
SPOT = C["spotlight"]
PROGRESS = C["progress"]
Image.MAX_IMAGE_PIXELS = None  # we check the size ourselves before decoding (see open_screenshot)


class ImageProblem(ValueError):
    """The uploaded file is not a usable screenshot. Arabic message."""


MAGIC = {b"\x89PNG\r\n\x1a\n": "PNG", b"\xff\xd8\xff": "JPEG"}


def sniff(data: bytes) -> str | None:
    for magic, kind in MAGIC.items():
        if data.startswith(magic):
            return kind
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def open_screenshot(path_or_file) -> Image.Image:
    """Decode safely: format, size and frame count are checked before pixels are loaded."""
    try:
        img = Image.open(path_or_file)
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for bad files
        raise ImageProblem("الملف ده مش صورة سليمة") from exc
    if img.format not in ("PNG", "JPEG", "WEBP"):
        raise ImageProblem("الصور المسموحة PNG أو JPG أو WebP بس")
    width, height = img.size
    if width * height > LIMITS["image_mp"] * 1_000_000:
        raise ImageProblem(f"الصورة كبيرة قوي ({width}×{height}). الأقصى {LIMITS['image_mp']} ميجا بكسل")
    if width < 16 or height < 16:
        raise ImageProblem("الصورة صغيرة قوي")
    if getattr(img, "n_frames", 1) != 1:
        raise ImageProblem("الصور المتحركة مش مدعومة، ابعت لقطة شاشة عادية")
    try:
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as exc:  # noqa: BLE001
        raise ImageProblem("الصورة دي باظت ومش قادر أفتحها") from exc
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return img.convert("RGB")


# --------------------------------------------------------------------------- redaction


def redact(img: Image.Image, rects: list[dict], seed: int = 7) -> Image.Image:
    """Cover each box with an opaque fill sampled from just outside it. Original pixels are gone."""
    if not rects:
        return img
    out = img.copy()
    width, height = out.size
    draw = ImageDraw.Draw(out)
    rnd = random.Random(seed)
    for rect in rects:
        x0, y0, x1, y1 = geo.pixel_box(rect, width, height)
        if x1 <= x0 or y1 <= y0:
            continue
        colour = _surround_colour(img, (x0, y0, x1, y1))
        radius = min(6, (x1 - x0) // 4, (y1 - y0) // 4)
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=colour)  # hard edges first: nothing leaks at corners
        if radius > 0 and (x1 - x0) > 24 and (y1 - y0) > 12:
            shade = tuple(max(0, c - 10) for c in colour)
            draw.rounded_rectangle((x0 + 2, y0 + 2, x1 - 3, y1 - 3), radius=radius, outline=shade, width=1)
        if (x1 - x0) * (y1 - y0) > 64:
            noise = Image.effect_noise((x1 - x0, y1 - y0), 6 + rnd.random()).convert("RGB")
            patch = Image.blend(
                out.crop((x0, y0, x1, y1)), ImageChops.multiply(noise, Image.new("RGB", noise.size, colour)), 0.02
            )
            out.paste(patch, (x0, y0))
    return out


def _surround_colour(img: Image.Image, box: tuple[int, int, int, int], ring: int = 6) -> tuple[int, int, int]:
    x0, y0, x1, y1 = box
    width, height = img.size
    strips = [
        (x0, max(y0 - ring, 0), x1, y0),
        (x0, y1, x1, min(y1 + ring, height)),
        (max(x0 - ring, 0), y0, x0, y1),
        (x1, y0, min(x1 + ring, width), y1),
    ]
    hist = [0] * 768
    for sx0, sy0, sx1, sy1 in strips:
        if sx1 > sx0 and sy1 > sy0:
            for i, count in enumerate(img.crop((sx0, sy0, sx1, sy1)).histogram()):
                hist[i] += count
    total = sum(hist[:256])
    if not total:
        return (128, 128, 128)
    return tuple(_median(hist[c * 256 : (c + 1) * 256], total) for c in range(3))  # type: ignore[return-value]


def _median(channel: list[int], total: int) -> int:
    seen = 0
    for value, count in enumerate(channel):
        seen += count
        if seen * 2 >= total:
            return value
    return 255


# --------------------------------------------------------------------------- backgrounds and cards


def background(theme: dict, size: tuple[int, int]) -> Image.Image:
    """Diagonal two-colour gradient with a soft vignette."""
    width, height = size
    c0, c1 = Image.new("RGB", size, hex_rgb(theme["bg0"])), Image.new("RGB", size, hex_rgb(theme["bg1"]))
    ramp = Image.linear_gradient("L").rotate(-35, resample=Image.BICUBIC, expand=True)
    ramp = ImageOps.fit(ramp, size, method=Image.BICUBIC)
    img = Image.composite(c0, c1, ramp)
    vignette = Image.radial_gradient("L").resize(size, Image.BICUBIC)
    vignette = vignette.point(lambda v: int(max(0, v - 120) * 0.35))
    return Image.composite(Image.new("RGB", size, (0, 0, 0)), img, vignette)


def _rounded_mask(size: tuple[int, int], radius: int, supersample: int = 3) -> Image.Image:
    big = Image.new("L", (size[0] * supersample, size[1] * supersample), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, big.width - 1, big.height - 1), radius=radius * supersample, fill=255)
    return big.resize(size, Image.LANCZOS)


def card_canvas(bg: Image.Image, shot: Image.Image | None, card: geo.Rect, k: float, theme: dict) -> Image.Image:
    """Background + shadowed rounded screenshot at canvas scale k (frame space × k)."""
    canvas = bg.copy()
    box = card.scaled(k)
    x, y = round(box.x), round(box.y)
    w, h = max(1, round(box.w)), max(1, round(box.h))
    radius = max(2, round(CARD["radius"] * k))
    shadow_cfg = CARD["shadow"]
    pad = round(shadow_cfg["blur"] * 2 * k)
    shadow = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    shadow.paste(_rounded_mask((w, h), radius), (pad, pad))
    shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_cfg["blur"] * k)).point(
        lambda v: int(v * shadow_cfg["alpha"])
    )
    canvas.paste(
        Image.new("RGB", shadow.size, (0, 0, 0)),
        (x - pad + round(shadow_cfg["dx"] * k), y - pad + round(shadow_cfg["dy"] * k)),
        shadow,
    )
    mask = _rounded_mask((w, h), radius)
    if shot is None:
        panel = Image.new("RGB", (w, h), _mix(hex_rgb(theme["bg1"]), (255, 255, 255), 0.06))
        canvas.paste(panel, (x, y), mask)
        return canvas
    resized = shot.resize((w, h), Image.LANCZOS, reducing_gap=3.0)
    canvas.paste(resized, (x, y), mask)
    edge = Image.new("L", (w, h), 0)
    ImageDraw.Draw(edge).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, outline=40, width=max(1, round(k)))
    canvas.paste(Image.new("RGB", (w, h), (255, 255, 255)), (x, y), edge)
    return canvas


def title_decor(img: Image.Image, theme: dict, scale: float) -> Image.Image:
    """A short accent bar between the title and its subtitle."""
    out = img.copy()
    bar = geo.Rect(900, 532, 120, 6).scaled(scale)
    ImageDraw.Draw(out).rounded_rectangle(
        (bar.x, bar.y, bar.x1, bar.y1), radius=max(1, round(3 * scale)), fill=hex_rgb(theme["accent"])
    )
    return out


def end_decor(img: Image.Image, theme: dict, scale: float) -> Image.Image:
    """A check mark in a ring, drawn at 3x and scaled down for smooth edges."""
    out = img.copy()
    ss = 3
    size = round(180 * scale)
    if size < 6:
        return out
    layer = Image.new("L", (size * ss, size * ss), 0)
    draw = ImageDraw.Draw(layer)
    s = size * ss
    stroke = max(2, round(9 * scale * ss))
    draw.ellipse((stroke, stroke, s - stroke, s - stroke), outline=255, width=stroke)
    draw.line([(s * 0.30, s * 0.52), (s * 0.45, s * 0.66), (s * 0.71, s * 0.38)], fill=255, width=stroke, joint="curve")
    layer = layer.resize((size, size), Image.LANCZOS)
    pos = (round(960 * scale - size / 2), round(330 * scale - size / 2))
    out.paste(Image.new("RGB", (size, size), hex_rgb(theme["accent"])), pos, layer)
    return out


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


# --------------------------------------------------------------------------- per-frame overlays


def spotlight_and_ring(frame: Image.Image, ring: geo.Rect, appear: float, alpha: float, theme: dict, scale: float):
    """Dim everything outside the focus and draw the ring. `appear` 0..1 drives the scale-in."""
    if alpha <= 0:
        return frame
    e = geo.ease(appear)
    grow = RING["scale_from"] + (1 - RING["scale_from"]) * e
    r = geo.Rect(ring.cx - ring.w * grow / 2, ring.cy - ring.h * grow / 2, ring.w * grow, ring.h * grow).scaled(scale)
    vis = alpha * e
    radius = RING["radius"] * scale
    # spotlight: one full-frame mask (cheap), hole where the ring is
    dim = Image.new("L", frame.size, round(255 * SPOT["alpha"] * vis))
    ImageDraw.Draw(dim).rounded_rectangle((r.x, r.y, r.x1, r.y1), radius=radius, fill=0)
    frame = Image.composite(Image.new("RGB", frame.size, (0, 0, 0)), frame, dim)
    # ring: drawn at 2x on a small crop, then scaled down
    stroke = RING["stroke"] * scale
    m = stroke + 2
    x0, y0 = int(r.x - m), int(r.y - m)
    x1, y1 = int(r.x1 + m) + 1, int(r.y1 + m) + 1
    w, h = x1 - x0, y1 - y0
    if w <= 2 or h <= 2:
        return frame
    ss = 2
    layer = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(layer).rounded_rectangle(
        ((r.x - x0) * ss, (r.y - y0) * ss, (r.x1 - x0) * ss, (r.y1 - y0) * ss),
        radius=radius * ss,
        outline=round(255 * vis),
        width=max(1, round(stroke * ss)),
    )
    layer = layer.resize((w, h), Image.BILINEAR)
    frame.paste(Image.new("RGB", (w, h), hex_rgb(theme["accent"])), (x0, y0), layer)
    return frame


def progress_bar(frame: Image.Image, done: int, total: int, theme: dict, scale: float) -> Image.Image:
    """Segmented bar along the bottom edge: finished and current steps in the accent colour."""
    if total <= 0:
        return frame
    draw = ImageDraw.Draw(frame)
    y0 = PROGRESS["y0"] * scale
    y1 = (PROGRESS["y0"] + PROGRESS["h"]) * scale
    gap = 6 * scale
    width = frame.width
    seg = (width - gap * (total - 1)) / total
    accent = hex_rgb(theme["accent"])
    muted = _mix(hex_rgb(theme["bg0"]), hex_rgb(theme["muted"]), 0.35)
    for i in range(total):
        # RTL: the first step is on the right
        x1 = width - i * (seg + gap)
        x0 = x1 - seg
        draw.rectangle((x0, y0, x1 - 1, y1), fill=accent if i < done else muted)
    return frame
