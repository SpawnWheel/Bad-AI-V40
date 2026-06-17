import ctypes

# Constants
R3E_SHARED_MEMORY_NAME = "$R3E"
R3E_VERSION_MAJOR = 3
R3E_VERSION_MINOR = 4
R3E_NUM_DRIVERS_MAX = 128
R3E_TIRE_INDEX_MAX = 4
R3E_TIRE_TEMP_INDEX_MAX = 3
R3E_PIT_MENU_MAX = 12

# Type aliases
r3e_int32 = ctypes.c_int32
r3e_float32 = ctypes.c_float
r3e_float64 = ctypes.c_double
r3e_u8char = ctypes.c_ubyte

# Enums (mapped to simple constants or used as int32)
R3E_SESSION_PRACTICE = 0
R3E_SESSION_QUALIFY = 1
R3E_SESSION_RACE = 2
R3E_SESSION_WARMUP = 3

R3E_SESSION_PHASE_GARAGE = 1
R3E_SESSION_PHASE_GRIDWALK = 2
R3E_SESSION_PHASE_FORMATION = 3
R3E_SESSION_PHASE_COUNTDOWN = 4
R3E_SESSION_PHASE_GREEN = 5
R3E_SESSION_PHASE_CHECKERED = 6

# Structs
class R3E_VEC3_F32(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("x", r3e_float32),
        ("y", r3e_float32),
        ("z", r3e_float32),
    ]

class R3E_VEC3_F64(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("x", r3e_float64),
        ("y", r3e_float64),
        ("z", r3e_float64),
    ]

class R3E_ORI_F32(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("pitch", r3e_float32),
        ("yaw", r3e_float32),
        ("roll", r3e_float32),
    ]

class R3E_SECTOR_STARTS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("sector1", r3e_float32),
        ("sector2", r3e_float32),
        ("sector3", r3e_float32),
    ]

class R3E_PLAYERDATA(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("user_id", r3e_int32),
        ("game_simulation_ticks", r3e_int32),
        ("game_simulation_time", r3e_float64),
        ("position", R3E_VEC3_F64),
        ("velocity", R3E_VEC3_F64),
        ("local_velocity", R3E_VEC3_F64),
        ("acceleration", R3E_VEC3_F64),
        ("local_acceleration", R3E_VEC3_F64),
        ("orientation", R3E_VEC3_F64),
        ("rotation", R3E_VEC3_F64),
        ("angular_acceleration", R3E_VEC3_F64),
        ("angular_velocity", R3E_VEC3_F64),
        ("local_angular_velocity", R3E_VEC3_F64),
        ("local_g_force", R3E_VEC3_F64),
        ("steering_force", r3e_float64),
        ("steering_force_percentage", r3e_float64),
        ("engine_torque", r3e_float64),
        ("current_downforce", r3e_float64),
        ("voltage", r3e_float64),
        ("ers_level", r3e_float64),
        ("power_mgu_h", r3e_float64),
        ("power_mgu_k", r3e_float64),
        ("torque_mgu_k", r3e_float64),
        ("suspension_deflection", r3e_float64 * R3E_TIRE_INDEX_MAX),
        ("suspension_velocity", r3e_float64 * R3E_TIRE_INDEX_MAX),
        ("camber", r3e_float64 * R3E_TIRE_INDEX_MAX),
        ("ride_height", r3e_float64 * R3E_TIRE_INDEX_MAX),
        ("front_wing_height", r3e_float64),
        ("front_roll_angle", r3e_float64),
        ("rear_roll_angle", r3e_float64),
        ("third_spring_suspension_deflection_front", r3e_float64),
        ("third_spring_suspension_velocity_front", r3e_float64),
        ("third_spring_suspension_deflection_rear", r3e_float64),
        ("third_spring_suspension_velocity_rear", r3e_float64),
        ("unused1", r3e_float64),
        ("unused2", r3e_float64),
        ("unused3", r3e_float64),
    ]

