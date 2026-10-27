#!/usr/bin/env python3
"""Generate the Axon app icons from one specification.

Every icon shares one grid (128 units), one tile (a superellipse with
continuous corners, 104 units wide, drawn at the same keyline), one stroke
weight and a single symbol. Each also gets a filled 16-unit symbolic
version, which GTK recolours to the text colour.

Outputs:
  theme/axon-icons/scalable/apps/<name>.svg
  theme/axon-icons/symbolic/apps/<name>-symbolic.svg

Usage:
  python3 scripts/gen_icons.py          write the files
  python3 scripts/gen_icons.py --check  exit 1 if any file is out of date
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "theme" / "axon-icons"
COLOUR_DIR = THEME / "scalable" / "apps"
SYMBOLIC_DIR = THEME / "symbolic" / "apps"

# Tile keyline on the 128 grid; the space below is left for the shadow.
TILE_X, TILE_Y, TILE = 12.0, 8.0, 104.0
CX, CY = 64.0, 60.0
STROKE = 7
SYMBOLIC_FILL = "#2e3436"

BLUE = "#1d64d8"
BLUE_LIGHT = "#7db3ff"
WHITE = "#ffffff"


def _n(v: float) -> str:
    """Compact number: at most two decimals, no trailing zeros."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _pts(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{_n(x)},{_n(y)}" for x, y in points)


def superellipse(x: float, y: float, size: float, n: float = 5.0, steps: int = 96) -> str:
    """Closed path of a superellipse: continuous corners, no visible tangent break."""
    a = size / 2
    cx, cy = x + a, y + a
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append(
            (
                cx + a * math.copysign(abs(c) ** (2 / n), c),
                cy + a * math.copysign(abs(s) ** (2 / n), s),
            )
        )
    return "M" + " L".join(f"{_n(px)},{_n(py)}" for px, py in pts) + " Z"


