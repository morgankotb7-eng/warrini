"""CI check that the bundled local voice really speaks: one short Egyptian sentence."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    from warrini import narrate

    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        narrator = narrate.Narrator(cache_dir=Path(tmp))
        try:
            clip = narrator.clip("voicetut", {"speaker": "Tareq"}, "اهلا بيك في ورّيني.")
        finally:
            narrator.close()
        print(f"voice ok: {clip.duration:.2f}s of audio in {time.monotonic() - started:.0f}s")
        return 0 if clip.duration > 0.5 else 1


if __name__ == "__main__":
    sys.exit(main())
