#!/bin/bash
# Double-click: puts your chosen voice on audio that's already edited (Descript exports).
# Drop files in DROP HERE/voice only. No cuts, no captions. Results in FINISHED/voice.
cd "$(dirname "$0")"
"./Make Jam.command" --voice-only
