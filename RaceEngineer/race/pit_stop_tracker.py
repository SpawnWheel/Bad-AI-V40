"""
Learned pit stop model for AMS2.

A pit stop costs:  pit loss = drive-through loss + stationary (service) time
  * drive-through loss : time the pit lane (speed limit, decel/accel into the box) costs compared with
                         driving the same stretch at racing pace. Measured every time the player uses the
                         pit lane (practice box visits count too - the stationary part is removed), using
                         the reference lap trace to know how long that stretch takes at racing speed.
  * service time       : refuel litres / refuel rate (learned per car), or the tyre change time if longer.

This fixes the old model, which averaged raw pit-lane times (entry -> exit) including very different
stationary times, so a 25 L refuel stop pushed the "pit loss" from 19 s to 35 s.

Also learns how long AI cars typically spend in the pit lane (for "will he come out ahead?" projections).
Persisted in data/pit_stops_v2.json.
"""

import json
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from telemetry.collector import TelemetrySnapshot
from telemetry.shared_memory import PitMode
from utils.logging import logger

DEFAULT_PIT_LOSS_SEC = 45.0
DEFAULT_REFUEL_RATE_LPS = 2.0
DEFAULT_TYRE_CHANGE_SEC = 12.0
MAX_MEASUREMENTS_PER_TRACK = 5
DEFAULT_STORAGE_PATH = Path("data/pit_stops_v2.json")


def _median(v: List[float]) -> Optional[float]:
    if not v:
        return None
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


