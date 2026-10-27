import ctypes
import time
import os
import datetime
import random
import json

from r3e_shared import (
    R3ESharedMemory, decode, VERSION_MAJOR, SESSION_NAMES, SESSION_RACE,
    PHASE_GARAGE, PHASE_GRIDWALK, PHASE_FORMATION, PHASE_COUNTDOWN, PHASE_GREEN, PHASE_CHECKERED,
    FINISH_FINISHED, FINISH_DNF, FINISH_DNS, FINISH_DQ,
    LENGTH_LAPS, LENGTH_TIME_AND_LAP, PENALTY_REASONS,
)
from leaderboard_recorder import LeaderboardRecorder

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Windows Virtual Key Code for F12
VK_F12 = 0x7B

POLL_INTERVAL = 0.25

# The running order must hold this long before overtakes are counted (side-by-side cars flicker)
ORDER_SETTLE_TIME = 1.0

# Fastest lap events are only announced once the leader has completed this many laps
FASTEST_LAP_MIN_RACE_LAPS = 5

PHASE_NAMES = {
    PHASE_GARAGE: "in the Garage",
    PHASE_GRIDWALK: "on the Grid",
    PHASE_FORMATION: "on the Formation Lap",
    PHASE_COUNTDOWN: "Counting Down",
}

# Penalty fields in the per-driver penalties struct (-1.0 = none pending) -> (display name, penaltyType)
PENALTY_FIELDS = {
    "drive_through": ("Drive-Through", 0),
    "stop_and_go": ("Stop-Go", 1),
    "pit_stop": ("Pit Stop", 2),
    "time_deduction": ("Time", 3),
    "slow_down": ("Slow-Down", 4),
}


class SimClock:
    """Elapsed session time in seconds, driven by the game's own physics clock.

    RaceRoom's physics clock does not always advance in replays, so when it stalls
    while cars are visibly moving and the game is not paused, wall-clock deltas are
    used instead. Never jumps: each tick adds at most a few seconds.
    """
    def __init__(self):
        self.elapsed = 0.0
        self.last_sim = None
        self.last_wall = None
        self.using_wall_clock = False

    def reset(self):
        self.elapsed = 0.0
        self.last_sim = None
        self.last_wall = None

    def tick(self, sim_time, paused, cars_moving):
        wall = time.monotonic()
        if self.last_sim is not None:
            sim_delta = sim_time - self.last_sim
            wall_delta = wall - self.last_wall
            if 0.0 < sim_delta < 5.0:
                self.elapsed += sim_delta
                self.using_wall_clock = False
            elif not paused and cars_moving and 0.0 < wall_delta < 5.0:
                self.elapsed += wall_delta
                self.using_wall_clock = True
        self.last_sim = sim_time
        self.last_wall = wall
        return self.elapsed


class R3ELookup:
    """Car, class, team and manufacturer names from RaceRoom's content data (r3e-data.json)."""
    def __init__(self):
        self.cars = {}
        self.classes = {}
        self.liveries = {}
        self.teams = {}
        self.manufacturers = {}
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "r3e-data.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cars = data.get("cars", {})
            self.classes = data.get("classes", {})
            self.liveries = data.get("liveries", {})
            self.teams = data.get("teams", {})
            self.manufacturers = data.get("manufacturers", {})
        except Exception as e:
            print(f"Warning: could not load car names from {path}: {e}")

        self.learned_cars = {} # model_id -> name, for content newer than r3e-data.json
        self.learned_classes = {} # class_id -> name

    def learn(self, info):
        """The focused car's info carries its full car name, which covers cars missing from r3e-data.json."""
        name = decode(info.name)
        if name and str(info.model_id) not in self.cars:
            self.learned_cars[info.model_id] = name
            if str(info.class_id) not in self.classes:
                self.learned_classes[info.class_id] = name

    def car_name(self, model_id):
        return self.cars.get(str(model_id), {}).get("Name") or self.learned_cars.get(model_id, "Unknown Car")

    def class_name(self, class_id):
        """Class name, or "" when RaceRoom's data doesn't know it."""
        return self.classes.get(str(class_id), {}).get("Name") or self.learned_classes.get(class_id, "")

    def team_name(self, team_id, livery_id):
        """Team from the team id, else the livery's team, or "" when unknown."""
        team = self.teams.get(str(team_id), {}).get("Name") or self.liveries.get(str(livery_id), {}).get("TeamName")
        return (team or "").strip()

    def manufacturer_name(self, manufacturer_id, model_id):
        name = self.manufacturers.get(str(manufacturer_id), {}).get("Name")
        return (name or self.cars.get(str(model_id), {}).get("BrandName") or "").strip()


