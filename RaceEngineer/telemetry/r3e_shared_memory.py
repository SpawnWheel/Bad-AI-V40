"""
RaceRoom Racing Experience (R3E) Shared Memory CTypes Interface.
Reads shared memory mapped at '$R3E'.

Layout follows the official r3e.h from KW Studios (github.com/sector3studios/r3e-api), version 3.5.
The Historic Update (21 Oct 2026) ships shared memory 3.6 (adds start procedure data) without a public
header yet. To stay correct across minor versions the driver array is located with the header's own
`all_drivers_offset` / `driver_data_size` fields instead of a fixed offset, and the reader cross-checks
the player block against the player's standings entry (see R3EFrame.layout_ok).
"""

import ctypes
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from utils.logging import logger

R3E_SHARED_MEMORY_NAME = "$R3E"
R3E_VERSION_MAJOR = 3
R3E_VERSION_MINOR_KNOWN = 5
R3E_NUM_DRIVERS_MAX = 128

# r3e_session
R3E_SESSION_UNAVAILABLE = -1
R3E_SESSION_PRACTICE = 0
R3E_SESSION_QUALIFY = 1
R3E_SESSION_RACE = 2
R3E_SESSION_WARMUP = 3

# r3e_session_phase
R3E_SESSION_PHASE_UNAVAILABLE = -1
R3E_SESSION_PHASE_GARAGE = 1
R3E_SESSION_PHASE_GRIDWALK = 2
R3E_SESSION_PHASE_FORMATION = 3
R3E_SESSION_PHASE_COUNTDOWN = 4
R3E_SESSION_PHASE_GREEN = 5
R3E_SESSION_PHASE_CHECKERED = 6

# r3e_finish_status
R3E_FINISH_STATUS_UNAVAILABLE = -1
R3E_FINISH_STATUS_NONE = 0
R3E_FINISH_STATUS_FINISHED = 1
R3E_FINISH_STATUS_DNF = 2
R3E_FINISH_STATUS_DNQ = 3
R3E_FINISH_STATUS_DNS = 4
R3E_FINISH_STATUS_DQ = 5

# r3e_session_length_format
R3E_SESSION_LENGTH_TIME_BASED = 0
R3E_SESSION_LENGTH_LAP_BASED = 1
R3E_SESSION_LENGTH_TIME_AND_LAP_BASED = 2  # extra lap after the time has run out

# r3e_pit_window
R3E_PIT_WINDOW_DISABLED = 0
R3E_PIT_WINDOW_COMPLETED = 4

# r3e_tire_subtype
R3E_TIRE_SUBTYPE_NAMES = {0: "Standard", 1: "Alternate", 2: "Soft", 3: "Medium", 4: "Hard"}
R3E_TIRE_TYPE_NAMES = {0: "Option", 1: "Prime"}

i32 = ctypes.c_int32
f32 = ctypes.c_float
f64 = ctypes.c_double
u8 = ctypes.c_uint8


class Vec3F32(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("x", f32), ("y", f32), ("z", f32)]


class Vec3F64(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("x", f64), ("y", f64), ("z", f64)]


class OriF32(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("pitch", f32), ("yaw", f32), ("roll", f32)]


class SectorStarts(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("sector1", f32), ("sector2", f32), ("sector3", f32)]


class PlayerData(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("user_id", i32),
        ("game_simulation_ticks", i32),
        ("game_simulation_time", f64),
        ("position", Vec3F64),
        ("velocity", Vec3F64),
        ("local_velocity", Vec3F64),
        ("acceleration", Vec3F64),
        ("local_acceleration", Vec3F64),
        ("orientation", Vec3F64),
        ("rotation", Vec3F64),
        ("angular_acceleration", Vec3F64),
        ("angular_velocity", Vec3F64),
        ("local_angular_velocity", Vec3F64),
        ("local_g_force", Vec3F64),
        ("steering_force", f64),
        ("steering_force_percentage", f64),
        ("engine_torque", f64),
        ("current_downforce", f64),
        ("voltage", f64),
        ("ers_level", f64),
        ("power_mgu_h", f64),
        ("power_mgu_k", f64),
        ("torque_mgu_k", f64),
        ("suspension_deflection", f64 * 4),
        ("suspension_velocity", f64 * 4),
        ("camber", f64 * 4),
        ("ride_height", f64 * 4),
        ("front_wing_height", f64),
        ("front_roll_angle", f64),
        ("rear_roll_angle", f64),
        ("third_spring_suspension_deflection_front", f64),
        ("third_spring_suspension_velocity_front", f64),
        ("third_spring_suspension_deflection_rear", f64),
        ("third_spring_suspension_velocity_rear", f64),
        ("unused1", f64),
        ("unused2", f64),
        ("unused3", f64),
    ]


