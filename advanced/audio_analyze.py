import argparse
import datetime
import os
import subprocess
import traceback

from .common_utils import get_temp_filename, get_video_duration, parsetime

import scipy.io.wavfile as wavfile
from sklearn.metrics.pairwise import cosine_similarity
import soundfile
import pystoi

def list_tracks(input_filename):
    """ Return a dictionary of the available audio tracks, with index as the key and codec as the value """
    result = subprocess.run(["ffprobe","-v", "error", "-show_entries", "stream=index,codec_name", "-select_streams", "a", "-of", "csv=p=0", input_filename], stdout=subprocess.PIPE, text=True)
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

def convert_to_wav(video_input : str, audio_bitrate = 320, full_audio = True, start : datetime.timedelta = None, duration : datetime.timedelta = None):
    """
    Converts the input to a wav file.

    video_input - The input filename to process
    audio_bitrate - Output bitrate, in kbps
    full_audio - A flag signaling whether or not to encode the whole thing. Make it False if you want to trim to the start and duration
    start - The start time to skip to with the ffmpeg '-ss' parameter. Only applied if full_audio = False
    duration - The duration to use with the ffmpeg '-t' parameter. Only applied if full_audio = False
    """
    tracks = list_tracks(video_input)
    ffmpeg_cmd = ["ffmpeg", '-hide_banner', '-y']
    if not full_audio:
        ffmpeg_cmd.extend(['-ss', str(start), '-t', str(duration)])
    ffmpeg_cmd.extend(['-i', video_input, '-vn'])
    if len(tracks) > 1:
        print('Multiple audio tracks detected. Using first track.')
        ffmpeg_cmd.extend(['-map', '0:a:0'])
    ffmpeg_cmd.extend(['-acodec', 'pcm_s16le'])
    ffmpeg_cmd.extend(['-b:a', '{}k'.format(audio_bitrate)])
    audio_no_video = get_temp_filename('wav')
    ffmpeg_cmd.append(audio_no_video)
    result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.isfile(audio_no_video):
        print(' '.join(ffmpeg_cmd))
        print(result.stderr)
        raise RuntimeError('Error rendering temp video. ffmpeg return code: {}'.format(result.returncode))
    return audio_no_video

def render_mono_opus(video_input : str, audio_bitrate : int, full_audio = True, start : datetime.timedelta = None, duration : datetime.timedelta = None):
    """
    Converts the input to a mono opus file.

    video_input - The input filename to process
    audio_bitrate - Output bitrate, in kbps
    full_audio - A flag signaling whether or not to encode the whole thing. Make it False if you want to trim to the start and duration
    start - The start time to skip to with the ffmpeg '-ss' parameter. Only applied if full_audio = False
    duration - The duration to use with the ffmpeg '-t' parameter. Only applied if full_audio = False
    """
    tracks = list_tracks(video_input)
    ffmpeg_cmd = ["ffmpeg", '-hide_banner', '-y']
    if not full_audio:
        ffmpeg_cmd.extend(['-ss', str(start), '-t', str(duration)])
    ffmpeg_cmd.extend(['-i', video_input, '-vn'])
    if len(tracks) > 1:
        print('Multiple audio tracks detected. Using first track.')
        ffmpeg_cmd.extend(['-map', '0:a:0'])
    ffmpeg_cmd.extend(['-ac', '1', '-acodec', 'libopus', '-b:a', '{}k'.format(audio_bitrate)])
    audio_no_video = get_temp_filename('opus')
    ffmpeg_cmd.append(audio_no_video)
    result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.isfile(audio_no_video):
        print(' '.join(ffmpeg_cmd))
        print(result.stderr)
        raise RuntimeError('Error rendering temp video. ffmpeg return code: {}'.format(result.returncode))
    return audio_no_video

