# webm-for-4chan Advanced Feature Branch
THE ADVANCED FEATURE BRANCH IS HIGHLY EXPERIMENTAL!\
COMPATIBILITY IS NOT GUARANTEED FOR YOUR SYSTEM!

## Advanced Features
- Smart mono mixdown and audio bitrate calculation
- Auto Transcript
- Auto Translate
- "vocal trim" mode, which will trim the silence in between sentences while ignoring noise from bgm
- "bgm swap" mode, which will replace any background music and keep the vocals

Each advanced feature module can also be run stand-alone on the command-line.

## Installation and Dependencies
Note that the advanced feature branch needs a virtual environment and pytorch to function.\
It also requires that the current working directory be the top of the webm-for-4chan directory as there are some hard-coded path assumptions.\
If you want to only use the standard library, stick with the main branch.

- Clone this repository and `cd` to the top level.
- Clone the submodules using `git submodule update --init`. This should produce the `advanced/ultimatevocalremovergui` directory. (submodules are pointers to other repositories at a specific commit)
- Create and activate python virtual environment:
  - `python -m venv venv`
  - `source venv/bin/activate` or `venv\Scripts\activate.bat`
- Install pytorch using the recommended command from the website:
  - https://pytorch.org/
- Install the dependencies:
  - `pip install -r requirements.txt`

## Usage
Usage is the same as the main branch, with extra flags

### Smart Mono Mixdown
In an effort to reduce file size, audio analysis will be run that compares the two channels (if the input is stereo). If the channels are substantially identical, the audio will be mixed down to mono and the audio bitrate reduced. Further analysis will be run at different bitrates (from 80kbps to 32kbps) and if the audio quality hasn't substantially degraded, the bitrate will be reduced further. For inputs that are mostly talking and don't require high dynamic range, this can result in very low bitrates and thus very low audio sizes.

### Transcript, Translate, and Find
- `--transcript` will run whisper transcription. An .srt subtitle file will be generated with the transcript and the subtitles will be burned-in.
  - `--no_burn_in` will disable transcript burn-in, only producing the .srt file
  - `--language` explicitly specifies the transcript language, i.e. `--language ja` will make sure that it tries to interpret the source as Japanese. I'm not sure which languages work, I've only tested English and Japanese. The model used is whisper-large-v3-turbo, so refer to the model card for supported languages.
  - `--prompt` will specify the initial prompt used by whisper. This can be used to condition the model on a style or initialize it with certain proper nouns that it otherwise wouldn't know.
  - `--uvr` will run a first pass of Ultimate Vocal Remover, then put transcribe the vocal segment. Usually you don't have to bother with this.
- `--find` will search the transcript for all instances of a matching string, i.e. `--find the` will return all instances of 'the' and splice a video with all instances of the word together.
- `--translate` will run the transcript through Google translate. There is no need to specify `--transcript` in this case.
  - It will burn-in the translated subtitles by default. Disable this with `--no_burn_in`
- `--save_transcript` will save the transcript to a json file that can be loaded with `--load_transcript`. The file will be named after the input file in the same manner as the subtitles.
- `--load_transcript` will skip the whisper transcription step and load from file, allowing functions like `--find` and `--translate` to be used.
  - An example of saving and then using the transcript:
  - `python webm_for_4chan.py input.mp4 --transcribe --save_transcript --dry_run`
  - `python webm_for_4chan.py input.mp4 --find "hello" --load_transcript input.en.json`
- Note that `--dry_run` only skips the last step of webm encoding. Useful if you want to transcribe / translate first and encode later.

### Vocal Trim
- The `--vocal_trim` flag will run Ultimate Vocal Remover on the audio track and then split the video on silence detected in the vocal track.
- `--trim_mode` will alter what happens to the instrumental track.
  - `--trim_mode all` will trim everything like you expect, the instrumental and vocal tracks are trimmed in the same places and audio will remain synchronized.
  - `--trim_mode vocal_only` will keep only the vocal track and discard the instrumental track.
  - `--trim_mode continuous_instrumental` will trim vocals and reassemble with untrimmed instrumental track. Ideal for light bgm that doesn't have to remain in sync with the vocals.
  - `--trim_mode substitute_instrumental` will swap the instrumental track with something else entirely. If you use this option, you also have to specify `--substitute_instrumental file.mp3` where `file.mp3` is the name of the new instrumental track you want to use.
- `--instrumental_gain` will adjust the volume of the instrumental track in dB, i.e. `--instrumental_gain -6` will lower the instrumental volume by 6dB.

### BGM Swap
- `--bgm_swap` functions similar to `--audio_replace` but uses UVR to replace only the instrumental track while preserving the vocals. Specify the path to the bgm you want to put in, i.e. `--bgm_swap file.mp3`
  - `--bgm_gain` will adjust the volume of the bgm track in dB. Usually if you're using loud music over quiet talking I recommend something like `--bgm_gain -12`
  - Note that the input video stream is copied, not re-encoded. Only the audio is processed.
  - The standard method of calculating the output audio bitrate applies here. You can override it with `--music_mode` or `--audio_rate`.

