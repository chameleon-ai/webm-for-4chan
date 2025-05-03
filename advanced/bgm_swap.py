# standard library
import argparse
import mimetypes
import os
import shutil
import subprocess
import traceback

# packages
from pydub import AudioSegment

# Local modules
from .common_utils import get_temp_filename, get_output_filename
from .uvr_cli import uvr_separate

def remove_audio_from_video(video_input : str):
    # Create a temp file that has no sound
    video_no_audio = get_temp_filename(os.path.splitext(video_input)[-1].replace('.',''))
    ffmpeg_cmd1 = ["ffmpeg", '-hide_banner', '-i', video_input, '-c:v', 'copy', '-an', video_no_audio]
    result = subprocess.run(ffmpeg_cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.isfile(video_no_audio):
        print(' '.join(ffmpeg_cmd1))
        print(result.stderr)
        raise RuntimeError('Error rendering temp video. ffmpeg return code: {}'.format(result.returncode))
    return video_no_audio

def combine_audio_and_video(video_input :str, audio_input : str, audio_bitrate = 128):
    category, mimetype = mimetypes.guess_type(video_input)[0].split('/')
    ffmpeg_cmd = ["ffmpeg", '-hide_banner', '-i', video_input, '-i', audio_input, '-c:v', 'copy', '-c:a']
    # Determine which type of audio to use for recombine
    if mimetype == 'mp4':
        print('Using mp4/aac')
        ffmpeg_cmd.append('aac')
    elif mimetype == 'webm':
        print('Using webm/opus')
        ffmpeg_cmd.append('libopus')
    elif mimetype == 'x-matroska':
        print('Using mkv/opus')
        ffmpeg_cmd.append('libopus')
    else:
        raise RuntimeError('Unsupported mime type {}/{}'.format(category, mimetype))
    ffmpeg_cmd.extend(['-b:a', '{}k'.format(audio_bitrate)])
    output_filename = get_temp_filename(os.path.splitext(video_input)[-1].replace('.',''))
    ffmpeg_cmd.append(output_filename)
    result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.isfile(output_filename):
        print(' '.join(ffmpeg_cmd))
        print(result.stderr)
        raise RuntimeError('Error rendering video. ffmpeg return code: {}'.format(result.returncode))
    return output_filename

def bgm_swap(input_video : str, input_bgm : str, bgm_gain = 0, audio_bitrate = 128):
    """
    Runs UVR and swaps the instrumental track for the one provided. The duration is limited to the input video.

    input_video - filename of the video to process
    input_bgm - the instrumental track to substitute
    bgm_gain - adjusts the volume in dB of the instrumental track
    audio_bitrate - the output audio bitrate in kbps
    """
    temp_files = []
    print('Running UVR inference...')
    vocal_track, instrumental_track = uvr_separate(input_video)
    temp_files.extend([vocal_track, instrumental_track])
    vocal_segment = AudioSegment.from_file(vocal_track)
    bgm_segment = AudioSegment.from_file(input_bgm)

    print('Combining vocals with new bgm...')
    # Make bgm match vocal duration
    diff_ms = int((vocal_segment.duration_seconds - bgm_segment.duration_seconds) * 1000)
    if diff_ms > 0: # Vocal segment is longer, add silence to bgm at end
        bgm_segment += AudioSegment.silent(duration=diff_ms)
    elif diff_ms < 0: # Vocal segment is shorter, truncate
        bgm_segment = bgm_segment[:int(vocal_segment.duration_seconds * 1000)]
    
    # Overlay vocals with new bgm
    output_segment = AudioSegment.empty()
    if bgm_gain != 0:
        bgm_segment += bgm_gain
    output_segment = bgm_segment.overlay(vocal_segment)
    output_track = get_temp_filename('opus')
    output_segment.export(output_track, format="opus", bitrate="{}k".format(audio_bitrate))
    temp_files.append(output_track)

    print('Recombining audio and video...')
    video_no_audio = remove_audio_from_video(input_video)
    temp_files.append(video_no_audio)
    output_video = combine_audio_and_video(video_no_audio, output_track, audio_bitrate)
    output_filename = get_output_filename(input_video)
    shutil.move(output_video, output_filename)
    return output_filename, temp_files

if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(
            prog='Vocal Silence Detect',
            description='Uses Ultimate Vocal Remover to isolate vocals, then splits on silence using the vocal track.')
        parser.add_argument('-i', '--input', type=str, help='Input video to process')
        parser.add_argument('-b', '--bgm', type=str, help='Input instrumental track to substitute')
        parser.add_argument('--gain', type=int, default=0, help='Amount of gain to apply to the instrumental track')
        parser.add_argument('--audio_rate', type=int, default=128, help='Output audio bitrate in kbps')
        parser.add_argument('-k', '--keep_temp_files', action='store_true', help="Keep temporary files generated during size calculation etc.")
        args, unknown_args = parser.parse_known_args()
        if help in args:
            parser.print_help()
        for arg in unknown_args:
            if os.path.isfile(arg) and mimetypes.guess_type(arg)[0].split('/')[0] == 'video':
                args.input = arg
            elif os.path.isfile(arg) and mimetypes.guess_type(arg)[0].split('/')[0] == 'audio':
                args.bgm = arg
        output, temp_files = bgm_swap(args.input, args.bgm, args.gain)
        # Cleanup temp files
        if not args.keep_temp_files:
            for filename in temp_files:
                if os.path.isfile(filename):
                    os.remove(filename)
        print('Output: {}'.format(output))
    except argparse.ArgumentError as e:
        print(e)
    except ValueError as e:
        print(e)
    except Exception:
        print(traceback.format_exc())