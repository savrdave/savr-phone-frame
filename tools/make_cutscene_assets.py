#!/usr/bin/env python3
"""
Builds the reusable SAVR cutscene assets (run once, results are committed):
  assets/cutscene/icon_layer.mov  transparent 4 sec icon animation (pop, double coin flip, flash, shine)
  assets/cutscene/glow.png        soft green spotlight that builds behind the icon

Needs Pillow + numpy, but only to rebuild these assets. make_cutscene.py itself only needs ffmpeg.
Usage: python3 tools/make_cutscene_assets.py path/to/SavrAppIcon.png
"""
import math, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "assets" / "cutscene"
FPS, DUR = 30, 4.0
CW, CH = 900, 1500          # canvas; the icon rests centred at (CW/2, REST_Y)
REST_Y = 1000
ICON = 380                  # resting icon size on the canvas
TOSS = 470                  # how high the coin flip goes (px)

# timeline (seconds)
POP0, POP1 = 1.15, 1.50
TOSS0, TOSS1 = 1.62, 2.55
SHINE0, SHINE1 = 2.90, 3.45


def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def smooth(p): p = clamp(p); return p * p * (3 - 2 * p)
def ease_out_back(p, s=2.2):
    p = clamp(p) - 1
    return 1 + p * p * ((s + 1) * p + s)


def load_icon(path):
    im = Image.open(path).convert("RGBA")
    n = im.width
    a = np.asarray(im.convert("RGB")).astype(int)
    # find the corner radius: walk the diagonal until the pixel stops being white
    d = next(i for i in range(n // 2) if a[i, i].sum() < 3 * 235)
    r = d / (1 - 1 / math.sqrt(2)) + 2
    ss = 4
    m = Image.new("L", (n * ss, n * ss), 0)
    inset = 3 * ss
    ImageDraw.Draw(m).rounded_rectangle([inset, inset, n * ss - inset, n * ss - inset], radius=r * ss, fill=255)
    im.putalpha(m.resize((n, n), Image.LANCZOS))
    return im


def shine(icon, p):
    """Diagonal light band sweeping across the icon, clipped to its shape."""
    n = icon.width
    band = Image.new("L", (n * 2, n * 2), 0)
    ImageDraw.Draw(band).rectangle([n - n * 0.09, 0, n + n * 0.09, n * 2], fill=150)
    band = band.filter(ImageFilter.GaussianBlur(n * 0.05)).rotate(28, resample=Image.BICUBIC)
    off = int((-0.9 + 1.8 * p) * n)
    band = band.crop((n // 2 - off, n // 2, n // 2 - off + n, n // 2 + n))
    a = np.minimum(np.asarray(band, float), np.asarray(icon.getchannel("A"), float)) / 255.0
    rgb = np.asarray(icon.convert("RGB"), float)
    rgb = rgb + (255 - rgb) * a[..., None] * 0.85
    out = Image.fromarray(rgb.clip(0, 255).astype(np.uint8)).convert("RGBA")
    out.putalpha(icon.getchannel("A"))
    return out


def frame(t, front, back):
    cv = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    if t < POP0:
        return cv
    sx = sy = 1.0
    y = REST_Y
    face = front
    alpha = 1.0
    if t < POP1:                                   # pop in
        p = (t - POP0) / (POP1 - POP0)
        sx = sy = max(0.01, ease_out_back(p))
        alpha = clamp(p * 3)
    elif TOSS0 <= t < TOSS1:                       # tossed up, two full flips
        p = (t - TOSS0) / (TOSS1 - TOSS0)
        y = REST_Y - TOSS * 4 * p * (1 - p)
        ang = 4 * math.pi * smooth(p)
        c = math.cos(ang)
        sx = abs(c) * (1 + 0.1 * math.sin(math.pi * p))
        sy = 1 + 0.1 * math.sin(math.pi * p)
        face = front if c >= 0 else back
    elif TOSS1 <= t < TOSS1 + 0.22:                # landing squash
        p = (t - TOSS1) / 0.22
        k = math.sin(math.pi * p) * (1 - p * 0.4)
        sx, sy = 1 + 0.10 * k, 1 - 0.10 * k
        y = REST_Y + ICON * 0.05 * k
    if SHINE0 <= t < SHINE1:
        face = shine(front, (t - SHINE0) / (SHINE1 - SHINE0))

    # soft shadow under the icon, smaller while it's in the air
    lift = (REST_Y - y) / TOSS
    sh = Image.new("L", (CW, CH), 0)
    sw = ICON * 0.9 * (1 - 0.45 * lift) * min(1, sy)
    shy = REST_Y + ICON * 0.55
    ImageDraw.Draw(sh).ellipse([CW / 2 - sw / 2, shy - sw * 0.08, CW / 2 + sw / 2, shy + sw * 0.08],
                               fill=int(120 * alpha * (1 - 0.6 * lift)))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    cv.paste(Image.new("RGBA", (CW, CH), (0, 0, 0, 255)), (0, 0), sh)

    # flash ring when it lands
    if TOSS1 <= t < TOSS1 + 0.45:
        p = (t - TOSS1) / 0.45
        ring = Image.new("L", (CW, CH), 0)
        rad = ICON * (0.6 + 0.9 * p)
        ImageDraw.Draw(ring).ellipse([CW / 2 - rad, REST_Y - rad, CW / 2 + rad, REST_Y + rad], fill=255)
        ring = ring.filter(ImageFilter.GaussianBlur(40))
        ring = ring.point(lambda v: int(v * 0.85 * (1 - p)))
        cv.alpha_composite(Image.merge("RGBA", [Image.new("L", (CW, CH), 255)] * 3 + [ring]))

    w, h = max(1, int(ICON * sx)), max(1, int(ICON * sy))
    if w > 2:
        ic = face.resize((w, h), Image.LANCZOS)
        if alpha < 1:
            ic.putalpha(ic.getchannel("A").point(lambda v: int(v * alpha)))
        cv.alpha_composite(ic, (int(CW / 2 - w / 2), int(y - h / 2)))
    return cv


def glow():
    n = 1000
    yy, xx = np.mgrid[0:n, 0:n]
    d = np.hypot(xx - n / 2, yy - n / 2) / (n / 2)
    a = np.clip(1 - d, 0, 1) ** 2.2
    img = np.zeros((n, n, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = 120, 205, 125   # SAVR light green
    img[..., 3] = (a * 210).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(OUT / "glow.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    front = load_icon(sys.argv[1]).resize((ICON, ICON), Image.LANCZOS)
    back = front.copy()
    rgb = np.asarray(back.convert("RGB"), float) * 0.6
    back = Image.fromarray(rgb.astype(np.uint8)).convert("RGBA"); back.putalpha(front.getchannel("A"))
    glow()
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
                           "-s", f"{CW}x{CH}", "-r", str(FPS), "-i", "-",
                           "-c:v", "png", "-pix_fmt", "rgba", str(OUT / "icon_layer.mov")], stdin=subprocess.PIPE)
    for i in range(int(FPS * DUR)):
        ff.stdin.write(frame(i / FPS, front, back).tobytes())
    ff.stdin.close(); ff.wait()
    print("wrote", OUT)


if __name__ == "__main__":
    main()
