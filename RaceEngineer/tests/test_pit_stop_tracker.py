"""
Unit tests for PitStopTracker: pit stop duration measurement,
JSON persistence, rolling window calculation, and garage filtering.
"""

import unittest
import tempfile
import json
from pathlib import Path

from race.pit_stop_tracker import PitStopTracker, DEFAULT_PIT_LOSS_SEC
from telemetry.collector import TelemetrySnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode


def make_dummy_snapshot(
    pit_mode: PitMode = PitMode.PIT_MODE_NONE,
    timestamp: float = 1000.0,
    track_name: str = "Interlagos",
    track_variation: str = "Grand Prix",
    current_lap: int = 5
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        timestamp=timestamp,
        game_state=GameState.GAME_INGAME_PLAYING,
        session_state=SessionState.SESSION_RACE,
        race_state=RaceState.RACESTATE_RACING,
        car_name="Porsche 911 GT3 R",
        car_class_name="GT3",
        speed_kmh=120.0,
        rpm=6000.0,
        max_rpm=8500.0,
        gear=3,
        throttle=0.8,
        brake=0.0,
        steering=0.0,
        clutch=0.0,
        track_name=track_name,
        track_variation=track_variation,
        track_length_m=4309.0,
        num_sectors=3,
        total_laps=25,
        lap_invalidated=False,
        player_name="Driver",
        position=3,
        current_lap=current_lap,
        laps_completed=current_lap - 1,
        current_sector=1,
        current_lap_time=45.0,
        last_lap_time=92.5,
        best_lap_time=91.8,
        split_time_ahead=1.5,
        split_time_behind=2.0,
        sector1_time=30.0,
        sector2_time=31.0,
        sector3_time=30.5,
        best_sector1_time=29.5,
        best_sector2_time=30.8,
        best_sector3_time=30.0,
        fuel_level_pct=50.0,
        fuel_liters=45.0,
        fuel_capacity_liters=90.0,
        water_temp_c=90.0,
        oil_temp_c=95.0,
        oil_pressure_kpa=400.0,
        tyre_compound="Dry",
        tyre_tread_temps_c=(85.0, 85.0, 85.0, 85.0),
        tyre_carcass_temps_c=(85.0, 85.0, 85.0, 85.0),
        tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
        tyre_wear_pct=(10.0, 10.0, 10.0, 10.0),
        brake_temps_c=(400.0, 400.0, 400.0, 400.0),
        ambient_temp_c=22.0,
        track_temp_c=32.0,
        rain_density=0.0,
        wind_speed_ms=2.0,
        flag_colour=FlagColour.FLAG_GREEN,
        pit_mode=pit_mode,
        aero_damage=0.0,
        engine_damage=0.0,
        collision_flag=False,
        collision_magnitude=0.0,
        drs_state=0,
        ers_mode=0
    )


