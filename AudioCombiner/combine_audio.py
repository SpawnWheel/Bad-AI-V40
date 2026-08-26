import os
import re
import sys
from pydub import AudioSegment
from pydub.silence import detect_leading_silence

def parse_timecode(filename):
    """
    Parses HHMMSS from filename. e.g., 000300.wav -> 180 seconds.
    """
    match = re.match(r'(\d{2})(\d{2})(\d{2})', filename)
    if match:
        hh, mm, ss = map(int, match.groups())
        return hh * 3600 + mm * 60 + ss
    return None

def trim_leading_silence_custom(audio, silence_threshold=-50.0, chunk_size=10, keep_silence_ms=1000):
    """
    Trims leading silence but keeps up to keep_silence_ms.
    """
    leading_silence = detect_leading_silence(audio, silence_threshold, chunk_size)
    
    if leading_silence > keep_silence_ms:
        print(f"  Trimming {leading_silence - keep_silence_ms}ms of silence.")
        return audio[leading_silence - keep_silence_ms:]
    else:
        return audio

def main(input_folder, output_file):
    if not os.path.exists(input_folder):
        print(f"Error: Folder '{input_folder}' not found.")
        return

    files = [f for f in os.listdir(input_folder) if f.lower().endswith(('.wav', '.mp3'))]
    # Filter files that match the expected timecode pattern
    valid_files = []
    for f in files:
        if parse_timecode(f) is not None:
            valid_files.append(f)
        else:
            print(f"Skipping {f} - does not match HHMMSS pattern.")
    
    # Sort by timecode first, then by file modification time for same-timecode files
    # This preserves the original commentary order when multiple clips share a timecode
    valid_files.sort(key=lambda f: (parse_timecode(f), os.path.getmtime(os.path.join(input_folder, f))))
    
    if not valid_files:
        print("No valid WAV or MP3 files found.")
        return

    print(f"Found {len(valid_files)} files. Processing...")
    
    timeline_items = []
    
    for filename in valid_files:
        print(f"Loading {filename}...")
        filepath = os.path.join(input_folder, filename)
        # Use from_file to support both wav and mp3
        audio = AudioSegment.from_file(filepath)
        
        # Trim leading silence
        audio = trim_leading_silence_custom(audio)
        
        scheduled_start_sec = parse_timecode(filename)
        scheduled_start_ms = scheduled_start_sec * 1000
        
        timeline_items.append({
            'filename': filename,
            'audio': audio,
            'scheduled_start': scheduled_start_ms,
            'duration': len(audio),
            'actual_start': scheduled_start_ms
        })

    # Apply shifting and truncation logic
    GAP_MS = 500              # Gap between clips that are too close together
    FADE_OUT_MS = 100         # Fade-out duration when truncating a clip
    MAX_SHIFT_MS = 5000       # Maximum allowed shift forward for a new event
    MIN_CLIP_DURATION = 1500  # Minimum duration a clip must play if truncated
    
    print("Applying timeline logic (shifts and truncations)...")
    for i in range(len(timeline_items)):
        item = timeline_items[i]
        
        if i > 0:
            prev_item = timeline_items[i-1]
            prev_end = prev_item['actual_start'] + prev_item['duration']
            earliest_natural_start = prev_end + GAP_MS
            
            if item['scheduled_start'] == prev_item['scheduled_start']:
                # Same timecode: play sequentially after previous clip with a short gap
                new_start = max(item['scheduled_start'], earliest_natural_start)
                item['actual_start'] = new_start
            else:
                # Different timecodes: apply shift limit
                max_allowed_start = item['scheduled_start'] + MAX_SHIFT_MS
                
                if earliest_natural_start <= max_allowed_start:
                    # Previous clip finishes within acceptable shift window
                    new_start = max(item['scheduled_start'], earliest_natural_start)
                    item['actual_start'] = new_start
                else:
                    # Waiting for previous clip would delay current clip beyond MAX_SHIFT_MS.
                    # Attempt to truncate previous clip so current clip starts at max_allowed_start.
                    target_prev_end = max_allowed_start - GAP_MS
                    truncate_at = target_prev_end - prev_item['actual_start']
                    min_prev_dur = min(prev_item['duration'], MIN_CLIP_DURATION)

                    if truncate_at >= min_prev_dur:
                        # Safe to truncate previous clip
                        truncate_ms = prev_item['duration'] - truncate_at
                        print(f"  Truncating {prev_item['filename']} by {truncate_ms}ms to fit.")
                        fade_len = min(FADE_OUT_MS, truncate_at)
                        if fade_len > 0:
                            prev_item['audio'] = prev_item['audio'][:truncate_at].fade_out(fade_len)
                        else:
                            prev_item['audio'] = prev_item['audio'][:truncate_at]
                        prev_item['duration'] = len(prev_item['audio'])
                        item['actual_start'] = max_allowed_start
                    else:
                        # Truncating to max_allowed_start would make previous clip too short or negative.
                        # Preserve at least min_prev_dur (or full length if already shorter) and shift current item after it.
                        if prev_item['duration'] > min_prev_dur:
                            truncate_ms = prev_item['duration'] - min_prev_dur
                            print(f"  Truncating {prev_item['filename']} by {truncate_ms}ms to minimum duration ({min_prev_dur}ms).")
                            fade_len = min(FADE_OUT_MS, min_prev_dur)
                            if fade_len > 0:
                                prev_item['audio'] = prev_item['audio'][:min_prev_dur].fade_out(fade_len)
                            else:
                                prev_item['audio'] = prev_item['audio'][:min_prev_dur]
                            prev_item['duration'] = len(prev_item['audio'])
                        item['actual_start'] = prev_item['actual_start'] + prev_item['duration'] + GAP_MS

            if item['actual_start'] > item['scheduled_start']:
                shift = item['actual_start'] - item['scheduled_start']
                print(f"  Shifted {item['filename']} by {shift}ms")

    # Build final timeline
    print("Building final WAV...")
    
    # Determine total duration required
    last_item = timeline_items[-1]
    total_duration_ms = last_item['actual_start'] + last_item['duration']
    
    # Create silent base
    # Note: We use the properties of the first audio file for the base (sample rate, channels)
    first_audio = timeline_items[0]['audio']
    combined = AudioSegment.silent(duration=total_duration_ms, frame_rate=first_audio.frame_rate)
    combined = combined.set_channels(first_audio.channels).set_sample_width(first_audio.sample_width)
    
    for item in timeline_items:
        combined = combined.overlay(item['audio'], position=item['actual_start'])
    
    print(f"Exporting to {output_file}...")
    combined.export(output_file, format="wav")
    print("Done!")

if __name__ == "__main__":
    # Default paths
    input_dir = "SAMPLE AUDIO"
    output_name = "combined_timeline.wav"
    r3e_project_path = os.environ.get("R3E_PROJECT_PATH")
    if r3e_project_path:
        output_name = os.path.join(r3e_project_path, output_name)
    
    # If no folder is provided as a command-line argument, ask the user
    if len(sys.argv) > 1:
        input_dir = sys.argv[1]
    else:
        user_input = input(f"Enter the folder path containing WAV or MP3 files (default: {input_dir}): ").strip()
        if user_input:
            input_dir = user_input
            
    # If no output name is provided as a command-line argument, use the default
    if len(sys.argv) > 2:
        output_name = sys.argv[2]
        
    main(input_dir, output_name)
