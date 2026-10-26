# RaceRoom Camera Controller

Plays back a Gemini camera sequence in a RaceRoom replay: it follows the right driver and switches to the right camera at each timecode, and writes director notes as it goes. It works like the AMS2 Camera Controller, with two extras: camera selection and checked driver switching.

## Driver switching
RaceRoom has no leaderboard you can step through like AMS2. Instead, the controller:

1. reads RaceRoom's shared memory to find the target driver and the car currently on screen,
2. presses **Car Ahead** (default `Num +`) or **Car Behind** (default `Num -`),
3. re-reads the focused car after every press and stops once the target is on screen.

If the keys don't change the focused car, or the driver can't be reached, it pauses the replay (when **Pause for manual pick** is ticked). Pick the driver yourself, then press Space to carry on.

Before a full run, use **Check Focused Car** and **Go To Driver...** in a replay to confirm switching works.

## Cameras
Each category (External, Onboard, Driver) has an entry key and a cycle key. The controller remembers which camera each category was left on and cycles from there. If you change camera by hand in the game, click **Reset Camera Memory** while every category is on its first camera.

**Edit Cameras...** lets you record keys, rename cameras, and untick cameras you don't want. The ticked cameras are the only ones offered to Gemini.

## Usage
1. Open the replay in RaceRoom and pick the RaceRoom window (click `R` to refresh).
2. **Generate (Gemini)** from the race log, or **Load JSON**.
3. Sync: put the log's green flag time in *Log Green Flag*, click **Capture Sim GF** when the replay shows the green flag, then **Apply Sync**.
4. **Play Sequence**. Space pauses and resumes.

Settings are saved in `config_raceroom.json`. *Gap* is the wait between key presses, so raise it if RaceRoom misses presses.
