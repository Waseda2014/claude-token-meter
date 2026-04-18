"""
Generates icon.png (1024x1024) — Carbon gauge style on Aurora background.
Three coloured arc zones, dotted outer ring, white needle, hub.
"""
from PIL import Image, ImageDraw, ImageFilter
import math, os

SIZE  = 1024
CX    = 512
CY    = 555
R_ARC = 350        # inner coloured arc radius
R_RING = 388       # outer dotted ring  (= R_ARC * 102/92)
ARC_W  = 18        # arc stroke width
RING_DOT = 4       # dot radius for dotted ring

# ── Helpers ───────────────────────────────────────────────────────────────────
def lerp(a, b, t):
    return a + (b - a) * t

def pt(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)

def draw_arc(layer, cx, cy, r, start_deg, end_deg, color, stroke, steps=800):
    """Draw a thick arc as a series of filled circles."""
    d = ImageDraw.Draw(layer)
    span = end_deg - start_deg
    if span < 0:
        span += 360
    for i in range(steps + 1):
        t = i / steps
        angle = math.radians(start_deg + span * t)
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        h = stroke // 2
        d.ellipse([x - h, y - h, x + h, y + h], fill=color)

# ── Canvas ────────────────────────────────────────────────────────────────────
img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))

# Aurora gradient background: #091828 → #0F2844 → #163A60
bg = Image.new('RGBA', (SIZE, SIZE))
bd = ImageDraw.Draw(bg)
for y in range(SIZE):
    t = y / SIZE
    if t < 0.5:
        f = t / 0.5
        r, g, b = int(lerp(9, 15, f)), int(lerp(24, 40, f)), int(lerp(40, 68, f))
    else:
        f = (t - 0.5) / 0.5
        r, g, b = int(lerp(15, 22, f)), int(lerp(40, 58, f)), int(lerp(68, 96, f))
    bd.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

# Rounded corners mask
mask = Image.new('L', (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE-1, SIZE-1], radius=200, fill=255)
img.paste(bg, mask=mask)

# ── Outer dotted ring ─────────────────────────────────────────────────────────
# Two segments matching the app: 142°→260° and 280°→38° (clockwise)
ring_layer = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
rd = ImageDraw.Draw(ring_layer)
dot_color = (255, 255, 255, 55)

def draw_dotted_ring(draw, cx, cy, r, start_deg, end_deg, dot_r, spacing_deg=4.2):
    span = end_deg - start_deg
    if span < 0:
        span += 360
    n = int(span / spacing_deg)
    for i in range(n + 1):
        t = i / max(n, 1)
        angle = math.radians(start_deg + span * t)
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        draw.ellipse([x - dot_r, y - dot_r, x + dot_r, y + dot_r], fill=dot_color)

draw_dotted_ring(rd, CX, CY, R_RING, 142, 260, RING_DOT)
draw_dotted_ring(rd, CX, CY, R_RING, 280, 398, RING_DOT)  # 398 = 38+360
img = Image.alpha_composite(img, ring_layer)

# ── Track arc (full 270°, faint) ─────────────────────────────────────────────
track = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
draw_arc(track, CX, CY, R_ARC, 135, 405, (255, 255, 255, 18), ARC_W)
img = Image.alpha_composite(img, track)

# ── Three coloured arc zones ──────────────────────────────────────────────────
# LOW  0–50%  : 135° → 266°   colour #AAD7FE
# MED 50–75%  : 274° → 333.5° colour #ECB967
# HIGH 75–100%: 341.5° → 405° colour #FF654D  (405 = 45+360)

ZONES = [
    (135,   266,   (170, 215, 254)),   # LOW  — #AAD7FE
    (274,   333.5, (236, 185, 103)),   # MED  — #ECB967
    (341.5, 405,   (255, 101,  77)),   # HIGH — #FF654D
]

for start, end, color in ZONES:
    # Glow pass
    glow = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw_arc(glow, CX, CY, R_ARC, start, end, color + (70,), ARC_W + 16)
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(14)))
    # Crisp pass
    crisp = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw_arc(crisp, CX, CY, R_ARC, start, end, color + (235,), ARC_W)
    img = Image.alpha_composite(img, crisp)

# ── Needle at ~42% ────────────────────────────────────────────────────────────
NEEDLE_PCT = 0.42
needle_deg = 135 + 270 * NEEDLE_PCT   # ≈ 248.4°
nx, ny = pt(CX, CY, R_ARC - 25, needle_deg)

# Glow
ng = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
ImageDraw.Draw(ng).line([(CX, CY), (nx, ny)], fill=(255, 255, 255, 100), width=22)
img = Image.alpha_composite(img, ng.filter(ImageFilter.GaussianBlur(10)))

# Crisp needle
nl = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
ImageDraw.Draw(nl).line([(CX, CY), (nx, ny)], fill=(255, 255, 255, 225), width=7)
img = Image.alpha_composite(img, nl)

# ── Hub ───────────────────────────────────────────────────────────────────────
hub = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
hd  = ImageDraw.Draw(hub)
hd.ellipse([CX-44, CY-44, CX+44, CY+44], fill=(255, 255, 255, 28))   # outer glow
hd.ellipse([CX-26, CY-26, CX+26, CY+26], fill=(255, 255, 255, 175))  # hub
hd.ellipse([CX-10, CY-10, CX+10, CY+10], fill=(15, 30, 55, 210))     # centre dot
img = Image.alpha_composite(img, hub)

# ── Save ──────────────────────────────────────────────────────────────────────
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
img.save(out)
print(f"Saved {out}")
