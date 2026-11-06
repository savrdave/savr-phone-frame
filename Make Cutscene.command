#!/bin/bash
# Double-click: makes the SAVR end card from the exact screen your video ends on.
cd "$(dirname "$0")"
echo "Drag in the screen recording your video ends on, then press Enter."
echo "(Just press Enter to use the newest recording in inbox/done.)"
read -r SRC
echo "Name for this video, e.g. montage-A (Enter to skip):"
read -r NAME
echo "Freeze on which second of the recording? (Enter = where the framed video ends)"
read -r AT
python3 make_cutscene.py "$SRC" --name "$NAME" ${AT:+--at "$AT"} && open out/cutscenes
read -n 1 -s -r -p "Press any key to close."
