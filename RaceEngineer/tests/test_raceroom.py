"""
RaceRoom (R3E) support: shared memory reader, conversion to TelemetrySnapshot, sim auto-detection.
"""

import ctypes
import mmap
import sys
import unittest
import uuid
from unittest import mock

from telemetry import collector as collector_mod
from telemetry.collector import TelemetryCollector
from telemetry.r3e_converter import R3EConverter, split_sectors
from telemetry.r3e_data import R3EData
from telemetry.r3e_shared_memory import (
    DRIVER_SIZE, HEAD_SIZE, DriverData, R3EFrame, R3ESharedHead, R3ETelemetryReader,
    R3E_SESSION_PHASE_FORMATION, R3E_SESSION_PHASE_GREEN, R3E_SESSION_PRACTICE, R3E_SESSION_RACE,
    R3E_SESSION_LENGTH_TIME_AND_LAP_BASED, R3E_SESSION_LENGTH_TIME_BASED,
)
from telemetry.shared_memory import FlagColour, GameState, PitMode, RaceState, SessionState
from telemetry.sim_detect import SIM_AMS2, SIM_R3E, normalize_sim, running_sims


def _set_str(arr, text: str):
    raw = text.encode("utf-8")[: len(arr) - 1]
    ctypes.memmove(arr, raw, len(raw))


def make_head(**kw) -> R3ESharedHead:
    h = R3ESharedHead()
    h.version_major, h.version_minor = 3, 5
    h.all_drivers_offset, h.driver_data_size = HEAD_SIZE, DRIVER_SIZE
    h.session_type = kw.get("session_type", R3E_SESSION_RACE)
    h.session_phase = kw.get("session_phase", R3E_SESSION_PHASE_GREEN)
    h.session_length_format = kw.get("session_length_format", R3E_SESSION_LENGTH_TIME_BASED)
    _set_str(h.track_name, "Circuit Zandvoort")
    _set_str(h.layout_name, "Grand Prix")
    _set_str(h.player_name, "Danny")
    h.layout_length = 4259.0
    h.number_of_laps = kw.get("number_of_laps", -1)
    h.session_time_duration = 1800.0
    h.session_time_remaining = kw.get("time_remaining", 900.0)
    h.position = 2
    h.completed_laps = 7
    h.track_sector = 2
    h.lap_distance = 1500.0
    h.current_lap_valid = 1
    h.lap_time_best_self = 95.0
    h.lap_time_previous_self = 96.2
    h.lap_time_current_self = 33.0
    h.sector_time_current_self[:] = (31.0, -1.0, -1.0)
    h.best_individual_sector_time_self[:] = (30.5, 63.0, 95.0)
    h.time_delta_front = 1.4
    h.time_delta_behind = 0.8
    h.finish_status = kw.get("finish_status", 0)
    h.vehicle_info.model_id = 4567
    h.vehicle_info.class_id = 1703
    h.vehicle_info.slot_id = 1
    h.car_speed = kw.get("car_speed", 50.0)  # m/s
    h.engine_rps = 700.0
    h.max_engine_rps = 880.0
    h.gear = 4
    h.throttle, h.brake, h.clutch, h.steer_input_raw = 0.8, 0.0, 0.0, -0.1
    h.fuel_left, h.fuel_capacity, h.fuel_per_lap = 42.0, 120.0, 3.1
    h.engine_temp, h.engine_oil_temp, h.engine_oil_pressure = 92.0, 105.0, 450.0
    h.brake_bias = 0.42  # rear share -> 58% front
    h.abs_setting, h.traction_control_setting = 3, 5
    for i in range(4):
        h.tire_wear[i] = 0.9                         # 90% life left -> 10% worn
        h.tire_pressure[i] = 180.0                   # kPa
        h.tire_speed[i] = 50.0
        h.tire_rps[i] = 150.0
        h.tire_temp[i].current_temp[:] = (80.0, 85.0, 90.0)
        h.tire_temp[i].cold_temp, h.tire_temp[i].optimal_temp, h.tire_temp[i].hot_temp = 70.0, 88.0, 105.0
        h.brake_temp[i].current_temp = 450.0
        h.brake_temp[i].optimal_temp, h.brake_temp[i].hot_temp = 550.0, 800.0
        h.player.ride_height[i] = 0.055              # metres
        h.player.suspension_deflection[i] = 0.02
    h.tire_subtype_front = 3
    for part in ("engine", "transmission", "aerodynamics", "suspension"):
        setattr(h.car_damage, part, 1.0)
    h.pit_window_status = 0
    h.pit_state = kw.get("pit_state", 0)
    h.in_pitlane = kw.get("in_pitlane", 0)
    h.flags.yellow = kw.get("yellow", 0)
    h.flags.checkered = kw.get("checkered", 0)
    return h


