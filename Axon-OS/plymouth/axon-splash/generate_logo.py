#!/usr/bin/env python3
"""
Generate axon.png — a 400x400 logo for the Axon OS Plymouth splash.

Background: #0e0e10  (14, 14, 16)
Circle:     #a78bfa  (167, 139, 250)  radius 60 px, centered
"""

import os
import struct
import zlib

WIDTH  = 400
HEIGHT = 400
BG     = (14,  14,  16)   # dark near-black
FG     = (167, 139, 250)  # violet #a78bfa
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
        WIDTH,   # width
        HEIGHT,  # height
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

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "axon.png")
with open(out_path, "wb") as f:
    f.write(_make_png(pixels))

print(f"Written: {out_path}  ({WIDTH}x{HEIGHT} px)")
