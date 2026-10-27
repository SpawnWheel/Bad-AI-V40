# AI Race Engineer for Automobilista 2 (AMS2) & RaceRoom (R3E)

An intelligent, lightweight race engineer for **Automobilista 2** and **RaceRoom Racing Experience** powered by **Google Gemini 3.8 Flash** for strategic reasoning and **Gemini 3.8 Flash TTS** for authentic, emotional team radio voice communication.

---

## Key Features

- **Live Telemetry Connection:** Connects directly to AMS2's Windows shared memory (`$pcars2$`) at **2Hz (0.5s interval)** with torn-read protection and negligible CPU usage (<0.05%).
- **Config-Driven Engineer Personas (Gemini 3.8 Voice Design):**
  - Pick who your engineer is with one line in `config.yaml` (`engineer.persona`). Presets included: GP-style British, Aussie larrikin, passionate Italian, American oval-racing engineer, Northern English strategist — or write your own.
  - Each persona sets personality, accent/dialect, language, age, gender, humour, catchphrases, banned phrases, how often he calls you "mate", and how emotional he's allowed to get.
  - The voice itself is designed once with **Gemini 3.8 Voice Design** from the persona's description (cached in `data/voice_cache.json`). Change the accent or description and a new voice is designed next launch.
- **Real Emotions, Never Scripted:**
  - Every call carries a specific emotion + delivery ("clipped and tense, rapid-fire", "quietly pleased, a smile in the voice", "controlled frustration") sent to the TTS as a style direction, plus natural vocal sounds like `<exhales>`, `<sigh>`, `<short pause>`.
  - A pit-wall **mood** builds up from race events (positions gained/lost, contact, battles, rain) and colours his reactions.
  - A **variety engine** remembers his recent openings, catchphrases and tones and steers him away from repeating himself.
  - Audition any persona with `python voice_test.py --persona <name>` (or `voice_test.bat`).
- **Lean, Smarter LLM Usage (stays on LOW thinking):**
  - Session-scoped system prompt: race/quali calls no longer carry the practice rules or the setup guide (~50% fewer input tokens per race call).
  - Structured JSON replies (transcript, intent, emotion, delivery, response) — no more fragile text parsing.
  - **Engineer's notebook:** he notes driver feedback, agreed plans and recurring problems, and remembers them beyond the last 10 radio messages.
  - Doesn't answer a plain "copy"/"thanks" — like a real engineer.
  - Optional `fast_model` for routine calls, `fallback_model` on quota/overload, request timeout, and per-call token usage in the logs (session totals on shutdown).
- **Streaming Radio (starts talking sooner, sounds the same):**
  - Gemini streams its reply; as soon as the first full sentence *and its emotion* exist, it goes to Gemini 3.8 TTS, whose audio also streams and plays on the first chunk. The rest of the reply follows as one piece while he's still talking.
  - Same model, same thinking level, same words — only the waiting is gone. At most two voice requests per reply to keep intonation natural; tiny fragments are never split off.
  - Gap-free playback with a small pre-buffer and click-free radio filter across chunks. `[LATENCY]` lines in the console show time from button to first audio.
  - Configure under `streaming:` in `config.yaml` (`enabled: false` restores the old behaviour).
- **Dual-Action Radio Button (Wheel & Keyboard):**
  - **Quick Tap (<350ms):** The engineer immediately delivers a complete **Race Situation Update (SitRep)** covering your position, gaps ahead and behind, remaining laps, fuel range, and tire conditions.
  - **Hold Down (>350ms):** Opens the team radio microphone with a tone cue. Speak your question (*"How is my fuel?"*, *"Should I box this lap?"*, *"What are my tire temps?"*), and release to get an immediate spoken reply.
- **Speak Only When Spoken To (Silent Mode):**
  - By default, the engineer is completely quiet until you press the radio button — no unprompted lap chatter or spontaneous monologues.
  - Quick tap for a complete SitRep, or hold down to ask a question.
