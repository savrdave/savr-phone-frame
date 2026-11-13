#!/bin/bash
# Double-click: puts the last finished session back in DROP HERE so Make Jam can render it again.
cd "$(dirname "$0")"
"./Make Jam.command" --redo