class R3E_FLAGS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("yellow", r3e_int32),
        ("yellowCausedIt", r3e_int32),
        ("yellowOvertake", r3e_int32),
        ("yellowPositionsGained", r3e_int32),
        ("sector_yellow", r3e_int32 * 3),
        ("closest_yellow_distance_into_track", r3e_float32),
        ("blue", r3e_int32),
        ("black", r3e_int32),
        ("green", r3e_int32),
        ("checkered", r3e_int32),
        ("white", r3e_int32),
        ("black_and_white", r3e_int32),
    ]

class R3E_CAR_DAMAGE(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("engine", r3e_float32),
        ("transmission", r3e_float32),
        ("aerodynamics", r3e_float32),
        ("suspension", r3e_float32),
        ("unused1", r3e_float32),
        ("unused2", r3e_float32),
    ]

class R3E_CUT_TRACK_PENALTIES(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("drive_through", r3e_float32),
        ("stop_and_go", r3e_float32),
        ("pit_stop", r3e_float32),
        ("time_deduction", r3e_float32),
        ("slow_down", r3e_float32),
    ]

class R3E_DRS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("equipped", r3e_int32),
        ("available", r3e_int32),
        ("numActivationsLeft", r3e_int32),
        ("engaged", r3e_int32),
    ]

class R3E_PUSH_TO_PASS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("available", r3e_int32),
        ("engaged", r3e_int32),
        ("amount_left", r3e_int32),
        ("engaged_time_left", r3e_float32),
        ("wait_time_left", r3e_float32),
    ]

class R3E_TIRE_TEMP(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("current_temp", r3e_float32 * R3E_TIRE_TEMP_INDEX_MAX),
        ("optimal_temp", r3e_float32),
        ("cold_temp", r3e_float32),
        ("hot_temp", r3e_float32),
    ]

class R3E_BRAKE_TEMP(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("current_temp", r3e_float32),
        ("optimal_temp", r3e_float32),
        ("cold_temp", r3e_float32),
        ("hot_temp", r3e_float32),
    ]

class R3E_AID_SETTINGS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("abs", r3e_int32),
        ("tc", r3e_int32),
        ("esp", r3e_int32),
        ("countersteer", r3e_int32),
        ("cornering", r3e_int32),
    ]

class R3E_DRIVER_INFO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("name", r3e_u8char * 64),
        ("car_number", r3e_int32),
        ("class_id", r3e_int32),
        ("model_id", r3e_int32),
        ("team_id", r3e_int32),
        ("livery_id", r3e_int32),
        ("manufacturer_id", r3e_int32),
        ("user_id", r3e_int32),
        ("slot_id", r3e_int32),
        ("class_performance_index", r3e_int32),
        ("engine_type", r3e_int32),
        ("car_width", r3e_float32),
        ("car_length", r3e_float32),
        ("rating", r3e_float32),
        ("reputation", r3e_float32),
        ("unused1", r3e_float32),
        ("unused2", r3e_float32),
    ]

class R3E_DRIVER_DATA(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("driver_info", R3E_DRIVER_INFO),
        ("finish_status", r3e_int32),
        ("place", r3e_int32),
        ("place_class", r3e_int32),
        ("lap_distance", r3e_float32),
        ("lap_distance_fraction", r3e_float32),
        ("position", R3E_VEC3_F32),
        ("track_sector", r3e_int32),
        ("completed_laps", r3e_int32),
        ("current_lap_valid", r3e_int32),
        ("lap_time_current_self", r3e_float32),
        ("sector_time_current_self", r3e_float32 * 3),
        ("sector_time_previous_self", r3e_float32 * 3),
        ("sector_time_best_self", r3e_float32 * 3),
        ("time_delta_front", r3e_float32),
        ("time_delta_behind", r3e_float32),
        ("pitstop_status", r3e_int32),
        ("in_pitlane", r3e_int32),
        ("num_pitstops", r3e_int32),
        ("penalties", R3E_CUT_TRACK_PENALTIES),
        ("car_speed", r3e_float32),
        ("tire_type_front", r3e_int32),
        ("tire_type_rear", r3e_int32),
        ("tire_subtype_front", r3e_int32),
        ("tire_subtype_rear", r3e_int32),
        ("base_penalty_weight", r3e_float32),
        ("aid_penalty_weight", r3e_float32),
        ("drs_state", r3e_int32),
        ("ptp_state", r3e_int32),
        ("virtual_energy", r3e_float32),
        ("penaltyType", r3e_int32),
        ("penaltyReason", r3e_int32),
        ("engineState", r3e_int32),
        ("orientation", R3E_VEC3_F32),
        ("unused1", r3e_float32),
        ("unused2", r3e_float32),
        ("unused3", r3e_float32),
    ]

