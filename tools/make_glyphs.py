"""Dev-only: pre-render status bar glyphs so the tool never needs ffmpeg's drawtext
(Homebrew's ffmpeg ships without it). Needs Pillow; users never run this."""
import json, string, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

font_path = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
SIZE = 200
out = Path(__file__).resolve().parent.parent / "assets" / "glyphs"
out.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype(font_path, SIZE)
asc, desc = font.getmetrics()
H = asc + desc
chars = string.digits + string.ascii_uppercase + ":+ "
meta = {"size": SIZE, "height": H}
glyphs = {}
for ch in chars:
    adv = max(1, int(round(font.getlength(ch))))
    im = Image.new("RGBA", (adv, H), (255, 255, 255, 0))
    ImageDraw.Draw(im).text((0, 0), ch, font=font, fill=(255, 255, 255, 255))
    name = {":": "colon", "+": "plus", " ": "space"}.get(ch, ch)
    im.save(out / f"{name}.png")
    glyphs[ch] = {"file": f"{name}.png", "advance": adv}
l, t, r, b = font.getbbox("9")
meta["cap_top"], meta["cap_bottom"] = t, b
meta["glyphs"] = glyphs
(out / "metrics.json").write_text(json.dumps(meta, indent=1))
print("ok", len(glyphs), "glyphs, height", H, "cap", t, b)
