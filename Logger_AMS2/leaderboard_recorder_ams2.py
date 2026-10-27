"""Records the live timing tower while the AMS2 logger runs, for the broadcast graphics.

The race log only carries the standings every 4 minutes, so the timing tower graphic would
show stale gaps. This writes the same <log name>_timing.jsonl as the RaceRoom recorder
(see Logger_Raceroom/leaderboard_recorder.py for the record layout): one small JSON line
every few seconds, plus one as soon as the running order settles on a change, so position
swaps line up with the overtakes. It reuses the logger's own shared-memory read and its
distance history for the time gaps, so it costs the sim nothing.

Only race sessions are recorded, timed on the logger's (sim) clock.
"""
import json
import os

SNAPSHOT_INTERVAL = 5.0   # seconds between regular snapshots
ORDER_SETTLE = 1.0        # a new running order must hold this long (side-by-side cars flicker)
MIN_WRITE_GAP = 1.0       # never write more often than this

SESSION_RACE = 5
RACE_STATE_RACING, RACE_STATE_FINISHED, RACE_STATE_DQ = 2, 3, 4
PIT_LANE_MODES = (1, 2, 3, 4, 5)  # driving into / in / out of the pits, in / out of the garage
# Finish codes shared with the RaceRoom recorder (and GraphicsOverlay/timing.py)
FINISH_NONE, FINISH_FINISHED, FINISH_DNF, FINISH_DQ = 0, 1, 2, 5
PHASE_GREEN, PHASE_CHECKERED = 5, 6
LENGTH_LAPS, LENGTH_TIME = 1, 0


def timing_path_for(log_path):
    return os.path.splitext(log_path)[0] + "_timing.jsonl"


def _num(v, nd=3):
    """Rounded float, or None for the game's 'no value' markers."""
    return round(float(v), nd) if v is not None and v > 0 else None


