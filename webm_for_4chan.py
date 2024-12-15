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
import os
import platform
import subprocess
import time
import traceback

max_bitrate = 2800 # (kbps) Cap bitrate in case the clip is really short. This is already an absurdly high rate.
max_size = [6144 * 1024, 4096 * 1024] # 4chan size limits, in bytes [wsg, all other boards]
max_duration = [400, 120] # Maximum clip durations, in seconds [wsg, all other boards]
resolution_table = [480, 576, 640, 720, 840, 960, 1024, 1280, 1440, 1600, 1920, 2048] # Table of discrete resolutions
audio_bitrate_table = [12, 24, 32, 40 , 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512] # Table of discrete audio bit-rates
resolution_fallback_map = { # This time-based lookup is used if smart resolution calculation fails for some reason
    15.0: 1920,
    30.0: 1600,
    45.0: 1440,
    75.0: 1280,
    115.0: 1024,
    145.0: 960,
    185.0: 840,
    245.0: 720,
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
null_output = 'NUL' if platform.system() == 'Windows' else '/dev/null' # For pass 1 and certain preprocessing steps, need to output to appropriate null depending on system

# This is only called if you don't specify a duration or end time. Uses ffprobe to find out how long the input is.
def get_video_duration(input_filename, start_time : float):
    # https://superuser.com/questions/650291/how-to-get-video-duration-in-seconds
    result = subprocess.run(["ffprobe","-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_filename], stdout=subprocess.PIPE, text=True)
    duration_seconds = float(result.stdout)
    return datetime.timedelta(seconds=duration_seconds - start_time)

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
class ConvertMode(Enum):
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

# Scales resolution sources to 1080p to match the calibrated resolution curve
def scale_to_1080(width, height):
    min_dimension = min(width, height)
    scale_factor = 1080 / min_dimension
    return [width * scale_factor, height * scale_factor]

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
                return nearest_resolution
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
    return frame_rate # Return calculated fps otherwise

# Use audio lookup table
def calculate_target_audio_rate(duration, music_mode, mode : ConvertMode):
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

# Simply renders the audio to file and gets its size.
# This is the most precise way of knowing the final audio size and rendering this takes a fraction of the time it takes to render the video.
# Returns a tuple containing the audio bit rate, normalization parameters if applicable, and the 5.1 surround workaround flag if applicable
def calculate_audio_size(input_filename, start, duration, audio_bitrate, track, mode, normalize):
    if str(mode) == 'wsg' or str(mode) == 'gif':
        surround_workaround = False # For working around a known bug in libopus: https://trac.ffmpeg.org/ticket/5718
        surround_workaround_args = ['-af', "channelmap=channel_layout=5.1"]
        output = 'temp.opus'
        if os.path.isfile(output):
            os.remove(output)
        ffmpeg_cmd = ['ffmpeg', '-ss', str(start), '-t', str(duration), '-i', input_filename, '-vn', '-acodec', 'libopus', '-b:a', audio_bitrate]
        if track is not None: # Optional audio track selection
            ffmpeg_cmd.extend(['-map', '0:a:{}'.format(track)])
        ffmpeg_cmd1 = ffmpeg_cmd.copy()
        ffmpeg_cmd1.append(output)
        result = subprocess.run(ffmpeg_cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 or not os.path.isfile(output):
            for line in result.stderr.splitlines():
                # Try to rerun with 5.1 workaround
                if 'libopus' in line and 'Invalid channel layout 5.1' in line:
                    surround_workaround = True
                    ffmpeg_cmd2 = ffmpeg_cmd.copy()
                    ffmpeg_cmd2.extend(surround_workaround_args)
                    ffmpeg_cmd2.append(output)
                    print('Warning: ffmpeg returned status code {}. Trying 5.1 workaround.'.format(result.returncode))
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
                output2 = 'temp.normalized.opus'
                if os.path.isfile(output2):
                    os.remove(output2)
                # The size of the normalized audio is different from the initial one, so render to get the exact size
                ffmpeg_cmd = ['ffmpeg', '-ss', str(start), '-t', str(duration), '-i', input_filename, '-vn', '-acodec', 'libopus', '-filter:a', 'loudnorm=linear=true:measured_I={}:measured_LRA={}:measured_tp={}:measured_thresh={}'.format(params['input_i'], params['input_lra'], params['input_tp'], params['input_thresh']), '-b:a', audio_bitrate]
                if track is not None: # Optional audio track selection
                    ffmpeg_cmd.extend(['-map', '0:a:{}'.format(track)])
                if surround_workaround:
                    ffmpeg_cmd.extend(surround_workaround_args)
                ffmpeg_cmd.append(output2)
                result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if result.returncode == 0 and os.path.isfile(output2):
                    return [os.path.getsize(output2), params, surround_workaround]
                else:
                    print('Warning: Could not render normalized audio. Skipping normalization.')
                    print('Debug info:')
                    print('ffmpeg return code: {}'.format(result.returncode))
                    print('stdout: {}'.format(result.stdout))
                    print('stderr: {}'.format(result.stderr))
                    return [os.path.getsize(output), None, surround_workaround]
            else:
                print('Warning: Could not process normalized audio. Skipping normalization.')
                print('Debug info:')
                print('ffmpeg return code: {}'.format(result.returncode))
                print('stdout: {}'.format(result.stdout))
                print('stderr: {}'.format(result.stderr))
                return [os.path.getsize(output), None, surround_workaround]
        return [os.path.getsize(output), None, surround_workaround]
    else: # No audio
        return [0, None, False]

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
    
# Convoluted method of determining the output file name. Avoid overwriting existing files, etc.
def get_output_filename(input_filename, args):
    # Use manually specified output
    if args.output is not None:
        output = args.output
        # Force webm suffix
        if os.path.splitext(output)[-1] != '.webm':
            output += '.webm'
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
            final_output = os.path.dirname(output) + (os.path.sep if os.path.dirname(output) != "" else "") + '_{}_'.format(filename_count) + os.path.basename(output) + '.webm'
            if os.path.isfile(final_output):
                filename_count += 1 # Try to deconflict the file name by finding a different file name
            else:
                return final_output

# The part where the webm is encoded
def encode_video(input, output, start, duration, video_bitrate, resolution, audio_bitrate, deadline : str, mt : bool, crop, extra_vf : list, extra_af : list, subtitles, track, full_video : bool, mode : ConvertMode, fast : bool, dry_run : bool):
    ffmpeg_args = ["ffmpeg"]
    slice_args = ['-ss', str(start), "-t", str(duration)] # The arguments needed for slicing a clip
    vf_args = '' # The video filter arguments. Size limit, fps, burn-in subtitles, etc.
    if crop is not None:
        vf_args += crop
    if resolution is not None:
        if vf_args != '':
            vf_args += ',' # Tack on to other args if string isn't empty
        # Constrain to a maximum of the target resolution, horizontal or vertical, while preserving the original aspect ratio
        vf_args += "scale='min({},iw)':'min({},ih):force_original_aspect_ratio=decrease'".format(resolution,resolution)
    print('Calculating fps: ', end='')
    fps = calculate_target_fps(input, duration) # Look up the target fps
    if fps is not None:
        print(fps)
        if vf_args != '':
            vf_args += ',' # Tack on to other args if string isn't empty
        vf_args += 'fps={}'.format(fps)
    else:
        print('same as source')
    for filter in extra_vf:
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
    print('Target bitrate: {}'.format(video_bitrate))
    vp9_args = ["-c:v", "libvpx-vp9", "-deadline", 'good' if fast else deadline]
    ffmpeg_args.extend(vp9_args)
    if fast:
        ffmpeg_args.extend(["-cpu-used", "5"]) # By default, this is 0, 5 means worst quality but fastest
    if mt: # Enable multithreading
        ffmpeg_args.extend(["-row-mt", "1"])
    ffmpeg_args.extend(["-b:v", video_bitrate, "-async", "1", "-vsync", "2"])

    # The constructed ffmpeg commands
    pass1 = ffmpeg_args
    pass2 = ffmpeg_args.copy() # Must make deep copy, or else arguments get jumbled
    pass1.extend(["-pass", "1"])
    pass2.extend(["-pass", "2"])
    pass1.extend(["-an", "-f", "null", null_output]) # Pass 1 doesn't output to file

    # Audio options. wsg/gif allow audio, else omit audio
    if str(mode) == 'wsg' or str(mode) == 'gif':
        if track is not None: # Optional track selection
            pass2.extend(['-map', '0:v:0', '-map', '0:a:{}'.format(track)])
        else: # When testing multi-track videos, leaving this argument out causes buggy time codes when clipping for some unknown reason
            #print('Inspecting audio tracks')
            audio_tracks = list_audio(input)
            if len(audio_tracks.items()) > 1:
                print('Multiple audio tracks detected, selecting track 0.')
                pass2.extend(['-map', '0:v:0', '-map', '0:a:0'])
        af_args = ''
        for filter in extra_af: # Apply miscellaneous filters
            if af_args != '':
                af_args += ',' # Tack on to other args if string isn't empty
            af_args += filter
        if af_args != '':
            pass2.append('-af')
            pass2.append(af_args)
        pass2.extend(["-c:a", "libopus", '-b:a', audio_bitrate])
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

def process_video(input_filename, start, duration, args, full_video):
    output = get_output_filename(input_filename, args)
    
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
    size_limit = max_size[0] if str(args.mode) == 'wsg' else max_size[1] # Look up the size cap depending on the board it's destined for
    print('Calculating audio bitrate: ', end='') # Do a lot of prints in case there is an error on one of the steps or it hangs
    audio_kbps = args.audio_rate if args.audio_rate is not None else calculate_target_audio_rate(duration, args.music_mode, args.mode)
    audio_bitrate = '{}k'.format(audio_kbps)
    print(audio_bitrate)
    print('Calculating audio size')
    # Calculate the audio file size and the volume normalization parameters if applicable. Always skip normalization in music mode.
    audio_size, np, surround_workaround = calculate_audio_size(input_filename, start, duration, audio_bitrate, audio_track, args.mode, args.normalize)
    print('Audio size: {}kB'.format(int(audio_size/1024)))
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
            output_subs = 'temp.ass'
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
    
    # Add miscellaneous video filters
    extra_vf = []
    if args.video_filter is not None:
        extra_vf.append(args.video_filter)
    
    # Add miscellaneous audio filters
    extra_af = []
    if surround_workaround:
        extra_af.append("channelmap=channel_layout=5.1") # Tack on the 5.1 surround workaround filter if applicable
    if np is not None: # Add audio normalization parameters if they exist
        # https://wiki.tnonline.net/w/Blog/Audio_normalization_with_FFmpeg
        # https://superuser.com/questions/1312811/ffmpeg-loudnorm-2pass-in-single-line
        extra_af.append("loudnorm=linear=true:measured_I={}:measured_LRA={}:measured_tp={}:measured_thresh={}".format(np['input_i'], np['input_lra'], np['input_tp'], np['input_thresh']))
    if args.audio_filter is not None:
        extra_af.append(args.audio_filter)

    # The main part where the video is rendered
    encode_video(input_filename, output, start, duration, video_bitrate, resolution, audio_bitrate, args.deadline, not args.no_mt, crop, extra_vf, extra_af, subs, audio_track, full_video, args.mode, args.fast, args.dry_run)

    if os.path.isfile(output):
        out_size = os.path.getsize(output)
        print('output file size: {} KB'.format(int(out_size/1024)))
        if out_size > size_limit:
            print('WARNING: Output size exceeded target maximum {}. You should rerun with --bitrate_compensation to reduce output size.'.format(int(size_limit/1024)))
    return output

def cleanup():
    for filename in ['temp.opus', 'temp.normalized.opus', 'temp.ass', 'ffmpeg2pass-0.log']:
        if os.path.isfile(filename):
            os.remove(filename)

if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(
            prog='4chan Webm Converter',
            description='Attempts to fit video clips into the 4chan size limit',
            epilog='Default behavior is to process the entire video. Specify --start and either --end or --duration to make a clip. Note that input name can be specified either with -i or by just throwing it in as a misc. argument')
        parser.add_argument('-m', '--mode', type=ConvertMode, default='wsg', choices=list(ConvertMode), help='Webm convert mode. wsg=6MB with sound, gif=4MB with sound, other=4MB no sound')
        parser.add_argument('-i', '--input', type=str, help='Input file to process')
        parser.add_argument('-o', '--output', type=str, help='Output File name (default output is named after the input prepended with "_1_")')
        parser.add_argument('-s', '--start', type=str, default='0.0', help='Start timestamp, i.e. 0:30:5.125')
        parser.add_argument('-e', '--end', type=str, help='End timestamp, i.e. 0:35:0.000')
        parser.add_argument('-d', '--duration', type=str, help='Clip duration (maximum {} seconds in wsg mode and {} seconds otherwise), i.e. 1:15.000'.format(max_duration[0], max_duration[1]))
        parser.add_argument('-b', '--bitrate_compensation', default=0, type=int, help='Fixed value to subtract from target bitrate (kbps). Use if your output size is overshooting')
        parser.add_argument('-r', '--resolution', type=int, help="Manual resolution override. Maximum resolution, i.e. 1280. Applied vertically and horzontally, aspect ratio is preserved.")
        parser.add_argument('-c', '--crop', type=str, help="Crop the video. This string is passed directly to ffmpeg's 'crop' filter. See ffmpeg documentation for details.")
        parser.add_argument('-n', '--normalize', action='store_true', help='Enable 2-pass audio normalization.')
        parser.add_argument('-v', '--video_filter', type=str, help="Video filter arguments. This string is passed directly to the -vf chain.")
        parser.add_argument('-a', '--audio_filter', type=str, help="Audio filter arguments. This string is passed directly to the -af chain.")
        parser.add_argument('--auto_crop', action='store_true', help="Automatic crop using cropdetect.")
        parser.add_argument('--blackframe', action='store_true', help="Skip initial black frames using a first pass with blackframe filter.")
        parser.add_argument('--music_mode', action='store_true', help="Prioritize audio quality over visual quality.")
        parser.add_argument('--list_subs', action='store_true', help="List embedded subtitles and quit. Use if you don't know which --sub_index or --sub_lang to specify.")
        parser.add_argument('--auto_subs', action='store_true', help="Automatically burn-in the first embedded subtitles, if they exist")
        parser.add_argument('--sub_index', type=int, help="Subtitle index to burn-in (use --list_subs if you don't know the index)")
        parser.add_argument('--sub_lang', type=str, help="Subtitle language to burn-in, must be an exact match with what is listed in the file (use --list_subs if you don't know the language)")
        parser.add_argument('--sub_file', type=str, help='Filename of subtitles to burn-in (use --sub_index or --sub_lang for embedded subs)')
        parser.add_argument('--list_audio', action='store_true', help="List audio tracks and quit. Use if you don't know which --audio_index or --audio_lang to specify.")
        parser.add_argument('--audio_index', type=int, help="Audio track index to select (use --list_audio if you don't know the index)")
        parser.add_argument('--audio_lang', type=str, help="Select audio track by language, must be an exact match with what is listed in the file (use --list_audio if you don't know the language)")
        parser.add_argument('--audio_rate', type=int, choices=audio_bitrate_table, help='Manual audio bit-rate override (kbps)')
        parser.add_argument('--no_resize', action='store_true', help='Disable resolution resizing (may cause file size overshoot)')
        parser.add_argument('--no_mt', action='store_true', help='Disable row based multithreading (the "-row-mt 1" switch)')
        parser.add_argument('--bypass_resolution_table', action='store_true', help='Do not snap to the nearest standard resolution and use raw calculated instead.')
        parser.add_argument('--resize_mode', type=ResizeMode, default='logarithmic', choices=list(ResizeMode), help='How to calculate target resolution. table = use time-based lookup table. Default is logarithmic.')
        parser.add_argument('--fast', action='store_true', help='Render fast at the expense of quality. Not recommended except for testing.')
        parser.add_argument('--deadline', type=str, default='good', choices=['good', 'best', 'realtime'], help='The -deadline argument passed to ffmpeg. Default is "good". "best" is higher quality but slower. See libvpx-vp9 documentation for details.')
        parser.add_argument('--dry_run', action='store_true', help='Make all the size calculations without encoding the webm. ffmpeg commands and bitrate calculations will be printed.')
        args, unknown_args = parser.parse_known_args()
        if help in args:
            parser.print_help()
        input_filename = None
        if args.input is not None: # Input was explicitly specified
            input_filename = args.input
        elif len(unknown_args) > 0: # Input was specified as an unknown argument
            input_filename = unknown_args[-1]
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
                    print('{},{}'.format(idx, lang))
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
            duration_sec = duration.total_seconds()
            duration_limit = max_duration[0] if str(args.mode) == 'wsg' else max_duration[1]
            if duration_sec > duration_limit:
                raise ValueError("Error: Specified duration {} seconds exceeds maximum {} seconds".format(duration_sec, duration_limit))
            result = process_video(input_filename, start_time, duration, args, full_video)
            print('output file: "{}"'.format(result))
            if not args.dry_run:
                cleanup()
        else:
            print('Input file not found: "' + input_filename + '"')
    except argparse.ArgumentError as e:
        print(e)
    except ValueError as e:
        print(e)
    except Exception:
        print(traceback.format_exc())