class PitStopTracker:
    def __init__(
        self,
        storage_path: Optional[Path] = None,
        default_pit_loss_sec: float = DEFAULT_PIT_LOSS_SEC,
        max_measurements: int = MAX_MEASUREMENTS_PER_TRACK,
        min_valid_pit_sec: float = 3.0,
        max_valid_pit_sec: float = 600.0,
        track_model=None,
    ):
        self.storage_path = Path(storage_path) if storage_path else DEFAULT_STORAGE_PATH
        self.default_pit_loss_sec = default_pit_loss_sec
        self.max_measurements = max_measurements
        self.min_valid_pit_sec = min_valid_pit_sec
        self.max_valid_pit_sec = max_valid_pit_sec
        self._track_model = track_model

        self._lock = threading.Lock()
        self._db: Dict[str, Any] = {"version": 2, "tracks": {}, "cars": {}}

        # player stop in progress
        self._is_in_pit = False
        self._entry_time = 0.0
        self._entry_dist = 0.0
        self._entry_fuel = 0.0
        self._max_fuel = 0.0
        self._entry_wear: Tuple[float, ...] = ()
        self._stationary = 0.0
        self._last_ts: Optional[float] = None
        self._visited_garage = False
        self._last_pit_mode = 0
        self._current_track_key = ""
        self.last_stop: Optional[Dict[str, Any]] = None
        self.session_stops: List[Dict[str, Any]] = []

        # competitor pit lane timing
        self._comp_entry: Dict[str, float] = {}
        self._comp_last_mode: Dict[str, int] = {}

        self.load()

    # ------------------------------------------------------------------ plumbing
    @property
    def track_model(self):
        if self._track_model is None:
            from race.track_model import track_model as tm
            self._track_model = tm
        return self._track_model

    @staticmethod
    def get_track_key(track_name: str, track_variation: str = "") -> str:
        return f"{(track_name or '').strip()}|{(track_variation or '').strip()}"

    def load(self):
        with self._lock:
            try:
                if self.storage_path.exists():
                    with open(self.storage_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and data.get("version") == 2:
                        data.setdefault("tracks", {})
                        data.setdefault("cars", {})
                        self._db = data
                    logger.info(f"Loaded pit stop model for {len(self._db['tracks'])} tracks from {self.storage_path}")
            except Exception as e:
                logger.warning(f"Failed to load pit stop model from {self.storage_path}: {e}")

    def save(self):
        with self._lock:
            try:
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump(self._db, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save pit stop model to {self.storage_path}: {e}")

    def reset_tracking(self):
        self._is_in_pit = False
        self._visited_garage = False
        self._last_pit_mode = 0
        self._stationary = 0.0
        self._last_ts = None
        self._comp_entry.clear()
        self._comp_last_mode.clear()
        self.session_stops = []
        self.last_stop = None

    def _track_rec(self, key: str) -> Dict[str, Any]:
        return self._db["tracks"].setdefault(key, {"drive_loss": [], "loss_total": [], "lane_time": [], "ai_lane_time": [], "last_updated": ""})

    def _car_rec(self, car: str) -> Dict[str, Any]:
        return self._db["cars"].setdefault(car or "unknown", {"refuel_lps": [], "tyre_change_sec": []})

    def _push(self, lst: List[float], val: float, n: Optional[int] = None):
        lst.append(round(float(val), 2))
        del lst[:-int(n or self.max_measurements)]

    # ------------------------------------------------------------------ queries
    def get_drive_loss(self, track_name: str, track_variation: str = "") -> Tuple[Optional[float], int]:
        rec = self._db["tracks"].get(self.get_track_key(track_name, track_variation))
        if not rec:
            return None, 0
        return _median(rec.get("drive_loss", [])), len(rec.get("drive_loss", []))

    def get_refuel_rate(self, car: str) -> Tuple[float, bool]:
        rec = self._db["cars"].get(car or "unknown")
        m = _median(rec.get("refuel_lps", [])) if rec else None
        if not m and rec:
            # only stops with a tyre change seen so far: litres / stationary time is a lower bound on the
            # pure refuel rate, but it predicts what a normal (fuel + tyres) stop actually costs
            m = _median(rec.get("refuel_lps_with_tyres", []))
        return (m, True) if m else (DEFAULT_REFUEL_RATE_LPS, False)

    def predict_ai_stop_loss(self, track_name: str, track_variation: str, pace: float = 90.0) -> Optional[float]:
        """Typical time an AI car loses to a pit stop here (lane time minus the racing time for that stretch)."""
        lane = self.typical_ai_lane_time(track_name, track_variation)
        if lane is None:
            return None
        ref = None
        tm = self.track_model
        info = getattr(tm, "info", None) if tm else None
        try:
            if info is not None and info.pit_entry_m is not None and info.pit_exit_m is not None:
                ref = tm.time_between(info.pit_entry_m, info.pit_exit_m, pace)
        except Exception:
            ref = None
        if ref is None or not (0.0 < ref < lane):
            rec = self._db["tracks"].get(self.get_track_key(track_name, track_variation)) or {}
            lt, lo = _median(rec.get("lane_time", [])), _median(rec.get("loss_total", []))
            ref = (lt - lo) if (lt and lo and lt > lo) else 5.0
        loss = lane - ref
        return loss if 5.0 <= loss <= 150.0 else None

    def get_tyre_change_sec(self, car: str) -> Tuple[float, bool]:
        rec = self._db["cars"].get(car or "unknown")
        m = _median(rec.get("tyre_change_sec", [])) if rec else None
        return (m, True) if m else (DEFAULT_TYRE_CHANGE_SEC, False)

    def predict_stop_loss(self, track_name: str, track_variation: str, car: str,
                          fuel_liters: float = 0.0, change_tyres: bool = False) -> Tuple[float, str, bool]:
        """Returns (seconds lost vs staying out, human-readable breakdown, learned?)."""
        drive, n = self.get_drive_loss(track_name, track_variation)
        rate, rate_learned = self.get_refuel_rate(car)
        tyre_t, _ = self.get_tyre_change_sec(car)
        service = max(fuel_liters / rate if fuel_liters > 0 else 0.0, tyre_t if change_tyres else 0.0)
        if fuel_liters > 0 or change_tyres:
            service += 1.0  # jacks down / release
        if drive is None:
            rec = self._db["tracks"].get(self.get_track_key(track_name, track_variation)) or {}
            legacy = _median(rec.get("loss_total_legacy", []))
            if legacy:
                return float(legacy), f"~{legacy:.0f}s (manually recorded total pit loss)", True
            total = self.default_pit_loss_sec
            return total, (f"~{total:.0f}s (default estimate - no pit lane measured at this track yet)"), False
        total = drive + service
        what = []
        if fuel_liters > 0:
            what.append(f"{fuel_liters:.0f} L at {rate:.1f} L/s{'' if rate_learned else ' (assumed rate)'}")
        if change_tyres:
            what.append("tyres")
        svc = f" + {service:.1f}s service ({', '.join(what)})" if what else ""
        return total, f"~{total:.1f}s = {drive:.1f}s pit-lane drive-through loss (measured x{n}){svc}", True

    def get_pit_loss_sec(self, track_name: str, track_variation: str = "") -> float:
        """Drive-through loss if learned, else the configured default total loss (legacy API)."""
        rec = self._db["tracks"].get(self.get_track_key(track_name, track_variation))
        if rec and rec.get("loss_total_legacy"):
            return float(_median(rec["loss_total_legacy"]))
        drive, _ = self.get_drive_loss(track_name, track_variation)
        return float(drive) if drive is not None else self.default_pit_loss_sec

    def get_pit_loss_info(self, track_name: str, track_variation: str = "") -> Tuple[float, bool, int]:
        rec = self._db["tracks"].get(self.get_track_key(track_name, track_variation))
        if rec and rec.get("loss_total_legacy"):
            return float(_median(rec["loss_total_legacy"])), True, len(rec["loss_total_legacy"])
        drive, n = self.get_drive_loss(track_name, track_variation)
        if drive is None:
            return self.default_pit_loss_sec, False, 0
        return float(drive), True, n

    def record_measurement(self, track_name: str, track_variation: str, loss_sec: float) -> float:
        """Legacy/manual: record a total pit loss for a track. Returns the new median."""
        key = self.get_track_key(track_name, track_variation)
        with self._lock:
            rec = self._track_rec(key)
            lst = rec.setdefault("loss_total_legacy", [])
            self._push(lst, loss_sec)
            rec["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            val = float(_median(lst))
        self.save()
        return val

    def typical_ai_lane_time(self, track_name: str, track_variation: str) -> Optional[float]:
        rec = self._db["tracks"].get(self.get_track_key(track_name, track_variation))
        return _median(rec.get("ai_lane_time", [])) if rec else None

    def competitor_pit_elapsed(self, driver_key: str, now: Optional[float] = None) -> Optional[float]:
        t = self._comp_entry.get(driver_key)
        if t is None:
            return None
        return (now or time.time()) - t

    def last_stop_summary(self) -> str:
        s = self.last_stop
        if not s:
            return ""
        parts = [f"Lap {s['lap']}: pit lane {s['lane_time']:.1f}s, stationary {s['stationary']:.1f}s"]
        if s.get("fuel_added", 0) > 0.3:
            parts.append(f"{s['fuel_added']:.1f} L added")
        if s.get("tyres"):
            parts.append("tyres changed")
        if s.get("loss") is not None:
            parts.append(f"total loss vs racing ~{s['loss']:.1f}s (drive-through {s['drive_loss']:.1f}s + service {s['stationary']:.1f}s)")
        return "; ".join(parts)

    # ------------------------------------------------------------------ live
    def on_telemetry(self, snapshot: TelemetrySnapshot):
        if not snapshot:
            return
        if snapshot.track_name:
            self._current_track_key = self.get_track_key(snapshot.track_name, snapshot.track_variation)
        now = snapshot.timestamp or time.time()
        pm = snapshot.pit_mode.value
        dt = 0.0 if self._last_ts is None else max(0.0, min(2.0, now - self._last_ts))
        self._last_ts = now

        if pm == PitMode.PIT_MODE_IN_GARAGE.value:
            self._visited_garage = True

        if self._last_pit_mode == 0 and pm > 0:
            self._is_in_pit = True
            self._entry_time = now
            self._entry_dist = float(getattr(snapshot, "current_lap_distance", 0.0))
            self._entry_fuel = snapshot.fuel_liters
            self._max_fuel = snapshot.fuel_liters
            self._entry_wear = tuple(snapshot.tyre_wear_pct)
            self._stationary = 0.0
            self._was_still = False
            self._visited_garage = (pm == PitMode.PIT_MODE_IN_GARAGE.value)
            self._entry_lap = snapshot.current_lap
            logger.info(f"[PIT TRACKER] Pit entry at {self._current_track_key} ({self._entry_dist:.0f} m).")
        elif self._is_in_pit and pm > 0:
            still = snapshot.speed_kmh < 2.0
            if still and getattr(self, "_was_still", False):
                self._stationary += dt
            self._was_still = still
            self._max_fuel = max(self._max_fuel, snapshot.fuel_liters)
        elif self._is_in_pit and pm == 0:
            self._finish_stop(snapshot, now)
            self._is_in_pit = False
            self._visited_garage = False

        self._last_pit_mode = pm
        self._track_competitors(snapshot, now)

    def _finish_stop(self, snap: TelemetrySnapshot, now: float):
        lane = now - self._entry_time
        if self._visited_garage:
            logger.info(f"[PIT TRACKER] Ignoring pit visit ({lane:.1f}s): car went to the garage.")
            return
        if not (self.min_valid_pit_sec <= lane <= self.max_valid_pit_sec):
            logger.info(f"[PIT TRACKER] Ignoring pit visit: lane time {lane:.1f}s out of range.")
            return
        exit_d = float(getattr(snap, "current_lap_distance", 0.0))
        pace = snap.best_lap_time if snap.best_lap_time > 20 else (snap.last_lap_time if snap.last_lap_time > 20 else 90.0)
        tm = self.track_model
        ref_t = tm.time_between(self._entry_dist, exit_d, pace) if (tm and self._entry_dist > 0 and exit_d > 0) else None
        fuel_added = max(0.0, self._max_fuel - self._entry_fuel)
        tyres = bool(self._entry_wear) and any(
            (a - b) > 3.0 for a, b in zip(self._entry_wear, snap.tyre_wear_pct)
        )
        stop: Dict[str, Any] = {
            "lap": getattr(self, "_entry_lap", snap.current_lap),
            "lane_time": lane,
            "stationary": self._stationary,
            "fuel_added": fuel_added,
            "tyres": tyres,
            "loss": None,
            "drive_loss": None,
        }
        key = self._current_track_key
        with self._lock:
            rec = self._track_rec(key)
            self._push(rec["lane_time"], lane)
            if ref_t is not None and 0 < ref_t < lane:
                loss = lane - ref_t
                drive = loss - self._stationary
                stop["loss"], stop["drive_loss"] = loss, drive
                if 2.0 <= drive <= 60.0:
                    self._push(rec["drive_loss"], drive)
                    self._push(rec["loss_total"], loss)
            car = self._car_rec(snap.car_name)
            if fuel_added > 1.0 and self._stationary > 1.0 and not tyres:
                self._push(car["refuel_lps"], fuel_added / self._stationary)
            elif fuel_added > 3.0 and self._stationary > 2.0 and tyres:
                self._push(car.setdefault("refuel_lps_with_tyres", []), fuel_added / self._stationary)
            if tyres and fuel_added < 1.0 and self._stationary > 2.0:
                self._push(car["tyre_change_sec"], self._stationary)
            rec["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.last_stop = stop
        self.session_stops.append(stop)
        self.save()
        logger.info(f"[PIT TRACKER] Stop recorded: {self.last_stop_summary()}")

    def _track_competitors(self, snap: TelemetrySnapshot, now: float):
        pname = (snap.player_name or "").strip().lower()
        for p in snap.participants or ():
            if not p.is_active or not p.name:
                continue
            k = p.name.strip().lower()
            if k == pname:
                continue
            last = self._comp_last_mode.get(k, 0)
            if last == 0 and p.pit_mode in (1, 2, 3):
                self._comp_entry[k] = now
            elif last in (1, 2, 3) and p.pit_mode == 0 and k in self._comp_entry:
                lane = now - self._comp_entry.pop(k)
                if 8.0 <= lane <= 180.0 and self._current_track_key:
                    with self._lock:
                        self._push(self._track_rec(self._current_track_key)["ai_lane_time"], lane, 9)
                    self.save()
            elif p.pit_mode == 4:
                self._comp_entry.pop(k, None)
            self._comp_last_mode[k] = p.pit_mode


pit_stop_tracker = PitStopTracker()
