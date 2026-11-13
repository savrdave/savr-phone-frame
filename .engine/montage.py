"""Visuals for the jam tool: turns one voice take into a finished video.

The session plan (DROP HERE/plan.txt, written by Claude when planning the week) says
what each jam needs, one line per jam:
    07 | screen | Grocery prices are up 33% since 2020
    08 | broll  | Points aren't saving you money
    09 | headlines+screen | Every headline says prices went up
Types: broll (automatic), screen (your next screen recording), headlines (files in
DROP HERE/visuals starting with the jam number), or both with "+". The last part is
the hook shown big for the first 2.5 seconds. Screen recordings are matched to the
"screen" jams in the order you recorded them, so you never name anything.

For each jam it decides what's on screen, in this order:
  1. Files you dropped in DROP HERE/visuals whose names start with the jam number,
     in name order: "07 a headline.png", "07 b drawer.mov". Screen recordings get
     put in the iPhone frame automatically. Images get a slow zoom.
  2. A background set in jam_settings.json (old way, still works).
  3. Otherwise b-roll from the B-ROLL folder, picked to match what you said,
     a new clip about every 3.5 seconds.
Optional "07 hook.txt" puts that text big at the top for the first 2.5 seconds.
Then the SAVR cutscene goes on the end.
"""
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import paths

W, H, FPS = 1080, 1920, 30
GREEN = "0x2b4c2c"
VIDEO_EXT = {".mov", ".mp4", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".webp"}
VISUALS = paths.DROP / "visuals"
BROLL = paths.TOP / "B-ROLL"
DEFAULT_CUTSCENE = paths.ENGINE / "assets" / "cutscene_default.mp4"
CLIP_LEN = 3.5

# iPhone screen recording sizes (portrait)
PHONE_SIZES = {(1170, 2532), (1179, 2556), (1206, 2622), (1284, 2778), (1290, 2796), (1320, 2868),
               (1080, 2340), (1125, 2436), (1242, 2688), (1260, 2736)}

# words you might say -> words in b-roll file names
TOPICS = {
    "price tag": "price prices priced cost costs cheaper cheapest expensive sale deal deals dollars $ "
                 "inflation paying pay overpaying flyer flyers lower",
    "produce": "produce fruit fruits vegetables veggies bananas apples oranges carrots mushrooms fresh",
    "shelf": "shelf shelves aisle aisles store stores grocery groceries brand brands",
    "shelf grab": "buy buying bought grab list items item pantry staples",
    "frozen": "frozen freezer",
    "checkout": "checkout receipt till cashier total bill",
    "cart": "cart basket trip shopping shop",
    "food": "cook cooking meal meals prep dinner lunch recipe protein macros",
    "meat": "meat beef chicken protein",
}


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"Command failed: {' '.join(str(c) for c in cmd[:6])}...")
    return r.stdout


def probe(p):
    out = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)]))
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    for sd in v.get("side_data_list", []) or []:
        if abs(int(sd.get("rotation", 0) or 0)) in (90, 270):
            w, h = h, w
    return w, h, float(out["format"].get("duration", 0) or 0), v.get("color_transfer", "")


def jam_files(n):
    """Files for this jam: '07 ...', '7 ...', '07a ...', 'jam07 ...'."""
    found = []
    for folder in (VISUALS, paths.BACKGROUNDS):
        if not folder.exists():
            continue
        for f in folder.iterdir():
            m = re.match(r"(?:jam\s*)?0*(\d+)(?![\d])", f.stem.lower())
            if m and int(m.group(1)) == n:
                found.append(f)
    return sorted(found, key=lambda f: f.name.lower())


def read_plan():
    """{jam number: {"type": "screen", "hook": "..."}} from DROP HERE/plan.txt."""
    plan = {}
    p = paths.DROP / "plan.txt"
    if not p.exists():
        return plan
    for line in p.read_text().splitlines():
        parts = [x.strip() for x in line.split("|")]
        m = re.match(r"(?:jam\s*)?0*(\d+)$", parts[0].lower()) if parts else None
        if not m:
            continue
        plan[int(m.group(1))] = {"type": (parts[1].lower() if len(parts) > 1 else "broll"),
                                 "hook": parts[2] if len(parts) > 2 else ""}
    return plan


