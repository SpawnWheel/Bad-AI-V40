"""RaceRoom (R3E) shared memory layout and a read-only reader.

Layout follows the official r3e.h, version 3.5 (sector3studios/r3e-api).
Only the fields the commentary tools need are named; the rest are kept as
padding-correct placeholders so every offset matches the game.
"""
import ctypes
from ctypes import wintypes

SHARED_MEMORY_NAME = "$R3E"
VERSION_MAJOR = 3
NUM_DRIVERS_MAX = 128
DRIVERS_OFFSET = 2012 # Start of the driver array in the official layout (num_cars is the int before it)

i32 = ctypes.c_int32
f32 = ctypes.c_float
f64 = ctypes.c_double
u8 = ctypes.c_ubyte

# Sessions
SESSION_PRACTICE = 0
SESSION_QUALIFY = 1
SESSION_RACE = 2
SESSION_WARMUP = 3
SESSION_NAMES = {0: "Practice", 1: "Qualifying", 2: "Race", 3: "Warmup"}

# Session phases
PHASE_GARAGE = 1
PHASE_GRIDWALK = 2
PHASE_FORMATION = 3
PHASE_COUNTDOWN = 4
PHASE_GREEN = 5
PHASE_CHECKERED = 6

# Finish status
FINISH_NONE = 0
FINISH_FINISHED = 1
FINISH_DNF = 2
FINISH_DNQ = 3
FINISH_DNS = 4
FINISH_DQ = 5

# Session length format
LENGTH_TIME = 0
LENGTH_LAPS = 1
LENGTH_TIME_AND_LAP = 2

# Penalty reasons, by penaltyType (see r3e.h)
PENALTY_REASONS = {
    0: {1: "cutting the track", 2: "speeding in the pit lane", 3: "a false start", 4: "ignoring blue flags",
        5: "driving too slowly", 6: "overtaking before the green flag", 7: "overtaking before the finish",
        8: "overtaking before the pit entrance", 9: "ignoring a slow-down penalty"},
    1: {1: "cutting the track", 2: "repeatedly cutting the track", 3: "overtaking under yellow flags",
        4: "exceeding the virtual energy limit"},
    2: {1: "ignoring the pit stop window"},
    3: {1: "serving the mandatory pit stop late", 2: "ignoring the minimum pit stop duration"},
    4: {1: "cutting the track", 2: "repeatedly cutting the track"},
}


class Vec3F32(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("x", f32), ("y", f32), ("z", f32)]


class Vec3F64(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("x", f64), ("y", f64), ("z", f64)]


class PlayerData(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("user_id", i32),
        ("game_simulation_ticks", i32),
        ("game_simulation_time", f64),
        ("vectors", Vec3F64 * 11),       # position .. local_g_force
        ("scalars", f64 * 9),            # steering_force .. torque_mgu_k
        ("wheel_values", f64 * 16),      # suspension deflection/velocity, camber, ride height
        ("tail", f64 * 10),              # wing height, roll angles, third springs, unused
    ]


class Flags(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("yellow", i32),
        ("yellow_caused_it", i32),
        ("yellow_overtake", i32),
        ("yellow_positions_gained", i32),
        ("sector_yellow", i32 * 3),
        ("closest_yellow_distance_into_track", f32),
        ("blue", i32),
        ("black", i32),
        ("green", i32),
        ("checkered", i32),
        ("white", i32),
        ("black_and_white", i32),
    ]


class CutTrackPenalties(ctypes.Structure):
    # -1.0 = none pending
    _pack_ = 1
    _fields_ = [
        ("drive_through", f32),
        ("stop_and_go", f32),
        ("pit_stop", f32),
        ("time_deduction", f32),
        ("slow_down", f32),
    ]


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
        ("sector_time_previous_self", f32 * 3),   # cumulative: [2] is the full lap time
        ("sector_time_best_self", f32 * 3),
        ("time_delta_front", f32),
        ("time_delta_behind", f32),
        ("pitstop_status", i32),
        ("in_pitlane", i32),
        ("num_pitstops", i32),
        ("penalties", CutTrackPenalties),
        ("car_speed", f32),                       # m/s
        ("tire_type_front", i32),
        ("tire_type_rear", i32),
        ("tire_subtype_front", i32),
        ("tire_subtype_rear", i32),
        ("base_penalty_weight", f32),
        ("aid_penalty_weight", f32),
        ("drs_state", i32),
        ("ptp_state", i32),
        ("virtual_energy", f32),
        ("penalty_type", i32),
        ("penalty_reason", i32),
        ("engine_state", i32),
        ("orientation", Vec3F32),
        ("discharge_rate", f32),
        ("brake_regen", f32),
        ("unused1", f32),
    ]


