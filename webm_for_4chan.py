# 4chan webm converter
# Copyright and related rights waived via CC0
# Requirements: Python 3.10+, ffmpeg, ffprobe
# Developed on Linux, your mileage may vary on Windows
# Make sure you specify timestamps as H:M:S.ms
# Specifying seconds larger than 59 causes a cryptic parse error

import argparse
import datetime
from enum import Enum
import json
import math
import mimetypes
import os
import platform
import signal
import subprocess
import sys
import time
import traceback

max_bitrate = 2800 # (kbps) Cap bitrate in case the clip is really short. This is already an absurdly high rate.
max_size = [6144 * 1024, 4096 * 1024] # 4chan size limits, in bytes [wsg, all other boards]
max_duration = [400, 300, 120] # Maximum clip durations, in seconds [wsg, gif, all other boards]
resolution_table = [480, 576, 640, 736, 854, 960, 1024, 1280, 1440, 1600, 1920, 2048] # Table of discrete resolutions
audio_bitrate_table = [12, 24, 32, 40 , 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512] # Table of discrete audio bit-rates
resolution_fallback_map = { # This time-based lookup is used if smart resolution calculation fails for some reason
    15.0: 1920,
    30.0: 1600,
    45.0: 1440,
    75.0: 1280,
    115.0: 1024,
    145.0: 960,
    185.0: 854,
    245.0: 736,
    285.0: 640,
    330.0: 576,
    400.0: 480
}
fps_map = { # Map of clip duration to fps. Clip must be below the duration to fit into the fps cap
    150.0: 60.0,
    200.0: 30.0,
    400.0: 24.0
}
audio_map = { # Map of clip duration to audio bitrate. Very long clips benefit from audio bitrate reduction, but not ideal for music oriented webms. Use --music_mode to bypass.
    60.0: 96,
    120.0: 80,
    240.0: 64,
    300.0: 56,
    360.0: 48,
    400.0: 32
}
audio_map_gif = { # Separate audio lookup for gif mode (4MB w/ sound)
    10.0: 96,
    20.0: 64,
    40.0: 56,
    60.0: 48,
    120.0: 32
}
audio_map_music_mode = { # Use high bit rate in music mode. Trying to keep the max size under 5.5MB.
    285.0: 128,
    330.0: 112,
    400.0: 96
}
bitrate_compensation_map = { # Automatic bitrate compensation, in kbps. This value is subtracted to prevent file size overshoot, which tends to happen in longer files
    300.0: 0,
    360.0: 2,
    400.0: 4
}
mixdown_stereo_threshold = 96 # Automatically mixdown to stereo if audio bitrate <= this value
mixdown_mono_threshold = 56 # Automatically mixdown to mono if audio bitrate <= this value
null_output = 'NUL' if platform.system() == 'Windows' else '/dev/null' # For pass 1 and certain preprocessing steps, need to output to appropriate null depending on system

files_to_clean = [] # List of temp files to be cleaned up at the end

# Determine size limit in bytes
def get_size_limit(args):
    # Manual size override
    if args.size is not None:
        return args.size * 1024 * 1024
    else:
        return max_size[0] if str(args.board) == 'wsg' else max_size[1] # Look up the size cap depending on the board it's destined for

# Find a filename with a given extension
def get_temp_filename(extension : str):
    basename = 'temp'
    filename = '{}.{}'.format(basename,extension)
    x = 0
    while os.path.isfile(filename):
        x += 1
        filename = '{}.{}.{}'.format(basename,x,extension)
    return filename


