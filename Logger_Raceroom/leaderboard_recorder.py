"""Records the live timing tower while the logger runs, for the broadcast graphics.

RaceRoom shares no driver data during replays, so the leaderboard graphic is built from
what was recorded live. The recorder reuses the logger's own shared-memory read (no extra
reads of the game) and writes one small JSON line every few seconds, plus one as soon as
the running order settles on a change, so position swaps line up with the overtakes.

Output: <log name>_timing.jsonl next to the race log, timed on the logger's clock.
Record types:
  {"type": "session", "t", "session"}                          new session (the clock restarts)
  {"type": "entry", "name", "num", "car", "class", "team", "mfr"}   once per driver
  {"type": "timing", "t", "phase", "rem", "laps", "fmt", "yel", "rows": [...], "ot"?}
     ot = when a changed running order first appeared (only on order-change snapshots)
     row = [name, place, class place, completed laps, lap fraction, interval s, gap to leader s,
            laps down, last lap s, best lap s, in pit lane, pit stops, finish status]
"""
import json
import os

SNAPSHOT_INTERVAL = 5.0   # seconds between regular snapshots
ORDER_SETTLE = 1.0        # a new running order must hold this long (side-by-side cars flicker)
MIN_WRITE_GAP = 1.0       # never write more often than this

# Row field order (kept positional to keep the file small)
ROW_FIELDS = ("name", "pos", "pos_class", "laps", "frac", "interval", "gap", "laps_down",
              "last", "best", "pit", "stops", "finish")


def timing_path_for(log_path):
    return os.path.splitext(log_path)[0] + "_timing.jsonl"


def _num(v, nd=3):
    """Rounded float, or None for the game's 'no value' markers."""
    return round(float(v), nd) if v is not None and v > 0 else None


class LeaderboardRecorder:
    def __init__(self, log_path, lookup, decode):
        self.path = timing_path_for(log_path)
        self.lookup = lookup
        self.decode = decode
        self.file = open(self.path, "w", encoding="utf-8")
        self.enabled = True
        self.session_key = None
        self.entries = set()
        self.last_write = None
        self.written_order = None
        self.pending_order = None  # (order, time first seen)
        self.best = {}             # name -> best valid lap this session
        self.last_laps = {}        # name -> completed laps on the previous tick
        self.last_valid = {}       # name -> current lap valid flag on the previous tick
        self.last_lap = {}         # name -> last completed lap time

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def _write(self, record):
        self.file.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
        self.file.flush()

    def update(self, header, raw_drivers, sim_now, session_type):
        """Called once per logger tick. Never raises: a failure only switches the recorder off."""
        if not self.enabled:
            return
        try:
            self._update(header, raw_drivers, sim_now, session_type)
        except Exception as e:
            self.enabled = False
            print(f"Leaderboard recorder stopped ({e}); the race log is unaffected.")

    def _update(self, header, raw_drivers, sim_now, session_type):
        key = (header.session_type, header.session_iteration)
        if key != self.session_key:
            self.session_key = key
            self.best, self.last_laps, self.last_valid, self.last_lap = {}, {}, {}, {}
            self.written_order = self.pending_order = self.last_write = None
            self._write({"type": "session", "t": round(sim_now, 1), "session": session_type})

        cars = []
        for d in raw_drivers:
            name = self.decode(d.driver_info.name)
            if name and d.place > 0:
                cars.append((d.place, name, d))
        if not cars:
            return
        cars.sort(key=lambda c: c[0])

        # Lap times are cheap to track every tick; only valid laps count as a best
        for _, name, d in cars:
            laps = d.completed_laps
            if laps > self.last_laps.get(name, laps):
                lap_time = d.sector_time_previous_self[2]
                if lap_time > 10.0:
                    self.last_lap[name] = lap_time
                    if self.last_valid.get(name, 1) == 1 and lap_time < self.best.get(name, 1e9):
                        self.best[name] = lap_time
            self.last_laps[name] = laps
            self.last_valid[name] = d.current_lap_valid

        order = tuple(name for _, name, _ in cars)
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

        for _, name, d in cars:
            if name not in self.entries:
                self.entries.add(name)
                self._write(self._entry(name, d.driver_info))
        record = self._snapshot(header, cars, sim_now)
        if settled_change:
            record["ot"] = round(self.pending_order[1], 1)  # when the new order first appeared
        self._write(record)

    def _entry(self, name, info):
        lk = self.lookup
        livery = lk.liveries.get(str(info.livery_id), {})
        team = lk.team_name(info.team_id, info.livery_id)
        mfr = lk.manufacturer_name(info.manufacturer_id, info.model_id)
        number = info.car_number if info.car_number > 0 else ""
        if not number and livery.get("Name", "").startswith("#"):
            number = livery["Name"][1:].strip()
        return {"type": "entry", "name": name, "num": str(number), "car": lk.car_name(info.model_id),
                "class": lk.class_name(info.class_id), "team": team, "mfr": mfr}

    def _snapshot(self, header, cars, sim_now):
        leader = cars[0][2]
        leader_dist = leader.completed_laps + max(0.0, leader.lap_distance_fraction)
        rows = []
        gap = 0.0
        for i, (place, name, d) in enumerate(cars):
            interval = _num(d.time_delta_front) if i else None
            dist = d.completed_laps + max(0.0, d.lap_distance_fraction)
            laps_down = max(0, int(leader_dist - dist)) if i else 0
            if i and gap is not None:
                gap = gap + interval if interval is not None else None
            rows.append([name, place, d.place_class, d.completed_laps, round(d.lap_distance_fraction, 3),
                         interval, round(gap, 3) if (i and gap is not None) else None, laps_down,
                         _num(self.last_lap.get(name)), _num(self.best.get(name)),
                         d.in_pitlane, d.num_pitstops, d.finish_status])
        return {
            "type": "timing",
            "t": round(sim_now, 1),
            "phase": header.session_phase,
            "rem": round(header.session_time_remaining, 1) if header.session_time_remaining >= 0 else None,
            "laps": header.number_of_laps if header.number_of_laps > 0 else None,
            "fmt": header.session_length_format,
            "yel": [i + 1 for i, v in enumerate(header.flags.sector_yellow) if v == 1],
            "rows": rows,
        }
