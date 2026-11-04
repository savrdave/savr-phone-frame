#!/bin/bash
cd "$(dirname "$0")"
./frame.sh
open out
read -n 1 -s -r -p "Done. Press any key to close."
