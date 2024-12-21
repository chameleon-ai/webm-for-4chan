# webm-for-4chan
Webm converter optimized for 4chan.\
Targets 6MB with sound (/wsg/) by default, but 4MB with sound (/gif/) and 4MB without sound are supported.\
Makes .webm (vp9/opus) by default, but .mp4 (h264/aac) is also supported.\
Developed on Linux, probably works on Windows.

## Features
- Precise size calculation, designed to render webms just under the size limit
- Automatic resolution scaling
- Automatic audio bit-rate reduction based on length
- (optional) Automatic volume normalization
- (optional) Automatic cropping
- Precise clipping to the nearest millisecond
- Cut segments out of the middle of the video
- Audio track selection for multi-audio sources
- Subtitle burn-in
- Skip black frames at the start of a video
- Music mode optimized for songs
- Combine static image with audio

## How Does it Work?
It's a simple wrapper for ffmpeg. A precise file size is determined by first rendering the audio, then calculating a target video bit-rate in kbps using the remaining space not taken up by the audio. Then, using 2-pass encoding, it's up to ffmpeg to hit the target size exactly. It's usually very good at hitting the target size without going over, but it's not perfect.

## Installation and Dependencies
As long as you have python, you're good to go. No requirements.txt needed.\
This script just uses the python standard library and makes system calls to ffmpeg and ffprobe.\
Make sure ffmpeg and ffprobe are accessible from your path, that's it.

## Usage
If the video is already clipped and ready to be converted, simply:\
`python webm_for_4chan.py input.mp4`

Combine a static image (or animated gif) with audio:\
`python webm_for_4chan.py image.png song.mp3`

The output will be the name of the input prepended with `_1_`, i.e. `_1_input.webm` or `_2_`, `_3_` etc. if the file already exists.\
Use `--output` to specify a custom file name.

Clip the video starting at 1 hr 23 minutes 45.1 seconds and ending at 1 hr 24 minutes 56.6 seconds:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -e 1:24:56.6`

Or specify a relative 2 minute duration:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -d 2:00`

Start time is 0:00 by default, so you can render the first minute of the clip like this:\
`python webm_for_4chan.py input.mp4 -d 1:00`

End time is also calculated if not specified, so to render from the 5:30 mark until the end of the video:\
`python webm_for_4chan.py input.mp4 -s 5:30`

Render a 30 second anime clip with dual audio, select japanese audio and burn in english soft-subs:\
`python webm_for_4chan.py input.mkv --sub_lang eng --audio_lang jpn -s 12:00 -d 30`

Same as above, but select audio and subs by index instead of language:\
`python webm_for_4chan.py input.mkv --sub_index 0 --audio_index 1 -s 12:00 -d 30`

Use external subtitles:\
`python webm_for_4chan.py input.mkv --sub_file "input.en.ssa"`

Cut a 30 second segment out of the middle of the video starting at 1 minute:\
`python webm_for_4chan.py input.mkv input.mp4 -x "1:00-1:30"`\
You can chain multiple cuts together with ';'\
Cut 2 segments. Cut #1 starting at 1:00 and ending at 1:30, cut #2 starting at 1:45 and ending at 2:00:\
`python webm_for_4chan.py input.mp4 -x "1:00-1:30;1:45-2:00"`\
Make a clip from 1 hour 20 minutes to 1 hour 23 minutes and cut the middle minute out, resulting in a 2 minute final clip:\
`python webm_for_4chan.py input.mp4 -s 1:20:00 -e 1:23:00 -x "1:21:00-1:22:00`\
Note that the timestamps for cutting are always absolute time from the original input.

By default, the script renders up to 6MiB, 400 seconds with sound for wsg.\
To set the size limit to 4MiB, 120 seconds with sound, use `--board gif`\
To set the size limit to 4MiB, 120 seconds with no sound, use `--board other`\
Remove sound altogether with `--no_audio`\
Manually set the file size limit, in MiB, with `--size`, i.e. `--size 5` will target a 5 MiB file.

