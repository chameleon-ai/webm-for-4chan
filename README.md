# webm-for-4chan
Webm converter optimized for 4chan.\
Targets 6MB with sound (/wsg/) by default, but 4MB with sound (/gif/) and 4MB without sound are supported.\
Makes .webm (vp9/opus) by default, but .mp4 (h264/aac) is also supported.\
Developed on Linux, probably works on Windows.

## Features
- Precise size calculation, designed to render webms just under the size limit
- Automatic resolution scaling
- Automatic audio bit-rate reduction based on length
- Automatic stereo or mono mixdown based on audio bit-rate
- (optional) Automatic volume normalization
- (optional) Automatic cropping
- Precise clipping to the nearest millisecond
- Cut segments out of the middle of the video
- Concatenate segments from different parts of the video
- Audio track selection for multi-audio sources
- Subtitle burn-in
- Skip black frames at the start of a video
- Automatically trim silent portions of a video
- Music mode optimized for songs
- Combine static image with audio
- Add/replace audio without re-encoding video

## How Does it Work?
It's a simple wrapper for ffmpeg. A precise file size is determined by first rendering the audio, then calculating a target video bit-rate in kbps using the remaining space not taken up by the audio. Then, using 2-pass encoding, it's up to ffmpeg to hit the target size exactly. It's usually very good at hitting the target size without going over, but it's not perfect.

## Installation and Dependencies
As long as you have python, you're good to go. No requirements.txt needed.\
This script just uses the python standard library and makes system calls to ffmpeg and ffprobe.\
Make sure ffmpeg and ffprobe are accessible from your path, that's it.

On Linux, I recommend adding an alias for easy access, something like this:\
`alias webm-for-4chan="python /path/to/webm-for-4chan/webm_for_4chan.py"`

On Windows, you can drag-and-drop the video onto a .bat file containing the following line:\
`python C:\path\to\webm-for-4chan\webm_for_4chan.py "%~1"`

## Usage
If the video is already clipped and ready to be converted, simply:\
`python webm_for_4chan.py input.mp4`

Combine a static image (or animated gif) with audio:\
`python webm_for_4chan.py image.png song.mp3`

Replace the video's audio (or add audio to a video without sound):\
`python webm_for_4chan.py --audio_replace video.webm audio.mp3`\
Note that this is a special mode where the video is copied, not re-encoded. The duration is limited to the video length.

The output will be the name of the input prepended with `_1_`, i.e. `_1_input.webm` or `_2_`, `_3_` etc. if the file already exists.\
Use `-o`/`--output` to specify a custom file name.

### Context Specific Arguments
For advanced usage, use the standard command-line flags (`-b`, `-x`, `--auto_subs`, etc.)\
However, the script will attempt to parse arguments without flags using context clues:
- If the argument is a file, it's treated as the input filename (`python webm_for_4chan.py input.mp4`)
- If the argument is a single timestamp, it's treated as a duration (`python webm_for_4chan.py input.mp4 30` is equivalent to `python webm_for_4chan.py input.mp4 -d 30`)
- If the argument is two timestamps, the lesser is treated as the start time and the greater is the end time (`python webm_for_4chan.py input.mp4 30 45` is equivalent to `python webm_for_4chan.py input.mp4 -s 30 -e 45`)
- If the argument is a timestamp segment (timestamps separated by a dash, `1:00-2:00`), it's treated as a `-c`/`--concat` segment. (`python webm_for_4chan.py input.mp4 30-45` is equivalent to `python webm_for_4chan.py input.mp4 -c 30-45`)

### Clipping
Use `-s`/`--start` to specify a starting timestamp and `-e`/`--end` to specify an ending timestamp.\
Or specify `-c` to specify a segment (see section on Cutting or Concatenating Segments below).

