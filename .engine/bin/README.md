Offline install: put the `ffmpeg` and `ffprobe` files here (Apple Silicon builds from https://www.osxexperts.net).
Then in Terminal, from the savr-phone-frame folder:

    chmod +x bin/ffmpeg bin/ffprobe
    xattr -d com.apple.quarantine bin/ffmpeg bin/ffprobe

The second line stops macOS blocking them ("can't be opened because Apple cannot check it").
