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


def read_plan(strict=False):
    """DROP HERE/plan.txt, one line per jam:
        07 | screen | Grocery prices are up 33% since 2020
        08 | broll | Points aren't saving you money | loyalty card checkout; receipt closeup
        09 | headlines2+screen | Every headline says prices went up
    -> {7: {"type": "screen", "hook": "...", "searches": [], "headlines": 0}, ...}
    The 4th part (b-roll searches, separated by ;) is written by Claude when planning.
    'headlines2' = the next 2 screenshots go to this jam ('headlines' alone = 1)."""
    plan, bad = {}, []
    p = paths.DROP / "plan.txt"
    if not p.exists():
        return (plan, bad) if strict else plan
    for line in p.read_text().splitlines():
        raw = line.strip().lstrip("-*").strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [x.strip() for x in raw.split("|")]
        if parts and parts[0] == "":
            parts = parts[1:]
        m = re.match(r"(?:jam\s*)?0*(\d+)$", parts[0].lower()) if parts else None
        if not m:
            bad.append(line)
            continue
        if len(parts) > 4:  # a | inside the hook: everything but the last part is the hook
            parts = parts[:2] + [" | ".join(parts[2:-1]), parts[-1]]
        kind = parts[1].lower().replace(" ", "") if len(parts) > 1 and parts[1] else "broll"
        h = re.search(r"headlines?:?(\d*)", kind)
        searches = parts[3] if len(parts) > 3 else ""
        searches = re.sub(r"^(stock|broll|b-roll)\s*:\s*", "", searches, flags=re.I)
        plan[int(m.group(1))] = {"type": kind, "hook": parts[2] if len(parts) > 2 else "",
                                 "searches": [q.strip() for q in searches.split(";") if q.strip()],
                                 "headlines": (int(h.group(1) or 1) if h else 0)}
    return (plan, bad) if strict else plan


def taken_order(f):
    """When a file was recorded or taken, not when it was copied over (AirDrop
    resets that). Video: creation_time tag. Image: EXIF date, else the IMG_ number."""
    try:
        if f.suffix.lower() in VIDEO_EXT:
            out = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
                                  "-of", "json", str(f)]))
            t = out.get("format", {}).get("tags", {}).get("creation_time")
            if t:
                return (0, t, f.name)
        else:
            from PIL import Image
            with Image.open(f) as im:
                t = (im.getexif().get(36867) or im.getexif().get(306) or "")
            if t:
                t = t.replace(":", "-", 2).replace(" ", "T")
                return (0, t, f.name)
    except Exception:
        pass
    # Mac: "Screenshot 2026-11-13 at 9.15.02 AM" / "Screen Recording 2026-11-13 at 1.10.05 PM"
    m = re.search(r"(\d{4}-\d{2}-\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})\s*([AP]M)?", f.stem, re.I)
    if m:
        h = int(m.group(2))
        if m.group(5):
            h = h % 12 + (12 if m.group(5).upper() == "PM" else 0)
        return (0, f"{m.group(1)}T{h:02d}:{m.group(3)}:{m.group(4)}", f.name)
    m = re.match(r"IMG_(\d+)", f.stem, re.I)
    if m:
        return (1, m.group(1).zfill(12), f.name)
    st = f.stat()
    return (2, f"{getattr(st, 'st_birthtime', st.st_mtime):020.3f}", f.name)


def _queued(folder, exts):
    return sorted([f for f in folder.iterdir() if f.suffix.lower() in exts and not f.name.startswith(".")],
                  key=taken_order) if folder.exists() else []


def assign_screens(jam_numbers, plan):
    """Screen recordings, in the order recorded, go to the jams the plan marks
    'screen', lowest jam number first."""
    recs = _queued(paths.SCREEN_IN, VIDEO_EXT)
    wants = [n for n in sorted(jam_numbers) if "screen" in plan.get(n, {}).get("type", "")]
    out = dict(zip(wants, recs))
    for n in wants[len(recs):]:
        print(f"  jam {n:02d} needs a screen recording but there's none left, using b-roll")
    if len(recs) > len(wants) and wants:
        print(f"  {len(recs) - len(wants)} screen recording(s) left over, not used")
    return out


