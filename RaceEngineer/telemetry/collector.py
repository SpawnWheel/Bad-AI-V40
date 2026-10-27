"""
Telemetry collector and snapshot generator for AMS2 and RaceRoom.
Runs background polling loop, detects which sim is running and provides clean Python data representations.
"""

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable

from .shared_memory import (
    AMS2TelemetryReader,
    AMS2SharedMemory,
    GameState,
    SessionState,
    RaceState,
    FlagColour,
    PitMode,
    TYRE_FRONT_LEFT,
    TYRE_FRONT_RIGHT,
    TYRE_REAR_LEFT,
    TYRE_REAR_RIGHT,
)
from .r3e_shared_memory import R3ETelemetryReader, R3EFrame
from .r3e_converter import R3EConverter
from .r3e_data import R3EData
from .sim_detect import SIM_AMS2, SIM_R3E, SIM_DISPLAY_NAMES, normalize_sim, running_sims
from utils.logging import logger


@dataclass(frozen=True)
class ParticipantSnapshot:
    name: str
    position: int
    lap: int
    sector: int
    lap_distance: float
    is_active: bool
    last_lap_time: float = 0.0
    best_lap_time: float = 0.0
    sector1_time: float = 0.0
    sector2_time: float = 0.0
    sector3_time: float = 0.0
    pit_mode: int = 0
    speed_kmh: float = 0.0
    race_state: int = 2
    laps_completed: int = 0
    flag_colour: int = 0
    # RaceRoom only: what each rival drives and their online driver rating / reputation (0 = not reported)
    car_name: str = ""
    car_class: str = ""
    rating: float = 0.0
    reputation: float = 0.0


@dataclass(frozen=True)
class TelemetrySnapshot:
    timestamp: float
    game_state: GameState
    session_state: SessionState
    race_state: RaceState

    # Vehicle
    car_name: str
    car_class_name: str
    speed_kmh: float
    rpm: float
    max_rpm: float
    gear: int
    throttle: float
    brake: float
    steering: float
    clutch: float

    # Track & Event
    track_name: str
    track_variation: str
    track_length_m: float
    num_sectors: int
    total_laps: int
    lap_invalidated: bool

    # Driver Timing
    player_name: str
    position: int
    current_lap: int
    laps_completed: int
    current_sector: int
    current_lap_time: float
    last_lap_time: float
    best_lap_time: float
    split_time_ahead: float
    split_time_behind: float

    # Sectors
    sector1_time: float
    sector2_time: float
    sector3_time: float
    best_sector1_time: float
    best_sector2_time: float
    best_sector3_time: float

    # Powertrain & Fluids
    fuel_level_pct: float
    fuel_liters: float
    fuel_capacity_liters: float
    water_temp_c: float
    oil_temp_c: float
    oil_pressure_kpa: float

    # Tyres & Brakes (FL, FR, RL, RR)
    tyre_compound: str
    tyre_tread_temps_c: tuple
    tyre_carcass_temps_c: tuple
    tyre_pressures_bar: tuple
    tyre_wear_pct: tuple
    brake_temps_c: tuple

    # Weather & Environment
    ambient_temp_c: float
    track_temp_c: float
    rain_density: float
    wind_speed_ms: float

    # Flags, Pit & Damage
    flag_colour: FlagColour
    pit_mode: PitMode
    aero_damage: float
    engine_damage: float
    collision_flag: bool
    collision_magnitude: float
    drs_state: int
    ers_mode: int

    # Session Timing & Status
    event_time_remaining_sec: float = -1.0
    session_duration_min: float = 0.0
    is_race_finished: bool = False
    enforced_pit_stop_lap: int = -1
    current_lap_distance: float = 0.0

    # All Competitors
    participants: tuple = field(default_factory=tuple)

    # Deep Diagnostics & In-Car Settings
    suspension_travel_mm: tuple = (0.0, 0.0, 0.0, 0.0)
    suspension_velocity_ms: tuple = (0.0, 0.0, 0.0, 0.0)
    ride_height_mm: tuple = (0.0, 0.0, 0.0, 0.0)
    tyre_slip_speed_ms: tuple = (0.0, 0.0, 0.0, 0.0)
    tyre_temps_inner_c: tuple = (0.0, 0.0, 0.0, 0.0)
    tyre_temps_center_c: tuple = (0.0, 0.0, 0.0, 0.0)
    tyre_temps_outer_c: tuple = (0.0, 0.0, 0.0, 0.0)
    brake_bias_pct: float = 0.0
    abs_setting: int = 0
    tc_setting: int = 0
    turbo_boost_pressure: float = 0.0
    engine_torque_nm: float = 0.0
    clutch_slipping: bool = False
    clutch_temp_c: float = 0.0

    # Vehicle dynamics (for handling / driving-style analysis)
    local_accel_ms2: tuple = (0.0, 0.0, 0.0)       # x = lateral, y = vertical, z = longitudinal (car frame)
    angular_velocity_rads: tuple = (0.0, 0.0, 0.0)  # y = yaw rate
    local_velocity_ms: tuple = (0.0, 0.0, 0.0)
    tyre_rps: tuple = (0.0, 0.0, 0.0, 0.0)
    ride_height_cm: tuple = (0.0, 0.0, 0.0, 0.0)
    tyre_pressures_raw: tuple = (0.0, 0.0, 0.0, 0.0)
    brake_bias_raw: float = -1.0
    session_additional_laps: int = 0
    num_participants: int = 0

    # Which sim produced this snapshot and what it can report
    sim: str = SIM_AMS2
    weather_available: bool = True                 # RaceRoom reports no weather / temperatures
    tyre_temp_window_c: tuple = ()                 # per tyre (cold, optimal, hot) when the sim provides it
    brake_temp_window_c: tuple = ()