class R3E_SHARED(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("version_major", r3e_int32),
        ("version_minor", r3e_int32),
        ("all_drivers_offset", r3e_int32),
        ("driver_data_size", r3e_int32),
        ("game_mode", r3e_int32),
        ("game_paused", r3e_int32),
        ("game_in_menus", r3e_int32),
        ("game_in_replay", r3e_int32),
        ("game_using_vr", r3e_int32),
        ("game_unused1", r3e_int32),
        ("player", R3E_PLAYERDATA),
        ("track_name", r3e_u8char * 64),
        ("layout_name", r3e_u8char * 64),
        ("track_id", r3e_int32),
        ("layout_id", r3e_int32),
        ("layout_length", r3e_float32),
        ("sector_start_factors", R3E_SECTOR_STARTS),
        ("race_session_laps", r3e_int32 * 3),
        ("race_session_minutes", r3e_int32 * 3),
        ("event_index", r3e_int32),
        ("session_type", r3e_int32),
        ("session_iteration", r3e_int32),
        ("session_length_format", r3e_int32),
        ("session_pit_speed_limit", r3e_float32),
        ("session_phase", r3e_int32),
        ("start_lights", r3e_int32),
        ("tire_wear_active", r3e_int32),
        ("fuel_use_active", r3e_int32),
        ("number_of_laps", r3e_int32),
        ("session_time_duration", r3e_float32),
        ("session_time_remaining", r3e_float32),
        ("max_incident_points", r3e_int32),
        ("event_unused1", r3e_float32),
        ("event_unused2", r3e_float32),
        ("pit_window_status", r3e_int32),
        ("pit_window_start", r3e_int32),
        ("pit_window_end", r3e_int32),
        ("in_pitlane", r3e_int32),
        ("pit_menu_selection", r3e_int32),
        ("pit_menu_state", r3e_int32 * R3E_PIT_MENU_MAX),
        ("pit_state", r3e_int32),
        ("pit_total_duration", r3e_float32),
        ("pit_elapsed_time", r3e_float32),
        ("pit_action", r3e_int32),
        ("num_pitstops", r3e_int32),
        ("pit_min_duration_total", r3e_float32),
        ("pit_min_duration_left", r3e_float32),
        ("flags", R3E_FLAGS),
        ("position", r3e_int32),
        ("position_class", r3e_int32),
        ("finish_status", r3e_int32),
        ("cut_track_warnings", r3e_int32),
        ("penalties", R3E_CUT_TRACK_PENALTIES),
        ("num_penalties", r3e_int32),
        ("completed_laps", r3e_int32),
        ("current_lap_valid", r3e_int32),
        ("track_sector", r3e_int32),
        ("lap_distance", r3e_float32),
        ("lap_distance_fraction", r3e_float32),
        ("lap_time_best_leader", r3e_float32),
        ("lap_time_best_leader_class", r3e_float32),
        ("session_best_lap_sector_times", r3e_float32 * 3),
        ("lap_time_best_self", r3e_float32),
        ("sector_time_best_self", r3e_float32 * 3),
        ("lap_time_previous_self", r3e_float32),
        ("sector_time_previous_self", r3e_float32 * 3),
        ("lap_time_current_self", r3e_float32),
        ("sector_time_current_self", r3e_float32 * 3),
        ("lap_time_delta_leader", r3e_float32),
        ("lap_time_delta_leader_class", r3e_float32),
        ("time_delta_front", r3e_float32),
        ("time_delta_behind", r3e_float32),
        ("time_delta_best_self", r3e_float32),
        ("best_individual_sector_time_self", r3e_float32 * 3),
        ("best_individual_sector_time_leader", r3e_float32 * 3),
        ("best_individual_sector_time_leader_class", r3e_float32 * 3),
        ("incident_points", r3e_int32),
        ("lap_valid_state", r3e_int32),
        ("prev_lap_valid", r3e_int32),
        ("unused1", r3e_float32),
        ("unused2", r3e_float32),
        ("unused3", r3e_float32),
        ("vehicle_info", R3E_DRIVER_INFO),
        ("player_name", r3e_u8char * 64),
        ("control_type", r3e_int32),
        ("car_speed", r3e_float32),
        ("engine_rps", r3e_float32),
        ("max_engine_rps", r3e_float32),
        ("upshift_rps", r3e_float32),
        ("gear", r3e_int32),
        ("num_gears", r3e_int32),
        ("car_cg_location", R3E_VEC3_F32),
        ("car_orientation", R3E_ORI_F32),
        ("local_acceleration", R3E_VEC3_F32),
        ("total_mass", r3e_float32),
        ("fuel_left", r3e_float32),
        ("fuel_capacity", r3e_float32),
        ("fuel_per_lap", r3e_float32),
        ("virtual_energy_left", r3e_float32),
        ("virtual_energy_capacity", r3e_float32),
        ("virtual_energy_per_lap", r3e_float32),
        ("engine_temp", r3e_float32),
        ("engine_oil_temp", r3e_float32),
        ("fuel_pressure", r3e_float32),
        ("engine_oil_pressure", r3e_float32),
        ("turbo_pressure", r3e_float32),
        ("throttle", r3e_float32),
        ("throttle_raw", r3e_float32),
        ("brake", r3e_float32),
        ("brake_raw", r3e_float32),
        ("clutch", r3e_float32),
        ("clutch_raw", r3e_float32),
        ("steer_input_raw", r3e_float32),
        ("steer_lock_degrees", r3e_int32),
        ("steer_wheel_range_degrees", r3e_int32),
        ("aid_settings", R3E_AID_SETTINGS),
        ("drs", R3E_DRS),
        ("pit_limiter", r3e_int32),
        ("push_to_pass", R3E_PUSH_TO_PASS),
        ("brake_bias", r3e_float32),
        ("drs_numActivationsTotal", r3e_int32),
        ("ptp_numActivationsTotal", r3e_int32),
        ("battery_soc", r3e_float32),
        ("water_left", r3e_float32),
        ("abs_setting", r3e_int32),
        ("headlights", r3e_int32),
        ("vehicle_unused1", r3e_float32),
        ("tire_type", r3e_int32),
        ("tire_rps", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("tire_speed", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("tire_grip", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("tire_wear", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("tire_flatspot", r3e_int32 * R3E_TIRE_INDEX_MAX),
        ("tire_pressure", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("tire_dirt", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("tire_temp", R3E_TIRE_TEMP * R3E_TIRE_INDEX_MAX),
        ("tire_type_front", r3e_int32),
        ("tire_type_rear", r3e_int32),
        ("tire_subtype_front", r3e_int32),
        ("tire_subtype_rear", r3e_int32),
        ("brake_temp", R3E_BRAKE_TEMP * R3E_TIRE_INDEX_MAX),
        ("brake_pressure", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("traction_control_setting", r3e_int32),
        ("engine_map_setting", r3e_int32),
        ("engine_brake_setting", r3e_int32),
        ("traction_control_percent", r3e_float32),
        ("tire_on_mtrl", r3e_int32 * R3E_TIRE_INDEX_MAX),
        ("tire_load", r3e_float32 * R3E_TIRE_INDEX_MAX),
        ("car_damage", R3E_CAR_DAMAGE),
        ("num_cars", r3e_int32),
        ("all_drivers_data_1", R3E_DRIVER_DATA * R3E_NUM_DRIVERS_MAX),
    ]