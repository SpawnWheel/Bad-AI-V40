"""
Telemetry module: shared memory readers for Automobilista 2 and RaceRoom, with automatic sim detection.
"""
from .shared_memory import AMS2SharedMemory, AMS2TelemetryReader, GameState, SessionState, RaceState, FlagColour
from .r3e_shared_memory import R3ETelemetryReader
from .sim_detect import SIM_AMS2, SIM_R3E, SIM_DISPLAY_NAMES
from .collector import TelemetryCollector, TelemetrySnapshot

__all__ = [
    "AMS2SharedMemory",
    "AMS2TelemetryReader",
    "R3ETelemetryReader",
    "GameState",
    "SessionState",
    "RaceState",
    "FlagColour",
    "TelemetryCollector",
    "TelemetrySnapshot",
    "SIM_AMS2",
    "SIM_R3E",
    "SIM_DISPLAY_NAMES",
]
