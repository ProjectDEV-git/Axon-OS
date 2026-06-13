#!/usr/bin/env python3
import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
THEME_DIR = os.path.join(HERE, "axon")
ICONS_DIR = os.path.join(THEME_DIR, "icons")

os.makedirs(THEME_DIR, exist_ok=True)
os.makedirs(ICONS_DIR, exist_ok=True)

# 1. Generate selection box (24x24 px, rounded corners, electric blue border)
def draw_rounded_rect(draw, x0, y0, x1, y1, r, fill, outline, width=1):
    # draw corners
    draw.ellipse([x0, y0, x0 + 2*r, y0 + 2*r], fill=fill, outline=outline, width=width)
    draw.ellipse([x1 - 2*r, y0, x1, y0 + 2*r], fill=fill, outline=outline, width=width)
    draw.ellipse([x0, y1 - 2*r, x0 + 2*r, y1], fill=fill, outline=outline, width=width)
    draw.ellipse([x1 - 2*r, y1 - 2*r, x1, y1], fill=fill, outline=outline, width=width)

    # fill inner areas
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)

    # draw border lines
    draw.line([x0 + r, y0, x1 - r, y0], fill=outline, width=width)
    draw.line([x0 + r, y1, x1 - r, y1], fill=outline, width=width)
    draw.line([x0, y0 + r, x0, y1 - r], fill=outline, width=width)
    draw.line([x1, y0 + r, x1, y1 - r], fill=outline, width=width)

# Create 24x24 canvas for selection box
canvas = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
draw = ImageDraw.Draw(canvas)

# Fill: dark grey (38, 38, 38, 255)
# Border: electric blue (59, 130, 246, 255)
draw_rounded_rect(draw, 0, 0, 23, 23, 5, (38, 38, 38, 255), (59, 130, 246, 255), width=1)

# Slice selection box into 9-slice images (8x8 px each)
slices = {
    "nw": (0, 0, 8, 8),
    "n": (8, 0, 16, 8),
    "ne": (16, 0, 24, 8),
    "w": (0, 8, 8, 16),
    "c": (8, 8, 16, 16),
    "e": (16, 8, 24, 16),
    "sw": (0, 16, 8, 24),
    "s": (8, 16, 16, 24),
    "se": (16, 16, 24, 24),
}

for name, box in slices.items():
    cropped = canvas.crop(box)
    cropped.save(os.path.join(THEME_DIR, f"select_{name}.png"))

print("Selection slices generated.")

# 2. Generate custom menu icons (24x24 px, white, transparent BG)
def create_icon_canvas():
    return Image.new("RGBA", (24, 24), (0, 0, 0, 0))

def draw_hexagon(draw, center, radius, fill=None, outline=None, width=1):
    cx, cy = center
    pts = [
        (cx + radius * math.cos(i * math.pi / 3),
         cy + radius * math.sin(i * math.pi / 3))
        for i in range(6)
    ]
    if fill:
        draw.polygon(pts, fill=fill)
    if outline:
        draw.polygon(pts, outline=outline, width=width)

# A. Axon OS Logo Icon (axonos.png)
img_axon = create_icon_canvas()
draw_axon = ImageDraw.Draw(img_axon)
draw_hexagon(draw_axon, (12, 12), 8, outline=(255, 255, 255, 255), width=2)
draw_hexagon(draw_axon, (12, 12), 4.5, fill=(255, 255, 255, 255))
draw_axon.ellipse([10.5, 10.5, 13.5, 13.5], fill=(0, 0, 0, 0)) # transparent inner core hole
img_axon.save(os.path.join(ICONS_DIR, "axonos.png"))

# B. Safe Graphics Icon (safe.png)
img_safe = create_icon_canvas()
draw_safe = ImageDraw.Draw(img_safe)
draw_safe.rectangle([3, 4, 20, 15], outline=(255, 255, 255, 255), width=2)
draw_safe.line([12, 15, 12, 19], fill=(255, 255, 255, 255), width=2)
draw_safe.line([8, 19, 16, 19], fill=(255, 255, 255, 255), width=2)
img_safe.save(os.path.join(ICONS_DIR, "safe.png"))

# C. Nvidia GPU Icon (nvidia.png)
img_nvidia = create_icon_canvas()
draw_nvidia = ImageDraw.Draw(img_nvidia)
draw_nvidia.arc([2, 5, 21, 18], start=180, end=360, fill=(255, 255, 255, 255), width=2)
draw_nvidia.arc([2, 5, 21, 18], start=0, end=180, fill=(255, 255, 255, 255), width=2)
draw_nvidia.ellipse([10, 10, 13, 13], fill=(255, 255, 255, 255))
img_nvidia.save(os.path.join(ICONS_DIR, "nvidia.png"))

# D. Power Off Icon (power.png)
img_power = create_icon_canvas()
draw_power = ImageDraw.Draw(img_power)
draw_power.arc([4, 4, 19, 19], start=315, end=225, fill=(255, 255, 255, 255), width=2)
draw_power.line([12, 2, 12, 10], fill=(255, 255, 255, 255), width=2)
img_power.save(os.path.join(ICONS_DIR, "power.png"))

print("Menu icons generated.")

# 3. Write theme.txt file
theme_txt_content = """# Axon OS Zorin-Style GRUB Theme
title-text: ""
desktop-image: ""
desktop-color: "#000000"
terminal-box: ""
terminal-font: "Sans 12"

+ boot_menu {
  left = 20%
  top = 30%
  width = 60%
  height = 50%
  item_font = "DejaVu Sans Regular 14"
  selected_item_font = "DejaVu Sans Bold 14"
  item_color = "#999999"
  selected_item_color = "#ffffff"
  item_height = 44
  item_padding = 8
  item_spacing = 16
  selected_item_pixmap_style = "select_*.png"
  icon_width = 24
  icon_height = 24
  item_icon_space = 16
}

+ label {
  left = 20%
  top = 90%
  width = 60%
  align = "center"
  color = "#666666"
  font = "DejaVu Sans Regular 11"
  text = "[Enter] Boot   [E] Edit   [C] Command Line"
}
"""

with open(os.path.join(THEME_DIR, "theme.txt"), "w") as f:
    f.write(theme_txt_content)

print("theme.txt written successfully.")
