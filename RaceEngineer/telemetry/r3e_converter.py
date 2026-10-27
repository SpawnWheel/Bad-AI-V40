"""
RaceRoom frame -> TelemetrySnapshot, in the same units and conventions the AMS2 path produces,
so everything downstream (race state, fuel, strategy, practice lab, engineer context) works unchanged.

Differences handled here:
  * tyre wear: R3E = life left (1.0 new)        -> % worn
  * damage:    R3E = health (1.0 intact)        -> 0 = no damage, 1 = destroyed
  * sectors:   R3E track_sector is 1..3         -> 0..2 like AMS2
  * sector times: R3E splits are cumulative      -> individual sector times
  * brake bias: R3E = rear share                 -> front %
  * no weather and no collision fields in R3E    -> weather_available=False, impacts from damage jumps
"""

import time
from typing import Callable, Optional, Sequence, Tuple

from .shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from .r3e_shared_memory import (
    R3EFrame, decode_str,
    R3E_SESSION_PRACTICE, R3E_SESSION_QUALIFY, R3E_SESSION_RACE, R3E_SESSION_WARMUP,
    R3E_SESSION_PHASE_FORMATION, R3E_SESSION_PHASE_GREEN,
    R3E_FINISH_STATUS_NONE, R3E_FINISH_STATUS_FINISHED, R3E_FINISH_STATUS_DNF, R3E_FINISH_STATUS_DNQ,
    R3E_FINISH_STATUS_DNS, R3E_FINISH_STATUS_DQ,
    R3E_SESSION_LENGTH_TIME_AND_LAP_BASED, R3E_PIT_WINDOW_DISABLED, R3E_PIT_WINDOW_COMPLETED,
    R3E_TIRE_SUBTYPE_NAMES, R3E_TIRE_TYPE_NAMES,
)
from .r3e_data import R3EData
from .sim_detect import SIM_R3E

RPS_TO_RPM = 60.0 / (2.0 * 3.141592653589793)
# collision_magnitude scale: 0.25 health lost in one frame = 2500 (the "heavy impact" threshold)
DAMAGE_TO_MAGNITUDE = 10000.0
# Session clock: a reading of 0/-1 only counts as "time's up" if the clock was already this close to zero
CLOCK_EXPIRY_WINDOW_SEC = 15.0
# ...otherwise it's treated as a glitch and bridged for this long
CLOCK_GLITCH_HOLD_SEC = 5.0


def _pos(v: float) -> float:
    """R3E uses -1 for N/A; the snapshot uses 0."""
    v = float(v)
    return v if v > 0.0 else 0.0


def split_sectors(cum: Sequence[float], ref_lap: float = 0.0) -> Tuple[float, float, float]:
    """
    R3E sector times are split times (S1, S1+S2, lap). Returns individual sector times.
    Values that already look individual (S3 far shorter than a lap) are passed through.
    """
    a, b, c = (_pos(x) for x in cum)
    cumulative = (b > a > 0.0) and (c <= 0.0 or c > b)
    if cumulative and ref_lap > 0.0 and c > 0.0 and c < 0.6 * ref_lap:
        cumulative = False
    if not cumulative:
        return a, b, c
    s2 = b - a if b > a else 0.0
    s3 = c - b if c > b else 0.0
    return a, s2, s3


def _race_state(finish_status: int, phase: int) -> RaceState:
    if finish_status == R3E_FINISH_STATUS_FINISHED:
        return RaceState.RACESTATE_FINISHED
    if finish_status in (R3E_FINISH_STATUS_DNF, R3E_FINISH_STATUS_DNQ):
        return RaceState.RACESTATE_DNF
    if finish_status == R3E_FINISH_STATUS_DQ:
        return RaceState.RACESTATE_DISQUALIFIED
    if finish_status == R3E_FINISH_STATUS_DNS:
        return RaceState.RACESTATE_RETIRED
    if 0 < phase < R3E_SESSION_PHASE_GREEN:
        return RaceState.RACESTATE_NOT_STARTED
    return RaceState.RACESTATE_RACING


