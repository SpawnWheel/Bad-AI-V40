"""
Track knowledge for AMS2: where the car is on the lap, how long it takes to get anywhere.

Learned live (and persisted per track layout in data/track_info.json):
  * Reference lap trace  - (lap distance -> elapsed lap time) from the best clean lap. Lets us turn any
                           distance on track into seconds (time to the line, to pit entry, true time gaps).
  * Pit entry / exit     - lap distance where the car enters / leaves the pit lane (median of observations).
  * Corner map           - corners auto-detected from lateral g on a clean lap, numbered C1..Cn in lap order.
                           (Our numbering; it may differ from the official turn numbers.)

Everything degrades gracefully: with no learned data we fall back to average-speed estimates.
"""

import json
import threading
import time
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.logging import logger

DEFAULT_TRACK_INFO_PATH = Path("data/track_info.json")
G = 9.81


def sector_label(raw_sector: int) -> int:
    """AMS2 mCurrentSector is 0-based (0,1,2). Returns 1..3 for display."""
    try:
        s = int(raw_sector)
    except Exception:
        return 0
    if 0 <= s <= 2:
        return s + 1
    return max(0, min(3, s))


@dataclass
class Corner:
    cid: int
    start_m: float
    apex_m: float
    end_m: float
    min_speed_kmh: float
    peak_lat_g: float

    def to_dict(self) -> dict:
        return {"cid": self.cid, "start_m": round(self.start_m, 1), "apex_m": round(self.apex_m, 1),
                "end_m": round(self.end_m, 1), "min_speed_kmh": round(self.min_speed_kmh, 1),
                "peak_lat_g": round(self.peak_lat_g, 2)}

    @staticmethod
    def from_dict(d: dict) -> "Corner":
        return Corner(int(d["cid"]), float(d["start_m"]), float(d["apex_m"]), float(d["end_m"]),
                      float(d.get("min_speed_kmh", 0)), float(d.get("peak_lat_g", 0)))

    def label(self) -> str:
        speed = "slow" if self.min_speed_kmh < 100 else ("medium-speed" if self.min_speed_kmh < 150 else "fast")
        return f"C{self.cid} ({speed}, apex ~{self.apex_m / 1000.0:.2f} km, min ~{self.min_speed_kmh:.0f} km/h)"


@dataclass
class TrackInfo:
    key: str
    length_m: float = 0.0
    ref_lap_time: float = 0.0
    ref_trace: List[Tuple[float, float]] = field(default_factory=list)  # (distance m, elapsed s)
    pit_entry_obs: List[float] = field(default_factory=list)
    pit_exit_obs: List[float] = field(default_factory=list)
    corners: List[Corner] = field(default_factory=list)
    updated: str = ""

    @staticmethod
    def _median(v: List[float]) -> Optional[float]:
        if not v:
            return None
        s = sorted(v)
        n = len(s)
        return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])

    @property
    def pit_entry_m(self) -> Optional[float]:
        return self._median(self.pit_entry_obs)

    @property
    def pit_exit_m(self) -> Optional[float]:
        return self._median(self.pit_exit_obs)

    def to_dict(self) -> dict:
        return {
            "length_m": round(self.length_m, 1),
            "ref_lap_time": round(self.ref_lap_time, 3),
            "ref_trace": [[round(d, 1), round(t, 3)] for d, t in self.ref_trace],
            "pit_entry_obs": [round(x, 1) for x in self.pit_entry_obs[-7:]],
            "pit_exit_obs": [round(x, 1) for x in self.pit_exit_obs[-7:]],
            "corners": [c.to_dict() for c in self.corners],
            "updated": self.updated,
        }

    @staticmethod
    def from_dict(key: str, d: dict) -> "TrackInfo":
        ti = TrackInfo(key=key)
        ti.length_m = float(d.get("length_m", 0.0))
        ti.ref_lap_time = float(d.get("ref_lap_time", 0.0))
        ti.ref_trace = [(float(a), float(b)) for a, b in d.get("ref_trace", [])]
        ti.pit_entry_obs = [float(x) for x in d.get("pit_entry_obs", [])]
        ti.pit_exit_obs = [float(x) for x in d.get("pit_exit_obs", [])]
        ti.corners = [Corner.from_dict(c) for c in d.get("corners", [])]
        ti.updated = d.get("updated", "")
        return ti