Make an .mp4 instead  of .webm with the `--mp4` flag or `--codec libx264`\
Enable audio volume normalization with `--normalize` or `-n`\
Skip black frames at the start of the video with `--blackframe`\
Crop using automatic edge detection with `--auto_crop`\
Crop using manually specified boundaries with `--crop`\
Automatically burn-in the first available subtitles, if any exist, with `--auto_subs`\
Print available subtitles with  `--list_subs`\
Print available audio tracks with  `--list_audio`\
Manually specify arbitrary audio and video filters for ffmpeg with `--audio_filter` and `--video_filter`

Type `--help` for a complete list of commands.

## Extra Notes and Quirks
- Audio bit-rate is automatically reduced for long clips. Force high audio bit-rate with `--music_mode`, or specify the exact rate manually with `--audio_rate`
- Image + audio combine mode behaves as if in `--music_mode`. You can still manually specify `--audio_rate`
- Fps cap is automatically reduced for long clips. You can manually specify with `--fps`
- If you don't like the automatically calculated resolution, use the `--resolution` override.
- By default, resolution remains unchanged in image + audio combine mode. You can still manually specify with `--resolution`
- Clipping (`-s`, `-e`, `-d`), `--auto_crop`, and subtitle burn-in are disabled in image + audio combine mode. You can still `--normalize` and apply arbitrary audio and video filters (`-a`, `-v`).
- It's tough to do one-size-fits-all automatic resolution scaling. Currently it is tuned to produce large resolutions, which can cause artifacts if the source has high motion or a lot of colors. If the output is a little too crunchy, I recommend specifying `--resolution` at one notch lower than what it automatically selected (you can find the resolution table at the top of the script)
- If you don't want to resize it at all, use `--no_resize`
- Dynamic resolution calculation will snap to a standard resolution size as defined in the resolution table. You can skip this with `--bypass_resolution_table`
- The resolution calculation method can be altered with `--resize_mode`. All options produce similar results, but `--resize_mode cubic` usually results in lower resolutions than the default of `logarithmic`. Instead of a bit-rate based calculation, a time-based lookup table can also be used with `--resize_mode table`. Note that this doesn't alter how ffmpeg resizes the video, it only affects what target resolution is chosen.
- If you want to see the calculations and ffmpeg commands without rendering the clip, use `--dry_run`
- The script is designed to get as close to the size limit as possible, but sometimes overshoots. If this happens, a warning is printed. Video bit-rate can be adjusted with `--bitrate_compensation`. Usually a compensation of just 2 or 3 is sufficient. If the file is undershooting by a large amount, you can also use a negative number to make the file bigger.
- The vp9 encoder's deadline argument is set to `good` by default. Better quality, but much slower, encoding can be achieved with `--deadline best`
- Use `--fast` to significantly speed up encoding at the expense of quality and rate control accuracy.
- Row based multithreading is enabled by default. This can be disabled with `--no_mt`
- You may notice an additional file 'temp.opus'. This is an intermediate audio file used for size calculation purposes. If normalization is enabled, 'temp.normalized.opus' will also be generated.
- With `--codec libx264`, 'temp.aac' and 'temp.normalized.aac' are generated instead of .opus files.
- When using `-x`/`--cut`, a lossless temporary file of the assembled segments called 'temp.mkv' gets generated.
- When using `-x`/`--cut`, it is currently not possible to burn-in subtitles or manually specify an audio track.
- Currently, image + audio combine mode only makes .webm files (vp9/opus), `--codec libx264` intentionally has no effect.
- The file 'temp.ass' is generated if burning in soft subs. I tried using the subs directly from the video, but this didn't work well when making clips, so I had to resort to exporting to a separate file.
- Subtitle burn-in is mostly tested with ASS subs. If external subs are in a format that ffmpeg doesn't recognize, you'll have to convert them manually.
- Audio will always be re-encoded even if the source is opus. I tried to make ffmpeg's copy option work, but it didn't work well when making clips.
- You may notice that rendering is significantly slower when burning in subtitles. I tried many different settings and ffmpeg is very fragile, this is the only method I could figure out that works consistently.

## Testers Wanted!
I tested this as well as I can, but I don't know how well it works for others. Some anons have tested pre-release versions of this script but any extra feedback is welcome.
