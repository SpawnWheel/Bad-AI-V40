# Faster Qwen3 TTS Commentary Processor

This application automates the conversion of race commentary text files into individual, timecoded MP3 files using the high-performance [faster-qwen3-tts](https://github.com/andimarafioti/faster-qwen3-tts) engine. It is specifically optimized for NVIDIA RTX GPUs (like the 3060 Ti) using CUDA graph capture for maximum throughput.

## Features

- **Timecode-Based Export:** Automatically parses text files and names files based on timecodes (e.g., `000300_Martin_Haven.mp3`).
- **Dynamic Voice Cloning:** Uses reference WAV files to clone any speaker's voice on the fly.
- **Smart Mapping:** Automatically matches speaker names in text to voice files in your `CommentatorsAndDrivers` folder and remembers them in `speaker_mappings.json`.
- **Dual Cloning Modes:** Supports both high-speed `x-vector` cloning and high-quality `ICL` (In-Context Learning) cloning if a transcript is provided.
- **Testing Controls:** Process specific ranges of lines using `--start_line` and `--limit` to save time and VRAM.

## Installation

Ensure you have Python 3.10+ and the required dependencies installed:

```powershell
pip install faster-qwen3-tts pydub soundfile torch
```

*Note: You may need [FFmpeg](https://ffmpeg.org/) installed on your system for `pydub` to export MP3 files.*

## Project Structure

- `process_tts.py`: The main execution script.
- `CommentatorsAndDrivers/`: Place your reference `.wav` files here.
- `speaker_mappings.json`: Automatically managed file that maps names to voices.
- `filtered_..._commentary.txt`: Your input text file formatted as `HH:MM:SS - [Name] Text`.

## Usage Examples

### 1. Simple Test (First 5 Lines)
Run a quick test to verify voices and output folder:
```powershell
python process_tts.py --output "test_runs" --limit 5 --xvec_only
```

### 2. High Quality Generation
To use the 1.7B model (higher quality, more VRAM) and full ICL mode:
```powershell
python process_tts.py --output "final_commentary" --model Qwen/Qwen3-TTS-12Hz-1.7B-Base
```

### 3. Resume from Specific Line
If you stopped a large run, you can resume from a specific line index:
```powershell
python process_tts.py --output "final_commentary" --start_line 45
```

## Advanced Settings

| Flag | Description | Default |
| :--- | :--- | :--- |
| `--input` | Path to the text commentary file. | (Your .txt file) |
| `--output` | Folder to save generated MP3s. | `outputs test` |
| `--voices` | Directory containing voice samples. | `CommentatorsAndDrivers` |
| `--model` | Model size (`0.6B-Base` or `1.7B-Base`). | `0.6B-Base` |
| `--xvec_only` | Use faster, lower-quality cloning mode. | `False` |
| `--temperature`| Controls randomness (0.1 - 1.0). | `0.9` |
| `--dry_run` | Show what would happen without generating. | `False` |

## Improving Voice Quality (ICL Mode)
By default, the system uses the reference audio for tone. To significantly improve pronunciation and emotion, create a `.txt` file with the **exact same name** as your reference `.wav` file (e.g., `AlexJacques.txt`) containing the transcript of that audio. The script will automatically detect it and enable High-Quality ICL mode.
