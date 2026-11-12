"""All on-screen text as one ASS script (libass shapes Arabic), plus an SRT sidecar.

Every event starts with U+200F (RIGHT-TO-LEFT MARK). Without it libass lays out a line that
starts with an English word ("VPN", "Outlook") left to right and the word order breaks.
"""

from __future__ import annotations

from ehky import subs
from ehky.tts import Word

from .consts import FPS, THEMES, hex_rgb
from .guide import to_arabic_digits
from .timeline import Segment

RLM = "‏"
FONT = "Cairo"
FONT_BOLD = "Cairo"  # static Bold instance; the Bold flag picks it
CAPTION_SIZE = 54
CAPTION_MARGIN = 120
BADGE = (1810, 70, 38)


def ass_colour(hex_colour: str, alpha: int = 0) -> str:
    r, g, b = hex_rgb(hex_colour)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").replace("\r", " ")


def wrap(text: str, max_chars: int, max_lines: int) -> str:
    """Break plain text into lines ourselves (explicit \\N), so libass never wraps RTL text."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > max_chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .،") + "…"
    return "\\N".join(f"{RLM}{escape(line)}" for line in lines)


def steps_label(n: int) -> str:
    """Egyptian Arabic count: خطوة واحدة، خطوتين، ٣ خطوات، ١١ خطوة."""
    if n == 1:
        return "خطوة واحدة"
    if n == 2:
        return "خطوتين"
    return f"{to_arabic_digits(n)} {'خطوات' if 3 <= n <= 10 else 'خطوة'}"


def ts(seconds: float) -> str:
    cs = max(round(seconds * 100), 0)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def header(theme_name: str) -> str:
    t = THEMES[theme_name]
    text = ass_colour(t["text"])
    muted = ass_colour(t["muted"])
    accent = ass_colour(t["accent"])
    box = ass_colour(t["caption_box"], 0x30)
    dark = "&H00000000"
    shadow = "&HA0000000"
    fmt = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    styles = [
        f"Style: Caption,{FONT},{CAPTION_SIZE},{text},{text},{box},{dark},-1,0,0,0,100,100,0,0,3,16,0,2,"
        f"{CAPTION_MARGIN},{CAPTION_MARGIN},52,-1",
        f"Style: Title,{FONT},100,{text},{text},{dark},{shadow},-1,0,0,0,100,100,0,0,1,0,3,5,160,160,0,-1",
        f"Style: TitleSub,{FONT},56,{muted},{muted},{dark},{dark},0,0,0,0,100,100,0,0,1,0,0,5,160,160,0,-1",
        f"Style: StepLabel,{FONT},40,{text},{text},{box},{dark},-1,0,0,0,100,100,0,0,3,10,0,6,0,0,0,-1",
        f"Style: BadgeDisc,{FONT},20,{accent},{accent},{dark},{dark},0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,-1",
        f"Style: BadgeNum,{FONT},46,{ass_colour(t['bg0'])},{ass_colour(t['bg0'])},{dark},{dark},-1,0,0,0,100,100,0,0,"
        f"1,0,0,5,0,0,0,-1",
        f"Style: Kicker,{FONT},46,{accent},{accent},{dark},{dark},-1,0,0,0,100,100,0,0,1,0,0,5,160,160,0,-1",
        f"Style: CardText,{FONT},64,{text},{text},{dark},{dark},-1,0,0,0,100,100,0,0,1,0,0,5,260,260,0,-1",
    ]
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n" + fmt + "\n" + "\n".join(styles) + "\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _circle(r: int) -> str:
    k = round(r * 0.5523)
    d = 2 * r
    return (
        f"m {r} 0 b {r + k} 0 {d} {r - k} {d} {r} b {d} {r + k} {r + k} {d} {r} {d} "
        f"b {r - k} {d} 0 {r + k} 0 {r} b 0 {r - k} {r - k} 0 {r} 0"
    )


def build(
    guide: dict,
    segments: list[Segment],
    words: list[Word],
    exact: bool,
) -> tuple[str, list[tuple[float, float, str]]]:
    """Returns (ass_text, caption_cues). Cues feed the SRT file."""
    theme = THEMES[guide["theme"]]
    accent = ass_colour(theme["accent"])
    total_steps = len(guide["steps"])
    events: list[str] = []

    def add(layer: int, start: float, end: float, style: str, text: str) -> None:
        if end - start >= 1 / FPS:
            events.append(f"Dialogue: {layer},{ts(start)},{ts(end)},{style},,0,0,0,,{text}")

    caption_windows: list[tuple[float, float]] = []
    for seg in segments:
        start, end = seg.start_s, seg.start_s + seg.seconds
        fade = "{\\fad(250,250)}"
        if seg.kind == "title":
            title = wrap(guide["title"], 28, 2)
            two = "\\N" in title
            add(1, start, end, "Kicker", f"{fade}{{\\pos(960,{290 if two else 380})}}{RLM}دليل خطوة بخطوة")
            add(1, start, end, "Title", f"{fade}{{\\pos(960,{420 if two else 465})}}{title}")
            count = RLM + steps_label(total_steps)
            add(1, start, end, "TitleSub", f"{fade}{{\\pos(960,600)}}{count}")
            if guide.get("intro"):  # spoken text differs from the title on screen: caption it
                caption_windows.append((start, end))
        elif seg.kind == "end":
            add(1, start, end, "Title", f"{fade}{{\\pos(960,520)}}{RLM}كده خلصت")
            add(1, start, end, "TitleSub", f"{fade}{{\\pos(960,620)}}{wrap(guide['title'], 48, 1)}")
            if seg.voice_len > 0:
                caption_windows.append((start, end))
        else:
            step = guide["steps"][seg.index]
            number = to_arabic_digits(seg.index + 1)
            cx, cy, r = BADGE
            disc = f"{{\\an5\\pos({cx},{cy})\\p1\\bord0\\shad0}}{_circle(r)}{{\\p0}}"
            add(2, start, end, "BadgeDisc", f"{fade}{disc}")
            add(3, start, end, "BadgeNum", f"{fade}{{\\pos({cx},{cy - 2})}}{RLM}{number}")
            label = f"{RLM}الخطوة {number} من {to_arabic_digits(total_steps)}"
            add(2, start, end, "StepLabel", f"{fade}{{\\pos({cx - r - 22},{cy})}}{label}")
            if step["image"]:
                caption_windows.append((start, end))
            else:
                add(1, start, end, "CardText", f"{fade}{{\\pos(960,515)}}{wrap(step['text'], 34, 5)}")

    cues: list[tuple[float, float, str]] = []
    for line in subs.group_lines(words, subs.max_chars_for(1920, CAPTION_SIZE, CAPTION_MARGIN), 0.6):
        if not any(a <= line.start < b for a, b in caption_windows):
            continue
        cues.append((line.start, line.end, line.text))
        window_end = next(b for a, b in caption_windows if a <= line.start < b)
        for s, e, text in subs.events([line], highlight=exact):
            text = text.replace(subs.HIGHLIGHT, accent)
            add(0, s, min(e, window_end), "Caption", f"{RLM}{text}")
    return header(guide["theme"]) + "\n".join(events) + "\n", cues


def srt(cues: list[tuple[float, float, str]]) -> str:
    def stamp(seconds: float) -> str:
        ms = max(round(seconds * 1000), 0)
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for i, (a, b, text) in enumerate(cues, 1):
        end = b + 0.35
        if i < len(cues):
            end = min(end, cues[i][0])  # never overlap the next cue
        blocks.append(f"{i}\n{stamp(a)} --> {stamp(end)}\n{RLM}{text}\n")
    return "﻿" + "\n".join(blocks)
