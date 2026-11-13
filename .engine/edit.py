"""Two-stage jams: Transcribe -> Claude edits -> Render.

Stage 1 (Make Jam, no edits yet): every voice memo in DROP HERE is transcribed,
split into jams, and written up for Claude in DROP HERE/claude/:
    transcript.md   every jam, numbered words, unclear audio marked, plan lines,
                    every file you dropped (with lengths) and the edit format
    drop.png        thumbnails of your screen recordings and screenshots
    broll.png       thumbnails of your own B-ROLL clips
Stage 2 (Make Jam again, once DROP HERE/claude/edits.json exists): Claude's
edits say what to cut, which words to fix, and which visual starts on which
word. Because edits point at the words you actually said, the video always
lines up, however far you went off the plan.

No Claude available: Make Jam --auto renders with simple automatic edits
(ums, "zebra" restarts, stutters) and guessed visuals.
"""
import datetime
import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np

import jam
import montage
import paths
from jam import SR, norm, read_wav, write_wav, splice

CLAUDE_DIR = paths.DROP / "claude"
CUT_GAP = 0.15   # seconds of silence left where something was cut out
EDITS = CLAUDE_DIR / "edits.json"
NUMBER_WORDS = {"number", "no", "num"}


# ------------------------------------------------------------- markers

def find_jams(words, tl, plan):
    """Strict jam markers. 'jam 7', 'jam seven', 'jam number 7', 'jam #7'.
    A marker needs a pause before it (so 'strawberry jam for $6' isn't one).
    The number is never skipped twice, so 'Jam four. For years...' keeps 'For'.
    Returns (markers [(n, i_marker, i_body)], notes)."""
    out, notes = [], []
    i = 0
    while i < len(words):
        t = norm(words[i]["w"])
        n, used = None, 1
        m = re.fullmatch(r"(?:jam|jem|gem|jan)#?(\d+)", t)
        if m:
            n = int(m.group(1))
        elif t in jam.JAM_WORDS | {"jan"}:
            k = i + 1
            if k < len(words) and norm(words[k]["w"]) in NUMBER_WORDS:
                k += 1
            num, u = jam.read_number(words, k)
            if num is not None:
                n, used = num, k - i + u
        if n is not None:
            pause = tl.to_orig(words[i]["s"]) - tl.to_orig(words[i - 1]["e"]) if i > 0 else 9.0
            expected = bool(plan) and n in plan
            if i == 0 or pause >= 0.3 or (expected and pause >= 0.12):
                if plan and n not in plan:
                    notes.append(f"heard 'jam {n}' but jam {n} isn't in plan.txt (treated as a jam anyway)")
                out.append((n, i, min(i + used, len(words))))
                i += used
                continue
            notes.append(f"'{' '.join(w['w'] for w in words[i:i + used])}' at {words[i]['s']:.0f}s "
                         "had no pause before it, so it was NOT treated as a jam marker")
        i += 1
    if out and out[0][1] > 5:
        notes.append(f"{out[0][1]} words before the first jam marker were ignored")
    return out, notes


# ------------------------------------------------------- coverage gaps

