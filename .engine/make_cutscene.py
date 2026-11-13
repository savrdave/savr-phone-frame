#!/usr/bin/env python3
"""
SAVR cutscene: a 4 sec end card that starts on the exact screen your video ends on.
That screen recedes and fades to black, a spotlight builds, the SAVR icon pops in,
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
import paths

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
    logged = paths.original_for(p)
    if logged:
        print(f"Using the original recording: {logged.name}")
        return logged, True
    if "_framed" in p.stem:
        stem = p.stem.split("_framed")[0]
        for folder in (paths.SCREEN_DONE, paths.SCREEN_IN):
            for f in folder.glob(stem + ".*"):
                if f.suffix.lower() in fp.VIDEO_EXT:
                    print(f"Using the original recording: {f.name}")
                    return f, True
        sys.exit("That's a framed video. Give me the plain screen recording it came from (it's in ORIGINALS/screen recordings).")
    return p, False


def newest_recording():
    vids = [f for d in (paths.SCREEN_DONE, paths.SCREEN_IN) if d.exists()
            for f in d.iterdir() if f.suffix.lower() in fp.VIDEO_EXT]
    if not vids:
        sys.exit("No recording given and nothing in ORIGINALS/screen recordings.")
    return max(vids, key=lambda f: f.stat().st_mtime)


def smooth(expr):
    # smoothstep of clip(expr,0,1), as an ffmpeg expression
    return f"(st(0,clip({expr},0,1))*ld(0)*(3-2*ld(0)))"


def screen_cutscene(src, at, wd, args):
    """The 4 sec end card at the recording's own size (no phone yet). Used by frame_phone too."""
    wd = Path(wd)
    if not fp.FFMPEG:  # called from frame_phone, which loaded its own copy
        fp.FFMPEG, fp.FFPROBE = fp.tool("ffmpeg"), fp.tool("ffprobe")
    W, H, _, _, _ = fp.probe(src)
    # 1. the exact last screen, with the same clean 9:41 status bar the framed video has
    still = wd / "still.png"
    fp.run([fp.FFMPEG, "-y", "-v", "error", "-ss", f"{at}", "-i", str(src), "-frames:v", "1", str(still)])
    sb_h = int(round(H * (args.statusbar_pct or fp.auto_statusbar_pct(W, H)) / 100))
    colour = fp.statusbar_colour(src, sb_h, wd, at=at)
    sb = wd / "sb.png"
    fp.make_status_bar(sb, W, sb_h, colour, args, wd)
    clean = wd / "clean.png"
    fp.run([fp.FFMPEG, "-y", "-v", "error", "-i", str(still), "-i", str(sb),
            "-filter_complex",
            f"[0:v]split[v0][v1];[v1]crop=iw:2:0:{sb_h + 4},scale=iw:{sb_h}:flags=neighbor[fill];"
            f"[v0][fill]overlay=0:0[cov];[cov][1:v]overlay=0:0", str(clean)])

    # 2. animate it: recede, dim, spotlight, icon (pop, flip, land), flash
    rec = f"(1-0.12*{smooth('(t-0.3)/0.8')})"
    cw = int(W * 0.9) // 2 * 2
    ch = int(cw * 1500 / 900) // 2 * 2
    icon_y = int(H / 2 - ch * 1000 / 1500)
    gw = int(W * 1.5) // 2 * 2
    graph = (
        f"[0:v]format=rgba,scale=w='2*trunc(iw*{rec}/2)':h='2*trunc(ih*{rec}/2)':eval=frame[sc];"
        f"color=black:s={W}x{H}:r={FPS}:d={DUR}[bg];"
        f"[bg][sc]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame[a];"
        f"color=black:s={W}x{H}:r={FPS}:d={DUR},format=rgba,colorchannelmixer=aa=1,"
        f"fade=t=in:st=0.3:d=0.8:alpha=1[dim];[a][dim]overlay[b];"
        f"[1:v]format=rgba,scale={gw}:-1,fade=t=in:st=0.7:d=0.6:alpha=1[glow];"
        f"[b][glow]overlay=x=(W-w)/2:y=(H-h)/2[c];"
        f"[2:v]format=rgba,scale={cw}:{ch}[icon];"
        f"[c][icon]overlay=x=(W-w)/2:y={icon_y}:eof_action=repeat[d];"
        f"color=white:s={W}x{H}:r={FPS}:d={DUR},format=rgba,colorchannelmixer=aa=0.12,"
        f"fade=t=in:st=2.25:d=0.04:alpha=1,fade=t=out:st=2.29:d=0.35:alpha=1[fl];"
        f"[d][fl]overlay,format=yuv420p[out]"
    )
    screen = wd / "cutscene_screen.mp4"
    fp.run([fp.FFMPEG, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", str(DUR), "-i", str(clean),
            "-loop", "1", "-framerate", str(FPS), "-t", str(DUR), "-i", str(ASSETS / "glow.png"),
            "-i", str(ASSETS / "icon_layer.mov"),
            "-filter_complex", graph, "-map", "[out]", "-t", str(DUR), "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", str(screen)])

    return screen


