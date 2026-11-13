#!/usr/bin/env python3
"""SAVR jam tool, v2.

One long recording in, one captioned 9:16 video per jam out.

While recording, say "jam seven" before each prompt. To start a jam over,
just say "jam seven" again (or pause, say "again", pause). The last full
take wins. Repeated false starts ("I use, I use SAVR to...") are cut
automatically, and every pause is cut from the audio itself.

Usage:
  python jam.py                  # everything in jam/inbox
  python jam.py my_file.m4a      # one specific file
  python jam.py --audition       # 20s of your voice in every preset, no video
  python jam.py --voice warm     # override the preset in settings.json
"""
import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths
import montage

ROOT = paths.TOP
INBOX = paths.VOICE_IN
DONE = paths.VOICE_DONE
BACKGROUNDS = paths.BACKGROUNDS
WORK = paths.ENGINE / ".work"
OUT = paths.JAMS
SETTINGS = paths.ENGINE / "jam_settings.json"

SR = 48000
W, H, FPS = 1080, 1920, 30
SAVR_GREEN = "0x2b4c2c"
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".caf", ".aiff", ".mp4", ".mov", ".flac"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".webp"}

DEFAULTS = {
    "voice": "natural",
    "whisper_model": "small.en",
    "silence_db": "auto",    # or a number like -40
    "min_pause": 0.25,       # pauses longer than this get cut down
    "keep_pause": 0.34,      # how much of each cut pause is left in
    "caption_max_words": 3,
    "caption_max_chars": 18,
    "caption_y": 0.62,
    "caption_size": 92,
    "default_background": "",
    "auto_broll": True,      # no visuals given -> b-roll from the B-ROLL folder
    "cutscene": True,        # SAVR end card on every jam
    "jams": {},
}

# name: (formant shift, pitch in semitones, pitch range, eq, extra)
BC = ["equalizer=f=100:t=q:w=1:g=4", "equalizer=f=250:t=q:w=1:g=-3",
      "equalizer=f=3000:t=q:w=1.2:g=4", "equalizer=f=8000:t=q:w=1:g=2"]
VOICES = {
    "natural":    (1.00,  0.0, 1.00, "std", []),
    "broadcast":  (1.00,  0.0, 1.00, "broadcast", []),
    "warm":       (0.97, -1.0, 1.00, "warm", []),
    "smooth":     (0.96, -0.5, 0.80, "warm", []),
    # Broadcast family (Nov 7): pitch moves only, formants left alone.
    "bc-low":     (1.00, -0.7, 1.00, "broadcast", []),
    "bc-deep":    (1.00, -1.3, 0.95, "broadcast", []),
    "bc-punchy":  (1.00, -0.4, 1.05, "punchy", ["sat"]),
    "bc-close":   (1.00, -0.5, 1.00, "close", []),
    "bc-calm":    (1.00, -0.6, 0.80, "broadcast", []),
    "bc-grit":    (1.00, -0.9, 1.00, "broadcast", ["sat"]),
}
EQ = {
    "std": ["equalizer=f=120:t=q:w=1:g=2", "equalizer=f=300:t=q:w=1:g=-2",
            "equalizer=f=3500:t=q:w=1.5:g=3"],
    "warm": ["equalizer=f=150:t=q:w=1:g=3", "equalizer=f=350:t=q:w=1:g=-2",
             "equalizer=f=3000:t=q:w=1.5:g=2", "equalizer=f=9000:t=q:w=1:g=-2"],
    "broadcast": BC,
    "punchy": ["equalizer=f=110:t=q:w=1:g=3", "equalizer=f=300:t=q:w=1:g=-4",
               "equalizer=f=2500:t=q:w=1:g=5", "equalizer=f=6500:t=q:w=1:g=2"],
    "close": ["equalizer=f=90:t=q:w=0.8:g=6", "equalizer=f=220:t=q:w=1:g=-2",
              "equalizer=f=3200:t=q:w=1.2:g=3", "equalizer=f=7500:t=q:w=1:g=-2"],
}
EXTRA = {
    "sat": ["volume=5dB", "asoftclip=type=tanh", "volume=-5dB"],
}

