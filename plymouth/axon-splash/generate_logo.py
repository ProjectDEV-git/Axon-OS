#!/usr/bin/env python3
"""
Generate the Axon OS Plymouth splash images.

axon.png — 400x400: the shared flat Axon mark (theme/axon_mark.py) on the
    neutral dark ground #161616, so it matches the app icon and GRUB.
progress-track.png / progress-fill.png — 1x1 solid colours that axon.script
    scales into the progress bar (Plymouth's script API can only draw images):
    track #282828 (surface_raised), fill #2b6fde (the Axon accent).
"""

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "theme"))
from axon_mark import GROUND, draw_mark  # noqa: E402

SIZE = 400


def main() -> None:
    logo = draw_mark(SIZE, background=GROUND, fill=0.16)
    logo.convert("RGB").save(os.path.join(HERE, "axon.png"))
    for name, colour in (
        ("progress-track.png", (40, 40, 40)),
        ("progress-fill.png", (43, 111, 222)),
    ):
        Image.new("RGB", (1, 1), colour).save(os.path.join(HERE, name))
    print(f"Written to {HERE}")


if __name__ == "__main__":
    main()