class Flags(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("yellow", i32),
        ("yellowCausedIt", i32),
        ("yellowOvertake", i32),
        ("yellowPositionsGained", i32),
        ("sector_yellow", i32 * 3),
        ("closest_yellow_distance_into_track", f32),  # since 23 Oct 2026: points at the incident itself
        ("blue", i32),
        ("black", i32),
        ("green", i32),
        ("checkered", i32),
        ("white", i32),
        ("black_and_white", i32),
    ]


class CarDamage(ctypes.Structure):
    _pack_ = 1
    _fields_ = [  # 1.0 = intact, 0.0 = destroyed, -1.0 = N/A
        ("engine", f32),
        ("transmission", f32),
        ("aerodynamics", f32),
        ("suspension", f32),
        ("unused1", f32),
        ("unused2", f32),
    ]


class CutTrackPenalties(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("drive_through", f32),
        ("stop_and_go", f32),
        ("pit_stop", f32),
        ("time_deduction", f32),
        ("slow_down", f32),
    ]


class Drs(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("equipped", i32), ("available", i32), ("numActivationsLeft", i32), ("engaged", i32)]


class PushToPass(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("available", i32),
        ("engaged", i32),
        ("amount_left", i32),
        ("engaged_time_left", f32),
        ("wait_time_left", f32),
    ]


class TireTemp(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("current_temp", f32 * 3),  # left, center, right (Celsius)
        ("optimal_temp", f32),
        ("cold_temp", f32),
        ("hot_temp", f32),
    ]


class BrakeTemp(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("current_temp", f32), ("optimal_temp", f32), ("cold_temp", f32), ("hot_temp", f32)]


class AidSettings(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("abs", i32), ("tc", i32), ("esp", i32), ("countersteer", i32), ("cornering", i32)]


class DriverInfo(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("name", u8 * 64),
        ("car_number", i32),
        ("class_id", i32),
        ("model_id", i32),
        ("team_id", i32),
        ("livery_id", i32),
        ("manufacturer_id", i32),
        ("user_id", i32),
        ("slot_id", i32),
        ("class_performance_index", i32),
        ("engine_type", i32),
        ("car_width", f32),
        ("car_length", f32),
        ("rating", f32),
        ("reputation", f32),
        ("unused1", f32),
        ("unused2", f32),
    ]


class DriverData(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("driver_info", DriverInfo),
        ("finish_status", i32),
        ("place", i32),
        ("place_class", i32),
        ("lap_distance", f32),
        ("lap_distance_fraction", f32),
        ("position", Vec3F32),
        ("track_sector", i32),
        ("completed_laps", i32),
        ("current_lap_valid", i32),
        ("lap_time_current_self", f32),
        ("sector_time_current_self", f32 * 3),
        ("sector_time_previous_self", f32 * 3),
        ("sector_time_best_self", f32 * 3),
        ("time_delta_front", f32),
        ("time_delta_behind", f32),
        ("pitstop_status", i32),
        ("in_pitlane", i32),
        ("num_pitstops", i32),
        ("penalties", CutTrackPenalties),
        ("car_speed", f32),
        ("tire_type_front", i32),
        ("tire_type_rear", i32),
        ("tire_subtype_front", i32),
        ("tire_subtype_rear", i32),
        ("base_penalty_weight", f32),
        ("aid_penalty_weight", f32),
        ("drs_state", i32),
        ("ptp_state", i32),
        ("virtual_energy", f32),
        ("penaltyType", i32),
        ("penaltyReason", i32),
        ("engineState", i32),
        ("orientation", Vec3F32),
        ("unused1", f32),
        ("unused2", f32),
        ("unused3", f32),
    ]


