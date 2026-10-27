"""
Unit tests for Session-Specific Context, Interaction Logging, and Speak-Only-When-Spoken-To mode.
"""

import unittest
import time
from pathlib import Path
from engineer.context import build_telemetry_context
from telemetry.collector import TelemetrySnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from race.fuel import FuelStatus
from race.tires import TireStatus
from race.gaps import GapAnalysis
from utils.interaction_logger import InteractionLogger


class TestSessionAndLogging(unittest.TestCase):
    def make_snapshot(self, session: SessionState):
        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=session,
            race_state=RaceState.RACESTATE_RACING,
            car_name="Porsche 911 GT3",
            car_class_name="GT3",
            speed_kmh=210.0,
            rpm=7500.0,
            max_rpm=8500.0,
            gear=4,
            throttle=1.0,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Silverstone",
            track_variation="Grand Prix",
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=20,
            lap_invalidated=False,
            player_name="Driver",
            position=3,
            current_lap=5,
            laps_completed=4,
            current_sector=2,
            current_lap_time=45.0,
            last_lap_time=98.5,
            best_lap_time=97.8,
            split_time_ahead=1.2,
            split_time_behind=2.5,
            sector1_time=32.0,
            sector2_time=33.5,
            sector3_time=32.3,
            best_sector1_time=31.8,
            best_sector2_time=33.2,
            best_sector3_time=32.0,
            fuel_level_pct=60.0,
            fuel_liters=40.0,
            fuel_capacity_liters=65.0,
            water_temp_c=90.0,
            oil_temp_c=102.0,
            oil_pressure_kpa=400.0,
            tyre_compound="Soft",
            tyre_tread_temps_c=(95.0, 96.0, 92.0, 93.0),
            tyre_carcass_temps_c=(92.0, 93.0, 90.0, 91.0),
            tyre_pressures_bar=(1.82, 1.83, 1.78, 1.79),
            tyre_wear_pct=(15.0, 16.0, 12.0, 13.0),
            brake_temps_c=(420.0, 430.0, 380.0, 390.0),
            ambient_temp_c=22.0,
            track_temp_c=29.0,
            rain_density=0.0,
            wind_speed_ms=1.5,
            flag_colour=FlagColour.FLAG_GREEN,
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            participants=()
        )

    def make_tires(self, wear=(15.0, 16.0, 12.0, 13.0)):
        life = tuple(100.0 - w for w in wear)
        return TireStatus(
            compound="Soft",
            tread_temps_c=(95.0, 96.0, 92.0, 93.0),
            pressures_bar=(1.82, 1.83, 1.78, 1.79),
            wear_pct=wear,
            life_pct=life,
            wear_rate_per_lap=2.5,
            estimated_laps_remaining=25.0,
            is_overheating=False,
            is_cold=False,
            overheated_tires=[],
            cold_tires=[],
            balance_note=None,
            is_gone_off=False,
            is_very_worn=False,
            is_critical_wear=False
        )

    def test_practice_context(self):
        snap = self.make_snapshot(SessionState.SESSION_PRACTICE)
        fuel = FuelStatus(40.0, 65.0, 60.0, 3.2, 12.5, 15, 2.5, None, False, False, False)
        tires = self.make_tires()
        gaps = GapAnalysis(None, None, None, None, False, None)

        context = build_telemetry_context(snap, fuel, tires, gaps)
        self.assertIn("PRACTICE / SETUP", context)
        self.assertIn("TIRE WINDOW & BALANCE", context)
        self.assertIn("BRAKES", context)

    def test_qualifying_context(self):
        snap = self.make_snapshot(SessionState.SESSION_QUALIFY)
        fuel = FuelStatus(15.0, 65.0, 23.0, 3.2, 4.6, 5, 0.4, None, False, False, False)
        tires = self.make_tires(wear=(5.0, 6.0, 4.0, 4.0))
        gaps = GapAnalysis(None, None, None, None, False, None)

        context = build_telemetry_context(snap, fuel, tires, gaps)
        self.assertIn("QUALIFYING / TIME ATTACK", context)
        self.assertIn("FLYING LAP TIMING", context)
        self.assertIn("TIRES FOR FLYING LAP", context)
        self.assertNotIn("FUEL FOR RUN", context)

    def test_race_context(self):
        snap = self.make_snapshot(SessionState.SESSION_RACE)
        fuel = FuelStatus(40.0, 65.0, 60.0, 3.2, 12.5, 15, 2.5, None, False, False, False)
        tires = self.make_tires()
        gaps = GapAnalysis(None, None, None, None, False, None)

        context = build_telemetry_context(snap, fuel, tires, gaps)
        self.assertIn("SESSION MODE]: RACE", context)
        self.assertIn("YOUR TIMING", context)
        self.assertIn("FUEL STATUS — COMPUTED", context)
        self.assertIn("PIT STRATEGY — AUTHORITATIVE", context)
        self.assertIn("TIRE LIFE & HEALTH", context)

    def test_interaction_logger_writes_file(self):
        test_dir = Path("logs/test_logs")
        test_logger = InteractionLogger(log_dir=test_dir)

        test_logger.log_interaction(
            interaction_type="TEST INTERACTION",
            driver_input="How is my fuel?",
            session_info="SESSION: RACE",
            telemetry_context="Context info",
            full_prompt_sent="Prompt text",
            ai_response_text="You have 5.4 laps remaining.",
            emotion="calm and composed",
            latency_sec=0.45
        )

        self.assertTrue(test_logger.log_file.exists())
        content = test_logger.log_file.read_text(encoding="utf-8")
        self.assertIn("TEST INTERACTION", content)
        self.assertIn("How is my fuel?", content)
        self.assertIn("You have 5.4 laps remaining.", content)

        # Cleanup test log
        try:
            test_logger.log_file.unlink()
            if test_logger.latest_log_file.exists():
                test_logger.latest_log_file.unlink()
            test_dir.rmdir()
        except Exception:
            pass

    def test_interaction_logger_switches_session(self):
        test_dir = Path("logs/test_session_switch_logs")
        test_logger = InteractionLogger(log_dir=test_dir)

        test_logger.switch_session("QUALIFY")
        q_file = test_logger.log_file
        self.assertIn("QUALIFY", str(q_file))

        test_logger.log_interaction(
            interaction_type="QUALIFY INTERACTION",
            driver_input="What's my pole delta?",
            session_info="QUALIFY",
            telemetry_context="Context",
            full_prompt_sent="Prompt",
            ai_response_text="Two tenths off pole.",
            emotion="relaxed, witty, and calm"
        )
        self.assertTrue(q_file.exists())
        self.assertIn("QUALIFY INTERACTION", q_file.read_text(encoding="utf-8"))

        # Switch session to COUNTDOWN
        test_logger.switch_session("COUNTDOWN")
        c_file = test_logger.log_file
        self.assertNotEqual(q_file, c_file)
        self.assertIn("COUNTDOWN", str(c_file))

        # Switch session to RACE
        test_logger.switch_session("RACE")
        r_file = test_logger.log_file
        self.assertIn("RACE", str(r_file))

        test_logger.log_interaction(
            interaction_type="RACE INTERACTION",
            driver_input="Pace update?",
            session_info="RACE",
            telemetry_context="Context",
            full_prompt_sent="Prompt",
            ai_response_text="Matching leader pace.",
            emotion="relaxed, witty, and calm"
        )
        self.assertTrue(r_file.exists())
        self.assertIn("RACE INTERACTION", r_file.read_text(encoding="utf-8"))

        # Cleanup
        try:
            for f in test_dir.glob("*.log"):
                f.unlink()
            test_dir.rmdir()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
