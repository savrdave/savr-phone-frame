# SAVR phone frame

Turns an iPhone screen recording into a video of a real iPhone with the recording playing inside it, on a transparent background. The status bar is replaced with a clean one (9:41, full signal, 5G, 85% battery), so the red recording dot is gone.

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
| `--format prores` | Huge ProRes 4444 .mov with transparency, if an editor won't read the default |
| `--format webm` | Transparent video for websites |
| `--format png` | One still image of the framed phone, for thumbnails and carousels |
| `--time 10:24` | Change the time in the fake status bar (default 9:41) |
| `--network LTE` | Change the network label (default 5G) |
| `--battery 60` | Change the battery level (default 85) |
| `--statusbar cover` | Blank bar instead of a fake one |
| `--statusbar none` | Leave the real status bar alone |
| `--height 1600` | Resize the output |
| `--audio` | Keep the recording's sound |

By default `frame.sh` trims 0.5 sec off each end, which cuts the Control Center swipe. To change it: `TRIM_START=1.5 TRIM_END=1 ./frame.sh`

## Notes

- On a Mac, `./frame.sh` makes small transparent HEVC .mov files (tens of MB). `--format prores` gives ProRes 4444 instead: every editor reads it, but a 30 sec clip can be 1 to 2 GB.
- The fake status bar takes its background colour from your app just below the status bar, and switches between white and black icons to match. If an app has a gradient or photo at the top, set it manually with `--statusbar-colour 0xffffff`.
- Works with any bezel PNG that has a transparent screen opening, not just Apple's.