def make_driver(name, place, slot, laps=7, in_pit=0, speed=50.0, finish=0) -> DriverData:
    d = DriverData()
    _set_str(d.driver_info.name, name)
    d.driver_info.slot_id = slot
    d.place = place
    d.completed_laps = laps
    d.track_sector = 2
    d.lap_distance = 1500.0 + 100.0 * (3 - place)
    d.sector_time_current_self[:] = (31.0, -1.0, -1.0)
    d.sector_time_previous_self[:] = (31.5, 64.0, 96.0)
    d.sector_time_best_self[:] = (31.0, 63.5, 95.5)
    d.in_pitlane = in_pit
    d.car_speed = speed
    d.finish_status = finish
    return d


def make_frame(head=None, drivers=None) -> R3EFrame:
    head = head or make_head()
    drivers = drivers if drivers is not None else [
        make_driver("Leader Guy", 1, 0), make_driver("Danny", 2, 1), make_driver("Chaser", 3, 2)]
    return R3EFrame(head=head, num_cars=len(drivers), drivers=drivers)


def make_data() -> R3EData:
    data = R3EData()
    data.cars = {4567: "BMW M4 GT3"}
    data.classes = {1703: "GTR 3"}
    data.path = "test"
    return data


class TestR3EConversion(unittest.TestCase):
    def setUp(self):
        self.conv = R3EConverter(make_data())

    def test_core_fields_and_units(self):
        s = self.conv.convert(make_frame())
        self.assertEqual(s.sim, SIM_R3E)
        self.assertFalse(s.weather_available)
        self.assertEqual(s.game_state, GameState.GAME_INGAME_PLAYING)
        self.assertEqual(s.session_state, SessionState.SESSION_RACE)
        self.assertEqual(s.race_state, RaceState.RACESTATE_RACING)
        self.assertEqual((s.car_name, s.car_class_name), ("BMW M4 GT3", "GTR 3"))
        self.assertEqual((s.track_name, s.track_variation), ("Circuit Zandvoort", "Grand Prix"))
        self.assertEqual(s.player_name, "Danny")
        self.assertAlmostEqual(s.speed_kmh, 180.0)
        self.assertAlmostEqual(s.rpm, 700.0 * 60 / (2 * 3.141592653589793), places=1)
        self.assertEqual((s.current_lap, s.laps_completed, s.current_sector), (8, 7, 1))
        self.assertAlmostEqual(s.fuel_liters, 42.0)
        self.assertAlmostEqual(s.fuel_level_pct, 35.0)
        self.assertAlmostEqual(s.tyre_wear_pct[0], 10.0, places=4)
        self.assertAlmostEqual(s.tyre_pressures_bar[0], 1.80)
        self.assertAlmostEqual(s.brake_bias_pct, 58.0, places=4)
        self.assertAlmostEqual(s.ride_height_cm[0], 5.5)
        self.assertAlmostEqual(s.suspension_travel_mm[0], 20.0)
        self.assertEqual(s.tyre_compound, "Medium")
        self.assertEqual(s.aero_damage, 0.0)
        self.assertEqual(s.tyre_temp_window_c[0], (70.0, 88.0, 105.0))
        self.assertEqual(s.tyre_carcass_temps_c, (0.0, 0.0, 0.0, 0.0))
        # FL: left = outer, right = inner (same convention as the AMS2 path)
        self.assertEqual((s.tyre_temps_inner_c[0], s.tyre_temps_outer_c[0]), (90.0, 80.0))
        self.assertEqual((s.tyre_temps_inner_c[1], s.tyre_temps_outer_c[1]), (80.0, 90.0))

    def test_sector_times_are_de_cumulated(self):
        s = self.conv.convert(make_frame())
        self.assertAlmostEqual(s.best_sector1_time, 30.5)
        self.assertAlmostEqual(s.best_sector2_time, 32.5)
        self.assertAlmostEqual(s.best_sector3_time, 32.0)
        self.assertEqual(split_sectors((30.0, 33.0, 31.0), 94.0), (30.0, 33.0, 31.0))  # already individual
        self.assertEqual(split_sectors((-1.0, -1.0, -1.0)), (0.0, 0.0, 0.0))

    def test_participants(self):
        s = self.conv.convert(make_frame(drivers=[
            make_driver("Leader Guy", 1, 0, in_pit=1, speed=0.0),
            make_driver("Danny", 2, 1),
            make_driver("Chaser", 3, 2, in_pit=1, speed=20.0)]))
        by = {p.name: p for p in s.participants}
        self.assertEqual(by["Leader Guy"].pit_mode, int(PitMode.PIT_MODE_IN_PIT))
        self.assertEqual(by["Chaser"].pit_mode, int(PitMode.PIT_MODE_DRIVING_INTO_PITS))
        self.assertEqual(by["Danny"].pit_mode, 0)
        self.assertAlmostEqual(by["Danny"].last_lap_time, 96.0)
        self.assertAlmostEqual(by["Danny"].best_lap_time, 95.5)
        self.assertEqual(by["Danny"].lap, 8)

    def test_session_and_race_states(self):
        s = self.conv.convert(make_frame(make_head(session_phase=R3E_SESSION_PHASE_FORMATION)))
        self.assertEqual(s.session_state, SessionState.SESSION_FORMATION_LAP)
        self.assertEqual(s.race_state, RaceState.RACESTATE_NOT_STARTED)
        s = self.conv.convert(make_frame(make_head(session_type=R3E_SESSION_PRACTICE)))
        self.assertEqual(s.session_state, SessionState.SESSION_PRACTICE)
        self.assertEqual(s.race_state, RaceState.RACESTATE_RACING)

    def test_timed_race_extra_lap_format(self):
        s = self.conv.convert(make_frame(make_head(session_length_format=R3E_SESSION_LENGTH_TIME_AND_LAP_BASED)))
        self.assertEqual(s.session_additional_laps, 1)
        s = self.conv.convert(make_frame(make_head()))
        self.assertEqual(s.session_additional_laps, 0)
        self.assertEqual(s.total_laps, 0)
        self.assertAlmostEqual(s.event_time_remaining_sec, 900.0)

    def test_chequered_only_when_player_finished(self):
        s = self.conv.convert(make_frame(make_head(checkered=1)))
        self.assertNotEqual(s.flag_colour, FlagColour.FLAG_CHEQUERED)
        self.assertFalse(s.is_race_finished)
        s = self.conv.convert(make_frame(make_head(checkered=1, finish_status=1)))
        self.assertEqual(s.flag_colour, FlagColour.FLAG_CHEQUERED)
        self.assertTrue(s.is_race_finished)
        self.assertEqual(s.race_state, RaceState.RACESTATE_FINISHED)

    def test_flags_and_pit_modes(self):
        self.assertEqual(self.conv.convert(make_frame(make_head(yellow=1))).flag_colour, FlagColour.FLAG_YELLOW)
        self.assertEqual(self.conv.convert(make_frame(make_head(pit_state=3, in_pitlane=1))).pit_mode, PitMode.PIT_MODE_IN_PIT)
        self.assertEqual(self.conv.convert(make_frame(make_head(pit_state=4, in_pitlane=1))).pit_mode, PitMode.PIT_MODE_DRIVING_OUT_OF_PITS)
        self.assertEqual(self.conv.convert(make_frame(make_head(in_pitlane=1))).pit_mode, PitMode.PIT_MODE_DRIVING_INTO_PITS)

    def test_impact_from_damage_jump(self):
        h = make_head()
        self.assertFalse(self.conv.convert(make_frame(h)).collision_flag)
        h.car_damage.aerodynamics = 0.6
        s = self.conv.convert(make_frame(h))
        self.assertTrue(s.collision_flag)
        self.assertGreater(s.collision_magnitude, 2500.0)
        self.assertAlmostEqual(s.aero_damage, 0.4, places=5)
        self.assertFalse(self.conv.convert(make_frame(h)).collision_flag)  # no further loss

    def test_menu_and_replay_states(self):
        h = make_head()
        h.session_type = -1
        self.assertEqual(self.conv.convert(make_frame(h)).game_state, GameState.GAME_FRONT_END)
        h = make_head()
        h.game_paused = 1
        self.assertEqual(self.conv.convert(make_frame(h)).game_state, GameState.GAME_INGAME_PAUSED)
        h = make_head()
        h.game_in_replay = 1
        self.assertEqual(self.conv.convert(make_frame(h)).game_state, GameState.GAME_INGAME_REPLAY)

    def test_unknown_car_without_data_file(self):
        data = R3EData()
        data._next_try = float("inf")  # never search the disk in tests
        s = R3EConverter(data).convert(make_frame())
        self.assertEqual(s.car_name, "RaceRoom car 4567")


