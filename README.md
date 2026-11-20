# BAD AI — race engineer and commentated replays

BAD AI does two things for **Automobilista 2** and **RaceRoom**:

1. **While you race:** an AI race engineer talks to you over the radio, and every race you drive is recorded in the background.
2. **After you race:** it turns a recorded race into a commentated replay video with AI commentators and broadcast graphics, guiding you through each step.

You click one logo before you race. Afterwards you pick the race from a list and follow the steps.

> **New track?** Run the [Corner Mapper](#map-the-track-first-corner-mapper) on it **before** you race, so the AI knows where on track every overtake and accident happened.

---

## Contents

- [Install](#install)
- [Start the app](#start-the-app)
- [Map the track first (Corner Mapper)](#map-the-track-first-corner-mapper)
- [Racing](#racing)
- [Making a replay](#making-a-replay)
- [Where everything is saved](#where-everything-is-saved)
- [The Activity panel](#the-activity-panel)
- [Settings](#settings)
- [Graphics in detail](#graphics-in-detail)
- [Troubleshooting](#troubleshooting)
- [Upgrading from the old launcher](#upgrading-from-the-old-launcher)
- [Help and contact](#help-and-contact)

---

## Install

You need Windows, **Python 3.10 to 3.13** and a **Gemini API key** (free from [Google AI Studio](https://aistudio.google.com/)).

Newer Python versions (3.14, 3.15) are not supported yet. Some packages, PyTorch in particular, don't have installers for them, so `pip install` fails.

1. Install Python 3.13 (or any version from 3.10 to 3.13) from [python.org](https://www.python.org/downloads/). Tick **"Add Python to PATH"** during the install.
2. Install the packages. Open a terminal in this folder and run:

   ```bash
   pip install google-genai pillow pyyaml pynput pydirectinput pygetwindow PyQt5 pydub numpy sounddevice pygame-ce
   ```

   On Python 3.13, also run `pip install audioop-lts`. Python 3.13 removed a module that `pydub` needs, and this package puts it back.

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

## Map the track first (Corner Mapper)

> **Important:** map the track with the **Corner Mapper** before you race on it. It must be done **before the race is recorded**, because the recorder writes the corner names into the log as it goes. Without a map, the AI can't tell where overtakes and accidents happened, and the commentary gets vague.

You only need to do this once per track layout. If you start a session on a track that has no map, the live session screen shows a warning with an **Open the Corner Mapper** button, so you can map it before the race you want to keep.

1. Open the Corner Mapper from any of these places:
   - the **Corner Mapper** links under *Ready to race?* on the **Race** screen
   - **Settings** → **Tools**
   - the **Corner Mapper** button on the live session screen
2. Drive a lap and mark each part of the track: not just the corners, but the straights, chicanes and other named sections too (for example *Start/finish straight*, *Back straight*). The more of the lap is named, the better the AI can place what happened.

## Racing

1. Mapped this track yet? If not, run the [Corner Mapper](#map-the-track-first-corner-mapper) first.
2. On the **Race** screen, click the **AMS2** or **RaceRoom** logo.
3. The race engineer and the race recorder start in the background, and the screen shows the live session.
4. Start the sim and race as usual. Hold **Caps Lock** (or your wheel button) to talk to your engineer.
5. When you're done, click **Stop session** or just close BAD AI. Both stop cleanly and your races are saved.

What gets recorded:

- **Only race sessions.** Practice and qualifying aren't recorded as races; recording starts by itself when a race session begins. During qualifying the recorder only keeps every driver's best lap, and the next race on the same track (within 6 hours) takes them for its starting grid.
- **Only races that ran at least 5 minutes** from the green flag. Restarts, quick tests and races you quit early are thrown away.
- Every race gets its own folder, named after the date, time, track and car, e.g. `Races\2026-10-27 12.16 Circuit Zolder - Grand Prix - Super Touring`.

> **Tip:** If the end of a live race isn't detected (it sometimes isn't in timed races), press **F12** on the last lap. You don't need to when logging an AMS2 replay (see below).

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
3. When the replay reaches its end, AMS2 jumps back to the start of the replay and the recorder saves the race by itself. Then click **Stop session**. The race appears in **My races** like any other.

AMS2 doesn't say when a race has finished during a replay, so the recorder works the finish out itself when you stop: the winner is the first car to complete the last lap, and every other car finishes the next time it crosses the line. The **Activity** panel then shows *Race end worked out from the replay* with the winner. Let the replay run to its end (or at least until the cars you care about have crossed the line) before you stop. If you stop while the cars are still racing, no finish is written. Pressing **F12** on the last lap still works and takes priority if you'd rather mark the finish yourself.

RaceRoom doesn't share driver data during replays, so RaceRoom races have to be recorded live.

## Making a replay

Go to **My races** and click **Make replay** on a race. The race screen shows the steps on the left. Each step explains what to do, and the next button to press is always the red one.

At a glance:

| Step | What you do |
| --- | --- |
| **Race notes** | Type a few notes about the race (optional). |
| **1. Film the replay** | Camera Director: **Generate (Gemini)**, sync to the green flag, **Play Sequence** while OBS records. Then **Use this** on your recording. |
| **2. Build the event list** | **Run** the merge, **Run** the event picker, then line the video up with the log in **Review & sync the events**. |
| **3. Write the commentary** | Starting grid and qualifying times (optional), then **Run** spice, line lengths and commentary. |
| **4. Voice it** | Record the voices in the browser, then **Run** the stitcher. |
| **5. Graphics** | Set up the graphics (optional), then **Run** the pop-up planner. |
| **6. Export** | **Run** and make a coffee. |

The full walkthrough is below.

> **Before you start:** has the track been mapped with the [Corner Mapper](#map-the-track-first-corner-mapper)? It has to be done **before the race is recorded**, otherwise the log can't say where things happened.

### Race notes

Type in anything the AI should know: the championship, storylines, rivalries, nicknames, how to pronounce names. They save automatically.

These notes are sent to the AI models (the spice step, the commentary writer and the graphics). You can leave them blank, but then the AI invents more of the story itself. **Camera notes** are only read by the Camera Director.

### 1. Film the replay

This step makes the pictures for your video: the sim plays the replay, the **Camera Director** switches cameras, and you screen-record it.

1. On the **Camera Director** step, press **Open**. The Camera Director opens in its own window.
2. Press **Generate (Gemini)**. The race log is sent to Gemini, which plans the camera shots. Wait until it's done (this enables **Play Sequence**). Do this before syncing.
3. Open **OBS** (or your screen recorder) and load the race replay in the sim.
4. **Sync the log time to the sim time** in the **Sync Tools** box:
   - **Log Green Flag** is filled in for you from the green flag line in the race log.
   - **AMS2:** nothing else to do. The Camera Director assumes the lights go green 6 seconds into the replay, then corrects itself from the replay's own clock once the race is under way.
   - **RaceRoom:** press **Capture Sim GF** when the replay shows the green flag. The sync is applied straight away. (If you type a **Sim Green Flag** in by hand instead, press **Apply Sync**.)
5. Go back to the very start of the replay (time zero) in the sim. In AMS2, press the replay's play button and quickly pause it again, so the button is highlighted and ready.
6. Start recording in OBS.
7. In the Camera Director, press **Play Sequence**. It starts the replay in the sim and the camera plan together, so they stay in sync.
8. Close the sim's replay bar before the green flag so it isn't in the video, then leave it alone. The Camera Director switches cameras for you.
9. When the replay ends, stop recording.

Back on the race screen, tell the app which video you recorded. The **Replay video** box suggests your newest recordings (from the folder set in **Settings**). Click **Use this** next to it, or **Choose file…** to pick it yourself.

### 2. Build the event list

1. **Merge camera notes into the log:** press **Run**. One click.
2. **Pick the events for commentary:** check the **Prompt** list (the latest prompt is chosen for you) and press **Run**. Wait for the AI to return the events.
3. **Review & sync the events:** press **Open**. The main job here is to line your recorded video up with the log:
   1. Fast-forward the video to the start of the race (the green flag).
   2. In the list, click the first event. It should be the green flag.
   3. Press **Sync All to Selected Row**. Every event now lines up with the video.

   You can also add events here or describe them better. If the log only says someone was overtaken but you can see they ran wide into the grass, write *XXX runs wide into the grass*. This helps most with wall contact where the car doesn't stop, because then no accident is logged. The app is good at spotting crashes from several positions lost, so you don't need to add every one.

   Press **Save**. It saves into the race; no file name needed.

### 3. Write the commentary

This page starts with two optional extras for the start of the video: the starting grid and the qualifying times. Then three steps write the script.

**Starting grid before the start (optional).** Tick it and the video opens on a full-screen starting grid while the commentators welcome the viewers and talk through the grid, then fades into the replay.

- **Grid shown for:** how many seconds of commentary before the green flag (5–30, default 10). About 15 seconds works well.
- **Replay starts:** how many seconds before the green flag the replay starts moving (default 2). This keeps the sim's menus out of the video: the video holds the frame at that point under the grid. **Check frame** shows it; pick a moment where the replay is already on screen.
- **Music:** **Add track…** copies a song into the `Music` folder. It plays under the grid, ducks under the commentators and fades out as the replay starts. **Quiet / Normal / Loud** sets its level.
- The grid shows each car's position, name, number, flag, team, logo and car, over the blurred race frame. The car comes from `Graphics\CarPreviews`: the Drivers tab car image when it is a car preview, else the file named after the driver, preferring one named after this race's car (`Thommo_Mini.dds` in a Mini race). The logo comes from the Drivers tab **Logo** (or a logo set as the car image).
- Nothing after the start moves: the intro uses the time before the green flag that was cut away before. Switching it on or off, or changing the length, marks **Work out how long each line can be** out of date, so run the commentary, voices and export again. Changing the music or the replay start only needs a new export.
- The recording has to start at least that long before the green flag; if it doesn't, the intro is shortened to fit.

**Qualifying times (optional).** Pole's lap time and everyone else's gap show on the grid, and the commentators get them too.

- If the recorder was on during qualifying, the times are read from the logs automatically.
- If not, use the **Qualifying times** box under the starting grid settings. Paste the results from the results screen, the league site or a spreadsheet, one driver a line with a lap time (`1:42.315`) or gap (`+0.214`), and press **Read pasted results**.
- Or paste a **screenshot** of the results into the box (Ctrl+V), or pick it with **Read screenshot…**. The AI reads the names and times, and also fills in the drivers' country flags. It needs the Gemini key, and the race's championship chosen at the top of the page.
- Pasted times replace recorded ones. A driver without a time (or a race without any) simply shows none. AMS2 replays carry no qualifying, so paste the results for races logged from a replay.

**Then the three automatic steps** (press **Run** on each, or **Run all**):

1. **Add in spice:** builds the subplots of the race from your notes and the AI's own research. Pick the latest prompt, or write your own. Some prompts are deliberately a little unhinged.
2. **Work out how long each line can be:** gives each event a word budget. It packs in more words per second near the start and then levels off, so the first minute or so is exciting with fewer pauses.
3. **Write the commentary:** pick your commentators in the **Prompt** list. A few commentator pairs come with the app, and it's easy to make your own. Press **Run** and wait for the script.

### 4. Voice it

1. **Record the voices:** press **Open**. This opens the voice generator in your browser and loads the voice model (faster-qwen3-tts).
   - It should find the voices the commentary needs and show them with green ticks. If one isn't ticked, find out why before going on: often a typo in the commentator's name returned by the AI, a prompt that made the AI return the wrong name, or a voice that isn't loaded. This is rare.
   - Change **Limit (0 for all)** from 5 to **0** and press **Start Processing**. This takes a while.
   - You can keep or redo clips as they come in, or just leave it to run. The model unloads itself a minute after it finishes.
   - When it's done, press **Stop** on the step and close the browser tab.
2. **Stitch the audio together:** press **Run**. It joins the clips into one commentary track (`10_commentary_audio.wav`).

### 5. Graphics

1. **Graphics setup (optional, but a nice touch):** press **Open** for a preview of your race video where you set up the timing tower, branding, drivers and pop-ups. There's a lot in here; see [Graphics in detail](#graphics-in-detail). Most settings carry over to your next race. Save and close when you're done.
2. **Plan the pop-up graphics:** press **Run**. The AI works out the timings and the extra pop-up boxes to go with your setup. You can go back to Graphics setup to check the plan under **Planned Graphics**, but it's usually right.

### 6. Export

Press **Run** and make a cup of coffee. A 30-minute video takes about 10 minutes to export with FFmpeg. Then press **Watch it**.

### Step badges and prompts

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
| `00_qualifying.json` | Qualifying times: the recorder, or pasted (text or a screenshot) on the commentary step |
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

Your screen recording stays where your recorder saved it. The race only remembers its path. Music for the starting grid lives in the `Music` folder next to the app.

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
- **Tools:** the **Corner Mapper** for AMS2 and RaceRoom. Drive a lap to map a track's corners and straights, so the log and commentary can say where things happened. Do it before the race is recorded (see [Map the track first](#map-the-track-first-corner-mapper)).

Settings are saved when you click **Save settings** or leave the screen.

## Graphics in detail

**Graphics setup** opens a preview of your race video:

- **Layout tab:** pick a region (leaderboard, map, logo, pop-ups, ticker, team radio) and drag a box over the video to place it.
- **Branding tab:** championship name, season, round, logo (from `Graphics\Logos`) and colours. Sponsor and manufacturer logos go here too.
- **Drivers tab:** links each name in the race log to a display name, a car image from `Graphics\CarPreviews` and a team logo from `Graphics\Logos`. **AI fill from race notes** reads your notes for numbers, countries and liveries. **Preview starting grid** shows the pre-race grid on the preview.
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
| The video won't open in **Review & sync the events** (or plays without a picture) | Windows is missing the video codecs. Install the free [K-Lite Codec Pack](https://codecguide.com/download_kl.htm) (the **Basic** version is enough), then open the review window again. |
| Something in the app itself went wrong | Details are written to `launcher_errors.log` in this folder. |

## Upgrading from the old launcher

Projects from the old launcher (`Projects\`) can be brought into **My races**. On **My races**, click **Import old projects**.

- Every race log of 5 minutes or more becomes a race.
- The files the project made are copied under the new names, so you can carry on where you left off.
- The `Projects` folder itself is not changed.

The old **Scan Files** button is gone: every step writes a fixed file name, so the app always knows what's done.

## Help and contact

Stuck, or found a bug? Find me on Discord (**Spawnwheel**), or open an issue in this repository and I'll get in touch.
