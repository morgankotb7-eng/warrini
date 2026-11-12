"""Layout, timing and theme constants shared by the renderer and the web editor (web/constants.json)."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files


@cache
def load() -> dict:
    return json.loads(files("warrini").joinpath("web/constants.json").read_text(encoding="utf-8"))


C = load()
FRAME_W: int = C["frame"]["w"]
FRAME_H: int = C["frame"]["h"]
FPS: int = C["frame"]["fps"]
TIMING: dict = C["timing"]
LIMITS: dict = C["limits"]
THEMES: dict = C["themes"]
DEFAULT_THEME = "navy"


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