class TrackModel:
    """Live + persisted knowledge of the current track layout."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path) if storage_path else DEFAULT_TRACK_INFO_PATH
        self._lock = threading.Lock()
        self._db: Dict[str, dict] = {}
        self.info: Optional[TrackInfo] = None
        self._load()

        # live lap recording
        self._cur_lap: int = -1
        self._cur_samples: List[Tuple[float, float, float, float]] = []  # (dist, t, lat_g, speed)
        self._lap_dirty: bool = False
        self._last_pit_mode: int = 0

    # ------------------------------------------------------------------ persistence
    def _load(self):
        try:
            if self.storage_path.exists():
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._db = data
        except Exception as e:
            logger.warning(f"Could not load track info ({self.storage_path}): {e}")
            self._db = {}

    def save(self):
        if not self.info:
            return
        with self._lock:
            self.info.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._db[self.info.key] = self.info.to_dict()
            try:
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump(self._db, f, indent=1)
            except Exception as e:
                logger.error(f"Could not save track info: {e}")

    @staticmethod
    def track_key(track_name: str, track_variation: str) -> str:
        return f"{(track_name or '').strip()}|{(track_variation or '').strip()}"

    def select_track(self, track_name: str, track_variation: str, length_m: float = 0.0):
        key = self.track_key(track_name, track_variation)
        if not key.replace("|", ""):
            return
        if self.info and self.info.key == key:
            if length_m > 100:
                self.info.length_m = length_m
            return
        raw = self._db.get(key)
        self.info = TrackInfo.from_dict(key, raw) if raw else TrackInfo(key=key)
        if length_m > 100:
            self.info.length_m = length_m
        self._cur_lap = -1
        self._cur_samples = []
        self._lap_dirty = False
        logger.info(
            f"[TRACK] {key}: ref lap {'%.3fs' % self.info.ref_lap_time if self.info.ref_trace else 'not learned yet'}, "
            f"pit entry {('%.0f m' % self.info.pit_entry_m) if self.info.pit_entry_m is not None else 'unknown'}, "
            f"{len(self.info.corners)} corners mapped."
        )

    def reset_live(self):
        self._cur_lap = -1
        self._cur_samples = []
        self._lap_dirty = False

    # ------------------------------------------------------------------ live feed
    def on_telemetry(self, snap) -> None:
        """Feed every snapshot (any rate). Learns pit entry/exit, reference trace and corners."""
        if not snap or not getattr(snap, "track_name", ""):
            return
        self.select_track(snap.track_name, snap.track_variation, snap.track_length_m)
        info = self.info
        if not info:
            return
        pm = int(getattr(snap.pit_mode, "value", snap.pit_mode))
        dist = float(getattr(snap, "current_lap_distance", 0.0))

        # pit entry / exit positions (garage teleports excluded)
        if self._last_pit_mode == 0 and pm in (1, 2) and dist > 0:
            info.pit_entry_obs.append(dist)
            info.pit_entry_obs = info.pit_entry_obs[-7:]
        elif self._last_pit_mode in (1, 2, 3) and pm == 0 and dist > 0:
            info.pit_exit_obs.append(dist)
            info.pit_exit_obs = info.pit_exit_obs[-7:]
        self._last_pit_mode = pm

        # lap trace recording
        lap = int(getattr(snap, "current_lap", 0))
        if lap != self._cur_lap:
            if self._cur_lap > 0 and lap == self._cur_lap + 1:
                self._finish_lap(float(getattr(snap, "last_lap_time", 0.0)))
            self._cur_lap = lap
            self._cur_samples = []
            self._lap_dirty = pm > 0
        if pm > 0 or getattr(snap, "game_state", 2) != 2:
            self._lap_dirty = True
        t = float(getattr(snap, "current_lap_time", 0.0))
        if dist > 0 and t > 0:
            lat = getattr(snap, "local_accel_ms2", (0.0, 0.0, 0.0))
            lat_g = abs(float(lat[0])) / G if lat else 0.0
            if not self._cur_samples or dist > self._cur_samples[-1][0]:
                self._cur_samples.append((dist, t, lat_g, float(snap.speed_kmh)))

    def _finish_lap(self, lap_time: float):
        info = self.info
        samples = self._cur_samples
        if self._lap_dirty or lap_time <= 20.0 or len(samples) < 20 or info is None:
            return
        length = info.length_m or (samples[-1][0] if samples else 0)
        if length <= 100 or samples[0][0] > 0.1 * length or samples[-1][0] < 0.85 * length:
            return  # incomplete coverage
        better = (not info.ref_trace) or lap_time < info.ref_lap_time - 0.01 or info.ref_lap_time <= 0
        if better:
            step = max(1, len(samples) // 400)
            info.ref_trace = [(d, t) for d, t, _, _ in samples[::step]]
            info.ref_lap_time = lap_time
            corners = self._detect_corners(samples)
            if corners:
                info.corners = corners
            self.save()
            logger.info(f"[TRACK] New reference lap {lap_time:.3f}s learned ({len(info.ref_trace)} pts, {len(info.corners)} corners).")

    @staticmethod
    def _detect_corners(samples) -> List[Corner]:
        """Contiguous stretches above 0.45 g lateral, merged when <60 m apart, min 25 m long."""
        segs = []
        cur = None
        for d, t, g, v in samples:
            if g >= 0.45:
                if cur is None:
                    cur = [d, d, g, d, v]  # start, end, peak g, apex dist, min speed
                else:
                    cur[1] = d
                    if v < cur[4]:
                        cur[4] = v
                        cur[3] = d
                    cur[2] = max(cur[2], g)
            elif cur is not None:
                segs.append(cur)
                cur = None
        if cur is not None:
            segs.append(cur)
        merged = []
        for s in segs:
            if merged and s[0] - merged[-1][1] < 60.0:
                m = merged[-1]
                m[1] = s[1]
                if s[4] < m[4]:
                    m[4], m[3] = s[4], s[3]
                m[2] = max(m[2], s[2])
            else:
                merged.append(list(s))
        out = []
        for s in merged:
            if s[1] - s[0] >= 25.0:
                out.append(Corner(len(out) + 1, s[0], s[3], s[1], s[4], s[2]))
        return out

    # ------------------------------------------------------------------ queries
    @property
    def has_reference(self) -> bool:
        return bool(self.info and len(self.info.ref_trace) >= 20 and self.info.ref_lap_time > 0)

    def _t_at(self, d: float) -> float:
        tr = self.info.ref_trace
        ds = [p[0] for p in tr]
        L = self.info.length_m or ds[-1]
        d = max(0.0, min(L, d))
        i = bisect_left(ds, d)
        if i <= 0:
            d0, t0 = 0.0, 0.0
            d1, t1 = tr[0]
        elif i >= len(tr):
            d0, t0 = tr[-1]
            d1, t1 = L, self.info.ref_lap_time
        else:
            d0, t0 = tr[i - 1]
            d1, t1 = tr[i]
        if d1 <= d0:
            return t0
        return t0 + (t1 - t0) * (d - d0) / (d1 - d0)

    def time_between(self, d_from: float, d_to: float, fallback_pace: float = 90.0) -> float:
        """Seconds (at reference pace) to drive from d_from to d_to going forward (wraps at the line)."""
        info = self.info
        L = info.length_m if info and info.length_m > 100 else 0.0
        if self.has_reference and L > 0:
            a = self._t_at(d_from % L)
            b = self._t_at(d_to % L)
            dt = b - a
            if dt < 0:
                dt += info.ref_lap_time
            return dt
        if L <= 0:
            return 0.0
        frac = ((d_to - d_from) % L) / L
        return frac * max(30.0, fallback_pace)

    def gap_seconds(self, d_ahead_total: float, d_behind_total: float, fallback_pace: float = 90.0) -> float:
        """Time gap between two cars given their total race distances in metres (ahead > behind)."""
        info = self.info
        L = info.length_m if info and info.length_m > 100 else 0.0
        if L <= 0:
            return 0.0
        diff = d_ahead_total - d_behind_total
        whole = int(diff // L)
        pace = info.ref_lap_time if self.has_reference else fallback_pace
        return whole * pace + self.time_between(d_behind_total % L, d_ahead_total % L, fallback_pace)

    def advance(self, d_total: float, seconds: float, fallback_pace: float = 90.0) -> float:
        """Total distance (m) reached after driving `seconds` at reference pace from total distance d_total."""
        info = self.info
        L = info.length_m if info and info.length_m > 100 else 0.0
        if L <= 0 or seconds <= 0:
            return d_total
        pace = info.ref_lap_time if self.has_reference else max(30.0, fallback_pace)
        laps, rem = divmod(seconds, pace)
        base = d_total + laps * L
        if not self.has_reference:
            return base + L * rem / pace
        d0 = base % L
        t_target = self._t_at(d0) + rem
        wrap = 0.0
        if t_target >= info.ref_lap_time:
            t_target -= info.ref_lap_time
            wrap = L
        # invert t(d) on the trace
        tr = info.ref_trace
        prev_d, prev_t = 0.0, 0.0
        d_new = L
        for dd, tt in tr + [(L, info.ref_lap_time)]:
            if tt >= t_target:
                d_new = prev_d + (dd - prev_d) * ((t_target - prev_t) / (tt - prev_t) if tt > prev_t else 0.0)
                break
            prev_d, prev_t = dd, tt
        return base - d0 + wrap + d_new

    def next_corner(self, d: float) -> Optional[Tuple[Corner, float]]:
        if not self.info or not self.info.corners:
            return None
        L = self.info.length_m or 1.0
        best = None
        for c in self.info.corners:
            ahead = (c.start_m - d) % L
            if best is None or ahead < best[1]:
                best = (c, ahead)
        return best

    def current_corner(self, d: float) -> Optional[Corner]:
        if not self.info:
            return None
        for c in self.info.corners:
            if c.start_m - 30 <= d <= c.end_m + 30:
                return c
        return None

    def describe(self, snap, fallback_pace: float = 90.0) -> str:
        """One-line position description for the LLM."""
        info = self.info
        L = info.length_m if info and info.length_m > 100 else float(getattr(snap, "track_length_m", 0.0))
        d = float(getattr(snap, "current_lap_distance", 0.0))
        if L <= 100:
            return f"Lap {snap.current_lap}, Sector {sector_label(snap.current_sector)}"
        pct = max(0.0, min(100.0, 100.0 * d / L))
        parts = [f"Lap {snap.current_lap}, {pct:.0f}% round ({d / 1000.0:.2f} of {L / 1000.0:.2f} km), Sector {sector_label(snap.current_sector)}"]
        to_line = self.time_between(d, L - 0.01, fallback_pace)
        parts.append(f"~{to_line:.0f}s to the start/finish line")
        pe = info.pit_entry_m if info else None
        if pe is not None:
            parts.append(f"~{self.time_between(d, pe, fallback_pace):.0f}s to pit entry")
        cc = self.current_corner(d)
        if cc:
            parts.append(f"in corner {cc.label()}")
        else:
            nc = self.next_corner(d)
            if nc:
                parts.append(f"next corner {nc[0].label()} in ~{nc[1]:.0f} m")
        return " | ".join(parts)

    def time_to_pit_entry(self, d: float, fallback_pace: float = 90.0) -> Optional[float]:
        if not self.info or self.info.pit_entry_m is None:
            return None
        return self.time_between(d, self.info.pit_entry_m, fallback_pace)

    def corner_map_summary(self) -> str:
        if not self.info or not self.info.corners:
            return "Corner map not learned yet (needs one clean lap)."
        return "; ".join(c.label() for c in self.info.corners)


track_model = TrackModel()