class R3ESharedHead(ctypes.Structure):
    """Everything in r3e_shared before num_cars (sizeof == all_drivers_offset for a matching layout)."""
    _pack_ = 1
    _fields_ = [
        # Version
        ("version_major", i32),
        ("version_minor", i32),
        ("all_drivers_offset", i32),
        ("driver_data_size", i32),

        # Game state
        ("game_mode", i32),
        ("game_paused", i32),
        ("game_in_menus", i32),
        ("game_in_replay", i32),
        ("game_using_vr", i32),
        ("game_player_in_garage", i32),

        # High detail
        ("player", PlayerData),

        # Event and session
        ("track_name", u8 * 64),
        ("layout_name", u8 * 64),
        ("track_id", i32),
        ("layout_id", i32),
        ("layout_length", f32),
        ("sector_start_factors", SectorStarts),
        ("race_session_laps", i32 * 3),
        ("race_session_minutes", i32 * 3),
        ("event_index", i32),
        ("session_type", i32),
        ("session_iteration", i32),
        ("session_length_format", i32),
        ("session_pit_speed_limit", f32),
        ("session_phase", i32),
        ("start_lights", i32),
        ("tire_wear_active", i32),
        ("fuel_use_active", i32),
        ("number_of_laps", i32),
        ("session_time_duration", f32),
        ("session_time_remaining", f32),
        ("max_incident_points", i32),
        ("event_unused1", f32),
        ("event_unused2", f32),

        # Pit
        ("pit_window_status", i32),
        ("pit_window_start", i32),
        ("pit_window_end", i32),
        ("in_pitlane", i32),
        ("pit_menu_selection", i32),
        ("pit_menu_state", i32 * 12),
        ("pit_state", i32),
        ("pit_total_duration", f32),
        ("pit_elapsed_time", f32),
        ("pit_action", i32),
        ("num_pitstops", i32),
        ("pit_min_duration_total", f32),
        ("pit_min_duration_left", f32),

        # Scoring & timings
        ("flags", Flags),
        ("position", i32),
        ("position_class", i32),
        ("finish_status", i32),
        ("cut_track_warnings", i32),
        ("penalties", CutTrackPenalties),
        ("num_penalties", i32),
        ("completed_laps", i32),
        ("current_lap_valid", i32),
        ("track_sector", i32),
        ("lap_distance", f32),
        ("lap_distance_fraction", f32),
        ("lap_time_best_leader", f32),
        ("lap_time_best_leader_class", f32),
        ("session_best_lap_sector_times", f32 * 3),
        ("lap_time_best_self", f32),
        ("sector_time_best_self", f32 * 3),
        ("lap_time_previous_self", f32),
        ("sector_time_previous_self", f32 * 3),
        ("lap_time_current_self", f32),
        ("sector_time_current_self", f32 * 3),
        ("lap_time_delta_leader", f32),
        ("lap_time_delta_leader_class", f32),
        ("time_delta_front", f32),
        ("time_delta_behind", f32),
        ("time_delta_best_self", f32),
        ("best_individual_sector_time_self", f32 * 3),
        ("best_individual_sector_time_leader", f32 * 3),
        ("best_individual_sector_time_leader_class", f32 * 3),
        ("incident_points", i32),
        ("lap_valid_state", i32),
        ("prev_lap_valid", i32),
        ("discharge_rate", f32),
        ("brake_regen", f32),
        ("unused1", f32),

        # Vehicle information
        ("vehicle_info", DriverInfo),
        ("player_name", u8 * 64),

        # Vehicle state
        ("control_type", i32),
        ("car_speed", f32),
        ("engine_rps", f32),
        ("max_engine_rps", f32),
        ("upshift_rps", f32),
        ("gear", i32),
        ("num_gears", i32),
        ("car_cg_location", Vec3F32),
        ("car_orientation", OriF32),
        ("local_acceleration", Vec3F32),
        ("total_mass", f32),
        ("fuel_left", f32),
        ("fuel_capacity", f32),
        ("fuel_per_lap", f32),
        ("virtual_energy_left", f32),
        ("virtual_energy_capacity", f32),
        ("virtual_energy_per_lap", f32),
        ("engine_temp", f32),
        ("engine_oil_temp", f32),
        ("fuel_pressure", f32),
        ("engine_oil_pressure", f32),
        ("turbo_pressure", f32),
        ("throttle", f32),
        ("throttle_raw", f32),
        ("brake", f32),
        ("brake_raw", f32),
        ("clutch", f32),
        ("clutch_raw", f32),
        ("steer_input_raw", f32),
        ("steer_lock_degrees", i32),
        ("steer_wheel_range_degrees", i32),
        ("aid_settings", AidSettings),
        ("drs", Drs),
        ("pit_limiter", i32),
        ("push_to_pass", PushToPass),
        ("brake_bias", f32),  # share towards the REAR wheels (0.3 = 30%), -1 = N/A
        ("drs_numActivationsTotal", i32),
        ("ptp_numActivationsTotal", i32),
        ("battery_soc", f32),
        ("water_left", f32),
        ("abs_setting", i32),
        ("headlights", i32),
        ("steer_wheel_max_rotation", i32),

        # Tires
        ("tire_type", i32),
        ("tire_rps", f32 * 4),
        ("tire_speed", f32 * 4),
        ("tire_grip", f32 * 4),
        ("tire_wear", f32 * 4),  # 1.0 = new, 0.0 = worn out
        ("tire_flatspot", i32 * 4),
        ("tire_pressure", f32 * 4),  # kPa
        ("tire_dirt", f32 * 4),
        ("tire_temp", TireTemp * 4),
        ("tire_type_front", i32),
        ("tire_type_rear", i32),
        ("tire_subtype_front", i32),
        ("tire_subtype_rear", i32),
        ("brake_temp", BrakeTemp * 4),
        ("brake_pressure", f32 * 4),

        # Electronics
        ("traction_control_setting", i32),
        ("engine_map_setting", i32),
        ("engine_brake_setting", i32),
        ("traction_control_percent", f32),
        ("tire_on_mtrl", i32 * 4),
        ("tire_load", f32 * 4),

        # Damage
        ("car_damage", CarDamage),
    ]


