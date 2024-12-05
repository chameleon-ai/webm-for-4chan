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
- Music mode optimized for songs with static images

## Installation and Dependencies
As long as you got python you're good to go.\
This script just uses the python standard library and makes system calls to ffmpeg and ffprobe.\
Make sure ffmpeg and ffprobe are accessible from your path, that's it.

## Usage
If the video is already clipped and ready to be converted, simply:\
`python webm_for_4chan.py input.mp4`\
The output will be the name of the input prepended with `_1_`, i.e. `_1_input.webm`\

Clipping the video starting at 1 hr 23 minutes 45.1 seconds and ending at 1 hr 24 minutes 56.6 seconds:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -e 1:24:56.6`\

Or specify a relative 2 minute duration:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -d 2:00`\

List available subtitles:\
`python webm_for_4chan.py input.mkv --list_subs`\

List available audio tracks:\
`python webm_for_4chan.py input.mkv --list_audio`\

Render a 30 second anime clip with dual audio, select japanese audio and burn in english soft-subs:\
`python webm_for_4chan.py input.mkv --sub_lang eng --audio_lang jpn -s 12:00 -d 30`\

By default, the script renders up to 6MB with sound.\
Render in 4MB mode with sound:\
`python webm_for_4chan.py input.mp4 --mode gif`\
Render in 4MB mode with no sound:\
`python webm_for_4chan.py input.mp4 --mode other`\