def assign_screens(jam_numbers, plan):
    """Screen recordings in DROP HERE/screen recordings, oldest first, go to the
    jams the plan marks 'screen', lowest jam number first."""
    folder = paths.SCREEN_IN
    recs = sorted([f for f in folder.iterdir() if f.suffix.lower() in VIDEO_EXT],
                  key=lambda f: f.stat().st_mtime) if folder.exists() else []
    wants = [n for n in sorted(jam_numbers) if "screen" in plan.get(n, {}).get("type", "")]
    out = dict(zip(wants, recs))
    for n in wants[len(recs):]:
        print(f"  jam {n:02d} needs a screen recording but there's none left, using b-roll")
    if len(recs) > len(wants) and wants:
        print(f"  {len(recs) - len(wants)} screen recording(s) left over, not used")
    return out


def hook_text(n, settings, plan=None):
    for f in jam_files(n):
        if f.suffix.lower() == ".txt":
            return f.read_text().strip()
    if plan and plan.get(n, {}).get("hook"):
        return plan[n]["hook"]
    return settings.get("jams", {}).get(str(n), {}).get("hook", "")


# ------------------------------------------------------------ segments

def hdr_fix(transfer):
    """iPhone video is HDR (HLG). Bring it to normal SDR so it isn't washed out."""
    if transfer in ("arib-std-b67", "smpte2084"):
        return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,")
    return ""


def has_zscale():
    if not hasattr(has_zscale, "v"):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], stdout=subprocess.PIPE, text=True)
        has_zscale.v = " zscale " in r.stdout
    return has_zscale.v


def enc(dest):
    return ["-an", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(dest)]


def seg_video(src, start, dur, dest, fill=True, hold_last=False):
    w, h, length, transfer = probe(src)
    fix = hdr_fix(transfer) if has_zscale() else ""
    fit = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}" if fill else
           f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={GREEN}")
    pre = []
    if length and start + dur > length:
        if hold_last:
            fit += f",tpad=stop_mode=clone:stop_duration={dur:.2f}"
        else:
            pre = ["-stream_loop", "-1"]
    run(["ffmpeg", "-y", "-v", "error", *pre, "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
         "-vf", f"{fix}{fit},fps={FPS},setsar=1", *enc(dest)])


def seg_image(src, dur, dest):
    """Tall images fill the screen; wide ones (headline screenshots) sit on SAVR green. Slow zoom."""
    from PIL import Image
    with Image.open(src) as im:
        w, h = im.size
    frames = int(dur * FPS) + 1
    zoom = (f"zoompan=z='min(1+0.0008*on,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={W}x{H}:fps={FPS}")
    if h / w > 1.5:
        vf = f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},{zoom}"
    else:
        vf = (f"scale={int(W*0.9)*2}:-2,pad={W*2}:{H*2}:(ow-iw)/2:(oh-ih)/2-{int(H*0.12)}:color={GREEN},{zoom}")
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(src),
         "-vf", vf + ",setsar=1", "-t", f"{dur:.3f}", *enc(dest)])


def is_screen_recording(p):
    if p.suffix.lower() not in VIDEO_EXT:
        return False
    w, h, _, _ = probe(p)
    return (w, h) in PHONE_SIZES or p.name.startswith(("ScreenRecording", "RPReplay"))


def framed(src, work):
    """Screen recording -> iPhone on SAVR green, 1080x1920 (cached)."""
    out_dir = work / "framed"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{src.stem}_framed.mp4"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        bezel = next(iter(sorted((paths.ENGINE / "bezels").glob("*.png"))), None)
        if not bezel:
            raise SystemExit("The iPhone bezel PNG is missing from .engine/bezels.")
        trim = ["--trim-start", "0.5", "--trim-end", "0.5"] if src.name.startswith("ScreenRecording") else []
        run([sys.executable, str(paths.ENGINE / "frame_phone.py"), str(src), "--bezel", str(bezel),
             "--out", str(out_dir), "--format", "mp4", *trim])
    return out


# --------------------------------------------------------------- b-roll

# ---------------------------------------------------------- Pexels stock
# Free stock footage (pexels.com licence: free to use, no credit needed).
# Put your free key in jam_settings.json as "pexels_key". Clips are fetched once
# per topic, trimmed to 6 seconds, and kept in B-ROLL/stock.
STOCK = BROLL / "stock"
SEARCHES = {
    "price tag": "grocery price tag",
    "produce": "grocery store produce",
    "shelf": "supermarket aisle",
    "shelf grab": "taking product from supermarket shelf",
    "frozen": "frozen food aisle supermarket",
    "checkout": "supermarket checkout",
    "cart": "shopping cart supermarket",
    "food": "meal prep cooking",
    "meat": "meat counter grocery",
    "receipt": "grocery receipt",
    "money": "counting money budget",
}
TOPICS.setdefault("receipt", "receipt receipts total bill paid")
TOPICS.setdefault("money", "money budget budgeting save saving savings afford rent paycheque")