HEAD_SIZE = ctypes.sizeof(R3ESharedHead)
DRIVER_SIZE = ctypes.sizeof(DriverData)


@dataclass
class R3EFrame:
    """One consistent read of the R3E shared memory."""
    head: R3ESharedHead
    num_cars: int
    drivers: List[DriverData] = field(default_factory=list)
    # False when the header's own offsets disagree with the 3.5 layout (fields before num_cars moved)
    layout_ok: bool = True


def decode_str(byte_arr) -> str:
    """Decodes a NUL-terminated UTF-8 r3e_u8char array."""
    raw = bytes(byte_arr)
    return raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()


class ExistingMapping:
    """
    Read-only view of a named shared memory block that must already exist.

    Python's mmap(-1, size, tagname=...) CREATES the mapping when it's missing. Doing that for $R3E
    while RaceRoom is starting would hand the game a block of our size, breaking its shared memory
    for every app. OpenFileMappingW never creates; VirtualQuery gives the block's real size.
    """
    FILE_MAP_READ = 0x0004

    class _MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", ctypes.c_uint32), ("PartitionId", ctypes.c_uint16),
            ("RegionSize", ctypes.c_size_t), ("State", ctypes.c_uint32),
            ("Protect", ctypes.c_uint32), ("Type", ctypes.c_uint32),
        ]

    def __init__(self, handle, addr, size):
        self._handle, self._addr, self.size = handle, addr, size

    @classmethod
    def open(cls, name: str) -> Optional["ExistingMapping"]:
        if sys.platform != "win32":
            return None
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        k32.VirtualQuery.restype = ctypes.c_size_t
        k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenFileMappingW(cls.FILE_MAP_READ, False, name)
        if not handle:
            return None
        addr = k32.MapViewOfFile(handle, cls.FILE_MAP_READ, 0, 0, 0)
        if not addr:
            k32.CloseHandle(handle)
            return None
        mbi = cls._MBI()
        size = int(mbi.RegionSize) if k32.VirtualQuery(addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) else 0
        return cls(handle, addr, size)

    def read(self) -> bytes:
        return ctypes.string_at(self._addr, self.size)

    def close(self):
        if self._addr:
            k32 = ctypes.WinDLL("kernel32")
            k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            k32.UnmapViewOfFile(self._addr)
            k32.CloseHandle(self._handle)
            self._addr = self._handle = None


