#!/bin/bash
# Double-click: frames every screen recording in DROP HERE/screen recordings.
cd "$(dirname "$0")"
.engine/frame.sh && open "FINISHED/framed"
read -n 1 -s -r -p "Done. Press any key to close."