# This is only called if you don't specify a duration or end time. Uses ffprobe to find out how long the input is.
def get_video_duration(input_filename, start_time : float):
    # https://superuser.com/questions/650291/how-to-get-video-duration-in-seconds
    result = subprocess.run(["ffprobe","-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_filename], stdout=subprocess.PIPE, text=True)
    duration_seconds = float(result.stdout)
    return datetime.timedelta(seconds=duration_seconds - start_time)

# Determines if the argument is a parsable timestamp
def is_timestamp(arg : str):
    if arg.isnumeric():
        return True
    elif ':' in arg: # Argument could have hh:mm:ss format
        for token in arg.split(':'):
            if not is_timestamp(token):
                return False
        return True
    else: # Attempt to parse the timestamp as a float
        try:
            float(arg)
            return True
        except ValueError:
            return False

# Determines if the argument is a segment (i.e. a collection of timestamps used by --cut or --concat)
def is_segment(arg : str):
    print('aa')
    for tok in arg.split(';'): # Segments can be split by semicolon
        if '-' not in tok:
            return False # Segments need to be separated by a dash
        for seg in tok.split('-'):
            if not is_timestamp(seg):
                return False
    return True # Only true if all arguments pass as individual timestamps

# Rudamentary timestamp parsing, the format is H:M:S.ms and hours/minutes/milliseconds can be omitted
def parsetime(ts_input):
    ts = ts_input.split('.')
    # Note: I didn't want to import a 3rd party library just to parse simple timestamps. The janky millisecond parsing is a result of this.
    ms = 0
    if len(ts) > 1:
        ms = int(ts[1])
        if len(ts[1]) == 1: # Single digit, that means its 100s of ms
            ms *= 100
        elif len(ts[1]) == 2: # 2 digits, that means its 10s of ms
            ms *= 10
    try:
        duration = time.strptime(ts[0], '%H:%M:%S')
        return datetime.timedelta(hours=duration.tm_hour, minutes=duration.tm_min, seconds=duration.tm_sec, milliseconds=ms)
    except ValueError:
        try:
            duration = time.strptime(ts[0], '%M:%S')
            return datetime.timedelta(minutes=duration.tm_min, seconds=duration.tm_sec, milliseconds=ms)
        except ValueError:
            duration = time.strptime(ts[0], '%S')
            return datetime.timedelta(seconds=duration.tm_sec, milliseconds=ms)

# Define the 3 different options for 4chan
class BoardMode(Enum):
    wsg = 'wsg'
    gif = 'gif'
    other = 'other'
    def __str__(self):
        return self.value

# Different resolution scaling options
class ResizeMode(Enum):
    cubic = 'cubic'
    logarithmic = 'logarithmic'
    table = 'table'
    def __str__(self):
        return self.value

# Different silence trim options
class SilenceTrimMode(Enum):
    start = 'start'
    end = 'end'
    start_and_end = 'start_and_end'
    all = 'all'
    def __str__(self):
        return self.value

# Different sound mixdown options
class MixdownMode(Enum):
    auto = 'auto'
    stereo = 'stereo'
    mono = 'mono'
    same_as_source = 'same_as_source'
    def __str__(self):
        return self.value

# Perform duration check to make sure it still fits on the board
def duration_check(duration : datetime.timedelta, board : BoardMode, no_duration_check : bool):
    if not no_duration_check:
        duration_sec = duration.total_seconds()
        duration_limit = max_duration[0] # wsg
        if board == BoardMode.gif:
            duration_limit = max_duration[1] # gif
        elif board == BoardMode.other: # all other boards
            duration_limit = max_duration[2]
        if duration_sec > duration_limit:
            raise ValueError("Error: Specified duration {} seconds exceeds maximum {} seconds".format(duration_sec, duration_limit))

# Scales resolution sources to 1080p to match the calibrated resolution curve
def scale_to_1080(width, height):
    min_dimension = min(width, height)
    scale_factor = 1080 / min_dimension
    return [width * scale_factor, height * scale_factor]

# Libx264 apparently needs the vertical and horizontal resolution to be an even number. This adjusts the resolution to the nearest even number.
def scale_to_even(original_width, original_height, scaled_width, scaled_height):
    # No need to adjust if it's already an even number
    if scaled_height % 2 == 0:
        return int(max(scaled_width, scaled_height))
    # Search for new height
    height = int(round(scaled_height))
    width = int(round(scaled_width))
    next_height = height # A continually decreasing counter
    # Brute force and find a resolution that is even horizontally and veritcally
    while (next_height > 0) and ((height % 2 != 0) or (width % 2 != 0)):
        next_height -= 1 # Reduce height by one until we find the nearest resolution that satisfies the requirement
        scale = next_height / original_height
        # Apparently ffmpeg rounds instead of truncates, so we can't rely on the integer floor
        width = int(round(original_width * scale))
        height = int(round(original_height * scale))
        #print("{}x{}".format(width, height))
    return int(max(height, width))

# Use the lookup table to find the highest resolution under the pre-defined durations in the table
def calculate_target_resolution(duration, input_filename, target_bitrate, resizing_mode : ResizeMode, bypass_resolution_table : bool):
    if str(resizing_mode) != 'table':
        try:
            # ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 input.mp4
            result = subprocess.run(["ffprobe","-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", input_filename], stdout=subprocess.PIPE, text=True)
            if result.returncode == 0:
                # Grab the largest dimension of the video's resolution
                raw_width, raw_height = [int(x) for x in result.stdout.strip().split(',')]
                raw_max_dimension = max(raw_width, raw_height)
                # The curve was tuned at 1080p. 4k sources cause an over-estimation, so we have to scale large sources down to 1080 for size calculation purposes
                width, height = scale_to_1080(raw_width, raw_height)
                total_pixels = width * height
                calculated_resolution = 0
                scale_factor = 1.0
                # Calculate resolution
                if str(resizing_mode) == 'logarithmic':
                    x = target_bitrate * total_pixels # Factor in the total resolution of the image and the bit rate
                    # Calculate the ideal resolution using logarithmic curve: y = a * ln(x/b)
                    # Parameters calculated with the help of https://curve.fit, values based on the fallback map
                    a = 2.311e-01
                    b = 3.547e+01
                    scale_factor = a * math.log(target_bitrate/b)
                elif str(resizing_mode) == 'cubic':
                    a = 1.318e-10
                    b = -6.532e-07
                    c = 1.110e-03
                    d = 1.977e-01
                    x = target_bitrate
                    # Standard cubic equation: y = ax^3 + bx^2 + cx + d
                    # Note that a similar curve fit from above was used, but this follows a cubic curve which has steeper rolloff at the beginning
                    scale_factor = a * math.pow(x,3) + b * pow(x,2) + c * x + d
                scaled_pixels = total_pixels * scale_factor
                scaled_height = scaled_pixels / width
                scaled_width = scaled_pixels / height
                calculated_resolution = max(scaled_height, scaled_width)
                # Either use raw calculated resolution or nearest standard resolution 
                if bypass_resolution_table: # Skip resolution table lookup and go to the nearest pixel
                    res = int(min(2048, calculated_resolution))
                    if raw_max_dimension <= res:
                        return None
                    return res
                    #print('{}'.format(calculated_resolution))
                nearest_resolution = resolution_table[0]
                for res in resolution_table:
                    if calculated_resolution >= res:
                        nearest_resolution = res
                    else:
                        break
                if raw_max_dimension <= nearest_resolution: # No need to resize if the resolution we calculated is bigger than the native res
                    return None # Return None to signal that the video should not be resized
                final_scale = nearest_resolution / max(height, width)
                final_horizontal_resolution = width * final_scale
                final_vertical_resolution = height * final_scale
                adjusted_resolution = scale_to_even(raw_width, raw_height, final_horizontal_resolution, final_vertical_resolution)
                return adjusted_resolution
            else:
                print(result.stdout)
                print('ffprobe returned error code {}'.format(result.returncode))
        except Exception as e:
            print(e) 
        print('Error getting input resolution. Falling back to time-based table.')   
    calculated_res = 1920
    for key in sorted(resolution_fallback_map):
        if duration.total_seconds() <= key:
            calculated_res = resolution_fallback_map[key]
            break
    return calculated_res

# Same idea as the resolution lookup table but for fps. Also takes into account the source fps.
def calculate_target_fps(input_filename, duration):
    frame_rate = 60
    # Get frame rate limit according to the map
    for key in sorted(fps_map):
        if duration.total_seconds() <= key:
            frame_rate = fps_map[key]
            break
    # Get input frame rate
    try:
        result = subprocess.run(["ffprobe","-v", "error", "-select_streams", "v", "-of", "default=noprint_wrappers=1:nokey=1", "-show_entries", "stream=r_frame_rate", input_filename], stdout=subprocess.PIPE, text=True)
        if result.returncode != 0:
            print(result.stdout)
            print('ffprobe returned error code {}'.format(result.returncode))
            print('Error getting input fps. Using no input fps assumptions.')
            return frame_rate
        # Outputs the frame rate as a precise fraction. Have to convert to decimal.
        source_fps_fractional = result.stdout.split('/')
        source_fps = round(float(source_fps_fractional[0]) / float(source_fps_fractional[1]), 2)
        # If source frame rate is already fine, return None to signal no fps filter necessary
        if source_fps <= frame_rate:
            return None
    except Exception as e:
        print(e)
        print('Error reading source fps. Falling back to time-based table.')
    return frame_rate # Return calculated fps otherwise

# Use audio lookup table
def calculate_target_audio_rate(duration, music_mode, mode : BoardMode):
    audiomap = None
    if music_mode:
        audiomap = audio_map_music_mode
    else:
        audiomap = audio_map_gif if str(mode) == 'gif' else audio_map
    for key in sorted(audiomap):
        if duration.total_seconds() <= key:
            return audiomap[key]
    return 96 # Unreachable code as long as the maps are set up correctly

def find_json(output):
    # Admittedly this is a fragile way of finding the json output.
    # It is mixed up with the regular output of ffmpeg so the json string must be isolated.
    start_index = output.find('{')
    end_index = output.find('}')
    if start_index > -1 and end_index > -1:
        params_str = output[start_index:end_index+1]
        try:
            params = json.loads(params_str)
            if params['input_i'] is not None:
                return params
            else:
                print('Warning: Could not find audio normalization parameters.')
                print('params: {}'.format(params))
                return None
        except Exception as e:
            print('Error processing audio normalization parameters: {}'.format(e))
    return None

# Return a tuple containing the stream layout and a flag that is True of no audio stream was detected
def get_audio_layout(input_filename : str, track : int):
    ffprobe_cmd = ['ffprobe', '-v', 'error', '-hide_banner', '-of', 'default=noprint_wrappers=1:nokey=1', '-show_streams', '-select_streams', 'a:{}'.format(track), '-print_format', 'json', input_filename]
    result = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(result.stdout)
        raise RuntimeError('ffprobe returned error code {}'.format(result.returncode))
    stream_info = json.loads(result.stdout)
    if stream_info['streams'] is not None:
        if len(stream_info['streams']) > 0:
            layout = stream_info['streams'][0]['channel_layout']
            return layout, False
        else:
            return None, True
    print(result.stdout)
    return None, False

# Simply renders the audio to file and gets its size.
# This is the most precise way of knowing the final audio size and rendering this takes a fraction of the time it takes to render the video.
# Returns a tuple containing the audio bit rate, normalization parameters if applicable, the surround workaround filter if applicable, and a special flag if no audio streams were found
def calculate_audio_size(input_filename, start, duration, audio_bitrate, track, mode : BoardMode, acodec : str, mixdown : MixdownMode, normalize : bool):
    if str(mode) == 'wsg' or str(mode) == 'gif':
        surround_workaround = False # For working around a known bug in libopus: https://trac.ffmpeg.org/ticket/5718
        surround_workaround_args = None
        output_ext = 'opus' if acodec == 'libopus' else 'aac'
        output = get_temp_filename(output_ext)
        files_to_clean.append(output)
        if os.path.isfile(output):
            os.remove(output)
        ffmpeg_cmd = ['ffmpeg', '-ss', str(start), '-t', str(duration), '-i', input_filename, '-vn', '-acodec', acodec, '-b:a', audio_bitrate]
        if track is not None: # Optional audio track selection
            ffmpeg_cmd.extend(['-map', '0:a:{}'.format(track)])
        # https://superuser.com/questions/852400/properly-downmix-5-1-to-stereo-using-ffmpeg
        if mixdown == MixdownMode.stereo:
            ffmpeg_cmd.extend(['-ac', '2'])
        elif mixdown == MixdownMode.mono:
            ffmpeg_cmd.extend(['-ac', '1'])
        ffmpeg_cmd1 = ffmpeg_cmd.copy()
        ffmpeg_cmd1.append(output)
        result = subprocess.run(ffmpeg_cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 or not os.path.isfile(output):
            for line in result.stderr.splitlines():
                # Try to rerun with surround sound workaround
                if 'libopus' in line:
                    ffmpeg_cmd2 = ffmpeg_cmd.copy()
                    layout, no_audio = get_audio_layout(input_filename, track if track is not None else 0)
                    if no_audio:
                        print('ffprobe did not detect any audio streams.')
                        return [0, None, False, True]
                    else:
                        print('Warning: ffmpeg returned status code {}. Trying surround workaround.'.format(result.returncode))
                    if layout is None:
                        raise RuntimeError('Could not determine channel layout.')
                    print('Detected channel layout: {}'.format(layout))
                    # The key is the channel layout as reported by ffprobe and the value is the appropriate layout to use in the audio filter.
                    # I think the bug only exists for 5.1(side) and that's the only case I've seen it, but other cases are added just to be safe.
                    # More on possible layouts: https://ffmpeg.org/ffmpeg-utils.html#channel-layout-syntax
                    layout_map = {
                        '5.0(side)' : '5.0',
                        '5.1(side)' : '5.1',
                        '6.0(front)' : '6.0',
                        '6.1(front)' : '6.1',
                        '7.0(front)' : '7.0',
                        '7.1(wide)' : '7.1',
                        '7.1(wide-side)' : '7.1'
                    }
                    if layout not in layout_map.keys():
                        raise RuntimeError("Could not find a workaround for channel layout '{}'".format(layout))
                    # Using a slightly modified form of the recommended solution from https://trac.ffmpeg.org/ticket/5718#comment:11
                    # as well as the technique to identify and substitute appropriate tracks from https://trac.ffmpeg.org/ticket/5718#comment:21
                    surround_workaround_args = 'aformat=channel_layouts={}'.format(layout_map[layout])
                    surround_workaround = True
                    ffmpeg_cmd2.append('-af')
                    ffmpeg_cmd2.append(surround_workaround_args)
                    ffmpeg_cmd2.append(output)
                    if os.path.isfile(output):
                        os.remove(output)
                    result = subprocess.run(ffmpeg_cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if result.returncode != 0 or not os.path.isfile(output):
                        print(' '.join(ffmpeg_cmd2))
                        print(result.stderr)
                        raise RuntimeError('Error rendering audio. ffmpeg return code: {}'.format(result.returncode))
                    else:
                        break
            if not surround_workaround:
                print(' '.join(ffmpeg_cmd1))
                print(result.stderr)
                raise RuntimeError('Error rendering audio. ffmpeg return code: {}'.format(result.returncode))
        if normalize:
            print('Normalizing audio (1st pass)')
            null_output = 'NUL' if platform.system() == 'Windows' else '/dev/null' # For pass 1, need to output to appropriate null depending on system
            result = subprocess.run(['ffmpeg', '-i', output, '-filter:a', 'loudnorm=print_format=json', "-f", "null", null_output], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # Search stdout and stderr for the loudnorm params
            params = None
            if result.returncode == 0:
                params = find_json(result.stdout)
            if result.returncode == 0 and params is None:
                params = find_json(result.stderr)
            if params is not None:
                print('Normalizing audio (2nd pass)')
                output2_ext = 'normalized.opus' if acodec == 'libopus' else 'normalized.aac'
                output2 = get_temp_filename(output2_ext)
                files_to_clean.append(output2)
                if os.path.isfile(output2):
                    os.remove(output2)
                # The size of the normalized audio is different from the initial one, so render to get the exact size
                ffmpeg_cmd = ['ffmpeg', '-ss', str(start), '-t', str(duration), '-i', input_filename, '-vn', '-acodec', acodec, '-filter:a', 'loudnorm=linear=true:measured_I={}:measured_LRA={}:measured_tp={}:measured_thresh={}'.format(params['input_i'], params['input_lra'], params['input_tp'], params['input_thresh']), '-b:a', audio_bitrate]
                if track is not None: # Optional audio track selection
                    ffmpeg_cmd.extend(['-map', '0:a:{}'.format(track)])
                if mixdown == MixdownMode.stereo:
                    ffmpeg_cmd.extend(['-ac', '2'])
                elif mixdown == MixdownMode.mono:
                    ffmpeg_cmd.extend(['-ac', '1'])
                elif surround_workaround:
                    ffmpeg_cmd.append('-af')
                    ffmpeg_cmd.append(surround_workaround_args)
                ffmpeg_cmd.append(output2)
                result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if result.returncode == 0 and os.path.isfile(output2):
                    return [os.path.getsize(output2), params, surround_workaround_args, False]
                else:
                    print('Warning: Could not render normalized audio. Skipping normalization.')
                    print('Debug info:')
                    print('ffmpeg return code: {}'.format(result.returncode))
                    print('stdout: {}'.format(result.stdout))
                    print('stderr: {}'.format(result.stderr))
                    return [os.path.getsize(output), None, surround_workaround_args, False]
            else:
                print('Warning: Could not process normalized audio. Skipping normalization.')
                print('Debug info:')
                print('ffmpeg return code: {}'.format(result.returncode))
                print('stdout: {}'.format(result.stdout))
                print('stderr: {}'.format(result.stderr))
                return [os.path.getsize(output), None, surround_workaround_args, False]
        return [os.path.getsize(output), None, surround_workaround_args, False]
    else: # No audio
        return [0, None, None, True]

# Attempt to compensate for calculated bitrate to prevent file size overshoot
# User can also manually specify additional compensation through the -b argument
def calculate_bitrate_compensation(duration, manual_compensation):
    for key in sorted(bitrate_compensation_map):
        if duration.total_seconds() <= key:
            return bitrate_compensation_map[key] + manual_compensation
    return 0 + manual_compensation

# Return a dictionary of the available subtitles, with index as the key and language as the value
def list_subtitles(input_filename):
    # ffprobe -loglevel error -select_streams s -show_entries stream=index:stream_tags=language -of csv=p=0
    result = subprocess.run(["ffprobe","-v", "error", "-select_streams", "s", "-of", "csv=p=0", "-show_entries", "stream=index:stream_tags=language", input_filename], stdout=subprocess.PIPE, text=True)
    if result.returncode == 0:
        lines = result.stdout.splitlines()
        subs = dict()
        # Note that ffprobe returns sub tracks in the form "4,eng", but the first index is the index of all streams, not just subtitles.
        # ffmpeg's "si" argument wants an index (starting from 0) of just the subtitles.
        for idx,line in enumerate(lines):
            lang = line.split(',')[-1]
            subs[idx] = lang
        return subs
    else:
        print(result.stdout)
        raise RuntimeError('ffprobe returned code {}'.format(result.returncode))

# Return a dictionary of the available audio tracks, with index as the key and language as the value
def list_audio(input_filename):
    # ffprobe -show_entries stream=index:stream_tags=language -select_streams a -of compact=p=0:nk=1
    result = subprocess.run(["ffprobe","-v", "error", "-show_entries", "stream=index:stream_tags=language", "-select_streams", "a", "-of", "csv=p=0", input_filename], stdout=subprocess.PIPE, text=True)
    if result.returncode == 0:
        lines = result.stdout.splitlines()
        tracks = dict()
        # Note that ffprobe returns track numbers that don't correspond with the track index used by ffmpeg's map command.
        # ffmpeg wants an index (starting from 0) of the audio tracks presented in this order.
        for idx,line in enumerate(lines):
            lang = line.split(',')[-1]
            tracks[idx] = lang
        return tracks
    else:
        print(result.stdout)
        raise RuntimeError('ffprobe returned code {}'.format(result.returncode))

# Parse cut/concat segment timestamps
def parse_segments(start, segments : str, do_print = True):
    parsed_segments = []
    for segment in segments.split(';'):
        segment_start, segment_end = segment.split('-')
        absolute_start = parsetime(segment_start)
        absolute_end = parsetime(segment_end)
        if absolute_start < start or absolute_end < start:
            raise RuntimeError('Segment {}-{} starts before the start time of {}'.format(absolute_start, absolute_end, start))
        relative_start = absolute_start - start
        relative_end = absolute_end - start
        if do_print:
            print('Identified segment: {}-{}'.format(absolute_start, absolute_end))
        parsed_segments.append((relative_start, relative_end))
    return parsed_segments

def build_filter_graph(segments_to_keep):
    video_filter_graph = ''
    audio_filter_graph = ''
    # Build a filter graph on the kept segments
    # Nice reference for how to build a filter graph: https://github.com/sriramcu/ffmpeg_video_editing
    for index, segment in enumerate(segments_to_keep, start=1):
        segment_start, segment_end = segment
        # [0]trim=start=34.5:end=55.1,setpts=PTS-STARTPTS[v1];
        video_filter_graph += '[0]trim=start={}:end={},setpts=PTS-STARTPTS[v{}];'.format(segment_start.total_seconds(), segment_end.total_seconds(), index)    
        audio_filter_graph += '[0]atrim=start={}:end={},asetpts=PTS-STARTPTS[a{}];'.format(segment_start.total_seconds(), segment_end.total_seconds(), index)  
        #print('{} {}-{}'.format(index, segment_start, segment_end))
    for index, segment in enumerate(segments_to_keep, start=1):
        # [v1][v2][v3]concat=n=3:v=1:a=0[outv]
        video_filter_graph += '[v{}]'.format(index)
    video_filter_graph += 'concat=n={}:v=1:a=0[outv]'.format(len(segments_to_keep))
    for index, segment in enumerate(segments_to_keep, start=1):
        audio_filter_graph += '[a{}]'.format(index)
    audio_filter_graph += 'concat=n={}:v=0:a=1[outa]'.format(len(segments_to_keep))
    return video_filter_graph, audio_filter_graph

def build_concat_segments(start, args):
    try:
        segments_to_keep = parse_segments(start, args.concat)
        segments_duration = datetime.timedelta(seconds=0.0)
        for segment_start, segment_end in segments_to_keep:
            segment_duration = segment_end - segment_start
            segments_duration += segment_duration
        
        # Make sure final duration time fits
        duration_check(segments_duration, args.board, args.no_duration_check)
        print('Total concatenated segment time: {}'.format(segments_duration))
        
        # Segments are ready to be built
        return build_filter_graph(segments_to_keep)
    except Exception as e:
        raise RuntimeError('Error parsing concatenated segments: {}'.format(e))

def build_cut_segments(start, duration, args):
    try:
        # Parse all segments
        segments_to_cut = parse_segments(start, args.cut)
        
        # Invert the cut segments into the segments to keep
        segments_duration = datetime.timedelta(seconds=0.0)
        segments_to_keep = []
        temp_start_time = datetime.timedelta(seconds=0.0)
        for segment_start, segment_end in segments_to_cut:
            segment_duration = segment_end - segment_start
            segments_duration += segment_duration
            start_time = temp_start_time
            end_time = segment_start
            segments_to_keep.append((start_time, end_time))
            temp_start_time = end_time + segment_duration
        # Final segment
        segments_to_keep.append((temp_start_time, duration))

        print('Total cut segment time: {}'.format(segments_duration))
        
        # Make sure final duration time fits
        adjusted_duration = duration - segments_duration
        duration_check(adjusted_duration, args.board, args.no_duration_check)

        # Segments are ready to be built
        return build_filter_graph(segments_to_keep)
    except Exception as e:
        raise RuntimeError('Error parsing cut segments: {}'.format(e))

# Concatenate or cut segments from the video and render to a temporary file. On success, the name of the temp file is returned.
def segment_video(input_filename : str, start, duration, full_video : bool, args):

    # Make sure no audio tracks beside the default are specified
    audio_tracks = list_audio(input_filename)
    if len(audio_tracks) > 1:
        if args.audio_index is not None and args.audio_index > 0:
            raise RuntimeError("--cut does not support cutting from multi-audio streams except for default stream 0")
        if args.audio_lang is not None:
            for key, value in audio_tracks.items():
                if args.audio_lang == value and key > 0:
                    raise RuntimeError("--cut/--concat does not support cutting from multi-audio streams except for default stream 0")
    # Also make sure no subtitle burn-in is enabled
    if args.auto_subs is True or args.sub_index is not None or args.sub_lang is not None or args.sub_file is not None:
        raise RuntimeError("--cut/--concat is not compatible with subtitle burn-in.")
    # Can't use 5.1 side surround sound when making a cut due to the libopus bug
    layout, no_audio = get_audio_layout(input_filename, 0)
    if '5.1(side)' in layout:
        raise RuntimeError("5.1(side) surround sound detected. --cut is not compatible with this audio track.")

    video_filter_graph, audio_filter_graph = build_cut_segments(start, duration, args) if args.cut is not None else build_concat_segments(start, args)
    ffmpeg_args = ['ffmpeg', '-hide_banner', '-y']
    if not full_video:
        ffmpeg_args.extend(['-ss', str(start), '-t', str(duration)])
    # Input file to process
    ffmpeg_args.extend(['-i', input_filename])
    # Build the filter arguments
    ffmpeg_args.extend(['-filter_complex', video_filter_graph + ';' + audio_filter_graph, '-map', '[outv]', '-map', '[outa]'])

    # Encoder. This is used to generate a temporary file.
    ffmpeg_args.extend(["-c:v", "libx265", "-x265-params", "lossless=1"])
    ffmpeg_args.extend(["-c:a", "libopus", "-b:a", "512k"])
    
    # Output file
    output_filename = get_temp_filename('mkv')
    files_to_clean.append(output_filename)
    ffmpeg_args.append(output_filename)
    print('Rendering cut video...')
    print(' '.join(ffmpeg_args))
    result = subprocess.run(ffmpeg_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError('ffmpeg returned code {}'.format(result.returncode))
    if os.path.isfile(output_filename):
        return output_filename
    else:
        raise RuntimeError("File '{}' not found".format(output_filename))

def blackframe(input_filename, start, duration):
    print('Running blackframe detection')
    try:
        result = subprocess.run(['ffmpeg', '-ss', str(start), '-t', str(duration), '-i', input_filename, '-vf', 'blackframe=threshold=96:amount=92', '-f', 'null', null_output, '-v', 'info'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print(result.stderr.decode())
            raise RuntimeError('ffmpeg returned code {}'.format(result.returncode))
        output = result.stderr.decode().splitlines()
        detected_frames = dict()
        for line in output:
            # Attempt to identify output that has blackframe parameters
            if 'blackframe' in line and 'frame:' in line and 't:' in line:
                frame = None
                t = None
                for param in line.split():
                    if ':' in param:
                        key, value = param.split(':')
                        if key == 'frame':
                            frame = int(value)
                        if key == 't':
                            t = value
                if frame is not None and t is not None:
                    detected_frames[frame] = t
        if len(detected_frames) > 0:
            # Detect contiguous frames, assuming the first frame is 1
            last_frame = 0
            last_ts = None
            frame_dt = 0 # use dt between frames to advance one frame past the last detected black frame
            for key, value in detected_frames.items():
                if last_frame + 1 == key:
                    last_frame = key
                    ts = float(value)
                    frame_dt = ts - last_ts if last_ts is not None else 0
                    last_ts = ts
            #print('dt {}'.format(frame_dt))
            if last_ts is not None:
                return datetime.timedelta(seconds=last_ts+frame_dt)
    except Exception as e:
        print(e)
        print('Error detecting blackframes. Skipping step.')
    return datetime.timedelta(seconds=0)

def cropdetect(input_filename, start, duration):
    print('Running cropdetect')
    result = subprocess.run(['ffmpeg', '-ss', str(start), '-t', str(duration), '-i', input_filename, '-vf', 'cropdetect', '-f', 'null', null_output, '-v', 'info'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stderr.decode())
        raise RuntimeError('ffmpeg returned code {}'.format(result.returncode))
    output = result.stderr.decode().splitlines()
    crop_output = []
    for line in output:
        # Attempt to identify output that has cropdetect parameters
        if 'cropdetect' in line and 'crop=' in line:
            crop_params = line.split()[-1]
            crop_output.append(crop_params)
    if len(crop_output) > 0:
        return crop_output[-1]
    else:
        return None

def silencedetect(input_filename, start, duration):
    print('Running silencedetect')
    result = subprocess.run(['ffmpeg', '-ss', str(start), '-t', str(duration), '-i', input_filename, '-af', 'silencedetect=n=-50dB:d=1.4', '-f', 'null', null_output, '-v', 'info'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stderr.decode())
        raise RuntimeError('ffmpeg returned code {}'.format(result.returncode))
    output = result.stderr.decode().splitlines()
    silence_start = None
    silence_end = None
    silence_segments = []
    for line in output:
        if 'silencedetect' in line:
            toks = line.split()
            if 'silence_start:' in toks:
                silence_start = datetime.timedelta(seconds=float(toks[toks.index('silence_start:') + 1]))
            if 'silence_end:' in toks:
                silence_end = datetime.timedelta(seconds=float(toks[toks.index('silence_end:') + 1]))
                silence_segments.append((silence_start if silence_start is not None else start, silence_end))
                silence_start = None
                silence_end = None
    # I don't think this is a real scenario but I'm covering my bases.
    # This is just for the case where silencedetect prints a silence_start but not a silence_end.
    if silence_start is not None and silence_end is None:
        silence_segments.append(silence_start,start+duration)
    return silence_segments
    
# Convoluted method of determining the output file name. Avoid overwriting existing files, etc.
def get_output_filename(input_filename, args):
    # Use manually specified output
    suffix = '.webm' if args.codec == 'libvpx-vp9' else '.mp4'
    if args.output is not None:
        output = args.output
        # Force webm suffix
        if os.path.splitext(output)[-1] != suffix:
            output += suffix
        if os.path.isfile(output):
            confirmation = ''
            while not (confirmation.lower() == 'y' or confirmation.lower() == 'n'):
                confirmation = input("File '{}' already exists, overwrite? Y/N ".format(output))
            if confirmation.lower() == 'y':
                os.remove(output) # Remove existing file
            else:
                print('Halting.')
                exit(0)
        return output
    # Automatically determine file name based on input file
    else:
        output = os.path.splitext(input_filename)[0]
        filename_count = 1
        while True:
            # Rename the output by prepending '_1_' to the start of the file name.
            # The dirname shenanigans are an attempt to differentiate a file in a subdirectory vs a filename unqualified in the current directory.
            final_output = os.path.dirname(output) + (os.path.sep if os.path.dirname(output) != "" else "") + '_{}_'.format(filename_count) + os.path.basename(output) + suffix
            if os.path.isfile(final_output):
                filename_count += 1 # Try to deconflict the file name by finding a different file name
            else:
                return final_output

# The part where the webm is encoded
def encode_video(input, output, start, duration, video_codec : list, video_filters : list, audio_codec : list, audio_filters : list, subtitles, track, full_video : bool, no_audio : bool, mixdown : MixdownMode, mode : BoardMode, dry_run : bool):
    ffmpeg_args = ["ffmpeg", '-hide_banner']
    slice_args = ['-ss', str(start), "-t", str(duration)] # The arguments needed for slicing a clip
    vf_args = '' # The video filter arguments
    for filter in video_filters:
        if vf_args != '':
            vf_args += ',' # Tack on to other args if string isn't empty
        vf_args += filter
    if subtitles != '':
        # The order of arguments apparently matters when it comes to the subtitles, with -i needing to come first if there are subs
        print("Subtitle burn-in enabled.")
        if vf_args != '':
            vf_args += ',' # Tack on to other args if string isn't empty
        vf_args += "subtitles={}".format(subtitles)
        ffmpeg_args.extend(['-i', input])
        if not full_video:
            ffmpeg_args.extend(slice_args)
    else:
        # Experimentally, it is faster to run -i after -ss and -t if there are no subtitles.
        # Is it placebo? I don't know, but I do know that -i needs to be first for subs to work,
        # and this is the order I used before adding the subtitle feature.
        if not full_video:
            ffmpeg_args.extend(slice_args)
        ffmpeg_args.extend(['-i', input])

    if vf_args != '': # Add video filter if there are any arguments
        ffmpeg_args.extend(["-vf", vf_args])
    ffmpeg_args.extend(video_codec)

    # The constructed ffmpeg commands
    pass1 = ffmpeg_args
    pass2 = ffmpeg_args.copy() # Must make deep copy, or else arguments get jumbled
    pass1.extend(["-pass", "1"])
    pass2.extend(["-pass", "2"])
    pass1.extend(["-an", "-f", "null", null_output]) # Pass 1 doesn't output to file

    # Audio options. wsg/gif allow audio, else omit audio
    if (str(mode) == 'wsg' or str(mode) == 'gif') and not no_audio:
        if track is not None: # Optional track selection
            pass2.extend(['-map', '0:v:0', '-map', '0:a:{}'.format(track)])
        else: # When testing multi-track videos, leaving this argument out causes buggy time codes when clipping for some unknown reason
            #print('Inspecting audio tracks')
            audio_tracks = list_audio(input)
            if len(audio_tracks.items()) > 1:
                print('Multiple audio tracks detected, selecting track 0.')
                pass2.extend(['-map', '0:v:0', '-map', '0:a:0'])
        if mixdown == MixdownMode.stereo:
            pass2.extend(['-ac', '2'])
        elif mixdown == MixdownMode.mono:
            pass2.extend(['-ac', '1'])
        af_args = ''
        for filter in audio_filters: # Apply miscellaneous filters
            if af_args != '':
                af_args += ',' # Tack on to other args if string isn't empty
            af_args += filter
        if af_args != '':
            pass2.append('-af')
            pass2.append(af_args)
        pass2.extend(audio_codec)
    else:
        pass2.extend(["-an"]) # No audio
    pass2.append(output)

    # Pass 1
    print('Encoding video (1st pass)')
    print(' '.join(pass1))
    if not dry_run:
        result = subprocess.run(pass1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print(result.stderr.decode())
            raise RuntimeError('ffmpeg returned code {}'.format(result.returncode))

    # Pass 2 (this takes a long time)
    print('Encoding video (2nd pass)')
    print(' '.join(pass2))
    if not dry_run:
        # Use popen so we can pend on completion
        pope = subprocess.Popen(pass2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        for line in iter(pope.stderr.readline, ""):
            if 'frame=' in line:
                print('\r' + line.strip(), end='')
            else:
                print(line, end='')
        pope.stderr.close()
        pope.wait()
        if pope.returncode != 0:
            raise RuntimeError('ffmpeg returned code {}'.format(pope.returncode))

# Take the first second from every minute within the specified start and duration.
# Inspired by the youtube channel @FirstSecondEveryMinute
def first_second_every_minute(start : datetime.timedelta, duration : datetime.timedelta):
    segments = []
    current_time = start
    while current_time.total_seconds() <= (duration.total_seconds() - 1):
        segments.append('{}-{}'.format(current_time, current_time + datetime.timedelta(seconds=1)))
        current_time += datetime.timedelta(minutes=1)
    return ';'.join(segments)

# Format a timedelta into hh:mm:ss.ms
def format_timedelta(ts : datetime.timedelta):
    hours, rm_hr = divmod(ts.total_seconds(), 3600)
    mins, rm_min = divmod(rm_hr, 60)
    sec, rm_sec = divmod(rm_min, 1)
    ts_str = '{:02}:{:02}:{:02}.{:03}'.format(int(hours), int(mins), int(sec), int(rm_sec*1000))
    return ts_str

def get_mixdown_mode(audio_kbps, audio_track, mixdown : MixdownMode):
    if mixdown == MixdownMode.auto:
        if audio_kbps <= mixdown_mono_threshold:
            mixdown = MixdownMode.mono
            try:
                layout, no_audio = get_audio_layout(input_filename, audio_track if audio_track is not None else 0)
                if not no_audio and layout == 'mono': # Use "same as source" mode if appropriate
                    mixdown = MixdownMode.same_as_source
            except:
                pass # Skip this if there's an error. There's no harm in forcing stereo mixdown.
        elif audio_kbps <= mixdown_stereo_threshold:
            mixdown = MixdownMode.stereo
            try:
                layout, no_audio = get_audio_layout(input_filename, audio_track if audio_track is not None else 0)
                if not no_audio and (layout == 'stereo' or layout == 'mono'):  # Use "same as source" mode if appropriate
                    mixdown = MixdownMode.same_as_source
            except:
                pass # Skip this if there's an error. There's no harm in forcing stereo mixdown.
        else:
            mixdown = MixdownMode.same_as_source
    if mixdown is MixdownMode.same_as_source:
        print('Audio mixdown: same as source') # print without underscores for readability
    else:
        print('Audio mixdown: {}'.format(mixdown))
    return mixdown

def process_video(input_filename, start, duration, args, full_video):
    output = get_output_filename(input_filename, args)

    if args.trim_silence is not None:
        silence_segments = silencedetect(input_filename, start, duration)
        if len(silence_segments) == 0:
                print('No silence detected')
        else:
            # Simple start and end silence trimming involves moving the start and duration
            trimmed_start = False
            original_start = start
            original_duration = duration
            if args.trim_silence != SilenceTrimMode.end: # Trim start
                silence_start, silence_end = silence_segments[0]
                silence_gap = silence_start - start
                # Allow for silence start to be slightly off from true start
                if silence_gap.total_seconds() < 0.1:
                    silence_duration = silence_end - silence_start
                    start += silence_duration
                    duration -= silence_duration
                    trimmed_start = True
                    print("Adjusted start time by {}".format(silence_duration))
            trimmed_end = False
            if args.trim_silence != SilenceTrimMode.start: # Trim end
                silence_start, silence_end = silence_segments[-1]
                silence_gap = (original_start + original_duration) - silence_end
                # Allow for silence start to be slightly off from true start
                if silence_gap.total_seconds() < 0.1:
                    silence_duration = silence_end - silence_start
                    duration -= silence_duration
                    trimmed_end = True
                    print("Adjusted end time by -{}".format(silence_duration))
            # Complex silence trimming involves hijacking the cut feature
            if args.trim_silence == SilenceTrimMode.all: # Trim in the middle
                segment_strings = []
                # Account for already cut segments from the begin and end
                if trimmed_start:
                    silence_segments = silence_segments[1:]
                if trimmed_end:
                    silence_segments = silence_segments[:-1]
                for start_segment, end_segment in silence_segments:
                    absolute_start = original_start + start_segment
                    absolute_end = original_start + end_segment
                    segment_strings.append(format_timedelta(absolute_start) + '-' + format_timedelta(absolute_end))
                segments = ';'.join(segment_strings)
                if args.cut is not None:
                    print("Warning: '--cut' cannot be used in conjunction with '--trim_silence all'. Arguments will be overridden.")
                args.cut = segments

    # Special case of only one concat segment (clip mode), which is logically equivalent to only adjusting start and duration
    if args.concat is not None:
        concat_segments = parse_segments(start, args.concat, do_print=False)
        if len(concat_segments) == 1:
            seg_start, seg_end = concat_segments[0]
            start += seg_start
            duration = seg_end - seg_start
            full_video = False # Since start and end have been adjusted, it's no longer eligible for full video mode
            print('clip start: {}'.format(start))
            print('clip duration: {}'.format(duration))
            args.concat = None  # Clear out the concat arg. There is nothing to concat with one segment.
    # Segment trimming feature involves rendering a temporary file with trimmed segments
    if args.cut is not None or args.concat is not None or args.first_second_every_minute:
        if args.first_second_every_minute:
            args.concat = first_second_every_minute(start, duration)
        if args.cut is not None and args.concat is not None:
            raise RuntimeError("Cannot use both --concat and --cut. Please use only one option.")
        new_filename = segment_video(input_filename, start, duration, full_video, args)
        # Reassign variables to use new temp file
        input_filename = new_filename
        start = datetime.timedelta(seconds=0.0)
        duration = get_video_duration(input_filename, start.total_seconds())
        print("Using cut file '{}', duration: {}".format(new_filename,duration))
        full_video = True
    
    # Duration check to make sure it will fit for the target board
    duration_check(duration, args.board, args.no_duration_check)

    if args.blackframe:
        frame_skip = blackframe(input_filename, start, duration)
        if frame_skip.total_seconds() > 0:
            start += frame_skip
            duration -= frame_skip
            full_video = False # If skipping frames, it can no longer be possible that full video is rendered
        print('Advancing start time by {}'.format(frame_skip))

    audio_track = None
    if args.audio_index is not None:
        track_list = list_audio(input_filename)
        if args.audio_index in track_list.keys():
            print('Selected audio track: {}'.format(args.audio_index))
            audio_track = args.audio_index
        else:
            print('Warning: Audio track {} not found, using default audio track.'.format(args.audio_index))
    elif args.audio_lang is not None:
        track_list = list_audio(input_filename)
        for key, value in track_list.items():
            if value == args.audio_lang:
                audio_track = key
                print('Selected audio track: {}'.format(key))
                break
        if audio_track is None:
            print('Warning: Audio language {} not found, using default audio track.'.format(args.audio_lang))

    # Calculate video bitrate by first calculating the audio size and subtracting it
    no_audio = False
    audio_size = 0
    surround_workaround = None
    np = None
    audio_bitrate = '96k'
    if args.no_audio or str(args.board) == 'other':
        no_audio = True
    else:
        print('Calculating audio bitrate: ', end='') # Do a lot of prints in case there is an error on one of the steps or it hangs
        audio_kbps = args.audio_rate if args.audio_rate is not None else calculate_target_audio_rate(duration, args.music_mode, args.board)
        audio_bitrate = '{}k'.format(audio_kbps)
        print(audio_bitrate)
        args.mixdown = get_mixdown_mode(audio_kbps, audio_track, args.mixdown) # Determine mixdown, if any
        print('Calculating audio size')
        # Calculate the audio file size and the volume normalization parameters if applicable. Always skip normalization in music mode.
        acodec = 'libopus' if args.codec == 'libvpx-vp9' else 'aac'
        audio_size, np, surround_workaround, no_audio = calculate_audio_size(input_filename, start, duration, audio_bitrate, audio_track, args.board, acodec, args.mixdown, args.normalize)
        print('Audio size: {}kB'.format(int(audio_size/1024)))
    size_limit = get_size_limit(args)
    adjusted_size_limit = size_limit - audio_size # File budget subtracting audio
    size_kb = adjusted_size_limit / 1024 * 8 # File budget in kilobits
    target_kbps = min((int)(size_kb / duration.total_seconds()), max_bitrate) # Bit rate in kilobits/sec, limit to max size so that small clips aren't unnecessarily large
    compensated_kbps = target_kbps - calculate_bitrate_compensation(duration, args.bitrate_compensation) # Subtract the compensation factor if specified
    video_bitrate = '{}k'.format(compensated_kbps)

    # Determine if we need to burn in subtitles
    subs = ''
    if args.sub_file is not None:
        if not os.path.exists(args.sub_file):
            print("Warning: Subtitle file '{}' not found, skipping subtitle burn-in.".format(args.sub_file))
        subs = "'{}'".format(args.sub_file)
    elif args.auto_subs or args.sub_index is not None or args.sub_lang is not None:
        sub_idx = None
        # Use the first sub index, if any exist
        if args.auto_subs:
            sub_list = list_subtitles(input_filename)
            for key in sub_list.keys():
                print('Auto sub: {},{}'.format(key,sub_list[key]))
                sub_idx = key
                break
            if sub_idx is None:
                print('Auto sub: No subtitles detected.')
        elif args.sub_index is not None:
            sub_list = list_subtitles(input_filename)
            if args.sub_index in sub_list.keys():
                sub_idx = args.sub_index
            else:
                print("Warning: Subtitle index {} not found, skipping subtitle burn-in. Use --list_subs for info on this file.".format(args.sub_index))
        elif args.sub_lang is not None:
            sub_list = list_subtitles(input_filename)
            for key, value in sub_list.items():
                if value == args.sub_lang:
                    sub_idx = key
            if sub_idx is None:
                print("Warning: Subtitle language '{}' not found, skipping subtitle burn-in. Use --list_subs for info on this file.".format(args.sub_lang))
        # Export embedded subs to a temporary file.
        # For some reason, using the subs embedded in the source file causes inconsistent results, but this approach seems to work reliably with clips.
        if sub_idx is not None:
            print("Exporting embedded subtitles to temp file")
            output_subs = get_temp_filename('ass')
            files_to_clean.append(output_subs)
            if os.path.exists(output_subs):
                os.remove(output_subs)
            result = subprocess.run(['ffmpeg', '-i', input_filename, '-map', '0:s:{}'.format(sub_idx), output_subs], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                print(result.stderr)
                raise RuntimeError("Error rendering subtitles. ffmpeg returned {}".format(result.returncode))
            subs = "'{}'".format(output_subs)
    
    crop = None
    if args.auto_crop:
        crop = cropdetect(input_filename, start, duration)
    elif args.crop:
        crop = 'crop={}'.format(args.crop)

    print('Calculating resolution: ', end='')
    resolution = None
    if not args.no_resize: # --no_resize argument skips the scale filter altogether
        if args.resolution is not None: # Manual resolution override
            resolution = args.resolution
        else: # Use resolution lookup map
            resolution = calculate_target_resolution(duration, input_filename, compensated_kbps, args.resize_mode, args.bypass_resolution_table) # Look up the appropriate resolution cap in the table
    if resolution is None:
        print('same as source')
    else:
        print(resolution)
    
    print('Calculating fps: ', end='')
    fps = args.fps if args.fps is not None else calculate_target_fps(input_filename, duration) # Look up the target fps
    if fps is not None:
        print(fps)
    else:
        print('same as source')

    # Add video filter arguments
    video_filters = []
    if crop is not None:
        video_filters.append(crop) # Crop should precede scale filter, since it's assumed that crop params correspond to the original input
    if resolution is not None:
        # Constrain to a maximum of the target resolution, horizontal or vertical, while preserving the original aspect ratio
        video_filters.append("scale='min({},iw)':'min({},ih):force_original_aspect_ratio=decrease'".format(resolution,resolution))
    if fps is not None:
        video_filters.append('fps={}'.format(fps))
    if args.video_filter is not None: # Arbitrary user-supplied filters
        video_filters.append(args.video_filter)
    
    # Add audio filters
    audio_filters = []
    if surround_workaround is not None:
        audio_filters.append(surround_workaround) # Tack on the surround workaround filter if applicable
    if np is not None: # Add audio normalization parameters if they exist
        # https://wiki.tnonline.net/w/Blog/Audio_normalization_with_FFmpeg
        # https://superuser.com/questions/1312811/ffmpeg-loudnorm-2pass-in-single-line
        audio_filters.append("loudnorm=linear=true:measured_I={}:measured_LRA={}:measured_tp={}:measured_thresh={}".format(np['input_i'], np['input_lra'], np['input_tp'], np['input_thresh']))
    if args.audio_filter is not None:
        audio_filters.append(args.audio_filter)
    
    video_codec = []
    if args.codec == 'libvpx-vp9':
        video_codec = ["-c:v", "libvpx-vp9", "-deadline", 'good' if args.fast else args.deadline]
        files_to_clean.append('ffmpeg2pass-0.log') # This is the pass 1 file for vp9
        if args.fast:
            video_codec.extend(["-cpu-used", "5"]) # By default, this is 0, 5 means worst quality but fastest
        if not args.no_mt: # Enable multithreading
            video_codec.extend(["-row-mt", "1"])
    elif args.codec == 'libx264':
        video_codec = ["-c:v", "libx264", "-preset", 'fast' if args.fast else 'slower']
        files_to_clean.append('ffmpeg2pass-0.log.mbtree') # This is the pass 1 file for h264
    else:
        raise RuntimeError("Invalid codec option '{}'".format(args.codec))
    print('Target bitrate: {}'.format(video_bitrate))
    video_codec.extend(["-b:v", video_bitrate, "-async", "1", "-vsync", "2"])
    
    audio_codec = []
    if no_audio:
        audio_codec = ["-an"]
    elif args.codec == 'libvpx-vp9':
        audio_codec = ["-c:a", "libopus", '-b:a', audio_bitrate]
    elif args.codec == 'libx264':
        audio_codec = ["-c:a", "aac", '-b:a', audio_bitrate]

    # The main part where the video is rendered
    encode_video(input_filename, output, start, duration, video_codec, video_filters, audio_codec, audio_filters, subs, audio_track, full_video, no_audio, args.mixdown, args.board, args.dry_run)

    if os.path.isfile(output):
        out_size = os.path.getsize(output)
        print('output file size: {} KB'.format(int(out_size/1024)))
        if out_size > size_limit:
            print('WARNING: Output size exceeded target maximum {}. You should rerun with --bitrate_compensation to reduce output size.'.format(int(size_limit/1024)))
    return output

# Figures out which input is image and which is audio. Returns (image, audio), which may be None if image or audio couldn't be found.
def get_image_audio_inputs(args : list):
    mime_types = [ mimetypes.guess_type(x) + (x,) for x in args ]
    image = None
    audio = None
    for type, encoding, filename in mime_types:
        if type is None:
           raise RuntimeError("Unknown mime type for input file '{}'".format(filename))
        # Possible mime types: https://www.iana.org/assignments/media-types/media-types.xhtml
        category = type.split('/')[0]
        if category == 'image':
            image = filename
        elif category == 'audio':
            audio = filename
        else:
            raise RuntimeError("Unsupported mime type '{}' for input file '{}'".format(type, encoding, filename))
    return image, audio

# Special mode for combining a static image (or animated gif) with an audio file
def image_audio_combine(input_image, input_audio, args):
    if args.duration is not None or args.start != '0.0' or args.end is not None:
        print('Warning: start, end, and duration are not used in image + audio mode. Parameters will be ignored.')
    if str(args.board) == 'other':
        print("Warning: Mode 'other' is not supported in audio combine mode. Mode will be treated as 'gif'")
        args.board = BoardMode.gif
    if args.no_audio:
        print("Warning: --no_audio flag will be ignored for image + audio combine mode.")
    if args.codec != 'libvpx-vp9':
        print("Warning: --codec is fixed to libvpx-vp9 for image + audio combine mode. Input will be ignored.")
        args.codec = 'libvpx-vp9'
    if args.cut is not None or args.concat is not None:
        print("Warning: --concat and --cut are not supported in image + audio mode. Parameters will be ignored.")
    output = get_output_filename(input_audio, args)
    
    audio_subtype = mimetypes.guess_type(input_audio)[0].split('/')[-1]
    duration = get_video_duration(input_audio, 0.0)
    print('Audio duration: {}'.format(duration))

    size_limit = get_size_limit(args)
    print('Calculating audio bitrate: ', end='') # Do a lot of prints in case there is an error on one of the steps or it hangs
    audio_kbps = args.audio_rate if args.audio_rate is not None else calculate_target_audio_rate(duration, True, args.board)
    audio_bitrate = '{}k'.format(audio_kbps)
    print(audio_bitrate)
    args.mixdown = get_mixdown_mode(audio_kbps, None, args.mixdown) # Determine mixdown, if any
    print('Calculating audio size')
    audio_size = 0
    audio_copy = False
    # Can copy audio if it's already opus
    if (audio_subtype == 'ogg') and args.normalize is None:
        audio_copy = True
        audio_size = os.path.getsize(input_audio)
    else:
        audio_size, np, surround_workaround, no_audio = calculate_audio_size(input_audio, 0.0, duration, audio_bitrate, None, args.board, 'libopus', args.mixdown, args.normalize)
        if no_audio:
            raise RuntimeError('Unable to complete image + audio combine mode. No audio stream found.')
    print('Audio size: {}kB'.format(int(audio_size/1024)))
    adjusted_size_limit = size_limit - audio_size # File budget subtracting audio
    size_kb = adjusted_size_limit / 1024 * 8 # File budget in kilobits
    target_kbps = min((int)(size_kb / duration.total_seconds()), max_bitrate) # Bit rate in kilobits/sec, limit to max size so that small clips aren't unnecessarily large
    compensated_kbps = target_kbps - calculate_bitrate_compensation(duration, args.bitrate_compensation) # Subtract the compensation factor if specified
    video_bitrate = '{}k'.format(compensated_kbps)
    
    ffmpeg_args = ["ffmpeg", '-hide_banner']

    # Image / video input
    image_subtype = mimetypes.guess_type(input_image)[0].split('/')[-1]
    if image_subtype == 'gif': # Gifs need different arguments than static images
        ffmpeg_args.extend(['-ignore_loop', '0'])
    else:
        ffmpeg_args.extend(['-framerate', '1', '-loop', '1']) # 1 fps, -loop 1 = loop frames
    ffmpeg_args.extend(['-i', input_image])

    # Audio input
    ffmpeg_args.extend(['-i', input_audio])
    if audio_copy:
        print('Opus audio detected. Using copy mode.')
        ffmpeg_args.extend(['-c:a', 'copy'])
    else:
        ffmpeg_args.extend(["-c:a", "libopus", '-b:a', audio_bitrate])
    if args.mixdown == MixdownMode.stereo:
        ffmpeg_args.extend(["-ac", "2"])
    elif args.mixdown == MixdownMode.mono:
        ffmpeg_args.extend(["-ac", "1"])

    # Output
    keyframe_interval = duration.total_seconds()

    # https://ffmpeg.org/ffmpeg-codecs.html#libvpx
    vp9_args = ["-c:v", "libvpx-vp9", "-deadline", 'good' if args.fast else args.deadline]
    # -g sets the maximum keyframe interval. Setting this to the duration of the song causes a slight size reduction.
    # It's possible that there is no detrimental effect to setting this for gif, but I err on the side of caution
    # in case it produces an undesirable side-effect. For static images though, only one keyframe is needed.
    if image_subtype != 'gif':
        vp9_args.extend(["-g", str(keyframe_interval)])
    ffmpeg_args.extend(vp9_args)
    ffmpeg_args.extend(["-b:v", video_bitrate])

    # Video filters
    # Note: I used to include decimate because it saved size for gifs but it seems to cause a desync in the animation
    vf_args = '' 
    if args.crop is not None:
        if vf_args != '':
            vf_args += ',' # Tack on to other args if string isn't empty
        vf_args += args.crop
    if args.resolution is not None:
        if vf_args != '':
            vf_args += ','
        # Constrain to a maximum of the target resolution, horizontal or vertical, while preserving the original aspect ratio
        vf_args += "scale='min({},iw)':'min({},ih):force_original_aspect_ratio=decrease'".format(args.resolution,args.resolution)
    if args.video_filter is not None:
        if vf_args != '':
            vf_args += ','
        vf_args += args.video_filter
    if vf_args != '': # Add video filter if there are any arguments
        ffmpeg_args.extend(["-vf", vf_args])
    
    # Audio filters
    extra_af = []
    if surround_workaround is not None:
        extra_af.append(surround_workaround) # Tack on the surround workaround filter if applicable
    if np is not None: # Add audio normalization parameters if they exist
        extra_af.append("loudnorm=linear=true:measured_I={}:measured_LRA={}:measured_tp={}:measured_thresh={}".format(np['input_i'], np['input_lra'], np['input_tp'], np['input_thresh']))
    if args.audio_filter is not None:
        extra_af.append(args.audio_filter)
    af_args = ''
    for filter in extra_af: # Apply miscellaneous filters
        if af_args != '':
            af_args += ',' # Tack on to other args if string isn't empty
        af_args += filter
    if af_args != '':
        ffmpeg_args.append('-af')
        ffmpeg_args.append(af_args)
    
    # Finalize with the output file itself
    # Need to specify the audio's duration in order to make the output length exactly match,
    # the -t method is more reliable than the -shortest flag, which tends to overshoot the length
    ffmpeg_args.extend(['-t', str(duration), output]) 

    print('Target bitrate: {}'.format(video_bitrate))
    print(' '.join(ffmpeg_args))
    if not args.dry_run:
        # Use popen so we can pend on completion
        pope = subprocess.Popen(ffmpeg_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        for line in iter(pope.stderr.readline, ""):
            if 'frame=' in line:
                print('\r' + line.strip(), end='')
            else:
                print(line, end='')
        pope.stderr.close()
        pope.wait()
        if pope.returncode != 0:
            raise RuntimeError('ffmpeg returned code {}'.format(pope.returncode))
    if os.path.isfile(output):
        out_size = os.path.getsize(output)
        print('output file size: {} KB'.format(int(out_size/1024)))
        if out_size > size_limit:
            print('WARNING: Output size exceeded target maximum {}. You should rerun with --bitrate_compensation to reduce output size.'.format(int(size_limit/1024)))
    return output

# Figures out which input is video and which is audio. Returns the tuple (video, audio), which may be None if video or audio couldn't be found.
def get_video_audio_inputs(args : list):
    mime_types = [ mimetypes.guess_type(x) + (x,) for x in args ]
    video = None
    audio = None
    for type, encoding, filename in mime_types:
        if type is None:
           raise RuntimeError("Unknown mime type for input file '{}'".format(filename))
        # Possible mime types: https://www.iana.org/assignments/media-types/media-types.xhtml
        category = type.split('/')[0]
        if category == 'video':
            video = filename
        elif category == 'audio':
            audio = filename
        else:
            raise RuntimeError("Unsupported mime type '{}' for input file '{}'".format(type, encoding, filename))
    return video, audio

def audio_replace(video_input, audio_input, args):
    if args.duration is not None or args.start != '0.0' or args.end is not None:
        print('Warning: start, end, and duration are not used in audio replace mode. Parameters will be ignored.')
    if str(args.board) == 'other':
        print("Warning: Mode 'other' is not supported in audio replace mode. Mode will be treated as 'gif'")
        args.board = BoardMode.gif
    if args.no_audio:
        print("Warning: --no_audio flag will be ignored for audio replace mode.")
    if args.cut is not None or args.concat is not None:
        print("Warning: --concat and --cut are not supported in audio replace mode. Parameters will be ignored.")
    # Create a temp file that has no sound
    video_out_no_sound = get_temp_filename(os.path.splitext(video_input)[-1].replace('.',''))
    files_to_clean.append(video_out_no_sound)
    ffmpeg_cmd1 = ["ffmpeg", '-hide_banner', '-i', video_input, '-c:v', 'copy', '-an', video_out_no_sound]
    result = subprocess.run(ffmpeg_cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.isfile(video_out_no_sound):
        print(' '.join(ffmpeg_cmd1))
        print(result.stderr)
        raise RuntimeError('Error rendering temp video. ffmpeg return code: {}'.format(result.returncode))
    category, mimetype = mimetypes.guess_type(video_input)[0].split('/')
    ffmpeg_args = ["ffmpeg", '-hide_banner', '-i', video_out_no_sound, '-i', audio_input, '-c:v', 'copy', '-c:a']
    if mimetype == 'mp4':
        print('Using mp4/aac')
        args.codec = 'libx264'
        ffmpeg_args.append('aac')
    elif mimetype == 'webm':
        print('Using webm/opus')
        args.codec = 'libvpx-vp9'
        ffmpeg_args.append('libopus')
    else:
        raise RuntimeError('Unsupported mime type {}/{}'.format(category, mimetype))
    output_filename = get_output_filename(video_input, args)
    vduration = get_video_duration(video_out_no_sound, 0.0)
    print('Video duration: {}'.format(vduration))
    aduration = get_video_duration(audio_input, 0.0)
    print('Audio duration: {}'.format(aduration))
    if aduration > vduration:
        print('Warning: Audio duration is longer than video. Output will be truncated to video length.')
    print('Calculating audio bitrate: ', end='') # Do a lot of prints in case there is an error on one of the steps or it hangs
    audio_kbps = args.audio_rate if args.audio_rate is not None else calculate_target_audio_rate(aduration, True, args.board)
    audio_bitrate = '{}k'.format(audio_kbps)
    print(audio_bitrate)
    # Note that '-t' is to limit duration to the video length in case audio is longer
    ffmpeg_args.extend(['-b:a', audio_bitrate, '-t', str(vduration.total_seconds()), output_filename])
    print(' '.join(ffmpeg_args))
    if not args.dry_run:
        # Use popen so we can pend on completion
        pope = subprocess.Popen(ffmpeg_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        for line in iter(pope.stderr.readline, ""):
            if 'frame=' in line:
                print('\r' + line.strip(), end='')
            else:
                print(line, end='')
        pope.stderr.close()
        pope.wait()
        if pope.returncode != 0:
            raise RuntimeError('ffmpeg returned code {}'.format(pope.returncode))
    if os.path.isfile(output_filename):
        out_size = os.path.getsize(output_filename)
        print('output file size: {} KB'.format(int(out_size/1024)))
        size_limit = get_size_limit(args)
        if out_size > size_limit:
            print('WARNING: Output size exceeded target maximum {}. Note that this mode does not re-encode video. Try a different video/audio candidate.'.format(int(size_limit/1024)))
    return output_filename

do_cleanup = True
def cleanup():
    if do_cleanup:
        for filename in files_to_clean:
            if os.path.isfile(filename):
                os.remove(filename)

def signal_handler(sig, frame):
    cleanup()

if __name__ == '__main__':
    try:
        signal.signal(signal.SIGINT, signal_handler)
        parser = argparse.ArgumentParser(
            prog='4chan Webm Converter',
            description='Attempts to fit video clips into the 4chan size limit',
            epilog='Default behavior is to process the entire video. Specify --start and either --end or --duration to make a clip. Note that input name can be specified either with -i or by just throwing it in as a misc. argument')
        parser.add_argument('-i', '--input', type=str, help='Input file to process')
        parser.add_argument('-o', '--output', type=str, help='Output File name (default output is named after the input prepended with "_1_")')
        parser.add_argument('-s', '--start', type=str, default='0.0', help='Start timestamp, i.e. 0:30:5.125')
        parser.add_argument('-e', '--end', type=str, help='End timestamp, i.e. 0:35:0.000')
        parser.add_argument('-d', '--duration', type=str, help='Clip duration (maximum {} seconds in wsg mode, {} for gif, {} seconds otherwise), i.e. 1:15.000'.format(max_duration[0], max_duration[1], max_duration[2]))
        parser.add_argument('-b', '--bitrate_compensation', default=0, type=int, help='Fixed value to subtract from target bitrate (kbps). Use if your output size is overshooting')
        parser.add_argument('-n', '--normalize', action='store_true', help='Enable 2-pass audio normalization.')
        parser.add_argument('-r', '--resolution', type=int, help="Manual resolution override. Maximum resolution, i.e. 1280. Applied vertically and horzontally, aspect ratio is preserved.")
        parser.add_argument('-a', '--audio_filter', type=str, help="Audio filter arguments. This string is passed directly to the -af chain.")
        parser.add_argument('-v', '--video_filter', type=str, help="Video filter arguments. This string is passed directly to the -vf chain.")
        parser.add_argument('-c', '--concat', '--clip', dest='concat', type=str, help='Segments to concatenate (everything BUT these are cut), separated by ";", i.e. "5:00-5:15;5:45-5:52.4"')
        parser.add_argument('-x', '--cut', type=str, help='Segments to cut (opposite of concatenate), separated by ";", i.e. "5:00-5:15;5:45-5:52.4"')
        parser.add_argument('-k', '--keep_temp_files', action='store_true', help="Keep temporary files generated during size calculation etc.")
        parser.add_argument('--audio_index', type=int, help="Audio track index to select (use --list_audio if you don't know the index)")
        parser.add_argument('--audio_lang', type=str, help="Select audio track by language, must be an exact match with what is listed in the file (use --list_audio if you don't know the language)")
        parser.add_argument('--audio_rate', type=int, choices=audio_bitrate_table, help='Manual audio bit-rate override (kbps)')
        parser.add_argument('--audio_replace', action='store_true', help="Special mode that replaces the audio of a clip with other audio without modifying the video.")
        parser.add_argument('--auto_crop', action='store_true', help="Automatic crop using cropdetect.")
        parser.add_argument('--auto_subs', action='store_true', help="Automatically burn-in the first embedded subtitles, if they exist")
        parser.add_argument('--blackframe', action='store_true', help="Skip initial black frames using a first pass with blackframe filter.")
        parser.add_argument('--board', '--mode', dest='board', type=BoardMode, default='wsg', choices=list(BoardMode), help='Webm convert mode. wsg=6MB with sound, gif=4MB with sound, other=4MB no sound')
        parser.add_argument('--bypass_resolution_table', action='store_true', help='Do not snap to the nearest standard resolution and use raw calculated instead.')
        parser.add_argument('--codec', type=str, default='libvpx-vp9', choices=['libvpx-vp9','libx264'], help='Video codec to use. Default is libvpx-vp9.')
        parser.add_argument('--crop', type=str, help="Crop the video. This string is passed directly to ffmpeg's 'crop' filter. See ffmpeg documentation for details.")
        parser.add_argument('--deadline', type=str, default='good', choices=['good', 'best', 'realtime'], help='The -deadline argument passed to ffmpeg. Default is "good". "best" is higher quality but slower. See libvpx-vp9 documentation for details.')
        parser.add_argument('--dry_run', action='store_true', help='Make all the size calculations without encoding the webm. ffmpeg commands and bitrate calculations will be printed.')
        parser.add_argument('--fast', action='store_true', help='Render fast at the expense of quality. Not recommended except for testing.')
        parser.add_argument('--first_second_every_minute', action='store_true', help='Take 1 second from every minute of the input.')
        parser.add_argument('--fps', type=float, help='Manual fps override.')
        parser.add_argument('--list_audio', action='store_true', help="List audio tracks and quit. Use if you don't know which --audio_index or --audio_lang to specify.")
        parser.add_argument('--list_subs', action='store_true', help="List embedded subtitles and quit. Use if you don't know which --sub_index or --sub_lang to specify.")
        parser.add_argument('--mono', action='store_true', help="Do mono mixdown. Equivalent to --mixdown mono")
        parser.add_argument('--mp4', action='store_true', help="Make .mp4 instead of .webm (shortcut for --codec libx264)")
        parser.add_argument('--music_mode', action='store_true', help="Prioritize audio quality over visual quality.")
        parser.add_argument('--mixdown', type=MixdownMode, default='auto', choices=list(MixdownMode), help='Sound mixdown mode. Default = auto')
        parser.add_argument('--no_audio', action='store_true', help='Drop audio if it exists')
        parser.add_argument('--no_duration_check', action='store_true', help='Disable max duration check')
        parser.add_argument('--no_resize', action='store_true', help='Disable resolution resizing (may cause file size overshoot)')
        parser.add_argument('--no_mixdown', action='store_true', help='Disable automatic audio mixdown. Equivalent to --mixdown same_as_source.')
        parser.add_argument('--no_mt', action='store_true', help='Disable row based multithreading (the "-row-mt 1" switch)')
        parser.add_argument('--resize_mode', type=ResizeMode, default='logarithmic', choices=list(ResizeMode), help='How to calculate target resolution. table = use time-based lookup table. Default is logarithmic.')
        parser.add_argument('--size', '--limit', dest='size', type=float, help='Target file size limit, in MiB. Default is 6 if board is wsg, and 4 otherwise.')
        parser.add_argument('--stereo', action='store_true', help="Do stereo mixdown. Equivalent to --mixdown stereo")
        parser.add_argument('--sub_index', type=int, help="Subtitle index to burn-in (use --list_subs if you don't know the index)")
        parser.add_argument('--sub_lang', type=str, help="Subtitle language to burn-in, must be an exact match with what is listed in the file (use --list_subs if you don't know the language)")
        parser.add_argument('--sub_file', type=str, help='Filename of subtitles to burn-in (use --sub_index or --sub_lang for embedded subs)')
        parser.add_argument('--trim_silence', type=SilenceTrimMode, choices=list(SilenceTrimMode), help="Skip silence using a first pass with silencedetect filter. Skip silence at the start, end, or cut all detected silence.")
        args, unknown_args = parser.parse_known_args()
        if help in args:
            parser.print_help()
        if args.keep_temp_files:
            do_cleanup = False
        if args.mp4: # Use this shortcut flag to override the --codec option
            args.codec = 'libx264'
        if args.stereo: # Determine aliases for mixdown mode
            args.mixdown = MixdownMode.stereo
        if args.mono:
            args.mixdown = MixdownMode.mono
        if args.no_mixdown:
            args.mixdown = MixdownMode.same_as_source
        input_filename = None
        if args.size is not None and args.size > 6.0:
            print("Warning: Manual size limit is larger than 4chan's supported size of 6MiB!")
        if args.audio_replace:
            if len(unknown_args) == 2 and os.path.isfile(unknown_args[0]) and os.path.isfile(unknown_args[1]):
                print('Using audio replace mode.')
                video_input, audio_input = get_video_audio_inputs(unknown_args)
                if video_input is None:
                    raise RuntimeError("Couldn't identify video source from input files.")
                if audio_input is None:
                    raise RuntimeError("Couldn't identify audio source from input files.")
                result = audio_replace(video_input, audio_input, args)
                print('output file: "{}"'.format(result))
                cleanup()
                exit(0)
        if args.input is not None: # Input was explicitly specified
            input_filename = args.input
        elif len(unknown_args) > 0: # Input was specified as an unknown argument, attempt smart context parsing
            if len(unknown_args) == 2 and os.path.isfile(unknown_args[0]) and os.path.isfile(unknown_args[1]): # Try to detect music + static image assembly mode
                print("2 input files specified. Using image + audio combine mode.")
                image_input, audio_input = get_image_audio_inputs(unknown_args)
                if image_input is None:
                    raise RuntimeError("Couldn't identify image source from input files.")
                if audio_input is None:
                    raise RuntimeError("Couldn't identify audio source from input files.")
                result = image_audio_combine(image_input, audio_input, args)
                print('output file: "{}"'.format(result))
                cleanup()
                exit(0)
            else:
                timestamp_args = []
                for arg in unknown_args:
                    if os.path.isfile(arg):
                        input_filename = arg
                    elif is_timestamp(arg):
                        timestamp_args.append(arg)
                    elif is_segment(arg):
                        args.concat = arg
                        print('Treating {} as a --concat segment'.format(arg))
                    else:
                        print("Unable to parse argument: '{}'".format(arg))
                        print('Please command-line flags to specify complex arguments')
                        exit(0)
            if len(timestamp_args) > 0:
                if args.concat is not None:
                    print('Unable to parse arguments. Concat segments cannot be used in conjuction with other unspecified timestamps.')
                    exit(0)
                if args.start != '0.0' or args.end is not None or args.duration is not None:
                    print('Unable to parse arguments. Unspecified timestamps cannot be used in conjuction with --start, --end, or --duration.')
                    exit(0)
                if len(timestamp_args) == 1: # One timestamp is treated as a duration
                    args.duration = timestamp_args[0]
                elif len(timestamp_args) == 2: # Two timestamps imply a start and end
                    parsed_ts_args = {x:parsetime(x) for x in timestamp_args} # Create dict where values are parsed timestamps
                    args.start = min(parsed_ts_args, key=parsed_ts_args.get) # Assume min value is start time
                    print('Treating {} as a --start time.'.format(args.start))
                    args.end = max(parsed_ts_args, key=parsed_ts_args.get) # Assume max value is end time
                    print('Treating {} as an --end time.'.format(args.end))
                else:
                    print('Argument parsing failed. Too many timestamps specified.')
                    exit(0)
            if input_filename is None:
                print('No input filename found.')
        else:
            parser.print_help() # Can't identify the input file
            exit(0)
        if input_filename is None:
            parser.print_help()
            exit(0)
        if os.path.isfile(input_filename):
            # List available subtitles and quit
            if args.list_subs:
                subs = list_subtitles(input_filename)
                for idx, lang in subs.items():
                    print('{},{}'.format(idx, lang))
                exit(0)
            if args.list_audio:
                tracks = list_audio(input_filename)
                for idx, lang in tracks.items():
                    layout, no_audio = get_audio_layout(input_filename, idx) # Also list layout
                    print('{},{},{}'.format(idx, lang,layout))
                exit(0)
            start_time = parsetime(args.start)
            print('start time:', start_time)
            # Attempt to find the duration of the clip
            duration = datetime.timedelta(milliseconds=0)
            full_video = False # Special flag for encoding the full video, which will skip -ss
            # Prefer a direct duration if specified
            if args.duration is not None:
                duration = parsetime(args.duration)
            # If an end timestamp is specified, convert that to a relative duration using start time
            elif args.end is not None:
                end = parsetime(args.end)
                if end.total_seconds() < start_time.total_seconds():
                    raise ValueError("Error: End time must be greater than start time")
                duration = end - start_time
            # If neither was specified, use the video itself
            else:
                duration = get_video_duration(input_filename, start_time.total_seconds())
                if start_time.total_seconds() == 0:
                    full_video = True
            print('duration:', duration)
            result = process_video(input_filename, start_time, duration, args, full_video)
            print('output file: "{}"'.format(result))
            cleanup()   
        else:
            print('Input file not found: "' + input_filename + '"')
    except argparse.ArgumentError as e:
        print(e)
    except ValueError as e:
        print(e)
    except Exception:
        print(traceback.format_exc())