def calculate_cosine_similarity(input_filename : str, full_audio = True, start : datetime.timedelta = None, duration : datetime.timedelta = None):
    """
    Calculates the cosine similarity of a stereo track, resulting in a number from 0 to 1 with 1 being identical.

    input_filename - The input filename to process
    full_audio - A flag signaling whether or not to encode the whole thing. Make it False if you want to trim to the start and duration
    start - The start time to skip to with the ffmpeg '-ss' parameter. Only applied if full_audio = False
    duration - The duration to use with the ffmpeg '-t' parameter. Only applied if full_audio = False
    """
    temp_files = []
    # Can only import wav files, so render an intermediate
    #print('Extracting audio...')
    audio_filename = convert_to_wav(input_filename, full_audio=full_audio, start=start, duration=duration)
    temp_files.append(audio_filename)

    rate, data = wavfile.read(audio_filename)
    num_channels = data.shape[1]
    #print('Channels: {}'.format(num_channels))
    cosimilarity = None
    if num_channels == 1:
        print('Mono audio detected.')
        cosimilarity = 1
    elif num_channels == 2:
        left = data[:, 0]
        right = data[:, 1]
        cosimilarity = cosine_similarity(left.reshape(1, -1), right.reshape(1, -1))[0][0]
        print('Channel cosine similarity: {:.4f}%'.format(cosimilarity * 100))
    elif num_channels == 6:
        print('5.1 Channel audio detected. Unable to analyze channel similarity.')
    return cosimilarity, temp_files

def calculate_bitrate_from_stoi(input_filename : str, stoi_threshold = 0.99, full_audio = True, start : datetime.timedelta = None, duration : datetime.timedelta = None):
    """
    Calculates the Short Term Objective Intelligibility of various bitrates.
    Returns the lowest available bitrate with stoi over the stoi_threshold.

    input_filename - The input filename to process
    full_audio - A flag signaling whether or not to encode the whole thing. Make it False if you want to trim to the start and duration
    start - The start time to skip to with the ffmpeg '-ss' parameter. Only applied if full_audio = False
    duration - The duration to use with the ffmpeg '-t' parameter. Only applied if full_audio = False
    """
    print('Running stoi comparisons...')
    temp_files = []
    audio_80k = render_mono_opus(input_filename, 80, full_audio, start, duration)
    temp_files.append(audio_80k)
    a80, fs = soundfile.read(audio_80k)
    ideal_bitrate = 80
    # Analyze the stoi over different bitrates and determine how low we can go
    bitrates = [64, 56, 48, 32, 24]
    for bitrate in bitrates:
        reduced_quality = render_mono_opus(input_filename, bitrate, full_audio, start, duration)
        temp_files.append(reduced_quality) 
        areduced, fs = soundfile.read(reduced_quality)
        d = pystoi.stoi(a80, areduced, fs, extended=False)
        print('stoi 80k vs {}k: {:.4f}'.format(bitrate, d * 100))
        if d > stoi_threshold:
            ideal_bitrate = bitrate
        else:
            break # Audio quality has degraded too much
    print('Best audio bitrate: {}kbps'.format(ideal_bitrate))
    return ideal_bitrate, temp_files


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(
            prog='Vocal Silence Detect',
            description='Uses Ultimate Vocal Remover to isolate vocals, then splits on silence using the vocal track.')
        parser.add_argument('-i', '--input', type=str, help='Input file to process')
        parser.add_argument('-s', '--start', type=str, default='0.0', help='Start timestamp, i.e. 0:30:5.125')
        parser.add_argument('-e', '--end', type=str, help='End timestamp, i.e. 0:35:0.000')
        parser.add_argument('-d', '--duration', type=str, help='Clip duration, use instead of --end')
        parser.add_argument('-k', '--keep_temp_files', action='store_true', help="Keep temporary files generated during processing.")
        args, unknown_args = parser.parse_known_args()
        if help in args:
            parser.print_help()
        for arg in unknown_args:
            if os.path.isfile(arg):
                args.input = arg
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
            duration = get_video_duration(args.input, start_time.total_seconds())
            if start_time.total_seconds() == 0:
                full_video = True
        print('duration:', duration)
        similarity, temp_files = calculate_cosine_similarity(args.input, full_video, start_time, duration)
        stoi, more_temp_files = calculate_bitrate_from_stoi(args.input, full_audio=full_video, start=start_time, duration=duration)
        temp_files.extend(more_temp_files)
        # Cleanup temp files
        if not args.keep_temp_files:
            for filename in temp_files:
                if os.path.isfile(filename):
                    os.remove(filename)
    except argparse.ArgumentError as e:
        print(e)
    except ValueError as e:
        print(e)
    except Exception:
        print(traceback.format_exc())