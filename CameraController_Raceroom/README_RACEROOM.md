# RaceRoom Camera Controller

Plays back a Gemini camera sequence in a RaceRoom replay: it follows the right driver and switches to the right camera at each timecode, and writes director notes as it goes. It works like the AMS2 Camera Controller, with two extras: camera selection and checked driver switching.

## Driver switching
RaceRoom's Driver Ahead/Behind keys (Numpad + / −) do nothing in replays, but the **◀ ▶ arrows next to the driver name** on the replay bar do. For each switch, the controller:

1. shows the replay bar with **H** (Hide UI) if it's hidden,
2. counts how many clicks the target driver is away, the short way round (◀ or ▶), and clicks them in one quick burst,
3. checks once which driver is in view, and recounts from there if it landed on someone else,
4. hides the bar again.

A switch takes about a second including showing and hiding the bar. RaceRoom takes every click, even in a fast burst.

The ▶ arrow steps through the field in race order: P1, then last place, then back up to P2. Retired cars are skipped. The controller gets that order from the logger's timing file (`Race_*_timing.jsonl` next to the race log), at the replay's current point in the race. Without a timing file, it learns the order instead when you press **Play Sequence**: one lap of ▶ while the replay is still paused, about 2 seconds. **Learn Drivers** does the same by hand.

Things to know:
- Some replays never fill in RaceRoom's shared field list. The controller doesn't need it: in a replay, the shared memory's player name is always the driver in view.
- The sequence clock has to match the replay (use the time offset). If it's a little off, the check after the burst catches it and a short correction follows.
- RaceRoom sometimes leaves one running car out of the arrow cycle for a while. If that car is the target, the controller stays on the driver it reached (next to the target) and tries again at that driver's next shot. It doesn't pause.
- The arrow positions are measured on a 1920×1080 RaceRoom window and scale with the window size.
- **Pause for manual pick** (off by default) pauses the replay instead when a switch fails. Pick the driver yourself, then press Space to carry on.

Before a full run, use **Check Focused Car** and **Go To Driver...** in a replay to confirm switching works.

## Cameras
Cameras switch with RaceRoom's own hotkeys, so camera changes never bring up the replay bar:

| Key | What it does in a replay |
|---|---|
| Page Down | back to the last external camera |
| End | next external camera: TV -> Action -> Aerial -> Static (only while external) |
| Home | back to the last onboard camera; each further press goes to the next one |
| Insert | Hood; each further press goes Hood -> Nose -> Chase -> Cockpit |

RaceRoom never reports which camera is showing, so the controller keeps count. When you press **Play Sequence** it first sets a known state while the replay is still paused: Onboard 1, then TV, picked from the replay bar's camera menu. **Sync Cameras** does the same by hand, which is useful if you change camera yourself.

Things to know:
- Some changes step through another camera on the way, for example TV -> Aerial passes Action, and Cockpit is reached via Hood, Nose and Chase. Presses are 14 ms apart (RaceRoom registers them reliably at that speed), so each in-between camera shows for one frame at most at 60 fps.
- Going back down the onboard list (Onboard 2 -> Onboard 1) would mean going all the way round, so that one switch uses the menu instead.
- **Switch with: Replay menu** picks every camera from the menu instead. It never flashes another camera, but the bar shows for about a second each time.

By default the director uses:
- **External:** TV is the default camera. Aerial is an occasional wide shot (about 10% of the race).
- **Inside:** Onboard 1 and Onboard 2 (both face forwards), and Cockpit.

**Choose Cameras...** changes which cameras Gemini may use.

**Rule:** the director never cuts straight from one inside camera to another, for example Onboard 1 to Onboard 2, or an onboard to Cockpit. It always goes through an external camera, as in Onboard 1 -> TV -> Onboard 2. The prompt tells Gemini this, and the controller also fixes any sequence that breaks the rule when it loads, swapping the offending shot for TV. Each fix is listed in the Activity Log.

## Usage
1. Open the replay in RaceRoom and pick the RaceRoom window (click `R` to refresh).
2. **Generate (Gemini)** from the race log, or **Load JSON**. The **Gemini Thinking** window shows the model's reasoning live as it streams. The model and thinking level are set next to the Generate button (default gemini-3.8-flash, HIGH).
3. Sync: put the log's green flag time in *Log Green Flag*, click **Capture Sim GF** when the replay shows the green flag, then **Apply Sync**.
4. **Play Sequence** syncs the cameras, then starts the RaceRoom replay and the sequence together. **Stop Sequence** pauses the replay. Space pauses and resumes both.

Settings are saved in `config_raceroom.json`. *Gap* is the longest wait for RaceRoom to react to a click.
