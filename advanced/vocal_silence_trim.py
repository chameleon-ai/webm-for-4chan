# standard library
import argparse
import datetime
from enum import Enum
import mimetypes
import os
import shutil
import subprocess
import traceback

# packages
from pydub import AudioSegment
from pydub.silence import detect_silence

# Local modules
from .uvr_cli import uvr_separate
from .common_utils import *

class TrimMode(Enum):
    vocal_only = 'vocal_only' # Only keep vocal track and discard instrumental track
    continuous_instrumental = 'continuous_instrumental' # Trim vocals and reassemble with untrimmed instrumental track
    substitute_instrumental = 'substitute_instrumental' # Swap the instrumental track with a user specified one
    all = 'all' # Trim both instrumentals and vocals, keeping them in sync
    def __str__(self):
        return self.value

# Format a time in milliseconds into hh:mm:ss.ms
def format_time_ms(ms : float):
    ts = datetime.timedelta(milliseconds=ms)
    hours, rm_hr = divmod(ts.total_seconds(), 3600)
    mins, rm_min = divmod(rm_hr, 60)
    sec, rm_sec = divmod(rm_min, 1)
    ts_str = ''
    # Omit leading zero hours and minutes
    if int(hours) == 0:
        if int(mins) == 0:
            ts_str = '{:02}.{:03}'.format(int(sec), int(rm_sec*1000))
        else:
            ts_str = '{:02}:{:02}.{:03}'.format(int(mins), int(sec), int(rm_sec*1000))
    else:
        ts_str = '{:02}:{:02}:{:02}.{:03}'.format(int(hours), int(mins), int(sec), int(rm_sec*1000))
    return ts_str

def build_filter_graph(segments_to_keep):
    video_filter_graph = ''
    # Build a filter graph on the kept segments
    # Nice reference for how to build a filter graph: https://github.com/sriramcu/ffmpeg_video_editing
    for index, segment in enumerate(segments_to_keep, start=1):
        segment_start, segment_end = segment
        # [0]trim=start=34.5:end=55.1,setpts=PTS-STARTPTS[v1];
        video_filter_graph += '[0]trim=start={}:end={},setpts=PTS-STARTPTS[v{}];'.format(segment_start / 1000.0, segment_end / 1000.0, index)    
        #print('{} {}-{}'.format(index, segment_start, segment_end))
    for index, segment in enumerate(segments_to_keep, start=1):
        # [v1][v2][v3]concat=n=3:v=1:a=0[outv]
        video_filter_graph += '[v{}]'.format(index)
    video_filter_graph += 'concat=n={}:v=1:a=0[outv]'.format(len(segments_to_keep))
    return video_filter_graph

def build_cut_segments(duration, segments_to_cut):
    try: 
        # Invert the cut segments into the segments to keep
        segments_duration = 0
        segments_to_keep = []
        temp_start_time = 0
        for segment_start, segment_end in segments_to_cut:
            segment_duration = segment_end - segment_start
            segments_duration += segment_duration
            start_time = temp_start_time
            end_time = segment_start
            segments_to_keep.append((start_time, end_time))
            temp_start_time = end_time + segment_duration
        # Final segment
        segments_to_keep.append((temp_start_time, duration * 1000.0))
        #print('Total cut segment time: {}'.format(format_time_ms(segments_duration)))

        # Segments are ready to be built
        return build_filter_graph(segments_to_keep)
    except Exception as e:
        raise RuntimeError('Error parsing cut segments: {}'.format(e))

