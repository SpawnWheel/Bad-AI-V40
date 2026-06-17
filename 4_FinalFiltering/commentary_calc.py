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

    output_lines = []
    default_last_duration = 5.0  # Seconds to assume for the final event

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
        
        word_count = min(int(round(duration * 2.7)), 80)
        
        # formatting message
        new_line = f"{current_item['original_text']} Commentate in {word_count} words"
        output_lines.append(new_line)

    # Construct output filename
    base_name, ext = os.path.splitext(os.path.basename(file_path))
    
    project_path = os.environ.get("R3E_PROJECT_PATH")
    if project_path:
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
