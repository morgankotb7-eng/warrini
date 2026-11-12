"""One timing table for the whole video. Geometry, audio and captions all read from it.

Every segment is a whole number of frames, so audio padded to the same boundaries never drifts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .consts import FPS, TIMING, C

CAM = C["camera"]


@dataclass(frozen=True)
class Segment:
    kind: str  # "title" | "step" | "end"
    index: int  # step index for kind == "step", else -1
    start: int  # first frame
    frames: int  # length in frames
    voice_at: float  # seconds from the segment start where narration begins
    voice_len: float  # narration length in seconds (0 = silent)
    zoom_out: bool  # camera returns to full view before the segment ends
    fade_out: bool  # crossfade into the next segment
    continues: bool  # camera starts where the previous step ended (same screenshot)

    @property
    def seconds(self) -> float:
        return self.frames / FPS

    @property
    def start_s(self) -> float:
        return self.start / FPS

    @property
    def end(self) -> int:
        return self.start + self.frames


def to_frames(seconds: float) -> int:
    return max(1, math.ceil(seconds * FPS - 1e-9))


def same_visual(a: dict, b: dict) -> bool:
    """Two steps on the same screenshot with the same covered areas: the camera just moves on."""
    return bool(a.get("image")) and a.get("image") == b.get("image") and a.get("redact") == b.get("redact")


def build(guide: dict, voice: dict[str, float]) -> list[Segment]:
    """`voice` maps segment keys ("title", "end", or a step id) to narration seconds."""
    steps = guide["steps"]
    segments: list[Segment] = []
    cursor = 0

    def add(kind: str, index: int, duration: float, voice_at: float, voice_len: float, **flags: bool) -> None:
        nonlocal cursor
        frames = to_frames(duration)
        segments.append(
            Segment(
                kind,
                index,
                cursor,
                frames,
                voice_at,
                voice_len,
                flags.get("zoom_out", False),
                flags.get("fade_out", True),
                flags.get("continues", False),
            )
        )
        cursor += frames

    n = voice.get("title", 0.0)
    lead, tail = TIMING["card_lead_in"], TIMING["card_tail"]
    add("title", -1, max(TIMING["title_min"], lead + n + tail), lead, n)

    for i, step in enumerate(steps):
        n = voice.get(step["id"], 0.0)
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        continues_next = nxt is not None and same_visual(step, nxt)
        zoom_out = bool(step.get("image")) and step.get("focus") is not None and not continues_next
        tail = max(step.get("pause_after", 0.6), TIMING["min_tail"])
        if zoom_out:
            tail = max(tail, CAM["zoom_out"] + TIMING["fade"])
        speech = max(n, CAM["move"] - TIMING["lead_in"])
        add(
            "step",
            i,
            TIMING["lead_in"] + speech + tail,
            TIMING["lead_in"],
            n,
            zoom_out=zoom_out,
            fade_out=not continues_next,
            continues=i > 0 and same_visual(steps[i - 1], step),
        )

    n = voice.get("end", 0.0)
    if n > 0:
        add("end", -1, lead + n + tail_end(), lead, n, fade_out=False)
    else:
        add("end", -1, TIMING["end_silent"], 0.0, 0.0, fade_out=False)
    return segments


def tail_end() -> float:
    return TIMING["card_tail"]


def total_frames(segments: list[Segment]) -> int:
    return segments[-1].end if segments else 0


def estimate_seconds(text: str) -> float:
    """Rough speech length before the voice exists (UI hint, test fake voice)."""
    return round(len(text) / TIMING["chars_per_second"], 2) if text else 0.0