NUMBERS = {
    "zero": 0, "one": 1, "won": 1, "two": 2, "to": 2, "too": 2, "three": 3,
    "four": 4, "for": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ate": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
}
JAM_WORDS = {"jam", "jams", "jem", "gem"}
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}
NO_STUTTER = {"no", "very", "so", "really", "go"}
REPLACE = {
    "savor": "SAVR", "saver": "SAVR", "savour": "SAVR", "savr": "SAVR", "sabre": "SAVR",
    "color": "colour", "colors": "colours", "favorite": "favourite",
    "favorites": "favourites", "flavor": "flavour", "flavors": "flavours",
    "neighbor": "neighbour", "neighbors": "neighbours", "behavior": "behaviour",
    "center": "centre", "labor": "labour", "favor": "favour", "honor": "honour",
}


# ---------------------------------------------------------------- helpers

def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"Command failed: {' '.join(str(c) for c in cmd[:6])}...")
    return r.stdout


def norm(t):
    return re.sub(r"[^a-z0-9#$]", "", t.lower())


def load_settings():
    s = dict(DEFAULTS)
    if SETTINGS.exists():
        s.update(json.loads(SETTINGS.read_text()))
    keys = paths.ENGINE / "keys.json"   # API keys live here, never uploaded to GitHub
    if keys.exists():
        s.update(json.loads(keys.read_text()))
    return s


