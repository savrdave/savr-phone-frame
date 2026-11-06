#!/usr/bin/env python3
"""
SAVR cutscene: a 4 sec end card that starts on the exact screen your video ends on.
That screen dims and recedes, a green spotlight builds, the SAVR icon pops in,
coin-flips twice, lands with a flash, gets a shine, and holds.

Usage (or just double-click "Make Cutscene.command"):
  python3 make_cutscene.py "inbox/done/ScreenRecording_11-05-2026 12-01-11_1.MP4" --name montage-A
  python3 make_cutscene.py recording.MP4 --at 21.4        # freeze on second 21.4 instead of the end

Give it the plain screen recording (the framed one also works: it finds the original in inbox/done).
Writes to out/cutscenes/:
  <date>_<name>_cutscene_capcut.mov    transparent phone, for CapCut (Mac)
  <date>_<name>_cutscene_descript.mp4  phone on SAVR green, 1080x1920, for Descript
Only needs Python 3 and ffmpeg, same as frame.sh.
"""
import argparse, datetime, re, shlex, shutil, sys, tempfile, platform
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import frame_phone as fp  # reuse the fake status bar and the iPhone framing

ASSETS = HERE / "assets" / "cutscene"
DUR, FPS = 4.0, 30


def clean_path(s):
    """Accept a path typed or dragged into Terminal (quotes, backslash-escaped spaces)."""
    s = s.strip()
    if not s:
        return None
    p = Path(s)
    if not p.exists():
        try:
            p = Path(shlex.split(s)[0])
        except ValueError:
            p = Path(s.strip("'\""))
    return p.expanduser()


def find_source(p):
    """If given a framed video, use its original screen recording instead."""
    if "_framed" in p.stem:
        stem = p.stem.split("_framed")[0]
        for folder in (HERE / "inbox" / "done", HERE / "inbox"):
            for f in folder.glob(stem + ".*"):
                if f.suffix.lower() in fp.VIDEO_EXT:
                    print(f"Using the original recording: {f.name}")
                    return f, True
        sys.exit("That's a framed video. Give me the plain screen recording it came from (it's in inbox/done).")
    return p, False


def newest_recording():
    vids = [f for d in (HERE / "inbox" / "done", HERE / "inbox") if d.exists()
            for f in d.iterdir() if f.suffix.lower() in fp.VIDEO_EXT]
    if not vids:
        sys.exit("No recording given and nothing in inbox/done.")
    return max(vids, key=lambda f: f.stat().st_mtime)


def smooth(expr):
    # smoothstep of clip(expr,0,1), as an ffmpeg expression
    return f"(st(0,clip({expr},0,1))*ld(0)*(3-2*ld(0)))"