def _curl(args):
    r = subprocess.run(["curl", "-sfL", "--max-time", "60", *args], stdout=subprocess.PIPE)
    return r.stdout if r.returncode == 0 else None


def _search(provider, key, topic):
    """[(id, url)] for one topic. Vertical clips first, then wide ones (cropped to 9:16)."""
    q = SEARCHES[topic].replace(" ", "+")
    if provider == "pixabay":
        raw = _curl([f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page=30&safesearch=true"])
        if not raw:
            return None
        found = []
        for v in json.loads(raw).get("hits", []):
            sizes = [s for s in (v.get("videos") or {}).values() if s.get("url") and s.get("width")]
            if not sizes:
                continue
            tall = [s for s in sizes if s["height"] > s["width"]]
            pool = tall or sizes
            best = min(pool, key=lambda s: abs(max(s["height"], s["width"]) - 1920))
            found.append((bool(tall), v["id"], best["url"]))
        found.sort(key=lambda x: not x[0])
        return [(i, u) for _, i, u in found]
    raw = _curl(["-H", f"Authorization: {key}",
                 f"https://api.pexels.com/videos/search?query={q}&orientation=portrait&size=medium&per_page=15"])
    if not raw:
        return None
    found = []
    for v in json.loads(raw).get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("height") and f.get("width")
                 and f["height"] > f["width"] and f["height"] >= 1280]
        if files:
            found.append((v["id"], min(files, key=lambda f: abs(f["height"] - 1920))["link"]))
    return found


def fetch_stock(topics, key, per_topic=4, provider="pixabay"):
    STOCK.mkdir(parents=True, exist_ok=True)
    done_file = STOCK / ".fetched.json"
    done = json.loads(done_file.read_text()) if done_file.exists() else {}
    for topic in topics:
        if f"{provider}:{topic}" in done or topic not in SEARCHES:
            continue
        results = _search(provider, key, topic)
        if results == []:  # small library: retry with simpler searches
            for simpler in (" ".join(SEARCHES[topic].split()[-2:]), "grocery", "supermarket"):
                SEARCHES["_retry"] = simpler
                results = _search(provider, key, "_retry")
                if results:
                    break
        if results is None:
            print(f"  Couldn't reach {provider} for '{topic}' (offline or bad key), using what's here")
            continue
        got = 0
        for vid, link in results:
            dest = STOCK / f"{topic} - {provider} {vid}.mp4"
            best = {"link": link}
            if not dest.exists():
                tmp = STOCK / f".dl_{vid}.mp4"
                data = _curl([best["link"]])
                if not data:
                    continue
                tmp.write_bytes(data)
                try:
                    run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(tmp), "-t", "6",
                         "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
                         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                         str(dest)])
                except SystemExit:
                    pass
                tmp.unlink(missing_ok=True)
            got += 1
            if got >= per_topic:
                break
        done[f"{provider}:{topic}"] = got
        done_file.write_text(json.dumps(done, indent=1))
        print(f"  {provider}: {got} clip(s) for '{topic}'")


def topics_for(text):
    said = set(re.findall(r"[a-z$]+", text.lower()))
    hits = [t for t, words in TOPICS.items() if said & set(words.split())]
    return hits or ["shelf", "price tag", "cart"]


def broll_pick(text, total, seed, settings=None):
    settings = settings or {}
    if settings.get("pixabay_key"):
        fetch_stock(topics_for(text), settings["pixabay_key"], provider="pixabay")
    elif settings.get("pexels_key"):
        fetch_stock(topics_for(text), settings["pexels_key"], provider="pexels")
    clips = [f for f in BROLL.rglob("*") if f.suffix.lower() in VIDEO_EXT and not f.name.startswith(".")
             and not f.name.lower().startswith("face")] if BROLL.exists() else []
    if not clips:
        return []
    said = set(re.findall(r"[a-z$]+", text.lower()))
    def score(f):
        name = f.stem.lower()
        s = sum(2 for w in re.findall(r"[a-z]+", name) if len(w) > 3 and w in said)
        if ("pexels" in name or "pixabay" in name) and settings.get("prefer_stock", True):
            s += 1  # your own clips aren't great yet, so stock wins ties
        for topic, words in TOPICS.items():
            if name.startswith(topic) and said & set(words.split()):
                s += 1
        return s
    rng = random.Random(seed)
    rng.shuffle(clips)
    clips.sort(key=score, reverse=True)
    n = max(1, round(total / CLIP_LEN))
    # best matches first, but mixed up so the video doesn't go topic by topic
    pool = clips[:max(n, 6)]
    rng.shuffle(pool)
    return [pool[i % len(pool)] for i in range(n)]


