#!/usr/bin/env python3
"""
Axon OS — Futuristic Desktop Preview Generator
1920 × 1080 · aurora wallpaper · glassmorphic windows · neon dock
"""

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance
import math, os, random

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H = 1920, 1080
OUT = os.path.join(os.path.dirname(__file__), "desktop-preview.png")

# ── Colour palette ────────────────────────────────────────────────────────────
P = {
    # backgrounds
    "bg":          (4,   4,  12),
    "panel":       (10,  10,  22),
    "win_bg":      (12,  12,  24),
    "win_title":   (15,  15,  28),
    "bar":         (8,   8,  18),
    "bar_border":  (24,  24,  44),
    # accents
    "violet":      (139,  92, 246),
    "violet_dim":  (80,   50, 180),
    "cyan":        (34,  211, 238),
    "cyan_dim":    (20,  130, 160),
    "aurora1":     (50,  255, 180),
    "aurora2":     (80,  200, 255),
    "aurora3":     (180,  80, 255),
    "pink":        (255,  80, 200),
    "orange":      (255, 160,  50),
    # text
    "text":        (228, 228, 244),
    "text_dim":    (110, 110, 150),
    "text_ghost":  (60,   60,  90),
    # traffic lights
    "red":         (255,  95,  87),
    "yellow":      (255, 189,  46),
    "green_btn":   (40,  200,  65),
    # code colours
    "ck":          (139,  92, 246),
    "cs":          (74,  222, 128),
    "cf":          (96,  165, 250),
    "cc":          (75,  100, 125),
    "cn":          (251, 191,  36),
    "co":          (248, 113, 113),
    # ui helpers
    "chip":        (22,  22,  40),
    "chip_border": (45,  45,  75),
    "scrollbar":   (35,  35,  60),
    "dock_bg":     (14,  14,  26),
    "sep":         (35,  35,  58),
}

RNG = random.Random(42)


# ── Font loader ───────────────────────────────────────────────────────────────
def _font(size, bold=False):
    for d, f in [
        ("/usr/share/fonts/truetype/inter",      "Inter-{}.ttf"),
        ("/usr/share/fonts/opentype/inter",       "Inter-{}.otf"),
        ("/usr/share/fonts/truetype/dejavu",      "DejaVuSans{}.ttf"),
        ("/usr/share/fonts/truetype/liberation",  "LiberationSans-{}.ttf"),
        ("/usr/share/fonts/truetype/ubuntu",      "Ubuntu-{}.ttf"),
    ]:
        variant = "Bold" if bold else "Regular"
        path = os.path.join(d, f.format(variant))
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def _mono(size, bold=False):
    for d, f in [
        ("/usr/share/fonts/truetype/liberation", "LiberationMono-{}.ttf"),
        ("/usr/share/fonts/truetype/dejavu",     "DejaVuSansMono{}.ttf"),
    ]:
        variant = "Bold" if bold else "Regular"
        path = os.path.join(d, f.format(variant))
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return _font(size, bold)


# ── Drawing helpers ───────────────────────────────────────────────────────────
def rr(draw, xy, r, fill=None, outline=None, w=1):
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=w)

def circle(draw, cx, cy, r, fill, outline=None, ow=1):
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=fill, outline=outline, width=ow)

def tcx(draw, text, cx, y, font, fill):
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bb[2]-bb[0])//2, y), text, font=font, fill=fill)

def trx(draw, text, rx, y, font, fill):
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((rx - (bb[2]-bb[0]), y), text, font=font, fill=fill)

def tw(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]

def hexagon(draw, cx, cy, size, fill):
    pts = [(cx + size*math.cos(math.radians(60*i-30)),
            cy + size*math.sin(math.radians(60*i-30))) for i in range(6)]
    draw.polygon(pts, fill=fill)

def glow_layer(size, cx, cy, rx, ry, color, steps=60, peak_alpha=80):
    layer = Image.new("RGBA", size, (0,0,0,0))
    d = ImageDraw.Draw(layer)
    for i in range(steps, 0, -1):
        a = int(peak_alpha * (1 - i/steps)**2.0)
        d.ellipse([cx-int(rx*i/steps), cy-int(ry*i/steps),
                   cx+int(rx*i/steps), cy+int(ry*i/steps)],
                  fill=(*color, a))
    return layer

def shadow_rr(img, x, y, w, h, r, depth=20):
    for i in range(depth, 0, -1):
        a = int(90 * (i/depth)**1.8)
        sl = Image.new("RGBA", img.size, (0,0,0,0))
        sd = ImageDraw.Draw(sl)
        sd.rounded_rectangle([x+i, y+i, x+w+i, y+h+i], radius=r, fill=(0,0,0,a))
        img.paste(sl, (0,0), sl)


