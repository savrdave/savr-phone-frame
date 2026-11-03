# SAVR phone frame

Turns an iPhone screen recording into a video of a real iPhone with the recording playing inside it, on a transparent background. The status bar is hidden, so the red recording dot is gone.

Drop it straight into CapCut or Descript on top of any background, b-roll or headline card.

## One-time setup (Mac, about 10 min, works offline)

You need Python 3 (already on your Mac if you've used Xcode; check with `python3 --version`) and ffmpeg. No other installs.

**ffmpeg, offline:** download the Apple Silicon `ffmpeg` and `ffprobe` zips from https://www.osxexperts.net, unzip them, and put both files in `bin/`. Then run the two commands in `bin/README.md`.
**ffmpeg, online:** `brew install ffmpeg` works too.

**Phone frame:** go to https://developer.apple.com/design/resources/, find **Product Bezels**, download the newest iPhone, and put the portrait PNG (the one with the see-through screen) in `bezels/`.

Make the script runnable once: `chmod +x frame.sh`

## Every time

1. Screen record on your iPhone and AirDrop it to the Mac.
2. Drop it into `inbox/`.
3. Run `./frame.sh`
4. Your framed videos are in `out/`. The originals move to `inbox/done/`.

## Options

Add any of these after `./frame.sh`:

| Option | What it does |
|---|---|
| `--format hevc` | Small transparent .mov that plays on iPhone (Mac only). Use this if CapCut on your phone shows a black background with the default. |
| `--format webm` | Transparent video for websites |
| `--format png` | One still image of the framed phone, for thumbnails and carousels |
| `--statusbar left` | Only hide the left side (the clock and red dot). Keeps the battery and signal. |
| `--statusbar none` | Leave the status bar alone |
| `--height 1600` | Resize the output |
| `--audio` | Keep the recording's sound |

By default `frame.sh` trims 0.5 sec off each end, which cuts the Control Center swipe. To change it: `TRIM_START=1.5 TRIM_END=1 ./frame.sh`

## Notes

- The default output is ProRes 4444 (.mov with transparency). It's big but every editor reads it. If CapCut shows a black box instead of transparency, rerun with `--format hevc`.
- The status bar cover takes its colour from your app just below the status bar, so it blends in. If an app has a gradient or photo at the top, set it manually with `--statusbar-colour 0xffffff`.
- Works with any bezel PNG that has a transparent screen opening, not just Apple's.