class R3ETelemetryReader:
    """Safely opens and reads RaceRoom shared memory (never creates the mapping)."""

    def __init__(self, tag_name: str = R3E_SHARED_MEMORY_NAME):
        self.tag_name = tag_name
        self.shm: Optional[ExistingMapping] = None
        self._layout_warned = False
        self.version = (0, 0)

    def is_connected(self) -> bool:
        return self.shm is not None

    def connect(self) -> bool:
        if self.shm is not None:
            return True
        for tag in [self.tag_name, "Local\\" + self.tag_name]:
            try:
                self.shm = ExistingMapping.open(tag)
            except (OSError, AttributeError):
                self.shm = None
            if self.shm is not None:
                self.tag_name = tag
                return True
        return False

    def disconnect(self):
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None

    def read_frame(self) -> Optional[R3EFrame]:
        """Reads one frame. Returns None if RaceRoom is not running or the data is not usable."""
        if not self.is_connected() and not self.connect():
            return None
        try:
            raw = self.shm.read()
        except (ValueError, OSError):
            self.disconnect()
            return None
        if len(raw) < HEAD_SIZE + 4:
            return None

        head = R3ESharedHead.from_buffer_copy(raw[:HEAD_SIZE])
        if head.version_major != R3E_VERSION_MAJOR:
            # 0 = mapping exists but the game hasn't written yet; anything else = incompatible major version
            if head.version_major != 0 and not self._layout_warned:
                logger.warning(f"RaceRoom shared memory major version {head.version_major} is not supported "
                               f"(expected {R3E_VERSION_MAJOR}.x).")
                self._layout_warned = True
            return None
        self.version = (int(head.version_major), int(head.version_minor))

        offset = int(head.all_drivers_offset)
        stride = int(head.driver_data_size)
        layout_ok = (offset == HEAD_SIZE and stride == DRIVER_SIZE)
        if offset < 1000 or offset > 1_000_000 or stride < 200 or stride > 16_384:
            # Header offsets not filled in: fall back to the 3.5 layout
            offset, stride, layout_ok = HEAD_SIZE, DRIVER_SIZE, True
        if not layout_ok and not self._layout_warned:
            self._layout_warned = True
            logger.warning(
                f"RaceRoom shared memory v{head.version_major}.{head.version_minor}: layout differs from the "
                f"known 3.5 header (drivers at {offset}/{stride} B, expected {HEAD_SIZE}/{DRIVER_SIZE} B). "
                f"Standings are read with the game's offsets; player values are cross-checked.")

        if offset + 4 > len(raw):
            return None
        num_cars = int.from_bytes(raw[offset:offset + 4], "little", signed=True)
        num_cars = max(0, min(R3E_NUM_DRIVERS_MAX, num_cars))
        drivers = []
        base = offset + 4
        n = min(stride, DRIVER_SIZE)
        for i in range(num_cars):
            start = base + i * stride
            chunk = raw[start:start + n]
            if len(chunk) < n:
                break
            if n < DRIVER_SIZE:
                chunk = chunk + bytes(DRIVER_SIZE - n)
            drivers.append(DriverData.from_buffer_copy(chunk))
        return R3EFrame(head=head, num_cars=num_cars, drivers=drivers, layout_ok=layout_ok)
