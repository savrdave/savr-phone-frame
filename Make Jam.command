#!/bin/bash
# Double-click: turns every voice memo in DROP HERE/voice memos into one video per jam.
cd "$(dirname "$0")"
VENV=.engine/.venv-jam
if [ ! -x $VENV/bin/python ] || ! $VENV/bin/python -c "import parselmouth, faster_whisper" 2>/dev/null; then
  echo "Setting up the jam tool. Needs internet, takes a few minutes."
  [ -x $VENV/bin/python ] || python3 -m venv $VENV
  $VENV/bin/python -m pip install -q --upgrade pip && \
  $VENV/bin/python -m pip install -q faster-whisper pillow numpy praat-parselmouth || {
    echo "Setup failed. Send Claude a screenshot of this window."
    read -n 1 -s -r -p "Press any key to close."; exit 1; }
fi
$VENV/bin/python .engine/jam.py "$@" && { [ -f "DROP HERE/claude/transcript.md" ] && [ ! -f "DROP HERE/claude/edits.json" ] && open "DROP HERE/claude" || open "$([[ " $* " == *" --voice-only "* ]] && echo FINISHED/voice || echo READY)"; }
read -n 1 -s -r -p "Press any key to close."