class SharedHeader(ctypes.Structure):
    """Fixed part of the header, up to player_name. Then come the player's vehicle data, num_cars at
    DRIVERS_OFFSET - 4, and the driver array."""
    _pack_ = 1
    _fields_ = [
        ("version_major", i32),
        ("version_minor", i32),
        ("all_drivers_offset", i32),
        ("driver_data_size", i32),
        ("game_mode", i32),
        ("game_paused", i32),
        ("game_in_menus", i32),
        ("game_in_replay", i32),
        ("game_using_vr", i32),
        ("game_player_in_garage", i32),
        ("player", PlayerData),
        ("track_name", u8 * 64),
        ("layout_name", u8 * 64),
        ("track_id", i32),
        ("layout_id", i32),
        ("layout_length", f32),
        ("sector_start_factors", f32 * 3),
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
        ("flags", Flags),
        ("scoring", u8 * 208),           # position .. unused3 (player scoring & timings)
        ("vehicle_info", DriverInfo),    # the car currently in focus (player, or the watched car in replays)
        ("player_name", u8 * 64),
    ]


def decode(raw):
    return bytes(raw).split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


class R3ESharedMemory:
    """Read-only view of RaceRoom's shared memory.

    Opens the existing mapping instead of creating it, so starting this before
    the game never leaves behind a wrong-sized mapping the game would then reuse.
    """
    FILE_MAP_READ = 0x0004

    def __init__(self):
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.OpenFileMappingW.restype = wintypes.HANDLE
        self._k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self._k32.MapViewOfFile.restype = ctypes.c_void_p
        self._k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        self._k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self._k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._handle = None
        self._view = None

    def open(self):
        if self._view:
            return True
        handle = self._k32.OpenFileMappingW(self.FILE_MAP_READ, False, SHARED_MEMORY_NAME)
        if not handle:
            return False
        view = self._k32.MapViewOfFile(handle, self.FILE_MAP_READ, 0, 0, 0)
        if not view:
            self._k32.CloseHandle(handle)
            return False
        self._handle, self._view = handle, view
        return True

    def close(self):
        if self._view:
            self._k32.UnmapViewOfFile(self._view)
        if self._handle:
            self._k32.CloseHandle(self._handle)
        self._handle = self._view = None

    def read(self):
        """Returns a consistent snapshot: (header, [DriverData, ...]) or (None, []) when unavailable."""
        if not self.open():
            return None, []
        header = SharedHeader.from_buffer_copy(ctypes.string_at(self._view, ctypes.sizeof(SharedHeader)))
        if header.version_major <= 0:
            return None, []
        # RaceRoom 3.6 reports all_drivers_offset as 2008, which is num_cars: the drivers really start at 2012
        offset = max(header.all_drivers_offset, DRIVERS_OFFSET)
        size = header.driver_data_size
        if size < ctypes.sizeof(DriverData):
            return header, []
        num_cars = ctypes.c_int32.from_address(self._view + offset - 4).value
        count = max(0, min(num_cars, NUM_DRIVERS_MAX))
        if count == 0:
            return header, []
        block = ctypes.string_at(self._view + offset, size * count)
        drivers = [DriverData.from_buffer_copy(block, i * size) for i in range(count)]
        return header, drivers

    def focused_slot(self):
        """slot_id of the car the game is currently showing (replay / spectate focus), or None."""
        header, _ = self.read()
        if header is None:
            return None
        return header.vehicle_info.slot_id


# Layout guard: these must match the official header or every field read is garbage.
assert ctypes.sizeof(DriverData) == 328, ctypes.sizeof(DriverData)
assert SharedHeader.flags.offset == 932 and SharedHeader.vehicle_info.offset == 1196
