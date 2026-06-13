#!/usr/bin/env python3
"""Generate Calamares branding images for Axon OS.

Produces logo.png, icon.png and welcome.png next to this script using
the Axon violet-on-dark design language. Run once and commit the output:

    python3 generate_branding.py
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

BLUE = (37, 99, 235)
BLUE_LIGHT = (96, 165, 250)
BG_DARK = (15, 15, 23)


def _hexagon(center, radius, rotation=math.pi / 6):
    cx, cy = center
    return [
        (
            cx + radius * math.cos(rotation + i * math.pi / 3),
            cy + radius * math.sin(rotation + i * math.pi / 3),
        )
        for i in range(6)
    ]


def draw_mark(size, transparent=True):
    """Axon hexagon mark with a soft blue glow."""
    img = Image.new(
        "RGBA", (size, size), (0, 0, 0, 0) if transparent else (*BG_DARK, 255)
    )

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon(_hexagon((size / 2, size / 2), size * 0.36), fill=(*BLUE, 180))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.09))
    img.alpha_composite(glow)

    d = ImageDraw.Draw(img)
    d.polygon(
        _hexagon((size / 2, size / 2), size * 0.34),
        outline=(*BLUE_LIGHT, 255),
        width=max(2, size // 28),
    )
    d.polygon(_hexagon((size / 2, size / 2), size * 0.20), fill=(*BLUE, 255))
    r = size * 0.055
    d.ellipse(
        (size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r),
        fill=(255, 255, 255, 255),
    )
    return img


def make_welcome(width=800, height=350):
    """Welcome banner: dark gradient backdrop with the mark on the left."""
    img = Image.new("RGB", (width, height), BG_DARK)
    d = ImageDraw.Draw(img)
    for y in range(height):
        t = y / height
        d.line(
            [(0, y), (width, y)],
            fill=(
                int(BG_DARK[0] + 18 * t),
                int(BG_DARK[1] + 10 * t),
                int(BG_DARK[2] + 34 * t),
            ),
        )
    mark = draw_mark(int(height * 0.8))
    img = img.convert("RGBA")
    img.alpha_composite(mark, (int(width * 0.06), int(height * 0.1)))
    return img.convert("RGB")


def main():
    draw_mark(256).save(os.path.join(HERE, "logo.png"))
    draw_mark(64).save(os.path.join(HERE, "icon.png"))
    make_welcome().save(os.path.join(HERE, "welcome.png"))
    from axon_logger import configure_app_logger
    logger = configure_app_logger(__name__)
    logger.info("Branding images written to %s", HERE)


if __name__ == "__main__":
    main()