class AMS2LeaderboardRecorder:
    def __init__(self, log_path, logger):
        self.path = timing_path_for(log_path)
        self.logger = logger
        self.file = open(self.path, "w", encoding="utf-8")
        self.enabled = True
        self.in_race = False
        self.last_t = None
        self.entries = set()
        self.last_write = None
        self.written_order = None
        self.pending_order = None  # (order, time first seen)
        self.pit_modes = {}        # name -> pit mode on the previous tick
        self.stops = {}            # name -> pit stops this race

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def _write(self, record):
        self.file.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
        self.file.flush()

    def update(self, sim_now):
        """Called once per logger tick, after the logger has read the participants. Never raises:
        a failure only switches the recorder off."""
        if not self.enabled or not self.file:
            return
        try:
            self._update(sim_now)
        except Exception as e:
            self.enabled = False
            print(f"Leaderboard recorder stopped ({e}); the race log is unaffected.")

    def _update(self, sim_now):
        data = self.logger.data
        racing = data.mSessionState == SESSION_RACE and data.mRaceState >= RACE_STATE_RACING
        # A restarted race starts the session clock again
        restarted = self.in_race and self.last_t is not None and sim_now < self.last_t - 5.0
        if not racing:
            self.in_race = False
            return
        if not self.in_race or restarted:
            self.in_race = True
            self.pit_modes, self.stops = {}, {}
            self.written_order = self.pending_order = self.last_write = None
            self._write({"type": "session", "t": round(sim_now, 1), "session": "Race"})
        self.last_t = sim_now

        cars = self._cars()
        if not cars:
            return
        # Pit stops are counted every tick (a quick stop could fall between two snapshots)
        for c in cars:
            prev = self.pit_modes.get(c["name"], 0)
            if c["pit_mode"] in (1, 2) and prev not in PIT_LANE_MODES:
                self.stops[c["name"]] = self.stops.get(c["name"], 0) + 1
            self.pit_modes[c["name"]] = c["pit_mode"]

        order = tuple(c["name"] for c in cars)
        if self.pending_order is None or self.pending_order[0] != order:
            self.pending_order = (order, sim_now)
        settled_change = order != self.written_order and sim_now - self.pending_order[1] >= ORDER_SETTLE
        due = self.last_write is None or sim_now - self.last_write >= SNAPSHOT_INTERVAL
        if not (due or settled_change):
            return
        if self.last_write is not None and 0 <= sim_now - self.last_write < MIN_WRITE_GAP:
            return
        self.last_write = sim_now
        self.written_order = order

        for c in cars:
            if c["name"] not in self.entries:
                self.entries.add(c["name"])
                car = c["car"]
                self._write({"type": "entry", "name": c["name"], "num": "", "car": car, "class": c["class"],
                             "team": "", "mfr": car.split()[0] if car else ""})
        record = self._snapshot(cars, sim_now)
        if settled_change:
            record["ot"] = round(self.pending_order[1], 1)  # when the new order first appeared
        self._write(record)

    def _cars(self):
        lg, data = self.logger, self.logger.data
        cars = []
        for i in range(data.mNumParticipants):
            p = data.mParticipantInfo[i]
            if not p.mIsActive or p.mRacePosition <= 0:
                continue
            name = lg.decode(p.mName)
            car = lg.decode(data.mCarNames[i])
            cls = lg.decode(data.mCarClassNames[i])
            if not name or lg.is_safety_car(name, car, cls):
                continue
            cars.append({"name": name, "pos": p.mRacePosition, "car": car, "class": cls,
                         "dist": p.mCurrentLapDistance, "laps": p.mLapsCompleted, "speed": data.mSpeeds[i],
                         "last_lap_time": data.mLastLapTimes[i], "fastest_lap_time": data.mFastestLapTimes[i],
                         "race_state": data.mRaceStates[i], "pit_mode": data.mPitModes[i]})
        cars.sort(key=lambda c: c["pos"])
        return cars

    def _gap_seconds(self, ahead, behind, sim_now, track_length):
        """Time from the car ahead passing behind's spot on track until now (as the logger works it out
        for its standings), else an estimate from the distance between them and the pace."""
        dist_behind = behind["laps"] * track_length + behind["dist"]
        hist = self.logger.distance_history.get(ahead["name"], [])
        for (t1, d1), (t2, d2) in zip(hist, hist[1:]):
            if d1 <= dist_behind <= d2 and d2 > d1:
                gap = sim_now - (t1 + (dist_behind - d1) / (d2 - d1) * (t2 - t1))
                if gap >= 0.0:
                    return gap
        dist_gap = max(0.0, ahead["laps"] * track_length + ahead["dist"] - dist_behind)
        for c, key in ((behind, "last_lap_time"), (ahead, "last_lap_time"),
                       (behind, "fastest_lap_time"), (ahead, "fastest_lap_time")):
            if c.get(key, 0) > 10.0:
                return dist_gap / (track_length / c[key])
        speed = max(behind.get("speed", 0), ahead.get("speed", 0))
        return dist_gap / speed if speed > 5.0 else None

    def _finish(self, c):
        state = c["race_state"]
        if state == RACE_STATE_FINISHED:
            return FINISH_FINISHED
        if state == RACE_STATE_DQ:
            return FINISH_DQ
        if state > RACE_STATE_DQ or c["name"] in getattr(self.logger, "assumed_retired_participants", set()):
            return FINISH_DNF
        return FINISH_NONE

    def _snapshot(self, cars, sim_now):
        data = self.logger.data
        track_length = data.mTrackLength if data.mTrackLength > 0 else 4000.0
        leader_dist = cars[0]["laps"] + cars[0]["dist"] / track_length
        class_pos = {}
        rows = []
        gap = 0.0
        for i, c in enumerate(cars):
            class_pos[c["class"]] = class_pos.get(c["class"], 0) + 1
            dist = c["laps"] + c["dist"] / track_length
            laps_down = max(0, int(leader_dist - dist)) if i else 0
            interval = None
            if i:
                secs = self._gap_seconds(cars[i - 1], c, sim_now, track_length)
                interval = round(secs, 3) if secs is not None else None
                gap = gap + interval if (gap is not None and interval is not None) else None
            rows.append([c["name"], c["pos"], class_pos[c["class"]], c["laps"], round(c["dist"] / track_length, 3),
                         interval, round(gap, 3) if (i and gap is not None) else None, laps_down,
                         _num(c["last_lap_time"]), _num(c["fastest_lap_time"]),
                         1 if c["pit_mode"] in PIT_LANE_MODES else 0, self.stops.get(c["name"], 0), self._finish(c)])
        laps = data.mLapsInEvent if data.mLapsInEvent > 0 else None
        rem = round(data.mEventTimeRemaining, 1) if data.mEventTimeRemaining > 0 else None
        return {
            "type": "timing",
            "t": round(sim_now, 1),
            "phase": PHASE_GREEN if data.mRaceState == RACE_STATE_RACING else PHASE_CHECKERED,
            "rem": rem,
            "laps": laps,
            "fmt": LENGTH_LAPS if laps and rem is None else LENGTH_TIME,
            "yel": [],  # AMS2 shares no sector yellows; the tower goes yellow from the logged flags
            "rows": rows,
        }