# ----------------------------------------------------------------- build

def build_background(n, total, text, settings, work, screen=None):
    """Returns (video path or None, last screen recording used or None, description)."""
    files = [f for f in jam_files(n) if f.suffix.lower() in VIDEO_EXT | IMAGE_EXT]
    if screen:
        files.append(screen)
    if not files:
        if settings.get("jams", {}).get(str(n), {}).get("background") or settings.get("default_background"):
            return None, None, "background from settings"
        if settings.get("auto_broll", True):
            files = broll_pick(text, total, seed=n, settings=settings)
            if not files:
                return None, None, "SAVR green (B-ROLL folder is empty)"
            mode = "broll"
        else:
            return None, None, "SAVR green"
    else:
        mode = "visuals"
    segdir = work / "segments"
    if segdir.exists():
        shutil.rmtree(segdir)
    segdir.mkdir(parents=True)
    share = total / len(files)
    parts, last_rec = [], None
    for k, f in enumerate(files):
        dur = share if k < len(files) - 1 else total - share * k
        dest = segdir / f"{k:03d}.mp4"
        if f.suffix.lower() in IMAGE_EXT:
            seg_image(f, dur, dest)
            last_rec = None
        elif f == screen or is_screen_recording(f):
            seg_video(framed(f, work), 0, dur, dest, fill=False, hold_last=True)
            last_rec = f
        else:
            _, _, length, _ = probe(f)
            start = max(0.0, (length - dur) / 2) if mode == "broll" else 0.0
            seg_video(f, start, dur, dest, fill=True, hold_last=False)
            last_rec = None
        parts.append(dest)
    lst = segdir / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = work / "visuals.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    names = ", ".join(f.stem for f in files[:4]) + (" ..." if len(files) > 4 else "")
    return out, last_rec, f"{mode}: {names}"


def hook_png(text, work):
    from PIL import Image, ImageDraw, ImageFont
    import jam
    font_path = jam.find_font()
    size = 104
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    while True:
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textlength(t, font=font) > W * 0.86 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = t
        lines.append(cur)
        if len(lines) <= 3 or size <= 64:
            break
        size -= 8
    y = int(H * 0.13)
    for line in lines:
        box = d.textbbox((0, 0), line, font=font, stroke_width=10)
        x = (W - (box[2] - box[0])) / 2 - box[0]
        d.text((x, y - box[1]), line, font=font, fill="white", stroke_width=10, stroke_fill="black")
        y += int(size * 1.15)
    p = work / "hook.png"
    img.save(p)
    return p


def cutscene_for(last_rec, work):
    """The end card: freezes on your last screen if the video ended on a screen recording."""
    if last_rec is not None:
        out_dir = work / "cutscene"
        if out_dir.exists():
            shutil.rmtree(out_dir)
        try:
            r = subprocess.run([sys.executable, str(paths.ENGINE / "make_cutscene.py"), str(last_rec),
                                "--formats", "mp4", "--name", f"jamtmp-{work.name}"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            made = sorted(paths.CUTSCENES.glob(f"*jamtmp-{work.name}*_descript.mp4"))
            if r.returncode == 0 and made:
                out_dir.mkdir()
                dest = out_dir / "cutscene.mp4"
                shutil.move(str(made[-1]), dest)
                return dest
        except Exception:
            pass
    return DEFAULT_CUTSCENE if DEFAULT_CUTSCENE.exists() else None


def append_cutscene(video, cutscene, dest):
    graph = (f"[0:v]fps={FPS},scale={W}:{H},setsar=1,format=yuv420p[v0];"
             f"[1:v]fps={FPS},scale={W}:{H},setsar=1,format=yuv420p[v1];"
             f"anullsrc=r=48000:cl=stereo,atrim=0:{probe(cutscene)[2]:.3f}[a1];"
             f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];"
             f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(cutscene), "-filter_complex", graph,
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)])
