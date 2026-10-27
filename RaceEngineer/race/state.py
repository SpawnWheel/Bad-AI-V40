"""
Race state manager and event detection engine for AMS2.
Tracks session transitions, lap history, and triggers race events.
"""

import dataclasses
import time
from typing import Optional, List, Callable, Dict, Any

from telemetry.shared_memory import (
    GameState,
    SessionState,
    RaceState,
    FlagColour,
    PitMode
)
from telemetry.collector import TelemetrySnapshot
from .events import RaceEvent, EventPriority, EventType
from .fuel import FuelTracker, FuelStatus, FuelLedger
from .track_model import track_model as _global_track_model, sector_label
from .race_distance import compute_race_distance, RaceDistance
from .car_track_stats import car_track_stats
from .tires import TireTracker, TireStatus
from .gaps import GapTracker, GapAnalysis
from .standings import timing_board
from .incidents import incident_tracker, IncidentTracker
from .pit_stop_tracker import pit_stop_tracker, PitStopTracker
from practice import PracticeSessionManager
from quali import QualiTracker
from utils.logging import logger
from utils.interaction_logger import interaction_logger

# The session clock only "runs out" from a reading at most this many seconds above zero
# (plus whatever time / laps passed since that reading)
CLOCK_EXPIRY_WINDOW_SEC = 60.0

def get_session_group(session_state: SessionState) -> str:
    """
    Classifies raw AMS2 session states into unified session groups.
    Countdown, Formation Lap, Race, and Cooldown Lap are all treated as the same RACE session.
    """
    if session_state in (SessionState.SESSION_PRACTICE, SessionState.SESSION_TEST):
        return "PRACTICE"
    elif session_state in (SessionState.SESSION_QUALIFY,):
        return "QUALIFY"
    elif session_state in (SessionState.SESSION_TIME_ATTACK,):
        return "TIME_ATTACK"
    elif session_state in (SessionState.SESSION_FORMATION_LAP, SessionState.SESSION_RACE):
        return "RACE"
    return "UNKNOWN"