def hexagon(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """Pointy-top hexagon, the Axon mark."""
    return [
        (
            cx + r * math.cos(math.radians(-90 + 60 * k)),
            cy + r * math.sin(math.radians(-90 + 60 * k)),
        )
        for k in range(6)
    ]


def poly_path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{_n(x)},{_n(y)}" for x, y in points) + " Z"


def rrect(x: float, y: float, w: float, h: float, r: float, reverse: bool = False) -> str:
    """Rounded rectangle path; *reverse* runs it the other way (for holes)."""
    r = min(r, w / 2, h / 2)
    if not reverse:
        return (
            f"M{_n(x + r)},{_n(y)} H{_n(x + w - r)} A{_n(r)},{_n(r)} 0 0 1 {_n(x + w)},{_n(y + r)} "
            f"V{_n(y + h - r)} A{_n(r)},{_n(r)} 0 0 1 {_n(x + w - r)},{_n(y + h)} H{_n(x + r)} "
            f"A{_n(r)},{_n(r)} 0 0 1 {_n(x)},{_n(y + h - r)} V{_n(y + r)} "
            f"A{_n(r)},{_n(r)} 0 0 1 {_n(x + r)},{_n(y)} Z"
        )
    return (
        f"M{_n(x + r)},{_n(y)} A{_n(r)},{_n(r)} 0 0 0 {_n(x)},{_n(y + r)} V{_n(y + h - r)} "
        f"A{_n(r)},{_n(r)} 0 0 0 {_n(x + r)},{_n(y + h)} H{_n(x + w - r)} "
        f"A{_n(r)},{_n(r)} 0 0 0 {_n(x + w)},{_n(y + h - r)} V{_n(y + r)} "
        f"A{_n(r)},{_n(r)} 0 0 0 {_n(x + w - r)},{_n(y)} Z"
    )


def thick_polyline(points: list[tuple[float, float]], width: float) -> list[tuple[float, float]]:
    """Outline of a polyline stroked at *width*: mitred joins, square ends."""
    h = width / 2
    left, right = [], []
    n = len(points)
    for i, (px, py) in enumerate(points):
        normals = []
        for j in (i - 1, i):
            if 0 <= j < n - 1:
                (ax, ay), (bx, by) = points[j], points[j + 1]
                length = math.hypot(bx - ax, by - ay)
                normals.append((-(by - ay) / length, (bx - ax) / length))
        nx = sum(v[0] for v in normals)
        ny = sum(v[1] for v in normals)
        length = math.hypot(nx, ny)
        nx, ny = nx / length, ny / length
        # Miter length: half width over the cosine of half the turn angle
        cos_half = nx * normals[0][0] + ny * normals[0][1]
        m = h / max(cos_half, 0.3)
        left.append((px + nx * m, py + ny * m))
        right.append((px - nx * m, py - ny * m))
    # Square ends: push the first and last pairs out along the segment by half width
    for idx, (a, b) in ((0, (points[0], points[1])), (-1, (points[-1], points[-2]))):
        dx, dy = a[0] - b[0], a[1] - b[1]
        length = math.hypot(dx, dy)
        ex, ey = dx / length * h, dy / length * h
        left[idx] = (left[idx][0] + ex, left[idx][1] + ey)
        right[idx] = (right[idx][0] + ex, right[idx][1] + ey)
    return left + right[::-1]


def annular_sector(cx: float, cy: float, r_out: float, r_in: float, a0: float, a1: float) -> str:
    """Filled arc band from angle a0 to a1 (degrees, clockwise on screen)."""

    def p(r: float, a: float) -> str:
        return f"{_n(cx + r * math.cos(math.radians(a)))},{_n(cy + r * math.sin(math.radians(a)))}"

    large = 1 if (a1 - a0) % 360 > 180 else 0
    return (
        f"M{p(r_out, a0)} A{_n(r_out)},{_n(r_out)} 0 {large} 1 {p(r_out, a1)} "
        f"L{p(r_in, a1)} A{_n(r_in)},{_n(r_in)} 0 {large} 0 {p(r_in, a0)} Z"
    )


def arrowhead(cx: float, cy: float, r: float, a: float, size: float) -> list[tuple[float, float]]:
    """Triangle at angle *a* on a circle, pointing clockwise along the tangent."""
    px, py = cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))
    tx, ty = -math.sin(math.radians(a)), math.cos(math.radians(a))
    nx, ny = math.cos(math.radians(a)), math.sin(math.radians(a))
    return [
        (px + tx * size, py + ty * size),
        (px - tx * size * 0.5 + nx * size, py - ty * size * 0.5 + ny * size),
        (px - tx * size * 0.5 - nx * size, py - ty * size * 0.5 - ny * size),
    ]


def gear(cx: float, cy: float, r_out: float, r_in: float, teeth: int) -> list[tuple[float, float]]:
    pts = []
    step = 360 / teeth
    for k in range(teeth):
        a = k * step
        for da, r in (
            (-step * 0.32, r_in),
            (-step * 0.18, r_out),
            (step * 0.18, r_out),
            (step * 0.32, r_in),
        ):
            pts.append(
                (cx + r * math.cos(math.radians(a + da)), cy + r * math.sin(math.radians(a + da)))
            )
    return pts


def circle_path(cx: float, cy: float, r: float, reverse: bool = False) -> str:
    sweep = 0 if reverse else 1
    return (
        f"M{_n(cx - r)},{_n(cy)} A{_n(r)},{_n(r)} 0 1 {sweep} {_n(cx + r)},{_n(cy)} "
        f"A{_n(r)},{_n(r)} 0 1 {sweep} {_n(cx - r)},{_n(cy)} Z"
    )


def bubble(x: float, y: float, w: float, h: float, r: float, tail: float) -> str:
    """Speech bubble path with a tail at the lower left."""
    t0 = x + r + tail * 0.9
    return (
        f"M{_n(x + r)},{_n(y)} H{_n(x + w - r)} A{_n(r)},{_n(r)} 0 0 1 {_n(x + w)},{_n(y + r)} "
        f"V{_n(y + h - r)} A{_n(r)},{_n(r)} 0 0 1 {_n(x + w - r)},{_n(y + h)} H{_n(t0)} "
        f"L{_n(x + r)},{_n(y + h + tail)} V{_n(y + h)} "
        f"A{_n(r)},{_n(r)} 0 0 1 {_n(x)},{_n(y + h - r)} V{_n(y + r)} "
        f"A{_n(r)},{_n(r)} 0 0 1 {_n(x + r)},{_n(y)} Z"
    )


