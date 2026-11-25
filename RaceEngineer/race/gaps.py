"""
Competitor gap, battle detection, and pace tracking for AMS2.
"""

import bisect
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
from collections import deque


class GapClock:
    """
    Time gaps between cars, measured the way the race logger does it: every car's total distance
    (laps completed x track length + distance into the lap) is recorded over time, and the gap from
    car A to the car behind it is how long ago A was where the car behind is now.

    AMS2's own split times (mSplitTimeAhead / Behind) belong to the camera's car and go badly wrong around
    pit stops - at Oulton they said a car 1.2 s up the road was 75 s ahead. This clock doesn't depend on them.
    """
    HISTORY_SEC = 180.0
    MAX_SPEED_MPS = 120.0        # anything faster between two samples is a counter glitch, not driving

    def __init__(self):
        self.reset()

    def reset(self):
        self._t: Dict[str, List[float]] = {}
        self._d: Dict[str, List[float]] = {}
        self._glitch_since: Dict[str, float] = {}
        self._track_len = 0.0

    @staticmethod
    def _key(name: str) -> str:
        return (name or "").strip().lower()

    def update(self, snapshot, now: Optional[float] = None):
        L = float(getattr(snapshot, "track_length_m", 0.0) or 0.0)
        parts = getattr(snapshot, "participants", None) or ()
        if L < 100 or not parts:
            return
        if self._track_len and abs(L - self._track_len) > 1.0:
            self.reset()
        self._track_len = L
        now = float(now if now is not None else (getattr(snapshot, "timestamp", 0.0) or time.time()))
        for p in parts:
            if not p.is_active or not p.name:
                continue
            k = self._key(p.name)
            total = max(0, int(p.laps_completed)) * L + max(0.0, float(p.lap_distance))
            ts, ds = self._t.setdefault(k, []), self._d.setdefault(k, [])
            if ts:
                dt = max(1e-3, now - ts[-1])
                dd = total - ds[-1]
                if dd < -30.0 or dd > self.MAX_SPEED_MPS * dt + 30.0:
                    # Lap counter and lap distance don't always flip on the same tick at the line.
                    # Skip the odd reading; if it persists it's real (a reset / tow to the pits): start again.
                    since = self._glitch_since.setdefault(k, now)
                    if now - since < 3.0:
                        continue
                    ts.clear()
                    ds.clear()
                elif dd < 0:
                    continue          # tiny jitter backwards: keep the history monotonic
            self._glitch_since.pop(k, None)
            ts.append(now)
            ds.append(total)
            cut = bisect.bisect_left(ts, now - self.HISTORY_SEC)
            if cut:
                del ts[:cut]
                del ds[:cut]

    def total(self, name: str) -> Optional[float]:
        ds = self._d.get(self._key(name))
        return ds[-1] if ds else None

    def gap(self, ahead: str, behind: str) -> Optional[float]:
        """
        Seconds from the car `ahead` to the car `behind` on the road (positive). None when unknown.
        More than a lap apart returns the time for the whole distance, so callers can see "lapped".
        """
        ka, kb = self._key(ahead), self._key(behind)
        ta, da = self._t.get(ka), self._d.get(ka)
        tb, db = self._t.get(kb), self._d.get(kb)
        if not ta or not tb:
            return None
        target = db[-1]
        now = tb[-1]
        if target > da[-1]:
            return None               # "behind" is actually ahead on the road
        i = bisect.bisect_left(da, target)
        if 0 < i < len(da):
            d0, d1 = da[i - 1], da[i]
            t0, t1 = ta[i - 1], ta[i]
            frac = (target - d0) / (d1 - d0) if d1 > d0 else 0.0
            return max(0.0, now - (t0 + frac * (t1 - t0)))
        if i == 0 and da[0] == target:
            return max(0.0, now - ta[0])
        # Further back than the history reaches: estimate from the distance and the car ahead's speed
        span_t = ta[-1] - ta[0]
        span_d = da[-1] - da[0]
        speed = span_d / span_t if span_t > 5 and span_d > 50 else 0.0
        if speed <= 0:
            return None
        return (da[-1] - target) / speed

    def road_traffic(self, me: str, max_sec: float, pace: float, skip=()) -> Tuple[list, list]:
        """
        Cars near us ON THE ROAD that are on a different lap.
        Returns (lappers, backmarkers): lappers are a lap or more up and behind us on the road (coming to lap
        us); backmarkers are a lap or more down and ahead of us on the road (we're coming to lap them).
        Each item: (name, road gap in seconds, whole laps between us).
        """
        L = self._track_len
        mine = self.total(me)
        if mine is None or L <= 0 or not pace or pace <= 20:
            return [], []
        speed = L / pace
        lappers, backmarkers = [], []
        km = self._key(me)
        for k, ds in self._d.items():
            if k == km or not ds or k in skip:
                continue
            diff = ds[-1] - mine
            ahead_m = diff % L                   # distance to him going forwards on the road
            behind_m = (-diff) % L               # distance to him looking backwards
            if diff > 0 and behind_m < ahead_m and diff > 0.5 * L:
                t = behind_m / speed
                if t <= max_sec:
                    lappers.append((k, t, int(round((diff + behind_m) / L))))
            elif diff < 0 and ahead_m < behind_m and -diff > 0.5 * L:
                t = ahead_m / speed
                if t <= max_sec:
                    backmarkers.append((k, t, int(round((-diff + ahead_m) / L))))
        lappers.sort(key=lambda x: x[1])
        backmarkers.sort(key=lambda x: x[1])
        return lappers, backmarkers

    def laps_apart(self, ahead: str, behind: str) -> float:
        a, b = self.total(ahead), self.total(behind)
        if a is None or b is None or self._track_len <= 0:
            return 0.0
        return (a - b) / self._track_len