@unittest.skipUnless(sys.platform == "win32", "named shared memory is Windows-only")
class TestR3EReader(unittest.TestCase):
    def _publish(self, head: R3ESharedHead, drivers, stride: int):
        tag = f"r3e_test_{uuid.uuid4().hex}"
        size = HEAD_SIZE + 4 + 128 * max(stride, DRIVER_SIZE) + 4096
        mem = mmap.mmap(-1, size, tagname=tag)
        offset = head.all_drivers_offset
        buf = bytearray(size)
        buf[:HEAD_SIZE] = bytes(head)
        buf[offset:offset + 4] = len(drivers).to_bytes(4, "little")
        for i, d in enumerate(drivers):
            start = offset + 4 + i * stride
            buf[start:start + DRIVER_SIZE] = bytes(d)
        mem.write(bytes(buf))
        self.addCleanup(mem.close)
        reader = R3ETelemetryReader(tag)
        self.addCleanup(reader.disconnect)
        return reader

    def test_reads_35_layout(self):
        head = make_head()
        reader = self._publish(head, [make_driver("A", 1, 0), make_driver("Danny", 2, 1)], DRIVER_SIZE)
        frame = reader.read_frame()
        self.assertIsNotNone(frame)
        self.assertTrue(frame.layout_ok)
        self.assertEqual(frame.num_cars, 2)
        self.assertEqual(R3EConverter(make_data()).convert(frame).participants[1].name, "Danny")
        self.assertEqual(reader.version, (3, 5))

    def test_follows_game_offsets_for_newer_layout(self):
        """Simulated 3.6: extra bytes before num_cars and a bigger per-driver block."""
        head = make_head()
        head.version_minor = 6
        head.all_drivers_offset = HEAD_SIZE + 64
        head.driver_data_size = DRIVER_SIZE + 16
        reader = self._publish(head, [make_driver("A", 1, 0), make_driver("Danny", 2, 1), make_driver("C", 3, 2)],
                               DRIVER_SIZE + 16)
        frame = reader.read_frame()
        self.assertIsNotNone(frame)
        self.assertFalse(frame.layout_ok)
        self.assertEqual([decode(d) for d in frame.drivers], ["A", "Danny", "C"])
        self.assertEqual(frame.drivers[2].place, 3)

    def test_rejects_other_major_version(self):
        head = make_head()
        head.version_major = 4
        self.assertIsNone(self._publish(head, [], DRIVER_SIZE).read_frame())

    def test_missing_mapping_is_never_created(self):
        """Creating $R3E before the game does would give RaceRoom a block of the wrong size."""
        tag = f"nope_{uuid.uuid4().hex}"
        reader = R3ETelemetryReader(tag)
        self.assertIsNone(reader.read_frame())
        k32 = ctypes.WinDLL("kernel32")
        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        self.assertFalse(k32.OpenFileMappingW(4, False, tag))


