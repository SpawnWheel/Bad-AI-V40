# AMS2 Camera Controller

This tool automates driver switching in Automobilista 2 (AMS2) based on a race event log processed by Gemini.

## Features
- **Automated Driver Switching:** Navigates the in-game leaderboard using Up/Down keys and selects the target driver with Enter.
- **Gemini Integration:** Generates a camera sequence JSON using your race log and the provided `Prompt.txt`.
- **Director Notes Logging:** Automatically logs every camera change to a timestamped text file.
- **Sync Tools:** Easily synchronize the tool's timer with the simulator's race time.
- **Global Pause:** Press `Space` at any time to pause/resume the sequence.

## Setup
1. Ensure you have a Gemini API Key. You can place it in `API_Key.txt` in the root folder or enter it when prompted.
2. Run `main_ams2.py`.
3. Select the "Automobilista 2" window from the dropdown (click 'R' to refresh).

## Usage
1. **Generate Sequence:** Click "Generate (Gemini)" and select your race log file (e.g., `Race_20260501_150848.txt`).
2. **Load Sequence:** If you already have a JSON sequence, click "Load JSON".
3. **Sync Timer:**
   - Note the time of the "Green Flag" in your log (default 00:00:06).
   - In the sim, when the race starts (or at any known time), click "Capture Sim GF".
   - Click "Apply Sync" to calculate the offset.
4. **Play:** Click "Play Sequence". The tool will automatically switch drivers at the specified timecodes.

## Configuration
You can adjust the following in the UI or `config_ams2.json`:
- **Hold Time:** How long a key is held down.
- **Gap:** Delay between keypresses.
- **Max Drivers:** The number of participants in the race (used to reset the selection to P1).

## Notes
- The game window must be in focus for keypresses to work.
- It is recommended to manually set the camera to "TV" or "Trackside" once before starting the sequence.
