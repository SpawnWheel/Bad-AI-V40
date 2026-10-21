import os
import random
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

def parse_speaker(filename):
    """
    Parses speaker name from filename, e.g. '000047_Mike_Joy.mp3' -> 'Mike_Joy'.
    Also handles suffixes like '000047_Mike_Joy_1.mp3' -> 'Mike_Joy'.
    Returns None if no speaker name pattern is found.
    """
    name_without_ext = os.path.splitext(filename)[0]
    match = re.match(r'^\d{6}_(.+)$', name_without_ext)
    if match:
        raw_speaker = match.group(1).strip()
        clean_speaker = re.sub(r'_\d+$', '', raw_speaker)
        return clean_speaker
    return None

def detect_speech_offset(audio, silence_threshold, chunk_size=10, min_sustained_ms=60, lookahead_ms=250):
    """
    Finds the millisecond offset where sustained speech begins, ignoring
    isolated brief TTS clicks/pops (< min_sustained_ms) followed by silence.
    """
    length = len(audio)
    if length == 0 or audio.dBFS == float('-inf'):
        return 0

    for t in range(0, length, chunk_size):
        if audio[t:t + chunk_size].dBFS >= silence_threshold:
            window_end = min(length, t + lookahead_ms)
            active_ms = sum(
                chunk_size for k in range(t, window_end, chunk_size)
                if audio[k:k + chunk_size].dBFS >= silence_threshold
            )
            if active_ms >= min_sustained_ms:
                return t
    return 0


def trim_clip_silence(
    audio,
    silence_threshold=-50.0,
    chunk_size=10,
    leading_buffer_ms=100,
    trailing_buffer_ms=150,
):
    """
    Trims leading and trailing dead air down to spoken audio using an adaptive
    clip-relative silence threshold and generous leading/trailing buffers so
    soft opening and closing words are never truncated.
    """
    if len(audio) == 0 or audio.dBFS == float('-inf'):
        return audio

    effective_threshold = max(
        -65.0,
        min(silence_threshold, audio.dBFS - 16.0, audio.max_dBFS - 32.0)
    )

    leading = detect_speech_offset(audio, effective_threshold, chunk_size)
    trailing = detect_speech_offset(audio.reverse(), effective_threshold, chunk_size)

    start_ms = max(0, leading - leading_buffer_ms)
    end_ms = min(len(audio), max(start_ms + 100, len(audio) - max(0, trailing - trailing_buffer_ms)))

    trimmed_lead = start_ms
    trimmed_trail = len(audio) - end_ms
    trimmed = audio[start_ms:end_ms]

    if trimmed_lead > 0 and len(trimmed) > 30:
        trimmed = trimmed.fade_in(15)
    if trimmed_trail > 0 and len(trimmed) > 30:
        trimmed = trimmed.fade_out(15)

    if trimmed_lead > 50 or trimmed_trail > 50:
        print(
            f"  Trimming silence (thresh {effective_threshold:.1f} dBFS): "
            f"{trimmed_lead}ms leading, {trimmed_trail}ms trailing."
        )
    return trimmed


def level_clip(audio, target_dbfs=-27.0, max_peak_dbfs=-6.0, max_gain_db=12.0, min_change_db=1.5):
    """
    Pulls each clip's average loudness toward a common target so every
    commentator voice enters the mix at a similar level. Soft clips are boosted
    (limited by peak headroom so they never clip) and loud clips are attenuated.
    """
    if len(audio) == 0 or audio.dBFS == float('-inf'):
        return audio

    orig_dbfs = audio.dBFS
    if orig_dbfs < target_dbfs:
        headroom = max_peak_dbfs - audio.max_dBFS
        gain_db = min(target_dbfs - orig_dbfs, headroom, max_gain_db)
    else:
        gain_db = max(target_dbfs - orig_dbfs, -max_gain_db)

    if abs(gain_db) >= min_change_db:
        audio = audio.apply_gain(gain_db)
        action = "Boosting soft" if gain_db > 0 else "Attenuating loud"
        print(
            f"  {action} clip by {gain_db:+.1f}dB "
            f"({orig_dbfs:.1f} dBFS -> {audio.dBFS:.1f} dBFS, peak {audio.max_dBFS:.1f} dBFS)."
        )
    return audio


