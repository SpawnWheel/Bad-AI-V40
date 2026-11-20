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
3. **Sync Timer:** nothing to do. *Log Green Flag* is read from the green flag line in the race log, and *Sim Green Flag* starts at 00:00:06 (where the lights almost always go green in an AMS2 replay), so the offset is set as soon as the log loads. Once the replay is past the green flag, the tool reads the replay's own clock from AMS2's shared memory and corrects the offset, so the guess no longer matters. To override it by hand, type a different *Sim Green Flag*: *Time Offset* follows the two boxes by itself.
4. **Play:** Click "Play Sequence". The tool will automatically switch drivers at the specified timecodes.
5. **Recording (OBS):** with "Record with OBS" ticked, Play Sequence switches OBS to the chosen scene and starts recording, and the video offset is saved to the race's `02_video_sync.json` (again once the replay clock is read). Space pauses the recording with the sequence. "Stop Recording" stops it and makes it the race's replay video. Set OBS up first: see *Set up OBS recording* in the main README.

## Configuration
- **Max Drivers** (in the window): the number of participants in the race (used to reset the selection to P1).
- `hold_time` and `step_gap` (only in `config_ams2.json`): how long a key is held down, and the delay between key presses.

## Notes
- The game window must be in focus for keypresses to work.
- It is recommended to manually set the camera to "TV" or "Trackside" once before starting the sequence.