# ══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND — aurora nebula
# ══════════════════════════════════════════════════════════════════════════════
def draw_background(img):
    draw = ImageDraw.Draw(img)

    # 1. deep-space vertical gradient
    for y in range(H):
        t = y / H
        r = int(4  + t * 8)
        g = int(4  + t * 5)
        b = int(12 + t * 20)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # 2. nebula glow clouds
    for cx, cy, rx, ry, col, alpha in [
        (960, 700, 700, 300, (90,  30, 180), 55),
        (300, 500, 400, 200, (20,  80, 160), 35),
        (1600,400, 350, 180, (160, 30, 120), 30),
        (960, 900, 500, 150, (0,  150, 130), 25),
    ]:
        gl = glow_layer(img.size, cx, cy, rx, ry, col, steps=80, peak_alpha=alpha)
        img.paste(gl, (0, 0), gl)

    # 3. aurora bands
    aurora = Image.new("RGBA", img.size, (0,0,0,0))
    ad = ImageDraw.Draw(aurora)
    band_configs = [
        (680, 55, 0.0035, 0.0,   P["aurora1"], 30, 70),
        (720, 40, 0.0028, 1.2,   P["aurora2"], 22, 55),
        (760, 30, 0.0042, 2.5,   P["aurora3"], 18, 45),
        (640, 25, 0.0020, 0.8,   P["cyan"],    14, 35),
        (800, 20, 0.0050, 3.1,   P["aurora1"], 12, 28),
    ]
    for yb, amp, freq, phase, col, thick, max_a in band_configs:
        for x in range(0, W, 2):
            y_center = int(yb + amp * math.sin(freq * x + phase))
            for dy in range(-thick, thick+1):
                falloff = 1 - abs(dy)/thick
                a = int(max_a * falloff**2.2)
                y_px = y_center + dy
                if 0 <= y_px < H:
                    ad.point((x, y_px), fill=(*col, a))
    img.paste(aurora, (0, 0), aurora)

    # 4. star field
    for _ in range(320):
        sx = RNG.randint(0, W-1)
        sy = RNG.randint(30, H - 120)
        a  = RNG.randint(50, 200)
        sz = RNG.choice([1,1,1,1,2,2,3])
        brightness = RNG.randint(180, 255)
        r_c = RNG.randint(150, brightness)
        g_c = RNG.randint(170, brightness)
        draw.ellipse([sx, sy, sx+sz, sy+sz], fill=(r_c, g_c, 255, a))

    # 5. scan-line overlay
    scan = Image.new("RGBA", img.size, (0,0,0,0))
    sd = ImageDraw.Draw(scan)
    for y in range(0, H, 4):
        sd.line([(0, y), (W, y)], fill=(0,0,0,14))
    img.paste(scan, (0,0), scan)

    # 6. vignette
    vig = Image.new("RGBA", img.size, (0,0,0,0))
    vd = ImageDraw.Draw(vig)
    for i in range(200, 0, -2):
        a = int(80 * (1 - i/200)**2.5)
        vd.rectangle([i, i, W-i, H-i], outline=(0,0,0,a), width=2)
    img.paste(vig, (0,0), vig)


# ══════════════════════════════════════════════════════════════════════════════
#  MENU BAR
# ══════════════════════════════════════════════════════════════════════════════
def draw_menubar(img):
    bar_layer = Image.new("RGBA", img.size, (0,0,0,0))
    bd = ImageDraw.Draw(bar_layer)
    bd.rectangle([0, 0, W, 30], fill=(*P["bar"], 220))
    bd.line([(0, 30), (W, 30)], fill=(*P["bar_border"], 200))
    bd.line([(0, 0), (W, 0)], fill=(255,255,255,12))
    img.paste(bar_layer, (0,0), bar_layer)
    draw = ImageDraw.Draw(img)

    f12 = _font(12); f13b = _font(13, True); f11 = _font(11)

    hexagon(draw, 16, 15, 7, P["violet"])
    draw.text((28, 7), "Axon OS", font=f13b, fill=P["text"])

    x = 100
    for item in ["File", "Edit", "View", "Go", "Window", "Help"]:
        draw.text((x, 8), item, font=f12, fill=(190,190,210))
        x += tw(draw, item, f12) + 22

    clock = "Mon 09 Jun   10:34 AM"
    trx(draw, clock, W-12, 8, f12, (210,210,225))
    rx = W - 12 - tw(draw, clock, f12) - 20

    ai_text = "⬡ Axon AI"
    draw.text((rx - tw(draw, ai_text, f13b) - 14, 7), ai_text, font=f13b, fill=P["violet"])
    circle(draw, rx-4, 15, 4, P["aurora1"])
    rx -= tw(draw, ai_text, f13b) + 34

    draw.rounded_rectangle([rx-36, 9, rx-4, 21], radius=3, outline=(170,170,195), width=1)
    draw.rectangle([rx-34, 11, rx-34+24, 19], fill=P["aurora1"])
    draw.rectangle([rx-3, 12, rx, 18], fill=(170,170,195))
    rx -= 56

    wc = rx - 10
    for sz in [3, 6, 9]:
        draw.arc([wc-sz, 20-sz, wc+sz, 20+sz], start=200, end=340,
                 fill=(200,200,220), width=1)
    circle(draw, wc, 22, 1, (200,200,220))


