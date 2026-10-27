"""
Competitor gap, battle detection, and pace tracking for AMS2.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
from collections import deque


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
