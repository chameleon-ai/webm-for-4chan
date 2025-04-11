import argparse
import copy
import datetime
from enum import Enum
import json
import os
import re
import subprocess
import traceback

from .common_utils import format_timedelta, get_output_filename, get_temp_filename, get_video_duration, parsetime

#import whisperx
import whisper_timestamped as whisper
from whisper_timestamped.make_subtitles import write_srt, write_vtt

class MatchMode(Enum):
    segment = 'segment' # Get the timestamp range of the full segment
    word = 'word' # Get the timestamps of only the matched pattern or word
    def __str__(self):
        return self.value

class TranscriptType(Enum):
    json = 'json' # Native transcript returned by transcribe
    vtt = 'vtt'
    srt = 'srt'
    def __str__(self):
        return self.value

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

def build_filter_graph(segments_to_keep):
    video_filter_graph = ''
    audio_filter_graph = ''
    # Build a filter graph on the kept segments
    # Nice reference for how to build a filter graph: https://github.com/sriramcu/ffmpeg_video_editing
    for index, segment in enumerate(segments_to_keep, start=1):
        segment_start, segment_end = segment
        # [0]trim=start=34.5:end=55.1,setpts=PTS-STARTPTS[v1];
        video_filter_graph += '[0]trim=start={}:end={},setpts=PTS-STARTPTS[v{}];'.format(segment_start, segment_end, index)    
        audio_filter_graph += '[0]atrim=start={}:end={},asetpts=PTS-STARTPTS[a{}];'.format(segment_start, segment_end, index)  
        #print('{} {}-{}'.format(index, segment_start, segment_end))
    for index, segment in enumerate(segments_to_keep, start=1):
        # [v1][v2][v3]concat=n=3:v=1:a=0[outv]
        video_filter_graph += '[v{}]'.format(index)
    video_filter_graph += 'concat=n={}:v=1:a=0[outv]'.format(len(segments_to_keep))
    for index, segment in enumerate(segments_to_keep, start=1):
        audio_filter_graph += '[a{}]'.format(index)
    audio_filter_graph += 'concat=n={}:v=0:a=1[outa]'.format(len(segments_to_keep))
    return video_filter_graph, audio_filter_graph

def segment_video(input_filename : str, video_filter_graph : str, audio_filter_graph : str, start : datetime.timedelta, duration : datetime.timedelta, full_video : bool):
    ffmpeg_args = ['ffmpeg', '-hide_banner', '-y']
    if not full_video: # Skip to specified segments if applicable
        ffmpeg_args.extend(['-ss', str(start), '-t', str(duration)])
    # Input file to process
    ffmpeg_args.extend(['-i', input_filename])
    # Build the filter arguments
    ffmpeg_args.extend(['-filter_complex', video_filter_graph + ';' + audio_filter_graph, '-map', '[outv]', '-map', '[outa]'])

    # Encoder. This is used to generate a temporary file.
    ffmpeg_args.extend(["-c:v", "libx265", "-x265-params", "lossless=1"])
    ffmpeg_args.extend(["-c:a", "libopus", "-b:a", "320k"])
    
    # Output file
    output_filename = get_output_filename(input_filename, 'mkv')
    ffmpeg_args.append(output_filename)
    print('Rendering cut video (please be patient)...')
    print(' '.join(ffmpeg_args))
    result = subprocess.run(ffmpeg_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode != 0:
        print(' '.join(ffmpeg_args))
        print(result.stderr)
        raise RuntimeError('ffmpeg returned code {}'.format(result.returncode))
    if os.path.isfile(output_filename):
        return output_filename
    else:
        raise RuntimeError("File '{}' not found".format(output_filename))

def render_segments(input_filename: str, timestamps : list, start : datetime.timedelta, duration : datetime.timedelta, full_video : bool, adjust_pre_ms = 100, adjust_post_ms = 300):
    adjusted_timestamps = [ [t[0], t[1]] for t in timestamps]
    if adjust_pre_ms > 0 or adjust_post_ms > 0: # Add time to the timestamps
        pre_sec = adjust_pre_ms / 1000.0 # Amount of time to adjust the start timestamp back
        post_sec = adjust_post_ms / 1000.0 # Amount of time to adjust the end timestamp forward
        end = start.total_seconds() + duration.total_seconds()  # Cap at the duration limit
        for idx,(start_ts, end_ts) in enumerate(timestamps):
            prev_end = 0.0 if idx < 1 else timestamps[idx-1][1]
            # Clamp to prev segment end and next segment start
            adjusted_timestamps[idx][0] = prev_end if start_ts - pre_sec < prev_end else start_ts - pre_sec
            next_start = end if idx + 1 >= len(timestamps) else timestamps[idx+1][0]
            adjusted_timestamps[idx][1] = next_start if end_ts + post_sec > next_start else end_ts + post_sec
            #print('{} {}'.format(timestamps[idx], adjusted_timestamps[idx]))
    
    # Make a new list with merged timestamps that overlap
    merged = [(adjusted_timestamps[0][0], adjusted_timestamps[0][1])] # Initialize the merged list with the first timestamp

    for current_start, current_end in adjusted_timestamps[1:]:
        last_start, last_end = merged[-1]
        if current_start <= last_end: # Check if there is an overlap
            # Merge the intervals by updating the end time
            merged[-1] = (last_start, max(last_end, current_end))
            #print("merging {} {} {}".format(last_start, last_end, current_end))
        else:
            # No overlap, just add the current timestamp
            merged.append((current_start, current_end))

    video_filter_graph, audio_filter_graph = build_filter_graph(merged)
    return segment_video(input_filename, video_filter_graph, audio_filter_graph, start, duration, full_video)

def transcribe(input_filename : str, model='large-v3-turbo', device='cuda', language='auto', initial_prompt=None, condition_on_previous_text=False, vad=True, naive_approach=True):
    print('Loading whisper...')
    loaded_model = whisper.load_model(model, device=device, download_root="./models/")
    audio = whisper.load_audio(input_filename)
    print('Transcribing...')
    result = whisper.transcribe_timestamped(loaded_model, audio, naive_approach=naive_approach, initial_prompt=initial_prompt, vad='auditok' if vad else False, beam_size=5, best_of=5, temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0), language=None if language == 'auto' else language, condition_on_previous_text=condition_on_previous_text)
    return result

