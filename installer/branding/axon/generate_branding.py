#!/usr/bin/env python3
"""Generate Calamares branding images for Axon OS.

Produces logo.png, icon.png and welcome.png next to this script using
the shared flat Axon mark on the neutral dark ground. Run once and commit:

    python3 generate_branding.py
"""

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))

# The shared mark (theme/axon_mark.py): flat, the same geometry as the app icon
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "theme"))
from axon_mark import GROUND, LINE, draw_mark


def make_welcome(width=800, height=350):
    """Welcome banner: the mark on the left of a flat neutral ground."""
    img = Image.new("RGBA", (width, height), GROUND)
    mark = draw_mark(int(height * 0.7), line=LINE)
    img.alpha_composite(mark, (int(width * 0.06), int(height * 0.15)))
    return img.convert("RGB")


def main():
    draw_mark(256).save(os.path.join(HERE, "logo.png"))
    draw_mark(64).save(os.path.join(HERE, "icon.png"))
    make_welcome().save(os.path.join(HERE, "welcome.png"))
    sys.stdout.write(f"Branding images written to {HERE}\n")


if __name__ == "__main__":
    main()