# ── Colour icons ────────────────────────────────────────────────────────────


def _stroke(colour: str, width: float = STROKE) -> str:
    return (
        f'fill="none" stroke="{colour}" stroke-width="{_n(width)}" '
        'stroke-linecap="round" stroke-linejoin="round"'
    )


def glyph_mark(colour_line: str, colour_dot: str) -> str:
    return (
        f'<path d="{poly_path(hexagon(CX, CY, 30))}" {_stroke(colour_line)}/>'
        f'<circle cx="{_n(CX)}" cy="{_n(CY)}" r="8.5" fill="{colour_dot}"/>'
    )


GLYPHS = {
    "axon-os": glyph_mark(BLUE_LIGHT, WHITE),
    "axon-welcome": glyph_mark(BLUE, BLUE),
    "axon-ai-panel": (
        f'<path d="{bubble(34, 33, 60, 46, 14, 12)}" {_stroke(WHITE)}/>'
        f'<path d="{poly_path(hexagon(64, 56, 9))}" fill="{WHITE}"/>'
    ),
    "axon-intent-bar": (
        f'<rect x="26" y="43" width="76" height="34" rx="17" {_stroke(WHITE, 6)}/>'
        f'<circle cx="45" cy="58.5" r="6.5" {_stroke(WHITE, 5)}/>'
        f'<line x1="50" y1="63.5" x2="54" y2="67.5" {_stroke(WHITE, 5)}/>'
        f'<line x1="66" y1="52" x2="66" y2="68" {_stroke(BLUE_LIGHT, 5)}/>'
    ),
    "axon-files": (
        '<path d="M30,40 A6,6 0 0 1 36,34 H52 L60,42 H92 A6,6 0 0 1 98,48 V80 '
        'A6,6 0 0 1 92,86 H36 A6,6 0 0 1 30,80 Z" fill="#ffffff" fill-opacity=".62"/>'
        '<rect x="30" y="50" width="68" height="36" rx="6" fill="#ffffff"/>'
    ),
    "axon-terminal": (
        f'<polyline points="{_pts([(40, 44), (56, 60), (40, 76)])}" {_stroke(WHITE, 8)}/>'
        f'<line x1="63" y1="78" x2="87" y2="78" {_stroke("#9a9a9a", 8)}/>'
    ),
    "axon-settings": (
        f'<path d="{poly_path(gear(CX, CY, 31, 23.5, 8))} {circle_path(CX, CY, 9.5, True)}" '
        'fill="#3a3d42" fill-rule="evenodd"/>'
    ),
    "axon-installer": (
        f'<path d="M34,64 V76 A8,8 0 0 0 42,84 H86 A8,8 0 0 0 94,76 V64" {_stroke(WHITE)}/>'
        f'<line x1="64" y1="32" x2="64" y2="64" {_stroke(BLUE_LIGHT)}/>'
        f'<polyline points="{_pts([(52, 53), (64, 65), (76, 53)])}" {_stroke(BLUE_LIGHT)}/>'
    ),
    "axon-updater": (
        f'<path d="M{_n(CX + 24 * math.cos(math.radians(200)))},{_n(CY + 24 * math.sin(math.radians(200)))} '
        f'A24,24 0 0 1 {_n(CX + 24 * math.cos(math.radians(325)))},{_n(CY + 24 * math.sin(math.radians(325)))}" '
        f"{_stroke(WHITE)}/>"
        f'<path d="M{_n(CX + 24 * math.cos(math.radians(20)))},{_n(CY + 24 * math.sin(math.radians(20)))} '
        f'A24,24 0 0 1 {_n(CX + 24 * math.cos(math.radians(145)))},{_n(CY + 24 * math.sin(math.radians(145)))}" '
        f"{_stroke(WHITE)}/>"
        f'<path d="{poly_path(arrowhead(CX, CY, 24, 330, 9))}" fill="{WHITE}" stroke="{WHITE}" '
        'stroke-width="2" stroke-linejoin="round"/>'
        f'<path d="{poly_path(arrowhead(CX, CY, 24, 150, 9))}" fill="{WHITE}" stroke="{WHITE}" '
        'stroke-width="2" stroke-linejoin="round"/>'
    ),
    "axon-windows-app": (
        f'<rect x="28" y="32" width="72" height="56" rx="9" {_stroke(WHITE, 6)}/>'
        f'<line x1="31" y1="46" x2="97" y2="46" {_stroke(WHITE, 5)}/>'
        f'<path d="M57,56 L57,78 L76,67 Z" fill="{WHITE}" stroke="{WHITE}" stroke-width="3" '
        'stroke-linejoin="round"/>'
    ),
    "axon-shortcuts": (
        f'<rect x="26" y="37" width="76" height="46" rx="10" {_stroke(WHITE, 6)}/>'
        + "".join(
            f'<rect x="{_n(37 + 11.5 * i)}" y="47" width="7" height="7" rx="2" fill="{WHITE}"/>'
            for i in range(5)
        )
        + "".join(
            f'<rect x="{_n(42.75 + 11.5 * i)}" y="57.5" width="7" height="7" rx="2" fill="{WHITE}"/>'
            for i in range(4)
        )
        + f'<rect x="48" y="68" width="32" height="6" rx="3" fill="{BLUE_LIGHT}"/>'
    ),
}

