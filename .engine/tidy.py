#!/usr/bin/env python3
"""Tidy Up: nothing gets deleted. Anything in FINISHED or ORIGINALS older than
14 days moves into an 'older/<year-month>' folder inside the same folder, so the
current week stays on top. Every move is written to .engine/tidy_log.md."""
import datetime, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
cutoff = time.time() - DAYS * 86400
log = paths.ENGINE / "tidy_log.md"
moved = []
for top in (paths.FINISHED, paths.ORIGINALS):
    for folder in [d for d in top.iterdir() if d.is_dir()]:
        for f in folder.iterdir():
            if f.is_file() and not f.name.startswith(".") and f.stat().st_mtime < cutoff:
                month = datetime.date.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m")
                dest = paths.move_original(f, folder / "older" / month)
                moved.append(f"- {f.relative_to(paths.TOP)} -> {dest.relative_to(paths.TOP)}")
if moved:
    with log.open("a") as fh:
        fh.write(f"\n## {datetime.datetime.now():%Y-%m-%d %H:%M}\n" + "\n".join(moved) + "\n")
print(f"Moved {len(moved)} file(s) older than {DAYS} days into 'older' folders. Nothing was deleted.")
for m in moved[:40]:
    print(m)
