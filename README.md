# webm-for-4chan
Webm converter optimized for 4chan.\
Designed for 6MB limit (/wsg/), but 4MB with sound (/gif/) and 4MB without sound are supported.\
Developed on Linux, probably works on Windows.

## Features
- Precise size calculation, designed to render webms just under the size limit
- Automatic resolution scaling based on length
- Automatic bit-rate reduction based on length
- Automatic volume normalization
- Automatic cropping
- Precise clipping to the nearest millisecond
- Audio track selection for multi-audio sources
- Subtitle burn-in
- Music mode optimized for songs

## How Does it Work?
It's a simple wrapper for ffmpeg. A precise file size is determined by first rendering the audio, then calculating a target video bit-rate in kbps using the remaining space not taken up by the audio. Then, using 2-pass encoding, it's up to ffmpeg to hit the target size exactly. It's usually very good at hitting the target size without going over, but it's not perfect.

## Installation and Dependencies
As long as you have python, you're good to go. No requirements.txt needed.\
This script just uses the python standard library and makes system calls to ffmpeg and ffprobe.\
Make sure ffmpeg and ffprobe are accessible from your path, that's it.

## Usage
If the video is already clipped and ready to be converted, simply:\
`python webm_for_4chan.py input.mp4`

The output will be the name of the input prepended with `_1_`, i.e. `_1_input.webm` or `_2_`, `_3_` etc. if the file already exists.
Use `--output` to specify a custom file name.

Clipping the video starting at 1 hr 23 minutes 45.1 seconds and ending at 1 hr 24 minutes 56.6 seconds:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -e 1:24:56.6`

Or specify a relative 2 minute duration:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -d 2:00`

List available subtitles:\
`python webm_for_4chan.py input.mkv --list_subs`

List available audio tracks:\
`python webm_for_4chan.py input.mkv --list_audio`

Render a 30 second anime clip with dual audio, select japanese audio and burn in english soft-subs:\
`python webm_for_4chan.py input.mkv --sub_lang eng --audio_lang jpn -s 12:00 -d 30`

Same as above, but select audio and subs by index instead of language:\
`python webm_for_4chan.py input.mkv --sub_index 0 --audio_index 1 -s 12:00 -d 30`

Use external subtitles:\
`python webm_for_4chan.py input.mkv --sub_file "input.en.ssa"`

By default, the script renders up to 6MiB with sound.\
To render up to 4MiB with sound, use `--mode gif`\
To render up to 4MiB with no sound, use `--mode other`

Enable audio volume normalization with `--normalize`\
Crop using automatic edge detection with `--auto_crop`\
Automatically burn-in the first available subtitles, if any exist, with `--auto_subs`\
Also supports a handful of other ffmpeg video filters with the `--deblock`, `--deflicker`, and `--decimate` options

Type `--help` for list of complete commands.

## Extra Notes and Quirks
- Audio bitrate is automatically reduced for long clips, which is bad for music. Force high audio bit-rate with `--music_mode`.
- If you don't like the automatically calculated resolution, use the `--resolution` override.
- If you don't want to resize it at all, use `--no_resize`
- If you want to see the calculations and ffmpeg commands without rendering the clip, use `--dry_run`
- The script is designed to get as close to the size limit as possible, but sometimes overshoots. If this happens, a warning is printed. Video bit-rate can be adjusted with `--bitrate_compensation`. Usually a compensation of just 2 or 3 is sufficient. If the file is undershooting by a large amount, you can also use a negative number to make the file bigger.
- You may notice an additional file 'temp.opus'. This is an intermediate audio file used for size calculation purposes. If normalization is enabled, temp.normalized.opus will also be generated.
- The file 'temp.ass' is generated if burning in soft subs. I tried using the subs directly from the video container, but this didn't always work properly when making clips, so I had to resort to exporting them to a separate file.
- Subtitle burn-in is mostly tested with ASS subs. If external subs are in a format that ffmpeg doesn't recognize, you'll have to convert them manually.

## Testers Wanted!
I tested this as well as I can, but I don't know how well it works for others. Some anons have tested pre-release versions of this script but any extra feedback is welcome.