- **Session-Specific Intelligence (Practice, Quali, Race):**
  - **Practice:** Focuses on car setup, tire temperature windows, understeer/oversteer balance, brake temps, and sector delta consistency.
  - **Qualifying:** Focuses on grid position, gap to pole or the car ahead on the timesheet, sector delta splits, and traffic gaps.
  - **Race:** Focuses on race position, live intervals ahead & behind (closing/opening), fuel laps to finish, pit stop windows, and track flags.
- **Development Interaction Logging (`logs/ai_interactions.log`):**
  - Every AI interaction is recorded with timestamps, latency, vocal emotions, full prompt & telemetry context sent to Gemini, and raw returned text.


### Setup engineering, pit calls & fuel accounting (30 Sep 2026)

- **Scientific practice engineer:** at 10 Hz the app measures handling per corner phase (entry / mid / exit) and speed band — an *understeer index* (steering needed vs the car's neutral response), front/rear slip balance, counter-steer moments, lock-ups per wheel, wheelspin on the auto-detected driven axle — plus lap-averaged tyre temps (inner/mid/outer, carcass), hot pressures, brake peaks, ride height and your driving style (trail-braking, coasting, throttle application, steering corrections). Corners are auto-mapped from your first clean lap.
- **Setup lab loop:** baseline → diagnose → one change → test → keep/revert. Every run is logged with the change made before it and compared with the previous run (median lap vs noise, handling deltas). What the engineer learns about a car (e.g. "camber is fixed") and each setup change / verdict persists in `data/setup_journal.json`, so the next session starts where you left off. New AMS2 setup guide in `reference_material/`.
- **Track awareness:** the engineer knows where you are (lap %, sector, seconds to the line and to pit entry, current/next corner). Pit entry/exit and a reference lap are learned per track (`data/track_info.json`).
- **Earlier pit calls:** heads-up at the start of the lap before, "box this lap" at the *start* of the pit lap, then a short "box, box" ~20 s before pit entry (`strategy.box_reminder_before_pit_entry_sec`).
- **Exact race distance:** "laps to go including this one" and the lap the flag falls on, following AMS2's timed-race rule (finish the lap when the clock hits zero + the extra lap). No more false fuel emergencies on the last lap.
- **Fuel ledger:** start fuel (taken at the green, not the grid), every refuel, total used, per-lap burn — so "how much fuel did I use?" gets a real answer. Burn rate and race pace learned in practice seed the race plan (`data/car_track_stats.json`).
- **Real pit-loss model:** drive-through loss (measured against the reference lap) + service time (litres ÷ learned refuel rate). Projections for rivals currently in the pits.
- Notes no longer leak between different events; tyre pressures are read in the right unit (`telemetry.tyre_pressure_unit`), sector numbers are 1-3.

---

## Getting Started

### 1. Enable Shared Memory in Automobilista 2

1. Launch **Automobilista 2**.
2. Go to **Options > System**.
3. Under **Shared Memory**, set it to **`Project CARS 2`**.
4. Save and exit settings.

**RaceRoom needs no setup** — its shared memory (`$R3E`) is always on.

### Which sim? It's detected automatically

Just double-click `run.bat` and start either game, before or after the app. The engineer checks which
game is running (`AMS2AVX.exe` / `RRRE64.exe`) and connects to it; quit one and start the other and he
follows. To force one, set `telemetry.sim: ams2` or `raceroom` in `config.yaml`.

RaceRoom notes:
- Built against KW Studios' official `r3e.h` **3.5**. The 21 Oct 2026 Historic Update ships shared
  memory **3.6** (adds start-procedure data) without a public header yet, so the app finds the
  standings with the game's own offsets and cross-checks the player data — watch the console for
  `layout check passed` / `FAILED` after you get in the car.
- Car and class names come from the game's `GameData\General\r3e-data.json` (found next to the running
  `RRRE64.exe`; set `telemetry.r3e_data_path` if needed). Without it, cars show as "RaceRoom car <id>".
- RaceRoom publishes no weather, air/track temperature, carcass temperature or collision data. The
  engineer is told so; impacts are detected from sudden damage instead. In exchange RaceRoom gives each
  car's tyre and brake **operating window**, which the engineer uses for tyre advice.
- Brake bias follows `r3e.h` (rear share). If the number he quotes is 100 minus your dash, set
  `telemetry.r3e_brake_bias_is_rear_share: false`.
