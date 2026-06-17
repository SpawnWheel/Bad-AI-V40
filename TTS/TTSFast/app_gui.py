import gradio as gr
import os
import json
import subprocess
import threading
import time
import re

# Get initial list of speakers to show in the UI if possible
def get_speakers_from_file(file_path):
    if file_path is None:
        return []
    
    # Handle both string paths and Gradio file objects
    path = file_path if isinstance(file_path, str) else getattr(file_path, "name", None)
    
    if path is None or not os.path.exists(path):
        return []
        
    speakers = set()
    pattern = r'^(\d{2}:\d{2}:\d{2}) - \[(.*?)\] (.*)$'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                match = re.match(pattern, line.strip())
                if match:
                    speakers.add(match.group(2))
    except Exception:
        pass
    return sorted(list(speakers))

def run_tts_process(input_file, output_folder, model_size, xvec_only, temperature, start_line, limit, progress=gr.Progress()):
    if input_file is None:
        yield "Error: Please select an input file."
        return

    path = input_file if isinstance(input_file, str) else getattr(input_file, "name", None)

    # Prepare arguments
    args = [
        "python", "process_tts.py",
        "--input", path,
        "--output", output_folder,
        "--model", f"Qwen/Qwen3-TTS-12Hz-{model_size}",
        "--temperature", str(temperature),
        "--start_line", str(int(start_line)),
    ]
    
    if xvec_only:
        args.append("--xvec_only")
    
    if limit and int(limit) > 0:
        args.extend(["--limit", str(int(limit))])

    # Run the process and capture output
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    output_log = ""
    # Simple progress tracking by counting "[x/y]" in output
    for line in process.stdout:
        output_log += line
        # Match progress format [1/150]
        progress_match = re.search(r'\[(\d+)/(\d+)\]', line)
        if progress_match:
            current = int(progress_match.group(1))
            total = int(progress_match.group(2))
            progress(current / total, desc=f"Processing line {current} of {total}")
        
        yield output_log

    process.wait()
    if process.returncode == 0:
        yield output_log + "\n--- Processing Complete! ---"
    else:
        yield output_log + f"\n--- Process exited with code {process.returncode} ---"

def get_mappings():
    if os.path.exists("speaker_mappings.json"):
        with open("speaker_mappings.json", "r", encoding="utf-8") as f:
            return json.dumps(json.load(f), indent=4)
    return "{}"