async def translate_bulk(segments : list, language : str):
    from googletrans import Translator
    async with Translator() as translator:
        translations = await translator.translate(segments, dest='en', src=language)
        return [t.text for t in translations]

def translate(transcript, language='auto'):
    import asyncio
    print('Translating...')
    segments = [seg['text'] for seg in transcript['segments']]
    loop = asyncio.get_event_loop()
    translations = loop.run_until_complete(translate_bulk(segments, language))
    translated_transcript = copy.deepcopy(transcript)
    if len(translations) != len(translated_transcript['segments']):
        print('Num translated segments {} does not match original transcript segments {}'.format(len(translations), len(translated_transcript['segments'])))
    for idx, translation in enumerate(translations):
        translated_transcript['segments'][idx]['text'] = translation
    translated_transcript['language'] = 'en'
    return translated_transcript

def save_transcript(transcript, video_filename : str, transcript_type = TranscriptType.json, start = datetime.timedelta(milliseconds=0), duration : datetime.timedelta = None, room_ms = 250):
    """
    Saves the transcript to file. Returns the transcript file name.

    transcript - The transcript to process
    video_filename - The filename to use as a basis for naming the transcript, i.e. if it is 'input.mp4', the output could be 'input.en.srt'
    transcript_type - The type of transcripr to save
    start - The relative start time of the input video. Specify this if you operated on a slice of the audio that didn't start at 0
    duration - The total duration, where the absolute end point is start + duration. Specify this if you need an upper bound to the timestamps.
    room_ms - The amount of miliseconds on either end of a segment to adjust the timestamps.
    """
    edited_transcript = copy.deepcopy(transcript) # Don't want to edit the original transcript
    if room_ms > 0: # Add time to the timestamps
        room_sec = room_ms / 1000.0
        end = edited_transcript['segments'][-1]['end']
        if duration is not None: # Cap at the duration limit if specified
            end = start.total_seconds() + duration.total_seconds()
        for idx,seg in enumerate(edited_transcript['segments']):
            prev_end = 0.0 if idx < 1 else edited_transcript['segments'][idx-1]['end']
            # Clamp to prev segment end and next segment start
            seg['start'] = prev_end if seg['start'] - room_sec < prev_end else seg['start'] - room_sec
            next_start = end if idx + 1 >= len(edited_transcript['segments']) else edited_transcript['segments'][idx+1]['start']
            seg['end'] = next_start if seg['end'] + room_sec > next_start else seg['end'] + room_sec
    
    if start.total_seconds() > 0.0: # Adjust time relative to start to correct for slicing
        for seg in edited_transcript['segments']:
            #print('Subtitle Adjusting start time by {}'.format(start.total_seconds()))
            seg['start'] += start.total_seconds()
            seg['end'] += start.total_seconds()

    output = os.path.splitext(video_filename)[0]
    transcript_filename = 'transcript'
    filename_count = 0
    while True:
        # Name the output after the input and appended with the language info. Add numbers if there is a conflict.
        base_path = os.path.dirname(output) + (os.path.sep if os.path.dirname(output) != "" else "") + os.path.basename(output)
        transcript_filename = '{}.{}'.format(base_path, edited_transcript['language'])
        if filename_count > 0:
            transcript_filename += '-{}'.format(filename_count)
        transcript_filename += '.' + str(transcript_type)
        if os.path.isfile(transcript_filename):
            filename_count += 1 # Try to deconflict the file name by finding a different file name
        else:
            break
    with open(transcript_filename, 'w', encoding='utf-8') as fout:
        if transcript_type == TranscriptType.json:
            json.dump(edited_transcript, fout, ensure_ascii=False, indent=2)
        elif transcript_type == TranscriptType.srt:
            write_srt(edited_transcript['segments'], fout)
        elif transcript_type == TranscriptType.vtt:
            write_vtt(edited_transcript['segments'], fout)
    return transcript_filename