- Learned data (pit loss, track map, fuel burn, setup journal) is stored per car/track name, so
  RaceRoom and AMS2 build up their own records.

### 2. Configure Your Wheel Button & API Key (One-Click Wizard)

Double-click **`setup_wheel.bat`** (or run `python detect_wheel.py`):
1. **API Key:** Paste your Google Gemini API key when prompted (saved to local `config.yaml`).
2. **Wheel Button:** Press the button on your steering wheel you want to use. The wizard automatically detects the controller and button index and saves it to `config.yaml`!

> [!NOTE]
> `config.yaml` is kept in `.gitignore` so your private API keys and wheel bindings are never committed or leaked to GitHub. A template is provided in `config.example.yaml`.

*(Or copy `config.example.yaml` to `config.yaml` manually: set `api_key: "..."` and `button_index: X`).*
```yaml
push_to_talk:
  mode: "auto"           # "auto", "wheel", or "keyboard"
  joystick_index: 0      # First detected wheel/joystick
  button_index: 0        # Button index on wheel rim (e.g. 0, 1, 4)
  keyboard_key: "caps_lock" # Global keyboard fallback
```

### 4. Run the Application

Double-click **`run.bat`** or run:
```bash
python main.py
```

Then launch AMS2 and start driving! The app will automatically connect as soon as you hit the track.

---

## Configuration Reference (`config.yaml`)

| Setting | Description | Default |
|---|---|---|
| `gemini.thinking_model` | AI model for race analysis and voice Q&A | `gemini-3.8-flash` |
| `gemini.temperature` | Wording creativity (Gemini 3.x is tuned for 1.0) | `1.0` |
| `streaming.enabled` | Stream LLM text + TTS audio, speak on first chunk | `true` |
| `streaming.split_first_sentence` | Start speaking after the first full sentence | `true` |
| `streaming.min_first_sentence_words` | Never split off shorter fragments | `5` |
| `streaming.prebuffer_ms` | Audio buffered before playback starts | `150` |
| `gemini.structured_output` | JSON replies with intent + notebook | `true` |
| `gemini.scoped_system_prompt` | Only send the current session's rules | `true` |
| `gemini.fast_model` | Cheaper model for routine proactive calls (`""` = off) | `""` |
| `gemini.fallback_model` | Retry model on quota/overload (`""` = off) | `""` |
| `gemini.request_timeout_sec` | Abandon hung LLM calls after this long | `20` |
| `engineer.notebook_size` | Facts the engineer keeps in his notebook | `8` |
| `tts.model` | Voice synthesis model | `gemini-3.8-flash-tts` |
| `tts.fallback_models` | Used for the rest of a session if the main model hits quota | `[gemini-3.8-flash-lite-tts]` |
| `tts.legacy_fallback_model` | Last-resort older TTS (`""` to disable) | `gemini-3.1-flash-tts-preview` |
| `engineer.persona` | Which preset from `personas:` to use | `gp_british` |
| `engineer.persona_overrides` | One-off tweaks on top of the preset | `{}` |
| `engineer.emotion_mode` | `dynamic`, `calm`, or `urgent` | `dynamic` |
| `personas.<name>.accent` | Accent/dialect for wording *and* voice | — |
| `personas.<name>.expressiveness` | `subtle`, `natural`, `expressive` | `natural` |
| `personas.<name>.voice.mode` | `designed`, `prebuilt`, or `voice_id` | `designed` |
| `personas.<name>.voice.description` | Voice-design prompt (age, timbre, accent only) | — |
| `push_to_talk.button_index` | Wheel button for radio | `0` |
| `push_to_talk.keyboard_key` | Keyboard fallback hotkey | `caps_lock` |
| `telemetry.poll_rate_hz` | Shared memory polling rate in Hz | `2` (0.5s interval) |
| `engineer.verbosity` | Chatter level: `minimal`, `balanced`, or `verbose` | `balanced` |
| `engineer.quiet_zone_braking` | Delays non-critical speech in heavy braking | `true` |
| `engineer.radio_effect` | Applies radio bandpass audio filter | `false` |

See the comments above `personas:` in `config.example.yaml` for every persona field.