def decode(d):
    return bytes(d.driver_info.name).split(b"\x00")[0].decode()


class TestSimDetection(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_sim("RaceRoom"), SIM_R3E)
        self.assertEqual(normalize_sim("r3e"), SIM_R3E)
        self.assertEqual(normalize_sim("AMS2"), SIM_AMS2)
        self.assertEqual(normalize_sim(None), "auto")
        self.assertEqual(normalize_sim("whatever"), "auto")

    def test_running_sims_from_process_list(self):
        self.assertEqual(running_sims({"rrre64.exe": 10, "explorer.exe": 1}), {SIM_R3E})
        self.assertEqual(running_sims({"ams2avx.exe": 11}), {SIM_AMS2})
        self.assertEqual(running_sims({"chrome.exe": 3}), set())

    def test_candidates(self):
        c = TelemetryCollector()
        with mock.patch.object(collector_mod, "running_sims", return_value={SIM_R3E}):
            self.assertEqual(c._candidate_sims(), [SIM_R3E, SIM_AMS2])
        with mock.patch.object(collector_mod, "running_sims", return_value=set()):
            self.assertEqual(c._candidate_sims(), [SIM_AMS2])  # stale R3E mapping is never probed
        with mock.patch.object(collector_mod, "running_sims", return_value=None):
            self.assertEqual(c._candidate_sims(), [SIM_AMS2, SIM_R3E])
        forced = TelemetryCollector(sim="raceroom")
        self.assertEqual(forced._candidate_sims(), [SIM_R3E])

    def test_connects_to_raceroom_and_drops_stale_mapping(self):
        c = TelemetryCollector()
        c.r3e_converter = R3EConverter(make_data())
        frame = make_frame()
        with mock.patch.object(collector_mod, "running_sims", return_value={SIM_R3E}), \
                mock.patch.object(c.reader, "read_snapshot", return_value=None), \
                mock.patch.object(c.r3e_reader, "read_frame", return_value=frame):
            snap = c._connect_any()
        self.assertIsNotNone(snap)
        self.assertEqual(c.active_sim, SIM_R3E)
        self.assertEqual(snap.sim, SIM_R3E)
        c._last_liveness_check = 0.0
        with mock.patch.object(collector_mod, "running_sims", return_value=set()):
            self.assertFalse(c._sim_still_running())  # RaceRoom closed, mapping held open by another app
        c.active_sim = SIM_AMS2
        with mock.patch.object(collector_mod, "running_sims", return_value=set()):
            self.assertTrue(c._sim_still_running())   # AMS2 reports its own exit


