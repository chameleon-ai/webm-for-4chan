# Common utilities used by other modules.
# This is all strictly standard library stuff.

import datetime
import os
import subprocess
import time

# Format a timedelta into hh:mm:ss.ms
def format_timedelta(ts : datetime.timedelta):
    hours, rm_hr = divmod(ts.total_seconds(), 3600)
    mins, rm_min = divmod(rm_hr, 60)
    sec, rm_sec = divmod(rm_min, 1)
    # Omit leading zero hours and minutes
    if int(hours) == 0:
        if int(mins) == 0:
            ts_str = '{:02}.{:03}'.format(int(sec), int(rm_sec*1000))
        else:
            ts_str = '{:02}:{:02}.{:03}'.format(int(mins), int(sec), int(rm_sec*1000))
    else:
        ts_str = '{:02}:{:02}:{:02}.{:03}'.format(int(hours), int(mins), int(sec), int(rm_sec*1000))
    return ts_str

def get_output_filename(input_filename : str, ext_override : str = None):
    """
    Find a filename named after the input, in the same directory as the input, prepended with \_1\_ or \_2\_ etc.

    input_filename - The file to use as reference.
    ext_override - If this is not None, this will be the new extension, else the extension is named after the input
    """
    output, suffix = os.path.splitext(input_filename)
    if ext_override is not None:
        suffix = '.' + ext_override
    filename_count = 1
    while True:
        # Rename the output by prepending '_1_' to the start of the file name.
        # The dirname shenanigans are an attempt to differentiate a file in a subdirectory vs a filename unqualified in the current directory.
        final_output = os.path.dirname(output) + (os.path.sep if os.path.dirname(output) != "" else "") + '_{}_'.format(filename_count) + os.path.basename(output) + suffix
        if os.path.isfile(final_output):
            filename_count += 1 # Try to deconflict the file name by finding a different file name
        else:
            return final_output

def get_temp_filename(extension : str):
    """Find a filename with a given extension"""
    basename = 'temp'
    filename = '{}.{}'.format(basename,extension)
    x = 0
    while os.path.isfile(filename):
        x += 1
        filename = '{}.{}.{}'.format(basename,x,extension)
    return filename

def get_video_duration(input_filename : str, start_time = 0.0):
    """
    Uses ffprobe to find out how long the input is.

    input_filename - The file to analyze
    start_time - This relative start time will be subtracted from the total duration if specified
    """
    # https://superuser.com/questions/650291/how-to-get-video-duration-in-seconds
    result = subprocess.run(["ffprobe","-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_filename], stdout=subprocess.PIPE, text=True)
    duration_seconds = float(result.stdout)
    return datetime.timedelta(seconds=duration_seconds - start_time)

def parsetime(ts_input : str):
    """
    Rudamentary timestamp parsing, the format is H:M:S.ms and hours/minutes/milliseconds can be omitted

    ts_input - A string representation of the timestamp, i.e. '30' or '1:23.456' or '22.2' etc.
    """
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