def _session_state(session_type: int, phase: int) -> SessionState:
    if session_type == R3E_SESSION_PRACTICE:
        return SessionState.SESSION_PRACTICE
    if session_type == R3E_SESSION_WARMUP:
        return SessionState.SESSION_TEST
    if session_type == R3E_SESSION_QUALIFY:
        return SessionState.SESSION_QUALIFY
    if session_type == R3E_SESSION_RACE:
        return SessionState.SESSION_FORMATION_LAP if phase == R3E_SESSION_PHASE_FORMATION else SessionState.SESSION_RACE
    return SessionState.SESSION_INVALID


def _flag(flags, finish_status: int) -> FlagColour:
    """
    Highest-priority flag shown to the player.
    Chequered only once the player has finished: the flag can be out (leader finished) while the
    player still has to reach the line, and downstream treats CHEQUERED as "our race is over".
    R3E's white flag isn't mapped: the app's own lap / clock maths decides the final lap, so a
    white with another meaning can't fake one.
    """
    if flags.black == 1:
        return FlagColour.FLAG_BLACK
    if flags.black_and_white > 0:
        return FlagColour.FLAG_BLACK_AND_WHITE
    if finish_status == R3E_FINISH_STATUS_FINISHED:
        return FlagColour.FLAG_CHEQUERED
    if flags.yellow == 1:
        return FlagColour.FLAG_YELLOW
    if flags.blue == 1:
        return FlagColour.FLAG_BLUE
    if flags.green == 1:
        return FlagColour.FLAG_GREEN
    return FlagColour.FLAG_NONE


def _participant_pit_mode(d) -> int:
    if d.in_pitlane != 1:
        return int(PitMode.PIT_MODE_NONE)
    return int(PitMode.PIT_MODE_IN_PIT) if d.car_speed < 0.5 else int(PitMode.PIT_MODE_DRIVING_INTO_PITS)


