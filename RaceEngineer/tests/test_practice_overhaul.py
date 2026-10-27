"""
Unit tests for Practice Mode Overhaul.
Verifies:
1. Tyre temp caching in pit box (no 0°C display)
2. Tyre temp averaging with outlap exclusion
3. Camber spread thresholds (< 5°C insufficient, 5-9°C optimal target 7°C, > 9°C excessive)
4. Tyre pressure bulging evaluation (middle temp between inner and outer)
5. Dynamic reference material loading from reference_material/
6. Session-specific logging without duplicate ai_interactions.log
7. Practice debrief prompt selection
"""

import unittest
import time
from pathlib import Path
from telemetry.collector import TelemetryCollector, TelemetrySnapshot, ParticipantSnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from practice import PracticeTelemetryMonitor, PracticeSessionManager
from engineer.prompts import load_reference_material, get_system_prompt, PRACTICE_PIT_DEBRIEF_PROMPT
from engineer.brain import RaceEngineerBrain
from engineer.context import build_telemetry_context
from race.fuel import FuelStatus
from race.tires import TireStatus
from race.gaps import GapAnalysis
from utils.interaction_logger import InteractionLogger


class TestPracticeOverhaul(unittest.TestCase):

    def make_snapshot(
        self,
        pit_mode=PitMode.PIT_MODE_NONE,
        speed=160.0,
        lap=2,
        inner_temps=(85.0, 85.0, 84.0, 84.0),
        center_temps=(81.5, 81.5, 80.5, 80.5),
        outer_temps=(78.0, 78.0, 77.0, 77.0),
    ) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=SessionState.SESSION_PRACTICE,
            race_state=RaceState.RACESTATE_RACING,
            car_name="Porsche 911 GT3",
            car_class_name="GT3",
            speed_kmh=speed,
            rpm=7000.0,
            max_rpm=8500.0,
            gear=4,
            throttle=0.9,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Silverstone",
            track_variation="Grand Prix",
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=0,
            lap_invalidated=False,
            player_name="Driver",
            position=1,
            current_lap=lap,
            laps_completed=max(0, lap - 1),
            current_sector=1,
            current_lap_time=30.0,
            last_lap_time=95.0,
            best_lap_time=94.5,
            split_time_ahead=0.0,
            split_time_behind=0.5,
            sector1_time=31.5,
            sector2_time=33.0,
            sector3_time=30.0,
            best_sector1_time=31.2,
            best_sector2_time=32.8,
            best_sector3_time=29.8,
            fuel_level_pct=50.0,
            fuel_liters=35.0,
            fuel_capacity_liters=70.0,
            water_temp_c=90.0,
            oil_temp_c=100.0,
            oil_pressure_kpa=450.0,
            tyre_compound="Medium",
            tyre_tread_temps_c=(82.0, 82.0, 81.0, 81.0),
            tyre_carcass_temps_c=(82.0, 82.0, 81.0, 81.0),
            tyre_pressures_bar=(1.8, 1.8, 1.75, 1.75),
            tyre_wear_pct=(5.0, 5.0, 5.0, 5.0),
            brake_temps_c=(450.0, 450.0, 400.0, 400.0),
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
            participants=(),
            suspension_travel_mm=(45.0, 45.0, 40.0, 40.0),
            suspension_velocity_ms=(0.05, 0.05, 0.04, 0.04),
            ride_height_mm=(55.0, 55.0, 60.0, 60.0),
            tyre_slip_speed_ms=(0.1, 0.1, 0.1, 0.1),
            tyre_temps_inner_c=inner_temps,
            tyre_temps_center_c=center_temps,
            tyre_temps_outer_c=outer_temps,
            brake_bias_pct=56.0,
            abs_setting=3,
            tc_setting=2
        )

    def test_camber_spread_rules(self):
        """
        Verify camber spread thresholds:
        - Target ~7°C
        - < 5°C = INSUFFICIENT_CAMBER
        - 5°C to 9°C = OPTIMAL
        - > 9°C = TOO_MUCH_CAMBER
        """
        monitor = PracticeTelemetryMonitor()

        # Optimal camber: Inner 85, Outer 78 (spread = 7.0°C)
        snap_opt = self.make_snapshot(
            inner_temps=(85.0, 85.0, 85.0, 85.0),
            center_temps=(81.5, 81.5, 81.5, 81.5),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_opt = monitor.get_summary(snap_opt)
        self.assertEqual(summary_opt.tyre_diagnoses["FL"].camber_status, "OPTIMAL")

        # Insufficient camber: Inner 82, Outer 78 (spread = 4.0°C < 5.0°C)
        snap_low = self.make_snapshot(
            inner_temps=(82.0, 82.0, 82.0, 82.0),
            center_temps=(80.0, 80.0, 80.0, 80.0),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_low = monitor.get_summary(snap_low)
        self.assertEqual(summary_low.tyre_diagnoses["FL"].camber_status, "INSUFFICIENT_CAMBER")

        # Excessive camber: Inner 88, Outer 78 (spread = 10.0°C > 9.0°C)
        snap_high = self.make_snapshot(
            inner_temps=(88.0, 88.0, 88.0, 88.0),
            center_temps=(83.0, 83.0, 83.0, 83.0),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_high = monitor.get_summary(snap_high)
        self.assertEqual(summary_high.tyre_diagnoses["FL"].camber_status, "TOO_MUCH_CAMBER")

    def test_tyre_bulging_pressure_rules(self):
        """
        Verify pressure evaluation based on tyre bulging:
        - Optimal: middle temp sits between inner and outer (contact patch flat)
        - Over-inflated (bulging out): middle temp hotter than gradient / above both
        - Under-inflated (bulging in): middle temp cooler than gradient / below both
        """
        monitor = PracticeTelemetryMonitor()

        # Optimal: Inner 85, Center 81.5, Outer 78 (middle is 81.5, directly between 85 and 78)
        snap_opt = self.make_snapshot(
            inner_temps=(85.0, 85.0, 85.0, 85.0),
            center_temps=(81.5, 81.5, 81.5, 81.5),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_opt = monitor.get_summary(snap_opt)
        self.assertEqual(summary_opt.tyre_diagnoses["FL"].pressure_status, "OPTIMAL")

        # Over-inflated (bulging out): Center 88°C is hotter than both Inner (85°C) and Outer (78°C)
        snap_over = self.make_snapshot(
            inner_temps=(85.0, 85.0, 85.0, 85.0),
            center_temps=(88.0, 88.0, 88.0, 88.0),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_over = monitor.get_summary(snap_over)
        self.assertEqual(summary_over.tyre_diagnoses["FL"].pressure_status, "OVER_INFLATED")

        # Under-inflated (bulging in): Center 76°C is cooler than both Inner (85°C) and Outer (78°C)
        snap_under = self.make_snapshot(
            inner_temps=(85.0, 85.0, 85.0, 85.0),
            center_temps=(76.0, 76.0, 76.0, 76.0),
            outer_temps=(78.0, 78.0, 78.0, 78.0)
        )
        summary_under = monitor.get_summary(snap_under)
        self.assertEqual(summary_under.tyre_diagnoses["FL"].pressure_status, "UNDER_INFLATED")

    def test_tyre_temp_averaging_excludes_outlap(self):
        """Verify PracticeTelemetryMonitor ignores outlap (lap 1) and averages flying laps (lap 2+)."""
        monitor = PracticeTelemetryMonitor()

        # Outlap frame (lap 1) with cold tyre temps
        snap_outlap = self.make_snapshot(
            lap=1,
            inner_temps=(60.0, 60.0, 60.0, 60.0),
            center_temps=(58.0, 58.0, 58.0, 58.0),
            outer_temps=(55.0, 55.0, 55.0, 55.0)
        )
        monitor.on_telemetry(snap_outlap)

        # Flying lap frames (lap 2) with hot optimal tyre temps
        for _ in range(15):
            snap_flying = self.make_snapshot(
                lap=2,
                inner_temps=(85.0, 85.0, 85.0, 85.0),
                center_temps=(81.5, 81.5, 81.5, 81.5),
                outer_temps=(78.0, 78.0, 78.0, 78.0)
            )
            monitor.on_telemetry(snap_flying)

        summary = monitor.get_summary()
        self.assertTrue(summary.is_temp_averaged)
        # Averaged temps should be ~85.0, not contaminated by outlap 60.0
        self.assertAlmostEqual(summary.averaged_inner_temps_c[0], 85.0, places=1)
        self.assertAlmostEqual(summary.averaged_center_temps_c[0], 81.5, places=1)
        self.assertAlmostEqual(summary.averaged_outer_temps_c[0], 78.0, places=1)
        self.assertEqual(summary.tyre_diagnoses["FL"].camber_status, "OPTIMAL")

    def test_dynamic_reference_material_loader(self):
        """Verify load_reference_material scans reference_material/ and includes files in system prompt."""
        ref_text = load_reference_material("reference_material")
        self.assertTrue(len(ref_text) > 500)
        self.assertIn("basic setup.txt", ref_text)
        self.assertIn("SETUP REFERENCE MATERIAL", ref_text)

        sys_prompt = get_system_prompt()
        self.assertIn("SETUP REFERENCE MATERIAL", sys_prompt)
        self.assertIn("basic setup.txt", sys_prompt)

    def test_no_duplicate_interaction_logging(self):
        """Verify interaction logger writes ONLY to session-specific log file, not ai_interactions.log."""
        test_dir = Path("logs/test_no_dup_logs")
        logger = InteractionLogger(log_dir=test_dir)

        logger.log_interaction(
            interaction_type="PRACTICE TEST",
            driver_input="How are tyre temps?",
            session_info="PRACTICE",
            telemetry_context="Context",
            full_prompt_sent="Prompt",
            ai_response_text="Tyre pressures are optimal.",
            emotion="calm"
        )

        # Session-specific log file should exist
        self.assertTrue(logger.log_file.exists())
        self.assertIn("PRACTICE TEST", logger.log_file.read_text(encoding="utf-8"))

        # Unified cumulative log file should NOT exist in this test dir
        self.assertFalse(logger.latest_log_file.exists())

        # Cleanup
        try:
            for f in test_dir.glob("*.log"):
                f.unlink()
            test_dir.rmdir()
        except Exception:
            pass

    def test_practice_context_cached_note(self):
        """Verify build_telemetry_context includes cached note when in pit box."""
        snap_pits = self.make_snapshot(pit_mode=PitMode.PIT_MODE_IN_GARAGE, speed=0.0)
        fuel = FuelStatus(35.0, 70.0, 50.0, 3.5, 10.0, 10, 0.0, None, False, False, False)
        tires = TireStatus(
            compound="Medium",
            tread_temps_c=(82.0, 82.0, 81.0, 81.0),
            pressures_bar=(1.8, 1.8, 1.75, 1.75),
            wear_pct=(5.0, 5.0, 5.0, 5.0),
            life_pct=(95.0, 95.0, 95.0, 95.0),
            wear_rate_per_lap=1.5,
            estimated_laps_remaining=40.0,
            is_overheating=False,
            is_cold=False,
            overheated_tires=[],
            cold_tires=[],
            balance_note=None,
            is_gone_off=False,
            is_very_worn=False,
            is_critical_wear=False
        )
        gaps = GapAnalysis(None, None, None, None, False, None)

        context = build_telemetry_context(snap_pits, fuel, tires, gaps)
        self.assertIn("last on-track reading retained in pits", context)
        self.assertIn("camber optimal (target ~7°C)", context)
        self.assertIn("pressure optimal (no bulging)", context)


if __name__ == "__main__":
    unittest.main()