class TestZolderRaceRegressions(unittest.TestCase):
    """Issues from the first RaceRoom race (Zolder, 26 Oct 2026, 15-minute timed race)."""

    def setUp(self):
        self.conv = R3EConverter(make_data())

    def test_clock_glitch_is_bridged(self):
        self.assertAlmostEqual(self.conv.convert(make_frame(make_head(time_remaining=800.0))).event_time_remaining_sec, 800.0)
        glitch = self.conv.convert(make_frame(make_head(time_remaining=-1.0))).event_time_remaining_sec
        self.assertGreater(glitch, 790.0)  # still counting from the last good value, not "expired"
        self.assertAlmostEqual(self.conv.convert(make_frame(make_head(time_remaining=798.0))).event_time_remaining_sec, 798.0)

    def test_clock_stays_expired_after_post_finish_countdown(self):
        for t in (12.0, 4.0, 0.4):
            self.conv.convert(make_frame(make_head(time_remaining=t)))
        self.assertEqual(self.conv.convert(make_frame(make_head(time_remaining=0.0))).event_time_remaining_sec, -1.0)
        # RaceRoom then starts a new countdown for the rest of the field
        self.assertEqual(self.conv.convert(make_frame(make_head(time_remaining=420.0))).event_time_remaining_sec, -1.0)
        # ...until a new session / restart
        self.conv.convert(make_frame(make_head(session_phase=R3E_SESSION_PHASE_FORMATION, time_remaining=900.0)))
        self.assertAlmostEqual(self.conv.convert(make_frame(make_head(time_remaining=899.0))).event_time_remaining_sec, 899.0)

    def _race_snapshots(self, mgr, clock_values):
        import dataclasses
        c = TelemetryCollector()
        c.r3e_converter = self.conv
        base = c._convert_r3e_snapshot(make_frame())
        lap = 1
        for i, t in enumerate(clock_values):
            if i and i % 10 == 0:
                lap += 1
            parts = tuple(dataclasses.replace(p, lap=lap + (1 if p.position == 1 else 0)) for p in base.participants)
            snap = dataclasses.replace(base, current_lap=lap, laps_completed=lap - 1, event_time_remaining_sec=t,
                                       current_lap_distance=400.0 * (i % 10), participants=parts,
                                       timestamp=base.timestamp + i)
            mgr.on_telemetry(snap)
        return mgr

    def test_bad_clock_reading_does_not_end_the_race(self):
        """Lap 1 clock dropped to -1 with 13 minutes left -> 'final lap' on lap 2 and the flag moving out every lap."""
        from race.events import EventType
        from race.state import RaceStateManager
        mgr = RaceStateManager()
        events = []
        mgr.register_event_listener(events.append)
        clock = [800.0 - i for i in range(40)]
        clock[8] = -1.0
        self._race_snapshots(mgr, clock)
        self.assertFalse(mgr._time_expired)
        types = [e.event_type for e in events]
        self.assertNotIn(EventType.FINAL_LAP, types)
        self.assertNotIn(EventType.LEADER_FINISHED, types)

    def test_real_expiry_still_detected(self):
        from race.state import RaceStateManager
        mgr = RaceStateManager()
        self._race_snapshots(mgr, [20.0 - i for i in range(20)] + [-1.0] * 5)
        self.assertTrue(mgr._time_expired)

    def test_leader_finished_means_we_finish_at_the_line(self):
        from race.race_distance import compute_race_distance
        import dataclasses
        snap = self.conv.convert(make_frame(make_head(time_remaining=420.0), drivers=[
            make_driver("Leader Guy", 1, 0, laps=11, finish=1), make_driver("Danny", 2, 1, laps=9)]))
        snap = dataclasses.replace(snap, current_lap=10, current_lap_distance=3300.0)
        rd = compute_race_distance(snap, 100.0, extra_laps=0)
        self.assertEqual((rd.finish_lap, rd.laps_incl_current), (10, 1))
        self.assertTrue(rd.is_final_lap)

    def test_finished_cars_entering_pits_are_not_pit_stops(self):
        s = self.conv.convert(make_frame(drivers=[
            make_driver("Leader Guy", 1, 0, in_pit=1, speed=15.0, finish=1), make_driver("Danny", 2, 1)]))
        self.assertEqual(s.participants[0].pit_mode, 0)

    def test_rival_car_and_rating_on_timing_board(self):
        from race.standings import CompetitorTimingBoard
        drivers = [make_driver("Leader Guy", 1, 0), make_driver("Danny", 2, 1), make_driver("Chaser", 3, 2)]
        drivers[2].driver_info.model_id = 4567
        drivers[2].driver_info.rating = 1712.0
        drivers[2].driver_info.reputation = 86.0
        s = self.conv.convert(make_frame(drivers=drivers))
        chaser = next(p for p in s.participants if p.name == "Chaser")
        self.assertEqual((chaser.car_name, chaser.rating, chaser.reputation), ("BMW M4 GT3", 1712.0, 86.0))
        _, table = CompetitorTimingBoard().analyze(s)
        self.assertIn("Car: BMW M4 GT3 | Rating 1712, Reputation 86", table)


