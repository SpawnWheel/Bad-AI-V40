import os
import re
import json
import random
import torch
import soundfile as sf
from pydub import AudioSegment
from faster_qwen3_tts import FasterQwen3TTS
import voice_lookup
from radio_fx import apply_radio
import argparse
import sys
import threading
import time
from collections import deque

CLIP_TAG = "@@CLIP "
FAILED_TAG = "@@FAILED "
IDLE_TAG = "@@IDLE"

def parse_line(line):
    # Format: HH:MM:SS - [Speaker Name] Text
    pattern = r'^(\d{2}:\d{2}:\d{2}) - \[(.*?)\] (.*)$'
    match = re.match(pattern, line)
    if match:
        return match.groups()
    return None

def find_voice_file(speaker_name, voices_dir, mappings):
    """The voice for a speaker: see voice_lookup.resolve (commentators by name, team radio by driver)."""
    return voice_lookup.resolve(speaker_name, voices_dir, mappings)[0]

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
    parser.add_argument("--seed", type=int, default=-1, help="Lock the random seed for every line (-1 = new random seed per line)")

    # Validation mode
    parser.add_argument("--validate", action="store_true", help="Only validate mappings and exit")
    parser.add_argument("--start_line", type=int, default=0, help="Line number to start from (0-indexed)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of lines to process")
    parser.add_argument("--dry_run", action="store_true", help="Show what would be processed without generating audio")

    # Redo queue (used by the GUI)
    parser.add_argument("--listen", action="store_true",
                        help='Read redo requests from stdin, one JSON per line: {"redo": line, "path": clip}; '
                             'keeps the model loaded after the last line until stdin closes')
    parser.add_argument("--redo", action="append", default=[], metavar="LINE=PATH",
                        help="Regenerate a line over an existing clip (repeatable)")
    parser.add_argument("--redo_only", action="store_true", help="Only process the --redo lines")
    parser.add_argument("--idle_timeout", type=float, default=0,
                        help="With --listen, unload the model after this many seconds without a redo (0 = never)")

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
    all_parsed = {}
    for i, line in enumerate(all_lines):
        parsed = parse_line(line.strip())
        if parsed:
            all_parsed[i] = parsed
    lines_to_process = list(all_parsed.items())

    if args.start_line > 0:
        lines_to_process = [l for l in lines_to_process if l[0] >= args.start_line]

    if args.limit is not None:
        lines_to_process = lines_to_process[:args.limit]

    redo_jobs = []
    for r in args.redo:
        idx, _, path = r.partition("=")
        if idx.strip().isdigit() and int(idx) in all_parsed and path:
            redo_jobs.append((int(idx), path))
        else:
            print(f"Ignoring redo request {r!r}: no such line in {args.input}")

    if args.redo_only:
        lines_to_process = []

    if not lines_to_process and not redo_jobs:
        print("No lines to process with current filters.")
        return

    speakers = set(p[1] for i, p in lines_to_process) | {all_parsed[idx][1] for idx, _ in redo_jobs}

    # Validate mappings and find reference texts
    print("\n--- Speaker Mapping Validation ---")
    new_mappings_found = False
    speaker_files = {}
    speaker_texts = {}
    speaker_styles = {}
    missing_speakers = []

    # Worked out for every speaker in the script at once, so no two drivers share a generic pool voice
    # (and a partial run or a redo gets the same voices as the full run)
    assignments = voice_lookup.resolve_all(sorted({p[1] for p in all_parsed.values()}), args.voices, mappings)

    def resolve_speaker(s):
        nonlocal new_mappings_found
        voice_path = assignments[s][0] if s in assignments else find_voice_file(s, args.voices, mappings)
        if not voice_path:
            return None
        # Relative to the voices folder: designed voices sit in subfolders (Drivers/, Engineers/, Pool/)
        filename = os.path.relpath(voice_path, args.voices).replace("\\", "/")
        speaker_files[s] = voice_path

        # Look for reference text (same name as wav but .txt)
        ref_text_path = voice_path.rsplit('.', 1)[0] + '.txt'
        if os.path.exists(ref_text_path):
            with open(ref_text_path, 'r', encoding='utf-8') as tf:
                speaker_texts[s] = tf.read().strip()

        # Optional delivery instruction for this voice, e.g. "Speak English with a Spanish accent."
        style_path = voice_path.rsplit('.', 1)[0] + '.style.txt'
        if os.path.exists(style_path):
            with open(style_path, 'r', encoding='utf-8') as style_file:
                speaker_styles[s] = style_file.read().strip()

        # Commentators found by name are remembered; team radio voices are only stored when picked in the app,
        # so a voice designed for a driver later takes over from the generic one
        if not voice_lookup.is_radio(s) and (s not in mappings or mappings[s] != filename):
            mappings[s] = filename
            new_mappings_found = True
        return filename

    for s in sorted(list(speakers)):
        filename = resolve_speaker(s)
        if filename:
            print(f"Speaker: {s:20} -> Voice: {filename}" + (" (with transcript)" if s in speaker_texts else "")
                  + (f" (style: {speaker_styles[s]})" if speaker_styles.get(s) else ""))
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
    
    # Redo requests jump the queue: they run after the current line, before the next one
    redo_queue = deque(redo_jobs)
    # With --listen the model stays loaded after the last line, waiting for redos, until stdin closes
    listening = threading.Event()
    wake = threading.Event()
    if args.listen:
        listening.set()
        def listen_for_redos():
            for raw in sys.stdin:
                try:
                    req = json.loads(raw)
                    idx, path = int(req["redo"]), req["path"]
                except (ValueError, KeyError, TypeError):
                    continue
                if idx in all_parsed and path:
                    redo_queue.append((idx, path))
                    wake.set()
            listening.clear()
            wake.set()
        threading.Thread(target=listen_for_redos, daemon=True).start()

    # Process lines
    print(f"Starting processing {len(lines_to_process)} lines...")
    
    i = 0
    idle_announced = False
    while True:
        if not redo_queue and i >= len(lines_to_process) and listening.is_set():
            if not idle_announced:
                print(IDLE_TAG, flush=True)
                idle_announced = True
                idle_since = time.monotonic()
            if args.idle_timeout > 0:
                remaining = args.idle_timeout - (time.monotonic() - idle_since)
                if remaining <= 0 or not wake.wait(remaining):
                    if not redo_queue:
                        print(f"No redos for {args.idle_timeout:g} s, unloading the model.")
                        break
            else:
                wake.wait()
            wake.clear()
            continue
        idle_announced = False
        if redo_queue:
            orig_idx, redo_path = redo_queue.popleft()
            timecode, speaker, text = all_parsed[orig_idx]
            label = "[redo]"
        elif i < len(lines_to_process):
            orig_idx, (timecode, speaker, text) = lines_to_process[i]
            redo_path = None
            i += 1
            label = f"[{i}/{len(lines_to_process)}]"
        else:
            break

        if speaker not in speaker_files and not resolve_speaker(speaker):
            print(f"{label} Line {orig_idx}: no voice file for {speaker}, skipped")
            continue
        ref_audio = speaker_files[speaker]
        ref_text = speaker_texts.get(speaker, "")
        
        # Re-seed before every line so a locked seed gives each line the same start,
        # whatever line the run starts from; unlocked runs log their seed so a good one can be reused.
        # A redo always takes a fresh seed, since the locked one would give the same take back.
        line_seed = args.seed if args.seed >= 0 and not redo_path else random.randint(0, 2**31 - 1)
        torch.manual_seed(line_seed)

        print(f"{label} Line {orig_idx}: {timecode} - {speaker} (seed {line_seed})")
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
                xvec_only=args.xvec_only,
                instruct=speaker_styles.get(speaker) or None
            )
            
            if not audio_list:
                print(f"Warning: No audio generated for {timecode}")
                print(FAILED_TAG + json.dumps({"line": orig_idx}), flush=True)
                continue
                
            audio = audio_list[0]
            radio = voice_lookup.radio_speaker(speaker)
            if radio:
                # Team radio: the voice goes through the broadcast radio sound (static, engine, open/close tones)
                audio = apply_radio(audio, sr, role=radio[0], seed=line_seed)
            
            # Save temporary wav
            temp_wav = f"temp_{os.getpid()}.wav"
            sf.write(temp_wav, audio, sr)
            
            if redo_path:
                # A redo replaces its clip in place, keeping the old file's modified time:
                # the audio combiner orders clips that share a timecode by modified time
                output_path = redo_path
                old_times = os.stat(output_path) if os.path.exists(output_path) else None
            else:
                # Convert to MP3
                safe_time = timecode.replace(":", "")
                safe_speaker = re.sub(r"[\\/:*?\"<>|'’]", "", speaker).replace(" ", "_").replace(".", "")
                base_filename = f"{safe_time}_{safe_speaker}"
                output_filename = f"{base_filename}.mp3"
                output_path = os.path.join(args.output, output_filename)

                # If file exists, add incrementing suffix to avoid overwriting
                suffix = 1
                while os.path.exists(output_path):
                    output_filename = f"{base_filename}_{suffix}.mp3"
                    output_path = os.path.join(args.output, output_filename)
                    suffix += 1

            audio_seg = AudioSegment.from_wav(temp_wav)
            audio_seg.export(output_path, format="mp3")
            if redo_path and old_times:
                os.utime(output_path, ns=(old_times.st_atime_ns, old_times.st_mtime_ns))

            os.remove(temp_wav)

            # Machine-readable line for the GUI's clip list
            print(CLIP_TAG + json.dumps({"line": orig_idx, "timecode": timecode, "speaker": speaker, "text": text,
                                         "path": os.path.abspath(output_path), "seed": line_seed,
                                         "redo": bool(redo_path)}), flush=True)

        except Exception as e:
            print(f"Error at {timecode}: {e}")
            print(FAILED_TAG + json.dumps({"line": orig_idx}), flush=True)
            import traceback
            traceback.print_exc()

    print("\nProcessing complete!")

if __name__ == "__main__":
    main()
