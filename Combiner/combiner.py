import sys
import os
import argparse

def normalize_timecode(line):
    # Normalize timecode from H:MM:SS to HH:MM:SS
    try:
        parts = line.split(' - ', 1)
        if len(parts) < 2:
            return line
        
        time_str = parts[0].strip()
        time_parts = time_str.split(':')
        
        if len(time_parts) == 3:
            h, m, s = time_parts
            # Pad each part to ensure HH:MM:SS
            h = h.zfill(2)
            m = m.zfill(2)
            s = s.zfill(2)
            return f"{h}:{m}:{s} - {parts[1]}"
    except Exception:
        pass
    return line

def parse_time(line):
    # Extracts HH:MM:SS from the start of the line
    try:
        parts = line.split(' - ')
        if len(parts) > 0:
            return parts[0].strip()
    except Exception:
        pass
    return "00:00:00"

def combine_files(file1_path, file2_path, output_path):
    try:
        with open(file1_path, 'r', encoding='utf-8') as f1, \
             open(file2_path, 'r', encoding='utf-8') as f2:
            
            # Read, normalize and filter empty lines
            lines1 = [normalize_timecode(line) for line in f1 if line.strip()]
            lines2 = [normalize_timecode(line) for line in f2 if line.strip()]
            
            # Combine and sort based on the normalized timecode string
            combined = lines1 + lines2
            # Use stable sort to preserve order of events at the exact same time
            combined.sort(key=parse_time)
            
            with open(output_path, 'w', encoding='utf-8') as out:
                out.writelines(combined)
            
            print(f"Successfully combined files into: {output_path}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Combine two race log files by timecode.")
    parser.add_argument("file1", nargs="?", help="Path to the first file")
    parser.add_argument("file2", nargs="?", help="Path to the second file")
    
    args = parser.parse_args()
    
    path1 = args.file1
    path2 = args.file2
    
    # If paths are not provided as arguments, prompt the user
    if not path1 or not path2:
        print("--- Race Log Combiner ---")
        if not path1:
            path1 = input("Enter path for the first file: ").strip().strip('"')
        if not path2:
            path2 = input("Enter path for the second file: ").strip().strip('"')
    
    if not os.path.exists(path1):
        print(f"Error: File not found: {path1}")
        sys.exit(1)
    if not os.path.exists(path2):
        print(f"Error: File not found: {path2}")
        sys.exit(1)
        
    output_name = "combined_race_log.txt"
    combine_files(path1, path2, output_name)