@dataclass
class CompetitorInfo:
    name: str
    position: int
    lap: int
    gap_seconds: float
    trend: str  # "closing", "opening", "stable"
    is_in_battle: bool  # gap < 1.5s


@dataclass
class GapAnalysis:
    car_ahead: Optional[CompetitorInfo]
    car_behind: Optional[CompetitorInfo]
    leader_name: Optional[str]
    gap_to_leader: Optional[float]
    in_battle: bool
    battle_target: Optional[str]  # e.g., "P2 (Verstappen) 0.8s ahead"


class GapTracker:
    def __init__(self):
        self._ahead_history: deque[float] = deque(maxlen=5)
        self._behind_history: deque[float] = deque(maxlen=5)
        self._last_participants: Dict[str, int] = {}  # name -> position

    def reset(self):
        self._ahead_history.clear()
        self._behind_history.clear()
        self._last_participants.clear()

    def _determine_trend(self, history: deque[float]) -> str:
        if len(history) < 3:
            return "stable"
        diff = history[-1] - history[0]
        if diff < -0.3:
            return "closing"
        elif diff > 0.3:
            return "opening"
        return "stable"

    def analyze(
        self,
        player_pos: int,
        split_ahead: float,
        split_behind: float,
        participants: Tuple
    ) -> GapAnalysis:
        # Filter active participants sorted by position
        sorted_p = sorted([p for p in participants if p.is_active and p.position > 0], key=lambda x: x.position)

        car_ahead_info = None
        car_behind_info = None
        leader_name = sorted_p[0].name if sorted_p else None
        gap_to_leader = None

        if split_ahead >= 0:
            self._ahead_history.append(split_ahead)
        if split_behind >= 0:
            self._behind_history.append(split_behind)

        ahead_trend = self._determine_trend(self._ahead_history)
        behind_trend = self._determine_trend(self._behind_history)

        # Locate P-1 and P+1 in participant list
        for p in sorted_p:
            if p.position == player_pos - 1 and player_pos > 1:
                gap = split_ahead if split_ahead >= 0 else 0.0
                car_ahead_info = CompetitorInfo(
                    name=p.name,
                    position=p.position,
                    lap=p.lap,
                    gap_seconds=gap,
                    trend=ahead_trend,
                    is_in_battle=(gap > 0 and gap <= 1.5)
                )
            elif p.position == player_pos + 1:
                gap = split_behind if split_behind >= 0 else 0.0
                car_behind_info = CompetitorInfo(
                    name=p.name,
                    position=p.position,
                    lap=p.lap,
                    gap_seconds=gap,
                    trend=behind_trend,
                    is_in_battle=(gap > 0 and gap <= 1.5)
                )

        in_battle = False
        battle_target = None

        if car_ahead_info and car_ahead_info.is_in_battle:
            in_battle = True
            if car_ahead_info.gap_seconds < 0.6:
                battle_target = f"Right on the gearbox of P{car_ahead_info.position} ({car_ahead_info.name}) — looking for a move"
            else:
                battle_target = f"P{car_ahead_info.position} ({car_ahead_info.name}) {car_ahead_info.gap_seconds:.1f}s ahead"
        elif car_behind_info and car_behind_info.is_in_battle:
            in_battle = True
            battle_target = f"P{car_behind_info.position} ({car_behind_info.name}) {car_behind_info.gap_seconds:.1f}s behind"

        return GapAnalysis(
            car_ahead=car_ahead_info,
            car_behind=car_behind_info,
            leader_name=leader_name,
            gap_to_leader=gap_to_leader,
            in_battle=in_battle,
            battle_target=battle_target
        )
