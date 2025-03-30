# webm-for-4chan Advanced Feature Branch
THE ADVANCED FEATURE BRANCH IS HIGHLY EXPERIMENTAL!\
COMPATIBILITY IS NOT GUARANTEED FOR YOUR SYSTEM!

## Advanced Features
- Smart mono mixdown and audio bitrate calculation
- "vocal trim" mode, which will trim the silence in between sentences while ignoring noise from bgm

Each advanced feature module can also be run stand-alone on the command-line.

## Installation and Dependencies
Note that the advanced feature branch needs a virtual environment and pytorch to function.\
It also requires that the current working directory be the top of the webm-for-4chan directory as there are some hard-coded path assumptions.\
If you want to only use the standard library, stich with the main branch.\

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
In an effort to safe space, audio analysis will be run that compares the two channels (if the input is stereo). If the channels are substantially identical, the audio will be mixed down to mono and the audio bitrate reduced. Further analysis will be run at different bitrates (from 80kbps to 32kbps) and if the audio quality hasn't substantially degraded, the bitrate will be reduced further. For inputs that are mostly talking and don't require high dynamic range, this can result in very low bitrates and thus very low audio sizes.

### Vocal Trim
- The `--vocal_trim` flag will run Ultimate Vocal Remover on the audio track and then split the video on silence detected in the vocal track.
- `--trim_mode` will alter what happens to the instrumental track.
  - `--trim_mode all` will trim everything like you expect, the instrumental and vocal tracks are trimmed in the same places and audio will remain synchronized.
  - `--trim_mode vocal_only` will keep only the vocal track and discard the instrumental track.
  - `--trim_mode continuous_instrumental` will trim vocals and reassemble with untrimmed instrumental track. Ideal for light bgm that doesn't have to remain in sync with the vocals.
  - `--trim_mode substitute_instrumental` will swap the instrumental track with something else entirely. If you use this option, you also have to specify `--substitute_instrumental file.mp3` where `file.mp3` is the name of the new instrumental track you want to use.
- `--instrumental_gain` will adjust the volume of the instrumental track in dB, i.e. `--instrumental_gain -6` will lower the instrumental volume by 6dB.