def read_wav(path):
    with wave.open(str(path)) as f:
        data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def write_wav(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((x * 32767).astype(np.int16).tobytes())


def splice(x, intervals, fade=0.006):
    """Join the kept pieces with tiny fades so cuts don't click."""
    n = int(fade * SR)
    ramp = np.linspace(0, 1, n, dtype=np.float32)
    parts = []
    for a, b in intervals:
        p = x[int(a * SR):int(b * SR)].copy()
        if len(p) > 2 * n:
            p[:n] *= ramp
            p[-n:] *= ramp[::-1]
        parts.append(p)
    return np.concatenate(parts) if parts else np.zeros(1, np.float32)


# ------------------------------------------------------- pause removal

def speech_intervals(x, s):
    """Find speech from the audio itself, not from the transcript."""
    hop = int(0.02 * SR)
    frames = len(x) // hop
    rms = np.sqrt(np.mean(x[:frames * hop].reshape(frames, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    if s["silence_db"] == "auto":
        floor = np.percentile(db, 10)
        thr = float(np.clip(floor + 14, -55, -30))
    else:
        thr = float(s["silence_db"])
    voiced = db > thr
    min_gap = int(s["min_pause"] / 0.02)
    idx = np.flatnonzero(voiced)
    if len(idx) == 0:
        return [], thr
    segs, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > min_gap:
            segs.append((start, prev + 1))
            start = i
        prev = i
    segs.append((start, prev + 1))
    segs = [(a, b) for a, b in segs if b - a >= 4]  # at least 80 ms
    half = s["keep_pause"] / 2
    out = []
    for a, b in segs:
        ta, tb = max(0, a * 0.02 - half), min(len(x) / SR, b * 0.02 + half)
        if out and ta <= out[-1][1]:
            out[-1][1] = tb
        else:
            out.append([ta, tb])
    return out, thr


class Timeline:
    """Maps time in the tightened audio back to the original recording."""

    def __init__(self, intervals):
        self.iv = intervals
        self.offsets = np.cumsum([0] + [b - a for a, b in intervals])

    def to_orig(self, t):
        k = int(np.searchsorted(self.offsets, t, side="right") - 1)
        k = min(max(k, 0), len(self.iv) - 1)
        return self.iv[k][0] + (t - self.offsets[k])


# ---------------------------------------------------------- transcription

_MODEL = {}
PROMPT = "Jam one. Again. Jam two. I use SAVR to compare grocery prices. Comment if you want the list."


def get_model(name):
    import os
    name = os.environ.get("JAM_WHISPER_MODEL", name)
    if name not in _MODEL:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise SystemExit("faster-whisper isn't installed. Run Make Jam.command once while online.")
        _MODEL[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL[name]


def whisper_words(model, wav, offset=0.0, prompt=PROMPT):
    segments, _ = model.transcribe(str(wav), language="en", word_timestamps=True,
                                   vad_filter=False, initial_prompt=prompt)
    return [{"w": w.word.strip(), "s": round(w.start + offset, 3), "e": round(w.end + offset, 3)}
            for seg in segments for w in (seg.words or [])]


def refine(words, x, joins, model, work):
    """Whisper tends to swallow a repeated phrase and stretch the next word over
    the gap ('I only buy... I only buy protein' comes back as one 'I only buy
    protein' with a 2-second 'protein'). Re-listen to just that piece."""
    out = []
    for w in words:
        inside = [j for j in joins if w["s"] + 0.05 < j < w["e"] - 0.05]
        if w["e"] - w["s"] > 0.7 and inside:
            start = inside[-1]
            clip = work / "refine.wav"
            write_wav(clip, x[int(max(0, start - 0.05) * SR):int((w["e"] + 0.05) * SR)])
            sub = [q for q in whisper_words(model, clip, max(0, start - 0.05), prompt=None)
                   if q["s"] < w["e"] - 0.1]
            if sub:
                out.extend(sub)
                continue
            w = dict(w, s=start)
        if out and w["s"] < out[-1]["e"] - 0.1:
            continue  # already covered by the re-listened piece
        out.append(w)
    return out


def transcribe(wav, cache, model_name, joins, work):
    if cache.exists() and cache.stat().st_mtime > wav.stat().st_mtime:
        return json.loads(cache.read_text())
    print("  Transcribing (first run downloads the model, needs internet)...")
    model = get_model(model_name)
    words = whisper_words(model, wav)
    words = refine(words, read_wav(wav), joins, model, work)
    cache.write_text(json.dumps(words))
    return words


# --------------------------------------------------------------- parsing

def read_number(words, i):
    if i >= len(words):
        return None, 0
    t = norm(words[i]["w"]).lstrip("#")
    if t.isdigit():
        return int(t), 1
    if t in NUMBERS:
        n = NUMBERS[t]
        if n >= 20 and i + 1 < len(words):
            u = norm(words[i + 1]["w"])
            if u in NUMBERS and NUMBERS[u] < 10:
                return n + NUMBERS[u], 2
        return n, 1
    return None, 0


def find_markers(words, tl):
    events, i = [], 0
    while i < len(words):
        t = norm(words[i]["w"])
        m = re.fullmatch(r"(?:jam|jem|gem)#?(\d+)", t)
        if m:
            events.append(("jam", int(m.group(1)), i, i + 1))
            i += 1
            continue
        if t in JAM_WORDS:
            n, used = read_number(words, i + 1)
            if n is not None:
                k = i + 1 + used
                # 'Jam for... four.' : skip the number said twice
                while k < len(words) and read_number(words, k)[0] == n:
                    k += 1
                events.append(("jam", n, i, k))
                i = k
                continue
        if t in ("again", "redo", "restart"):
            # Pauses are measured in the ORIGINAL recording, before tightening.
            gb = tl.to_orig(words[i]["s"]) - tl.to_orig(words[i - 1]["e"]) if i > 0 else 9
            ga = tl.to_orig(words[i + 1]["s"]) - tl.to_orig(words[i]["e"]) if i + 1 < len(words) else 9
            if gb >= 0.5 and ga >= 0.5:
                events.append(("again", None, i, i + 1))
        i += 1
    return events


def split_jams(words, tl):
    events = find_markers(words, tl)
    jam_events = [e for e in events if e[0] == "jam"]
    if not jam_events:
        return [(1, words)]
    best = {}
    for k, (_, n, _, body_start) in enumerate(jam_events):
        body_end = jam_events[k + 1][2] if k + 1 < len(jam_events) else len(words)
        agains = [e for e in events if e[0] == "again" and body_start <= e[2] < body_end]
        takes, start = [], body_start
        for _, _, a_i, a_end in agains:
            takes.append(words[start:a_i])
            start = a_end
        takes.append(words[start:body_end])
        takes = [t for t in takes if len(t) >= 4]
        if takes:
            best[n] = takes[-1]   # a jam number said again later replaces the earlier one
    return sorted(best.items())


def drop_restarts(words, tl=None):
    """Cut false starts and keep the last attempt.

    Exact:  'I use, I use SAVR to'        -> 'I use SAVR to'
    Reword: 'I prep 5 meals. I portion 5 meals' -> 'I portion 5 meals'
            (a short phrase, one word swapped, after a pause)
    """
    def orig(t):
        return tl.to_orig(t) if tl else t

    out = list(words)
    changed = True
    while changed:
        changed = False
        n = len(out)
        for i in range(n):
            for d in range(1, 21):
                j = i + d
                if j >= n:
                    break
                m = 0
                while j + m < n and m < d and norm(out[i + m]["w"]) == norm(out[j + m]["w"]):
                    m += 1
                if d == 1:
                    ok = m == 1 and norm(out[i]["w"]) not in NO_STUTTER and bool(norm(out[i]["w"]))
                elif d == 2:
                    ok = m == 2
                else:
                    ok = m >= d - 1
                if not ok and 3 <= d <= 5 and j + d <= n and m >= 1:
                    # A short phrase said again with one word swapped, after a pause.
                    same = sum(norm(out[i + k]["w"]) == norm(out[j + k]["w"]) for k in range(d))
                    pause = orig(out[j]["s"]) - orig(out[j - 1]["e"])
                    ok = same >= d - 1 and pause >= 0.4
                if not ok and 3 <= d <= 6 and j + 2 <= n and m >= 2:
                    # Same opening, then a different ending: 'roughly 10 times a day.
                    # Roughly 10 days at a time.' keeps the second.
                    a = [norm(w["w"]) for w in out[i:j]]
                    b = [norm(w["w"]) for w in out[j:j + d + 1]]
                    shared = sum(min(a.count(x), b.count(x)) for x in set(a))
                    ok = shared / d >= 0.6
                if ok:
                    del out[i:j]
                    changed = True
                    break
            if changed:
                break
    return out


def plan_cuts(take, total_len, s, tl=None):
    joins = [float(j) for j in tl.offsets[1:]] if tl else []
    pos = {id(w): k for k, w in enumerate(take)}
    words = [w for w in take if norm(w["w"]) not in FILLERS]
    words = drop_restarts(words, tl)
    pad_b, pad_a = 0.12, 0.25
    intervals, j = [], 0
    a = max(0.0, words[0]["s"] - pad_b)
    for prev, nxt in zip(words, words[1:]):
        # Anything skipped between two kept words (a restart, a filler) or a
        # long gap becomes a cut.
        skipped = pos[id(nxt)] != pos[id(prev)] + 1
        if skipped:
            # End where the sound actually stops (keeps the 's' on 'dollars'),
            # or a quarter second after the word if the restart had no pause.
            first_cut = take[pos[id(prev)] + 1]
            last_cut = take[pos[id(nxt)] - 1]
            # End: where the sound stops after the kept word, before the cut words.
            nat = [j for j in joins if prev["e"] - 0.05 <= j <= first_cut["s"] + 0.05]
            end = nat[0] if nat else min(prev["e"] + pad_a, (prev["e"] + first_cut["s"]) / 2 + 0.05)
            # Start: where the sound starts again after the cut words.
            nat_s = [j for j in joins if last_cut["e"] - 0.05 <= j <= nxt["s"] + 0.05]
            start = nat_s[-1] if nat_s else max(nxt["s"] - pad_b, (last_cut["e"] + nxt["s"]) / 2 - 0.05)
            intervals.append([a, end])
            a = max(start, end)
    intervals.append([a, min(total_len, words[-1]["e"] + 0.5)])
    mapped, offset = [], 0.0
    for ia, ib in intervals:
        while j < len(words) and words[j]["s"] < ib:
            w = words[j]
            ws = offset + max(0.0, w["s"] - ia)
            we = offset + min(ib, w["e"]) - ia
            mapped.append({"w": w["w"], "s": ws, "e": min(we, ws + 1.2)})
            j += 1
        offset += ib - ia
    return intervals, mapped, offset


# ------------------------------------------------------------------ voice

def shape_voice(in_wav, out_wav, preset):
    formant, semis, rng, eq, extra = VOICES[preset]
    src = in_wav
    if (formant, semis, rng) != (1.0, 0.0, 1.0):
        import parselmouth
        from parselmouth.praat import call
        snd = parselmouth.Sound(str(in_wav))
        pitch = snd.to_pitch()
        median = call(pitch, "Get quantile", 0, 0, 0.5, "Hertz")
        new_median = median * (2 ** (semis / 12)) if median == median else 0
        shifted = call(snd, "Change gender", 75, 600, formant, new_median, rng, 1.0)
        src = out_wav.with_suffix(".shift.wav")
        shifted.save(str(src), "WAV")
    chain = ["aresample=48000", "highpass=f=80", "afftdn=nr=10:nf=-45", *EQ[eq],
             *[f for e in extra for f in EXTRA[e]],
             "acompressor=threshold=-20dB:ratio=3.5:attack=8:release=150:makeup=2",
             "loudnorm=I=-14:TP=-1.5:LRA=9", "aresample=48000"]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", ",".join(chain),
         "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)])


# ---------------------------------------------------------------- captions

def find_font():
    for p in ["/System/Library/Fonts/Supplement/Arial Black.ttf",
              "/System/Library/Fonts/Supplement/Arial Bold.ttf",
              "/Library/Fonts/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if Path(p).exists():
            return p
    return None


def clean_text(t):
    out = []
    for word in t.split():
        core = re.sub(r"[^A-Za-z]", "", word)
        rep = REPLACE.get(core.lower())
        if rep:
            if rep != "SAVR" and core[:1].isupper():
                rep = rep.capitalize()
            word = word.replace(core, rep)
        out.append(word)
    return " ".join(out).strip(" ,")


def chunk_captions(words, s):
    chunks, cur = [], []
    for k, w in enumerate(words):
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        nxt = words[k + 1] if k + 1 < len(words) else None
        brk = (len(cur) >= s["caption_max_words"] or len(text) >= s["caption_max_chars"]
               or w["w"][-1:] in ".?!," or (nxt and nxt["s"] - w["e"] > 0.3))
        if brk or nxt is None:
            chunks.append(cur)
            cur = []
    return chunks


def render_captions(words, total, s, workdir):
    from PIL import Image, ImageDraw, ImageFont
    for old in workdir.glob("cap_*.png"):
        old.unlink()
    font_path = find_font()
    font = ImageFont.truetype(font_path, s["caption_size"]) if font_path else ImageFont.load_default()
    blank = workdir / "cap_blank.png"
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    chunks = chunk_captions(words, s)
    lines, t = [], 0.0
    for k, ch in enumerate(chunks):
        start = max(ch[0]["s"], t)
        end = chunks[k + 1][0]["s"] if k + 1 < len(chunks) else min(total, ch[-1]["e"] + 0.4)
        if start > t + 0.01:
            lines.append((blank, start - t))
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        text = clean_text(" ".join(w["w"] for w in ch)).rstrip(".,")
        f = font
        box = d.textbbox((0, 0), text, font=f, stroke_width=8)
        size = s["caption_size"]
        while font_path and box[2] - box[0] > W * 0.9 and size > 48:  # never run off the screen
            size -= 4
            f = ImageFont.truetype(font_path, size)
            box = d.textbbox((0, 0), text, font=f, stroke_width=8)
        x = (W - (box[2] - box[0])) / 2 - box[0]
        y = H * s["caption_y"] - (box[3] - box[1]) / 2 - box[1]
        d.text((x, y), text, font=f, fill="white", stroke_width=8, stroke_fill="black")
        p = workdir / f"cap_{k:04d}.png"
        img.save(p)
        dur = max(0.04, end - start)
        lines.append((p, dur))
        t = start + dur
    if total > t:
        lines.append((blank, total - t))
    lst = workdir / "captions.txt"
    body = "".join(f"file '{p}'\nduration {d:.3f}\n" for p, d in lines)
    body += f"file '{lines[-1][0]}'\n"
    lst.write_text(body)
    return lst


# ------------------------------------------------------------------ video

def background_input(name, total):
    if name:
        p = Path(name)
        if not p.is_absolute():
            p = BACKGROUNDS / name
        if p.exists():
            if p.suffix.lower() in IMAGE_EXT:
                frames = int(total * FPS) + 1
                vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
                      f"zoompan=z='min(1+0.0006*on,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                      f":d={frames}:s={W}x{H}:fps={FPS}")
                return ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(p)], vf
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}"
            return ["-stream_loop", "-1", "-t", f"{total:.3f}", "-i", str(p)], vf
        print(f"  background '{name}' not found in DROP HERE/backgrounds, using SAVR green")
    return ["-f", "lavfi", "-t", f"{total:.3f}", "-i", f"color=c={SAVR_GREEN}:s={W}x{H}:r={FPS}"], "null"


def render_video(audio, captions_list, bg_name, total, dest, hook=None):
    bg_args, bg_vf = background_input(bg_name, total)
    graph = (f"[0:v]{bg_vf},format=yuv420p[bg];"
             f"[1:v]fps={FPS},format=rgba[cap];"
             f"[bg][cap]overlay=0:0:format=auto[bc]")
    hook_in = []
    if hook:
        hook_in = ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(hook)]
        graph += ";[3:v]format=rgba,fade=t=out:st=2.3:d=0.25:alpha=1[hk];[bc][hk]overlay=0:0:format=auto[bh]"
        last = "[bh]"
    else:
        last = "[bc]"
    graph += f";{last}format=yuv420p[v]"
    run(["ffmpeg", "-y", "-v", "error", *bg_args,
         "-f", "concat", "-safe", "0", "-i", str(captions_list),
         "-i", str(audio), *hook_in, "-filter_complex", graph,
         "-map", "[v]", "-map", "2:a", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)])


# ------------------------------------------------------------------- main

def inbox_files():
    INBOX.mkdir(parents=True, exist_ok=True)
    return sorted([f for f in INBOX.iterdir() if f.suffix.lower() in AUDIO_EXT],
                  key=lambda f: f.stat().st_mtime)


def pick_sources(arg):
    if arg:
        p = Path(arg.strip().strip("'\""))
        if not p.exists():
            raise SystemExit(f"Can't find {p}")
        return [p]
    files = inbox_files()
    if not files:
        raise SystemExit("Drop your voice memo in DROP HERE/voice memos first.")
    return files


def unique_base(date, n, label=""):
    """'2026-11-10 jam07 grocery prices went up', so you can tell them apart in Finder."""
    label = re.sub(r"[^A-Za-z0-9 $%]", "", label).strip().lower()
    label = " ".join(label.split()[:6])
    stem = f"{date} jam{n:02d}" + (f" {label}" if label else "")
    base, k = stem, 1
    while (OUT / f"{base}.mp4").exists():
        k += 1
        base = f"{stem} {k}"
    return base


def prepare(src, s):
    """Decode, cut every pause, ready for transcription."""
    work = WORK / re.sub(r"\W+", "_", src.stem)
    work.mkdir(parents=True, exist_ok=True)
    full = work / "full.wav"
    if not full.exists() or full.stat().st_mtime < src.stat().st_mtime:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(SR),
             "-c:a", "pcm_s16le", str(full)])
    x = read_wav(full)
    iv, thr = speech_intervals(x, s)
    tight = work / "tight.wav"
    write_wav(tight, splice(x, iv))
    kept = sum(b - a for a, b in iv)
    print(f"  {len(x)/SR:.0f}s recorded, {kept:.0f}s after cutting pauses (threshold {thr:.0f} dB)")
    return work, tight, Timeline(iv)