# (top, bottom) of each tile's vertical tone step, and whether the tile is light
TILES = {
    "axon-os": ("#333333", "#1c1c1c", False),
    "axon-welcome": ("#ffffff", "#eceef1", True),
    "axon-ai-panel": ("#3d84f0", "#1f5fd0", False),
    "axon-intent-bar": ("#60646b", "#3f4248", False),
    "axon-files": ("#f6c158", "#e49d1f", False),
    "axon-terminal": ("#2c2c2c", "#121212", False),
    "axon-settings": ("#eceef0", "#c8cbd0", True),
    "axon-installer": ("#3a3a3a", "#222222", False),
    "axon-updater": ("#43bf80", "#25915a", False),
    "axon-windows-app": ("#687a8e", "#47566a", False),
    "axon-shortcuts": ("#f2766f", "#d34d46", False),
}

TITLES = {
    "axon-os": "Axon OS",
    "axon-welcome": "Welcome to Axon OS",
    "axon-ai-panel": "Axon AI Panel",
    "axon-intent-bar": "Axon Intent Bar",
    "axon-files": "Axon Files",
    "axon-terminal": "Axon Terminal",
    "axon-settings": "Axon Settings",
    "axon-installer": "Install Axon OS",
    "axon-updater": "Axon Update",
    "axon-windows-app": "Windows program",
    "axon-shortcuts": "Axon Shortcuts",
}


def colour_svg(name: str) -> str:
    top, bottom, light = TILES[name]
    tile = superellipse(TILE_X, TILE_Y, TILE)
    edge = (
        '<path d="{d}" fill="none" stroke="#000000" stroke-opacity=".09" stroke-width="1"/>'
        if light
        else '<path d="{d}" fill="none" stroke="#ffffff" stroke-opacity=".10" stroke-width="1"/>'
    ).format(d=tile)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">'
        f"<title>{TITLES[name]}</title>"
        "<defs>"
        '<linearGradient id="tone" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/>'
        "</linearGradient>"
        '<filter id="lift" x="-20%" y="-20%" width="140%" height="150%">'
        '<feDropShadow dx="0" dy="1.5" stdDeviation="1.5" flood-opacity=".22"/>'
        '<feDropShadow dx="0" dy="6" stdDeviation="5" flood-opacity=".14"/>'
        "</filter>"
        "</defs>"
        f'<path d="{tile}" fill="url(#tone)" filter="url(#lift)"/>'
        f"{edge}{GLYPHS[name]}</svg>\n"
    )


# ── Symbolic icons (16 grid, filled shapes only so GTK can recolour them) ───


def _sym(paths: list[str]) -> str:
    body = "".join(f'<path d="{d}" fill="{SYMBOLIC_FILL}" fill-rule="evenodd"/>' for d in paths)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        f"{body}</svg>\n"
    )