def silence_trim(vocal_stem : str, instrumental_stem : str, mode=TrimMode.all, keep_silence=250, min_silence_len=600, silence_thresh=-58, seek_step=1, substitute_instrumental=None, instrumental_gain=0):
    """
    Trims the silence out of the vocal segment and re-assembles the vocal and instrumental tracks.
    Returns a list of timestamps that were trimmed and the name of the re-assembled cut track.

    vocal_stem - the filename of the vocal stem to trim
    instrumental_stem - the filename of the instrumental stem, which is integrated according to mode
    mode - how to overlay the instrumental stem. If vocal_only, the instrumental stem is discarded.
    keep_silence - the amount of silence to keep in ms
    min_silence_len - the minimum length for any silent section
    silence_thresh - the upper bound for how quiet is silent in dFBS
    seek_step - step size for interating over the segment in ms
    """

    if min_silence_len <= keep_silence*2:
        raise RuntimeError('keep_silence length of {} must be less than half of min_silence_len {}'.format(keep_silence, min_silence_len))
    print('Analyzing vocal track...')
    vocal_segment = AudioSegment.from_file(vocal_stem)
    instrumental_segment = AudioSegment.from_file(instrumental_stem)
    timestamps = detect_silence(vocal_segment, min_silence_len=min_silence_len, silence_thresh=silence_thresh, seek_step=seek_step)
    adjusted_timestamps = [ [start+keep_silence, end-keep_silence] for start,end in timestamps ]
    time_total_ms = sum([ end-start for start,end in adjusted_timestamps ])
    print('Total silent segment duration: {}'.format(format_time_ms(time_total_ms)))

    # Iterate over all segments to cut and remove them
    start = 0
    trimmed_vocals = AudioSegment.empty()
    trimmed_instrumental = AudioSegment.empty()
    for start_cut, end_cut in adjusted_timestamps:
        trimmed_vocals += vocal_segment[start:start_cut]
        trimmed_instrumental += instrumental_segment[start:start_cut]
        start = end_cut

    # Overlay vocal and instrumental segments depending on mode
    output_segment = AudioSegment.empty()
    if mode == TrimMode.all:
        if instrumental_gain != 0:
                trimmed_instrumental += instrumental_gain
        output_segment = trimmed_instrumental.overlay(trimmed_vocals)
    elif mode == TrimMode.continuous_instrumental:
        # Take the instrumental track and make the duration fit the vocal duration, but don't cut in the middle
        trimmed_instrumental = instrumental_segment[0:trimmed_vocals.duration_seconds * 1000.0]
        if instrumental_gain != 0:
                trimmed_instrumental += instrumental_gain
        output_segment = trimmed_instrumental.overlay(trimmed_vocals)
    elif mode == TrimMode.vocal_only:
        # Discard instrumental track
        output_segment = trimmed_vocals
    elif mode == TrimMode.substitute_instrumental:
        if substitute_instrumental is not None:
            replacement_segment = AudioSegment.from_file(substitute_instrumental)
            trimmed_replacement = replacement_segment[0:trimmed_vocals.duration_seconds * 1000.0]
            if instrumental_gain != 0:
                trimmed_replacement += instrumental_gain
            output_segment = trimmed_replacement.overlay(trimmed_vocals)
        else: # Fall back to only vocals
            output_segment = trimmed_vocals

    # Export cut vocal segments
    output_track = get_temp_filename('opus')
    output_segment.export(output_track, format="opus", bitrate="320k")
    print('Trimmed video duration: {}'.format(format_time_ms(output_segment.duration_seconds * 1000.0)))

    # Return the timestamps for use in video cutting, and return the name of the newly cut audio segment
    return adjusted_timestamps, output_track, trimmed_vocals.duration_seconds