class TelemetryCollector:
    """Manages background telemetry polling and snapshot dispatching (default: 2Hz = 0.5s)."""

    # How often to re-check that the connected sim's process is still alive (stale mappings)
    LIVENESS_CHECK_SEC = 5.0

    def __init__(self, poll_rate_hz: int = 2, shm_tag: str = "$pcars2$",
                 tyre_pressure_unit: str = "auto", invert_brake_bias: bool = False,
                 sim: str = "auto", r3e_data_path: Optional[str] = None,
                 r3e_brake_bias_is_rear_share: bool = True):
        # Unit of mAirPressure as AMS2 actually writes it: auto | kpa | psi | bar.
        # SharedMemory.h says PSI, but AMS2 has been observed writing values around 160-260 for
        # racing slicks, which only make sense as kPa (1.6-2.6 bar). "auto" decides by magnitude.
        self.tyre_pressure_unit = (tyre_pressure_unit or "auto").strip().lower()
        # mBrakeBias is documented as 0..1 without saying which axle. False = value is the FRONT share.
        self.invert_brake_bias = bool(invert_brake_bias)
        self.poll_rate_hz = max(1, min(60, poll_rate_hz))
        self.interval = 1.0 / self.poll_rate_hz
        self.reader = AMS2TelemetryReader(shm_tag)

        # RaceRoom
        self.r3e_reader = R3ETelemetryReader()
        self.r3e_converter = R3EConverter(R3EData(r3e_data_path), r3e_brake_bias_is_rear_share)

        # "auto" = follow whichever supported sim is running
        self.sim_preference = normalize_sim(sim)
        self.active_sim: Optional[str] = None
        self._last_liveness_check = 0.0
        self._r3e_layout_checked = False

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Rolling history of recent snapshots
        self.history: deque[TelemetrySnapshot] = deque(maxlen=300)  # 30 seconds at 10Hz
        self.latest: Optional[TelemetrySnapshot] = None

        # Cached last-known-good on-track tyre temps (retained in pit box / garage)
        self._last_good_inner_temps: Optional[List[float]] = None
        self._last_good_center_temps: Optional[List[float]] = None
        self._last_good_outer_temps: Optional[List[float]] = None
        self._last_good_tread_temps: Optional[List[float]] = None

        # Listeners: callbacks invoked on each valid snapshot
        self.listeners: List[Callable[[TelemetrySnapshot], None]] = []

    def _pressure_to_bar(self, v: float) -> float:
        """Converts a raw mAirPressure value to bar."""
        if v <= 0.0:
            return 0.0
        unit = self.tyre_pressure_unit
        if unit == "auto":
            if v > 80.0:
                unit = "kpa"      # 80..400 -> 0.8..4.0 bar
            elif v > 6.0:
                unit = "psi"      # 6..80 psi
            else:
                unit = "bar"
        if unit == "kpa":
            return v / 100.0
        if unit == "psi":
            return v * 0.0689476
        return v

    def _cache_tyre_temps(self, inner, center, outer, tread, is_on_track: bool, in_pits: bool):
        """
        Preserve / cache tyre temperatures: sims drop tyre temps to 0 in the pit box / garage.
        On track at speed the cache is refreshed; in the pits or when readings drop to 0 the last
        known good temperatures are restored.
        """
        has_valid_temps = any(t > 10.0 for t in inner) or any(t > 10.0 for t in outer)
        if is_on_track and has_valid_temps:
            self._last_good_inner_temps = list(inner)
            self._last_good_center_temps = list(center)
            self._last_good_outer_temps = list(outer)
            self._last_good_tread_temps = list(tread)
        else:
            temps_zeroed = all(t < 5.0 for t in inner) and all(t < 5.0 for t in outer)
            if (in_pits or temps_zeroed) and self._last_good_inner_temps is not None:
                inner = list(self._last_good_inner_temps)
                center = list(self._last_good_center_temps)
                outer = list(self._last_good_outer_temps)
                if self._last_good_tread_temps is not None and all(t < 5.0 for t in tread):
                    tread = list(self._last_good_tread_temps)
        return inner, center, outer, tread

    def reset_tyre_cache(self):
        """Clears cached tyre temperatures for a clean session start."""
        with self._lock:
            self._last_good_inner_temps = None
            self._last_good_center_temps = None
            self._last_good_outer_temps = None
            self._last_good_tread_temps = None

    def set_poll_rate_hz(self, poll_rate_hz: int):
        """Dynamically adjusts telemetry polling frequency (e.g. 10Hz in Practice, 2Hz in Quali/Race)."""
        rate = max(1, min(60, poll_rate_hz))
        if rate != self.poll_rate_hz:
            self.poll_rate_hz = rate
            self.interval = 1.0 / rate
            logger.info(f"Telemetry polling rate updated to {rate}Hz ({self.interval:.3f}s interval).")

    def register_listener(self, cb: Callable[[TelemetrySnapshot], None]):
        self.listeners.append(cb)

    def start(self):
        """Starts background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="TelemetryCollector", daemon=True)
        self._thread.start()
        logger.info(f"Telemetry collector started at {self.poll_rate_hz}Hz polling.")

    def stop(self):
        """Stops background polling thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.reader.disconnect()
        logger.info("Telemetry collector stopped.")

    def _convert_snapshot(self, raw: AMS2SharedMemory) -> TelemetrySnapshot:
        """Converts raw ctypes struct into an immutable TelemetrySnapshot dataclass."""
        dec = self.reader.decode_str
        viewed_idx = raw.mViewedParticipantIndex
        if viewed_idx < 0 or viewed_idx >= raw.mNumParticipants:
            viewed_idx = 0

        # Extract participants with full timing information
        participants_list = []
        for i in range(min(raw.mNumParticipants, 64)):
            p = raw.mParticipantInfo[i]
            p_name = dec(p.mName)
            if p_name and p.mIsActive:
                participants_list.append(ParticipantSnapshot(
                    name=p_name,
                    position=int(p.mRacePosition),
                    lap=int(p.mCurrentLap),
                    sector=int(p.mCurrentSector),
                    lap_distance=float(p.mCurrentLapDistance),
                    is_active=bool(p.mIsActive),
                    last_lap_time=max(0.0, float(raw.mLastLapTimes[i])),
                    best_lap_time=max(0.0, float(raw.mFastestLapTimes[i])),
                    sector1_time=max(0.0, float(raw.mCurrentSector1Times[i])),
                    sector2_time=max(0.0, float(raw.mCurrentSector2Times[i])),
                    sector3_time=max(0.0, float(raw.mCurrentSector3Times[i])),
                    pit_mode=int(raw.mPitModes[i]),
                    speed_kmh=max(0.0, float(raw.mSpeeds[i]) * 3.6),
                    race_state=int(raw.mRaceStates[i]) if hasattr(raw, "mRaceStates") else 2,
                    laps_completed=int(p.mLapsCompleted),
                    flag_colour=int(raw.mHighestFlagColours[i]) if hasattr(raw, "mHighestFlagColours") else 0
                ))

        player_p = raw.mParticipantInfo[viewed_idx]
        player_name = dec(player_p.mName) if player_p.mIsActive else "Driver"

        fuel_cap = max(1.0, float(raw.mFuelCapacity))
        fuel_liters = float(raw.mFuelLevel) * fuel_cap

        # Tyre compound (first wheel)
        tyre_comp = dec(raw.mTyreCompound[0]) or "Dry"

        # Resolve tyre tread and carcass temperatures in Celsius
        tread_temps = []
        carcass_temps = []
        pressures = []
        for i in range(4):
            # 1. Inner/Center/Outer average (Celsius)
            tl = float(raw.mTyreTempLeft[i])
            tc = float(raw.mTyreTempCenter[i])
            tr = float(raw.mTyreTempRight[i])
            if tl > 5.0 and tc > 5.0 and tr > 5.0:
                tread_c = (tl + tc + tr) / 3.0
            elif float(raw.mTyreTemp[i]) > 5.0:
                tread_c = float(raw.mTyreTemp[i])
            elif float(raw.mTyreTreadTemp[i]) > 150.0:
                tread_c = float(raw.mTyreTreadTemp[i]) - 273.15
            else:
                tread_c = 0.0
            tread_temps.append(tread_c)

            # Carcass (Kelvin in shared memory)
            carc_k = float(raw.mTyreCarcassTemp[i])
            if carc_k > 150.0:
                carcass_temps.append(carc_k - 273.15)
            else:
                carcass_temps.append(tread_c)

            # Pressures -> bar (unit auto-detected, see _pressure_to_bar)
            pressures.append(self._pressure_to_bar(float(raw.mAirPressure[i])))

        # Collision detection from AMS2 opponent collision fields
        collision_flag = (raw.mLastOpponentCollisionIndex != -1 and raw.mLastOpponentCollisionMagnitude > 0.0)
        collision_mag = float(raw.mLastOpponentCollisionMagnitude)

        player_flag_val = raw.mHighestFlagColours[viewed_idx] if hasattr(raw, "mHighestFlagColours") and viewed_idx < len(raw.mHighestFlagColours) else raw.mHighestFlagColour
        active_flag = FlagColour(raw.mHighestFlagColour) if raw.mHighestFlagColour in FlagColour._value2member_map_ else FlagColour.FLAG_NONE
        # Disqualification / black flag must specifically target the player, not an AI rival
        if active_flag == FlagColour.FLAG_BLACK and player_flag_val != int(FlagColour.FLAG_BLACK):
            active_flag = FlagColour(player_flag_val) if player_flag_val in FlagColour._value2member_map_ else FlagColour.FLAG_NONE

        # Resolve player best lap time from AMS2 shared memory
        player_best_lap = 0.0
        if hasattr(raw, "mBestLapTime") and raw.mBestLapTime > 0:
            player_best_lap = float(raw.mBestLapTime)
        elif hasattr(raw, "mFastestLapTimes") and viewed_idx < len(raw.mFastestLapTimes) and raw.mFastestLapTimes[viewed_idx] > 0:
            player_best_lap = float(raw.mFastestLapTimes[viewed_idx])
        elif hasattr(raw, "mPersonalFastestLapTime") and raw.mPersonalFastestLapTime > 0:
            player_best_lap = float(raw.mPersonalFastestLapTime)

        # Tyre inner, center, outer temperatures (Celsius)
        # Left tyres (FL=0, RL=2): Left is outer shoulder, Right is inner shoulder
        # Right tyres (FR=1, RR=3): Left is inner shoulder, Right is outer shoulder
        inner_temps = [
            float(raw.mTyreTempRight[0]),  # FL inner
            float(raw.mTyreTempLeft[1]),   # FR inner
            float(raw.mTyreTempRight[2]),  # RL inner
            float(raw.mTyreTempLeft[3]),   # RR inner
        ]
        center_temps = [float(raw.mTyreTempCenter[i]) for i in range(4)]
        outer_temps = [
            float(raw.mTyreTempLeft[0]),   # FL outer
            float(raw.mTyreTempRight[1]),  # FR outer
            float(raw.mTyreTempLeft[2]),   # RL outer
            float(raw.mTyreTempRight[3]),  # RR outer
        ]

        # AMS2 shared memory drops tyre temps to 0 when in pit box / garage
        speed_kmh_raw = float(raw.mSpeed) * 3.6
        inner_temps, center_temps, outer_temps, tread_temps = self._cache_tyre_temps(
            inner_temps, center_temps, outer_temps, tread_temps,
            is_on_track=int(raw.mPitMode) == 0 and speed_kmh_raw > 15.0,
            in_pits=int(raw.mPitMode) > 0,
        )

        # Suspension travel & ride height in millimeters (convert m -> mm if in meters)
        susp_travel_mm = []
        susp_velocity_ms = []
        ride_height_mm = []
        ride_height_cm = []
        tyre_slip_ms = []
        for i in range(4):
            st = float(raw.mSuspensionTravel[i])
            susp_travel_mm.append(st * 1000.0 if 0.0 < abs(st) < 2.0 else st)
            susp_velocity_ms.append(float(raw.mSuspensionVelocity[i]))
            rh_cm = float(raw.mRideHeight[i])  # SharedMemory.h: [ UNITS = cm ]
            ride_height_cm.append(rh_cm)
            ride_height_mm.append(rh_cm * 10.0)
            tyre_slip_ms.append(float(raw.mTyreSlipSpeed[i]))

        brake_bias_raw = float(getattr(raw, "mBrakeBias", -1.0))
        if 0.0 < brake_bias_raw <= 1.0:
            front_frac = (1.0 - brake_bias_raw) if self.invert_brake_bias else brake_bias_raw
            brake_bias_val = front_frac * 100.0
        else:
            brake_bias_val = 0.0  # unset / not available on this car

        abs_val = int(getattr(raw, "mAntiLockSetting", 0))
        tc_val = int(getattr(raw, "mTractionControlSetting", 0))
        turbo_val = float(getattr(raw, "mTurboBoostPressure", 0.0))
        torque_val = float(getattr(raw, "mEngineTorque", 0.0))
        clutch_slip = bool(getattr(raw, "mClutchSlipping", False))
        clutch_t = float(getattr(raw, "mClutchTemp", 0.0))

        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState(raw.mGameState) if raw.mGameState in GameState._value2member_map_ else GameState.GAME_EXITED,
            session_state=SessionState(raw.mSessionState) if raw.mSessionState in SessionState._value2member_map_ else SessionState.SESSION_INVALID,
            race_state=RaceState(raw.mRaceState) if raw.mRaceState in RaceState._value2member_map_ else RaceState.RACESTATE_INVALID,

            car_name=dec(raw.mCarName),
            car_class_name=dec(raw.mCarClassName),
            speed_kmh=max(0.0, float(raw.mSpeed) * 3.6),
            rpm=max(0.0, float(raw.mRpm)),
            max_rpm=max(1000.0, float(raw.mMaxRPM)),
            gear=int(raw.mGear),
            throttle=max(0.0, min(1.0, float(raw.mThrottle))),
            brake=max(0.0, min(1.0, float(raw.mBrake))),
            steering=float(raw.mSteering),
            clutch=float(raw.mClutch),

            track_name=dec(raw.mTrackLocation),
            track_variation=dec(raw.mTrackVariation),
            track_length_m=float(raw.mTrackLength),
            num_sectors=int(raw.mNumSectors),
            total_laps=int(raw.mLapsInEvent),
            lap_invalidated=bool(raw.mLapInvalidated),

            player_name=player_name,
            position=int(player_p.mRacePosition),
            current_lap=int(player_p.mCurrentLap),
            laps_completed=max(0, int(player_p.mCurrentLap) - 1),
            current_sector=int(player_p.mCurrentSector),
            current_lap_distance=float(player_p.mCurrentLapDistance),
            current_lap_time=max(0.0, float(raw.mCurrentTime)),
            last_lap_time=max(0.0, float(raw.mLastLapTime)),
            best_lap_time=max(0.0, player_best_lap),
            split_time_ahead=max(0.0, float(raw.mSplitTimeAhead)),
            split_time_behind=max(0.0, float(raw.mSplitTimeBehind)),

            sector1_time=max(0.0, float(raw.mCurrentSector1Time)),
            sector2_time=max(0.0, float(raw.mCurrentSector2Time)),
            sector3_time=max(0.0, float(raw.mCurrentSector3Time)),
            best_sector1_time=max(0.0, float(raw.mPersonalFastestSector1Time)),
            best_sector2_time=max(0.0, float(raw.mPersonalFastestSector2Time)),
            best_sector3_time=max(0.0, float(raw.mPersonalFastestSector3Time)),

            fuel_level_pct=float(raw.mFuelLevel) * 100.0,
            fuel_liters=fuel_liters,
            fuel_capacity_liters=fuel_cap,
            water_temp_c=float(raw.mWaterTempCelsius),
            oil_temp_c=float(raw.mOilTempCelsius),
            oil_pressure_kpa=float(raw.mOilPressureKPa),

            tyre_compound=tyre_comp,
            tyre_tread_temps_c=tuple(tread_temps),
            tyre_carcass_temps_c=tuple(carcass_temps),
            tyre_pressures_bar=tuple(pressures),
            tyre_wear_pct=tuple(float(raw.mTyreWear[i]) * 100.0 for i in range(4)),
            brake_temps_c=tuple(float(raw.mBrakeTempCelsius[i]) for i in range(4)),

            ambient_temp_c=float(raw.mAmbientTemperature),
            track_temp_c=float(raw.mTrackTemperature),
            rain_density=max(0.0, min(1.0, float(raw.mRainDensity))),
            wind_speed_ms=float(raw.mWindSpeed),

            flag_colour=active_flag,
            pit_mode=PitMode(raw.mPitMode) if raw.mPitMode in PitMode._value2member_map_ else PitMode.PIT_MODE_NONE,
            enforced_pit_stop_lap=int(getattr(raw, "mEnforcedPitStopLap", -1)),
            aero_damage=float(raw.mAeroDamage),
            engine_damage=float(raw.mEngineDamage),
            collision_flag=collision_flag,
            collision_magnitude=collision_mag,
            drs_state=int(raw.mDrsState),
            ers_mode=int(raw.mErsDeploymentMode),

            event_time_remaining_sec=float(raw.mEventTimeRemaining) if raw.mEventTimeRemaining >= 0.0 else -1.0,
            session_duration_min=float(raw.mSessionDuration) if hasattr(raw, "mSessionDuration") else 0.0,
            is_race_finished=(
                raw.mRaceState == 3  # RACESTATE_FINISHED
                or (hasattr(raw, "mRaceStates") and viewed_idx < len(raw.mRaceStates) and raw.mRaceStates[viewed_idx] == 3)
                or player_flag_val == int(FlagColour.FLAG_CHEQUERED)  # 11
                or (raw.mHighestFlagColour == int(FlagColour.FLAG_CHEQUERED) and int(player_p.mRacePosition) <= 1)
                or (raw.mLapsInEvent > 0 and player_p.mCurrentLap > raw.mLapsInEvent)
            ),

            participants=tuple(participants_list),

            # Deep Diagnostics & In-Car Settings
            suspension_travel_mm=tuple(susp_travel_mm),
            suspension_velocity_ms=tuple(susp_velocity_ms),
            ride_height_mm=tuple(ride_height_mm),
            tyre_slip_speed_ms=tuple(tyre_slip_ms),
            tyre_temps_inner_c=tuple(inner_temps),
            tyre_temps_center_c=tuple(center_temps),
            tyre_temps_outer_c=tuple(outer_temps),
            brake_bias_pct=brake_bias_val,
            abs_setting=abs_val,
            tc_setting=tc_val,
            turbo_boost_pressure=turbo_val,
            engine_torque_nm=torque_val,
            clutch_slipping=clutch_slip,
            clutch_temp_c=clutch_t,

            local_accel_ms2=tuple(float(v) for v in raw.mLocalAcceleration),
            angular_velocity_rads=tuple(float(v) for v in raw.mAngularVelocity),
            local_velocity_ms=tuple(float(v) for v in raw.mLocalVelocity),
            tyre_rps=tuple(float(raw.mTyreRPS[i]) for i in range(4)),
            ride_height_cm=tuple(ride_height_cm),
            tyre_pressures_raw=tuple(float(raw.mAirPressure[i]) for i in range(4)),
            brake_bias_raw=brake_bias_raw,
            session_additional_laps=int(getattr(raw, "mSessionAdditionalLaps", 0)),
            num_participants=int(raw.mNumParticipants),
        )

    # ------------------------------------------------------------------ sim detection
    def _candidate_sims(self) -> List[str]:
        """Sims to try connecting to, in order."""
        if self.sim_preference != "auto":
            return [self.sim_preference]
        running = running_sims()
        if running is None:
            return [SIM_AMS2, SIM_R3E]  # process list unavailable: probe both mappings
        order = [s for s in (SIM_AMS2, SIM_R3E) if s in running]
        if SIM_AMS2 not in order:
            # AMS2 flags its own exit in shared memory, so its mapping is always safe to probe
            order.append(SIM_AMS2)
        return order

    def _disconnect_all(self):
        self.reader.disconnect()
        self.r3e_reader.disconnect()

    def _convert_r3e_snapshot(self, frame: R3EFrame) -> TelemetrySnapshot:
        snapshot = self.r3e_converter.convert(frame, tyre_cache=self._cache_tyre_temps)
        if not frame.layout_ok and not self._r3e_layout_checked and snapshot.position > 0:
            # Newer layout than the header we know: if the player block still agrees with the
            # player's standings entry, the fields we use haven't moved.
            self._r3e_layout_checked = True
            me = next((p for p in snapshot.participants if p.position == snapshot.position), None)
            if me is None or me.laps_completed != snapshot.laps_completed:
                logger.warning("RaceRoom shared memory layout check FAILED: player data disagrees with the standings. "
                               "Some values may be wrong until the app is updated for this RaceRoom version.")
            else:
                logger.info("RaceRoom shared memory layout check passed (player data matches standings).")
        return snapshot

    def _read(self, sim: str) -> Optional[TelemetrySnapshot]:
        """One frame from the given sim, converted. None if unavailable."""
        if sim == SIM_R3E:
            frame = self.r3e_reader.read_frame()
            return self._convert_r3e_snapshot(frame) if frame is not None else None
        raw = self.reader.read_snapshot()
        return self._convert_snapshot(raw) if raw is not None else None

    def _sim_still_running(self) -> bool:
        """
        False once RaceRoom's process is gone: R3E has no "exited" state and another app (CrewChief,
        SimHub, a dash) can keep its mapping open with stale data. AMS2 writes GAME_EXITED itself.
        """
        if self.active_sim != SIM_R3E:
            return True
        now = time.time()
        if now - self._last_liveness_check < self.LIVENESS_CHECK_SEC:
            return True
        self._last_liveness_check = now
        running = running_sims()
        return running is None or self.active_sim in running

    def _connect_any(self) -> Optional[TelemetrySnapshot]:
        for sim in self._candidate_sims():
            try:
                snap = self._read(sim)
            except Exception as ex:
                logger.debug(f"{sim} probe failed: {ex}")
                snap = None
            if snap is not None:
                self.active_sim = sim
                self._last_liveness_check = time.time()
                return snap
            (self.r3e_reader if sim == SIM_R3E else self.reader).disconnect()
        return None

    def _run_loop(self):
        was_connected = False

        while self._running:
            snapshot = None
            try:
                if self.active_sim is None:
                    snapshot = self._connect_any()
                elif self._sim_still_running():
                    snapshot = self._read(self.active_sim)
            except Exception as ex:
                logger.error(f"Error processing telemetry frame: {ex}")

            if snapshot is None:
                if was_connected:
                    name = SIM_DISPLAY_NAMES.get(self.active_sim or "", "the sim")
                    logger.warning(f"Lost connection to {name} shared memory. Waiting for a sim to start...")
                    was_connected = False
                    with self._lock:
                        self.latest = None
                if self.active_sim is not None:
                    self._disconnect_all()
                    self.active_sim = None
                time.sleep(1.0)
                continue

            if not was_connected:
                name = SIM_DISPLAY_NAMES.get(self.active_sim, self.active_sim)
                if self.active_sim == SIM_R3E:
                    major, minor = self.r3e_reader.version
                    name = f"{name} (shared memory v{major}.{minor})"
                    self.r3e_converter.reset()
                    self._r3e_layout_checked = False
                logger.info(f"Connected to {name}! Telemetry mapped successfully.")
                was_connected = True

            with self._lock:
                self.latest = snapshot
                self.history.append(snapshot)

            # Dispatch to listeners
            for listener in self.listeners:
                try:
                    listener(snapshot)
                except Exception as ex:
                    logger.error(f"Error in telemetry listener: {ex}")

            time.sleep(self.interval)