class R3EConverter:
    def __init__(self, data: Optional[R3EData] = None, brake_bias_is_rear_share: bool = True):
        self.data = data or R3EData()
        self.brake_bias_is_rear_share = bool(brake_bias_is_rear_share)
        self._last_health: Optional[float] = None
        self.reset()

    def reset(self):
        self._last_health = None
        self._clock_key = None
        self._clock_last: Optional[Tuple[float, float]] = None  # (seconds remaining, wall time)
        self._clock_expired = False

    def _session_clock(self, h, now: float) -> float:
        """
        session_time_remaining, cleaned up for the race logic (-1 = no clock / expired):
          * a drop to 0/-1 while minutes were left is a glitch (seen at Zolder on lap 1): the clock
            keeps counting down from the last good value for up to CLOCK_GLITCH_HOLD_SEC;
          * once the clock has really run out it stays expired for the rest of the session -
            RaceRoom starts a new countdown for the rest of the field after the leader finishes.
        """
        key = (int(h.session_type), int(h.session_iteration), int(h.event_index), int(h.layout_id))
        phase = int(h.session_phase)
        if key != self._clock_key or 0 < phase < R3E_SESSION_PHASE_GREEN:
            self._clock_key = key
            self._clock_last = None
            self._clock_expired = False
        raw = float(h.session_time_remaining)
        if self._clock_expired:
            return -1.0
        if raw > 0.0:
            self._clock_last = (raw, now)
            return raw
        if self._clock_last is not None:
            last_v, last_t = self._clock_last
            if last_v <= CLOCK_EXPIRY_WINDOW_SEC and phase >= R3E_SESSION_PHASE_GREEN:
                self._clock_expired = True
                return -1.0
            held = last_v - (now - last_t)
            if now - last_t < CLOCK_GLITCH_HOLD_SEC and held > 0.0:
                return held
        return -1.0

    def _player_index(self, frame: R3EFrame) -> int:
        h = frame.head
        slot = h.vehicle_info.slot_id
        for i, d in enumerate(frame.drivers):
            if slot >= 0 and d.driver_info.slot_id == slot:
                return i
        for i, d in enumerate(frame.drivers):
            if d.place == h.position:
                return i
        return -1

    def _game_state(self, h) -> GameState:
        if h.game_in_replay == 1:
            return GameState.GAME_INGAME_REPLAY
        if h.session_type < 0 or h.layout_length <= 0.0 or not decode_str(h.track_name):
            return GameState.GAME_FRONT_END
        if h.game_paused == 1:
            return GameState.GAME_INGAME_PAUSED
        if h.game_in_menus == 1:
            return GameState.GAME_INGAME_INMENU_TIME_TICKING
        return GameState.GAME_INGAME_PLAYING

    def _pit_mode(self, h) -> PitMode:
        if h.game_player_in_garage == 1:
            return PitMode.PIT_MODE_IN_GARAGE
        if h.pit_state == 3:
            return PitMode.PIT_MODE_IN_PIT
        if h.pit_state == 4:
            return PitMode.PIT_MODE_DRIVING_OUT_OF_PITS
        if h.pit_state == 2 or h.in_pitlane == 1:
            return PitMode.PIT_MODE_DRIVING_INTO_PITS
        return PitMode.PIT_MODE_NONE

    def _impact(self, dmg) -> Tuple[bool, float]:
        """R3E has no collision data: an impact is a sudden loss of car health."""
        parts = [v for v in (dmg.engine, dmg.transmission, dmg.aerodynamics, dmg.suspension) if v >= 0.0]
        if not parts:
            return False, 0.0
        health = sum(parts)
        last, self._last_health = self._last_health, health
        if last is None or health >= last:
            return False, 0.0
        loss = last - health
        return loss > 0.005, loss * DAMAGE_TO_MAGNITUDE

    def convert(self, frame: R3EFrame, tyre_cache: Optional[Callable] = None):
        from .collector import ParticipantSnapshot, TelemetrySnapshot  # avoid import cycle

        h = frame.head
        if not self.data.available:
            self.data.load()
        phase = int(h.session_phase)
        pidx = self._player_index(frame)
        session_state = _session_state(h.session_type, phase)
        game_state = self._game_state(h)
        is_race = h.session_type == R3E_SESSION_RACE

        participants = []
        for d in frame.drivers:
            name = decode_str(d.driver_info.name)
            if not name:
                continue
            ref = _pos(d.sector_time_best_self[2]) or _pos(d.sector_time_previous_self[2])
            s1, s2, s3 = split_sectors(d.sector_time_current_self, ref)
            finished = d.finish_status == R3E_FINISH_STATUS_FINISHED
            # After the flag (or a DNF) cars drive into the pit lane - that's not a pit stop
            pit_mode = _participant_pit_mode(d) if d.finish_status <= R3E_FINISH_STATUS_NONE else 0
            info = d.driver_info
            participants.append(ParticipantSnapshot(
                name=name,
                position=int(d.place),
                lap=max(0, int(d.completed_laps)) + 1,
                sector=max(0, int(d.track_sector) - 1),
                lap_distance=_pos(d.lap_distance),
                is_active=True,
                last_lap_time=_pos(d.sector_time_previous_self[2]),
                best_lap_time=_pos(d.sector_time_best_self[2]),
                sector1_time=s1, sector2_time=s2, sector3_time=s3,
                pit_mode=pit_mode,
                speed_kmh=_pos(d.car_speed) * 3.6,
                race_state=int(_race_state(d.finish_status, phase if is_race else -1)),
                laps_completed=max(0, int(d.completed_laps)),
                flag_colour=int(FlagColour.FLAG_CHEQUERED) if finished else 0,
                car_name=self.data.car_name(info.model_id),
                car_class=self.data.class_name(info.class_id),
                rating=_pos(info.rating),
                reputation=_pos(info.reputation),
            ))

        player_name = decode_str(h.player_name) or (
            decode_str(frame.drivers[pidx].driver_info.name) if pidx >= 0 else "") or "Driver"

        # Tyres: left/center/right across the tread, as seen from behind the car (same as AMS2)
        tt = h.tire_temp
        left = [float(tt[i].current_temp[0]) for i in range(4)]
        center = [float(tt[i].current_temp[1]) for i in range(4)]
        right = [float(tt[i].current_temp[2]) for i in range(4)]
        inner = [right[0], left[1], right[2], left[3]]
        outer = [left[0], right[1], left[2], right[3]]
        tread = [(left[i] + center[i] + right[i]) / 3.0 if min(left[i], center[i], right[i]) > 5.0 else 0.0
                 for i in range(4)]
        pit_mode = self._pit_mode(h)
        speed_kmh = _pos(h.car_speed) * 3.6
        if tyre_cache is not None:
            inner, center, outer, tread = tyre_cache(
                inner, center, outer, tread,
                is_on_track=(pit_mode == PitMode.PIT_MODE_NONE and speed_kmh > 15.0),
                in_pits=pit_mode != PitMode.PIT_MODE_NONE,
            )
        pressures_kpa = [_pos(h.tire_pressure[i]) for i in range(4)]
        wear = [(1.0 - float(h.tire_wear[i])) * 100.0 if 0.0 <= h.tire_wear[i] <= 1.0 else 0.0 for i in range(4)]
        tyre_window = tuple((float(tt[i].cold_temp), float(tt[i].optimal_temp), float(tt[i].hot_temp)) for i in range(4))
        bt = h.brake_temp
        brake_window = tuple((float(bt[i].cold_temp), float(bt[i].optimal_temp), float(bt[i].hot_temp)) for i in range(4))

        sub = int(h.tire_subtype_front)
        compound = R3E_TIRE_SUBTYPE_NAMES.get(sub) or R3E_TIRE_TYPE_NAMES.get(int(h.tire_type_front)) or "Dry"

        dmg = h.car_damage
        collision_flag, collision_mag = self._impact(dmg)

        bias_raw = float(h.brake_bias)
        if 0.0 < bias_raw < 1.0:
            front = (1.0 - bias_raw) if self.brake_bias_is_rear_share else bias_raw
            bias_pct = front * 100.0
        else:
            bias_pct = 0.0

        p = h.player
        ref_lap = _pos(h.lap_time_best_self) or _pos(h.lap_time_previous_self)
        s1, s2, s3 = split_sectors(h.sector_time_current_self, ref_lap)
        b1, b2, b3 = split_sectors(h.best_individual_sector_time_self, ref_lap)

        enforced = -1
        if (h.pit_window_status not in (R3E_PIT_WINDOW_DISABLED, R3E_PIT_WINDOW_COMPLETED, -1)
                and h.number_of_laps > 0 and h.pit_window_end > 0):
            enforced = int(h.pit_window_end)

        fuel_cap = max(1.0, float(h.fuel_capacity))
        fuel_l = _pos(h.fuel_left)
        tyre_speed = [float(h.tire_speed[i]) for i in range(4)]
        v = _pos(h.car_speed)
        slip = [abs(abs(ts) - v) if ts > -1.0 else 0.0 for ts in tyre_speed]

        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=game_state,
            session_state=session_state,
            race_state=_race_state(h.finish_status, phase if is_race else -1),

            car_name=self.data.car_name(h.vehicle_info.model_id),
            car_class_name=self.data.class_name(h.vehicle_info.class_id),
            speed_kmh=speed_kmh,
            rpm=_pos(h.engine_rps) * RPS_TO_RPM,
            max_rpm=max(1000.0, _pos(h.max_engine_rps) * RPS_TO_RPM),
            gear=int(h.gear) if h.gear >= -1 else 0,
            throttle=max(0.0, min(1.0, float(h.throttle))),
            brake=max(0.0, min(1.0, float(h.brake))),
            steering=max(-1.0, min(1.0, float(h.steer_input_raw))),
            clutch=max(0.0, min(1.0, float(h.clutch))),

            track_name=decode_str(h.track_name),
            track_variation=decode_str(h.layout_name),
            track_length_m=_pos(h.layout_length),
            num_sectors=3,
            total_laps=max(0, int(h.number_of_laps)),
            lap_invalidated=(h.current_lap_valid == 0),

            player_name=player_name,
            position=max(0, int(h.position)),
            current_lap=max(0, int(h.completed_laps)) + 1,
            laps_completed=max(0, int(h.completed_laps)),
            current_sector=max(0, int(h.track_sector) - 1),
            current_lap_distance=_pos(h.lap_distance),
            current_lap_time=_pos(h.lap_time_current_self),
            last_lap_time=_pos(h.lap_time_previous_self),
            best_lap_time=_pos(h.lap_time_best_self),
            split_time_ahead=_pos(h.time_delta_front),
            split_time_behind=_pos(h.time_delta_behind),

            sector1_time=s1, sector2_time=s2, sector3_time=s3,
            best_sector1_time=b1, best_sector2_time=b2, best_sector3_time=b3,

            fuel_level_pct=fuel_l / fuel_cap * 100.0,
            fuel_liters=fuel_l,
            fuel_capacity_liters=fuel_cap,
            water_temp_c=_pos(h.engine_temp),
            oil_temp_c=_pos(h.engine_oil_temp),
            oil_pressure_kpa=_pos(h.engine_oil_pressure),

            tyre_compound=compound,
            tyre_tread_temps_c=tuple(tread),
            tyre_carcass_temps_c=(0.0, 0.0, 0.0, 0.0),  # R3E has no carcass temperature (0 = not reported)
            tyre_pressures_bar=tuple(x / 100.0 for x in pressures_kpa),
            tyre_wear_pct=tuple(wear),
            brake_temps_c=tuple(_pos(bt[i].current_temp) for i in range(4)),

            ambient_temp_c=0.0,
            track_temp_c=0.0,
            rain_density=0.0,
            wind_speed_ms=0.0,

            flag_colour=_flag(h.flags, int(h.finish_status)),
            pit_mode=pit_mode,
            aero_damage=(1.0 - float(dmg.aerodynamics)) if 0.0 <= dmg.aerodynamics <= 1.0 else 0.0,
            engine_damage=(1.0 - float(dmg.engine)) if 0.0 <= dmg.engine <= 1.0 else 0.0,
            collision_flag=collision_flag,
            collision_magnitude=collision_mag,
            drs_state=1 if h.drs.engaged == 1 else 0,
            ers_mode=0,

            event_time_remaining_sec=self._session_clock(h, time.time()),
            session_duration_min=_pos(h.session_time_duration) / 60.0,
            is_race_finished=h.finish_status in (R3E_FINISH_STATUS_FINISHED, R3E_FINISH_STATUS_DNF, R3E_FINISH_STATUS_DQ),
            enforced_pit_stop_lap=enforced,

            participants=tuple(participants),

            suspension_travel_mm=tuple(float(p.suspension_deflection[i]) * 1000.0 for i in range(4)),
            suspension_velocity_ms=tuple(float(p.suspension_velocity[i]) for i in range(4)),
            ride_height_mm=tuple(float(p.ride_height[i]) * 1000.0 for i in range(4)),
            tyre_slip_speed_ms=tuple(slip),
            tyre_temps_inner_c=tuple(inner),
            tyre_temps_center_c=tuple(center),
            tyre_temps_outer_c=tuple(outer),
            brake_bias_pct=bias_pct,
            abs_setting=max(0, int(h.abs_setting)),
            tc_setting=max(0, int(h.traction_control_setting)),
            turbo_boost_pressure=_pos(h.turbo_pressure),
            engine_torque_nm=float(p.engine_torque),
            clutch_slipping=False,
            clutch_temp_c=0.0,

            local_accel_ms2=(float(p.local_acceleration.x), float(p.local_acceleration.y), float(p.local_acceleration.z)),
            angular_velocity_rads=(float(p.local_angular_velocity.x), float(p.local_angular_velocity.y), float(p.local_angular_velocity.z)),
            local_velocity_ms=(float(p.local_velocity.x), float(p.local_velocity.y), float(p.local_velocity.z)),
            tyre_rps=tuple(float(h.tire_rps[i]) for i in range(4)),
            ride_height_cm=tuple(float(p.ride_height[i]) * 100.0 for i in range(4)),
            tyre_pressures_raw=tuple(pressures_kpa),
            brake_bias_raw=bias_raw,
            session_additional_laps=1 if h.session_length_format == R3E_SESSION_LENGTH_TIME_AND_LAP_BASED else 0,
            num_participants=int(frame.num_cars),

            sim=SIM_R3E,
            weather_available=False,
            tyre_temp_window_c=tyre_window,
            brake_temp_window_c=brake_window,
        )
