# webm-for-4chan
Webm converter optimized for 4chan.\
Works mostly for 6MB limit (/wsg/), but 4MB with sound (/gif/) and 4MB without sound are supported.\
Developed on Linux, probably works on Windows.\

## Features
- Precise size calculation, designed to render webms just under the size limit
- Automatic resolution scaling based on length
- Automatic bit-rate reduction based on length
- Automatic volume normalization
- Automatic cropping
- Precise clipping to the nearest millisecond
- Audio track selection for multi-aduio sources
- Subtitle burn-in
- Music mode optimized for songs

## How Does it Work?
It's a simple wrapper for ffmpeg. A precise file size is determined by first rendering the audio, then calculating a target video bit-rate in kbps using the remaining space not taken up by the audio. Then, using 2-pass encoding, it's up to ffmpeg to hit the target size exactly. It's usually very good at hitting the target size without going over, but it's not perfect.

## Installation and Dependencies
As long as you got python you're good to go.\
This script just uses the python standard library and makes system calls to ffmpeg and ffprobe.\
Make sure ffmpeg and ffprobe are accessible from your path, that's it.

## Usage
If the video is already clipped and ready to be converted, simply:\
`python webm_for_4chan.py input.mp4`
The output will be the name of the input prepended with `_1_`, i.e. `_1_input.webm`

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

By default, the script renders up to 6MB with sound.\
Render in 4MB mode with sound:\
`python webm_for_4chan.py input.mp4 --mode gif`
Render in 4MB mode with no sound:\
`python webm_for_4chan.py input.mp4 --mode other`

Type `--help` for list of complete commands.

## Extra Notes and Quirks
- Audio volume is normalized by default. If this is undesirable, use `--no_normalize`
- Audio bitrate is automatically reduced for long clips, which is bad for music. Force high audio bit-rate with `--music_mode` (this also disables normalization)
- If you don't like the automatically calculated resolution, use the `--resolution` override.
- If you want to see the calculations and ffmpeg commands without rendering the clip, use `--dry_run`
- The script is designed to get as close to the size limit as possible, but sometimes overshoots. If this happens, a warning is printed. Video bit-rate can be adjusted with `--bitrate_compensation`. Usually a compensation of just 2 or 3 is sufficient. If the file is undershooting by a large amount, you can also use a negative number to make the file bigger.
- You may notice some droppings in your current working directory like temp.opus and temp.normalized.opus. These are the intermediate audio files used for size calculation purposes. They're not removed automatically because they're currently useful to me for debugging purposes.
- Even if you manually specify an output file name, it will be prepended with `_1_` and appended with `.webm`, which can lead to weird file names like "_1_output.webm.webm". I'll have to overhaul the output file name logic at some point, just be aware of this known issue.