def search_transcript(transcript, pattern : str, match_mode = MatchMode.word):
    """
    Finds the match pattern in the transcript. Returns a list of datetime.timedeltas in the form of (start, end).
    """
    match = re.search(pattern, transcript['text'], re.IGNORECASE)
    timestamps = []
    if match is None:
        print('Search pattern not found in transcript.')
    else:
        for segment in transcript['segments']:
            if re.search(pattern, segment['text'], re.IGNORECASE):
                segment_start = datetime.timedelta(seconds=float(segment['start']))
                segment_end = datetime.timedelta(seconds=float(segment['end']))
                if match_mode == MatchMode.segment: # Retrieve timestamps of whole segment
                    timestamps.append((float(segment['start']), float(segment['end'])))
                    print('[{}-{}]: "{}"'.format(format_timedelta(segment_start), format_timedelta(segment_end), segment['text']))
                elif match_mode == MatchMode.word:
                    for word in segment['words']: # Retrieve timestamps of each matching word
                        if re.search(pattern, word['text'], re.IGNORECASE):
                            word_start = datetime.timedelta(seconds=float(word['start']))
                            word_end = datetime.timedelta(seconds=float(word['end']))
                            timestamps.append((float(word['start']), float(word['end'])))
                            print('[{}-{}]: "{}"'.format(format_timedelta(word_start), format_timedelta(word_end), word['text']))
                            found = True
                    if not found: # Fallback to full segment if nothing was found at the word level
                        timestamps.append((float(segment['start']), float(segment['end'])))
                        print('[{}-{}]: "{}"'.format(format_timedelta(segment_start), format_timedelta(segment_end), segment['text']))
    return timestamps

if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(
            prog='Whisper Search',
            description='Runs whisper to transcribe an input and searches for matching text.')
        parser.add_argument('-i', '--input', type=str, help='Input file to process')
        parser.add_argument('-s', '--start', type=str, default='0.0', help='Start timestamp, i.e. 0:30:5.125')
        parser.add_argument('-e', '--end', type=str, help='End timestamp, i.e. 0:35:0.000')
        parser.add_argument('-d', '--duration', type=str, help='Clip duration, use instead of --end')
        parser.add_argument('--find', type=str, help='String to search for in the transcript.')
        parser.add_argument('--match_mode', type=MatchMode, default='segment', choices=list(MatchMode), help='Whether to match timestamps for the whole segment or just the word.')
        parser.add_argument('--render', action='store_true', help='Render found timestamps.')
        parser.add_argument('--uvr', action='store_true', help='Cleanup vocals using UVR first pass.')
        parser.add_argument('--translate', action='store_true', help='Translate to English.')
        parser.add_argument('--condition_on_previous', action='store_true', help='Whether to provide the previous output as a prompt for the next window.')
        parser.add_argument('--load_transcript', type=str, help='Skip whisper and operate on a pre-saved transcript file.')
        parser.add_argument('--save_transcript', type=TranscriptType, default='srt', choices=list(TranscriptType), help='Save the transcript to the specified file type.')
        parser.add_argument('--prompt', type=str, help='Initial prompt to use for transcription.')
        parser.add_argument('--model', type=str, default='large-v3-turbo', help='Whisper model to use.')
        parser.add_argument('--device', type=str, default='cuda', choices=['cuda','cpu'], help='Cuda device to use.')
        parser.add_argument('--language', type=str, default='auto', choices=['auto', 'en', 'ja', 'fr'], help='Transcription language. Usually faster if you specify. Capability depends on the model.')
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
        temp_files = []
        input_filename = args.input
        if not full_video:
            input_filename = clip_audio(args.input, start_time, duration)
            temp_files.append(input_filename)
        if args.uvr:
            from .uvr_cli import uvr_separate
            vocal_track, instrumental_track = uvr_separate(input_filename)
            temp_files.extend([vocal_track, instrumental_track])
            input_filename = vocal_track
        if args.load_transcript:
            print('Loading transcript from file.')
            with open(args.load_transcript) as fin:
                transcript = json.load(fin)
        else:
            transcript = transcribe(input_filename, model=args.model, device=args.device, language=args.language, initial_prompt=args.prompt, condition_on_previous_text=args.condition_on_previous)
            if args.save_transcript:
                transcript_filename = save_transcript(transcript, args.input, args.save_transcript, start_time, duration)
                print('Transcript was saved to "{}"'.format(transcript_filename))
        if args.translate:
            transcript = translate(transcript, args.language)
            if args.save_transcript:
                transcript_filename = save_transcript(transcript, args.input, args.save_transcript, start_time, duration)
                print('Transcript was saved to "{}"'.format(transcript_filename))
        if args.find:
            ts = search_transcript(transcript, args.find, match_mode=args.match_mode)
            print('{} matches found.'.format(len(ts)))
            if args.render:
                output_filename = render_segments(args.input, ts, start_time, duration, full_video)
                print('Output: {}'.format(output_filename))
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