class RaceStateManager:
    def __init__(
        self,
        verbosity: str = "balanced",
        incident_speed_threshold_kmh: float = 20.0,
        incident_duration_sec: float = 2.0,
        incident_cooldown_sec: float = 60.0,
        incident_retention_sec: float = 120.0,
        estimated_pit_loss_sec: float = 45.0,
        pit_stop_tracker_inst: Optional[PitStopTracker] = None,
        fuel_margin_laps: float = 0.0,
        pit_lap_preference: str = "latest",
        pit_arrival_reserve_laps: float = 0.6,
        box_reminder_sec: float = 20.0,
        timed_race_extra_laps="auto",
        track_model_inst=None,
        stats_store=None,
        fuel_margin_liters: float = 1.0,
        predict_leader_pit_stop: bool = True,
    ):
        self.verbosity = verbosity
        self.box_reminder_sec = float(box_reminder_sec)
        self.timed_race_extra_laps = timed_race_extra_laps
        self.predict_leader_pit_stop = bool(predict_leader_pit_stop)
        self._extra_laps_logged = False
        self.track_model = track_model_inst or _global_track_model
        self.stats_store = stats_store or car_track_stats
        self.estimated_pit_loss_sec = estimated_pit_loss_sec
        self.pit_stop_tracker = pit_stop_tracker_inst or pit_stop_tracker
        if self.pit_stop_tracker and estimated_pit_loss_sec != 45.0:
            self.pit_stop_tracker.default_pit_loss_sec = estimated_pit_loss_sec

        # Sub-trackers
        self.fuel_tracker = FuelTracker(margin_laps=fuel_margin_laps, pit_lap_preference=pit_lap_preference,
                                        arrival_reserve_laps=pit_arrival_reserve_laps, margin_liters=fuel_margin_liters)
        self.fuel_ledger = FuelLedger()
        self.pit_laps: set = set()            # player laps that included a pit visit
        self.start_position: int = 0
        self._box_call_lap: Dict[str, int] = {}  # which lap each box call was made for
        self._stint_start_lap_for_box = 0
        self._last_race_distance: Optional[RaceDistance] = None
        self._own_stop_loss = 0.0                 # predicted cost of our planned stop (timed-race distance)
        self.tire_tracker = TireTracker()
        self.gap_tracker = GapTracker()
        self.incident_tracker = IncidentTracker(
            speed_threshold_kmh=incident_speed_threshold_kmh,
            duration_sec=incident_duration_sec,
            cooldown_per_car_sec=incident_cooldown_sec,
            memory_retention_sec=incident_retention_sec
        )

        timing_board.track_model = self.track_model
        timing_board.pit_projector = self._project_competitor_pit

        # Specialized session managers
        self.practice_manager = PracticeSessionManager(stats_store=self.stats_store)
        self.quali_tracker = QualiTracker()
        self._practice_briefing_done = False

        # Tactical alert tracking flags
        self._5min_warning_done = False
        self._final_lap_done = False
        self._box_next_lap_done = False

        # State cache
        self.last_snapshot: Optional[TelemetrySnapshot] = None
        self.current_snapshot: Optional[TelemetrySnapshot] = None

        # Tracking state
        self.current_lap = 0
        self.current_position = 1
        self.current_flag = FlagColour.FLAG_NONE
        self.current_session = SessionState.SESSION_INVALID
        self.current_session_group = "UNKNOWN"
        self.current_track = ""
        self.current_variation = ""
        self.current_car = ""
        self._track_greeting_done = False
        self.lap_times: List[float] = []
        self.rain_was_active = False
        self._last_rain_density = 0.0
        self._session_needs_reset = True

        # Tactical alert tracking flags
        self._10min_warning_done = False
        self._5min_warning_done = False
        self._2min_warning_done = False
        self._1min_warning_done = False
        self._5laps_warning_done = False
        self._2laps_warning_done = False
        self._final_lap_done = False
        self._leader_finished_done = False
        self._chequered_flag_done = False
        self._box_next_lap_done = False
        self._fuel_warning_done = False
        self._last_fuel_liters: Optional[float] = None
        self._time_expired = False
        self._lap_at_time_expiry = 0
        self._leader_lap_at_time_expiry = 0
        self._last_yellow_flag_time = 0.0
        self._last_incident_time = 0.0
        self._last_incident_desc = ""

        # Leader pit stop tracking (driver_key -> is_in_pit)
        self._leader_pit_state: Dict[str, bool] = {}

        # Competitor pit exit tracking (driver_key -> previous pit_mode)
        self._competitor_pit_modes: Dict[str, int] = {}
        self._competitor_pit_exit_alerted: set[str] = set()

        # Event cooldowns (event_type -> timestamp)
        self._event_cooldowns: Dict[str, float] = {}

        # Event listeners
        self.event_listeners: List[Callable[[RaceEvent], None]] = []

        # Session reset listeners (e.g. app scheduler, brain history, audio player)
        self.session_reset_listeners: List[Callable[[str], None]] = []

    def register_event_listener(self, listener: Callable[[RaceEvent], None]):
        self.event_listeners.append(listener)

    def register_session_reset_listener(self, listener: Callable[[str], None]):
        self.session_reset_listeners.append(listener)

    def _emit_event(self, event_type: str, priority: EventPriority, message: str, details: Dict[str, Any], cooldown_seconds: float = 5.0):
        now = time.time()
        last_time = self._event_cooldowns.get(event_type, 0.0)
        if (now - last_time) < cooldown_seconds:
            return

        self._event_cooldowns[event_type] = now
        event = RaceEvent(
            event_type=event_type,
            priority=priority,
            timestamp=now,
            message=message,
            details=details
        )

        for listener in self.event_listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Error handling race event {event_type}: {e}")

    def reset_session(self, new_session_state: SessionState, snapshot: Optional[TelemetrySnapshot] = None):
        """
        Cleanly resets all session-specific trackers, calculations, alert flags,
        and event cooldowns for a fresh session.
        """
        session_group = get_session_group(new_session_state)
        logger.info(f"Resetting session state for new session: {session_group} ({new_session_state.name})")

        prev_group = self.current_session_group
        self.current_session = new_session_state
        self.current_session_group = session_group

        if snapshot:
            self.current_lap = snapshot.current_lap
            self.current_position = snapshot.position
            self.current_flag = snapshot.flag_colour
            self.current_track = snapshot.track_name
            self.current_variation = snapshot.track_variation
            self.current_car = snapshot.car_name
        else:
            self.current_lap = 0
            self.current_position = 1
            self.current_flag = FlagColour.FLAG_NONE

        self.last_snapshot = None
        self.current_snapshot = None
        self.lap_times.clear()
        self.in_pit = False
        self.rain_was_active = False
        self._last_rain_density = 0.0

        # Reset tactical alert tracking flags
        self._10min_warning_done = False
        self._5min_warning_done = False
        self._2min_warning_done = False
        self._1min_warning_done = False
        self._5laps_warning_done = False
        self._2laps_warning_done = False
        self._final_lap_done = False
        self._leader_finished_done = False
        self._chequered_flag_done = False
        self._box_next_lap_done = False
        self._fuel_warning_done = False
        self._last_fuel_liters = None
        self._time_expired = False
        self._lap_at_time_expiry = 0
        self._leader_lap_at_time_expiry = 0
        self._last_yellow_flag_time = 0.0
        self._last_incident_time = 0.0
        self._last_incident_desc = ""
        self._event_cooldowns.clear()
        self.pit_laps = set()
        self.start_position = 0
        self._box_call_lap = {}
        self._last_race_distance = None
        self._own_stop_loss = 0.0
        self.fuel_ledger.reset()

        # Reset all sub-trackers
        self.fuel_tracker.reset()
        if snapshot is not None:
            try:
                self.fuel_tracker.seed(self.stats_store.fuel_burn(snapshot.car_name, snapshot.track_name, snapshot.track_variation))
            except Exception as e:
                logger.debug(f"fuel seed failed: {e}")
            self.track_model.select_track(snapshot.track_name, snapshot.track_variation, snapshot.track_length_m)
            logger.info(f"[SESSION] Raw mSessionAdditionalLaps={getattr(snapshot, 'session_additional_laps', 'n/a')} | "
                        f"raw tyre pressure={getattr(snapshot, 'tyre_pressures_raw', ())} | raw brake bias={getattr(snapshot, 'brake_bias_raw', 'n/a')}")
        self.track_model.reset_live()
        self.tire_tracker.reset()
        self.gap_tracker.reset()
        self.incident_tracker.reset()
        self.pit_stop_tracker.reset_tracking()
        self._leader_pit_state.clear()
        self._competitor_pit_modes.clear()
        self._competitor_pit_exit_alerted.clear()
        timing_board.reset()
        self.practice_manager.reset_session()
        self.quali_tracker.reset()
        self._practice_briefing_done = False
        from race.strategy import strategy_engine
        strategy_engine.reset()

        if snapshot and snapshot.current_lap > 0:
            self.fuel_tracker.on_lap_started(snapshot.current_lap, snapshot.fuel_liters)
            self.tire_tracker.on_lap_started(snapshot.current_lap, snapshot.tyre_wear_pct)

        # Switch interaction logger to the session group
        sess_label = session_group if session_group != "UNKNOWN" else new_session_state.name.replace("SESSION_", "")
        interaction_logger.switch_session(sess_label)

        # Notify app-level listeners (MessageScheduler, AudioPlayer, Brain history)
        for listener in self.session_reset_listeners:
            try:
                listener(session_group)
            except Exception as e:
                logger.error(f"Error in session reset listener: {e}")

        # Emit SESSION_CHANGED event only on actual session transition
        if prev_group != "UNKNOWN" and prev_group != session_group:
            self._emit_event(
                EventType.SESSION_CHANGED,
                EventPriority.MEDIUM,
                f"Session transitioned to {sess_label}",
                {"session": new_session_state.name, "session_group": session_group},
                cooldown_seconds=5.0
            )

    def on_telemetry(self, snapshot: TelemetrySnapshot):
        """Called whenever a new TelemetrySnapshot arrives."""
        # 1. Front-end menu or exited: mark that session needs reset on next in-game entry
        if snapshot.game_state in (GameState.GAME_FRONT_END, GameState.GAME_EXITED):
            self._session_needs_reset = True
            self._track_greeting_done = False
            self.current_track = ""
            self.current_variation = ""
            self.current_snapshot = snapshot
            self.last_snapshot = None
            timing_board.reset()
            return

        session_group = get_session_group(snapshot.session_state)

        # 2. Check if a clean session reset is required
        needs_reset = False
        if self._session_needs_reset:
            # Entering in-game after front-end / game launch
            needs_reset = True
            self._session_needs_reset = False
        elif self.current_session_group != "UNKNOWN" and session_group != "UNKNOWN" and session_group != self.current_session_group:
            # Session group changed (e.g. PRACTICE -> QUALIFY, QUALIFY -> RACE)
            needs_reset = True
        elif snapshot.game_state == GameState.GAME_INGAME_RESTARTING:
            needs_reset = True
        elif self.last_snapshot is not None and self.current_lap > 1 and snapshot.current_lap <= 1 and snapshot.laps_completed == 0:
            needs_reset = True
        elif self.current_track and snapshot.track_name and (snapshot.track_name != self.current_track or snapshot.track_variation != self.current_variation):
            needs_reset = True
        elif self.current_car and snapshot.car_name and snapshot.car_name != self.current_car:
            needs_reset = True

        if needs_reset:
            self.reset_session(snapshot.session_state, snapshot)

        # Pit stop counts for every car need every transition, not just when a context is built
        try:
            timing_board.update_pit_counts(snapshot)
        except Exception as e:
            logger.debug(f"pit count update failed: {e}")

        # Track knowledge (pit entry/exit, reference lap, corners) + pit stop learning
        try:
            self.track_model.on_telemetry(snapshot)
        except Exception as e:
            logger.debug(f"track model error: {e}")
        self.pit_stop_tracker.on_telemetry(snapshot)

        # Fuel ledger: starts when the race goes green (grid fuel can still show the previous session's load)
        in_pits_now = snapshot.pit_mode.value > 0
        if in_pits_now and snapshot.current_lap > 0:
            self.pit_laps.add(snapshot.current_lap)
            self.fuel_tracker.mark_lap_dirty()
        if not self.fuel_ledger.started:
            if session_group == "RACE":
                if snapshot.race_state == RaceState.RACESTATE_RACING and snapshot.session_state == SessionState.SESSION_RACE:
                    self.fuel_ledger.start(snapshot.fuel_liters, max(1, snapshot.current_lap))
                    self.start_position = snapshot.position
            elif session_group in ("PRACTICE", "QUALIFY") and not in_pits_now and snapshot.speed_kmh > 5:
                self.fuel_ledger.start(snapshot.fuel_liters, max(1, snapshot.current_lap))
        self.fuel_ledger.on_sample(snapshot.fuel_liters, in_pits_now, snapshot.current_lap, snapshot.fuel_capacity_liters)

        # If paused or in garage setup menu, update cache but skip on-track dynamic driving event triggers
        if snapshot.game_state != GameState.GAME_INGAME_PLAYING:
            self.current_snapshot = snapshot
            return

        # Always feed session managers (practice telemetry & quali outlap tracking)
        self.practice_manager.on_telemetry(snapshot)
        quali_run = self.quali_tracker.on_telemetry(snapshot)

        # 0. Track Arrival / Initial Briefing
        track_key = f"{snapshot.track_name}|{snapshot.track_variation}"
        if snapshot.track_name and (track_key != f"{self.current_track}|{self.current_variation}" or not self._track_greeting_done):
            self.current_track = snapshot.track_name
            self.current_variation = snapshot.track_variation
            self.current_car = snapshot.car_name
            self._track_greeting_done = True

            variation_str = f" {snapshot.track_variation}" if snapshot.track_variation else ""
            if session_group == "PRACTICE":
                greeting_msg = (
                    f"We're here at {snapshot.track_name}{variation_str}. "
                    f"Radio check loud and clear. Let's head out for an initial 4 to 5 lap baseline run to check balance, scrub tyres, and get our fuel burn numbers."
                )
                self._emit_event(
                    EventType.PRACTICE_BRIEFING,
                    EventPriority.HIGH,
                    greeting_msg,
                    {
                        "track_name": snapshot.track_name,
                        "track_variation": snapshot.track_variation,
                        "ambient_temp": snapshot.ambient_temp_c,
                        "track_temp": snapshot.track_temp_c,
                        "rain_density": snapshot.rain_density,
                        "car_name": snapshot.car_name,
                    },
                    cooldown_seconds=30.0
                )
            else:
                length_km = (snapshot.track_length_m / 1000.0) if snapshot.track_length_m > 0 else 0.0
                length_str = f", {length_km:.1f} kilometers" if length_km > 0 else ""
                greeting_msg = (
                    f"We're here at {snapshot.track_name}{variation_str}{length_str}. "
                    f"Radio check loud and clear. Would you like a quick rundown on the circuit?"
                )
                self._emit_event(
                    EventType.TRACK_LOADED,
                    EventPriority.HIGH,
                    greeting_msg,
                    {
                        "track_name": snapshot.track_name,
                        "track_variation": snapshot.track_variation,
                        "track_length_m": snapshot.track_length_m,
                        "ambient_temp": snapshot.ambient_temp_c,
                        "track_temp": snapshot.track_temp_c,
                        "rain_density": snapshot.rain_density,
                        "car_name": snapshot.car_name,
                    },
                    cooldown_seconds=30.0
                )

        # Establish initial baseline on first snapshot of a session
        if self.last_snapshot is None:
            self.last_snapshot = snapshot
            self.current_snapshot = snapshot
            self.current_lap = snapshot.current_lap
            self.current_position = snapshot.position
            self.current_flag = snapshot.flag_colour
            self.current_session = snapshot.session_state
            self.current_session_group = session_group
            self.fuel_tracker.on_lap_started(snapshot.current_lap, snapshot.fuel_liters)
            self.tire_tracker.on_lap_started(snapshot.current_lap, snapshot.tyre_wear_pct)
            return

        prev = self.last_snapshot
        self.last_snapshot = self.current_snapshot
        self.current_snapshot = snapshot

        # 1b. Chequered Flag & Race Finished Check (Full in-lap radio silence)
        if snapshot.session_state == SessionState.SESSION_RACE:
            if not self._chequered_flag_done:
                if (
                    snapshot.flag_colour == FlagColour.FLAG_CHEQUERED
                    or snapshot.is_race_finished
                    or snapshot.race_state == RaceState.RACESTATE_FINISHED
                ):
                    self._chequered_flag_done = True
                    self.current_flag = snapshot.flag_colour
                    # Book the final lap before going quiet (lap time, fuel used, ledger)
                    if snapshot.current_lap > self.current_lap and self.current_lap > 0:
                        if snapshot.last_lap_time > 0:
                            self.lap_times.append(snapshot.last_lap_time)
                        fin = self.current_lap
                        was_pit = fin in self.pit_laps
                        self.fuel_ledger.on_lap_completed(fin, snapshot.fuel_liters, pit_lap=was_pit)
                        self.fuel_tracker.on_lap_started(snapshot.current_lap, snapshot.fuel_liters, was_pit_lap=was_pit)
                        self.current_lap = snapshot.current_lap
                    self.fuel_ledger.freeze()
                    self._emit_event(
                        EventType.FLAG_CHEQUERED,
                        EventPriority.CRITICAL,
                        f"Chequered flag! P{snapshot.position} finish. Race is complete, bring her home safely.",
                        {"position": snapshot.position},
                        cooldown_seconds=600.0
                    )
                    return
            else:
                # Race is complete and chequered flag was already emitted.
                # Enforce complete radio silence for all proactive events on the in-lap.
                return

        # 2. Lap Completion Detection (when racing or active practice/quali)
        if snapshot.race_state == RaceState.RACESTATE_RACING or session_group in ("PRACTICE", "QUALIFY", "TIME_ATTACK"):
            if snapshot.current_lap > self.current_lap and self.current_lap > 0:
                lap_time = snapshot.last_lap_time
                if lap_time > 0:
                    self.lap_times.append(lap_time)

                completed = self.current_lap
                was_pit = completed in self.pit_laps or (completed + 1) in self.pit_laps
                self.fuel_ledger.on_lap_completed(completed, snapshot.fuel_liters, pit_lap=was_pit)
                if session_group == "RACE" and not was_pit and completed >= 2:
                    try:
                        if self.fuel_ledger.lap_burns and self.fuel_ledger.lap_burns[-1][0] == completed and self.fuel_ledger.lap_burns[-1][2]:
                            self.stats_store.add_fuel_burns(snapshot.car_name, snapshot.track_name, snapshot.track_variation,
                                                            [self.fuel_ledger.lap_burns[-1][1]])
                        best = min([t for t in self.lap_times if t > 20] or [0]) if self.lap_times else 0
                        if lap_time > 20 and best and lap_time <= best * 1.05:
                            self.stats_store.add_lap_times(snapshot.car_name, snapshot.track_name, snapshot.track_variation, [lap_time])
                    except Exception as e:
                        logger.debug(f"stats persist failed: {e}")
                self.fuel_tracker.on_lap_started(snapshot.current_lap, snapshot.fuel_liters, was_pit_lap=was_pit)
                self.tire_tracker.on_lap_started(snapshot.current_lap, snapshot.tyre_wear_pct)

                if session_group == "QUALIFY":
                    # Only emit if this was a valid flying lap (outlaps are automatically filtered out by QualiTracker)
                    if quali_run is not None:
                        pb_tag = " (NEW PB!)" if quali_run.is_personal_best else ""
                        lap_msg = f"Flying lap: {lap_time:.2f}s{pb_tag}, P{quali_run.position_at_completion}. {quali_run.sector_analysis}."
                        self._emit_event(
                            EventType.QUALI_LAP_COMPLETED,
                            EventPriority.HIGH,
                            lap_msg,
                            {
                                "completed_lap": self.current_lap,
                                "lap_time": lap_time,
                                "position": quali_run.position_at_completion,
                                "is_pb": quali_run.is_personal_best,
                                "analysis": quali_run.sector_analysis
                            },
                            cooldown_seconds=3.0
                        )
                elif session_group == "PRACTICE":
                    # Keep radio clear during practice runs so driver is not distracted; debrief is given on demand
                    pass
                else:
                    # Race
                    lap_msg = f"Completed Lap {self.current_lap}. Last lap: {lap_time:.2f}s, Position: P{snapshot.position}"
                    self._emit_event(
                        EventType.LAP_COMPLETED,
                        EventPriority.MEDIUM,
                        lap_msg,
                        {
                            "completed_lap": self.current_lap,
                            "new_lap": snapshot.current_lap,
                            "lap_time": lap_time,
                            "position": snapshot.position
                        },
                        cooldown_seconds=3.0
                    )
            self.current_lap = snapshot.current_lap

        # 3. Position Change
        # Only in RACE (in Practice and Quali, positions change continuously on the timing screen and create chatter)
        if session_group == "RACE":
            is_lap1_launch = (
                snapshot.session_state == SessionState.SESSION_RACE
                and snapshot.current_lap <= 1
                and snapshot.current_lap_time < 30.0
            )
            if snapshot.position != self.current_position and snapshot.position > 0:
                if not is_lap1_launch:
                    if snapshot.position < self.current_position:
                        # Overtook / Gained position
                        self._emit_event(
                            EventType.POSITION_GAINED,
                            EventPriority.HIGH,
                            f"Position gained! Now P{snapshot.position}",
                            {"old_pos": self.current_position, "new_pos": snapshot.position},
                            cooldown_seconds=6.0
                        )
                    else:
                        # Dropped position
                        self._emit_event(
                            EventType.POSITION_LOST,
                            EventPriority.HIGH,
                            f"Position lost. Down to P{snapshot.position}",
                            {"old_pos": self.current_position, "new_pos": snapshot.position},
                            cooldown_seconds=6.0
                        )
                self.current_position = snapshot.position
        else:
            self.current_position = snapshot.position

        # 4. Flag Changes
        if snapshot.flag_colour != self.current_flag:
            now_t = time.time()
            if snapshot.flag_colour in (FlagColour.FLAG_YELLOW, FlagColour.FLAG_DOUBLE_YELLOW):
                # Suppress generic yellow callout if specific incident was already alerted in last 8s
                if (now_t - self._last_incident_time) < 8.0:
                    pass
                else:
                    self._last_yellow_flag_time = now_t
                    self._emit_event(
                        EventType.FLAG_YELLOW,
                        EventPriority.CRITICAL,
                        "Yellow flag on track! Caution, maintain delta.",
                        {"flag": snapshot.flag_colour.name},
                        cooldown_seconds=10.0
                    )
            elif snapshot.flag_colour == FlagColour.FLAG_WHITE_FINAL_LAP and not self._final_lap_done:
                self._final_lap_done = True
                self._emit_event(
                    EventType.FINAL_LAP,
                    EventPriority.HIGH,
                    "Final lap! White flag, this is the last lap to the chequered flag. Everything on the table.",
                    {"lap": snapshot.current_lap},
                    cooldown_seconds=600.0
                )
            elif snapshot.flag_colour == FlagColour.FLAG_CHEQUERED and not self._chequered_flag_done:
                self._chequered_flag_done = True
                self._emit_event(
                    EventType.FLAG_CHEQUERED,
                    EventPriority.CRITICAL,
                    f"Chequered flag! P{snapshot.position} finish.",
                    {"position": snapshot.position},
                    cooldown_seconds=600.0
                )
            elif snapshot.flag_colour == FlagColour.FLAG_BLACK:
                self._emit_event(
                    EventType.FLAG_BLACK,
                    EventPriority.CRITICAL,
                    "Black flag penalty received!",
                    {},
                    cooldown_seconds=15.0
                )
            self.current_flag = snapshot.flag_colour

        # 5. Heavy Collisions & Sudden Aero/Engine Damage
        if snapshot.collision_flag and snapshot.collision_magnitude > 2500.0:
            self._emit_event(
                EventType.COLLISION_DAMAGE,
                EventPriority.CRITICAL,
                f"Heavy impact detected (mag: {snapshot.collision_magnitude:.0f}). Check car handling.",
                {"magnitude": snapshot.collision_magnitude, "aero": snapshot.aero_damage, "engine": snapshot.engine_damage},
                cooldown_seconds=15.0
            )

        # 6. Engine Overheat
        if snapshot.water_temp_c > 115.0 or snapshot.oil_temp_c > 125.0:
            self._emit_event(
                EventType.ENGINE_OVERHEAT,
                EventPriority.CRITICAL,
                f"Engine overheating! Water {snapshot.water_temp_c:.0f}°C, Oil {snapshot.oil_temp_c:.0f}°C",
                {"water": snapshot.water_temp_c, "oil": snapshot.oil_temp_c},
                cooldown_seconds=30.0
            )

        # 7. Weather Lifecycle (Rain start, intensify, stop)
        current_rain = snapshot.rain_density
        if not self.rain_was_active:
            if current_rain >= 0.05:
                self.rain_was_active = True
                self._last_rain_density = current_rain
                self._emit_event(
                    EventType.WEATHER_RAIN_STARTED,
                    EventPriority.CRITICAL,
                    f"Rain reported! Track getting wet (density: {current_rain * 100:.0f}%).",
                    {"rain_density": current_rain},
                    cooldown_seconds=60.0
                )
        else:
            if current_rain <= 0.02:
                self.rain_was_active = False
                self._last_rain_density = 0.0
                self._emit_event(
                    EventType.WEATHER_RAIN_STOPPED,
                    EventPriority.HIGH,
                    "Rain has stopped! Radar showing 0%. Track will begin to dry out.",
                    {"rain_density": current_rain},
                    cooldown_seconds=60.0
                )
            elif current_rain >= (self._last_rain_density + 0.20):
                self._last_rain_density = current_rain
                self._emit_event(
                    EventType.WEATHER_RAIN_INTENSIFIED,
                    EventPriority.CRITICAL,
                    f"Rain is intensifying heavily! Density up to {current_rain * 100:.0f}%. High aquaplaning risk.",
                    {"rain_density": current_rain},
                    cooldown_seconds=45.0
                )
            elif current_rain <= (self._last_rain_density - 0.25):
                self._last_rain_density = current_rain
                self._emit_event(
                    EventType.WEATHER_RAIN_STOPPED,
                    EventPriority.HIGH,
                    f"Rain easing off, down to {current_rain * 100:.0f}%. Greasy track conditions.",
                    {"rain_density": current_rain},
                    cooldown_seconds=45.0
                )

        # 8. Lap Invalidated (Cut track)
        if snapshot.lap_invalidated and not prev.lap_invalidated:
            self._emit_event(
                EventType.LAP_INVALIDATED,
                EventPriority.HIGH,
                "Lap time invalidated for track limits.",
                {"lap": snapshot.current_lap},
                cooldown_seconds=15.0
            )

        # Track refueling to reset fuel alert if driver pitted and refueled
        if self._last_fuel_liters is not None and snapshot.fuel_liters > (self._last_fuel_liters + 5.0):
            self._fuel_warning_done = False
            self._event_cooldowns.pop(EventType.FUEL_EMERGENCY, None)
            self._event_cooldowns.pop(EventType.FUEL_CRITICAL, None)
        self._last_fuel_liters = snapshot.fuel_liters

        # 9. Fuel Checks
        fuel_status = self._analyze_fuel(snapshot)
        if session_group == "RACE" and snapshot.race_state == RaceState.RACESTATE_RACING:
            # Single fuel warning when entering the single-lap reserve (<= 1.1 laps)
            if fuel_status.is_empty_risk and not self._fuel_warning_done and not in_pits_now:
                self._fuel_warning_done = True
                can_finish = fuel_status.laps_remaining_fuel >= fuel_status.laps_remaining_race
                if not can_finish:
                    self._emit_event(
                        EventType.FUEL_EMERGENCY,
                        EventPriority.CRITICAL,
                        f"Fuel emergency! {snapshot.fuel_liters:.1f}L left = {fuel_status.laps_remaining_fuel:.1f} laps of fuel, "
                        f"but {fuel_status.laps_remaining_race:.1f} laps to the flag. Box immediately - this lap!",
                        {"liters": snapshot.fuel_liters, "laps": fuel_status.laps_remaining_fuel, "urgent": True},
                        cooldown_seconds=120.0
                    )
                else:
                    self._emit_event(
                        EventType.FUEL_CRITICAL,
                        EventPriority.HIGH,
                        f"Fuel light is on ({snapshot.fuel_liters:.1f}L, {fuel_status.laps_remaining_fuel:.1f} laps of fuel) but only "
                        f"{fuel_status.laps_remaining_race:.1f} laps to the flag - fuel is safe to the finish, no stop needed.",
                        {"liters": snapshot.fuel_liters, "laps": fuel_status.laps_remaining_fuel, "urgent": False},
                        cooldown_seconds=120.0
                    )
        elif session_group == "PRACTICE" and fuel_status.is_empty_risk and not self._fuel_warning_done and not in_pits_now:
            self._fuel_warning_done = True
            self._emit_event(
                EventType.FUEL_EMERGENCY,
                EventPriority.HIGH,
                f"Fuel level low in tank ({snapshot.fuel_liters:.1f}L remaining). Box to refuel.",
                {"liters": snapshot.fuel_liters},
                cooldown_seconds=60.0
            )

        # Pit calls: heads-up a lap early, "box this lap" at the START of the pit lap, reminder before pit entry
        if session_group == "RACE" and snapshot.race_state == RaceState.RACESTATE_RACING:
            self._check_box_calls(snapshot, fuel_status, in_pits_now)

        # 10. Tire Overheat / Wear (Race only)
        if session_group == "RACE":
            tire_status = self.tire_tracker.analyze(
                snapshot.tyre_compound,
                snapshot.tyre_tread_temps_c,
                snapshot.tyre_pressures_bar,
                snapshot.tyre_wear_pct
            )
            if tire_status.is_critical_wear:
                self._emit_event(
                    EventType.TIRE_PUNCTURE_RISK,
                    EventPriority.HIGH,
                    f"Critical tire wear detected (>75% on {tire_status.compound}). Puncture risk.",
                    {"wear": snapshot.tyre_wear_pct},
                    cooldown_seconds=60.0
                )
            elif tire_status.is_overheating and self.verbosity != "minimal":
                self._emit_event(
                    EventType.TIRE_OVERHEAT,
                    EventPriority.MEDIUM,
                    f"Tires overheating: {', '.join(tire_status.overheated_tires)}. Tread temps over 105°C.",
                    {"overheated": tire_status.overheated_tires},
                    cooldown_seconds=90.0
                )

        # 11. Battle Detection (Race only)
        if session_group == "RACE":
            is_pre_race = (
                snapshot.race_state == RaceState.RACESTATE_NOT_STARTED
                or snapshot.session_state == SessionState.SESSION_FORMATION_LAP
            )
            if not is_pre_race and not is_lap1_launch:
                gap_analysis = self.gap_tracker.analyze(
                    snapshot.position,
                    snapshot.split_time_ahead,
                    snapshot.split_time_behind,
                    snapshot.participants
                )
                if gap_analysis.in_battle and gap_analysis.battle_target:
                    self._emit_event(
                        EventType.BATTLE_ENTERED,
                        EventPriority.MEDIUM,
                        f"Battle active: {gap_analysis.battle_target}",
                        {"target": gap_analysis.battle_target},
                        cooldown_seconds=120.0
                    )

        # 12. Time / Lap Countdown Warnings (Timed Race & Lap Race)
        if snapshot.session_state in (SessionState.SESSION_RACE, SessionState.SESSION_FORMATION_LAP):
            # A. Timed Race Warnings
            if snapshot.total_laps == 0 and snapshot.event_time_remaining_sec > 0:
                t_rem = snapshot.event_time_remaining_sec
                prev_t = prev.event_time_remaining_sec if prev else t_rem

                # 10 Minutes warning (for races > 12 mins)
                if not self._10min_warning_done and t_rem <= 600.0 and prev_t > 600.0:
                    self._10min_warning_done = True
                    self._emit_event(
                        EventType.TIME_WARNING_10MIN,
                        EventPriority.HIGH,
                        f"Ten minutes remaining on the clock. {self._laps_to_go_phrase(snapshot)}",
                        {"time_remaining_sec": t_rem},
                        cooldown_seconds=600.0
                    )

                # 5 Minutes warning
                if not self._5min_warning_done and t_rem <= 300.0 and (prev_t > 300.0 or (prev and prev.current_lap == 0)):
                    self._5min_warning_done = True
                    mins = int(t_rem // 60)
                    secs = int(t_rem % 60)
                    self._emit_event(
                        EventType.TIME_WARNING_5MIN,
                        EventPriority.HIGH,
                        f"Five minutes remaining on the clock ({mins}m {secs:02d}s). {self._laps_to_go_phrase(snapshot)}",
                        {"time_remaining_sec": t_rem},
                        cooldown_seconds=600.0
                    )

                # 2 Minutes warning
                if not self._2min_warning_done and t_rem <= 120.0 and prev_t > 120.0:
                    self._2min_warning_done = True
                    mins = int(t_rem // 60)
                    secs = int(t_rem % 60)
                    self._emit_event(
                        EventType.TIME_WARNING_2MIN,
                        EventPriority.HIGH,
                        f"Two minutes remaining on the clock ({mins}m {secs:02d}s). {self._laps_to_go_phrase(snapshot)}",
                        {"time_remaining_sec": t_rem},
                        cooldown_seconds=600.0
                    )

                # 1 Minute warning
                if not self._1min_warning_done and t_rem <= 60.0 and prev_t > 60.0:
                    self._1min_warning_done = True
                    self._emit_event(
                        EventType.TIME_WARNING_1MIN,
                        EventPriority.HIGH,
                        f"One minute remaining on the clock. {self._laps_to_go_phrase(snapshot)}",
                        {"time_remaining_sec": t_rem},
                        cooldown_seconds=600.0
                    )

            # B. Lap-based Race Countdown
            elif snapshot.total_laps > 0:
                laps_left = snapshot.total_laps - snapshot.current_lap + 1
                prev_laps_left = (snapshot.total_laps - prev.current_lap + 1) if prev else laps_left

                if not self._5laps_warning_done and snapshot.total_laps >= 8 and laps_left == 5 and prev_laps_left > 5:
                    self._5laps_warning_done = True
                    self._emit_event(
                        EventType.LAPS_WARNING_5LAPS,
                        EventPriority.HIGH,
                        "Five laps remaining to the chequered flag.",
                        {"laps_remaining": 5},
                        cooldown_seconds=600.0
                    )

                if not self._2laps_warning_done and snapshot.total_laps >= 4 and laps_left == 2 and prev_laps_left > 2:
                    self._2laps_warning_done = True
                    self._emit_event(
                        EventType.LAPS_WARNING_2LAPS,
                        EventPriority.HIGH,
                        "Two laps to go. Penultimate lap.",
                        {"laps_remaining": 2},
                        cooldown_seconds=600.0
                    )

        # 13. Final Lap Alert
        is_final_lap = False
        if snapshot.session_state == SessionState.SESSION_RACE and not self._final_lap_done and not snapshot.is_race_finished:
            # Explicit AMS2 white flag
            if snapshot.flag_colour == FlagColour.FLAG_WHITE_FINAL_LAP:
                is_final_lap = True
            # Lap-based race: entered the final lap
            elif snapshot.total_laps > 0 and snapshot.current_lap == snapshot.total_laps and (prev is None or prev.current_lap < snapshot.total_laps):
                is_final_lap = True
            # Timed race: clock expiry and subsequent line crossing
            elif snapshot.total_laps == 0:
                # Detect when session clock expires. Only from a clock that was already about to run out:
                # a jump from minutes left straight to 0 / -1 is a bad reading, not the end of the race
                # (it made RaceRoom call "final lap" on lap 2 and push the flag out a lap per leader lap).
                if not self._time_expired:
                    if self._clock_ran_out(prev, snapshot):
                        self._time_expired = True
                        self._lap_at_time_expiry = snapshot.current_lap
                        leader_p = next((p for p in snapshot.participants if p.is_active and p.position == 1), None) if snapshot.participants else None
                        self._leader_lap_at_time_expiry = leader_p.lap if leader_p else snapshot.current_lap
                        logger.info(f"Session timer expired during Lap {snapshot.current_lap}. Final lap will start when crossing the start/finish line.")
                elif self._time_expired and self._lap_at_time_expiry > 0:
                    # Final lap begins when crossing the start/finish line after time expired
                    if snapshot.current_lap > self._lap_at_time_expiry:
                        is_final_lap = True

        if is_final_lap:
            self._final_lap_done = True
            self._emit_event(
                EventType.FINAL_LAP,
                EventPriority.HIGH,
                "Final lap! White flag, this is the last lap to the chequered flag. Everything on the table.",
                {"lap": snapshot.current_lap},
                cooldown_seconds=600.0
            )

        # 13b. Leader Crosses Line to Take Checkered Flag (with 15s proximity suppression)
        if (
            snapshot.session_state == SessionState.SESSION_RACE
            and not self._leader_finished_done
            and not self._chequered_flag_done
            and snapshot.position > 1
            and snapshot.participants
        ):
            leader = next((p for p in snapshot.participants if p.is_active and p.position == 1), None)
            if leader and leader.position != snapshot.position and not (snapshot.player_name and snapshot.player_name.strip().lower() == leader.name.strip().lower()):
                leader_has_finished = (
                    getattr(leader, "race_state", 2) == int(RaceState.RACESTATE_FINISHED)
                    or getattr(leader, "flag_colour", 0) == int(FlagColour.FLAG_CHEQUERED)
                    or (snapshot.total_laps > 0 and (leader.lap > snapshot.total_laps or getattr(leader, "laps_completed", 0) >= snapshot.total_laps))
                    or (snapshot.total_laps == 0 and self._time_expired and self._leader_lap_at_time_expiry > 0 and leader.lap > self._leader_lap_at_time_expiry + 1)
                )
                if leader_has_finished:
                    self._leader_finished_done = True
                    if snapshot.total_laps == 0 and self._leader_lap_at_time_expiry > 0:
                        observed = max(0, int(leader.lap) - 1 - self._leader_lap_at_time_expiry)
                        logger.info(f"[SESSION] Timed race: leader took the flag {observed} lap(s) after the clock ran out "
                                    f"(mSessionAdditionalLaps={getattr(snapshot, 'session_additional_laps', 'n/a')}, "
                                    f"config timed_race_extra_laps={self.timed_race_extra_laps}).")
                    track_len = snapshot.track_length_m if snapshot.track_length_m > 100.0 else 4000.0
                    lap_pace = max(30.0, snapshot.best_lap_time or snapshot.last_lap_time or 90.0)
                    avg_speed_mps = max(10.0, track_len / lap_pace, snapshot.speed_kmh / 3.6)
                    dist_to_line_m = max(0.0, track_len - getattr(snapshot, "current_lap_distance", 0.0))
                    time_to_line_sec = dist_to_line_m / avg_speed_mps
                    if snapshot.position == 2 and 0.0 < snapshot.split_time_ahead <= 15.0:
                        time_to_line_sec = min(time_to_line_sec, snapshot.split_time_ahead)

                    if time_to_line_sec > 15.0:
                        surname = leader.name.split()[-1] if leader.name else "Leader"
                        self._emit_event(
                            EventType.LEADER_FINISHED,
                            EventPriority.HIGH,
                            f"The leader {surname} has just crossed the line to take the chequered flag! Finish this lap to take the flag in P{snapshot.position}.",
                            {
                                "leader": leader.name,
                                "player_position": snapshot.position,
                                "time_to_line_sec": time_to_line_sec
                            },
                            cooldown_seconds=600.0
                        )
                    else:
                        logger.info(
                            f"Leader {leader.name} took the chequered flag, but skipping LEADER_FINISHED alert "
                            f"as player is due to cross the line in ~{time_to_line_sec:.1f}s (<= 15s threshold)."
                        )

        # 14. Race Finished / Chequered Flag (Fallback check if flag event missed)
        if snapshot.session_state == SessionState.SESSION_RACE and not self._chequered_flag_done:
            if (
                snapshot.flag_colour == FlagColour.FLAG_CHEQUERED
                or snapshot.is_race_finished
                or snapshot.race_state == RaceState.RACESTATE_FINISHED
            ):
                self._chequered_flag_done = True
                self._emit_event(
                    EventType.FLAG_CHEQUERED,
                    EventPriority.CRITICAL,
                    f"Chequered flag! P{snapshot.position} finish. Race is complete, bring her home safely.",
                    {"position": snapshot.position},
                    cooldown_seconds=600.0
                )

        # 15. Competitor Incidents & Stopped Cars
        # Suppress during pre-race grid, formation laps, and first 15 seconds of green flag race start
        is_grid_or_formation = (
            snapshot.race_state == RaceState.RACESTATE_NOT_STARTED
            or snapshot.session_state == SessionState.SESSION_FORMATION_LAP
        )
        is_launch_phase = (
            snapshot.session_state == SessionState.SESSION_RACE
            and snapshot.current_lap <= 1
            and snapshot.current_lap_time < 15.0
        )
        if not is_grid_or_formation and not is_launch_phase:
            new_incidents = self.incident_tracker.update(
                snapshot.participants,
                snapshot.player_name,
                time.time(),
                player_lap_dist=getattr(snapshot, "current_lap_distance", 0.0),
                track_length_m=snapshot.track_length_m,
                player_speed_kmh=snapshot.speed_kmh
            )
            now_t = time.time()
            for inc in new_incidents:
                self._last_incident_time = now_t
                self._last_incident_desc = inc.description

                # If yellow flag was recently triggered (within 8s), merge into single yellow caution call
                if (now_t - self._last_yellow_flag_time) < 8.0:
                    msg = f"Yellow flag caution: {inc.description}"
                else:
                    msg = f"Incident on track: {inc.description}"

                self._emit_event(
                    EventType.RIVAL_INCIDENT,
                    EventPriority.HIGH,
                    msg,
                    {
                        "driver": inc.driver_name,
                        "position": inc.position,
                        "sector": inc.sector,
                        "speed_kmh": inc.speed_kmh,
                        "is_ahead": inc.is_ahead,
                        "likely_to_see": inc.likely_to_see,
                        "relative_delta_m": inc.relative_delta_m,
                        "projected_delta_m": inc.projected_delta_m
                    },
                    cooldown_seconds=25.0
                )

        # 16. Race Leader Pit Stop Detection (Race only)
        if session_group == "RACE" and snapshot.participants:
            active_parts = [p for p in snapshot.participants if p.is_active and p.position > 0]
            leader = next((p for p in active_parts if p.position == 1), None)
            if leader and leader.position != snapshot.position and not (snapshot.player_name and snapshot.player_name.strip().lower() == leader.name.strip().lower()):
                l_key = leader.name.strip().lower() if leader.name else "leader"
                l_in_pit = (leader.pit_mode > 0)
                was_in_pit = self._leader_pit_state.get(l_key, False)
                if not was_in_pit and l_in_pit:
                    surname = leader.name.split()[-1] if leader.name else "Leader"
                    self._emit_event(
                        EventType.LEADER_PITTED,
                        EventPriority.MEDIUM,
                        f"The race leader P1 ({surname}) has pitted!",
                        {"driver": leader.name, "position": 1},
                        cooldown_seconds=900.0  # 15 minute cooldown
                    )
                self._leader_pit_state[l_key] = l_in_pit

        # 17. Competitor Pit Exit Proximity Detection (Race only)
        if session_group == "RACE" and snapshot.participants and not is_grid_or_formation and not is_launch_phase:
            track_len = snapshot.track_length_m if snapshot.track_length_m > 100.0 else 4000.0
            lap_pace = max(30.0, snapshot.best_lap_time or snapshot.last_lap_time or 90.0)
            avg_speed_mps = track_len / lap_pace

            for p in snapshot.participants:
                if not p.is_active or p.position <= 0:
                    continue
                # Skip the player
                if p.position == snapshot.position or (snapshot.player_name and snapshot.player_name.strip().lower() == p.name.strip().lower()):
                    continue

                p_key = p.name.strip().lower() if p.name else f"driver_{p.position}"
                prev_p_mode = self._competitor_pit_modes.get(p_key, 0)
                curr_p_mode = p.pit_mode

                # If competitor enters or sits in pit stall, clear alerted flag for new stop
                if curr_p_mode in (1, 2):
                    self._competitor_pit_exit_alerted.discard(p_key)

                # Check if competitor is driving out of pits or just emerging onto track
                is_exiting = (curr_p_mode == 3) or (prev_p_mode == 3 and curr_p_mode == 0)

                if is_exiting and p_key not in self._competitor_pit_exit_alerted:
                    # Calculate proximity along the circuit track spline with wraparound
                    player_dist = getattr(snapshot, "current_lap_distance", 0.0)
                    comp_dist = getattr(p, "lap_distance", 0.0)
                    delta_m = (player_dist - comp_dist)
                    delta_m = (delta_m + track_len / 2.0) % track_len - (track_len / 2.0)

                    time_gap_sec = abs(delta_m) / avg_speed_mps

                    if time_gap_sec <= 3.0:
                        self._competitor_pit_exit_alerted.add(p_key)
                        surname = p.name.split()[-1] if p.name else f"Car {p.position}"

                        # Determine if competitor is a lap down
                        is_lap_down = p.lap < snapshot.current_lap
                        laps_behind = max(0, snapshot.current_lap - p.lap)
                        if is_lap_down:
                            lap_desc = f"they are {laps_behind} laps down" if laps_behind > 1 else "they are a lap down"
                        else:
                            lap_desc = "racing on the same lap"

                        rel_pos = "ahead of you" if delta_m < 0 else "right behind you"
                        msg = f"Watch the pit exit! P{p.position} ({surname}) is exiting {rel_pos} (~{time_gap_sec:.1f}s gap, {lap_desc})."

                        self._emit_event(
                            EventType.PIT_EXIT_PROXIMITY,
                            EventPriority.HIGH,
                            msg,
                            {
                                "driver": p.name,
                                "position": p.position,
                                "time_gap_sec": time_gap_sec,
                                "is_lap_down": is_lap_down,
                                "laps_behind": laps_behind,
                                "relative_pos": rel_pos
                            },
                            cooldown_seconds=300.0  # 5 minute cooldown
                        )

                self._competitor_pit_modes[p_key] = curr_p_mode

    # ------------------------------------------------------------------ race distance, fuel & pit calls
    @staticmethod
    def _clock_ran_out(prev: Optional[TelemetrySnapshot], snapshot: TelemetrySnapshot) -> bool:
        """
        True when the session clock has just run out. The previous reading must have been about to
        expire - or enough time / laps passed since it to use up what was left. Minutes vanishing
        within one lap is a bad reading (RaceRoom, Zolder: lap 1 with 13 minutes left).
        """
        if prev is None or prev.event_time_remaining_sec <= 0.0 or snapshot.event_time_remaining_sec > 0.0:
            return False
        elapsed = max(0.0, snapshot.timestamp - prev.timestamp)
        laps = max(0, int(snapshot.current_lap) - int(prev.current_lap))
        pace = max(30.0, snapshot.best_lap_time or snapshot.last_lap_time or 0.0)
        return prev.event_time_remaining_sec <= CLOCK_EXPIRY_WINDOW_SEC + max(elapsed, laps * pace)

    def _extra_laps(self, snapshot: Optional[TelemetrySnapshot]) -> int:
        """
        Laps the leader runs AFTER the lap in which the clock hits zero.
        auto: AMS2's mSessionAdditionalLaps (0 = plain timed race, 1 = "+1 lap"). 0 is a real value, not
        "unknown" (treating it as 1 planned a whole extra lap of fuel). Out-of-range values fall back to 0.
        After the clock has run out, what the leader actually does overrides it: if he crossed the line
        and is still racing, there IS at least one more lap.
        """
        cfg = self.timed_race_extra_laps
        if isinstance(cfg, (int, float)) and not isinstance(cfg, bool):
            extra = max(0, int(cfg))
        else:
            raw = int(getattr(snapshot, "session_additional_laps", 0) or 0) if snapshot else 0
            extra = raw if 0 <= raw <= 5 else 0
        if snapshot is not None and self._time_expired and self._leader_lap_at_time_expiry > 0:
            leader = next((p for p in (snapshot.participants or ()) if p.is_active and p.position == 1), None)
            if leader is not None and getattr(leader, "race_state", 2) != int(RaceState.RACESTATE_FINISHED):
                seen = int(leader.lap or 0) - self._leader_lap_at_time_expiry
                if seen > extra:
                    extra = seen
        return extra

    def _leader_pending_stop_loss(self, snapshot: TelemetrySnapshot, pace: float, finish_lap_est: int) -> float:
        """
        Seconds the race leader will still lose to a pit stop before the flag (0 if none expected).
        Timed races only: a leader who still has to stop covers less distance before the clock runs out,
        so the race is a lap shorter more often than not. Expected when the leader hasn't stopped yet and
        the race can't be done on one tank (judged with our car - same-class racing assumed).
        """
        if not self.predict_leader_pit_stop or snapshot.total_laps > 0 or snapshot.event_time_remaining_sec <= 0:
            return 0.0
        parts = snapshot.participants or ()
        leader = next((p for p in parts if p.is_active and p.position == 1), None)
        if leader is None or snapshot.position == 1:
            return 0.0
        if leader.pit_mode in (1, 2, 3):
            return 0.0  # stopping right now - his distance already reflects it
        stops = timing_board.stops_for(leader.name)
        if stops is None or stops > 0:
            return 0.0
        avg = self.fuel_tracker.get_avg_consumption()
        cap = snapshot.fuel_capacity_liters if snapshot.fuel_capacity_liters > 1 else 0.0
        if not cap or finish_lap_est <= 0 or finish_lap_est * avg <= cap + 1.0:
            return 0.0  # one tank is enough -> no reason to expect a stop
        tr = self.pit_stop_tracker
        loss = tr.predict_ai_stop_loss(snapshot.track_name, snapshot.track_variation, pace)
        if loss is None:
            need = finish_lap_est * avg - cap + 1.0
            loss, _, _ = tr.predict_stop_loss(snapshot.track_name, snapshot.track_variation, snapshot.car_name, need)
        return max(0.0, float(loss or 0.0))

    def representative_pace(self, snapshot: Optional[TelemetrySnapshot]) -> float:
        """Leader's race pace (clean last lap or best), else ours, else what we learned earlier, else 90 s."""
        if snapshot is None:
            return 90.0
        leader = next((p for p in (snapshot.participants or ()) if p.is_active and p.position == 1), None)
        if leader is not None:
            best = leader.best_lap_time
            last = leader.last_lap_time
            if last > 20 and (best <= 20 or last <= best * 1.07):
                return last
            if best > 20:
                return best * 1.01
        clean = [t for t in self.lap_times[-5:] if t > 20]
        if clean:
            best = min(clean)
            clean = [t for t in clean if t <= best * 1.07]
            return sorted(clean)[len(clean) // 2]
        if snapshot.best_lap_time > 20:
            return snapshot.best_lap_time
        try:
            pace = self.stats_store.race_pace(snapshot.car_name, snapshot.track_name, snapshot.track_variation)
            if pace:
                return float(pace)
        except Exception:
            pass
        if self.track_model.has_reference:
            return self.track_model.info.ref_lap_time * 1.01
        return 90.0

    def get_race_distance(self, snapshot: Optional[TelemetrySnapshot] = None,
                          player_time_loss_sec: Optional[float] = None) -> Optional[RaceDistance]:
        """player_time_loss_sec: our own upcoming stop (None = the one the current fuel plan expects)."""
        snapshot = snapshot or self.current_snapshot
        store = player_time_loss_sec is None
        if store:
            player_time_loss_sec = self._own_stop_loss
        if snapshot is None or get_session_group(snapshot.session_state) != "RACE":
            return None
        pace = self.representative_pace(snapshot)
        extra = self._extra_laps(snapshot)
        expiry_lap = self._leader_lap_at_time_expiry if self._time_expired else 0
        rd = compute_race_distance(snapshot, pace, extra_laps=extra, leader_lap_at_expiry=expiry_lap,
                                   player_time_loss_sec=player_time_loss_sec)
        if rd.mode == "TIMED" and not rd.clock_expired:
            l_loss = self._leader_pending_stop_loss(snapshot, pace, rd.finish_lap)
            if l_loss > 0:
                rd = compute_race_distance(snapshot, pace, extra_laps=extra, leader_lap_at_expiry=expiry_lap,
                                           leader_time_loss_sec=l_loss, player_time_loss_sec=player_time_loss_sec,
                                           borderline_long_laps=0.06 + 0.1 * l_loss / max(30.0, pace))
        if store:
            self._last_race_distance = rd
        return rd

    def _laps_to_go_phrase(self, snapshot: TelemetrySnapshot) -> str:
        rd = self.get_race_distance(snapshot)
        if not rd or rd.mode == "NONE":
            return ""
        unsure = " (borderline - could be one lap more or less depending on pace)" if rd.borderline else ""
        return (f"Laps to go including this one: {rd.laps_incl_current} - chequered flag at the end of Lap {rd.finish_lap}{unsure}.")

    def _analyze_fuel(self, snapshot: TelemetrySnapshot) -> FuelStatus:
        rd = self.get_race_distance(snapshot)
        pace = self.representative_pace(snapshot)
        if rd is not None and rd.mode != "NONE" and not snapshot.is_race_finished:
            L = snapshot.track_length_m if snapshot.track_length_m > 100 else 0.0
            frac = (snapshot.current_lap_distance / L) if L else 0.0

            def run(r):
                return self.fuel_tracker.analyze(
                    snapshot.fuel_liters, snapshot.fuel_capacity_liters, snapshot.total_laps, snapshot.current_lap,
                    event_time_remaining_sec=snapshot.event_time_remaining_sec, lap_pace_sec=pace,
                    laps_to_go=r.laps_to_go, lap_fraction=frac, finish_lap=r.finish_lap,
                )
            rd = self.get_race_distance(snapshot, player_time_loss_sec=0.0)
            status = run(rd)
            used = rd
            # Timed race: our own stop costs time on the leader, so we may cover less distance before the flag.
            if status.needs_pit_stop and rd.mode == "TIMED" and not rd.clock_expired and snapshot.event_time_remaining_sec > 0:
                loss, _, _ = self.pit_stop_tracker.predict_stop_loss(
                    snapshot.track_name, snapshot.track_variation, snapshot.car_name, status.recommended_refuel_liters)
                rd2 = self.get_race_distance(snapshot, player_time_loss_sec=loss)
                if rd2 is not None and rd2.finish_lap < rd.finish_lap:
                    status2 = run(rd2)
                    if status2.needs_pit_stop:
                        status, used = status2, rd2
                self._own_stop_loss = float(loss or 0.0)
            else:
                self._own_stop_loss = 0.0
            notes = []
            if "leader's pending stop" in (used.note or ""):
                notes.append("leader still has to pit - included")
            # The safety net is small (~1 L), so if the clock is projected to run out just before the leader
            # reaches the line, plan the fuel for the extra lap rather than risk running dry.
            if used.mode == "TIMED" and not used.clock_expired and used.borderline_long:
                safe = dataclasses.replace(used, finish_lap=used.finish_lap + 1, laps_to_go=used.laps_to_go + 1.0)
                status = run(safe)
                notes.append("borderline - fuel planned for one extra lap")
            status.distance_note = "; ".join(notes)
            return status
        if rd is not None and snapshot.is_race_finished:
            return self.fuel_tracker.analyze(snapshot.fuel_liters, snapshot.fuel_capacity_liters, 0, snapshot.current_lap,
                                             laps_to_go=0.0)
        return self.fuel_tracker.analyze(
            snapshot.fuel_liters, snapshot.fuel_capacity_liters, snapshot.total_laps, snapshot.current_lap,
            event_time_remaining_sec=snapshot.event_time_remaining_sec, lap_pace_sec=pace,
        )

    def _check_box_calls(self, snapshot: TelemetrySnapshot, fuel: FuelStatus, in_pits: bool):
        """
        Pit call sequence for a planned stop at the END of lap k:
          1. heads-up at the start of lap k-1  ("box at the end of NEXT lap")
          2. "box THIS lap" at the start of lap k (plenty of warning, not when you're on top of pit entry)
          3. short "box, box" reminder ~box_reminder_sec before pit entry on lap k
        If the plan appears late (mid-lap), the relevant call is made immediately.
        """
        if in_pits:
            return
        # a completed stop clears the calls so a second stop gets its own sequence
        last = self.pit_stop_tracker.last_stop
        if last and self._box_call_lap and last.get("lap", 0) >= min(self._box_call_lap.values()):
            self._box_call_lap = {}
        if not fuel.needs_pit_stop or not fuel.pit_window_lap:
            return
        k = int(fuel.pit_window_lap)
        lap = snapshot.current_lap
        L = snapshot.track_length_m if snapshot.track_length_m > 100 else 0.0
        frac = (snapshot.current_lap_distance / L) if L else 0.0
        add_l = fuel.recommended_refuel_liters
        # plan freeze: once "box this lap" was called for lap k, keep it
        called_k = self._box_call_lap.get("this_lap")
        if called_k and called_k >= lap:
            k = called_k

        if lap == k - 1 and self._box_call_lap.get("heads_up") != k:
            self._box_call_lap["heads_up"] = k
            self._emit_event(
                EventType.BOX_NEXT_LAP,
                EventPriority.HIGH,
                f"Pit heads-up: box at the END of NEXT lap (Lap {k}), not this one. Plan ~{add_l:.0f} L of fuel. "
                f"Fuel now {snapshot.fuel_liters:.1f} L ({fuel.laps_remaining_fuel:.1f} laps).",
                {"pit_lap": k, "refuel_l": add_l},
                cooldown_seconds=30.0,
            )
        elif lap == k and self._box_call_lap.get("this_lap") != k:
            self._box_call_lap["this_lap"] = k
            self._box_call_lap.setdefault("heads_up", k)
            self._emit_event(
                EventType.BOX_THIS_LAP,
                EventPriority.HIGH,
                f"Box THIS lap - pit at the end of this lap (Lap {k}). ~{add_l:.0f} L going in. "
                f"You're {100 * frac:.0f}% into the lap.",
                {"pit_lap": k, "refuel_l": add_l},
                cooldown_seconds=30.0,
            )
        elif lap == k and self.box_reminder_sec > 0 and self._box_call_lap.get("reminder") != k:
            pace = self.representative_pace(snapshot)
            t_entry = self.track_model.time_to_pit_entry(snapshot.current_lap_distance, pace)
            due = (t_entry is not None and t_entry <= self.box_reminder_sec) or (t_entry is None and frac >= 0.85)
            if due and frac > 0.3:
                self._box_call_lap["reminder"] = k
                self._emit_event(
                    EventType.BOX_REMINDER,
                    EventPriority.HIGH,
                    f"Box box - pit entry coming up{f' in ~{t_entry:.0f}s' if t_entry is not None else ''}. ~{add_l:.0f} L.",
                    {"pit_lap": k, "time_to_entry": t_entry},
                    cooldown_seconds=30.0,
                )
        elif lap > k and fuel.laps_remaining_fuel <= 1.5 and self._box_call_lap.get("overdue") != lap:
            # plan missed (driver stayed out) - make sure he knows now
            self._box_call_lap["overdue"] = lap
            self._emit_event(
                EventType.BOX_THIS_LAP,
                EventPriority.CRITICAL,
                f"Box THIS lap - we missed the planned stop and only have {fuel.laps_remaining_fuel:.1f} laps of fuel.",
                {"pit_lap": lap, "refuel_l": add_l},
                cooldown_seconds=30.0,
            )

    def get_context_extras(self) -> dict:
        """Extra facts for the LLM context (track position, race distance, fuel ledger, our stops)."""
        snap = self.current_snapshot
        if snap is None:
            return {}
        ex: dict = {"pit_laps": set(self.pit_laps), "start_position": self.start_position}
        try:
            pace = self.representative_pace(snap)
            ex["track_position"] = self.track_model.describe(snap, pace)
        except Exception as e:
            logger.debug(f"track describe failed: {e}")
        grp = get_session_group(snap.session_state)
        if grp == "RACE":
            ex["race_distance"] = self.get_race_distance(snap)
        if grp == "PRACTICE":
            ex["corner_map"] = self.track_model.corner_map_summary()
        L = snap.track_length_m if snap.track_length_m > 100 else 0.0
        laps_done = max(0.0, (snap.current_lap - self.fuel_ledger.start_lap) + ((snap.current_lap_distance / L) if L else 0.0))
        if snap.is_race_finished and grp == "RACE":
            laps_done = float(len(self.lap_times)) or laps_done
        ex["fuel_ledger"] = self.fuel_ledger.summary(laps_done)
        stops = self.pit_stop_tracker.session_stops
        if stops:
            parts = []
            for st in stops:
                bit = f"Lap {st['lap']}: pit lane {st['lane_time']:.1f}s, stationary {st['stationary']:.1f}s"
                if st.get("fuel_added", 0) > 0.3:
                    bit += f", {st['fuel_added']:.1f} L added"
                if st.get("tyres"):
                    bit += ", tyres changed"
                if st.get("loss") is not None:
                    bit += f", cost ~{st['loss']:.1f}s vs staying out"
                parts.append(bit)
            ex["last_stop"] = " || ".join(parts)
        return ex

    def _project_competitor_pit(self, p, gap_sec: float) -> str:
        """Where will a car that is in the pit lane right now come out relative to us?"""
        snap = self.current_snapshot
        if snap is None:
            return ""
        key = p.name.strip().lower() if p.name else ""
        elapsed = self.pit_stop_tracker.competitor_pit_elapsed(key)
        typical = self.pit_stop_tracker.typical_ai_lane_time(snap.track_name, snap.track_variation)
        if typical is None:
            lanes = [st["lane_time"] for st in self.pit_stop_tracker.session_stops if st.get("lane_time")]
            typical = sorted(lanes)[len(lanes) // 2] if lanes else None
        if elapsed is None:
            return "IN PIT LANE (entry not seen)"
        if typical is None:
            return f"in pit lane for {elapsed:.0f}s (no typical stop time learned yet - rejoin unknown)"
        remaining = max(2.0, typical - elapsed) if p.pit_mode != 3 else 2.0
        tm = self.track_model
        L = snap.track_length_m if snap.track_length_m > 100 else 0.0
        me = next((x for x in snap.participants if x.position == snap.position), None)
        if not L or me is None:
            return f"in pit lane {elapsed:.0f}s of a typical {typical:.0f}s"
        pace = self.representative_pace(snap)
        my_total_then = tm.advance(me.lap * L + me.lap_distance, remaining, pace)
        exit_d = tm.info.pit_exit_m if (tm.info and tm.info.pit_exit_m is not None) else (p.lap_distance + remaining * 16.7) % L
        their_total = p.lap * L + p.lap_distance
        their_total_then = their_total + ((exit_d - p.lap_distance) % L)
        if their_total_then >= my_total_then:
            g = tm.gap_seconds(their_total_then, my_total_then, pace)
            where = f"~{g:.1f}s AHEAD of you"
        else:
            g = tm.gap_seconds(my_total_then, their_total_then, pace)
            where = f"~{g:.1f}s BEHIND you"
        return f"in pit {elapsed:.0f}s (typical stop {typical:.0f}s) -> projected to rejoin {where} (estimate)"

    def get_recent_incidents_summary(self) -> str:
        return self.incident_tracker.get_recent_incidents_summary(time.time())

    def get_race_storylines_summary(self) -> str:
        parts = self.current_snapshot.participants if self.current_snapshot else ()
        return self.incident_tracker.get_race_storylines_summary(parts, time.time())

    def get_current_fuel_status(self) -> FuelStatus:
        if not self.current_snapshot:
            return self.fuel_tracker.analyze(0, 100, 0, 0)
        return self._analyze_fuel(self.current_snapshot)

    def get_current_strategy(self):
        from race.strategy import strategy_engine
        fuel = self.get_current_fuel_status()
        tires = self.get_current_tire_status()
        track_name = self.current_snapshot.track_name if self.current_snapshot else ""
        track_var = self.current_snapshot.track_variation if self.current_snapshot else ""
        car = self.current_snapshot.car_name if self.current_snapshot else ""
        refuel = fuel.recommended_refuel_liters if fuel.needs_pit_stop else 0.0
        pit_loss, breakdown, _ = self.pit_stop_tracker.predict_stop_loss(track_name, track_var, car, refuel)
        return strategy_engine.compute_strategy(
            self.current_snapshot,
            fuel,
            tires,
            estimated_pit_loss_sec=pit_loss,
            pit_loss_breakdown=breakdown,
            track_model=self.track_model,
        )

    def get_current_tire_status(self) -> TireStatus:
        if not self.current_snapshot:
            return self.tire_tracker.analyze("Dry", (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
        return self.tire_tracker.analyze(
            self.current_snapshot.tyre_compound,
            self.current_snapshot.tyre_tread_temps_c,
            self.current_snapshot.tyre_pressures_bar,
            self.current_snapshot.tyre_wear_pct
        )

    def get_current_gap_analysis(self) -> GapAnalysis:
        if not self.current_snapshot:
            return self.gap_tracker.analyze(1, -1, -1, ())
        return self.gap_tracker.analyze(
            self.current_snapshot.position,
            self.current_snapshot.split_time_ahead,
            self.current_snapshot.split_time_behind,
            self.current_snapshot.participants
        )

    def get_timing_board_string(self) -> str:
        if not self.current_snapshot:
            return "No competitor timing data available."
        _, table_str = timing_board.analyze(self.current_snapshot)
        return table_str

    def get_lap_times(self) -> List[float]:
        return list(self.lap_times)

    def get_practice_debrief(self) -> str:
        return self.practice_manager.get_comparative_debrief(self.current_snapshot)

    def mark_practice_debrief_delivered(self):
        self.practice_manager.mark_debrief_delivered()

    def get_quali_summary(self) -> str:
        if not self.current_snapshot:
            return ""
        return self.quali_tracker.get_quali_status_summary(self.current_snapshot)