def audition(src, s, names):
    print(f"Voice test from {src.name}")
    work, tight, _ = prepare(src, s)
    x = read_wav(tight)
    clip = work / "audition_src.wav"
    write_wav(clip, x[: 20 * SR])
    dest = OUT / "voice-test"
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        wav = work / f"aud_{name}.wav"
        shape_voice(clip, wav, name)
        run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "192k",
             str(dest / f"{name}.m4a")])
        print(f"  {name:<11} -> {(dest / (name + '.m4a')).relative_to(ROOT)}")


def voice_only(src, s, voice):
    """Same voice, no cutting: for audio that's already edited (Descript)."""
    dest_dir = paths.FINISHED / "voice"
    dest_dir.mkdir(parents=True, exist_ok=True)
    work = WORK / re.sub(r"\W+", "_", src.stem)
    work.mkdir(parents=True, exist_ok=True)
    full = work / "full.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(SR),
         "-c:a", "pcm_s16le", str(full)])
    dest = dest_dir / f"{src.stem}_{voice}.wav"
    shape_voice(full, dest, voice)
    print(f"  {src.name} -> {dest.relative_to(ROOT)}")


def process(src, s, voice, args):
    print(f"\n== {src.name}")
    work, tight, tl = prepare(src, s)
    joins = [float(j) for j in tl.offsets[1:-1]]
    words = transcribe(tight, work / "words.json", s["whisper_model"], joins, work)
    if not words:
        print("  Didn't hear any words in that file, skipping.")
        return
    jams = split_jams(words, tl)
    if args.only:
        keep = {int(x) for x in args.only.split(",")}
        jams = [j for j in jams if j[0] in keep]
    OUT.mkdir(parents=True, exist_ok=True)
    date = datetime.date.today().isoformat()
    notes = [f"# Jams from {src.name} ({date}, voice: {voice})\n"]
    print(f"  Found {len(jams)} jam(s): {', '.join(str(n) for n, _ in jams)}")
    x = read_wav(tight)
    for n, take in jams:
        intervals, mapped, total = plan_cuts(take, len(x) / SR, s, tl)
        said = clean_text(" ".join(w["w"] for w in mapped))
        hook = montage.hook_text(n, s)
        base = unique_base(date, n, hook or said)
        jdir = work / f"jam{n:02d}"
        jdir.mkdir(exist_ok=True)
        raw = jdir / "cut.wav"
        write_wav(raw, splice(x, intervals))
        voiced = jdir / f"voice_{voice}.wav"
        shape_voice(raw, voiced, voice)
        caps = render_captions(mapped, total, s, jdir)
        visuals, last_rec, what = montage.build_background(n, total, said, s, jdir)
        bg = str(visuals) if visuals else s["jams"].get(str(n), {}).get("background", s["default_background"])
        dest = OUT / f"{base}.mp4"
        hook_img = montage.hook_png(hook, jdir) if hook else None
        if s.get("cutscene", True):
            body = jdir / "body.mp4"
            render_video(voiced, caps, bg, total, body, hook_img)
            end = montage.cutscene_for(last_rec, jdir)
            if end:
                montage.append_cutscene(body, end, dest)
                total += montage.probe(end)[2]
            else:
                shutil.move(str(body), dest)
        else:
            render_video(voiced, caps, bg, total, dest, hook_img)
        print(f"           visuals: {what}")
        text = clean_text(" ".join(w["w"] for w in mapped))
        notes.append(f"## {base}  ({total:.0f}s)\n\n{text}\n")
        print(f"  jam {n:02d}  {total:5.1f}s  -> {dest.relative_to(ROOT)}")
    tname, k = f"{date}_transcript", 1
    while (OUT / f"{tname}.md").exists():
        k += 1
        tname = f"{date}_transcript-{k}"
    (OUT / f"{tname}.md").write_text("\n".join(notes))
    if not args.source and src.parent == INBOX:
        paths.move_original(src, DONE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?")
    ap.add_argument("--voice", help=", ".join(VOICES))
    ap.add_argument("--only", help="comma list of jam numbers, e.g. 3,7")
    ap.add_argument("--audition", action="store_true", help="voice samples only, no video")
    ap.add_argument("--voice-only", action="store_true",
                    help="apply the voice to already-edited audio, no cuts, no video")
    ap.add_argument("--family", help="only audition presets starting with this, e.g. bc")
    args = ap.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found. Install it with: brew install ffmpeg")
    s = load_settings()
    voice = (args.voice or s["voice"]).lower()
    if voice not in VOICES:
        raise SystemExit(f"Unknown voice '{voice}'. Pick one of: {', '.join(VOICES)}")
    if args.audition:
        if args.source:
            src = pick_sources(args.source)[0]
        elif inbox_files():
            src = inbox_files()[-1]
        else:
            done = [f for f in DONE.glob("*") if f.suffix.lower() in AUDIO_EXT]
            if not done:
                raise SystemExit("No voice memo to test with. Drop one in DROP HERE/voice memos.")
            src = max(done, key=lambda f: f.stat().st_mtime)
        names = [n for n in VOICES if n.startswith(args.family)] if args.family else list(VOICES)
        audition(src, s, names)
        return
    if args.voice_only:
        files = [args.source] if args.source else sorted(paths.DROP.glob("voice only/*"))
        files = [Path(str(f).strip().strip("'\"")) for f in files]
        files = [f for f in files if f.suffix.lower() in AUDIO_EXT]
        if not files:
            raise SystemExit("Drop your Descript export in DROP HERE/voice only first.")
        for f in files:
            voice_only(f, s, voice)
            if f.parent == paths.DROP / "voice only":
                paths.move_original(f, paths.ORIGINALS / "voice only")
        return
    sources = pick_sources(args.source)
    print(f"{len(sources)} recording(s) to process.")
    for src in sources:
        process(src, s, voice, args)
    print("\nDone.")


if __name__ == "__main__":
    main()