Clip the video starting at 1 hr 23 minutes 45.1 seconds and ending at 1 hr 24 minutes 56.6 seconds:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -e 1:24:56.6`\
Or using context specific arguments:\
`python webm_for_4chan.py input.mp4 1:23:45.1 1:24:56.6`

Note that using `-c` with one segment is equivalent:\
`python webm_for_4chan.py input.mp4 -c "1:23:45.1-1:24:56.6"`\
Or using context specific arguments:\
`python webm_for_4chan.py input.mp4 "1:23:45.1-1:24:56.6"`

Specify a relative 2 minute duration using `-d`/`--duration`:\
`python webm_for_4chan.py input.mp4 -s 1:23:45.1 -d 2:00`

Start time is 0:00 by default, so you can render the first minute of the clip like this:\
`python webm_for_4chan.py input.mp4 -d 1:00`\
Or using context specific arguments:\
`python webm_for_4chan.py input.mp4 1:00`

End time is also calculated if not specified, so to render from the 5:30 mark until the end of the video:\
`python webm_for_4chan.py input.mp4 -s 5:30`

### Subtitle Burn-in
Render a 30 second anime clip with dual audio, select japanese audio and burn in english soft-subs:\
`python webm_for_4chan.py input.mkv --sub_lang eng --audio_lang jpn -s 12:00 -d 30`

Same as above, but select audio and subs by index instead of language:\
`python webm_for_4chan.py input.mkv --sub_index 0 --audio_index 1 -s 12:00 -d 30`

If you don't know the index or language, use `--list_subs` or `--list_audio`

Use external subtitles:\
`python webm_for_4chan.py input.mkv --sub_file "input.en.ssa"`

### Cutting or Concatenating Segments
You can trim the video using 2 different methods: `-x`/`--cut` and `-c`/`--concat`/`--clip`\
Cut means that the specified segments will be removed.\
Concat means that only the specified segments will be kept (the opposite of cut)
- Specify segments using a timestamp range, i.e. `"1:00-2:00"`
- Chain multiple segments together with ';', i.e. `"1:00-2:00;2:05-2:10"`
- Note that the segments are always absolute time from the original input.
- You must specify a start and end timestamp for the segment, separated by '-'.
- Segments must be in chronological order and must start after the `-s` start time.
- It is highly recommended that you specify a `-s`/`--start` and `-e`/`--end` time even when using concat. The start and end time are passed to ffmpeg to trim the video before processing the segments, greatly increasing the speed and efficiency of the concat operation.

Cut a 30 second segment out of the middle of the video starting at 1 minute:\
`python webm_for_4chan.py input.mkv input.mp4 -x "1:00-1:30"`

Cut 2 segments. Cut #1 starting at 1:00 and ending at 1:30, cut #2 starting at 1:45 and ending at 2:00:\
`python webm_for_4chan.py input.mp4 -x "1:00-1:30;1:45-2:00"`

Make a clip from 1:20:00 to 1:23:00 and cut the middle minute out, resulting in a 2 minute final clip:\
`python webm_for_4chan.py input.mp4 -s 1:20:00 -e 1:23:00 -x "1:21:00-1:22:00"`

Concatenate a segment from 1:00:05 to 1:00:10 and a segment from 1:28:30 to 1:28:45, creating a 20 second final clip:\
`python webm_for_4chan.py input.mp4 -s 1:00:00 -e 1:30:00 -c "1:00:05-1:00:10;1:28:30-1:28:45"`

### Trimming Silence
You can automatically cut silent portions of the video using `--trim_silence`. There are 4 options:
- `--trim_silence start` trims the beginning of the video, advancing the specified start time
- `--trim_silence end` trims the end of the video, reducing the specified end time or duration
- `--trim_silence start_and_end` does both of the above
- `--trim_silence all` trims all detected silence, even in the middle of the video. Note that this option overrides any manual cuts from the `-x`/`--cut` feature. This option can potentially take a long time if there are a lot of segments to cut out.

### Changing Target Size and Removing Sound
By default, the script renders up to 6MiB, 400 seconds with sound for wsg.\
To set the size limit to 4MiB, 300 seconds with sound, use `--board gif`\
To set the size limit to 4MiB, 120 seconds with no sound, use `--board other`\
Remove sound altogether with `--no_audio`\
Manually set the file size limit, in MiB, with `--size`, i.e. `--size 5` will target a 5 MiB file.\
Reduce the target bit-rate with `-b`/`--bitrate-compensation`, i.e. `-b 5` will subtract 5 kbps from the automatically calculated bit-rate. This will cause a slight reduction in file size.

### Miscellaneous Features
Make an .mp4 instead  of .webm with the `--mp4` flag or `--codec libx264`\
Enable audio volume normalization with `-n`/`--normalize`\
Disable automatic audio mixdown with `--no_mixdown` or `--mixdown same_as_source`\
Force stereo mixdown with `--stereo` or `--mixdown stereo`\
Force mono mixdown with `--mono` or `--mixdown mono`\
Skip black frames at the start of the video with `--blackframe`\
Crop using automatic edge detection with `--auto_crop`\
Crop using manually specified boundaries with `--crop`\
Automatically burn-in the first available subtitles, if any exist, with `--auto_subs`\
Print available built-in subtitles with  `--list_subs`\
Print available audio tracks with  `--list_audio`\
Specify arbitrary filters for ffmpeg with `-a`/`--audio_filter` and `-v`/`--video_filter`\
For fun, try `--first_second_every_minute`, inspired by the youtube channel FirstSecondEveryMinute (Warning: This can take a while)\
Keep generated temp files with `-k`/`--keep_temp_files`

Type `--help` for a complete list of commands.

## Extra Notes and Quirks
- The script is designed to get as close to the size limit as possible, but sometimes overshoots. If this happens, a warning is printed. Video bit-rate can be adjusted with `-b`/`--bitrate_compensation`. Usually a compensation of just 2 or 3 is sufficient. If the file is undershooting by a large amount, you can also use a negative number to make the file bigger.
- Audio bit-rate is automatically reduced for long clips. Force high audio bit-rate with `--music_mode`, or specify the exact rate manually with `--audio_rate`
- If your source is surround sound, it's highly recommended to use `--music_mode` or `--stereo` especially for clips over 2:00. The default audio bit-rate is meant for stereo and can cause surround sources to sound too crunchy.
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
- You will get an error if you try to render a clip longer than the max duration of the target board. This can be disabled with `--no_duration_check`, but will result in a file not uploadable to 4chan. The max duration bypass hack for 4chan is not supported as it results in a corrupted file.
- The vp9 encoder's deadline argument is set to `good` by default. Better quality, but much slower, encoding can be achieved with `--deadline best`
- Use `--fast` to significantly speed up encoding at the expense of quality and rate control accuracy.
- Row based multithreading is enabled by default. This can be disabled with `--no_mt`
- You may notice an additional file 'temp.opus'. This is an intermediate audio file used for size calculation purposes. If normalization is enabled, 'temp.normalized.opus' will also be generated.
- With `--mp4`/`--codec libx264`, 'temp.aac' and 'temp.normalized.aac' are generated instead of .opus files.
- If any temp files already exist (such as when using `-k`), a new one will be made with an incrementing number (temp.1.opus, temp.2.opus, etc.)
- Expect size overshoots much more often with `--mp4`/`--codec libx264`. This is a result of libx264's rate control accuracy being much more sloppy than libvpx-vp9.
- When using `-x`/`--cut` or `-c`/`--concat`, a lossless temporary file of the assembled segments called 'temp.mkv' gets generated.
- When using `-x`/`--cut` or `-c`/`--concat` it is currently not possible to burn-in subtitles or to specify an audio track besides the default.
- Currently, image + audio combine mode only makes .webm files (vp9/opus), `--codec libx264` intentionally has no effect.
- The file 'temp.ass' is generated if burning in soft subs. I tried using the subs directly from the video, but this didn't work well when making clips, so I had to resort to exporting to a separate file.
- Subtitle burn-in is mostly tested with ASS subs. If external subs are in a format that ffmpeg doesn't recognize, you'll have to convert them manually.
- Audio will always be re-encoded even if the source is opus. I tried to make ffmpeg's copy option work, but it didn't work well when making clips.
- You may notice that rendering is significantly slower when burning in subtitles. I tried many different settings and ffmpeg is very fragile, this is the only method I could figure out that works consistently.

## Tips, Tricks, and References
- If you're unsure about your `-s`/`--start` and `-e`/`--end` timestamps, try a `--dry_run -k` and inspect temp.opus to see if the audio is the right slice that you want.
- For long videos (over about 3 minutes), it's usually beneficial to add `-v spp`
  - https://ffmpeg.org/ffmpeg-filters.html#spp-1
- Filter graph building for `-c`/`--concat` and `-x`/`--cut` were made possible through this valuable reference:
  - https://github.com/sriramcu/ffmpeg_video_editing
- Specify `-k` when running `-c`/`--concat` or`-x`/`--cut` to keep the original file containing the spliced segments. This will save time if you are unsatisfied with the final result and need to re-encode.
- There is a known issue with some varieties of surround sound for libopus. I have attempted to detect and correct for this issue, but some features in the script are incompatible with this workaround. For more information, see:
  - https://trac.ffmpeg.org/ticket/5718
- More information on the audio normalization technique used by `-n`/`--normalize`:
  - https://wiki.tnonline.net/w/Blog/Audio_normalization_with_FFmpeg
  - https://superuser.com/questions/1312811/ffmpeg-loudnorm-2pass-in-single-line
- The `--blackframe` option uses ffmpeg's blackdetect and blackframe filters. For more information:
  - https://ffmpeg.org/ffmpeg-filters.html#blackdetect
- The `--auto_crop` option makes use of ffmpeg's cropdetect filter. For more information:
  - https://ffmpeg.org/ffmpeg-filters.html#cropdetect
- The `--trim_silence` option uses ffmpeg's silencedetect filter. For more information:
  - https://ffmpeg.org/ffmpeg-filters.html#silencedetect
- You can do basically anything you want with `-v`/`--video_filter` and `-a`/`--audio_filter` as they are passed directly to ffmpeg's -vf and -af arguments. For instance, if you want to reverse the clip, just specify `-v reverse -a areverse`
  - https://ffmpeg.org/ffmpeg-filters.html

## Testers Wanted!
I tested this as well as I can, but I don't know how well it works for others. Any feedback is welcome. If you come across a bug, please give me the printed output and provide the input file along with your command-line arguments if possible. There are a lot of quirks depending on the exact input, so having the original file goes a long way in being able to replicate the issue.