# ══════════════════════════════════════════════════════════════════════════════
#  WINDOW CHROME
# ══════════════════════════════════════════════════════════════════════════════
def window_frame(img, x, y, w, h, title, radius=14, accent=None):
    layer = img.copy().convert("RGBA")
    draw = ImageDraw.Draw(layer)
    if accent is None:
        accent = P["violet"]

    shadow_rr(layer, x, y, w, h, radius, depth=24)

    draw.rounded_rectangle([x, y, x+w, y+h], radius=radius, fill=P["win_bg"])
    draw.rounded_rectangle([x, y, x+w, y+38], radius=radius, fill=P["win_title"])
    draw.rectangle([x, y+24, x+w, y+38], fill=P["win_title"])
    draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                            outline=(*accent, 60), width=1)
    draw.rounded_rectangle([x+1, y+1, x+w-1, y+h-1], radius=radius-1,
                            outline=(255,255,255,8), width=1)
    draw.line([(x, y+38), (x+w, y+38)], fill=(*P["sep"], 200))

    for lx, col in [(x+18, P["red"]), (x+40, P["yellow"]), (x+62, P["green_btn"])]:
        circle(draw, lx, y+19, 7, col)
        circle(draw, lx-2, y+17, 2, (255,255,255,60))

    tcx(draw, title, x + w//2, y+11, _font(12), (170,170,200))
    img.paste(layer, (0,0), layer)
    return ImageDraw.Draw(img)


# ══════════════════════════════════════════════════════════════════════════════
#  CODE EDITOR
# ══════════════════════════════════════════════════════════════════════════════
def draw_code_editor(img, x, y, w, h):
    window_frame(img, x, y, w, h, "intent_engine.py — Axon OS")
    d = ImageDraw.Draw(img)

    ty = y + 38
    d.rectangle([x, ty, x+w, ty+30], fill=(10,10,19))
    tabs = [("intent_engine.py", True), ("model_router.py", False),
            ("config.toml", False), ("axon_core.py", False)]
    tx2 = x + 10
    ft = _font(11)
    for name, active in tabs:
        tbw = tw(d, name, ft) + 20
        if active:
            rr(d, [tx2-2, ty+4, tx2+tbw, ty+26], 4, fill=P["win_bg"])
            d.text((tx2+8, ty+8), name, font=ft, fill=P["text"])
            d.line([(tx2-2, ty+26), (tx2+tbw, ty+26)], fill=P["violet"], width=2)
        else:
            d.text((tx2+8, ty+8), name, font=ft, fill=P["text_ghost"])
        tx2 += tbw + 4

    cy2 = ty + 30
    lnw = 46
    d.rectangle([x, cy2, x+lnw, y+h], fill=(9, 9, 17))
    d.rectangle([x+lnw, cy2, x+w, y+h], fill=P["win_bg"])
    d.line([(x+lnw, cy2), (x+lnw, y+h)], fill=(22,22,38))

    fm = _mono(12)
    line_h = 19

    # highlighted current line
    d.rectangle([x+lnw, cy2 + 8 + 14*line_h - 1,
                 x+w-10, cy2 + 8 + 15*line_h - 1], fill=(25,20,50))

    code = [
        [(P["cc"], "# Axon OS — Intent Engine  ·  v0.4.0-alpha")],
        [],
        [(P["ck"],"import"),(P["text"]," asyncio"),(P["text"],", "),(P["ck"],"json")],
        [(P["ck"],"from"),(P["text"]," axon.ai "),(P["ck"],"import"),(P["text"]," IntentParser, ModelRouter")],
        [(P["ck"],"from"),(P["text"]," axon.shell "),(P["ck"],"import"),(P["text"]," SpaceManager")],
        [],
        [(P["cc"],"# ── Config ──────────────────────────────────────")],
        [(P["ck"],"@dataclass")],
        [(P["ck"],"class"),(P["text"]," "),(P["cf"],"AxonConfig"),(P["text"],":")],
        [(P["text"],"    model:   "),(P["cs"],'"axon-3b"')],
        [(P["text"],"    host:    "),(P["cs"],'"localhost:11434"')],
        [(P["text"],"    stream:  "),(P["ck"],"True")],
        [(P["text"],"    accent:  "),(P["cs"],'"#8b5cf6"'),(P["cc"],"  # Axon violet")],
        [],
        [(P["cc"],"# ── Intent handler ──────────────────────────────")],
        [(P["ck"],"async"),(P["text"]," "),(P["ck"],"def"),(P["text"]," "),(P["cf"],"handle_intent"),
         (P["text"],"(query: "),(P["cf"],"str"),(P["text"],") -> "),(P["cf"],"dict"),(P["text"],":")],
        [(P["text"],"    parser = "),(P["cf"],"IntentParser"),(P["text"],"()")],
        [(P["text"],"    router = "),(P["cf"],"ModelRouter"),(P["text"],".from_config()")],
        [(P["text"],"    intent = "),(P["ck"],"await"),(P["text"]," parser."),(P["cf"],"parse"),(P["text"],"(query)")],
        [],
        [(P["text"],"    "),(P["ck"],"match"),(P["text"]," intent.type:")],
        [(P["text"],"        "),(P["ck"],"case"),(P["text"]," "),(P["cs"],'"run_command"'),(P["text"],":")],
        [(P["text"],"            result = "),(P["ck"],"await"),(P["text"]," "),(P["cf"],"exec_shell"),(P["text"],"(intent.payload)")],
        [(P["text"],"        "),(P["ck"],"case"),(P["text"]," "),(P["cs"],'"open_app"'),(P["text"],":")],
        [(P["text"],"            result = "),(P["cf"],"launch_app"),(P["text"],"(intent.payload)")],
        [(P["text"],"        "),(P["ck"],"case _:")],
        [(P["text"],"            result = "),(P["ck"],"await"),(P["text"]," router."),(P["cf"],"stream"),(P["text"],"(intent)")],
        [],
        [(P["text"],"    "),(P["ck"],"return"),(P["text"]," {"),(P["cs"],'"ok"'),(P["text"],": result, "),(P["cs"],'"intent"'),(P["text"],": intent}")],
    ]

    for i, tokens in enumerate(code):
        ly = cy2 + 8 + i * line_h
        if ly > y + h - 26: break
        ln = str(i+1)
        d.text((x+lnw-8-tw(d,ln,fm), ly), ln, font=fm, fill=P["text_ghost"])
        lx = x + lnw + 14
        for col, tok in tokens:
            d.text((lx, ly), tok, font=fm, fill=col)
            lx += tw(d, tok, fm)

    rr(d, [x+w-8, cy2+4, x+w-2, y+h-26], 3, fill=P["scrollbar"])
    rr(d, [x+w-8, cy2+4, x+w-2, cy2+90], 3, fill=(65,65,100))

    sb_y = y + h - 22
    d.rectangle([x, sb_y, x+w, y+h], fill=(9,9,19))
    d.line([(x, sb_y), (x+w, sb_y)], fill=(20,20,38))
    f9 = _font(9)
    for lbl, col, ox in [("● Python", P["aurora1"], 10),
                          ("⬡ AI-assist ON", P["violet"], 90),
                          ("Ln 15, Col 42", P["text_dim"], 215)]:
        d.text((x+ox, sb_y+4), lbl, font=f9, fill=col)
    trx(d, "UTF-8  LF  Spaces: 4", x+w-6, sb_y+4, f9, P["text_ghost"])


# ══════════════════════════════════════════════════════════════════════════════
#  TERMINAL
# ══════════════════════════════════════════════════════════════════════════════
def draw_terminal(img, x, y, w, h):
    window_frame(img, x, y, w, h, "Axon Terminal — zsh", accent=P["cyan"])
    d = ImageDraw.Draw(img)

    inner_y = y + 38
    d.rectangle([x, inner_y, x+w, y+h], fill=(6,10,16))

    fm = _mono(13)
    f10 = _mono(10)
    lh = 20

    dim_col   = P["text_ghost"]
    path_col  = P["cyan"]
    cmd_col   = P["text"]
    out_col   = (160, 180, 200)

    ty_base = inner_y + 10
    row = [0]  # mutable for nested helper

    def prompt_line(cmd, out_lines):
        px = x + 14
        ly = ty_base + row[0]*lh
        d.text((px, ly), "❯", font=fm, fill=P["violet"])
        d.text((px+20, ly), "~/Axon\\ OS", font=fm, fill=path_col)
        sx = px+20+tw(d,"~/Axon\\ OS",fm)
        d.text((sx, ly), " $ ", font=fm, fill=dim_col)
        d.text((sx+tw(d," $ ",fm), ly), cmd, font=fm, fill=cmd_col)
        row[0] += 1
        for col3, txt in out_lines:
            d.text((x+14, ty_base+row[0]*lh), txt, font=fm, fill=col3)
            row[0] += 1
        row[0] += 1

    d.text((x+14, ty_base), "Axon OS 0.1.0-alpha  ·  kernel 6.8.0-axon  ·  llama3.2:3b ✓",
           font=f10, fill=dim_col)
    row[0] += 1
    d.line([(x+10, ty_base+row[0]*lh-4), (x+w-10, ty_base+row[0]*lh-4)], fill=(20,30,40))
    row[0] += 1

    prompt_line("axon status", [
        (P["aurora1"], "  ● axon-shell         active"),
        (P["aurora1"], "  ● ollama             running  (llama3.2:3b loaded)"),
        (P["cyan"],    "  ● axon-intent-bar    listening on Ctrl+Space"),
    ])

    prompt_line("git log --oneline -3", [
        (P["violet"], "9ffa358  feat: futuristic UI overhaul, aurora wallpaper"),
        (dim_col,     "6a86c84  feat: streaming AI, welcome app, shell extension"),
        (dim_col,     "fa7bd35  feat: initial Axon OS scaffold"),
    ])

    prompt_line("sudo bash build/build.sh", [
        (out_col,        "  [axon-build] Phase 1/5: Checking dependencies... OK"),
        (out_col,        "  [axon-build] Phase 3/5: Configuring live-build..."),
        (P["aurora1"],   "  [axon-build] Phase 5/5: Building ISO..."),
    ])

    # blinking cursor
    d.text((x+14, ty_base+row[0]*lh), "❯", font=fm, fill=P["violet"])
    d.text((x+34, ty_base+row[0]*lh), "~/Axon\\ OS", font=fm, fill=path_col)
    sx2 = x+34+tw(d,"~/Axon\\ OS",fm)+tw(d," $ ",fm)
    d.text((x+34+tw(d,"~/Axon\\ OS",fm), ty_base+row[0]*lh), " $ ", font=fm, fill=dim_col)
    d.rectangle([sx2, ty_base+row[0]*lh+1, sx2+9, ty_base+row[0]*lh+lh-2],
                fill=(*P["cyan"], 180))


# ══════════════════════════════════════════════════════════════════════════════
#  AI PANEL
# ══════════════════════════════════════════════════════════════════════════════
def draw_ai_panel(img):
    pw, ph = 390, H - 30 - 88
    px = W - pw
    py = 30

    layer = img.copy().convert("RGBA")
    d = ImageDraw.Draw(layer)

    for i in range(28, 0, -1):
        a = int(70*(i/28)**2)
        d.rectangle([px-i, py, px, py+ph], fill=(0,0,0,a))

    d.rectangle([px, py, px+pw, py+ph], fill=(*P["panel"], 245))
    d.line([(px, py), (px, py+ph)], fill=(*P["violet"], 80))
    d.line([(px+1, py), (px+1, py+ph)], fill=(255,255,255,5))

    hdr_h = 54
    d.rectangle([px, py, px+pw, py+hdr_h], fill=(9,9,20))
    d.line([(px, py+hdr_h), (px+pw, py+hdr_h)], fill=(30,30,52))
    hexagon(d, px+24, py+27, 10, P["violet"])
    f16b = _font(16, True); f11 = _font(11); f12 = _font(12)
    d.text((px+42, py+11), "Axon AI", font=f16b, fill=P["text"])

    mb = "axon-3b"
    mbw = tw(d, mb, f11) + 14
    mbx = px + pw - mbw - 10
    rr(d, [mbx, py+16, mbx+mbw, py+36], 6,
       fill=(10,50,70,200), outline=(*P["cyan"],140), w=1)
    d.text((mbx+7, py+18), mb, font=f11, fill=P["cyan"])
    circle(d, px+pw-12, py+hdr_h-10, 5, P["aurora1"])

    my = py + hdr_h + 12

    def user_bubble(text, yy):
        bw2 = min(tw(d, text, f12)+24, pw-20)
        bx2 = px + pw - bw2 - 10
        rr(d, [bx2, yy, bx2+bw2, yy+30], 10, fill=(55,30,110,220))
        d.text((bx2+12, yy+7), text, font=f12, fill=(220,190,255))
        return yy + 38

    def ai_bubble(lines, yy):
        bh = len(lines)*18 + 18
        rr(d, [px+10, yy, px+pw-10, yy+bh], 10,
           fill=(18,18,34,220), outline=(40,40,70,180), w=1)
        hexagon(d, px+22, yy+12, 6, P["violet"])
        for i, (col, txt) in enumerate(lines):
            d.text((px+22, yy+9+i*18), txt, font=f12, fill=col)
        return yy + bh + 8

    my = user_bubble("Make the UI look futuristic", my)
    my = ai_bubble([
        (P["text_dim"], "Done! Highlights of the overhaul:"),
        (P["aurora1"],  "  ✓ Aurora nebula wallpaper"),
        (P["cyan"],     "  ✓ Glassmorphic neon windows"),
        (P["violet"],   "  ✓ Liquid-glass dock + glow"),
        (P["text_dim"], "  ✓ Scan-line + vignette fx"),
    ], my)

    my = user_bubble("How do I boot in VirtualBox?", my)
    my = ai_bubble([
        (P["text_dim"], "1. Build ISO:  sudo bash build/build.sh"),
        (P["cyan"],     "2. New VM → type: Linux, Ubuntu 64-bit"),
        (P["aurora1"],  "3. 4 GB RAM, 2 CPU, 3D accel ON"),
        (P["text_dim"], "4. Attach axon-os-0.1.0-alpha.iso"),
        (P["violet"],   "5. Boot → Calamares installs Axon OS"),
    ], my)

    for di in range(3):
        a = 220 if di==0 else (150 if di==1 else 80)
        circle(d, px+18+di*16, my+10, 5, (*P["violet"], a))

    iy = py + ph - 50
    d.rectangle([px, iy-1, px+pw, py+ph], fill=(8,8,18))
    d.line([(px, iy-1), (px+pw, iy-1)], fill=(28,28,48))
    rr(d, [px+10, iy+8, px+pw-48, iy+34], 8,
       fill=(18,18,32), outline=(40,40,70), w=1)
    d.text((px+20, iy+13), "Ask Axon AI...", font=f12, fill=P["text_ghost"])
    sbx = px+pw-42
    rr(d, [sbx, iy+8, sbx+30, iy+34], 8, fill=(*P["violet_dim"], 220))
    tcx(d, "↑", sbx+15, iy+9, _font(14, True), P["text"])

    img.paste(layer, (0,0), layer)


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM MONITOR WIDGET
# ══════════════════════════════════════════════════════════════════════════════
def draw_sysmon(img):
    ai_panel_x = W - 390
    ww, wh = 250, 118
    wx = ai_panel_x - ww - 12
    wy = 38

    layer = img.copy().convert("RGBA")
    d = ImageDraw.Draw(layer)
    shadow_rr(layer, wx, wy, ww, wh, 12, depth=14)
    rr(d, [wx, wy, wx+ww, wy+wh], 12, fill=(*P["panel"], 225))
    rr(d, [wx, wy, wx+ww, wy+wh], 12, outline=(*P["violet"], 40), w=1)
    rr(d, [wx+1, wy+1, wx+ww-1, wy+wh-1], 11, outline=(255,255,255,6), w=1)

    f11b = _font(11, True); f9 = _font(9)
    d.text((wx+12, wy+8), "System Monitor", font=f11b, fill=P["text_dim"])

    bars = [
        ("CPU",  62, P["cyan"]),
        ("RAM",  48, P["violet"]),
        ("GPU",  35, P["aurora1"]),
        ("Disk", 22, P["aurora2"]),
    ]
    bar_area = ww - 24
    for i, (lbl, pct, col) in enumerate(bars):
        by = wy + 28 + i*20
        d.text((wx+12, by), lbl, font=f9, fill=P["text_dim"])
        track_x = wx + 44
        track_w = bar_area - 52
        rr(d, [track_x, by+2, track_x+track_w, by+12], 5, fill=(20,20,40))
        fw = int(track_w * pct/100)
        rr(d, [track_x, by+2, track_x+fw, by+12], 5, fill=col)
        d.text((track_x+track_w+6, by+2), f"{pct}%", font=f9, fill=col)

    img.paste(layer, (0,0), layer)


# ══════════════════════════════════════════════════════════════════════════════
#  INTENT BAR
# ══════════════════════════════════════════════════════════════════════════════
def draw_intent_bar(img):
    bw, bh = 700, 92
    bx = (W - 390 - bw) // 2  # center between left edge and AI panel
    by = H // 2 - 55

    layer = img.copy().convert("RGBA")
    d = ImageDraw.Draw(layer)

    gl = glow_layer(img.size, bx+bw//2, by+bh//2, 380, 160, P["violet"],
                    steps=100, peak_alpha=45)
    layer.paste(gl, (0,0), gl)
    gl2 = glow_layer(img.size, bx+bw//2, by+bh//2, 260, 100, P["cyan"],
                     steps=80, peak_alpha=20)
    layer.paste(gl2, (0,0), gl2)

    for i in range(24, 0, -1):
        a = int(110*(i/24)**2)
        d.rounded_rectangle([bx-i, by+i, bx+bw+i, by+bh+i], radius=20, fill=(0,0,0,a))

    d.rounded_rectangle([bx, by, bx+bw, by+bh], radius=20,
                         fill=(*P["win_bg"], 252))
    d.rounded_rectangle([bx, by, bx+bw, by+bh], radius=20,
                         outline=(*P["violet"], 130), width=1)
    d.rounded_rectangle([bx+1, by+1, bx+bw-1, by+bh-1], radius=19,
                         outline=(255,255,255,10), width=1)
    d.rounded_rectangle([bx+2, by+1, bx+bw-2, by+2], radius=19,
                         fill=(255,255,255,22))

    hexagon(d, bx+26, by+26, 10, P["violet"])
    d.line([(bx+44, by+10), (bx+44, by+42)], fill=(35,35,60))

    f17 = _font(17)
    d.text((bx+56, by+14), "Build ISO and push to GitHub", font=f17, fill=P["text"])
    cx5 = bx+56+tw(d,"Build ISO and push to GitHub",f17)
    d.rectangle([cx5+3, by+16, cx5+12, by+32], fill=(*P["violet"], 200))

    f11b = _font(11, True)
    badges = [("● axon-3b", P["cyan"], (10,50,70,200), P["cyan"]),
              ("⬡ Intent",  P["violet"], (40,20,90,200), P["violet"])]
    bx_r = bx + bw - 16
    for lbl, col, bg, border in reversed(badges):
        bw2 = tw(d, lbl, f11b) + 18
        bx_r -= bw2
        rr(d, [bx_r, by+9, bx_r+bw2, by+32], 9, fill=bg,
           outline=(*border, 150), w=1)
        d.text((bx_r+9, by+11), lbl, font=f11b, fill=col)
        bx_r -= 8

    f11 = _font(11)
    cx6 = bx + 14
    cy6 = by + 56
    for chip in ["Open App", "Run Command", "Search Web", "Write Code", "Summarize"]:
        cw2 = tw(d, chip, f11) + 20
        if cx6+cw2 > bx+bw-14: break
        rr(d, [cx6, cy6, cx6+cw2, cy6+22], 8,
           fill=(*P["chip"], 200), outline=(*P["chip_border"], 180), w=1)
        d.text((cx6+10, cy6+3), chip, font=f11, fill=P["text_dim"])
        cx6 += cw2 + 8

    f10 = _font(10)
    hx = bx + bw - 14
    for hint in ["↵ Execute", "⌥↵ AI answer", "⎋ Dismiss"]:
        trx(d, hint, hx, cy6+4, f10, P["text_ghost"])
        hx -= tw(d, hint, f10) + 18

    img.paste(layer, (0,0), layer)


# ══════════════════════════════════════════════════════════════════════════════
#  DOCK
# ══════════════════════════════════════════════════════════════════════════════
def draw_dock(img):
    ICONS = [
        ("Finder",   (60, 130, 200),  "⊞",  True),
        ("Firefox",  (220, 100, 40),  "◎",  True),
        ("Terminal", (30, 180, 110),  ">_", True),
        ("VS Code",  (30, 120, 220),  "{}",  True),
        ("Settings", (110, 110, 140), "⚙",  False),
        ("Calendar", (220, 55,  75),  "31", False),
        ("Photos",   (200, 155, 40),  "⬡",  False),
        ("Music",    (185, 55, 205),  "♪",  False),
        ("Notes",    (240, 200, 50),  "✎",  False),
        (None, None, None, False),
        ("AI Panel", (139, 92, 246),  "⬡",  True),
        ("Intent",   (34, 211, 238),  "✦",  True),
    ]

    icon_sz  = 54
    icon_gap = 12
    sep_w    = 18
    pad_h    = 10
    n_real   = sum(1 for ic in ICONS if ic[0] is not None)
    dock_w   = icon_sz*n_real + icon_gap*(n_real-1) + sep_w + 40
    dock_h   = icon_sz + pad_h*2 + 16
    dock_x   = (W - 390 - dock_w) // 2
    dock_y   = H - dock_h - 10

    layer = img.copy().convert("RGBA")
    d = ImageDraw.Draw(layer)

    for i in range(16, 0, -1):
        a = int(55*(i/16)**2)
        d.rounded_rectangle([dock_x-i, dock_y+i, dock_x+dock_w+i, dock_y+dock_h+i],
                             radius=36, fill=(0,0,0,a))

    d.rounded_rectangle([dock_x, dock_y, dock_x+dock_w, dock_y+dock_h],
                         radius=36, fill=(*P["dock_bg"], 235))
    d.rounded_rectangle([dock_x, dock_y, dock_x+dock_w, dock_y+dock_h],
                         radius=36, outline=(*P["sep"], 200), width=1)
    d.arc([dock_x+6, dock_y+3, dock_x+dock_w-6, dock_y+dock_h-3],
          start=200, end=340, fill=(255,255,255,22), width=1)

    ix = dock_x + 20
    f9   = _font(9)
    f_s  = _font(16, True)
    f_s2 = _font(13, True)

    for name, color, sym, running in ICONS:
        if name is None:
            d.line([(ix+6, dock_y+16), (ix+6, dock_y+dock_h-16)],
                   fill=(*P["sep"], 180), width=1)
            ix += sep_w
            continue

        icx = ix + icon_sz//2
        icy = dock_y + pad_h + icon_sz//2
        lift = 8 if running else 0

        if running:
            gl = glow_layer(layer.size, icx, icy-lift, 50, 50, color,
                            steps=40, peak_alpha=55)
            layer.paste(gl, (0,0), gl)
            d = ImageDraw.Draw(layer)

        circle(d, icx, icy-lift, icon_sz//2, (*color, 230))
        d.arc([icx-icon_sz//2+3, icy-lift-icon_sz//2+3,
               icx+icon_sz//2-3, icy-lift+icon_sz//2-3],
              start=200, end=360, fill=(255,255,255,55), width=3)

        fs = f_s if len(sym) <= 2 else f_s2
        bb = d.textbbox((0,0), sym, font=fs)
        d.text((icx-(bb[2]-bb[0])//2, icy-lift-(bb[3]-bb[1])//2-2),
               sym, font=fs, fill=(255,255,255,230))

        tcx(d, name, icx, dock_y+pad_h+icon_sz+4, f9, P["text_ghost"])

        if running:
            circle(d, icx, dock_y+dock_h-6, 3, (*P["violet"], 220))

        ix += icon_sz + icon_gap

    img.paste(layer, (0,0), layer)


# ══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATION TOAST
# ══════════════════════════════════════════════════════════════════════════════
def draw_notification(img):
    ai_x = W - 390
    nw, nh = 300, 70
    nx = ai_x - nw - 16
    ny = 38

    layer = img.copy().convert("RGBA")
    d = ImageDraw.Draw(layer)
    shadow_rr(layer, nx, ny, nw, nh, 14, depth=12)
    rr(d, [nx, ny, nx+nw, ny+nh], 14, fill=(*P["panel"], 240))
    rr(d, [nx, ny, nx+nw, ny+nh], 14, outline=(*P["sep"], 150), w=1)
    d.rounded_rectangle([nx, ny+8, nx+4, ny+nh-8], radius=2, fill=P["aurora1"])

    hexagon(d, nx+20, ny+nh//2, 8, P["aurora1"])
    f12b = _font(12, True); f11 = _font(11)
    d.text((nx+36, ny+10), "Build complete", font=f12b, fill=P["text"])
    d.text((nx+36, ny+28), "axon-os-0.1.0-alpha.iso  ·  2.1 GB", font=f11, fill=P["text_dim"])
    d.text((nx+36, ny+46), "Pushed to GitHub ✓", font=f11, fill=P["aurora1"])
    d.text((nx+nw-18, ny+8), "✕", font=_font(10), fill=P["text_ghost"])

    img.paste(layer, (0,0), layer)


# ══════════════════════════════════════════════════════════════════════════════
#  WATERMARK
# ══════════════════════════════════════════════════════════════════════════════
def draw_watermark(img):
    d = ImageDraw.Draw(img)
    f = _font(11)
    tcx(d, "Axon OS 0.1.0-alpha  ·  GNOME on Ubuntu Noble  ·  Futuristic Edition",
        W//2, H-14, f, P["text_ghost"])


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("Generating Axon OS futuristic desktop preview (1920×1080)...")
    img = Image.new("RGBA", (W, H), (4, 4, 12, 255))

    print("  [1/9] Background — aurora nebula + star field...")
    draw_background(img)

    print("  [2/9] Code editor window...")
    draw_code_editor(img, 40, 48, 820, 560)

    print("  [3/9] Terminal window...")
    draw_terminal(img, 76, 168, 740, 380)

    print("  [4/9] AI panel...")
    draw_ai_panel(img)

    print("  [5/9] System monitor widget...")
    draw_sysmon(img)

    print("  [6/9] Menu bar...")
    draw_menubar(img)

    print("  [7/9] Intent bar (Spotlight)...")
    draw_intent_bar(img)

    print("  [8/9] Dock...")
    draw_dock(img)

    print("  [9/9] Notification + watermark...")
    draw_notification(img)
    draw_watermark(img)

    final = Image.new("RGB", (W, H), (4, 4, 12))
    final.paste(img, (0, 0), img)
    final = ImageEnhance.Sharpness(final).enhance(1.15)
    final = ImageEnhance.Contrast(final).enhance(1.05)

    final.save(OUT, "PNG", compress_level=6)
    print(f"\n  ✓ Saved: {OUT}")
    return OUT

if __name__ == "__main__":
    main()
