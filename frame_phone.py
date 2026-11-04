#!/usr/bin/env python3
"""
SAVR phone frame: put iPhone screen recordings inside a real iPhone frame,
on a transparent background, with the status bar (and the red recording dot) removed.

Usage:
  python3 frame_phone.py inbox/ --bezel bezels/iphone.png
  python3 frame_phone.py my_recording.mov --bezel bezels/iphone.png --format hevc
"""
import argparse, json, shutil, subprocess, sys, tempfile
from pathlib import Path

# No pip packages needed: only Python 3 and ffmpeg/ffprobe.
# ffmpeg is taken from PATH, or from a bin/ folder next to this script.
HERE = Path(__file__).resolve().parent


def tool(name):
    local = HERE / "bin" / name
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found. Put the {name} file in the bin/ folder (see README), or: brew install ffmpeg")
    return found


FFMPEG, FFPROBE = None, None

VIDEO_EXT = {".mov", ".mp4", ".m4v"}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Command failed:\n{' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r.stdout


def probe(path):
    out = run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = v.get("avg_frame_rate", "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    # iPhone recordings can carry a rotation flag
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    w, h = int(v["width"]), int(v["height"])
    if rot in (90, 270):
        w, h = h, w
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return w, h, fps, float(info["format"]["duration"]), has_audio


def run_bytes(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"Command failed:\n{' '.join(map(str, cmd))}\n{r.stderr.decode(errors='ignore')[-2000:]}")
    return r.stdout


def analyse_bezel(bezel_path, workdir):
    """Find the transparent screen area inside the bezel and build a matching mask."""
    info = json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(bezel_path)]))
    W, H = int(info["streams"][0]["width"]), int(info["streams"][0]["height"])
    alpha = run_bytes([FFMPEG, "-v", "error", "-i", str(bezel_path), "-vf", "format=rgba,alphaextract",
                       "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    if len(alpha) < W * H:
        sys.exit("Couldn't read the bezel's transparency. Is it a PNG with a see-through screen?")
    # '.' = see-through, '#' = solid phone
    table = bytes(46 if i < 128 else 35 for i in range(256))
    cx = W // 2
    rows = [alpha[y * W:(y + 1) * W].translate(table) for y in range(H)]
    centre_open = [y for y in range(H) if rows[y][cx] == 46 and b"#" in rows[y][:cx] and b"#" in rows[y][cx:]]
    if not centre_open:
        sys.exit("Couldn't find the screen: the centre of the bezel isn't see-through.")
    top, bottom = centre_open[0], centre_open[-1]
    spans = {}
    for y in range(top, bottom + 1):
        r = rows[y]
        if r[cx] == 46:  # normal row: grow out from the centre to the phone's edges
            l = r.rfind(b"#", 0, cx) + 1
            rr = r.find(b"#", cx)
        else:  # row through the Dynamic Island: step past it, then out to the edges
            lo = r.rfind(b".", 0, cx)
            ro = r.find(b".", cx)
            if lo < 0 or ro < 0:
                continue
            l = r.rfind(b"#", 0, lo) + 1
            rr = r.find(b"#", ro)
        if l > 0 and rr > l:
            spans[y] = (l, rr)
    x0 = min(v[0] for v in spans.values()); x1 = max(v[1] for v in spans.values())
    pad = 3
    x0, y0 = max(0, x0 - pad), max(0, top - pad)
    x1, y1 = min(W, x1 + pad), min(H, bottom + 1 + pad)
    if (x1 - x0) % 2: x1 -= 1
    if (y1 - y0) % 2: y1 -= 1
    mw, mh = x1 - x0, y1 - y0
    buf = bytearray(mw * mh)
    for y, (l, rr) in spans.items():
        yy = y - y0
        if 0 <= yy < mh:
            a, b = max(0, l - x0), min(mw, rr - x0)
            buf[yy * mw + a: yy * mw + b] = b"\xff" * (b - a)
    raw = Path(workdir) / "mask_raw.pgm"
    raw.write_bytes(f"P5 {mw} {mh} 255\n".encode() + bytes(buf))
    # Grow the mask a few px so the video tucks under the bezel's soft edge (no gaps)
    mask_path = Path(workdir) / "mask.png"
    run([FFMPEG, "-y", "-v", "error", "-i", str(raw), "-vf", "dilation,dilation,dilation", str(mask_path)])
    return dict(W=W, H=H, x=int(x0), y=int(y0), w=int(mw), h=int(mh), mask=mask_path)


def statusbar_colour(video, sb_h, workdir, at=0.5):
    """Sample the app colour just under the status bar so the cover blends in."""
    w = probe(video)[0]
    row = run_bytes([FFMPEG, "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1",
                     "-vf", f"crop=w=iw:h=2:x=0:y={sb_h + 6}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    row = row[: w * 3]  # first of the two rows (yuv needs an even crop height)
    px = [row[i:i + 3] for i in range(0, len(row) - 2, 3)]
    def med(c):
        v = sorted(p[c] for p in px); return v[len(v) // 2]
    return f"0x{med(0):02x}{med(1):02x}{med(2):02x}"


# ---------- fake iOS status bar (time, signal, 5G, battery) ----------
import struct, zlib

GLYPHS = HERE / "assets" / "glyphs"


def text_layers(text, size_px, x, mid, align):
    """Lay out pre-rendered glyphs (no drawtext needed). Returns [(png, x, y, w, h)]."""
    meta = json.loads((GLYPHS / "metrics.json").read_text())
    k = size_px / meta["size"]
    chars = [c for c in text.upper() if c in meta["glyphs"]]
    total = sum(meta["glyphs"][c]["advance"] for c in chars) * k
    cx = x - total / 2 if align == "centre" else (x - total if align == "right" else x)
    y = mid - (meta["cap_top"] + meta["cap_bottom"]) / 2 * k
    h = max(2, int(round(meta["height"] * k)))
    layers = []
    for c in chars:
        g = meta["glyphs"][c]
        w = max(1, int(round(g["advance"] * k)))
        if c != " ":
            layers.append((GLYPHS / g["file"], int(round(cx)), int(round(y)), w, h))
        cx += g["advance"] * k
    return layers


def write_png(path, w, h, rgba):
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _rrect_cov(px, py, x0, y0, x1, y1, r, ss=4):
    """Coverage (0..1) of pixel (px,py) by a rounded rectangle, supersampled."""
    hit = 0
    for i in range(ss):
        for j in range(ss):
            x = px + (i + 0.5) / ss; y = py + (j + 0.5) / ss
            if x < x0 or x > x1 or y < y0 or y > y1:
                continue
            cx = min(max(x, x0 + r), x1 - r); cy = min(max(y, y0 + r), y1 - r)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                hit += 1
    return hit / (ss * ss)


def _paint(buf, W, H, rgb, alpha, x0, y0, x1, y1, r, hole=None):
    for py in range(max(0, int(y0)), min(H, int(y1) + 2)):
        for px in range(max(0, int(x0)), min(W, int(x1) + 2)):
            c = _rrect_cov(px, py, x0, y0, x1, y1, r)
            if hole:
                c = max(0.0, c - _rrect_cov(px, py, *hole))
            if c <= 0:
                continue
            a_new = c * alpha
            i = (py * W + px) * 4
            a_old = buf[i + 3] / 255
            a_out = a_new + a_old * (1 - a_new)
            for k in range(3):
                buf[i + k] = int(round((rgb[k] * a_new + buf[i + k] * a_old * (1 - a_new)) / a_out)) if a_out else 0
            buf[i + 3] = int(round(a_out * 255))


def make_status_bar(path, W, H, bg_hex, args, workdir):
    """iPhone-style status bar sized to the recording. Layout in iOS points, scaled to pixels."""
    s = W / 393.0                                   # 393pt wide = iPhone 15/16/17 Pro
    r, g, b = int(bg_hex[2:4], 16), int(bg_hex[4:6], 16), int(bg_hex[6:8], 16)
    light_bg = (0.299 * r + 0.587 * g + 0.114 * b) > 150
    fg = (0, 0, 0) if light_bg else (255, 255, 255)
    buf = bytearray([r, g, b, 255] * (W * H))       # solid background = hides the red dot
    mid = H / 2 + 2 * s                             # icon centre line
    # signal bars (4)
    bw, gap = 3 * s, 1.6 * s
    label_w = 8.6 * len(args.network)               # rough width of the network label in points
    x = (335 - label_w - 4) * s - (4 * bw + 3 * gap)  # signal bars sit just left of the label
    for i, bh in enumerate((4.5, 6.5, 9, 11.5)):
        bx = x + i * (bw + gap)
        _paint(buf, W, H, fg, 1.0, bx, mid + 5.75 * s - bh * s, bx + bw, mid + 5.75 * s, 0.9 * s)
    # battery: outline, level fill, nub
    bx0, by0 = 340 * s, mid - 6.5 * s
    bx1, by1 = bx0 + 25 * s, by0 + 13 * s
    sw = 1.05 * s
    _paint(buf, W, H, fg, 0.4, bx0, by0, bx1, by1, 4.2 * s,
           hole=(bx0 + sw, by0 + sw, bx1 - sw, by1 - sw, 3.2 * s))
    level = max(0.05, min(1.0, args.battery / 100))
    inner = 2.1 * s
    _paint(buf, W, H, fg, 1.0, bx0 + inner, by0 + inner,
           bx0 + inner + (bx1 - bx0 - 2 * inner) * level, by1 - inner, 2.4 * s)
    _paint(buf, W, H, fg, 0.4, bx1 + 1.2 * s, mid - 2.2 * s, bx1 + 2.6 * s, mid + 2.2 * s, 0.7 * s)
    base = Path(workdir) / "sb_base.png"
    write_png(base, W, H, buf)
    # text: time on the left, network label just before the battery
    layers = text_layers(args.time, 17 * s, 66 * s, mid, "centre") + \
        text_layers(args.network, 13.5 * s, 335 * s, mid, "right")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(base)]
    graph, prev = [], "[0:v]"
    tint = "colorchannelmixer=rr=0:gg=0:bb=0," if light_bg else ""
    for i, (png, x, y, w, h) in enumerate(layers, start=1):
        cmd += ["-i", str(png)]
        graph.append(f"[{i}:v]format=rgba,{tint}scale={w}:{h}:flags=lanczos[g{i}]")
        graph.append(f"{prev}[g{i}]overlay={x}:{y}:format=auto[o{i}]")
        prev = f"[o{i}]"
    if layers:
        cmd += ["-filter_complex", ";".join(graph), "-map", prev]
    cmd += ["-frames:v", "1", "-pix_fmt", "rgba", str(path)]
    run(cmd)


def process(video, args, bez, outdir, workdir):
    vw, vh, fps, dur, has_audio = probe(video)
    start = args.trim_start
    end = dur - args.trim_end
    if end <= start:
        print(f"  skip {video.name}: trims longer than the clip")
        return
    length = end - start

    filters = []
    sb_png = None
    if args.statusbar != "none":
        sb_h = int(round(vh * args.statusbar_pct / 100))
        colour = args.statusbar_colour or statusbar_colour(video, sb_h, workdir, at=start + 0.2)
        if args.statusbar == "fake":
            sb_png = Path(workdir) / f"statusbar_{vw}x{sb_h}.png"
            make_status_bar(sb_png, vw, sb_h, colour, args, workdir)
        else:
            cover_w = "iw" if args.statusbar == "cover" else "iw*0.38"
            filters.append(f"drawbox=x=0:y=0:w={cover_w}:h={sb_h}:color={colour}:t=fill")
    filters.append(f"scale={bez['w']}:{bez['h']}:force_original_aspect_ratio=increase:flags=lanczos")
    filters.append(f"crop={bez['w']}:{bez['h']}")
    filters.append("format=rgba")

    pre = ""
    if sb_png:
        pre = "[0:v][3:v]overlay=0:0:format=auto[sbv];"
        src = "[sbv]"
    else:
        src = "[0:v]"
    graph = (
        pre +
        f"{src}{','.join(filters)}[scr];"
        f"[2:v]format=gray,scale={bez['w']}:{bez['h']}[msk];"
        f"[scr][msk]alphamerge[scra];"
        f"color=c=black@0.0:s={bez['W']}x{bez['H']}:r={fps:.3f},format=rgba[bg];"
        f"[bg][scra]overlay={bez['x']}:{bez['y']}:format=auto[t];"
        f"[1:v]format=rgba[bz];"
        f"[t][bz]overlay=0:0:format=auto"
    )
    if args.height:
        graph += f",scale=-2:{args.height}:flags=lanczos"
    graph += "[out]"

    fmt = args.format
    ext = {"prores": ".mov", "hevc": ".mov", "webm": ".webm", "png": ".png"}[fmt]
    out = outdir / f"{video.stem}_framed{ext}"

    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{start}", "-t", f"{length}", "-i", str(video),
           "-loop", "1", "-i", str(args.bezel), "-loop", "1", "-i", str(bez["mask"])]
    if sb_png:
        cmd += ["-loop", "1", "-i", str(sb_png)]
    cmd += ["-filter_complex", graph, "-map", "[out]"]
    if fmt == "png":
        cmd += ["-frames:v", "1"]
    else:
        cmd += ["-t", f"{length}", "-r", f"{fps:.3f}"]
        if args.audio and has_audio:
            cmd += ["-map", "0:a?"]
        if fmt == "prores":
            cmd += ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16"]
            if args.audio: cmd += ["-c:a", "pcm_s16le"]
        elif fmt == "hevc":  # Mac only: small files, transparent, plays natively on iPhone
            cmd += ["-c:v", "hevc_videotoolbox", "-pix_fmt", "bgra", "-alpha_quality", "0.75", "-tag:v", "hvc1", "-b:v", "12M"]
            if args.audio: cmd += ["-c:a", "aac"]
        elif fmt == "webm":
            cmd += ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28", "-row-mt", "1"]
            if args.audio: cmd += ["-c:a", "libopus"]
    cmd.append(str(out))
    run(cmd)
    print(f"  done  {out}")


def main():
    p = argparse.ArgumentParser(description="Frame iPhone screen recordings in a real iPhone, transparent background.")
    p.add_argument("input", help="a video file, or a folder of videos")
    p.add_argument("--bezel", required=True, help="Apple product bezel PNG (transparent screen)")
    p.add_argument("--out", default="out", help="output folder (default: out)")
    p.add_argument("--format", choices=["prores", "hevc", "webm", "png"], default="prores",
                   help="prores = transparent .mov for editors (default); hevc = small transparent .mov (Mac only); "
                        "webm = transparent web video; png = one still frame")
    p.add_argument("--statusbar", choices=["fake", "cover", "left", "none"], default="fake",
                   help="fake = replace it with a clean iPhone status bar: time, signal, 5G, battery (default); "
                        "cover = blank bar; left = blank only the clock/red dot side; none = leave it")
    p.add_argument("--time", default="9:41", help="time shown in the fake status bar (default 9:41)")
    p.add_argument("--network", default="5G", help="network label, e.g. 5G, LTE, 5G+ (default 5G)")
    p.add_argument("--battery", type=int, default=85, help="battery level %% in the fake status bar (default 85)")
    p.add_argument("--statusbar-pct", type=float, default=6.4, help="status bar height as %% of video height (default 6.4)")
    p.add_argument("--statusbar-colour", help="force the cover colour, e.g. 0xffffff (default: auto-sampled)")
    p.add_argument("--trim-start", type=float, default=0.0, help="seconds to cut from the start (e.g. Control Center closing)")
    p.add_argument("--trim-end", type=float, default=0.0, help="seconds to cut from the end")
    p.add_argument("--height", type=int, help="scale output to this height in px (default: bezel size)")
    p.add_argument("--audio", action="store_true", help="keep the recording's audio (off by default)")
    p.add_argument("--move-done", action="store_true", help="move processed inputs into a 'done' folder")
    args = p.parse_args()

    global FFMPEG, FFPROBE
    FFMPEG, FFPROBE = tool("ffmpeg"), tool("ffprobe")

    src = Path(args.input)
    videos = sorted(f for f in src.iterdir() if f.suffix.lower() in VIDEO_EXT) if src.is_dir() else [src]
    if not videos:
        sys.exit("No videos found.")
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as wd:
        bez = analyse_bezel(args.bezel, wd)
        print(f"Bezel screen area: {bez['w']}x{bez['h']} at ({bez['x']},{bez['y']}) in {bez['W']}x{bez['H']}")
        for v in videos:
            print(f"- {v.name}")
            process(v, args, bez, outdir, wd)
            if args.move_done and src.is_dir():
                done = src / "done"; done.mkdir(exist_ok=True)
                shutil.move(str(v), done / v.name)


if __name__ == "__main__":
    main()
