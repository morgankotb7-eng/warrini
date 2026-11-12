"""Frame generator: one RGB frame at a time, smooth sub-pixel camera moves.

Each step builds a canvas at 2x the output size once. A frame is a single
`resize(box=<float viewport>)` from that canvas, plus the spotlight and ring when visible.
Frames that look the same as the previous one are reused without redrawing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from PIL import Image

from . import geometry as geo
from . import layers
from .consts import FPS, FRAME_H, FRAME_W, THEMES, TIMING, C
from .timeline import Segment

CAM = C["camera"]
RING = C["ring"]


class Scene:
    """Everything needed to draw the frames of one segment."""

    def __init__(self, seg: Segment, guide: dict, folder: Path, scale: float, bg: Image.Image, start_view: geo.Rect):
        self.seg = seg
        self.scale = scale
        self.size = (round(FRAME_W * scale), round(FRAME_H * scale))
        self.theme = THEMES[guide["theme"]]
        self.k = 2 * scale  # canvas scale
        self.total_steps = len(guide["steps"])
        self.start_view = start_view
        self.focus: geo.Rect | None = None
        self.target = geo.FULL
        self._last_key = None
        self._last_frame: Image.Image | None = None
        if seg.kind == "title":
            self.canvas = None
            self.static = layers.title_decor(bg, self.theme, scale)
            self.done = 0
        elif seg.kind == "end":
            self.canvas = None
            self.static = layers.end_decor(bg, self.theme, scale)
            self.done = self.total_steps
        else:
            step = guide["steps"][seg.index]
            self.done = seg.index + 1
            if step["image"]:
                shot = layers.open_screenshot(folder / step["image"])
                shot = layers.redact(shot, step["redact"])
                card = geo.fit_image(*shot.size)
                big_bg = bg.resize((round(FRAME_W * self.k), round(FRAME_H * self.k)), Image.BICUBIC)
                self.canvas = layers.card_canvas(big_bg, shot, card, self.k, self.theme)
                if step["focus"]:
                    self.focus = geo.to_frame(step["focus"], card)
                    self.target = geo.target_view(self.focus, card)
                self.static = None
            else:
                self.canvas = None
                self.static = layers.card_canvas(bg, None, geo.CARD_BOX, scale, self.theme)
        if self.static is not None:
            self.static = layers.progress_bar(self.static.copy(), self.done, self.total_steps, self.theme, scale)

    # timing helpers (seconds from segment start)
    @property
    def duration(self) -> float:
        return self.seg.seconds

    @property
    def zoom_out_start(self) -> float:
        return self.duration - TIMING["fade"] - CAM["zoom_out"]

    def view_at(self, t: float) -> geo.Rect:
        view = geo.lerp_view(self.start_view, self.target, t / CAM["move"]) if t <= CAM["move"] else self.target
        if self.seg.zoom_out and t >= self.zoom_out_start:
            view = geo.lerp_view(self.target, geo.FULL, (t - self.zoom_out_start) / CAM["zoom_out"])
        return view

    @property
    def end_view(self) -> geo.Rect:
        return geo.FULL if self.seg.zoom_out else self.target

    def ring_state(self, t: float) -> tuple[float, float]:
        """(appear 0..1, alpha 0..1)."""
        if self.focus is None:
            return 0.0, 0.0
        appear = (t - CAM["move"]) / RING["appear"]
        if appear <= 0:
            return 0.0, 0.0
        gone = self.zoom_out_start if self.seg.zoom_out else self.duration
        alpha = min(1.0, max(0.0, (gone - t) / RING["fade_out"]))
        return min(appear, 1.0), alpha

    def frame(self, f: int) -> Image.Image:
        if self.static is not None:
            return self.static
        t = f / FPS
        view = self.view_at(t)
        appear, alpha = self.ring_state(t)
        key = (round(view.x, 3), round(view.y, 3), round(view.w, 3), round(appear, 3), round(alpha, 3))
        if key == self._last_key and self._last_frame is not None:
            return self._last_frame
        box = (view.x * self.k, view.y * self.k, view.x1 * self.k, view.y1 * self.k)
        box = tuple(min(max(v, 0.0), lim) for v, lim in zip(box, (*self.canvas.size, *self.canvas.size), strict=True))
        img = self.canvas.resize(self.size, Image.BICUBIC, box=box, reducing_gap=None)
        if alpha > 0 and self.focus is not None:
            img = layers.spotlight_and_ring(img, geo.ring_rect(self.focus, view), appear, alpha, self.theme, self.scale)
        img = layers.progress_bar(img, self.done, self.total_steps, self.theme, self.scale)
        self._last_key, self._last_frame = key, img
        return img


def frames(
    guide: dict,
    folder: Path,
    segments: list[Segment],
    scale: float,
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[bytes]:
    """Yield raw RGB24 frames for the whole video."""
    bg = layers.background(THEMES[guide["theme"]], (round(FRAME_W * scale), round(FRAME_H * scale)))
    fade_frames = round(TIMING["fade"] * FPS)
    scene = Scene(segments[0], guide, folder, scale, bg, geo.FULL)
    for i, seg in enumerate(segments):
        nxt = None
        if i + 1 < len(segments):
            start = scene.end_view if segments[i + 1].continues else geo.FULL
            nxt = Scene(segments[i + 1], guide, folder, scale, bg, start)
        last_bytes: bytes | None = None
        last_img: Image.Image | None = None
        for f in range(seg.frames):
            if cancelled():
                return
            img = scene.frame(f)
            from_end = seg.frames - f
            if seg.fade_out and nxt is not None and from_end <= fade_frames:
                a = (fade_frames - from_end + 1) / (fade_frames + 1)
                img = Image.blend(img, nxt.frame(0), a)
            if img is last_img and last_bytes is not None:
                yield last_bytes
                continue
            last_img, last_bytes = img, img.tobytes()
            yield last_bytes
        if nxt is not None:
            scene = nxt
