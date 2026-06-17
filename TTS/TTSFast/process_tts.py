import os
import re
import json
import torch
import soundfile as sf
from pydub import AudioSegment
from faster_qwen3_tts import FasterQwen3TTS
import argparse
import sys

def parse_line(line):
    # Format: HH:MM:SS - [Speaker Name] Text
    pattern = r'^(\d{2}:\d{2}:\d{2}) - \[(.*?)\] (.*)$'
    match = re.match(pattern, line)
    if match:
        return match.groups()
    return None

def find_voice_file(speaker_name, voices_dir, mappings):
    # 1. Check mappings
    voice_file_name = mappings.get(speaker_name)
    if voice_file_name:
        path = os.path.join(voices_dir, voice_file_name)
        if os.path.exists(path):
            return path
        # If it was just the name without path, or filename changed
    
    # 2. Search in voices_dir
    files = os.listdir(voices_dir)
    norm_key = speaker_name.lower().replace(" ", "").replace("_", "")
    
    # Try fuzzy match
    for f in files:
        if not f.endswith(".wav"):
            continue
        norm_f = f.lower().replace(".wav", "").replace("commentator_", "").replace(" ", "").replace("_", "")
        if norm_key in norm_f or norm_f in norm_key:
            return os.path.join(voices_dir, f)
            
    return None

