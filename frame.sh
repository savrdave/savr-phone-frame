#!/bin/bash
# Drop screen recordings into inbox/, run ./frame.sh, grab results from out/.
# Processed recordings move to inbox/done/.
cd "$(dirname "$0")"
BEZEL=$(ls bezels/*.png 2>/dev/null | head -1)
if [ -z "$BEZEL" ]; then echo "Put an Apple iPhone bezel PNG in bezels/ first (see README)."; exit 1; fi
mkdir -p inbox out
python3 frame_phone.py inbox --bezel "$BEZEL" --out out --move-done --trim-start "${TRIM_START:-0.5}" --trim-end "${TRIM_END:-0.5}" "$@"