def main():
    ap = argparse.ArgumentParser(description="SAVR cutscene from the last screen of a recording.")
    ap.add_argument("input", nargs="?", default="", help="screen recording (Enter/empty = newest in inbox/done)")
    ap.add_argument("--name", default="", help="short name for the video this ends, e.g. montage-A")
    ap.add_argument("--at", type=float, help="second of the recording to freeze on (default: where the framed video ends)")
    ap.add_argument("--trim-end", type=float, help="seconds the framed video cut off the end (default 0.5 for raw "
                                                   "ScreenRecording files, otherwise 0)")
    ap.add_argument("--time", default="9:41"); ap.add_argument("--network", default="5G")
    ap.add_argument("--battery", type=int, default=85)
    ap.add_argument("--statusbar-pct", type=float, default=6.4)
    ap.add_argument("--formats", default="", help="comma list of hevc,mp4,prores,webm (default: hevc+mp4 on Mac, mp4 elsewhere)")
    args = ap.parse_args()

    fp.FFMPEG, fp.FFPROBE = fp.tool("ffmpeg"), fp.tool("ffprobe")
    src = clean_path(args.input) or newest_recording()
    if not src.exists():
        sys.exit(f"Can't find {src}")
    src, from_framed = find_source(src)
    W, H, fps, dur, _ = fp.probe(src)

    # the moment to freeze on
    if args.at is not None:
        at = min(max(0.0, args.at), dur - 0.05)
    else:
        trim = args.trim_end if args.trim_end is not None else \
            (0.5 if src.name.startswith("ScreenRecording") or from_framed else 0.0)
        at = max(0.0, dur - trim - 1.0 / fps)
    print(f"Freezing on {at:.2f}s of {src.name} ({dur:.2f}s long)")

    name = re.sub(r"[^A-Za-z0-9._-]+", "-", args.name.strip()).strip("-") or src.stem.replace(" ", "_")
    stamp = datetime.date.today().isoformat()
    outdir = HERE / "out" / "cutscenes"; outdir.mkdir(parents=True, exist_ok=True)
    formats = [f for f in args.formats.split(",") if f] or (["hevc", "mp4"] if platform.system() == "Darwin" else ["mp4"])
    bezel = next(iter(sorted((HERE / "bezels").glob("*.png"))), None)
    if not bezel:
        sys.exit("Put the Apple iPhone bezel PNG in bezels/ first.")

    with tempfile.TemporaryDirectory() as wd:
        wd = Path(wd)
        # 1. the exact last screen, with the same clean 9:41 status bar the framed video has
        still = wd / "still.png"
        fp.run([fp.FFMPEG, "-y", "-v", "error", "-ss", f"{at}", "-i", str(src), "-frames:v", "1", str(still)])
        sb_h = int(round(H * args.statusbar_pct / 100))
        colour = fp.statusbar_colour(src, sb_h, wd, at=at)
        sb = wd / "sb.png"
        fp.make_status_bar(sb, W, sb_h, colour, args, wd)
        clean = wd / "clean.png"
        fp.run([fp.FFMPEG, "-y", "-v", "error", "-i", str(still), "-i", str(sb),
                "-filter_complex", "[0:v][1:v]overlay=0:0", str(clean)])

        # 2. animate it: recede, dim, spotlight, icon (pop, flip, land), flash
        rec = f"(1-0.12*{smooth('(t-0.35)/0.9')})"
        cw = int(W * 0.9) // 2 * 2
        ch = int(cw * 1500 / 900) // 2 * 2
        icon_y = int(H / 2 - ch * 1000 / 1500)
        gw = int(W * 1.5) // 2 * 2
        graph = (
            f"[0:v]format=rgba,scale=w='2*trunc(iw*{rec}/2)':h='2*trunc(ih*{rec}/2)':eval=frame[sc];"
            f"color=black:s={W}x{H}:r={FPS}:d={DUR}[bg];"
            f"[bg][sc]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame[a];"
            f"color=black:s={W}x{H}:r={FPS}:d={DUR},format=rgba,colorchannelmixer=aa=0.72,"
            f"fade=t=in:st=0.35:d=0.9:alpha=1[dim];[a][dim]overlay[b];"
            f"[1:v]format=rgba,scale={gw}:-1,fade=t=in:st=0.9:d=1.0:alpha=1[glow];"
            f"[b][glow]overlay=x=(W-w)/2:y=(H-h)/2[c];"
            f"[2:v]format=rgba,scale={cw}:{ch}[icon];"
            f"[c][icon]overlay=x=(W-w)/2:y={icon_y}:eof_action=repeat[d];"
            f"color=white:s={W}x{H}:r={FPS}:d={DUR},format=rgba,colorchannelmixer=aa=0.35,"
            f"fade=t=in:st=2.52:d=0.04:alpha=1,fade=t=out:st=2.56:d=0.35:alpha=1[fl];"
            f"[d][fl]overlay,format=yuv420p[out]"
        )
        screen = wd / f"{name}_cutscene.mp4"
        fp.run([fp.FFMPEG, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", str(DUR), "-i", str(clean),
                "-loop", "1", "-framerate", str(FPS), "-t", str(DUR), "-i", str(ASSETS / "glow.png"),
                "-i", str(ASSETS / "icon_layer.mov"),
                "-filter_complex", graph, "-map", "[out]", "-t", str(DUR), "-r", str(FPS),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", str(screen)])

        # 3. put it in the iPhone, same as the main video
        made = []
        label = {"hevc": "capcut", "prores": "capcut_prores", "webm": "web", "mp4": "descript"}
        for f in formats:
            fdir = wd / f"fmt_{f}"; fdir.mkdir()
            fp.run([sys.executable, str(HERE / "frame_phone.py"), str(screen), "--bezel", str(bezel),
                    "--out", str(fdir), "--statusbar", "none", "--format", f])
            res = next(fdir.iterdir())
            final = outdir / f"{stamp}_{name}_cutscene_{label.get(f, f)}{res.suffix}"
            shutil.move(str(res), final)
            made.append(final)

    print("\nDone:")
    for m in made:
        print(f"  {m.relative_to(HERE)}")


if __name__ == "__main__":
    main()
