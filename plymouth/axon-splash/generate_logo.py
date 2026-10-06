#!/usr/bin/env python3
"""
Generate the Axon OS Plymouth splash images.

axon.png — 400x400 logo.
    Background: #0e0e10  (14, 14, 16)
    Circle:     #60a5fa  (96, 165, 250)  radius 60 px, centered
progress-track.png / progress-fill.png — 1x1 solid colours that axon.script
    scales into the progress bar (Plymouth's script API can only draw images).
"""

import os
import struct
import zlib

WIDTH  = 400
HEIGHT = 400
BG     = (14,  14,  16)   # dark near-black
FG     = (96, 165, 250)  # sky blue #60a5fa
RADIUS = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Pack a PNG chunk: length + type + data + CRC."""
    length = struct.pack(">I", len(data))
    crc    = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    return length + chunk_type + data + crc


def _make_png(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """Encode pixel data (list of rows of RGB triples) as a minimal PNG."""
    signature = b"\x89PNG\r\n\x1a\n"

    # IHDR
    ihdr_data = struct.pack(
        ">IIBBBBB",
        len(pixels[0]),  # width
        len(pixels),     # height
        8,       # bit depth
        2,       # color type: RGB
        0,       # compression method
        0,       # filter method
        0,       # interlace method
    )
    ihdr = _chunk(b"IHDR", ihdr_data)

    # IDAT — filter byte 0 (None) prepended to each row
    raw_rows = bytearray()
    for row in pixels:
        raw_rows.append(0)  # filter type: None
        for r, g, b in row:
            raw_rows += bytes([r, g, b])

    compressed = zlib.compress(bytes(raw_rows), level=9)
    idat = _chunk(b"IDAT", compressed)

    # IEND
    iend = _chunk(b"IEND", b"")

    return signature + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Build pixel grid
# ---------------------------------------------------------------------------

cx = WIDTH  // 2
cy = HEIGHT // 2

pixels: list[list[tuple[int, int, int]]] = []
for y in range(HEIGHT):
    row: list[tuple[int, int, int]] = []
    for x in range(WIDTH):
        dx = x - cx
        dy = y - cy
        if dx * dx + dy * dy <= RADIUS * RADIUS:
            row.append(FG)
        else:
            row.append(BG)
    pixels.append(row)

# ---------------------------------------------------------------------------
# Write PNG
# ---------------------------------------------------------------------------

out_dir = os.path.dirname(os.path.abspath(__file__))
out_path = os.path.join(out_dir, "axon.png")
with open(out_path, "wb") as f:
    f.write(_make_png(pixels))

# Progress bar: track #1e1e23, fill #a78bfa (violet)
for name, colour in (("progress-track.png", (30, 30, 35)), ("progress-fill.png", (167, 139, 250))):
    with open(os.path.join(out_dir, name), "wb") as f:
        f.write(_make_png([[colour]]))

from axon_logger import configure_app_logger

logger = configure_app_logger(__name__)
logger.info("Written: %s  (%sx%s px)", out_path, WIDTH, HEIGHT)
