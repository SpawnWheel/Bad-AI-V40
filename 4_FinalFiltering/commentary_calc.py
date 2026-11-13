import os
import re
from datetime import datetime

def parse_time(line):
    """Extracts timestamp from the start of the line."""
    match = re.search(r'^(\d{2}:\d{2}:\d{2})', line)
    if match:
        return datetime.strptime(match.group(1), '%H:%M:%S')
    return None

import sys

# Words per second of gap. The voices speak a little faster than this (about 2.8 words a second), which leaves
# room for the short pauses the audio combiner puts between lines. Raise it for more talk, lower it for more air.
WORDS_PER_SEC = 2.5
MAX_WORDS = 90
# A share of the gap is the right amount of air for a short gap, but on a long one it adds up: a 20 second gap
# ended on 5 to 8 seconds of silence. From about 11 seconds up, the budget instead fills the gap up to a fixed pause.
# Measured over a race: the voices speak about 2.9 words a second and the AI writes about 85% of the words asked for.
# A shorter pause fills more, but lines start running into the next event (at 2.5 the odd clip had to be cut short).
PAUSE_SEC = 3.0
VOICE_WORDS_PER_SEC = 2.9
WRITTEN_SHARE = 0.85
# The start is shouted and rushed: events in the first seconds after the green flag get more words
START_WORDS_PER_SEC = 2.8
START_SECONDS = 25
# The winner's moment: lines in the seconds after the winner crosses the line (back markers finishing, a pass for
# a minor place) are folded into the winner's line, so the booth has the whole time to celebrate the win instead of
# announcing him in one breath and reading out the cars behind. A podium finisher (P2/P3) ends the fold.
WINNER_HOLD_SEC = 20
WINNER_RE = re.compile(r'\[FINISH\].*?took P1!')
PODIUM_FINISH_RE = re.compile(r'to take P[23]\b')
RADIO_TAIL_RE = re.compile(r'\s*\(Radio\).*$')
STAMP_RE = re.compile(r'^\d{2}:\d{2}:\d{2}\s*-\s*')


def fold_winner_moment(parsed_data):
    """Folds the lines in the WINNER_HOLD_SEC after the winner's finish line into it (see WINNER_HOLD_SEC)."""
    win = next((i for i, p in enumerate(parsed_data)
                if p['timestamp'] is not None and WINNER_RE.search(p['original_text'])), None)
    if win is None:
        return parsed_data
    win_t = parsed_data[win]['timestamp']
    folded, keep = [], parsed_data[:win + 1]
    rest = parsed_data[win + 1:]
    for n, p in enumerate(rest):
        if p['timestamp'] is None:
            keep.append(p)
            continue
        if (p['timestamp'] - win_t).total_seconds() > WINNER_HOLD_SEC or PODIUM_FINISH_RE.search(p['original_text']):
            keep.extend(rest[n:])
            break
        # Radio from a minor place would talk over the winner: dropped
        folded.append(RADIO_TAIL_RE.sub('', STAMP_RE.sub('', p['original_text'])).strip())
    if folded:
        line = parsed_data[win]
        keep[win] = dict(line, original_text=f"{line['original_text']} While the winner celebrates: {' '.join(folded)}")
        print(f"Folded {len(folded)} line(s) after the winner into the winner's moment.")
    return keep


def main():
    print("--- Race Event Word Count Calculator ---")
    
    # 1. Check CLI Arg
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        print(f"Loading file from arguments: {file_path}")
    else:
        file_path = input("Enter the file name to load (e.g., events.txt): ").strip()
    
    # Remove quotes if the user pasted the path as "path/to/file"
    if file_path.startswith('"') and file_path.endswith('"'):
        file_path = file_path[1:-1]

    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' not found.")
        # Only pause if interactive (no args)
        if len(sys.argv) <= 1:
            input("Press Enter to exit...")
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    parsed_data = []
    
    # Parse lines
    for line in lines:
        timestamp = parse_time(line)
        parsed_data.append({
            'timestamp': timestamp,
            'original_text': line.strip()
        })

    parsed_data = fold_winner_moment(parsed_data)

    # The green flag: the event that says so, else the first event
    green_flag = next((p['timestamp'] for p in parsed_data
                       if p['timestamp'] is not None and 'green flag' in p['original_text'].lower()),
                      next((p['timestamp'] for p in parsed_data if p['timestamp'] is not None), None))

    output_lines = []
    default_last_duration = 15.0  # Seconds to assume for the final event

    for i in range(len(parsed_data)):
        current_item = parsed_data[i]
        timestamp = current_item['timestamp']
        
        if timestamp is None:
            # If no timestamp found, preserve the line as is
            output_lines.append(current_item['original_text'])
            continue

        duration = default_last_duration

        # Look ahead for the next valid timestamp
        for j in range(i + 1, len(parsed_data)):
            if parsed_data[j]['timestamp'] is not None:
                diff = parsed_data[j]['timestamp'] - timestamp
                # Handle cases where time might wrap around or be out of order (though unlikely in a log)
                total_seconds = diff.total_seconds()
                if total_seconds >= 0:
                    duration = total_seconds
                break

        at_start = green_flag is not None and 0 <= (timestamp - green_flag).total_seconds() < START_SECONDS
        rate = START_WORDS_PER_SEC if at_start else WORDS_PER_SEC
        words = max(duration * rate, (duration - PAUSE_SEC) * VOICE_WORDS_PER_SEC / WRITTEN_SHARE)
        word_count = max(3, min(int(round(words)), MAX_WORDS))
        
        # formatting message
        new_line = f"{current_item['original_text']} Commentate in {word_count} words"
        output_lines.append(new_line)

    # Construct output filename
    base_name, ext = os.path.splitext(os.path.basename(file_path))
    
    project_path = os.environ.get("R3E_PROJECT_PATH")
    if os.environ.get("BADAI_OUTPUT"):
        output_path = os.environ["BADAI_OUTPUT"]
    elif project_path:
        output_path = os.path.join(project_path, f"{base_name}_forCommentary{ext}")
    else:
        output_path = f"{base_name}_forCommentary{ext}"

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines))
        print(f"\nSuccess! Processed {len(lines)} lines.")
        print(f"Saved to: {output_path}")
    except Exception as e:
        print(f"Error writing output file: {e}")

    if len(sys.argv) <= 1:
        input("\nPress Enter to close...")

if __name__ == "__main__":
    main()
