"""Pure geometry: where the screenshot sits, where the camera looks, where the ring goes.

All coordinates are in "frame space": the 1920x1080 frame at zoom 1. The renderer multiplies
by its own scale. A viewport is the part of frame space the camera shows at a given moment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .consts import FRAME_H, FRAME_W, C

CARD = C["card"]
CAM = C["camera"]
RING = C["ring"]
ASPECT = FRAME_W / FRAME_H


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def contains(self, other: Rect, eps: float = 1e-6) -> bool:
        return (
            other.x >= self.x - eps
            and other.y >= self.y - eps
            and other.x1 <= self.x1 + eps
            and other.y1 <= self.y1 + eps
        )

    def scaled(self, k: float) -> Rect:
        return Rect(self.x * k, self.y * k, self.w * k, self.h * k)

    def outset(self, d: float) -> Rect:
        return Rect(self.x - d, self.y - d, self.w + 2 * d, self.h + 2 * d)


FULL = Rect(0, 0, FRAME_W, FRAME_H)
CARD_BOX = Rect(CARD["x0"], CARD["y0"], CARD["x1"] - CARD["x0"], CARD["y1"] - CARD["y0"])


def fit_image(img_w: int, img_h: int, box: Rect = CARD_BOX) -> Rect:
    """The screenshot scaled to fit the card box, centred. Small crops are enlarged at most 2x."""
    k = min(box.w / img_w, box.h / img_h, 2.0)
    w, h = img_w * k, img_h * k
    return Rect(box.cx - w / 2, box.cy - h / 2, w, h)


def to_frame(norm: dict, card: Rect) -> Rect:
    """A rectangle normalized to the image (0..1) placed in frame space."""
    return Rect(card.x + norm["x"] * card.w, card.y + norm["y"] * card.h, norm["w"] * card.w, norm["h"] * card.h)


def target_view(focus: Rect | None, card: Rect) -> Rect:
    """The viewport that frames the focus box with padding, as 16:9, inside the frame."""
    if focus is None:
        return FULL
    pad = CAM["pad"] * max(focus.w, focus.h)
    pw, ph = focus.w + 2 * pad, focus.h + 2 * pad
    if pw / ph < ASPECT:
        pw = ph * ASPECT
    else:
        ph = pw / ASPECT
    zoom = min(max(FRAME_W / pw, 1.0), CAM["zmax"])
    vw, vh = FRAME_W / zoom, FRAME_H / zoom
    view = Rect(focus.cx - vw / 2, focus.cy - vh / 2, vw, vh)
    return _inside(_inside(view, card), FULL)  # stay on the screenshot when it is big enough


def _inside(view: Rect, bounds: Rect) -> Rect:
    x = min(max(view.x, bounds.x), bounds.x1 - view.w) if view.w <= bounds.w else bounds.cx - view.w / 2
    y = min(max(view.y, bounds.y), bounds.y1 - view.h) if view.h <= bounds.h else bounds.cy - view.h / 2
    return Rect(x, y, view.w, view.h)


def zoom_of(view: Rect) -> float:
    return FRAME_W / view.w


def ease(t: float) -> float:
    """Cubic in-out on 0..1."""
    t = min(max(t, 0.0), 1.0)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp_view(a: Rect, b: Rect, t: float) -> Rect:
    """Interpolate the edges; the result keeps 16:9 because both ends do."""
    e = ease(t)
    x0 = a.x + (b.x - a.x) * e
    y0 = a.y + (b.y - a.y) * e
    x1 = a.x1 + (b.x1 - a.x1) * e
    y1 = a.y1 + (b.y1 - a.y1) * e
    return Rect(x0, y0, x1 - x0, y1 - y0)


def to_screen(rect: Rect, view: Rect) -> Rect:
    """Frame-space rectangle → where it appears on screen with this viewport."""
    k = FRAME_W / view.w
    return Rect((rect.x - view.x) * k, (rect.y - view.y) * k, rect.w * k, rect.h * k)


def ring_rect(focus: Rect, view: Rect) -> Rect:
    return to_screen(focus, view).outset(RING["outset"])


def pixel_box(norm: dict, img_w: int, img_h: int, grow: int = 2) -> tuple[int, int, int, int]:
    """Normalized rectangle → pixel box rounded outward, grown by `grow` px, clipped to the image."""
    x0 = math.floor(norm["x"] * img_w + 1e-9) - grow
    y0 = math.floor(norm["y"] * img_h + 1e-9) - grow
    x1 = math.ceil((norm["x"] + norm["w"]) * img_w - 1e-9) + grow
    y1 = math.ceil((norm["y"] + norm["h"]) * img_h - 1e-9) + grow
    return max(x0, 0), max(y0, 0), min(x1, img_w), min(y1, img_h)