def save_mappings(text):
    try:
        data = json.loads(text)
        with open("speaker_mappings.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return "Mappings saved successfully!"
    except Exception as e:
        return f"Error saving mappings: {e}"

def refresh_speakers(file_path):
    if file_path is None:
        return "Please upload a commentary file to see detected speakers."
        
    speakers = get_speakers_from_file(file_path)
    if not speakers:
        return "No speakers detected in file."
    
    # Load mappings to find where the voices are
    mappings = {}
    if os.path.exists("speaker_mappings.json"):
        with open("speaker_mappings.json", "r", encoding="utf-8") as f:
            mappings = json.load(f)
            
    voices_dir = "CommentatorsAndDrivers"
    status_lines = [f"### Detected Speakers ({len(speakers)}):"]
    
    for s in speakers:
        found_msg = "❌ No voice file mapped"
        voice_file = mappings.get(s)
        
        if voice_file:
            path = os.path.join(voices_dir, voice_file)
            if os.path.exists(path):
                ref_text_path = path.rsplit('.', 1)[0] + '.txt'
                if os.path.exists(ref_text_path):
                    found_msg = "✅ Found Voice + Transcript (High Quality)"
                else:
                    found_msg = "⚠️ Found Voice (Standard Quality)"
        
        status_lines.append(f"- **{s}**: {found_msg}")
        
    return "\n".join(status_lines)

def upload_voice(wav_file, txt_file=None, direct_text=""):
    if wav_file is None:
        return "Please select a WAV file."
    
    voices_dir = "CommentatorsAndDrivers"
    if not os.path.exists(voices_dir):
        os.makedirs(voices_dir)
        
    # Save WAV
    wav_name = os.path.basename(wav_file.name)
    wav_dest = os.path.join(voices_dir, wav_name)
    import shutil
    shutil.copy(wav_file.name, wav_dest)
    
    msg = f"Successfully uploaded {wav_name}."
    
    # Save TXT (priority: direct text > file upload)
    transcript_content = ""
    if direct_text.strip():
        transcript_content = direct_text.strip()
    elif txt_file is not None:
        try:
            with open(txt_file.name, 'r', encoding='utf-8') as f:
                transcript_content = f.read().strip()
        except Exception as e:
            return f"Error reading transcript file: {e}"

    if transcript_content:
        txt_name = wav_name.rsplit('.', 1)[0] + ".txt"
        txt_dest = os.path.join(voices_dir, txt_name)
        with open(txt_dest, 'w', encoding='utf-8') as f:
            f.write(transcript_content)
        msg += f" Added transcript {txt_name} for High Quality mode."
        
    return msg

def load_voice_transcript(voice_filename):
    if not voice_filename:
        return ""
    voices_dir = "CommentatorsAndDrivers"
    txt_path = os.path.join(voices_dir, voice_filename.rsplit('.', 1)[0] + ".txt")
    if os.path.exists(txt_path):
        with open(txt_path, 'r', encoding='utf-8') as f:
            return f.read()
    return ""

def save_voice_transcript(voice_filename, text):
    if not voice_filename:
        return "No voice selected."
    voices_dir = "CommentatorsAndDrivers"
    txt_path = os.path.join(voices_dir, voice_filename.rsplit('.', 1)[0] + ".txt")
    try:
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(text.strip())
        return f"✅ Transcript saved for {voice_filename}. Voice is now High Quality!"
    except Exception as e:
        return f"❌ Error: {e}"

def list_voices_dropdown():
    voices_dir = "CommentatorsAndDrivers"
    if os.path.exists(voices_dir):
        return sorted([f for f in os.listdir(voices_dir) if f.endswith(".wav")])
    return []

# Build the UI
with gr.Blocks(title="Faster Qwen3 TTS - Race Commentary") as demo:
    gr.Markdown("# 🏎️ Faster Qwen3 TTS Commentary Processor")
    gr.Markdown("Transform your commentary text files into individual MP3 files optimized for the 3060 Ti.")
    
    with gr.Tab("Generation"):
        with gr.Row():
            with gr.Column():
                input_file = gr.File(label="Commentary Text File", file_types=[".txt"])
                output_folder = gr.Textbox(label="Output Folder", value="outputs test")
                
                with gr.Row():
                    model_size = gr.Dropdown(
                        label="Model Size", 
                        choices=["0.6B-Base", "1.7B-Base"], 
                        value="0.6B-Base"
                    )
                    cloning_mode = gr.Checkbox(label="Fast Mode (xvec_only)", value=True)
                
                with gr.Row():
                    temp = gr.Slider(minimum=0.1, maximum=1.5, value=0.9, step=0.1, label="Temperature")
                    start_line = gr.Number(label="Start Line Index", value=0, precision=0)
                    limit = gr.Number(label="Limit (0 for all)", value=5, precision=0)
                
                generate_btn = gr.Button("🚀 Start Processing", variant="primary")
            
            with gr.Column():
                with gr.Row():
                    speaker_info = gr.Markdown("Select a file to see detected speakers...")
                    refresh_spk_btn = gr.Button("🔄 Refresh Status", size="sm")
                status_log = gr.Textbox(label="Processing Log", interactive=False, lines=15)
        
        input_file.change(refresh_speakers, inputs=[input_file], outputs=[speaker_info])
        refresh_spk_btn.click(refresh_speakers, inputs=[input_file], outputs=[speaker_info])
        generate_btn.click(run_tts_process, 
                          inputs=[input_file, output_folder, model_size, cloning_mode, temp, start_line, limit], 
                          outputs=[status_log])

    with gr.Tab("Manage Library"):
        gr.Markdown("### 📚 Manage Voices & Transcripts")
        gr.Markdown("Select an existing voice to add or edit its transcript for **High Quality** mode.")
        with gr.Row():
            with gr.Column():
                voice_select = gr.Dropdown(label="Select Voice from Library", choices=list_voices_dropdown())
                refresh_library_btn = gr.Button("🔄 Refresh Library List", size="sm")
                transcript_editor = gr.Textbox(label="Voice Transcript", placeholder="Write what the speaker says in their reference WAV...", lines=8)
                save_transcript_btn = gr.Button("💾 Save Transcript", variant="primary")
            with gr.Column():
                library_status = gr.Markdown("")
        
        voice_select.change(load_voice_transcript, inputs=[voice_select], outputs=[transcript_editor])
        save_transcript_btn.click(save_voice_transcript, inputs=[voice_select, transcript_editor], outputs=[library_status])
        refresh_library_btn.click(lambda: gr.update(choices=list_voices_dropdown()), outputs=[voice_select])

    with gr.Tab("Load New Voice"):
        gr.Markdown("### 🎙️ Upload Reference Audio")
        gr.Markdown("Add a new voice to your library. Providing a transcript enables High-Quality ICL mode.")
        with gr.Row():
            with gr.Column():
                new_wav = gr.File(label="Select WAV File", file_types=[".wav"])
                gr.Markdown("---")
                gr.Markdown("**Transcript (Optional)**")
                new_txt_input = gr.Textbox(label="Type Transcript Here", placeholder="Paste the text from the audio clip...", lines=5)
                new_txt_file = gr.File(label="OR Upload Transcript File", file_types=[".txt"])
                upload_btn = gr.Button("📤 Upload to Library", variant="primary")
            with gr.Column():
                upload_status = gr.Textbox(label="Upload Status", interactive=False)
        
        upload_btn.click(upload_voice, inputs=[new_wav, new_txt_file, new_txt_input], outputs=[upload_status])

    with gr.Tab("Speaker Mappings"):
        gr.Markdown("Edit the manual mappings between name tags and WAV files.")
        mapping_text = gr.Code(value=get_mappings(), language="json", label="speaker_mappings.json")
        with gr.Row():
            refresh_map_btn = gr.Button("🔄 Refresh from File")
            save_map_btn = gr.Button("💾 Save Mappings", variant="primary")
        
        save_status = gr.Markdown("")
        
        refresh_map_btn.click(get_mappings, outputs=[mapping_text])
        save_map_btn.click(save_mappings, inputs=[mapping_text], outputs=[save_status])

    with gr.Tab("Voices Preview"):
        gr.Markdown("Current files in `CommentatorsAndDrivers` folder.")
        
        def list_voices():
            if os.path.exists("CommentatorsAndDrivers"):
                files = [f for f in os.listdir("CommentatorsAndDrivers") if f.endswith(".wav")]
                return "\n".join([f"- {f}" for f in files])
            return "Folder not found."
            
        voices_list = gr.Markdown(list_voices())
        refresh_voices = gr.Button("Refresh List")
        refresh_voices.click(list_voices, outputs=[voices_list])

if __name__ == "__main__":
    demo.queue().launch(inbrowser=True)
