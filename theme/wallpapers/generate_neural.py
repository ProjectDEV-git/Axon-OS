#!/usr/bin/env python3
"""Generate the default Axon OS wallpapers: axon-neural-dark.png and -light.png.

The idea comes from the name: an axon is the long fibre a nerve cell sends
its signal along. Sparse flow lines drift across a neutral graphite (or
paper) ground and gather at three nodes, drawn in the system blue. It is
quiet on purpose: icons and windows sit on top of it all day.

Deterministic (fixed seed), rendered at 2x and downsampled for smooth lines.
Needs Pillow and numpy. Run and commit the output:

    python3 theme/wallpapers/generate_neural.py
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
W, H = 2560, 1440
SCALE = 2
SEED = 1987

VARIANTS = {
    "dark": {
        "ground": (18, 18, 19),
        "light": (34, 36, 40),  # a soft pool of light behind the nodes
        "line": (93, 157, 255),
        "line_alpha": (14, 52),
        "node": (125, 179, 255),
    },
    "light": {
        "ground": (240, 240, 241),
        "light": (252, 252, 253),
        "line": (29, 100, 216),
        "line_alpha": (8, 34),
        "node": (24, 88, 194),
    },
}

# The nodes the fibres gather at, as fractions of the screen; kept off the
# centre so a window there does not hide the whole idea.
NODES = [(0.70, 0.36, 1.0), (0.83, 0.62, 0.7), (0.58, 0.70, 0.55)]


def flow_angle(x: float, y: float) -> float:
    """A smooth direction field: a gentle drift to the right, bent by waves
    and pulled toward the nodes."""
    u, v = x / W, y / H
    a = 0.18 * math.sin(2.1 * u + 1.3) + 0.22 * math.sin(3.7 * v + 2.0 * u) + 0.08 * math.sin(9 * u)
    for nx, ny, w in NODES:
        dx, dy = nx - u, ny - v
        d2 = dx * dx + dy * dy + 0.004
        a += w * 0.010 * math.atan2(dy, dx) / d2 * 0.9 * math.exp(-d2 * 6)
    return a


def trace(x: float, y: float, steps: int, step: float) -> list[tuple[float, float]]:
    pts = [(x, y)]
    prev = None
    for _ in range(steps):
        a = flow_angle(x, y)
        # Ignore sudden turns (the field gets steep near a node)
        if prev is not None and abs(math.remainder(a - prev, math.tau)) > 0.35:
            a = prev + math.copysign(0.35, math.remainder(a - prev, math.tau))
        prev = a
        x += math.cos(a) * step
        y += math.sin(a) * step
        if not (-200 < x < W + 200 and -200 < y < H + 200):
            break
        # A fibre that reaches a node ends there, like an axon at a synapse
        for nx, ny, _w in NODES:
            if math.hypot(x - nx * W, y - ny * H) < 26:
                pts.append((nx * W, ny * H))
                return pts
        pts.append((x, y))
    return pts


def ground(colours: dict) -> Image.Image:
    """Flat ground with one very soft pool of light around the main node."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nx, ny, _ = NODES[0]
    d = np.sqrt(((xx / W - nx) * 1.6) ** 2 + (yy / H - ny) ** 2)
    t = np.clip(1 - d / 0.75, 0, 1) ** 2
    g = np.array(colours["ground"], np.float32)
    lt = np.array(colours["light"], np.float32)
    img = g + (lt - g) * t[..., None]
    # A whisper of grain so the soft light does not band on 8-bit panels
    rng = np.random.default_rng(SEED)
    img += rng.normal(0, 0.9, img.shape[:2])[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def render(name: str, colours: dict) -> Image.Image:
    rnd = random.Random(SEED)
    base = ground(colours).resize((W * SCALE, H * SCALE), Image.BICUBIC).convert("RGBA")
    lines = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(lines)
    lo, hi = colours["line_alpha"]
    for _ in range(150):
        # Start on the left two thirds so the fibres travel into the nodes
        x, y = rnd.uniform(-150, W * 0.65), rnd.uniform(-60, H + 60)
        pts = trace(x, y, steps=420, step=7)
        if len(pts) < 40:
            continue
        alpha = rnd.uniform(lo, hi)
        width = rnd.choice((1.0, 1.0, 1.4, 2.0)) * SCALE
        # Fade each fibre in and out along its length
        chunk = 12
        for i in range(0, len(pts) - 1, chunk):
            seg = pts[i : i + chunk + 1]
            t = i / len(pts)
            fade = math.sin(math.pi * min(max(t, 0.0), 1.0))
            a = int(alpha * fade)
            if a <= 0 or len(seg) < 2:
                continue
            draw.line(
                [(px * SCALE, py * SCALE) for px, py in seg],
                fill=(*colours["line"], a),
                width=int(width),
                joint="curve",
            )
    out = Image.alpha_composite(base, lines)

    # Nodes: a small solid core with a soft halo, sized by weight
    halo = Image.new("RGBA", base.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    core = Image.new("RGBA", base.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    for nx, ny, w in NODES:
        cx, cy = nx * W * SCALE, ny * H * SCALE
        r = 46 * w * SCALE
        hd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*colours["node"], int(70 * w)))
        rc = 7 * (0.6 + 0.4 * w) * SCALE
        cd.ellipse((cx - rc, cy - rc, cx + rc, cy + rc), fill=(*colours["node"], 235))
    halo = halo.filter(ImageFilter.GaussianBlur(40 * SCALE))
    out = Image.alpha_composite(out, halo)
    out = Image.alpha_composite(out, core)
    return out.convert("RGB").resize((W, H), Image.LANCZOS)


def main() -> None:
    for name, colours in VARIANTS.items():
        path = HERE / f"axon-neural-{name}.png"
        render(name, colours).save(path, optimize=True)
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()