def main():
    parser = argparse.ArgumentParser(description="Faster Qwen3 TTS Processor")
    parser.add_argument("--input", default="filtered_GoodFinalLastFinal_forCommentary_commentary.txt", help="Input text file")
    parser.add_argument("--output", default="outputs test", help="Output directory")
    parser.add_argument("--voices", default="CommentatorsAndDrivers", help="Voices directory")
    parser.add_argument("--mappings", default="speaker_mappings.json", help="Speaker mappings JSON")
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base", help="Model name")
    
    # TTS Settings
    parser.add_argument("--language", default="English", help="Language")
    parser.add_argument("--temperature", type=float, default=0.9, help="Temperature")
    parser.add_argument("--top_k", type=int, default=50, help="Top K")
    parser.add_argument("--top_p", type=float, default=1.0, help="Top P")
    parser.add_argument("--repetition_penalty", type=float, default=1.05, help="Repetition Penalty")
    parser.add_argument("--xvec_only", action="store_true", help="Use x-vector only (faster, lower quality)")
    parser.add_argument("--no_sample", action="store_false", dest="do_sample", help="Disable sampling")
    parser.set_defaults(do_sample=True)
    parser.add_argument("--chunk_size", type=int, default=8, help="Chunk size for generation")
    
    # Validation mode
    parser.add_argument("--validate", action="store_true", help="Only validate mappings and exit")
    parser.add_argument("--start_line", type=int, default=0, help="Line number to start from (0-indexed)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of lines to process")
    parser.add_argument("--dry_run", action="store_true", help="Show what would be processed without generating audio")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.output):
        os.makedirs(args.output)
        
    # Load mappings
    mappings = {}
    if os.path.exists(args.mappings):
        with open(args.mappings, 'r', encoding='utf-8') as f:
            mappings = json.load(f)
            
    # Read input file and find unique speakers
    if not os.path.exists(args.input):
        print(f"Error: Input file {args.input} not found.")
        return

    with open(args.input, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
        
    # Filter lines based on start_line and limit
    lines_to_process = []
    for i, line in enumerate(all_lines):
        parsed = parse_line(line.strip())
        if parsed:
            lines_to_process.append((i, parsed))
            
    if args.start_line > 0:
        lines_to_process = [l for l in lines_to_process if l[0] >= args.start_line]
        
    if args.limit is not None:
        lines_to_process = lines_to_process[:args.limit]

    if not lines_to_process:
        print("No lines to process with current filters.")
        return

    speakers = set(p[1] for i, p in lines_to_process)
            
    # Validate mappings and find reference texts
    print("\n--- Speaker Mapping Validation ---")
    new_mappings_found = False
    speaker_files = {}
    speaker_texts = {}
    missing_speakers = []
    
    for s in sorted(list(speakers)):
        voice_path = find_voice_file(s, args.voices, mappings)
        if voice_path:
            filename = os.path.basename(voice_path)
            speaker_files[s] = voice_path
            
            # Look for reference text (same name as wav but .txt)
            ref_text_path = voice_path.rsplit('.', 1)[0] + '.txt'
            if os.path.exists(ref_text_path):
                with open(ref_text_path, 'r', encoding='utf-8') as tf:
                    speaker_texts[s] = tf.read().strip()
                print(f"Speaker: {s:20} -> Voice: {filename} (with transcript)")
            else:
                print(f"Speaker: {s:20} -> Voice: {filename}")
                
            if s not in mappings or mappings[s] != filename:
                mappings[s] = filename
                new_mappings_found = True
        else:
            print(f"Speaker: {s:20} -> NOT FOUND")
            missing_speakers.append(s)
            
    if missing_speakers:
        print("\nError: The following speakers have no matching voice files:")
        for ms in missing_speakers:
            print(f"  - {ms}")
        if not args.validate and not args.dry_run:
            print(f"\nPlease add them to {args.voices} or update {args.mappings}.")
            sys.exit(1)
            
    # Save mappings if updated
    if new_mappings_found:
        with open(args.mappings, 'w', encoding='utf-8') as f:
            json.dump(mappings, f, indent=4)
        print(f"\nUpdated {args.mappings} with new mappings.")

    if args.validate:
        print("\nValidation complete. Exiting.")
        return

    if args.dry_run:
        print(f"\n--- Dry Run: Would process {len(lines_to_process)} lines ---")
        for i, (orig_idx, (timecode, speaker, text)) in enumerate(lines_to_process):
            print(f"{i+1}. Line {orig_idx}: [{timecode}] {speaker}: {text[:50]}...")
        return

    # Initialize TTS
    print(f"\nLoading model {args.model}...")
    model = FasterQwen3TTS.from_pretrained(args.model)
    
    # Process lines
    print(f"Starting processing {len(lines_to_process)} lines...")
    
    for i, (orig_idx, (timecode, speaker, text)) in enumerate(lines_to_process):
        ref_audio = speaker_files.get(speaker)
        if not ref_audio:
            continue
            
        ref_text = speaker_texts.get(speaker, "")
        
        print(f"[{i+1}/{len(lines_to_process)}] Line {orig_idx}: {timecode} - {speaker}")
        
        try:
            audio_list, sr = model.generate_voice_clone(
                text=text,
                language=args.language,
                ref_audio=ref_audio,
                ref_text=ref_text,
                temperature=args.temperature,
                top_k=args.top_k,
                top_p=args.top_p,
                do_sample=args.do_sample,
                repetition_penalty=args.repetition_penalty,
                xvec_only=args.xvec_only
            )
            
            if not audio_list:
                print(f"Warning: No audio generated for {timecode}")
                continue
                
            audio = audio_list[0]
            
            # Save temporary wav
            temp_wav = f"temp_{os.getpid()}.wav"
            sf.write(temp_wav, audio, sr)
            
            # Convert to MP3
            safe_time = timecode.replace(":", "")
            safe_speaker = speaker.replace(" ", "_").replace(".", "")
            output_filename = f"{safe_time}_{safe_speaker}.mp3"
            output_path = os.path.join(args.output, output_filename)
            
            audio_seg = AudioSegment.from_wav(temp_wav)
            audio_seg.export(output_path, format="mp3")
            
            os.remove(temp_wav)
            
        except Exception as e:
            print(f"Error at {timecode}: {e}")
            import traceback
            traceback.print_exc()

    print("\nProcessing complete!")

if __name__ == "__main__":
    main()
