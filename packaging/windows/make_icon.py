"""Draw the Warrini icon (navy tile, yellow play mark, white bar) as a multi-size .ico."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

NAVY, YELLOW, WHITE = (18, 34, 74, 255), (245, 200, 69, 255), (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    s = size * 4  # draw large, scale down for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.28), fill=NAVY)
    d.polygon([(s * 0.28, s * 0.36), (s * 0.56, s * 0.50), (s * 0.28, s * 0.64)], fill=YELLOW)
    d.rounded_rectangle((s * 0.625, s * 0.31, s * 0.725, s * 0.69), radius=round(s * 0.05), fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def main(out: Path) -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw(n) for n in sizes]
    images[-1].save(out, format="ICO", sizes=[(n, n) for n in sizes], append_images=images[:-1])
    print(out)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "warrini.ico"))