def _hex_ring(cx: float, cy: float, r_out: float, r_in: float) -> str:
    return poly_path(hexagon(cx, cy, r_out)) + " " + poly_path(hexagon(cx, cy, r_in)[::-1])


SYMBOLIC = {
    "axon-os": [_hex_ring(8, 8, 7.2, 5.0), circle_path(8, 8, 1.9)],
    "axon-welcome": [_hex_ring(8, 8, 7.2, 5.0), circle_path(8, 8, 1.9)],
    "axon-ai-panel": [
        bubble(1, 1.5, 14, 10, 3, 3) + " " + rrect(2.6, 3.1, 10.8, 6.8, 1.6, reverse=True),
        poly_path(hexagon(8, 6.5, 2.1)),
    ],
    "axon-intent-bar": [
        rrect(0.5, 3.5, 15, 9, 4.5) + " " + rrect(2, 5, 12, 6, 3, reverse=True),
        circle_path(5.3, 7.6, 1.9) + " " + circle_path(5.3, 7.6, 1.0, True),
        poly_path(thick_polyline([(6.6, 8.9), (7.6, 9.9)], 1.1)),
        rrect(9.6, 6, 1.4, 4, 0.5),
    ],
    "axon-files": [
        "M1,4 A1.5,1.5 0 0 1 2.5,2.5 H6 L7.5,4 H13.5 A1.5,1.5 0 0 1 15,5.5 V12 "
        "A1.5,1.5 0 0 1 13.5,13.5 H2.5 A1.5,1.5 0 0 1 1,12 Z"
    ],
    "axon-terminal": [
        poly_path(thick_polyline([(2.5, 3.5), (6.5, 7.5), (2.5, 11.5)], 1.9)),
        rrect(8, 11, 6, 1.9, 0.5),
    ],
    "axon-settings": [poly_path(gear(8, 8, 7.4, 5.6, 8)) + " " + circle_path(8, 8, 2.3, True)],
    "axon-installer": [
        "M1.5,9 H3.3 V12.2 H12.7 V9 H14.5 V12.5 A1.5,1.5 0 0 1 13,14 H3 A1.5,1.5 0 0 1 1.5,12.5 Z",
        poly_path(thick_polyline([(8, 1.5), (8, 9.5)], 1.9)),
        poly_path(thick_polyline([(4.8, 6.6), (8, 9.8), (11.2, 6.6)], 1.9)),
    ],
    "axon-updater": [
        annular_sector(8, 8, 6.9, 5.1, 200, 318),
        annular_sector(8, 8, 6.9, 5.1, 20, 138),
        poly_path(arrowhead(8, 8, 6, 330, 2.9)),
        poly_path(arrowhead(8, 8, 6, 150, 2.9)),
    ],
    "axon-windows-app": [
        rrect(1, 2, 14, 12, 2) + " " + rrect(2.6, 5.4, 10.8, 7, 0.8, reverse=True),
        "M6.5,6.8 L6.5,11.2 L10.3,9 Z",
    ],
    "axon-shortcuts": [
        rrect(0.5, 3, 15, 10, 2.2) + " " + rrect(2, 4.5, 12, 7, 1.2, reverse=True),
        *(rrect(3.2 + 2.6 * i, 5.6, 1.6, 1.6, 0.4) for i in range(4)),
        rrect(5, 8.6, 6, 1.6, 0.6),
    ],
}

ICONS = tuple(TILES)


def generate() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for name in ICONS:
        out[COLOUR_DIR / f"{name}.svg"] = colour_svg(name)
        out[SYMBOLIC_DIR / f"{name}-symbolic.svg"] = _sym(SYMBOLIC[name])
    return out


def stale(files: dict[Path, str]) -> list[Path]:
    return [p for p, t in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != t]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if outputs are out of date")
    args = parser.parse_args(argv)
    files = generate()
    out_of_date = stale(files)
    if args.check:
        for path in out_of_date:
            sys.stdout.write(f"out of date: {path.relative_to(ROOT)}\n")
        return 1 if out_of_date else 0
    for path in out_of_date:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[path], encoding="utf-8")
        sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