# Bus compressor + limiter applied (via ffmpeg) to the stitched timeline so the
# different commentators' voices sit at a consistent level in the final mix.
COMPRESSOR_FILTER = (
    "acompressor=threshold=-30dB:ratio=3:attack=5:release=150:knee=4:makeup=6dB,"
    "alimiter=limit=-1dB:attack=5:release=50:level=disabled"
)


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
        
        # Trim leading and trailing silence to clean speech boundaries
        audio = trim_clip_silence(audio)
        # Level each clip toward a common loudness so speakers sit consistently on the timeline
        audio = level_clip(audio)
        
        scheduled_start_sec = parse_timecode(filename)
        scheduled_start_ms = scheduled_start_sec * 1000
        
        timeline_items.append({
            'filename': filename,
            'audio': audio,
            'scheduled_start': scheduled_start_ms,
            'duration': len(audio),
            'actual_start': scheduled_start_ms
        })

    # Timeline layout constants
    MIN_GAP_MS = 10           # Minimum natural gap between clips (10ms)
    MAX_GAP_MS = 800          # Maximum natural gap between clips (800ms)
    MAX_SHIFT_MS = 4000       # Maximum allowed delay (4s) from scheduled timecode
    MAX_OVERLAP_MS = 800      # Maximum allowed overlap between different speakers (up to 800ms)
    FADE_OUT_MS = 100         # Fade-out duration when truncating a clip
    MIN_CLIP_DURATION = 1500  # Minimum duration a clip must play if truncated

    print("Applying timeline logic (shifts, overlaps, and truncations)...")
    for i in range(len(timeline_items)):
        item = timeline_items[i]
        
        if i == 0:
            item['actual_start'] = item['scheduled_start']
            continue

        prev_item = timeline_items[i-1]
        prev_end = prev_item['actual_start'] + prev_item['duration']
        
        # Natural gap between sequential clips
        gap_ms = random.randint(MIN_GAP_MS, MAX_GAP_MS)
        natural_start = prev_end + gap_ms
        
        if item['scheduled_start'] == prev_item['scheduled_start']:
            # Same timecode: play sequentially after previous clip
            item['actual_start'] = max(item['scheduled_start'], natural_start)
        else:
            # Different timecodes: apply 4-second maximum delay limit
            max_allowed_start = item['scheduled_start'] + MAX_SHIFT_MS
            
            if natural_start <= max_allowed_start:
                # Natural start is within allowed 4s window - no overlap or truncation needed
                item['actual_start'] = max(item['scheduled_start'], natural_start)
            elif prev_end <= max_allowed_start:
                # Previous clip has already finished playing before the 4s deadline;
                # only the random gap was pushing it past. Start immediately after prev_end.
                item['actual_start'] = max(item['scheduled_start'], prev_end)
            else:
                # Previous clip is still playing at the 4s deadline (prev_end > max_allowed_start).
                # Check if different speakers can overlap.
                prev_speaker = parse_speaker(prev_item['filename'])
                curr_speaker = parse_speaker(item['filename'])
                different_speakers = (
                    prev_speaker is not None and 
                    curr_speaker is not None and 
                    prev_speaker.lower() != curr_speaker.lower()
                )
                
                if different_speakers:
                    # Allow overlap up to 800ms, ensuring previous clip has played for at least 200ms
                    allowed_overlap = min(MAX_OVERLAP_MS, max(0, prev_item['duration'] - 200))
                else:
                    # Same speakers can never overlap
                    allowed_overlap = 0
                
                overhang = prev_end - max_allowed_start
                
                if overhang <= allowed_overlap:
                    # Overlap alone is enough! Previous clip plays in full, overlapping next clip.
                    item['actual_start'] = max_allowed_start
                else:
                    # Overlap alone isn't enough (or no overlap allowed).
                    # Truncate previous clip to end at max_allowed_start + allowed_overlap.
                    target_prev_end = max_allowed_start + allowed_overlap
                    target_prev_duration = target_prev_end - prev_item['actual_start']
                    min_prev_dur = min(prev_item['duration'], MIN_CLIP_DURATION)
                    
                    if target_prev_duration >= min_prev_dur:
                        # Safe to truncate to target duration
                        truncate_ms = prev_item['duration'] - target_prev_duration
                        fade_len = min(FADE_OUT_MS, target_prev_duration)
                        if fade_len > 0:
                            prev_item['audio'] = prev_item['audio'][:target_prev_duration].fade_out(fade_len)
                        else:
                            prev_item['audio'] = prev_item['audio'][:target_prev_duration]
                        prev_item['duration'] = len(prev_item['audio'])
                        print(f"  Truncating {prev_item['filename']} by {truncate_ms}ms to fit (new duration: {prev_item['duration']}ms).")
                        item['actual_start'] = max_allowed_start
                    else:
                        # Truncating to target would fall below MIN_CLIP_DURATION.
                        # Preserve minimum clip duration.
                        if prev_item['duration'] > min_prev_dur:
                            truncate_ms = prev_item['duration'] - min_prev_dur
                            fade_len = min(FADE_OUT_MS, min_prev_dur)
                            if fade_len > 0:
                                prev_item['audio'] = prev_item['audio'][:min_prev_dur].fade_out(fade_len)
                            else:
                                prev_item['audio'] = prev_item['audio'][:min_prev_dur]
                            prev_item['duration'] = len(prev_item['audio'])
                            print(f"  Truncating {prev_item['filename']} by {truncate_ms}ms to minimum duration ({min_prev_dur}ms).")
                        
                        # Place item with allowed overlap after the preserved minimum duration
                        if allowed_overlap > 0:
                            item['actual_start'] = max(
                                item['scheduled_start'],
                                prev_item['actual_start'] + prev_item['duration'] - allowed_overlap
                            )
                        else:
                            item['actual_start'] = max(
                                item['scheduled_start'],
                                prev_item['actual_start'] + prev_item['duration'] + MIN_GAP_MS
                            )

        effective_prev_end = prev_item['actual_start'] + prev_item['duration']
        if item['actual_start'] < effective_prev_end:
            overlap_ms = effective_prev_end - item['actual_start']
            print(f"  Overlapped {item['filename']} with previous speaker by {overlap_ms}ms")

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
    
    print(f"Exporting to {output_file} (applying compressor)...")
    combined.export(output_file, format="wav", parameters=["-af", COMPRESSOR_FILTER])
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