def assign_shots(jam_numbers, plan):
    """Screenshots, in the order taken, go to the jams the plan marks 'headlines'."""
    shots = _queued(paths.SHOTS_IN, IMAGE_EXT)
    out, k = {}, 0
    for n in sorted(jam_numbers):
        want = plan.get(n, {}).get("headlines", 0)
        if want:
            out[n] = shots[k:k + want]
            if len(out[n]) < want:
                print(f"  jam {n:02d} wanted {want} screenshot(s), only {len(out[n])} left")
            k += want
    if k < len(shots) and k:
        print(f"  {len(shots) - k} screenshot(s) left over, not used")
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

# ----------------------------------------------------------- stock b-roll
# Free stock footage from Pixabay (Pexels as a fallback). Key goes in
# .engine/keys.json as "pixabay_key" (never uploaded). Clips are trimmed to 6
# seconds and kept in B-ROLL/stock. Best case: plan.txt names the searches for
# each jam (Claude writes them when planning), so footage fits what's said.
STOCK = BROLL / "stock"
USED = STOCK / ".used.json"   # clip -> date last used, so videos don't repeat footage
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


def _search(provider, key, topic, page=1):
    """[(id, url)] for one topic. Vertical clips first, then wide ones (cropped to 9:16)."""
    q = (SEARCHES.get(topic) or topic).replace(" ", "+")
    if provider == "pixabay":
        raw = _curl([f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page=50&page={page}"
                     f"&safesearch=true&video_type=film"])
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
            if dest.exists() or _download(link, dest):
                got += 1
            if got >= per_topic:
                break
        if got:  # an empty or failed fetch gets retried next time
            done[f"{provider}:{topic}"] = got
            done_file.write_text(json.dumps(done, indent=1))
        print(f"  {provider}: {got} clip(s) for '{topic}'")


def _slug(q):
    return re.sub(r"[^a-z0-9]+", " ", q.lower()).strip()[:40]


def _download(link, dest):
    tmp = dest.with_name(f".dl_{dest.stem}.mp4")
    data = _curl([link])
    if not data:
        return False
    tmp.write_bytes(data)
    try:
        run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(tmp), "-t", "6",
             "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", str(dest)])
    except SystemExit:
        dest.unlink(missing_ok=True)
    finally:
        tmp.unlink(missing_ok=True)
    # broken or tiny downloads (a 26 KB clip turned up on disk) are thrown away
    if dest.exists() and (dest.stat().st_size < 150_000 or probe(dest)[2] < 2.5):
        dest.unlink()
    return dest.exists()


