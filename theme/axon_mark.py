"""The Axon mark as a raster image, for places SVG can't go (boot splash,
GRUB, the Calamares installer).

Same geometry as the app icon in scripts/gen_icons.py: a pointy-top hexagon
outline with a node at its centre, flat, no glow. Drawn at 4x and
downsampled so the edges are smooth. Needs Pillow.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

LINE = (125, 179, 255, 255)  # accent_text on dark, #7db3ff
NODE = (242, 242, 242, 255)  # fg on dark, #f2f2f2
GROUND = (22, 22, 22, 255)  # bg on dark, #161616

# Proportions of the icon glyph: hexagon radius 30, stroke 7, node 8.5
_STROKE = 7 / 30
_NODE = 8.5 / 30


def hexagon(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.radians(-90 + 60 * k)),
            cy + r * math.sin(math.radians(-90 + 60 * k)),
        )
        for k in range(6)
    ]


def draw_mark(
    size: int,
    line: tuple[int, int, int, int] = LINE,
    node: tuple[int, int, int, int] = NODE,
    background: tuple[int, int, int, int] | None = None,
    fill: float = 0.36,
) -> Image.Image:
    """The mark centred on a *size* square; *fill* is the hexagon radius as a
    share of the side."""
    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), background or (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = big * fill
    width = max(2, round(r * _STROKE))
    pts = hexagon(big / 2, big / 2, r)
    d.line([*pts, pts[0], pts[1]], fill=line, width=width, joint="curve")
    n = r * _NODE
    d.ellipse((big / 2 - n, big / 2 - n, big / 2 + n, big / 2 + n), fill=node)
    return img.resize((size, size), Image.LANCZOS)
