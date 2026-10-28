# BAD AI — race engineer and commentated replays

BAD AI does two things for **Automobilista 2** and **RaceRoom**:

1. **While you race:** an AI race engineer talks to you over the radio, and every race you drive is recorded in the background.
2. **After you race:** it turns a recorded race into a commentated replay video with AI commentators and broadcast graphics, guiding you through each step.

You click one logo before you race. Afterwards you pick the race from a list and follow the steps.

---

## Contents

- [Install](#install)
- [Start the app](#start-the-app)
- [Racing](#racing)
- [Making a replay](#making-a-replay)
- [Where everything is saved](#where-everything-is-saved)
- [The Activity panel](#the-activity-panel)
- [Settings](#settings)
- [Graphics in detail](#graphics-in-detail)
- [Troubleshooting](#troubleshooting)
- [Upgrading from the old launcher](#upgrading-from-the-old-launcher)

---

## Install

You need Windows, **Python 3.10 or newer** and a **Gemini API key** (free from [Google AI Studio](https://aistudio.google.com/)).

1. Install Python from [python.org](https://www.python.org/downloads/). Tick **"Add Python to PATH"** during the install.
2. Install the packages. Open a terminal in this folder and run:

   ```bash
   pip install google-genai pillow pyyaml pynput pydirectinput pygetwindow PyQt5 pydub numpy sounddevice pygame-ce
   ```

3. Install **FFmpeg** (used to cut and export the video) and make sure `ffmpeg` works in a terminal. You can get it from [ffmpeg.org](https://ffmpeg.org/download.html) or with `winget install ffmpeg`.
4. Install the voice generator. It needs an NVIDIA graphics card; see [TTS/TTSFast/README.md](TTS/TTSFast/README.md):

   ```bash
   pip install faster-qwen3-tts gradio soundfile torch
   ```

5. Copy `launcher_config.example.json` to `launcher_config.json`. You can also skip this and enter your API key on the Settings screen later.
6. The race engineer has its own setup (wheel button, persona, voice). See [RaceEngineer/README.md](RaceEngineer/README.md). Run `RaceEngineer\setup_wheel.bat` once to pick your push-to-talk button.

## Start the app

Double-click **`BAD AI.pyw`**. It opens without a console window.

You can also run it from a terminal:

```bash
python launcher.py
```

The app has three screens, shown along the top:

| Screen | What it's for |
| --- | --- |
| **Race** | Pick your sim and go racing. |
| **My races** | Every race you've recorded. Open one to make its replay. |
| **Settings** | Gemini key, where your screen recordings are saved, and the race engineer. |

## Racing

1. On the **Race** screen, click the **AMS2** or **RaceRoom** logo.
2. The race engineer and the race recorder start in the background, and the screen shows the live session.
3. Start the sim and race as usual. Hold **Caps Lock** (or your wheel button) to talk to your engineer.
4. When you're done, click **Stop session** or just close BAD AI. Both stop cleanly and your races are saved.

What gets recorded:

- **Only race sessions.** Practice and qualifying are skipped. Recording starts by itself when a race session begins.
- **Only races that ran at least 5 minutes** from the green flag. Restarts, quick tests and races you quit early are thrown away.
- Every race gets its own folder, named after the date, time, track and car, e.g. `Races\2026-10-27 12.16 Circuit Zolder - Grand Prix - Super Touring`.

> **Tip:** If the end of a race isn't detected (it sometimes isn't in timed races), press **F12** on the last lap.

### Engineer only, or recorder only

Under the logos are two switches, **Race engineer** and **Race recorder** (your choice is remembered). At least one is always on.

| Engineer | Recorder | Use it for |
| --- | --- | --- |
| ON | ON | Racing with the engineer, and keeping the race for a replay (the default). |
| ON | OFF | Just the engineer on the radio. Nothing is recorded; use this if you never make replays. |
| OFF | ON | Just recording, e.g. logging an AMS2 replay to make a new race. |

To make a new race from an **AMS2 replay**:

1. Set the engineer **OFF** (recorder **ON**) and click the **AMS2** logo.
2. In AMS2, open the race replay and play it **from the start**, past the green flag, to the finish. Don't rewind once the race has started — the recorder would take that as a restart.
3. When the replay ends, click **Stop session**. The race appears in **My races** like any other.

RaceRoom doesn't share driver data during replays, so RaceRoom races have to be recorded live.

## Making a replay

Go to **My races** and click **Make replay** on a race. The race screen shows the steps on the left. Each step explains what to do, and the next button to press is always the red one.

| Step | What happens | Your part |
| --- | --- | --- |
| **Race notes** | Notes that every AI step reads: championship, storylines, rivalries, nicknames, how to pronounce names. **Camera notes** are only for the camera director. | Type them in. They save automatically. Optional, but they make a big difference. |
| **1. Film the replay** | The **Camera Director** plans the camera shots with AI and switches cameras while the replay plays. | Press **Generate**, load the replay in the sim, start your screen recorder (OBS), sync at the green flag, press **Play Sequence**. Then pick the video in the **Replay video** box. The newest recordings are suggested. |
| **2. Build the event list** | Merges the camera notes into the race log, then the AI picks the moments worth talking about. | In **Review & sync the events**, watch the video, fix or add events, and sync the log to the video. Press **Save**. It saves into the race; no file name needed. |
| **3. Write the commentary** | Adds colour from your race notes, gives each event a word count, and writes the commentators' script. | Press **Run all**. Pick a different commentary prompt (commentator pair) first if you like. |
| **4. Voice it** | Generates the voices, then stitches them into one commentary track. | The voice generator opens in your browser. When the clips are done, press **Stop** on that step, then **Run** to stitch the audio. |
| **5. Graphics** | Broadcast graphics: timing tower, pop-ups, driver cards. | Set the graphics up once (they carry over to your next race), then let the AI plan the pop-ups. |
| **6. Export** | Cuts the replay to the commentary, adds the audio and graphics, and saves the MP4. | Press **Run**, then **Watch**. |

How the step badges work:

- **Done** — the step's file exists.
- **Ready** — everything the step needs is there.
- **Waiting** — something earlier is missing. The card says what.
- **Out of date** — something the step reads changed after it ran (for example, you re-reviewed the events). Run it again.

Every AI step has a **Prompt** list (the app remembers your choice) and a **Copy prompt** button, so you can paste the full prompt into another AI if you prefer.

## Where everything is saved

Each race is one folder in `Races\`, and every file has the same name in every race, so you always know what's what:

| File | Made by |
| --- | --- |
| `race.json` | The app: sim, track, date, length, drivers, chosen video |
| `race_notes.txt`, `camera_notes.txt` | You (Race notes step) |
| `01_race_log.txt` | The race recorder |
| `01_race_log_timing.jsonl` | The race recorder (live timing for the graphics) |
| `02_camera_sequence.json`, `02_director_notes.txt` | Camera Director |
| `03_combined_log.txt` | Merge camera notes into the log |
| `04_events.txt` | Pick the events for commentary |
| `05_events_reviewed.txt` | Review & sync the events |
| `06_events_spiced.txt` | Add in spice |
| `07_events_timed.txt` | Work out how long each line can be |
| `08_commentary.txt` | Write the commentary |
| `09_audio_clips\` | Record the voices |
| `10_commentary_audio.wav` | Stitch the audio together |
| `11_graphics_setup.json`, `11_graphics_plan.json` | Graphics setup / Plan the pop-up graphics |
| `12_final_video.mp4` | Export the final video |
| `history\` | The previous version of a file, kept whenever a step runs again |
| `debug\` | The graphics AI's prompt and raw reply, and the graphics test clips |

Your screen recording stays where your recorder saved it. The race only remembers its path.

**Archive** on a race card moves the race to `Races\_Archive`. Nothing is ever deleted.

## The Activity panel

Nothing opens a console window. Everything the engineer, the recorders and the replay steps print goes to the **Activity** panel. Open it with the **Activity** button at the top right, which also shows how many things are running.

- **Everything** shows all output interleaved. Click a single item to see only its output.
- **Stop selected** stops one item, and **Clear finished** tidies the list.
- When an automatic step runs, the panel opens on it, so you can watch the AI think.

Tools with their own window (Camera Director, Review & Sync, Graphics setup, the voice generator) still open their window. Only the console behind them is gone.

## Settings

- **Gemini (AI):** API key, the model used by the replay steps, max output tokens and thinking level.
- **Replay recordings:** the folder OBS (or your recorder) saves to, so the newest videos can be suggested. Default: your Windows *Videos* folder.
- **Race engineer:** persona, emotion, push-to-talk, models and strategy options. These are saved on top of `RaceEngineer\config.yaml`; anything else is edited in that file.
- **Tools:** the **Corner Mapper** for AMS2 and RaceRoom. Drive a lap to map a track's corner names, so the log and commentary can say where things happened.

Settings are saved when you click **Save settings** or leave the screen.

## Graphics in detail

**Graphics setup** opens a preview of your race video:

- **Layout tab:** pick a region (leaderboard, map, logo, pop-ups, ticker, team radio) and drag a box over the video to place it.
- **Branding tab:** championship name, season, round, logo (from `Graphics\Logos`) and colours. Sponsor and manufacturer logos go here too.
- **Drivers tab:** links each name in the race log to a display name and a car image from `Graphics\CarPreviews`. **AI fill from race notes** reads your notes for numbers, countries and liveries.
- **Championships:** the bar under the header shows the championship you're working in. Championships hold the branding, layout and every driver, and live in `Graphics\Championships`. When one is overwritten or deleted, the old version goes to `Graphics\Championships\Backups` (the last 10 are kept).
- **Timing tower:** both sims get a full timing tower drawn from the live timing the recorder saves (`01_race_log_timing.jsonl`). For AMS2, hide the in-game leaderboard when you record. To go back to a frame around the game's own leaderboard, untick **Show timing tower** on the Layout tab.
- **Automatic graphics:** biggest gainer and loser one minute after the start, a Fastest Lap card, and team-radio quotes for every (Radio) line in the reviewed events. Each can be switched off.
- **Render 30s test clip** checks a section before the full export. The clip is saved in the race's `debug\` folder.

Then **Plan the pop-up graphics** lets the AI choose the moments for driver cards, battles, overtakes, race control, pit stops and stats. Every number on screen comes from the race data. You can review the plan in Graphics setup under **Planned Graphics**.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| A step says **Waiting** | The card names what's missing. Usually an earlier step, or the replay video. |
| An automatic step finished but stays **Ready** | It didn't write its file. Open **Activity**, select the step and read the end of its output (often an API key or quota problem). |
| The engineer or recorder **stopped unexpectedly** | The live screen and the Activity panel show its last messages. |
| No race was saved | Only race sessions of 5 minutes or more are kept. Check the recorder's output in **Activity**. |
| RaceRoom replays have no driver data | RaceRoom doesn't share driver data during replays. Record the race live. |
| Something in the app itself went wrong | Details are written to `launcher_errors.log` in this folder. |

## Upgrading from the old launcher

Projects from the old launcher (`Projects\`) can be brought into **My races**. On **My races**, click **Import old projects**.

- Every race log of 5 minutes or more becomes a race.
- The files the project made are copied under the new names, so you can carry on where you left off.
- The `Projects` folder itself is not changed.

The old **Scan Files** button is gone: every step writes a fixed file name, so the app always knows what's done.