# Concatenate or cut segments from the video and render to a temporary file. On success, the name of the temp file is returned.
def segment_video(input_filename : str, video_filter_graph : str, start : datetime.timedelta, duration : datetime.timedelta, full_video : bool):
    """
    Concatenate or cut segments from the video and render to a temporary file. On success, the name of the temp file is returned.

    video_filter_graph - The built filter graph to apply from build_cut_segments()
    """
    ffmpeg_args = ['ffmpeg', '-hide_banner', '-y']
    if not full_video: # Skip to specified segments if applicable
        ffmpeg_args.extend(['-ss', str(start), '-t', str(duration)])
    # Input file to process
    ffmpeg_args.extend(['-i', input_filename])
    # Build the filter arguments
    ffmpeg_args.extend(['-filter_complex', video_filter_graph, '-map', '[outv]', '-an'])

    # Encoder. This is used to generate a temporary file.
    ffmpeg_args.extend(["-c:v", "libx265", "-x265-params", "lossless=1"])
    
    # Output file
    output_filename = get_temp_filename('mkv')
    ffmpeg_args.append(output_filename)
    print('Rendering cut video (please be patient)...')
    #print(' '.join(ffmpeg_args))
    result = subprocess.run(ffmpeg_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode != 0:
        print(' '.join(ffmpeg_args))
        print(result.stderr)
        raise RuntimeError('ffmpeg returned code {}'.format(result.returncode))
    if os.path.isfile(output_filename):
        return output_filename
    else:
        raise RuntimeError("File '{}' not found".format(output_filename))

def combine_audio_and_video(video_input :str, audio_input : str, audio_bitrate=320):
    print('Combining audio and video...')
    ffmpeg_cmd = ["ffmpeg", '-hide_banner', '-i', video_input, '-i', audio_input, '-c:v', 'copy', '-c:a', 'libopus']
    ffmpeg_cmd.extend(['-b:a', '{}k'.format(audio_bitrate)])
    output_filename = get_temp_filename('mkv')
    ffmpeg_cmd.append(output_filename)
    result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.isfile(output_filename):
        print(' '.join(ffmpeg_cmd))
        print(result.stderr)
        raise RuntimeError('Error rendering video. ffmpeg return code: {}'.format(result.returncode))
    return output_filename

def clip_audio(input_filename : str, start : datetime.timedelta, duration : datetime.timedelta, audio_bitrate=320):
    print('Rendering intermediate audio segment...')
    ffmpeg_cmd = ['ffmpeg', '-hide_banner', '-y', '-ss', str(start), '-t', str(duration), '-i', input_filename]
    ffmpeg_cmd.extend([ '-vn', '-c:a', 'libopus', '-b:a', '{}k'.format(audio_bitrate)])
    output_filename = get_temp_filename('opus')
    ffmpeg_cmd.append(output_filename)
    result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.isfile(output_filename):
        print(' '.join(ffmpeg_cmd))
        print(result.stderr)
        raise RuntimeError('Error rendering audio. ffmpeg return code: {}'.format(result.returncode))
    return output_filename

def vocal_silence_trim(input_filename : str, start : datetime.timedelta, duration : datetime.timedelta, full_video : bool, trim_mode = TrimMode.all, substitute_instrumental=None, instrumental_gain=0):
    """
    Trims silence on only the vocal track using UVR. Returns the name of the trimmed video and a list of generated temp files.

    input_filename - input video to process
    start - start time of the clip
    duration - duration of the clip
    full_video - True if we should use the entire video and skip the intermediate clip process
    trim_mode - The trimming mode to use
    substitute_instrumental - Instrumental track to use if the trim mode is substitute_instrumental
    instrumental_gain - The gain to apply to the instrumental track in dB
    """
    temp_files = []
    clipped_audio = input_filename
    if not full_video:
        clipped_audio = clip_audio(input_filename, start, duration)
        temp_files.append(clipped_audio)

    print('Running UVR inference...')
    vocal_track, instrumental_track = uvr_separate(clipped_audio)
    temp_files.extend([vocal_track, instrumental_track])
    cut_segments, audio_filename, audio_duration = silence_trim(vocal_track, instrumental_track, mode=trim_mode, substitute_instrumental=substitute_instrumental, instrumental_gain=instrumental_gain)
    temp_files.append(audio_filename)
    filter_graph = build_cut_segments(audio_duration, cut_segments)
    video_filename = segment_video(input_filename, filter_graph, start, duration, full_video)
    temp_files.append(video_filename)
    recombined_video = combine_audio_and_video(video_filename, audio_filename)

    return recombined_video, temp_files

if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(
            prog='Vocal Silence Detect',
            description='Uses Ultimate Vocal Remover to isolate vocals, then splits on silence using the vocal track.')
        parser.add_argument('-i', '--input', type=str, help='Input file to process')
        parser.add_argument('-s', '--start', type=str, default='0.0', help='Start timestamp, i.e. 0:30:5.125')
        parser.add_argument('-e', '--end', type=str, help='End timestamp, i.e. 0:35:0.000')
        parser.add_argument('-d', '--duration', type=str, help='Clip duration, use instead of --end')
        parser.add_argument('--trim_mode', type=TrimMode, default='continuous_instrumental', choices=list(TrimMode), help='Trim mode. Default = all')
        parser.add_argument('--substitute_instrumental', type=str, help='Path to the instrumental track to substitute')
        parser.add_argument('--instrumental_gain', type=int, default=0, help='Amount of gain to apply to the instrumental track')
        parser.add_argument('-k', '--keep_temp_files', action='store_true', help="Keep temporary files generated during size calculation etc.")
        args, unknown_args = parser.parse_known_args()
        if help in args:
            parser.print_help()
        for arg in unknown_args:
            if os.path.isfile(arg) and mimetypes.guess_type(arg)[0].split('/')[0] == 'video':
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
        # Process input video
        processed, temp_files = vocal_silence_trim(args.input, start_time, duration, full_video, args.trim_mode, args.substitute_instrumental, args.instrumental_gain)
        output_filename = get_output_filename(args.input, 'mkv')
        shutil.move(processed, output_filename)
        # Cleanup temp files
        if not args.keep_temp_files:
            for filename in temp_files:
                if os.path.isfile(filename):
                    os.remove(filename)
        print('Output: {}'.format(output_filename))
    except argparse.ArgumentError as e:
        print(e)
    except ValueError as e:
        print(e)
    except Exception:
        print(traceback.format_exc())