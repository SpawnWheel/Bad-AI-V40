# Faster Qwen3 TTS Commentary Processor

This application automates the conversion of race commentary text files into individual, timecoded MP3 files using the high-performance [faster-qwen3-tts](https://github.com/andimarafioti/faster-qwen3-tts) engine. It is specifically optimized for NVIDIA RTX GPUs (like the 3060 Ti) using CUDA graph capture for maximum throughput.

## Features

- **Timecode-Based Export:** Automatically parses text files and names files based on timecodes (e.g., `000300_Martin_Haven.mp3`).
- **Dynamic Voice Cloning:** Uses reference WAV files to clone any speaker's voice on the fly.
- **Smart Mapping:** Automatically matches speaker names in text to voice files in your `CommentatorsAndDrivers` folder and remembers them in `speaker_mappings.json`.
- **Dual Cloning Modes:** Supports both high-speed `x-vector` cloning and high-quality `ICL` (In-Context Learning) cloning if a transcript is provided.
- **Testing Controls:** Process specific ranges of lines using `--start_line` and `--limit` to save time and VRAM.

## Installation

Ensure you have Python 3.10 to 3.13 and the required dependencies installed:

```powershell
pip install faster-qwen3-tts pydub soundfile torch
```

*On Python 3.13, also install `audioop-lts`, which `pydub` needs.*

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

## Designing New Voices (Voice Designer tab)
Open the app (or **Voice studio** on the launcher's home screen or My races, no race needed) and go to **Voice Designer**. Voices are designed with **Gemini 3.8 Flash TTS**, using the Gemini API key from the launcher's Settings. Qwen3-TTS VoiceDesign was tried first and made every accent American.
1. Pick who the voice is for: a driver, a driver's engineer, a generic pool voice or a commentator. *Fill in from a championship* copies a driver's name, other names, accent and a sample line in that accent from a championship file.
2. Set the gender, accent (e.g. `Scottish (Glasgow)`) and language code, then describe the person's permanent traits: age, pitch, texture, pace and manner. Make each driver different from the others. Ask for a clean close-mic voice: the radio sound is added later.
3. Generate a few takes. Each take is a new Gemini voice saying the sample line (about 20 s each). Listen (with or without the radio sound) and save the one you like.

The take is saved as `.wav` + `.txt` (its words) + `.voice.json` (description, gender, accent and the Gemini voice id). From then on the commentary clones that clip with Qwen3-TTS on your graphics card, so the voice and its accent are the same in every race. **Try a saved voice** lets you hear any library voice say a new line, cloned the same way.

**Changing a voice:** open **Edit a saved voice**, pick it and press *Load into the form*. Then either:
- change the sample line and press **Record a new sample**: the same Gemini voice says the new line (say, angrier or calmer), so it stays the same person, or
- change the description and press **Generate takes** to design a new voice for them.

Either way, save with *Replace* ticked (loading ticks it for you).

**Adding a voice** from a recording you already have (a `.wav` plus what it says) works in the **Load New Voice** tab, as before.

Where voices go in `CommentatorsAndDrivers`:
- `Drivers/`: one voice per driver, found by any of the names in its `.voice.json` (`aliases`).
- `Engineers/`: an engineer for a particular driver (`for` lists the driver's names).
- `Pool/`: generic driver and engineer voices. A driver or engineer without their own voice gets one of these, picked from the name, so they sound the same every race.
- Top level: commentators, as before.

## Team Radio
In the commentary script, a line tagged `[RADIO <driver>]` or `[RADIO <driver>'s engineer]` is a team radio message (the V23 commentary prompts write these). It's voiced by that driver's or engineer's voice and gets a broadcast radio sound: narrow band, distortion, static and crackle, engine noise in the car or pit-wall noise behind the engineer, plus open and close tones (`radio_fx.py`). To use your own tones, put `radio_in.wav` / `radio_out.wav` in a `RadioSounds` folder here.

To use a different voice for any speaker, open **Pick a voice for a speaker** on the Generation tab. Your pick is saved in `speaker_mappings.json`, and **(automatic)** gives the choice back to the app.

## Improving Voice Quality (ICL Mode)
By default, the system uses the reference audio for tone. To significantly improve pronunciation and emotion, create a `.txt` file with the **exact same name** as your reference `.wav` file (e.g., `AlexJacques.txt`) containing the transcript of that audio. The script will automatically detect it and enable High-Quality ICL mode.
