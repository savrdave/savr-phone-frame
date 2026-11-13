#!/bin/bash
# Frames every screen recording in "DROP HERE/screen recordings".
# Results: FINISHED/framed/<date>_framedNN.mov. Used recordings move to ORIGINALS/screen recordings.
ENGINE="$(cd "$(dirname "$0")" && pwd)"
TOP="$(dirname "$ENGINE")"
cd "$ENGINE"
BEZEL=$(ls bezels/*.png 2>/dev/null | head -1)
if [ -z "$BEZEL" ]; then echo "The iPhone bezel PNG is missing from .engine/bezels."; exit 1; fi
IN="$TOP/DROP HERE/screen recordings"; OUT="$TOP/FINISHED/framed"; DONE="$TOP/ORIGINALS/screen recordings"
mkdir -p "$IN" "$OUT" "$DONE"
FMT=""
if [ "$(uname)" = "Darwin" ] && [[ " $* " != *" --format "* ]]; then FMT="--format hevc"; fi
python3 frame_phone.py "$IN" --bezel "$BEZEL" --out "$OUT" --move-done --done-dir "$DONE" --dated-names \
  --trim-start "${TRIM_START:-0.5}" --trim-end "${TRIM_END:-0.5}" --end-card $FMT "$@"
