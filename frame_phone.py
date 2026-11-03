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


def process(video, args, bez, outdir, workdir):
    vw, vh, fps, dur, has_audio = probe(video)
    start = args.trim_start
    end = dur - args.trim_end
    if end <= start:
        print(f"  skip {video.name}: trims longer than the clip")
        return
    length = end - start

    filters = []
    if args.statusbar != "none":
        sb_h = int(round(vh * args.statusbar_pct / 100))
        colour = args.statusbar_colour or statusbar_colour(video, sb_h, workdir, at=start + 0.2)
        cover_w = "iw" if args.statusbar == "cover" else "iw*0.38"
        filters.append(f"drawbox=x=0:y=0:w={cover_w}:h={sb_h}:color={colour}:t=fill")
    filters.append(f"scale={bez['w']}:{bez['h']}:force_original_aspect_ratio=increase:flags=lanczos")
    filters.append(f"crop={bez['w']}:{bez['h']}")
    filters.append("format=rgba")

    graph = (
        f"[0:v]{','.join(filters)}[scr];"
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
           "-loop", "1", "-i", str(args.bezel), "-loop", "1", "-i", str(bez["mask"]),
           "-filter_complex", graph, "-map", "[out]"]
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
            cmd += ["-c:v", "hevc_videotoolbox", "-alpha_quality", "0.75", "-tag:v", "hvc1", "-b:v", "12M"]
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
    p.add_argument("--statusbar", choices=["cover", "left", "none"], default="cover",
                   help="cover = hide the whole status bar (default); left = hide only the clock/red dot side; none = leave it")
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
