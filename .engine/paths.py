"""Where everything lives. The top folder only shows buttons and three folders."""
import datetime
import json
import re
import shutil
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
TOP = ENGINE.parent

DROP = TOP / "DROP HERE"
SCREEN_IN = DROP / "screen recordings"
VOICE_IN = DROP / "voice memos"
SHOTS_IN = DROP / "screenshots"   # headline screenshots, matched to jams in the order taken
BACKGROUNDS = DROP / "backgrounds"

FINISHED = TOP / "FINISHED"
FRAMED = FINISHED / "framed"
CUTSCENES = FINISHED / "cutscenes"
JAMS = TOP / "READY"          # finished videos waiting to be posted
POSTED = TOP / "POSTED"       # drag a video here once it's posted
TRANSCRIPTS = FINISHED / "transcripts"

ORIGINALS = TOP / "ORIGINALS"
SCREEN_DONE = ORIGINALS / "screen recordings"
VOICE_DONE = ORIGINALS / "voice memos"

FRAMED_LOG = ENGINE / "framed_log.json"


def today():
    return datetime.date.today().isoformat()


def next_number(folder, prefix):
    """Next free number for e.g. 2026-11-07_framed -> 3 if 01 and 02 exist."""
    nums = [int(m.group(1)) for f in Path(folder).glob(prefix + "*")
            for m in [re.match(re.escape(prefix) + r"(\d+)", f.name)] if m]
    return max(nums, default=0) + 1


def move_original(src, dest_dir, new_stem=None):
    """Move a used file into ORIGINALS without ever overwriting another one."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = new_stem or src.stem
    target, k = dest_dir / f"{stem}{src.suffix}", 1
    while target.exists():
        k += 1
        target = dest_dir / f"{stem} ({k}){src.suffix}"
    shutil.move(str(src), str(target))
    return target


def log_framed(framed_name, original_name):
    log = json.loads(FRAMED_LOG.read_text()) if FRAMED_LOG.exists() else {}
    log[framed_name] = original_name
    FRAMED_LOG.write_text(json.dumps(log, indent=1))


def original_for(framed_path):
    log = json.loads(FRAMED_LOG.read_text()) if FRAMED_LOG.exists() else {}
    name = log.get(Path(framed_path).stem)
    if name:
        for d in (SCREEN_DONE, SCREEN_IN):
            if (d / name).exists():
                return d / name
    return None


# ---------------------------------------------------------------- names
# Outputs are named "<date> <what it shows>", e.g. "2026-11-10 drawer savings.mov",
# so you can tell them apart in Finder without opening them.
AUTO_NAME = re.compile(r"^(ScreenRecording|RPReplay|IMG_|New Recording|Screen Recording|Untitled)", re.I)


def clean_label(text):
    text = re.sub(r"[^A-Za-z0-9 $%+&-]", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)[:40].strip()


ASK_FOR_NAMES = False  # jams name themselves; framed clips just get the time


def ask_label(video):
    """Use the file's own name if you renamed it. Otherwise show it and ask."""
    video = Path(video)
    if not AUTO_NAME.match(video.stem):
        return clean_label(video.stem) or "recording"
    import subprocess, sys
    label = ""
    if ASK_FOR_NAMES and sys.stdin.isatty():
        viewer = None
        try:  # quick preview so you can see what it is
            viewer = subprocess.Popen(["qlmanage", "-p", str(video)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
        try:
            label = input(f"  What's in this one? 2 to 4 words (e.g. drawer savings), Enter to skip: ")
        except EOFError:
            label = ""
        if viewer:
            viewer.terminate()
    label = clean_label(label)
    if not label:
        made = datetime.datetime.fromtimestamp(video.stat().st_mtime)
        label = f"framed {made:%-I.%M%p}".lower()
    return label


def unique_stem(folder, stem):
    folder, k, out = Path(folder), 1, stem
    while any(folder.glob(glob_escape(out) + ".*")):
        k += 1
        out = f"{stem} {k}"
    return out


def glob_escape(s):
    return re.sub(r"([\[\]*?])", r"[\1]", s)


# ---------------------------------------------------------------- cleanup
# Runs by itself every time you press a button. Working files (framed clips, old
# cutscenes, voice tests) and posted videos go to the Mac Trash after 30 days,
# so nothing piles up. ORIGINALS, B-ROLL and READY are never touched.
KEEP_DAYS = 30


def auto_clean(days=KEEP_DAYS):
    import time
    trash = Path.home() / ".Trash"
    if not trash.exists():
        return
    cutoff = time.time() - days * 86400
    folders = [FINISHED / "framed", FINISHED / "cutscenes", FINISHED / "voice", FINISHED / "voice-test", POSTED]
    gone = []
    for folder in folders:
        if not folder.exists():
            continue
        for f in folder.rglob("*"):
            if f.is_file() and not f.name.startswith(".") and f.stat().st_mtime < cutoff:
                target, k = trash / f.name, 1
                while target.exists():
                    k += 1
                    target = trash / f"{f.stem} ({k}){f.suffix}"
                try:
                    shutil.move(str(f), str(target))
                    gone.append(f.name)
                except OSError:
                    pass
    if gone:
        log = ENGINE / "cleanup_log.md"
        with log.open("a") as fh:
            fh.write(f"\n## {datetime.datetime.now():%Y-%m-%d %H:%M} (to Trash)\n" + "".join(f"- {g}\n" for g in gone))
        print(f"(Moved {len(gone)} working file(s) older than {days} days to the Trash.)")
