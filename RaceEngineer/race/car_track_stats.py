"""
Small persistent store of what we have learned for a car at a track (data/car_track_stats.json):
fuel burn per lap and a reference race pace. Practice fills it; the race starts from it instead of a
blind 3.5 L/lap and 90 s default.
"""

import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from utils.logging import logger

DEFAULT_PATH = Path("data/car_track_stats.json")


class CarTrackStats:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = threading.Lock()
        self._db: Dict[str, dict] = {}
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._db = d
        except Exception as e:
            logger.warning(f"Could not load {self.path}: {e}")

    @staticmethod
    def key(car: str, track: str, variation: str) -> str:
        return f"{(car or '').strip()}@{(track or '').strip()}|{(variation or '').strip()}"

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._db, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save {self.path}: {e}")

    def add_fuel_burns(self, car: str, track: str, variation: str, burns: List[float]):
        burns = [float(b) for b in burns if 0.3 < float(b) < 30]
        if not burns:
            return
        with self._lock:
            rec = self._db.setdefault(self.key(car, track, variation), {})
            lst = rec.setdefault("fuel_burns", [])
            lst.extend(round(b, 3) for b in burns)
            del lst[:-12]
            rec["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save()

    def fuel_burn(self, car: str, track: str, variation: str) -> Optional[float]:
        rec = self._db.get(self.key(car, track, variation)) or {}
        lst = rec.get("fuel_burns") or []
        if not lst:
            return None
        s = sorted(lst[-8:])
        return s[len(s) // 2]

    def add_lap_times(self, car: str, track: str, variation: str, laps: List[float]):
        laps = [float(x) for x in laps if 20 < float(x) < 1200]
        if not laps:
            return
        with self._lock:
            rec = self._db.setdefault(self.key(car, track, variation), {})
            best = rec.get("best_lap") or 0
            b = min(laps)
            if not best or b < best:
                rec["best_lap"] = round(b, 3)
            lst = rec.setdefault("recent_laps", [])
            lst.extend(round(x, 3) for x in laps)
            del lst[:-20]
            rec["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save()

    def race_pace(self, car: str, track: str, variation: str) -> Optional[float]:
        rec = self._db.get(self.key(car, track, variation)) or {}
        lst = rec.get("recent_laps") or []
        best = rec.get("best_lap")
        if not lst:
            return best
        clean = sorted(x for x in lst if not best or x <= best * 1.05)
        if not clean:
            return best
        return clean[len(clean) // 2]


car_track_stats = CarTrackStats()