class TestPitStopTracker(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "test_pit_stops.json"
        self.tracker = PitStopTracker(
            storage_path=self.storage_path,
            default_pit_loss_sec=45.0,
            max_measurements=3
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_fallback_when_no_data(self):
        """Returns 45.0 default fallback when no pit stops have been recorded for track."""
        self.assertEqual(self.tracker.get_pit_loss_sec("Unknown Track", "GP"), 45.0)
        loss, is_learned, count = self.tracker.get_pit_loss_info("Unknown Track", "GP")
        self.assertEqual(loss, 45.0)
        self.assertFalse(is_learned)
        self.assertEqual(count, 0)

    def test_record_measurement_and_rolling_average(self):
        """Verifies 3-stop rolling average calculation and window truncation."""
        track = "Interlagos"
        layout = "Grand Prix"

        # 1st stop: 28.0s
        avg1 = self.tracker.record_measurement(track, layout, 28.0)
        self.assertAlmostEqual(avg1, 28.0)
        loss, is_learned, count = self.tracker.get_pit_loss_info(track, layout)
        self.assertTrue(is_learned)
        self.assertEqual(count, 1)

        # 2nd stop: 30.0s -> avg (28 + 30) / 2 = 29.0s
        avg2 = self.tracker.record_measurement(track, layout, 30.0)
        self.assertAlmostEqual(avg2, 29.0)
        self.assertEqual(self.tracker.get_pit_loss_info(track, layout)[2], 2)

        # 3rd stop: 32.0s -> avg (28 + 30 + 32) / 3 = 30.0s
        avg3 = self.tracker.record_measurement(track, layout, 32.0)
        self.assertAlmostEqual(avg3, 30.0)
        self.assertEqual(self.tracker.get_pit_loss_info(track, layout)[2], 3)

        # 4th stop: 34.0s -> oldest (28.0) dropped -> measurements [30, 32, 34] -> avg = 32.0s
        avg4 = self.tracker.record_measurement(track, layout, 34.0)
        self.assertAlmostEqual(avg4, 32.0)
        loss, is_learned, count = self.tracker.get_pit_loss_info(track, layout)
        self.assertEqual(count, 3)
        self.assertAlmostEqual(loss, 32.0)

    def test_json_persistence(self):
        """Manually recorded total losses are persisted (v2 file) and reloadable."""
        track = "Silverstone"
        layout = "GP"
        self.tracker.record_measurement(track, layout, 26.5)
        self.tracker.record_measurement(track, layout, 27.5)
        self.assertTrue(self.storage_path.exists())
        with open(self.storage_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["tracks"]["Silverstone|GP"]["loss_total_legacy"], [26.5, 27.5])
        new_tracker = PitStopTracker(storage_path=self.storage_path)
        self.assertAlmostEqual(new_tracker.get_pit_loss_sec(track, layout), 27.0)
        loss, is_learned, count = new_tracker.get_pit_loss_info(track, layout)
        self.assertTrue(is_learned)
        self.assertEqual(count, 2)

    def test_telemetry_pit_stop_detection(self):
        """
        Pit loss is split into drive-through loss (lane time - time the same stretch takes at racing pace
        - stationary time) and service time (fuel / learned refuel rate).
        """
        from types import SimpleNamespace

        class FakeTrack:
            def time_between(self, a, b, pace=90.0):
                return 10.0  # the pit lane stretch takes 10 s at racing speed

        self.tracker._track_model = FakeTrack()

        def snap(mode, t, speed=80.0, fuel=20.0, dist=4000.0):
            s = make_dummy_snapshot(mode, timestamp=t)
            return s.__class__(**{**s.__dict__, "speed_kmh": speed, "fuel_liters": fuel, "current_lap_distance": dist})

        self.tracker.on_telemetry(snap(PitMode.PIT_MODE_NONE, 1000.0))
        self.tracker.on_telemetry(snap(PitMode.PIT_MODE_DRIVING_INTO_PITS, 1002.0, dist=4000.0))
        self.assertTrue(self.tracker._is_in_pit)
        # stationary 10 s while 20 L goes in (2 L/s)
        t = 1010.0
        self.tracker.on_telemetry(snap(PitMode.PIT_MODE_IN_PIT, t, speed=0.0, fuel=20.0))
        for i in range(1, 11):
            self.tracker.on_telemetry(snap(PitMode.PIT_MODE_IN_PIT, t + i, speed=0.0, fuel=20.0 + 2.0 * i))
        self.tracker.on_telemetry(snap(PitMode.PIT_MODE_DRIVING_OUT_OF_PITS, 1025.0, fuel=40.0))
        # lane time = 1032.0 - 1002.0 = 30 s ; loss = 30 - 10 = 20 s ; drive-through = 20 - 10 = 10 s
        self.tracker.on_telemetry(snap(PitMode.PIT_MODE_NONE, 1032.0, fuel=40.0, dist=300.0))
        self.assertFalse(self.tracker._is_in_pit)

        drive, n = self.tracker.get_drive_loss("Interlagos", "Grand Prix")
        self.assertEqual(n, 1)
        self.assertAlmostEqual(drive, 10.0, places=0)
        rate, learned = self.tracker.get_refuel_rate("Porsche 911 GT3 R")
        self.assertTrue(learned)
        self.assertAlmostEqual(rate, 2.0, places=1)
        # predicted loss for a 30 L stop = 10 s drive-through + 15 s fuel + 1 s release
        loss, breakdown, ok = self.tracker.predict_stop_loss("Interlagos", "Grand Prix", "Porsche 911 GT3 R", 30.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(loss, 26.0, places=0)
        self.assertIn("drive-through", breakdown)
        self.assertIn("20.0 L added", self.tracker.last_stop_summary())

    def test_telemetry_garage_visit_filtered(self):
        """Verifies that visiting the garage during a pit visit is disregarded."""
        t = 1000.0
        self.tracker.on_telemetry(make_dummy_snapshot(PitMode.PIT_MODE_NONE, timestamp=t))

        # Enter pits
        t = 1002.0
        self.tracker.on_telemetry(make_dummy_snapshot(PitMode.PIT_MODE_DRIVING_INTO_PITS, timestamp=t))

        # Enter garage
        t = 1010.0
        self.tracker.on_telemetry(make_dummy_snapshot(PitMode.PIT_MODE_IN_GARAGE, timestamp=t))

        # Exit garage and drive out
        t = 1040.0
        self.tracker.on_telemetry(make_dummy_snapshot(PitMode.PIT_MODE_DRIVING_OUT_OF_PITS, timestamp=t))

        # Rejoin track
        t = 1050.0
        self.tracker.on_telemetry(make_dummy_snapshot(PitMode.PIT_MODE_NONE, timestamp=t))

        # Should NOT be recorded because garage was visited
        loss, is_learned, count = self.tracker.get_pit_loss_info("Interlagos", "Grand Prix")
        self.assertFalse(is_learned)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