def frame_db(x, hop=0.01):
    h = int(hop * SR)
    n = len(x) // h
    rms = np.sqrt(np.mean(x[:n * h].reshape(n, h) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms)


def unclear_tokens(words, x, a, b, thr, min_len=0.6):
    """Speech in [a, b] that no word covers (Whisper skipped it) -> tokens."""
    db = frame_db(x)
    lo, hi = int(a / 0.01), min(int(b / 0.01), len(db))
    voiced = db[lo:hi] > thr
    covered = np.zeros_like(voiced)
    for w in words:
        s, e = int(w["s"] / 0.01) - lo - 5, int(w["e"] / 0.01) - lo + 5
        covered[max(0, s):max(0, e)] = True
    gaps, start = [], None
    for k, v in enumerate(voiced & ~covered):
        if v and start is None:
            start = k
        elif not v and start is not None:
            if (k - start) * 0.01 >= min_len:
                gaps.append((lo + start, lo + k))
            start = None
    if start is not None and (len(voiced) - start) * 0.01 >= min_len:
        gaps.append((lo + start, hi))
    return [{"w": f"[unclear {(e - s) * 0.01:.1f}s]", "s": s * 0.01, "e": e * 0.01, "gap": True}
            for s, e in gaps]


# ---------------------------------------------------------- stage 1

def transcribe_memo(src, s, plan):
    work, tight, tl = jam.prepare(src, s)
    joins = [float(j) for j in tl.offsets[1:-1]]
    words = jam.transcribe(tight, work / "words.json", s["whisper_model"], joins, work)
    x = read_wav(tight)
    markers, notes = find_jams(words, tl, plan)
    if not markers:
        markers = [(1, 0, 0)]
        notes.append("no 'jam N' markers heard, the whole memo is jam 1")
    _, thr = jam.speech_intervals(read_wav(work / "full.wav"), s)
    jams, seen = {}, {}
    for k, (n, mi, bi) in enumerate(markers):
        end_i = markers[k + 1][1] if k + 1 < len(markers) else len(words)
        body = words[bi:end_i]
        if len(body) < 3:
            notes.append(f"jam {n} has almost nothing in it ({len(body)} words), skipped")
            continue
        a = max(0.0, body[0]["s"] - 0.15)
        b = words[end_i]["s"] - 0.05 if end_i < len(words) else len(x) / SR
        # Re-listen to this jam on its own: Whisper skips less on short clips.
        cache = work / f"jam{n:02d}_{k}_words.json"
        again = None
        if cache.exists() and cache.stat().st_mtime > tight.stat().st_mtime:
            again = json.loads(cache.read_text())
        else:
            try:
                clip = work / "slice.wav"
                write_wav(clip, x[int(a * SR):int(b * SR)])
                again = jam.whisper_words(jam.get_model(s["whisper_model"]), clip, a)
                cache.write_text(json.dumps(again))
            except (Exception, SystemExit) as err:
                notes.append(f"jam {n}: couldn't re-check the transcript on its own ({str(err)[:80]}), "
                             "using the first pass")
                again = None
        if again and len(again) >= 0.9 * len(body):
            body = [w for w in again if w["s"] >= a - 0.05]
        body = sorted(body + unclear_tokens(body, x, a, b, thr), key=lambda w: w["s"])
        if n in seen:
            notes.append(f"jam {n} was recorded twice; the later one is kept")
        seen[n] = True
        jams[str(n)] = body
    h = hashlib.sha1(json.dumps(jams, sort_keys=True).encode()).hexdigest()[:10]
    session = {"memo": src.name, "hash": h, "jams": jams, "notes": notes}
    (work / "session.json").write_text(json.dumps(session))
    return session


def phrases(words, gap=0.35):
    """[(first index, last index)] split on pauses and sentence ends, for reading."""
    out, start = [], 0
    for i in range(1, len(words)):
        prev = words[i - 1]
        if words[i]["s"] - prev["e"] >= gap or prev["w"].rstrip()[-1:] in ".?!" or prev.get("gap") \
                or words[i].get("gap"):
            out.append((start, i - 1))
            start = i
    out.append((start, len(words) - 1))
    return out


def inventory():
    """Every visual file waiting in DROP HERE, in the order recorded/taken."""
    rows = []
    for label, folder, exts in (("screen recording", paths.SCREEN_IN, montage.VIDEO_EXT),
                                ("screenshot", paths.SHOTS_IN, montage.IMAGE_EXT),
                                ("numbered visual", montage.VISUALS, montage.VIDEO_EXT | montage.IMAGE_EXT)):
        for f in montage._queued(folder, exts):
            dur = montage.probe(f)[2] if f.suffix.lower() in montage.VIDEO_EXT else None
            rows.append((label, f, dur))
    return rows


def own_broll():
    if not montage.BROLL.exists():
        return []
    return sorted(f for f in montage.BROLL.rglob("*") if f.suffix.lower() in montage.VIDEO_EXT
                  and montage.STOCK not in f.parents and not f.name.startswith("."))


def contact_sheet(items, dest, frames=3, tw=180):
    """Grid of thumbnails with the file name under each, so Claude can see them."""
    from PIL import Image, ImageDraw
    if not items:
        return None
    th = int(tw * 16 / 9)
    cells = []
    tmp = CLAUDE_DIR / ".thumbs"
    tmp.mkdir(parents=True, exist_ok=True)
    for f in items:
        shots = []
        try:
            if f.suffix.lower() in montage.IMAGE_EXT:
                shots = [Image.open(f).convert("RGB")]
            else:
                dur = montage.probe(f)[2] or 1
                for k in range(frames):
                    p = tmp / f"t{len(cells)}_{k}.jpg"
                    jam.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * (k + 0.5) / frames:.2f}", "-i", str(f),
                             "-frames:v", "1", "-vf", f"scale={tw}:-2", str(p)])
                    shots.append(Image.open(p).convert("RGB"))
        except (Exception, SystemExit):
            pass
        cells.append((f.name, [im.resize((tw, th)) for im in shots]))
    per_row = max(1, 1200 // (tw * frames + 20))
    cw, ch = tw * frames + 20, th + 40
    rows = -(-len(cells) // per_row)
    sheet = Image.new("RGB", (cw * per_row, ch * rows), "white")
    d = ImageDraw.Draw(sheet)
    for k, (name, shots) in enumerate(cells):
        x0, y0 = (k % per_row) * cw, (k // per_row) * ch
        for j, im in enumerate(shots):
            sheet.paste(im, (x0 + j * tw, y0))
        d.text((x0 + 2, y0 + th + 4), name[:60], fill="black")
    sheet.save(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


EDIT_HELP = """
## Edit format (Claude writes DROP HERE/claude/edits.json)

{"memos": {"<memo file name>": {"hash": "<hash above>", "jams": {
  "7": {"cut": [[0, 4], [20, 22]],          word numbers to remove, inclusive
        "fix": {"31": "Comment"},           caption spelling fixes by word number
        "hook": "Short hook headline",      big text, first 2.5 seconds
        "visuals": [                         each starts at a word number, runs to the next
          {"at": 0, "file": "IMG_0001.PNG"},
          {"at": 12, "file": "ScreenRecording_....mov", "start": 1.5},
          {"at": 30, "stock": "supermarket checkout"},
          {"at": 40, "broll": "price tag - mushrooms.mov"}],
        "end_card": false,                  SAVR end card after the video (default off)
        "skip": false}}}}}

Always removed without listing them: ums/uhs, the restart word, and [unclear] pieces
listed in "cut". [unclear] pieces not cut are KEPT (they may be real speech Whisper missed).
"""


def write_pack(sessions, plan, s):
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    cue = s.get("restart_word", "zebra")
    out = [f"# Jam session for Claude ({datetime.date.today().isoformat()})",
           f"Restart word: '{cue}'. Voice: {s['voice']}.\n"]
    for sess in sessions:
        out.append(f"## {sess['memo']}   hash: {sess['hash']}")
        for note in sess["notes"]:
            out.append(f"NOTE: {note}")
        for n, ws in sorted(sess["jams"].items(), key=lambda kv: int(kv[0])):
            secs = ws[-1]["e"] - ws[0]["s"]
            p = plan.get(int(n))
            pl = f"   plan: {p['type']} | {p['hook']}" + (f" | {'; '.join(p['searches'])}" if p["searches"] else "") if p else \
                "   (not in plan.txt)"
            out.append(f"\n### jam {int(n):02d}  ({secs:.0f}s){pl}")
            for a, b in phrases(ws):
                out.append(f"[{a}-{b}] " + " ".join(w["w"] for w in ws[a:b + 1]))
        out.append("")
    missing = sorted(set(plan) - {int(n) for sess in sessions for n in sess["jams"]})
    if missing:
        out.append(f"NOTE: in plan.txt but not heard: jam {', '.join(map(str, missing))}\n")
    out.append("## Files waiting in DROP HERE (in the order recorded or taken)")
    inv = inventory()
    for label, f, dur in inv:
        out.append(f"- {label}: {f.name}" + (f"  ({dur:.1f}s)" if dur else ""))
    if not inv:
        out.append("- none")
    own = own_broll()
    out.append(f"\n## Your own B-ROLL clips ({len(own)}), see broll.png")
    out += [f"- {f.relative_to(montage.BROLL)}" for f in own]
    out.append(EDIT_HELP)
    (CLAUDE_DIR / "transcript.md").write_text("\n".join(out))
    contact_sheet([f for _, f, _ in inv], CLAUDE_DIR / "drop.png")
    sheet = CLAUDE_DIR / "broll.png"
    if own and (not sheet.exists() or max(f.stat().st_mtime for f in own) > sheet.stat().st_mtime):
        contact_sheet(own, sheet, frames=1, tw=140)
    return CLAUDE_DIR / "transcript.md"


# ---------------------------------------------------------- stage 2

def quietest(x, a, b):
    """Time of the quietest 10 ms in [a, b], so cuts land between sounds."""
    a, b = max(0.0, a), min(len(x) / SR, b)
    if b - a < 0.02:
        return a
    db = frame_db(x[int(a * SR):int(b * SR)])
    return a + float(np.argmin(db)) * 0.01 + 0.005


def auto_cuts(ws, cue):
    """Without Claude: each restart word cuts back to the start of its sentence;
    exact one-word stutters ('I I') go."""
    cut = []
    for c, w in enumerate(ws):
        if norm(w["w"]) == cue:
            k = c - 1
            while k > 0 and not (ws[k]["s"] - ws[k - 1]["e"] >= 0.35 or ws[k - 1]["w"].rstrip()[-1:] in ".?!"):
                k -= 1
            cut.append([max(0, k), c])
    for i in range(len(ws) - 1):
        a, b = norm(ws[i]["w"]), norm(ws[i + 1]["w"])
        if a and a == b and a not in jam.NO_STUTTER and ws[i + 1]["s"] - ws[i]["e"] < 0.25:
            cut.append([i, i])
    return cut


def plan_audio(ws, x, e, cue):
    """Kept words -> audio intervals (cut points snapped to quiet) + caption words."""
    drop = set()
    for a, b in e.get("cut", []):
        drop.update(range(int(a), int(b) + 1))
    for i, w in enumerate(ws):
        if not w.get("gap") and (norm(w["w"]) in jam.FILLERS or norm(w["w"]) == cue):
            drop.add(i)
    keep = [i for i in range(len(ws)) if i not in drop]
    if not keep:
        raise ValueError("every word was cut")
    runs, cur = [], [keep[0]]
    for i in keep[1:]:
        if i == cur[-1] + 1:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    iv = []
    gap = float(e.get("cut_gap", CUT_GAP))
    for k, r in enumerate(runs):
        s0, e0 = ws[r[0]]["s"], ws[r[-1]]["e"]
        # At a cut, keep only a breath of air on each side (about `gap` in total);
        # at the very start and end of the jam, leave a little more room.
        a = quietest(x, s0 - (0.18 if k == 0 else gap * 0.45), s0 - 0.01)
        b = quietest(x, e0 + 0.02, e0 + (0.25 if k == len(runs) - 1 else gap * 0.55))
        if iv and a <= iv[-1][1]:
            iv[-1][1] = max(iv[-1][1], b)
        else:
            iv.append([a, b])
    fixes = {int(k): v for k, v in e.get("fix", {}).items()}
    mapped, off, j = [], 0.0, 0
    keep_words = [i for i in keep if not ws[i].get("gap")]
    for a, b in iv:
        while j < len(keep_words) and ws[keep_words[j]]["s"] < b:
            i = keep_words[j]
            w = ws[i]
            st = off + max(0.0, w["s"] - a)
            mapped.append({"i": i, "w": fixes.get(i, w["w"]), "s": st, "e": min(off + min(b, w["e"]) - a, st + 1.2)})
            j += 1
        off += b - a
    return iv, mapped, off, sorted(drop)


def _loose(n):
    return re.sub(r"\s+", " ", n.replace("\u202f", " ").replace("\u00a0", " ")).strip().lower()


def find_file(name):
    for folder in (paths.SCREEN_IN, paths.SHOTS_IN, montage.VISUALS, paths.DROP, montage.BROLL):
        if (folder / name).exists():
            return folder / name
        # Mac screenshot names have an invisible special space before AM/PM
        if folder.exists():
            for f in folder.iterdir():
                if _loose(f.name) == _loose(name):
                    return f
    hits = [f for f in montage.BROLL.rglob("*") if _loose(f.name) == _loose(name)] if montage.BROLL.exists() else []
    if hits:
        return hits[0]
    raise ValueError(f"edits name a file that isn't there: {name}")


def time_at(mapped, idx, total):
    for m in mapped:
        if m["i"] >= idx:
            return m["s"]
    return total


def build_visuals(e, mapped, total, s, jdir, used):
    """Visuals from Claude's edits. Returns (video, last screen recording, description, warnings, files used)."""
    items = sorted(e.get("visuals", []), key=lambda v: v.get("at", 0))
    warn, used_files, parts, last_rec, desc = [], [], [], None, []
    starts = [0.0] + [time_at(mapped, v.get("at", 0), total) for v in items[1:]]
    segdir = jdir / "segments"
    shutil.rmtree(segdir, ignore_errors=True)
    segdir.mkdir(parents=True)

    for k, v in enumerate(items):
        t0 = starts[k]
        t1 = starts[k + 1] if k + 1 < len(items) else total
        dur = t1 - t0
        if dur < 0.3:
            continue
        if "file" in v or "broll" in v:
            f = find_file(v.get("file") or v.get("broll"))
            used_files.append(f)
            desc.append(f"{t0:4.1f}s {f.name}")
            if f.suffix.lower() in montage.IMAGE_EXT:
                montage.seg_image(f, dur, segdir / f"{len(parts):03d}.mp4")
                parts.append(segdir / f"{len(parts):03d}.mp4")
                last_rec = None
            elif "file" in v and montage.is_screen_recording(f):
                st = float(v.get("start", 0))
                length = montage.probe(f)[2]
                if length and st + dur > length + 0.3:
                    warn.append(f"{f.name} is {length:.1f}s but needs {st + dur:.1f}s here: it freezes on its last frame")
                dest = segdir / f"{len(parts):03d}.mp4"
                montage.seg_video(montage.framed(f, jdir), st, dur, dest, fill=False, hold_last=True)
                parts.append(dest)
                last_rec = f
            else:
                length = montage.probe(f)[2]
                st = float(v.get("start", max(0.0, (length - dur) / 2)))
                dest = segdir / f"{len(parts):03d}.mp4"
                montage.seg_video(f, st, dur, dest, fill=True)
                parts.append(dest)
                last_rec = None
        elif "stock" in v:
            n = max(1, round(dur / montage.CLIP_LEN))
            clips, w = montage.stock_for([v["stock"]], n, s, set(used) | montage.recently_used())
            warn += w
            desc.append(f"{t0:4.1f}s stock '{v['stock']}' x{len(clips)}")
            if not clips:
                warn.append(f"no stock for '{v['stock']}', SAVR green instead")
                dest = segdir / f"{len(parts):03d}.mp4"
                jam.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                         f"color=c={montage.GREEN}:s={montage.W}x{montage.H}:r={montage.FPS}", *montage.enc(dest)])
                parts.append(dest)
            else:
                used.update(clips)
                montage.mark_used(clips)
                each = dur / len(clips)
                for c in clips:
                    length = montage.probe(c)[2]
                    dest = segdir / f"{len(parts):03d}.mp4"
                    montage.seg_video(c, max(0.0, (length - each) / 2), each, dest, fill=True)
                    parts.append(dest)
            last_rec = None
        else:
            raise ValueError(f"visual item with no file/stock/broll: {v}")
    lst = segdir / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = jdir / "visuals.mp4"
    jam.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out, last_rec, "; ".join(desc), warn, used_files


def render_jam(n, ws, e, x, s, voice, plan, date, work, used, auto):
    cue = s.get("restart_word", "zebra")
    if auto:
        e = dict(e, cut=e.get("cut", []) + auto_cuts(ws, cue))
    iv, mapped, total, dropped = plan_audio(ws, x, e, cue)
    said = jam.clean_text(" ".join(m["w"] for m in mapped))
    hook = e.get("hook") or (plan.get(n, {}).get("hook") if plan else "") or montage.hook_text(n, s, plan)
    base = jam.unique_base(date, n, hook or said)
    jdir = work / f"jam{n:02d}"
    jdir.mkdir(exist_ok=True)
    raw = jdir / "cut.wav"
    write_wav(raw, splice(x, iv))
    voiced = jdir / f"voice_{voice}.wav"
    jam.shape_voice(raw, voiced, voice)
    caps = jam.render_captions(mapped, total, s, jdir)
    files_used, warn = [], []
    if e.get("visuals"):
        visuals, last_rec, what, warn, files_used = build_visuals(e, mapped, total, s, jdir, used)
    else:
        visuals, last_rec, what, warn = montage.build_background(
            n, total, said, s, jdir, entry=plan.get(n, {}), durs=jam.visual_cuts(mapped, total, montage.CLIP_LEN),
            used=used)
        what = what + " (automatic)"
    bg = str(visuals) if visuals else ""
    hook_img = montage.hook_png(hook, jdir) if hook else None
    tmp = jdir / "final.mp4"
    if e.get("end_card", s.get("end_card_default", False)):
        body = jdir / "body.mp4"
        jam.render_video(voiced, caps, bg, total, body, hook_img)
        end = montage.cutscene_for(last_rec, jdir)
        if end:
            montage.append_cutscene(body, end, tmp)
            total += montage.probe(end)[2]
        else:
            shutil.move(str(body), tmp)
    else:
        jam.render_video(voiced, caps, bg, total, tmp, hook_img)
    if montage.probe(tmp)[2] < 1:
        raise ValueError("render came out empty")
    dest = jam.OUT / f"{base}.mp4"
    shutil.move(str(tmp), dest)  # only finished files ever land in READY
    if total < 8:
        warn.append(f"only {total:.0f}s long, check nothing important got cut")
    print(f"  jam {n:02d}  {total:5.1f}s  -> {dest.relative_to(jam.ROOT)}")
    cut_text = " ".join(ws[i]["w"] for i in dropped)
    return {"n": n, "file": dest.name, "secs": total, "hook": hook, "said": said, "cut": cut_text,
            "visuals": what, "warn": warn, "files": files_used}


def review(date, voice, results, failed, notes, auto):
    out = [f"# Review ({date}, voice: {voice}{', AUTOMATIC edits, no Claude' if auto else ''})\n"]
    if failed:
        out.append("**FAILED:** " + "; ".join(f"{k or 'session'}: {why}" for k, why in failed) +
                   "\nFix it (or tell Claude), then double-click Make Jam to redo only these.\n")
    out += [f"NOTE: {x}" for x in notes]
    for r in results:
        out.append(f"\n## jam {r['n']:02d}  ({r['secs']:.0f}s)  {r['file']}")
        if r["hook"]:
            out.append(f"Hook: {r['hook']}")
        out.append(f"Visuals: {r['visuals']}")
        out += [f"WARNING: {w}" for w in r["warn"]]
        if r["cut"]:
            out.append(f"Cut: {r['cut']}")
        out.append(f"\n{r['said']}")
    name, k = f"{date} review", 1
    while (jam.OUT / f"{name}.md").exists():
        k += 1
        name = f"{date} review {k}"
    (jam.OUT / f"{name}.md").write_text("\n".join(out) + "\n")
    return jam.OUT / f"{name}.md"


def load_session(src, s):
    work = jam.WORK / re.sub(r"\W+", "_", src.stem)
    p = work / "session.json"
    return (json.loads(p.read_text()), work) if p.exists() else (None, work)


def run(sources, s, voice, auto=False, only=None):
    plan, bad = montage.read_plan(strict=True)
    for line in bad:
        print(f"  plan.txt line not understood, ignored: {line!r}")
    sessions = []
    for src in sources:
        sess, work = load_session(src, s)
        stale = sess is not None and (work / "session.json").stat().st_mtime < src.stat().st_mtime
        if sess is None or stale:
            print(f"\n== Transcribing {src.name}")
            sess = transcribe_memo(src, s, plan)
        sessions.append((src, sess))
    edits = json.loads(EDITS.read_text()) if EDITS.exists() else None
    if edits is None and not auto:
        pack = write_pack([x for _, x in sessions], plan, s)
        print(f"\nStage 1 done. Written for Claude: {pack.relative_to(jam.ROOT)}")
        print("Tell Claude 'jams ready'. Once edits.json is in DROP HERE/claude, double-click Make Jam again.")
        return
    date = datetime.date.today().isoformat()
    jam.OUT.mkdir(parents=True, exist_ok=True)
    results, failed, notes, used, files = [], [], [], set(), []
    retry = CLAUDE_DIR / ".failed.json"
    redo = set(json.loads(retry.read_text())) if retry.exists() and not only else None
    for src, sess in sessions:
        work = load_session(src, s)[1]
        x = read_wav(work / "tight.wav")
        me = (edits or {}).get("memos", {}).get(src.name, {})
        if me and me.get("hash") != sess["hash"]:
            failed.append((0, f"{src.name} was re-transcribed after Claude's edits (hash {sess['hash']} vs "
                              f"{me.get('hash')}). Ask Claude to redo the edits."))
            continue
        notes += [f"{src.name}: {x}" for x in sess["notes"]]
        for n, ws in sorted(sess["jams"].items(), key=lambda kv: int(kv[0])):
            n = int(n)
            key = f"{src.name}#{n}"
            if (only and n not in only) or (redo is not None and key not in redo):
                continue
            e = me.get("jams", {}).get(str(n))
            if e is None and not auto:
                notes.append(f"{src.name}: no edits for jam {n}, made with automatic edits")
            if e and e.get("skip"):
                continue
            try:
                r = render_jam(n, ws, e or {}, x, s, voice, plan, date, work, used, auto or e is None)
                results.append(r)
                files += r["files"]
            except (Exception, SystemExit) as err:
                failed.append((key, str(err)))
                print(f"  jam {n:02d} FAILED: {err}")
    sheet = review(date, voice, results, failed, notes, auto)
    print(f"\n  Review sheet: {sheet.relative_to(jam.ROOT)}")
    if failed:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        retry.write_text(json.dumps([k for k, _ in failed if k]))
        print(f"  {len(failed)} problem(s). Nothing was moved; run Make Jam again to redo just those.")
        return
    archive(sources, files, date)


def archive(sources, files, date):
    """Session done: memo, used files, plan and Claude's folder go to ORIGINALS."""
    dest = paths.ORIGINALS / "sessions" / f"{date} {datetime.datetime.now():%H%M}"
    for src in sources:
        if src.exists() and src.parent == jam.INBOX:
            paths.move_original(src, paths.VOICE_DONE)
    for f in set(files):
        if f.exists() and paths.DROP in f.parents:
            paths.move_original(f, paths.SCREEN_DONE)
    dest.mkdir(parents=True, exist_ok=True)
    if (paths.DROP / "plan.txt").exists():
        shutil.move(str(paths.DROP / "plan.txt"), dest / "plan.txt")
    if CLAUDE_DIR.exists():
        for f in CLAUDE_DIR.iterdir():
            shutil.move(str(f), dest / f.name)
    print(f"  Session filed in {dest.relative_to(jam.ROOT)}")
