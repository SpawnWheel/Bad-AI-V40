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
            ref = self.robust_burn(lst) if len(lst) >= 4 else None
            if ref:
                burns = [b for b in burns if b >= 0.75 * ref]   # short-measured lap: don't keep it
                if not burns:
                    return
            lst.extend(round(b, 3) for b in burns)
            del lst[:-12]
            rec["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save()

    @staticmethod
    def robust_burn(burns: List[float]) -> Optional[float]:
        """
        Typical full-lap burn from stored values. A lap-counter glitch can only make a lap look SHORT
        (0.3 L instead of 2.4), never long, so take the cluster near the highest values (>= 85% of the
        max) and use its median. If one value were wrongly high this errs towards more fuel - the safe side.
        """
        vals = [float(b) for b in burns if b and float(b) > 0]
        if not vals:
            return None
        top = max(vals)
        cluster = sorted(v for v in vals if v >= 0.85 * top)
        return cluster[len(cluster) // 2]

    def fuel_burn(self, car: str, track: str, variation: str) -> Optional[float]:
        rec = self._db.get(self.key(car, track, variation)) or {}
        return self.robust_burn(rec.get("fuel_burns") or [])

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
