# Jam tool

One long recording in, one captioned 9:16 video per jam out.

## Record
1. Say "jam seven", then talk in your own words.
2. Start the whole jam over: say "jam seven" again. The last one wins.
3. Tripped on a few words: pause, back up to the start of the phrase, say it again. "I use, I use SAVR" becomes "I use SAVR".
4. Swap one word: pause, then say the short phrase again with the new word. "I prep 5 meals... I portion 5 meals" keeps only the second. Works for phrases up to 5 words.
5. Rewording a whole sentence: the tool can't tell that apart from saying two different things, so restart the jam instead.
6. Keep going: "jam eight", and so on. One file for the whole session. Pauses and ums get cut automatically.

## Make the videos
1. Drop the recording in `DROP HERE/voice memos/` (several files is fine too).
2. Double-click **Make Jam.command**.
3. Videos land in `FINISHED/jams/` as `<date>_jam07.mp4`, plus a transcript for writing post captions.

## What's on screen (Nov 10)
Every jam now comes out finished: visuals, captions, then the SAVR end card.

- **Your own visuals:** drop files in `DROP HERE/visuals/` named with the jam number first, in the order you want them:
  `07 a headline.png`, `07 b drawer.mov`. Each gets an equal share of the jam.
  Screen recordings go into the iPhone frame automatically. Wide images (headline screenshots) sit on SAVR green with a slow zoom.
- **Hook headline:** a text file `07 hook.txt` puts that line big at the top for the first 2.5 seconds (for people watching muted).
  It also becomes the file name.
- **Nothing dropped:** b-roll from the `B-ROLL` folder, picked to match what you said, a new clip every 3.5 seconds.
  Clips starting with `face -` are never picked automatically.
- **End card:** if the jam ends on a screen recording, the end card freezes on that exact screen. Otherwise the standard one.
  Turn it off with `"cutscene": false` in jam_settings.json. Turn off auto b-roll with `"auto_broll": false`.
- **Names:** `2026-11-10 jam07 grocery prices went up.mp4` (the hook, or your first few words).

## B-ROLL folder
Name clips `<type> - <what's in it>.mov`, e.g. `price tag - mushrooms 2.97 pointing.mov`.
Types the picker knows: price tag, produce, shelf, shelf grab, frozen, checkout, cart, food, meat, face.

## Pick your voice
Double-click **Voice Test.command**. It makes 20 seconds of your latest recording in every preset in `FINISHED/jams/voice-test/`. Put the one you like in `.engine/jam_settings.json` as `"voice"`.

Presets: natural, broadcast, warm, smooth, bright, late-night.

## Settings (`.engine/jam_settings.json`)
- `voice`: one of the presets above.
- `default_background`, and per jam: `"jams": {"7": {"background": "kitchen.mp4"}}`. Files go in `DROP HERE/backgrounds/`. Images get a slow zoom, videos loop. Nothing set = SAVR green.
- `silence_db` ("auto"), `min_pause` (0.25), `keep_pause` (0.18): how hard pauses get cut.
- `caption_size`, `caption_y`: caption size and height.

The "unauthenticated requests to the HF hub" message on the first run is harmless. It's just the speech model downloading.