class TestRaceRoomEndToEnd(unittest.TestCase):
    def test_state_manager_and_context(self):
        from engineer.context import build_telemetry_context
        from race.state import RaceStateManager

        c = TelemetryCollector()
        c.r3e_converter = R3EConverter(make_data())
        mgr = RaceStateManager()
        events = []
        mgr.register_event_listener(events.append)
        h = make_head(session_type=R3E_SESSION_PRACTICE)
        for i in range(30):
            h.lap_distance = 100.0 + i * 50.0
            h.lap_time_current_self = 2.0 + i
            mgr.on_telemetry(c._convert_r3e_snapshot(make_frame(h)))
        snap = mgr.current_snapshot
        self.assertEqual(snap.sim, SIM_R3E)
        ctx = build_telemetry_context(
            snap, mgr.get_current_fuel_status(), mgr.get_current_tire_status(), mgr.get_current_gap_analysis(),
            timing_board_table=mgr.get_timing_board_string(), extras=mgr.get_context_extras())
        self.assertIn("[SIM] RaceRoom", ctx)
        self.assertIn("not reported by RaceRoom", ctx)
        self.assertIn("SIM TYRE WINDOW", ctx)
        self.assertIn("BMW M4 GT3", ctx)


if __name__ == "__main__":
    unittest.main()
