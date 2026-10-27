"""
Event definitions and priority hierarchy for AMS2 race engineer.
"""

from enum import IntEnum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import time


class EventPriority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class EventType:
    # Critical
    FLAG_YELLOW = "flag_yellow"
    FLAG_DOUBLE_YELLOW = "flag_double_yellow"
    FLAG_BLACK = "flag_black"
    FLAG_CHEQUERED = "flag_chequered"
    COLLISION_DAMAGE = "collision_damage"
    ENGINE_OVERHEAT = "engine_overheat"
    FUEL_EMERGENCY = "fuel_emergency"
    WEATHER_RAIN_STARTED = "weather_rain_started"
    WEATHER_RAIN_INTENSIFIED = "weather_rain_intensified"

    # High
    POSITION_GAINED = "position_gained"
    POSITION_LOST = "position_lost"
    FUEL_CRITICAL = "fuel_critical"
    TIRE_PUNCTURE_RISK = "tire_puncture_risk"
    LAP_INVALIDATED = "lap_invalidated"
    BOX_THIS_LAP = "box_this_lap"
    BOX_NEXT_LAP = "box_next_lap"      # heads-up: box at the end of NEXT lap
    BOX_REMINDER = "box_reminder"      # short "box box" before pit entry on the pit lap
    PIT_EXIT_PROXIMITY = "pit_exit_proximity"
    TIME_WARNING_10MIN = "time_warning_10min"
    TIME_WARNING_5MIN = "time_warning_5min"
    TIME_WARNING_2MIN = "time_warning_2min"
    TIME_WARNING_1MIN = "time_warning_1min"
    LAPS_WARNING_5LAPS = "laps_warning_5laps"
    LAPS_WARNING_2LAPS = "laps_warning_2laps"
    FINAL_LAP = "final_lap"
    LEADER_FINISHED = "leader_finished"
    RIVAL_INCIDENT = "rival_incident"
    WEATHER_RAIN_STOPPED = "weather_rain_stopped"

    # Medium
    TRACK_LOADED = "track_loaded"
    PRACTICE_BRIEFING = "practice_briefing"
    QUALI_LAP_COMPLETED = "quali_lap_completed"
    LAP_COMPLETED = "lap_completed"
    BATTLE_ENTERED = "battle_entered"
    RIVAL_PITTED = "rival_pitted"
    LEADER_PITTED = "leader_pitted"
    TIRE_OVERHEAT = "tire_overheat"
    SESSION_CHANGED = "session_changed"

    # Low
    GAP_UPDATE = "gap_update"
    TIRE_STATUS = "tire_status"
    WEATHER_UPDATE = "weather_update"


@dataclass
class RaceEvent:
    event_type: str
    priority: EventPriority
    timestamp: float
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def is_critical(self) -> bool:
        return self.priority == EventPriority.CRITICAL

    def is_interruptible(self) -> bool:
        """Determines if this event should interrupt whatever the engineer is currently saying."""
        return self.priority == EventPriority.CRITICAL