class RaceroomEventLogger:
    def __init__(self):
        self.shm = R3ESharedMemory()
        self.header = None
        self.lookup = R3ELookup()
        self.clock = SimClock()
        self.log_file = None
        self.recorder = None # Live timing for the leaderboard graphic (replays share no driver data)
        self.wall_clock_noted = False
        self.version_warned = False

        self.last_session_key = None
        self.last_phase = None
        self.last_track_key = None
        self.track_length = 0.0
        self.last_leaderboard_time = None
        self.green_flag_time = None
        self.green_flag_fired = False
        self.checkered_flag_shown = False
        self.manual_timer_expired = False # Set True when F12 is pressed
        self.f12_was_pressed = False # Tracks the previous state of the F12 key
        self.yellow_sectors = set()
        self.yellow_clear_since = None

        self.reset_driver_state()

        self.landmarks = self.load_landmarks()
        self.current_landmarks = []

    def reset_driver_state(self):
        self.drivers = {} # slot_id -> latest driver dict
        self.last_positions = {} # name -> pos
        self.confirmed_positions = {} # name -> pos, once the order has held for ORDER_SETTLE_TIME
        self.pending_order = None # (positions, sim_time first seen)
        self.last_laps_completed = {} # name -> completed laps
        self.last_lap_valid = {} # name -> current_lap_valid on the previous tick
        self.last_finish_status = {} # name -> finish_status
        self.last_in_pitlane = {} # name -> 0/1
        self.pit_stopped = {} # name -> True once stationary in the pit lane this visit
        self.last_penalties = {} # name -> {field: bool pending}
        self.finished_participants = set() # set of names who have finished or retired
        self.assumed_retired_participants = set() # set of names assumed retired due to no movement
        self.last_movement_time = {} # name -> sim_time of last movement
        self.last_overtake_time = {} # name -> sim_time of last overtake
        self.accidents = {} # name -> sim_time when accident started
        self.battles = {} # (name1, name2) -> {"type": str, "time": float, "count": int}
        self.distance_history = {} # name -> list of (sim_time, total_distance)
        self.best_laps = {} # name -> (lap_time, lap_number)
        self.last_lap_times = {} # name -> last completed lap time
        self.fastest_lap = None # {"name", "time", "lap"} - best valid lap of the race so far
        self.fastest_lap_announced = False

    # ------------------------------------------------------------------ landmarks

    def load_landmarks(self):
        """RaceRoom corner landmarks keyed by layout id. Corner Names/ entries win over the older 1_Logger copy."""
        landmarks = {}
        for path in (os.path.join(APP_DIR, "1_Logger", "trackLandmarksData.json"),
                     os.path.join(APP_DIR, "Corner Names", "raceroomTrackLandmarksData.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for track in data.get("TrackLandmarksData", []):
                    layout_id = track.get("raceroomLayoutId")
                    if layout_id and track.get("trackLandmarks"):
                        landmarks[int(layout_id)] = track["trackLandmarks"]
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"Warning: could not read landmarks from {path}: {e}")
        return landmarks

    def get_corner_at_distance(self, distance):
        for lm in self.current_landmarks:
            start = lm.get("distanceRoundLapStart", 0)
            end = lm.get("distanceRoundLapEnd", 0)
            name = lm.get("landmarkName", "").replace("_", " ").title()
            if start <= end:
                if start <= distance <= end:
                    return name
            elif distance >= start or distance <= end: # Wraps over the start/finish line
                return name
        return None

    # ------------------------------------------------------------------ logging

    def start_logging(self):
        project_path = os.environ.get("R3E_PROJECT_PATH")
        if project_path:
            log_dir = os.path.join(project_path, "Logs")
            os.makedirs(log_dir, exist_ok=True)
        else:
            log_dir = "."

        filename = f"Race_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filepath = os.path.join(log_dir, filename)
        self.log_file = open(filepath, "w", encoding="utf-8")
        self.log("SYSTEM", f"New log file started: {os.path.abspath(filepath)}")
        try:
            self.recorder = LeaderboardRecorder(filepath, self.lookup, decode)
            print(f"Recording live timing for the leaderboard graphic: {self.recorder.path}")
        except Exception as e:
            print(f"Live timing for the leaderboard graphic is off ({e}); the race log is unaffected.")

    def log(self, category, message, sim_time=None):
        ts_val = max(0, int(self.clock.elapsed if sim_time is None else sim_time))
        log_line = f"{datetime.timedelta(seconds=ts_val)} - [{category}] {message}"
        print(log_line)
        if self.log_file:
            self.log_file.write(log_line + "\n")
            self.log_file.flush()

    def check_f12_key(self):
        """Check if F12 has been pressed (non-blocking, edge-triggered)."""
        try:
            is_pressed = (ctypes.windll.user32.GetAsyncKeyState(VK_F12) & 0x8000) != 0
            if is_pressed and not self.f12_was_pressed:
                self.f12_was_pressed = True
                return True
            if not is_pressed:
                self.f12_was_pressed = False
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------ main update

    def read_drivers(self, raw_drivers):
        drivers = []
        for d in raw_drivers:
            info = d.driver_info
            name = decode(info.name)
            if not name or d.place <= 0:
                continue
            drivers.append({
                "slot": info.slot_id,
                "name": name,
                "pos": d.place,
                "car": self.lookup.car_name(info.model_id),
                "team": self.lookup.team_name(info.team_id, info.livery_id),
                "manufacturer": self.lookup.manufacturer_name(info.manufacturer_id, info.model_id),
                "class": self.lookup.class_name(info.class_id),
                "dist": max(0.0, d.lap_distance),
                "laps": max(0, d.completed_laps),
                "lap_valid": d.current_lap_valid,
                "prev_lap_time": d.sector_time_previous_self[2],
                "speed": d.car_speed,
                "pit": d.in_pitlane,
                "finish": d.finish_status,
                "gap_front": d.time_delta_front,
                "penalties": {f: getattr(d.penalties, f) >= 0.0 for f in PENALTY_FIELDS},
                "penalty_type": d.penalty_type,
                "penalty_reason": d.penalty_reason,
            })
        return drivers

    def update(self):
        header, raw_drivers = self.shm.read()
        if header is None:
            return False
        self.header = header

        if header.version_major != VERSION_MAJOR and not self.version_warned:
            self.version_warned = True
            self.log("SYSTEM", f"Warning: RaceRoom shared memory version {header.version_major}.{header.version_minor} "
                               f"is newer than this logger expects ({VERSION_MAJOR}.x). Data may be wrong.")

        self.lookup.learn(header.vehicle_info)
        drivers = self.read_drivers(raw_drivers)
        cars_moving = any(d["speed"] > 1.0 for d in drivers)
        sim_now = self.clock.tick(header.player.game_simulation_time, header.game_paused == 1, cars_moving)
        if self.clock.using_wall_clock and not self.wall_clock_noted:
            self.wall_clock_noted = True
            self.log("SYSTEM", "Game physics clock is not advancing (replay?) - timing this session from the real-time clock instead.")

        # Track Identification & Reporting
        track_name = decode(header.track_name)
        layout_name = decode(header.layout_name)
        track_key = (header.track_id, header.layout_id)
        if track_name and track_key != self.last_track_key:
            self.last_track_key = track_key
            self.track_length = header.layout_length
            track_display = f"{track_name} - {layout_name}".strip(" -")
            self.log("TRACK", f"Track Loaded: {track_display}")
            if header.layout_length > 0:
                self.log("TRACK", f"Track Length: {header.layout_length:.1f} meters")
            self.current_landmarks = self.landmarks.get(header.layout_id, [])
            if self.current_landmarks:
                self.log("SYSTEM", f"Loaded {len(self.current_landmarks)} corner landmarks for {track_display}")
            else:
                self.log("SYSTEM", f"Notice: No corner landmarks found for layout {header.layout_id} ({track_display}).")

        # Session
        session_key = (header.session_type, header.session_iteration)
        if session_key != self.last_session_key:
            if self.last_session_key is not None:
                self.clock.reset()
                sim_now = 0.0
            self.last_session_key = session_key
            self.log("SESSION", f"Session Changed: We are now in {SESSION_NAMES.get(header.session_type, 'Unknown')}.")
            self.last_phase = None
            self.green_flag_fired = False
            self.green_flag_time = None
            self.checkered_flag_shown = False
            self.manual_timer_expired = False
            self.yellow_sectors = set()
            self.reset_driver_state()

        is_race = header.session_type == SESSION_RACE

        # Replay rewound: most cars' lap counts went backwards, so forget per-driver history
        went_back = sum(1 for d in drivers if d["laps"] < self.last_laps_completed.get(d["name"], d["laps"]))
        if drivers and went_back > len(drivers) // 2:
            self.log("SYSTEM", "Replay jumped backwards - resetting driver tracking.")
            self.reset_driver_state()

        # Phase Updates
        phase = header.session_phase
        if phase != self.last_phase:
            if phase in PHASE_NAMES:
                self.log("SESSION", f"Phase Update: {PHASE_NAMES[phase]}")
            if phase == PHASE_COUNTDOWN and is_race:
                self.log_race_distance(header)
            elif phase == PHASE_GREEN and is_race and not self.green_flag_fired:
                self.green_flag_fired = True
                self.green_flag_time = sim_now
                self.last_leaderboard_time = sim_now
                self.log_starting_grid(drivers)
                self.log("SESSION", "Green Flag! The race begins!")
            elif phase == PHASE_CHECKERED and is_race:
                self.checkered_flag_shown = True
            self.last_phase = phase

        # Periodic Leaderboard Updates (every 4 minutes)
        if is_race and phase == PHASE_GREEN and self.last_leaderboard_time is not None:
            if sim_now - self.last_leaderboard_time > 240:
                self.log_leaderboard(drivers, "Current Standings", sim_now)
                self.last_leaderboard_time = sim_now

        self.check_yellow_flags(header, sim_now)

        # F12 manual timer trigger
        if self.check_f12_key() and not self.manual_timer_expired:
            self.manual_timer_expired = True
            self.log("SESSION", "Manual race end triggered (F12). Waiting for P1 to cross the line.")

        # Time/lap limit reached and the leader crossed the line = chequered flag (fallback when the phase lags)
        if is_race and not self.checkered_flag_shown and phase == PHASE_GREEN:
            for d in drivers:
                if d["pos"] == 1 and d["laps"] > self.last_laps_completed.get(d["name"], d["laps"]):
                    laps_up = header.number_of_laps > 0 and header.session_length_format == LENGTH_LAPS and d["laps"] >= header.number_of_laps
                    time_up = header.session_time_duration > 0 and 0 <= header.session_time_remaining <= 0.0
                    if laps_up or (time_up and header.session_length_format != LENGTH_TIME_AND_LAP) or self.manual_timer_expired:
                        self.checkered_flag_shown = True

        for d in drivers:
            self.update_driver(d, sim_now, is_race, phase)

        # Disconnected / removed cars
        if self.last_positions and is_race:
            current = {d["name"] for d in drivers}
            for name in set(self.last_positions) - current:
                if name not in self.finished_participants:
                    self.log("SESSION", f"{name} has retired and left the session.")
                    self.finished_participants.add(name)

        if is_race and phase == PHASE_GREEN:
            self.check_overtakes(drivers, sim_now)
            self.check_fastest_lap(drivers, sim_now)
            self.check_accidents(drivers, sim_now)
            self.check_battles(drivers, sim_now)

        if is_race and self.recorder:
            self.recorder.update(header, raw_drivers, sim_now, SESSION_NAMES.get(header.session_type, "Unknown"))

        self.last_positions = {d["name"]: d["pos"] for d in drivers}
        self.last_laps_completed = {d["name"]: d["laps"] for d in drivers}
        self.last_lap_valid = {d["name"]: d["lap_valid"] for d in drivers}
        self.drivers = {d["slot"]: d for d in drivers}
        return True

    def log_race_distance(self, header):
        if header.session_length_format == LENGTH_LAPS and header.number_of_laps > 0:
            self.log("SESSION", f"Race Distance: {header.number_of_laps} laps.")
        elif header.session_time_duration > 0:
            minutes = int(round(header.session_time_duration / 60))
            extra = " plus one lap" if header.session_length_format == LENGTH_TIME_AND_LAP else ""
            self.log("SESSION", f"Race Duration: {minutes} minutes{extra}.")

    def check_yellow_flags(self, header, sim_now):
        sectors = {i + 1 for i, v in enumerate(header.flags.sector_yellow) if v == 1}
        for sector in sorted(sectors - self.yellow_sectors):
            self.log("FLAG", f"Flag: Yellow in sector {sector}")
        if sectors:
            self.yellow_clear_since = None
        elif self.yellow_sectors:
            self.yellow_clear_since = sim_now
        # Only announce the all-clear once it has stayed clear for a few seconds (yellows flicker)
        if not sectors and self.yellow_clear_since is not None and sim_now - self.yellow_clear_since >= 5.0:
            self.log("FLAG", "Flag: Green again (Yellow cleared).")
            self.yellow_clear_since = None
        self.yellow_sectors = sectors

    # ------------------------------------------------------------------ per-driver events

    def update_driver(self, d, sim_now, is_race, phase):
        name, pos = d["name"], d["pos"]
        old_laps = self.last_laps_completed.get(name, d["laps"])
        crossed_line = d["laps"] > old_laps

        if is_race:
            self._record_distance_history(name, sim_now, self.total_distance(d))

        # Lap times: the previous-lap time is cumulative sector 3
        if crossed_line:
            lap_time = d["prev_lap_time"]
            if lap_time > 10.0:
                self.last_lap_times[name] = lap_time
                was_valid = self.last_lap_valid.get(name, 1) == 1
                best = self.best_laps.get(name)
                if was_valid and (best is None or lap_time < best[0]):
                    self.best_laps[name] = (lap_time, d["laps"])

        if not is_race:
            return

        # Retirement by lack of movement (2 mins), only once the race is running
        if phase == PHASE_GREEN and d["speed"] < 2.0 and not d["pit"]:
            if name not in self.last_movement_time:
                self.last_movement_time[name] = sim_now
            elif sim_now - self.last_movement_time[name] >= 120.0 and name not in self.finished_participants:
                self.log("SESSION", f"{name} has not moved in 2 minutes and is assumed retired.")
                self.finished_participants.add(name)
                self.assumed_retired_participants.add(name)
                self.accidents.pop(name, None)
        elif d["speed"] >= 2.0 and name in self.last_movement_time:
            del self.last_movement_time[name]
            if name in self.assumed_retired_participants:
                self.assumed_retired_participants.discard(name)
                self.finished_participants.discard(name)
                self.log("SESSION", f"Notice: {name} was assumed retired but has started moving again!")

        # Finish / terminal states, straight from the game
        old_finish = self.last_finish_status.get(name, d["finish"])
        if d["finish"] != old_finish and name not in self.finished_participants:
            if d["finish"] == FINISH_FINISHED:
                self.log_finish(name, pos)
            elif d["finish"] == FINISH_DNF:
                self.log("SESSION", f"{name} has retired from the race.")
                self.finished_participants.add(name)
            elif d["finish"] == FINISH_DQ:
                self.log("SESSION", f"{name} has been disqualified.")
                self.finished_participants.add(name)
            elif d["finish"] == FINISH_DNS:
                self.log("SESSION", f"{name} did not start.")
                self.finished_participants.add(name)
        elif crossed_line and self.checkered_flag_shown and name not in self.finished_participants:
            # Fallback when finish_status lags behind the line crossing
            self.log_finish(name, pos)
        self.last_finish_status[name] = d["finish"]

        if name not in self.finished_participants:
            self.check_pits(d)
            self.check_penalties(d)

    @staticmethod
    def class_suffix(d):
        return f" in {d['class']}" if d["class"] else ""

    def log_finish(self, name, pos):
        if pos == 1:
            self.log("FINISH", f"Checkered Flag! {name} took P1!")
            self.checkered_flag_shown = True
        else:
            self.log("FINISH", f"Checkered Flag! {name} comes past the line to take P{pos}.")
        self.finished_participants.add(name)

    def check_pits(self, d):
        name = d["name"]
        was_in = self.last_in_pitlane.get(name, d["pit"])
        if d["pit"] and not was_in:
            self.pit_stopped[name] = False
            self.log("PIT", f"{name} has entered the pit lane.")
        elif d["pit"] and was_in:
            if not self.pit_stopped.get(name) and d["speed"] < 0.5:
                self.pit_stopped[name] = True
                self.log("PIT", f"{name} has arrived at their pit box.")
            elif self.pit_stopped.get(name) is True and d["speed"] > 3.0:
                self.pit_stopped[name] = "leaving"
                self.log("PIT", f"{name} is leaving the pits.")
        elif not d["pit"] and was_in:
            if self.pit_stopped.get(name) is False:
                # Drove straight through (e.g. a drive-through penalty)
                self.log("PIT", f"{name} is leaving the pits.")
            self.pit_stopped.pop(name, None)
        self.last_in_pitlane[name] = d["pit"]

    def check_penalties(self, d):
        name = d["name"]
        old = self.last_penalties.get(name, d["penalties"])
        for field, (label, ptype) in PENALTY_FIELDS.items():
            if d["penalties"][field] and not old[field]:
                reason = ""
                if d["penalty_type"] == ptype:
                    why = PENALTY_REASONS.get(ptype, {}).get(d["penalty_reason"])
                    reason = f" for {why}" if why else ""
                self.log("PENALTY", f"{name} (P{d['pos']}) has been given a {label} penalty{reason}!")
            elif old[field] and not d["penalties"][field]:
                self.log("PENALTY", f"{name} has served their penalty.")
        self.last_penalties[name] = d["penalties"]

    # ------------------------------------------------------------------ field-wide events

    def total_distance(self, d):
        length = self.track_length if self.track_length > 0 else 4000.0
        return d["laps"] * length + d["dist"]

    def check_overtakes(self, drivers, sim_now):
        order = {d["name"]: d["pos"] for d in drivers}
        if not self.confirmed_positions:
            self.confirmed_positions = order
            return
        if self.pending_order is None or self.pending_order[0] != order:
            self.pending_order = (order, sim_now)
        if order == self.confirmed_positions or sim_now - self.pending_order[1] < ORDER_SETTLE_TIME:
            return
        previous, self.confirmed_positions = self.confirmed_positions, order
        new_positions = {d["name"]: d for d in drivers}

        def ignored(name):
            d = new_positions.get(name)
            return name in self.finished_participants or (d is not None and d["pit"])

        for name, d in new_positions.items():
            if ignored(name) or name not in previous:
                continue
            old_pos, pos = previous[name], d["pos"]
            if pos >= old_pos or old_pos == 0:
                continue
            overtaken = []
            for other, other_old_pos in previous.items():
                if other == name or ignored(other):
                    continue
                other_new = new_positions.get(other)
                if other_new and other_old_pos < old_pos and other_new["pos"] > pos:
                    overtaken.append(other)
            if overtaken:
                corner = self.get_corner_at_distance(d["dist"])
                location_str = f" at {corner}" if corner else ""
                self.log("OVERTAKE", f"{name} overtook {', '.join(overtaken)} for P{pos}{self.class_suffix(d)}{location_str}.")
                self.last_overtake_time[name] = sim_now
                for o_name in overtaken:
                    self.last_overtake_time[o_name] = sim_now

    @staticmethod
    def format_lap_time(seconds):
        minutes, secs = divmod(seconds, 60)
        return f"{int(minutes)}:{secs:06.3f}"

    def check_fastest_lap(self, drivers, sim_now):
        """Tracks the race's fastest lap and logs it once the leader has done enough laps."""
        positions = {d["name"]: d["pos"] for d in drivers}
        best = None
        for name, (lap_time, lap_no) in self.best_laps.items():
            if name in positions and (best is None or lap_time < best[1]):
                best = (name, lap_time, lap_no)
        if best is None:
            return

        previous = self.fastest_lap
        improved = previous is None or best[1] < previous["time"] - 0.0005
        if improved:
            self.fastest_lap = {"name": best[0], "time": best[1], "lap": best[2]}

        if max((d["laps"] for d in drivers), default=0) < FASTEST_LAP_MIN_RACE_LAPS:
            return

        current = self.fastest_lap
        name = current["name"]
        lap_str = self.format_lap_time(current["time"])
        lap_no = f" on lap {current['lap']}" if current["lap"] > 0 else ""
        pos = positions.get(name, 0)
        if not self.fastest_lap_announced:
            # First announcement: the best lap set during the opening laps
            self.fastest_lap_announced = True
            self.log("FASTEST_LAP", f"{name} (P{pos}) holds the fastest lap of the race: {lap_str}{lap_no}.")
        elif improved:
            prev_str = ""
            if previous:
                gain = previous["time"] - current["time"]
                prev_holder = "their own" if previous["name"] == name else f"{previous['name']}'s"
                prev_str = f", {gain:.3f}s quicker than {prev_holder} {self.format_lap_time(previous['time'])}"
            self.log("FASTEST_LAP", f"{name} (P{pos}) sets a new fastest lap of the race: {lap_str}{lap_no}{prev_str}.")

    def check_accidents(self, drivers, sim_now):
        # COOLDOWN: No accidents for the first 20 seconds of the race
        if self.green_flag_time is not None and sim_now - self.green_flag_time < 20:
            return
        for d in drivers:
            name = d["name"]
            if name in self.finished_participants or d["pit"]:
                self.accidents.pop(name, None)
                continue
            if d["speed"] < 2.0 and d["dist"] > 10: # Stopped on track
                if name not in self.accidents:
                    self.accidents[name] = sim_now
                    corner = self.get_corner_at_distance(d["dist"])
                    location_str = f" at {corner}" if corner else ""
                    self.log("ACCIDENT", f"Alert: {name} (P{d['pos']}{self.class_suffix(d)}) is slow/stopped on track{location_str}! Potential accident.")
            elif name in self.accidents and d["speed"] > 5.0:
                del self.accidents[name]
                self.log("ACCIDENT", f"Notice: {name} is back on the move.")

    def check_battles(self, drivers, sim_now):
        ordered = sorted(drivers, key=lambda x: x["pos"])
        for ahead, behind in zip(ordered, ordered[1:]):
            name1, pos1 = ahead["name"], ahead["pos"]
            name2, pos2 = behind["name"], behind["pos"]

            # Ignore drivers who have finished, are on the opening lap, or are in the pits
            if name1 in self.finished_participants or name2 in self.finished_participants:
                continue
            if ahead["laps"] < 1 or behind["laps"] < 1:
                continue
            if ahead["pit"] or behind["pit"]:
                continue

            dist_diff = abs(self.total_distance(ahead) - self.total_distance(behind))
            battle_key = tuple(sorted([name1, name2]))
            battle_info = self.battles.get(battle_key)

            since_ot = min(sim_now - self.last_overtake_time.get(name1, -1e9),
                           sim_now - self.last_overtake_time.get(name2, -1e9))

            # Prevent flybys (> 36 km/h difference) and ghost battles straight after an overtake
            if abs(ahead["speed"] - behind["speed"]) > 10.0 or since_ot < 20.0:
                continue

            if dist_diff < 2.5:
                if not battle_info or battle_info["type"] != "Side by side" or (sim_now - battle_info["time"] > 15.0):
                    count = battle_info["count"] + 1 if (battle_info and battle_info["type"] == "Side by side") else 1
                    if count > 1:
                        self.log("BATTLE", f"{name2} (P{pos2}) and {name1} (P{pos1}) are still battling side by side!")
                    else:
                        self.log("BATTLE", f"Side by side! {name2} (P{pos2}) is fighting {name1} (P{pos1})!")
                    self.battles[battle_key] = {"type": "Side by side", "time": sim_now, "count": count}
            elif dist_diff < 15.0:
                if not battle_info or (sim_now - battle_info["time"] > 15.0):
                    count = battle_info["count"] + 1 if (battle_info and battle_info["type"] == "Drafting") else 1
                    drafter, d_pos, leader, l_pos = name2, pos2, name1, pos1
                    messages = [
                        f"{drafter} (P{d_pos}) is in the draft of {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is still pressuring {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is looking for a way to make the pass on {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is still battling with {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is glued to the back of {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is stalking {leader} (P{l_pos}) through the corners.",
                        f"{drafter} (P{d_pos}) is trying to force a mistake from {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is right in the wheel tracks of {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is refusing to let {leader} (P{l_pos}) escape.",
                        f"{drafter} (P{d_pos}) is all over the back of {leader} (P{l_pos}), looking for an opening.",
                        f"{drafter} (P{d_pos}) is closing in on {leader} (P{l_pos}) under braking.",
                        f"{drafter} (P{d_pos}) is keeping the pressure on {leader} (P{l_pos}) lap after lap.",
                        f"{drafter} (P{d_pos}) has the pace to challenge {leader} (P{l_pos}) here.",
                        f"{drafter} (P{d_pos}) is filling the mirrors of {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is using the slipstream to stay right with {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is biding their time behind {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is probing for a gap on {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is turning up the heat on {leader} (P{l_pos}).",
                        f"{drafter} (P{d_pos}) is nose to tail with {leader} (P{l_pos}), waiting for the moment to strike."
                    ]
                    self.log("BATTLE", random.choice(messages))
                    self.battles[battle_key] = {"type": "Drafting", "time": sim_now, "count": count}

    # ------------------------------------------------------------------ leaderboards

    def _record_distance_history(self, name, sim_time, total_distance):
        hist = self.distance_history.setdefault(name, [])
        hist.append((sim_time, total_distance))
        cutoff = sim_time - 90.0
        while hist and hist[0][0] < cutoff:
            hist.pop(0)

    def _calculate_time_gap(self, ahead, behind, sim_now):
        """Gap from a driver to the car directly ahead: the game's own delta, else trajectory history, else pace."""
        name = behind["name"]
        if behind["finish"] == FINISH_DQ:
            return "DQ"
        if behind["finish"] == FINISH_DNF:
            return "DNF"
        if name in self.assumed_retired_participants:
            return "Retired"

        length = self.track_length if self.track_length > 0 else 4000.0
        dist_ahead = self.total_distance(ahead)
        dist_behind = self.total_distance(behind)
        dist_diff = dist_ahead - dist_behind

        if dist_diff >= length:
            laps_behind = int(dist_diff // length)
            return "+1 Lap" if laps_behind == 1 else f"+{laps_behind} Laps"

        if behind["gap_front"] > 0.0:
            return f"+{behind['gap_front']:.3f}s"

        hist_ahead = self.distance_history.get(ahead["name"], [])
        for (t1, d1), (t2, d2) in zip(hist_ahead, hist_ahead[1:]):
            if d1 <= dist_behind <= d2 and d2 > d1:
                gap = sim_now - (t1 + (dist_behind - d1) / (d2 - d1) * (t2 - t1))
                if gap >= 0.0:
                    return f"+{gap:.3f}s"

        lap_time = self.last_lap_times.get(name) or self.last_lap_times.get(ahead["name"])
        if lap_time:
            speed = length / lap_time
        else:
            speed = max(behind["speed"], ahead["speed"], 0.0)
            if speed <= 5.0:
                speed = 40.0
        return f"+{max(0.0, dist_diff) / speed:.3f}s"

    def log_leaderboard(self, drivers, label, sim_now):
        grid = sorted(drivers, key=lambda x: x["pos"])
        entries = []
        for idx, item in enumerate(grid):
            gap_str = "Leader" if idx == 0 else self._calculate_time_gap(grid[idx - 1], item, sim_now)
            entries.append(f"P{item['pos']}: {item['name']} ({gap_str})")
        self.log("LEADERBOARD", f"{label}: {' | '.join(entries)}")

    def log_starting_grid(self, drivers):
        grid = sorted(drivers, key=lambda x: x["pos"])
        grid_str = " | ".join(f"P{d['pos']}: {d['name']} [{d['car']}]" for d in grid)
        self.log("LEADERBOARD", f"Starting Grid Order: {grid_str}")
        # Who drives for whom, so the commentators can mention teams and manufacturers
        teams = []
        for d in grid:
            team = d["team"] or d["manufacturer"]
            if team:
                maker = f" ({d['manufacturer']})" if d["manufacturer"] and d["manufacturer"] not in team else ""
                teams.append(f"{d['name']} - {team}{maker}")
        if teams:
            self.log("ENTRY", f"Teams: {' | '.join(teams)}")

    # ------------------------------------------------------------------ run loop

    def run(self):
        print("Waiting for RaceRoom... (start a session or open a replay)")
        while not self.shm.read()[0]:
            time.sleep(1.0)
        header = self.shm.read()[0]
        print("Press F12 on the final lap if the race end isn't detected automatically. Ctrl+C to stop.")

        self.start_logging()
        self.log("SYSTEM", f"Connected to RaceRoom shared memory (version {header.version_major}.{header.version_minor})")
        if header.game_in_replay == 1:
            self.log("SYSTEM", "Warning: RaceRoom does not share driver data during replays. Log the race live instead.")
        try:
            while True:
                if not self.update():
                    time.sleep(1.0) # Game closed or between sessions
                    continue
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            self.log("SYSTEM", "Logging stopped by user.")
        finally:
            if self.log_file:
                self.log_file.close()
            if self.recorder:
                self.recorder.close()
            self.shm.close()


if __name__ == "__main__":
    RaceroomEventLogger().run()
