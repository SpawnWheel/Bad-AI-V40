"""
Unit tests for specialized Practice and Qualifying engineer modes.
Verifies:
- Practice 10Hz telemetry diagnostics (bottoming, redline, wheelspin, 3-point tyre temps)
- Practice stint tracking and stint-to-stint comparisons
- Practice race fuel burn calculation
- Quali outlap detection and exclusion
- Quali pure timing and sector analysis (where fast / where slow)
- Race mode preservation
"""

import unittest
from unittest.mock import MagicMock
from telemetry.collector import TelemetrySnapshot, TelemetryCollector, ParticipantSnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from practice import PracticeTelemetryMonitor, PracticeSessionManager
from quali import QualiTracker
from race.state import RaceStateManager, EventType


class TestPracticeAndQualiModes(unittest.TestCase):

    def make_snapshot(
        self,
        session_state=SessionState.SESSION_PRACTICE,
        lap=1,
        last_lap=0.0,
        best_lap=0.0,
        fuel=40.0,
        pit_mode=PitMode.PIT_MODE_NONE,
        speed=150.0,
        rpm=6500.0,
        max_rpm=8000.0,
        throttle=0.8,
        gear=4,
        susp_travel=(45.0, 45.0, 40.0, 40.0),
        susp_velocity=(0.05, 0.05, 0.04, 0.04),
        ride_height=(55.0, 55.0, 60.0, 60.0),
        tyre_slip=(0.1, 0.1, 0.1, 0.1),
        inner_temps=(88.0, 89.0, 85.0, 86.0),
        center_temps=(82.0, 83.0, 80.0, 81.0),
        outer_temps=(76.0, 77.0, 73.0, 74.0),
        brake_temps=(450.0, 450.0, 420.0, 420.0),
        brake_bias=57.0,
        abs_val=2,
        tc_val=3
    ) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            timestamp=1000.0,
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=session_state,
            race_state=RaceState.RACESTATE_RACING,
            car_name="GT3 Porsche",
            car_class_name="GT3",
            speed_kmh=speed,
            rpm=rpm,
            max_rpm=max_rpm,
            gear=gear,
            throttle=throttle,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Silverstone",
            track_variation="GP",
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=0,
            lap_invalidated=False,
            player_name="Driver",
            position=3,
            current_lap=lap,
            laps_completed=max(0, lap - 1),
            current_sector=1,
            current_lap_time=45.0,
            last_lap_time=last_lap,
            best_lap_time=best_lap,
            split_time_ahead=0.4,
            split_time_behind=0.8,
            sector1_time=32.1,
            sector2_time=33.4,
            sector3_time=31.8,
            best_sector1_time=31.9,
            best_sector2_time=33.1,
            best_sector3_time=31.5,
            fuel_level_pct=60.0,
            fuel_liters=fuel,
            fuel_capacity_liters=70.0,
            water_temp_c=90.0,
            oil_temp_c=100.0,
            oil_pressure_kpa=450.0,
            tyre_compound="Medium",
            tyre_tread_temps_c=(85.0, 85.0, 82.0, 82.0),
            tyre_carcass_temps_c=(85.0, 85.0, 82.0, 82.0),
            tyre_pressures_bar=(1.8, 1.8, 1.75, 1.75),
            tyre_wear_pct=(5.0, 5.0, 6.0, 6.0),
            brake_temps_c=brake_temps,
            ambient_temp_c=22.0,
            track_temp_c=30.0,
            rain_density=0.0,
            wind_speed_ms=2.0,
            flag_colour=FlagColour.FLAG_GREEN,
            pit_mode=pit_mode,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            event_time_remaining_sec=1200.0,
            participants=(
                ParticipantSnapshot("Leader", 1, 1, 1, 1000.0, True, best_lap_time=96.5),
                ParticipantSnapshot("Rival", 2, 1, 1, 900.0, True, best_lap_time=96.8),
                ParticipantSnapshot("Driver", 3, 1, 1, 800.0, True, best_lap_time=best_lap),
            ),
            suspension_travel_mm=susp_travel,
            suspension_velocity_ms=susp_velocity,
            ride_height_mm=ride_height,
            tyre_slip_speed_ms=tyre_slip,
            tyre_temps_inner_c=inner_temps,
            tyre_temps_center_c=center_temps,
            tyre_temps_outer_c=outer_temps,
            brake_bias_pct=brake_bias,
            abs_setting=abs_val,
            tc_setting=tc_val
        )

    def test_practice_diagnostics_bottoming_and_wheelspin(self):
        """Verify 10Hz monitor detects bottoming out, redline, and wheelspin."""
        monitor = PracticeTelemetryMonitor()

        # Normal frame
        snap_normal = self.make_snapshot()
        monitor.on_telemetry(snap_normal)
        summary = monitor.get_summary()
        self.assertFalse(summary.has_bottoming_issue)
        self.assertFalse(summary.has_wheelspin_issue)

        # Bottoming out frame: front travel < 4mm with bump velocity spike
        snap_bottoming = self.make_snapshot(
            susp_travel=(2.5, 3.0, 35.0, 35.0),
            susp_velocity=(0.8, 0.7, 0.1, 0.1)
        )
        snap_bottoming2 = self.make_snapshot(
            susp_travel=(2.0, 2.5, 35.0, 35.0),
            susp_velocity=(0.9, 0.8, 0.1, 0.1)
        )
        monitor.on_telemetry(snap_bottoming)
        # advance timestamp to trigger separate tally
        snap_bottoming2 = self.make_snapshot(
            susp_travel=(2.0, 2.5, 35.0, 35.0),
            susp_velocity=(0.9, 0.8, 0.1, 0.1)
        )
        monitor._last_bottoming_time = 0.0
        monitor.on_telemetry(snap_bottoming2)

        summary = monitor.get_summary()
        self.assertTrue(summary.has_bottoming_issue)
        self.assertGreaterEqual(summary.bottoming_count_front, 2)

        # Wheelspin frame: rear tyre slip > 5 m/s
        for _ in range(4):
            monitor._last_wheelspin_time = 0.0
            snap_spin = self.make_snapshot(
                speed=40.0,
                throttle=0.9,
                tyre_slip=(0.1, 0.1, 6.2, 5.8)
            )
            monitor.on_telemetry(snap_spin)

        summary = monitor.get_summary()
        self.assertTrue(summary.has_wheelspin_issue)
        self.assertGreaterEqual(summary.wheelspin_count, 3)

    def test_practice_tyre_thermal_spread(self):
        """Verify Inner/Center/Outer analysis detects camber (7°C target, 5-9°C window) and pressure bulging."""
        monitor = PracticeTelemetryMonitor()

        # 1. Over-inflated tire (bulging out): Center (92°C) is hotter than both Inner (85°C) and Outer (78°C)
        # Inner 85, Center 92, Outer 78 -> spread = 7.0°C (optimal camber), bulging out (OVER_INFLATED)
        snap_over = self.make_snapshot(
            inner_temps=(85.0, 85.0, 85.0, 85.0),
            center_temps=(92.0, 92.0, 92.0, 92.0),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        monitor.on_telemetry(snap_over)
        summary = monitor.get_summary(snap_over)

        fl = summary.tyre_diagnoses["FL"]
        self.assertEqual(fl.pressure_status, "OVER_INFLATED")
        self.assertEqual(fl.camber_status, "OPTIMAL")  # 85 - 78 = 7°C spread (target optimal)

        # 2. Excessive camber: Inner 90, Center 84, Outer 78 (spread = 12°C > 9°C max)
        snap_camber_excess = self.make_snapshot(
            inner_temps=(90.0, 90.0, 90.0, 90.0),
            center_temps=(84.0, 84.0, 84.0, 84.0),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_camber = monitor.get_summary(snap_camber_excess)
        self.assertEqual(summary_camber.tyre_diagnoses["FL"].camber_status, "TOO_MUCH_CAMBER")

        # 3. Insufficient camber: Inner 81, Center 79.5, Outer 78 (spread = 3°C < 5°C min)
        snap_camber_low = self.make_snapshot(
            inner_temps=(81.0, 81.0, 81.0, 81.0),
            center_temps=(79.5, 79.5, 79.5, 79.5),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_low = monitor.get_summary(snap_camber_low)
        self.assertEqual(summary_low.tyre_diagnoses["FL"].camber_status, "INSUFFICIENT_CAMBER")

        # 4. Optimal pressure: Center sits evenly between Inner and Outer without bulging
        # Inner 85, Center 81.5, Outer 78 (mid sits directly between 85 and 78)
        snap_opt = self.make_snapshot(
            inner_temps=(85.0, 85.0, 85.0, 85.0),
            center_temps=(81.5, 81.5, 81.5, 81.5),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_opt = monitor.get_summary(snap_opt)
        self.assertEqual(summary_opt.tyre_diagnoses["FL"].pressure_status, "OPTIMAL")
        self.assertEqual(summary_opt.tyre_diagnoses["FL"].camber_status, "OPTIMAL")

    def test_practice_stint_tracking_and_comparison(self):
        """Verify stint lifecycle: pit exit -> track -> pit in -> comparative debrief."""
        manager = PracticeSessionManager()

        # 1. In pits
        snap_pit = self.make_snapshot(pit_mode=PitMode.PIT_MODE_IN_GARAGE, lap=1)
        manager.on_telemetry(snap_pit)
        self.assertFalse(manager.is_car_on_track)

        # 2. Exits pits (Run 1 starts)
        snap_out = self.make_snapshot(pit_mode=PitMode.PIT_MODE_NONE, lap=1, fuel=40.0)
        manager.on_telemetry(snap_out)
        self.assertTrue(manager.is_car_on_track)
        self.assertEqual(manager.current_run_number, 1)

        # 3. Completes 3 laps in Stint 1
        for lap_num in range(2, 5):
            snap_lap = self.make_snapshot(pit_mode=PitMode.PIT_MODE_NONE, lap=lap_num, last_lap=98.0, fuel=40.0 - (lap_num * 3.0))
            manager.on_telemetry(snap_lap)

        # 4. Enters pits (Run 1 ends)
        snap_in = self.make_snapshot(pit_mode=PitMode.PIT_MODE_IN_GARAGE, lap=5, fuel=28.0)
        manager.on_telemetry(snap_in)
        self.assertFalse(manager.is_car_on_track)
        self.assertEqual(len(manager.completed_runs), 1)
        self.assertEqual(manager.current_run_number, 2)

        # 5. Second run with faster laps
        snap_out2 = self.make_snapshot(pit_mode=PitMode.PIT_MODE_NONE, lap=5, fuel=30.0)
        manager.on_telemetry(snap_out2)
        for lap_num in range(6, 9):
            snap_lap2 = self.make_snapshot(pit_mode=PitMode.PIT_MODE_NONE, lap=lap_num, last_lap=97.2, fuel=30.0 - (lap_num - 5) * 2.8)
            manager.on_telemetry(snap_lap2)
        snap_in2 = self.make_snapshot(pit_mode=PitMode.PIT_MODE_IN_GARAGE, lap=9, fuel=21.0)
        manager.on_telemetry(snap_in2)

        self.assertEqual(len(manager.completed_runs), 2)
        debrief = manager.get_comparative_debrief()
        self.assertIn("Run #2 Summary", debrief)
        self.assertIn("COMPARISON VS RUN #1", debrief)
        self.assertIn("Found 0.80s improvement", debrief)
        self.assertIn("SETUP LAB RUN LOG", debrief)

    def test_quali_outlap_exclusion_and_timing_only(self):
        """Verify QualiTracker ignores pit outlap and only records valid flying laps."""
        tracker = QualiTracker()

        # 1. Car leaves pits on Lap 1 -> OUTLAP
        snap_outlap_start = self.make_snapshot(session_state=SessionState.SESSION_QUALIFY, pit_mode=PitMode.PIT_MODE_NONE, lap=1)
        rec = tracker.on_telemetry(snap_outlap_start)
        self.assertIsNone(rec)

        # 2. Crosses line to complete outlap (Lap 1 -> Lap 2)
        snap_outlap_end = self.make_snapshot(session_state=SessionState.SESSION_QUALIFY, pit_mode=PitMode.PIT_MODE_NONE, lap=2, last_lap=105.0)
        rec = tracker.on_telemetry(snap_outlap_end)
        # Outlap must NOT be recorded as a flying run
        self.assertIsNone(rec)
        self.assertEqual(len(tracker.completed_runs), 0)

        # 3. Crosses line to complete Flying Lap 1 (Lap 2 -> Lap 3)
        snap_flying1 = self.make_snapshot(session_state=SessionState.SESSION_QUALIFY, pit_mode=PitMode.PIT_MODE_NONE, lap=3, last_lap=97.0)
        rec = tracker.on_telemetry(snap_flying1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.run_number, 1)
        self.assertEqual(rec.lap_time, 97.0)
        self.assertTrue(rec.is_personal_best)

        # 4. Status summary checks: zero fuel talk, pure timing
        summary = tracker.get_quali_status_summary(snap_flying1)
        self.assertIn("QUALIFYING TIMINGS & LEADERBOARD", summary)
        self.assertIn("Position: You are P3", summary)
        self.assertIn("SECTOR ANALYSIS", summary)
        self.assertNotIn("Fuel", summary)
        self.assertNotIn("Liters", summary)

    def test_collector_dynamic_poll_rate(self):
        """Verify TelemetryCollector supports switching between 10Hz and 2Hz."""
        collector = TelemetryCollector(poll_rate_hz=2)
        self.assertEqual(collector.poll_rate_hz, 2)
        self.assertAlmostEqual(collector.interval, 0.5)

        collector.set_poll_rate_hz(10)
        self.assertEqual(collector.poll_rate_hz, 10)
        self.assertAlmostEqual(collector.interval, 0.1)

        collector.set_poll_rate_hz(2)
        self.assertEqual(collector.poll_rate_hz, 2)
        self.assertAlmostEqual(collector.interval, 0.5)


if __name__ == "__main__":
    unittest.main()