def main():
    ap = argparse.ArgumentParser(description="SAVR cutscene from the last screen of a recording.")
    ap.add_argument("input", nargs="?", default="", help="screen recording (Enter/empty = newest in inbox/done)")
    ap.add_argument("--name", default="", help="short name for the video this ends, e.g. montage-A")
    ap.add_argument("--at", type=float, help="second of the recording to freeze on (default: where the framed video ends)")
    ap.add_argument("--trim-end", type=float, help="seconds the framed video cut off the end (default 0.5 for raw "
                                                   "ScreenRecording files, otherwise 0)")
    ap.add_argument("--time", default="9:41"); ap.add_argument("--network", default="5G")
    ap.add_argument("--battery", type=int, default=85)
    ap.add_argument("--statusbar-pct", type=float, default=None)
    ap.add_argument("--formats", default="", help="comma list of hevc,mp4,prores,webm (default: hevc+mp4 on Mac, mp4 elsewhere)")
    args = ap.parse_args()

    fp.FFMPEG, fp.FFPROBE = fp.tool("ffmpeg"), fp.tool("ffprobe")
    src = clean_path(args.input) or newest_recording()
    if not src.exists():
        sys.exit(f"Can't find {src}")
    src, from_framed = find_source(src)
    if not from_framed and paths.FRAMED_LOG.exists():
        import json
        from_framed = src.name in json.loads(paths.FRAMED_LOG.read_text()).values()
    W, H, fps, dur, _ = fp.probe(src)

    # the moment to freeze on
    if args.at is not None:
        at = min(max(0.0, args.at), dur - 0.05)
    else:
        trim = args.trim_end if args.trim_end is not None else \
            (0.5 if src.name.startswith("ScreenRecording") or from_framed else 0.0)
        at = max(0.0, dur - trim - 1.0 / fps)
    print(f"Freezing on {at:.2f}s of {src.name} ({dur:.2f}s long)")

    stamp = datetime.date.today().isoformat()
    typed = re.sub(r"[^A-Za-z0-9._-]+", "-", args.name.strip()).strip("-")
    if typed:
        name = f"{stamp}_{typed}"
    else:
        # Pair it with the framed video: 2026-11-07_framed01 -> 2026-11-07_framed01_cutscene_...
        import json
        log = json.loads(paths.FRAMED_LOG.read_text()) if paths.FRAMED_LOG.exists() else {}
        framed = [k for k, v in log.items() if v == src.name]
        if framed:
            name = framed[-1]
        else:
            prefix = f"{stamp}_cutscene"
            name = f"{prefix}{paths.next_number(paths.CUTSCENES, prefix):02d}"
    outdir = paths.CUTSCENES; outdir.mkdir(parents=True, exist_ok=True)
    formats = [f for f in args.formats.split(",") if f] or (["hevc", "mp4"] if platform.system() == "Darwin" else ["mp4"])
    bezel = next(iter(sorted((HERE / "bezels").glob("*.png"))), None)
    if not bezel:
        sys.exit("Put the Apple iPhone bezel PNG in bezels/ first.")

    with tempfile.TemporaryDirectory() as wd:
        wd = Path(wd)
        screen = screen_cutscene(src, at, wd, args)

        # 3. put it in the iPhone, same as the main video
        made = []
        label = {"hevc": "capcut", "prores": "capcut_prores", "webm": "web", "mp4": "descript"}
        for f in formats:
            fdir = wd / f"fmt_{f}"; fdir.mkdir()
            fp.run([sys.executable, str(HERE / "frame_phone.py"), str(screen), "--bezel", str(bezel),
                    "--out", str(fdir), "--statusbar", "none", "--format", f])
            res = next(fdir.iterdir())
            tail = "" if "_cutscene" in name else "_cutscene"
            final = outdir / f"{name}{tail}_{label.get(f, f)}{res.suffix}"
            shutil.move(str(res), final)
            made.append(final)

    print("\nDone:")
    for m in made:
        print(f"  {m.relative_to(paths.TOP)}")


if __name__ == "__main__":
    main()