def stock_for(searches, need, settings, avoid):
    """Clips for the searches Claude put in plan.txt: up to `need` in total, fresh
    ones fetched when the folder doesn't have enough unused ones."""
    key = settings.get("pixabay_key")
    provider = "pixabay" if key else ("pexels" if settings.get("pexels_key") else None)
    key = key or settings.get("pexels_key")
    STOCK.mkdir(parents=True, exist_ok=True)
    per = max(2, -(-need // len(searches)) + 1)
    picked, warn, taken = [], [], set()
    for q in searches:
        slug = _slug(q)
        have = sorted(f for f in STOCK.glob(f"q {slug} - *.mp4") if f not in avoid and f not in taken)
        if len(have) < per and provider:
            results = _search(provider, key, q) or []
            if not results and len(q.split()) > 2:
                results = _search(provider, key, " ".join(q.split()[-2:])) or []
            for vid, link in results:
                if len(have) >= per:
                    break
                same = [f for f in STOCK.glob(f"* - {provider} {vid}.mp4")]  # same video, any search
                dest = same[0] if same else STOCK / f"q {slug} - {provider} {vid}.mp4"
                if dest.exists() or _download(link, dest):
                    if dest not in have and dest not in avoid and dest not in taken:
                        have.append(dest)
        if not have:
            warn.append(f"no stock found for '{q}'")
        taken.update(have[:per])
        picked.append(have[:per])
    if not provider:
        warn.append("no pixabay_key in .engine/keys.json, using clips already downloaded")
    # Blocks in plan order (Claude lists searches in the order the script goes),
    # each search getting its share; short ones are topped up from the others.
    base, extra = divmod(need, len(searches))
    quota = [base + (1 if k < extra else 0) for k in range(len(searches))]
    out = [c for g, q in zip(picked, quota) for c in g[:q]]
    spare = [c for g, q in zip(picked, quota) for c in g[q:]]
    out += spare[:need - len(out)]
    return out, warn


def topics_for(text):
    said = set(re.findall(r"[a-z$]+", text.lower()))
    hits = [t for t, words in TOPICS.items() if said & set(words.split())]
    return hits or ["shelf", "price tag", "cart"]


def recently_used(days=14):
    import datetime
    log = json.loads(USED.read_text()) if USED.exists() else {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return {STOCK / k for k, d in log.items() if d >= cutoff}


def mark_used(clips):
    import datetime
    log = json.loads(USED.read_text()) if USED.exists() else {}
    for c in clips:
        if c.parent == STOCK:
            log[c.name] = datetime.date.today().isoformat()
    STOCK.mkdir(parents=True, exist_ok=True)
    USED.write_text(json.dumps(log, indent=1))


def broll_pick(text, total, seed, settings=None, searches=None, need=None, avoid=()):
    """Returns (clips, warnings)."""
    settings = settings or {}
    need = need or max(1, round(total / CLIP_LEN))
    avoid = set(avoid) | recently_used()
    if searches:
        clips, warn = stock_for(searches, need, settings, avoid)
        if len(clips) >= need:
            return clips, warn
        if clips:
            return [clips[i % len(clips)] for i in range(need)], warn + ["not enough stock, some clips repeat"]
    else:
        warn = ["plan.txt has no b-roll searches for this jam, guessed from the words (expect weaker footage)"]
    # Old way: guess topics from the words, pick by file name.
    if settings.get("pixabay_key"):
        fetch_stock(topics_for(text), settings["pixabay_key"], provider="pixabay")
    elif settings.get("pexels_key"):
        fetch_stock(topics_for(text), settings["pexels_key"], provider="pexels")
    own = settings.get("use_own_broll", False)
    clips = [f for f in BROLL.rglob("*") if f.suffix.lower() in VIDEO_EXT and not f.name.startswith(".")
             and not f.name.lower().startswith("face") and (own or f.parent == STOCK)] if BROLL.exists() else []
    fresh = [c for c in clips if c not in avoid]
    clips = fresh if len(fresh) >= need else clips
    if not clips:
        return [], warn
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
    n = need
    # best matches first, but mixed up so the video doesn't go topic by topic
    pool = clips[:max(n, 6)]
    rng.shuffle(pool)
    return [pool[i % len(pool)] for i in range(n)], warn


# ----------------------------------------------------------------- build

def build_background(n, total, text, settings, work, screen=None, shots=(), entry=None, durs=None, used=None):
    """Returns (video path or None, last screen recording used or None, description, warnings).
    Order on screen: files named with the jam number, then headline screenshots,
    then the screen recording. Nothing given -> b-roll."""
    entry, used = entry or {}, used if used is not None else set()
    warn = []
    files = [f for f in jam_files(n) if f.suffix.lower() in VIDEO_EXT | IMAGE_EXT]
    files += list(shots)
    if screen:
        files.append(screen)
    if "screen" in entry.get("type", "") and not screen:
        warn.append("plan wanted a screen recording, none was left")
    if entry.get("headlines") and len(shots) < entry["headlines"]:
        warn.append(f"plan wanted {entry['headlines']} screenshot(s), got {len(shots)}")
    if not files:
        if settings.get("jams", {}).get(str(n), {}).get("background") or settings.get("default_background"):
            return None, None, "background from settings", warn
        if settings.get("auto_broll", True):
            need = len(durs) if durs else None
            files, w = broll_pick(text, total, seed=n, settings=settings,
                                  searches=entry.get("searches"), need=need, avoid=used)
            warn += w
            if not files:
                return None, None, "SAVR green (no b-roll found)", warn + ["no b-roll at all, plain green"]
            used.update(files)
            mark_used(files)
            mode = "broll"
        else:
            return None, None, "SAVR green", warn
    else:
        mode = "visuals"
    segdir = work / "segments"
    if segdir.exists():
        shutil.rmtree(segdir)
    segdir.mkdir(parents=True)
    if mode == "broll" and durs and len(durs) == len(files):
        lengths = durs
    elif screen and len(files) > 1:
        # headlines/images flash by (up to 2.5s each), the app gets the rest
        each = min(2.5, total * 0.5 / (len(files) - 1))
        lengths = [each] * (len(files) - 1) + [total - each * (len(files) - 1)]
    else:
        lengths = [total / len(files)] * len(files)
    parts, last_rec = [], None
    for k, f in enumerate(files):
        dur = lengths[k] if k < len(files) - 1 else total - sum(lengths[:-1])
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
    return out, last_rec, f"{mode}: {names}", warn


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
