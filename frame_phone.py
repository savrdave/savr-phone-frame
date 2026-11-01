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

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

VIDEO_EXT = {".mov", ".mp4", ".m4v"}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Command failed:\n{' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
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


def analyse_bezel(bezel_path, workdir):
    """Find the transparent screen area inside the bezel and build a matching mask."""
    img = Image.open(bezel_path).convert("RGBA")
    a = np.array(img)[:, :, 3]
    # 255 = transparent (screen or outside), 0 = solid phone body
    tr = Image.fromarray(np.where(a < 128, 255, 0).astype(np.uint8), "L").copy()  # copy: floodfill is a no-op on array-backed images
    W, H = tr.size
    seed = (W // 2, H // 2)
    if tr.getpixel(seed) != 255:
        sys.exit("Couldn't find the screen: the centre of the bezel isn't transparent. Is this a bezel PNG with a see-through screen?")
    ImageDraw.floodfill(tr, seed, 128)
    screen = np.array(tr) == 128
    if screen[0, :].any() or screen[:, 0].any():
        sys.exit("The screen area leaks to the edge of the image. Use a bezel PNG with a closed screen opening.")
    ys, xs = np.where(screen)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    # Grow the mask a few px so the video tucks under the anti-aliased bezel edge (no gaps)
    m = Image.fromarray((screen * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(7))
    pad = 3
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(W, x1 + pad), min(H, y1 + pad)
    # ffmpeg wants even sizes for some codecs
    if (x1 - x0) % 2: x1 -= 1
    if (y1 - y0) % 2: y1 -= 1
    mask_path = Path(workdir) / "mask.png"
    m.crop((x0, y0, x1, y1)).save(mask_path)
    return dict(W=W, H=H, x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0), mask=mask_path)


def statusbar_colour(video, sb_h, workdir, at=0.5):
    """Sample the app colour just under the status bar so the cover blends in."""
    frame = Path(workdir) / "first.png"
    run(["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(frame)])
    arr = np.array(Image.open(frame).convert("RGB"))
    row = arr[min(arr.shape[0] - 1, sb_h + 6), :, :]
    r, g, b = np.median(row, axis=0).astype(int)
    return f"0x{r:02x}{g:02x}{b:02x}"


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

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start}", "-t", f"{length}", "-i", str(video),
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

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. On a Mac: brew install ffmpeg")

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
