"""
Unit tests for Pit Wall Timing Board, Strategy Predictions,
Shared Memory Struct Offsets, and Driver Audio Parsing.
"""

import unittest
import time
import ctypes
from telemetry.shared_memory import (
    AMS2SharedMemory,
    ParticipantInfo,
    GameState,
    SessionState,
    RaceState,
    FlagColour,
    PitMode
)
from telemetry.collector import TelemetrySnapshot, ParticipantSnapshot
from race.standings import CompetitorTimingBoard, CompetitorEntry
from race.fuel import FuelTracker
from race.tires import TireTracker
from engineer.brain import RaceEngineerBrain


class TestPitwallStrategy(unittest.TestCase):

    def test_shared_memory_struct_offsets(self):
        """Verifies critical ctypes struct offsets match official AMS2 SharedMemory.h."""
        self.assertEqual(AMS2SharedMemory.mVersion.offset, 0)
        self.assertEqual(AMS2SharedMemory.mGameState.offset, 8)
        self.assertEqual(AMS2SharedMemory.mSessionState.offset, 12)
        self.assertEqual(AMS2SharedMemory.mParticipantInfo.offset, 28)
        # 28 + (64 * 100) = 6428 -> mUnfilteredThrottle at 6428
        self.assertEqual(AMS2SharedMemory.mUnfilteredThrottle.offset, 6428)
        self.assertEqual(AMS2SharedMemory.mCarName.offset, 6444)
        self.assertEqual(AMS2SharedMemory.mTrackLocation.offset, 6576)
        self.assertEqual(AMS2SharedMemory.mOilTempCelsius.offset, 6820)
        self.assertEqual(AMS2SharedMemory.mWaterTempCelsius.offset, 6828)
        self.assertEqual(AMS2SharedMemory.mSpeed.offset, 6848)
        self.assertEqual(AMS2SharedMemory.mAntiLockActive.offset, 6888)
        self.assertEqual(AMS2SharedMemory.mLastOpponentCollisionIndex.offset, 6892)
        # Vectors come BEFORE tyres
        self.assertEqual(AMS2SharedMemory.mOrientation.offset, 6908)
        self.assertEqual(AMS2SharedMemory.mTyreFlags.offset, 6992)
        self.assertEqual(AMS2SharedMemory.mTyreTemp.offset, 7072)
        self.assertEqual(AMS2SharedMemory.mTyreTreadTemp.offset, 7200)
        # Weather offsets
        self.assertEqual(AMS2SharedMemory.mAmbientTemperature.offset, 7292)
        self.assertEqual(AMS2SharedMemory.mTrackTemperature.offset, 7296)
        self.assertEqual(AMS2SharedMemory.mRainDensity.offset, 7300)
        # Version 8 additions
        self.assertEqual(AMS2SharedMemory.mAirPressure.offset, 7372)
        self.assertEqual(AMS2SharedMemory.mTyreCompound.offset, 19388)
        # AMS2 v10 additions
        self.assertEqual(AMS2SharedMemory.mTyreTempLeft.offset, 20584)
        self.assertEqual(AMS2SharedMemory.mDrsState.offset, 20632)

    def test_competitor_timing_board_catch_prediction(self):
        """Verifies competitor board generates catch-up predictions and pace deltas."""
        participants = (
            ParticipantSnapshot(
                name="Max Verstappen",
                position=1,
                lap=10,
                sector=2,
                lap_distance=3500.0,
                is_active=True,
                last_lap_time=91.500,
                best_lap_time=91.000,
                sector1_time=28.1,
                sector2_time=34.2,
                sector3_time=29.2,
                pit_mode=0
            ),
            ParticipantSnapshot(
                name="Lando Norris",
                position=2,
                lap=10,
                sector=2,
                lap_distance=3350.0,
                is_active=True,
                last_lap_time=91.800,
                best_lap_time=91.200,
                sector1_time=28.2,
                sector2_time=34.4,
                sector3_time=29.2,
                pit_mode=0
            ),
            ParticipantSnapshot(
                name="Driver",
                position=3,
                lap=10,
                sector=2,
                lap_distance=3200.0,
                is_active=True,
                last_lap_time=91.300,  # 0.5s faster than Norris
                best_lap_time=91.100,
                sector1_time=28.0,
                sector2_time=34.2,
                sector3_time=29.1,
                pit_mode=0
            ),
            ParticipantSnapshot(
                name="Charles Leclerc",
                position=4,
                lap=10,
                sector=2,
                lap_distance=3000.0,
                is_active=True,
                last_lap_time=92.100,
                best_lap_time=91.400,
                sector1_time=28.3,
                sector2_time=34.5,
                sector3_time=29.3,
                pit_mode=0
            ),
        )

        snap = TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_RACING,
            car_name="McLaren GT3",
            car_class_name="GT3",
            speed_kmh=200.0,
            rpm=7000.0,
            max_rpm=8000.0,
            gear=4,
            throttle=1.0,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Silverstone",
            track_variation="GP",
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=25,
            lap_invalidated=False,
            player_name="Driver",
            position=3,
            current_lap=10,
            laps_completed=9,
            current_sector=2,
            current_lap_time=45.0,
            last_lap_time=91.300,
            best_lap_time=91.100,
            split_time_ahead=2.5,  # 2.5s behind Norris
            split_time_behind=3.0,
            sector1_time=28.0,
            sector2_time=34.2,
            sector3_time=29.1,
            best_sector1_time=27.9,
            best_sector2_time=34.1,
            best_sector3_time=29.0,
            fuel_level_pct=60.0,
            fuel_liters=40.0,
            fuel_capacity_liters=65.0,
            water_temp_c=85.0,
            oil_temp_c=100.0,
            oil_pressure_kpa=400.0,
            tyre_compound="Medium",
            tyre_tread_temps_c=(90.0, 92.0, 88.0, 89.0),
            tyre_carcass_temps_c=(88.0, 90.0, 86.0, 87.0),
            tyre_pressures_bar=(1.85, 1.85, 1.80, 1.80),
            tyre_wear_pct=(20.0, 22.0, 18.0, 19.0),
            brake_temps_c=(350.0, 360.0, 320.0, 330.0),
            ambient_temp_c=24.0,
            track_temp_c=32.0,
            rain_density=0.0,
            wind_speed_ms=2.0,
            flag_colour=FlagColour.FLAG_GREEN,
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            participants=participants
        )

        board = CompetitorTimingBoard()
        entries, table_str = board.analyze(snap)

        self.assertEqual(len(entries), 4)
        # Check that player entry is identified
        player_entry = next(e for e in entries if e.is_player)
        self.assertEqual(player_entry.position, 3)

        # Check Norris (P2) entry: player is 0.5s faster (91.3 vs 91.8), gap is 2.5s -> catch in 5 laps
        norris_entry = next(e for e in entries if e.position == 2)
        self.assertAlmostEqual(norris_entry.pace_delta_sec, -0.5, places=2)
        self.assertIsNotNone(norris_entry.laps_to_catch)
        self.assertAlmostEqual(norris_entry.laps_to_catch, 5.0, delta=0.5)

        # Check table formatting
        self.assertIn("[COMPETITOR TIMING & PACE BOARD]", table_str)
        self.assertIn("Max Verstappen", table_str)
        self.assertIn("Lando Norris", table_str)
        self.assertIn("catch in ~5 laps", table_str)

    def test_fuel_pit_window_prediction(self):
        """Verifies rolling lap fuel burn and pit window lap calculation."""
        tracker = FuelTracker()

        # Simulate 4 laps: Lap 1 burns 3.0L (ignored), Lap 2 burns 3.2L, Lap 3 burns 3.1L, Lap 4 burns 3.0L
        tracker.on_lap_started(1, 50.0)
        tracker.on_lap_started(2, 47.0)  # 3.0L (Lap 1 ignored)
        tracker.on_lap_started(3, 43.8)  # 3.2L (Lap 2 counted)
        tracker.on_lap_started(4, 40.7)  # 3.1L (Lap 3 counted)
        tracker.on_lap_started(5, 37.7)  # 3.0L (Lap 4 counted)

        status = tracker.analyze(current_liters=37.7, capacity_liters=60.0, total_laps=30, current_lap=5)

        # Avg burn should be ~3.1L ((3.2 + 3.1 + 3.0)/3 = 3.1L)
        self.assertAlmostEqual(status.avg_per_lap, 3.1, delta=0.1)
        self.assertEqual(status.laps_counted, 3)
        # With 37.7L at 3.1L/lap -> ~12.2 laps remaining
        self.assertAlmostEqual(status.laps_remaining_fuel, 12.2, delta=0.5)
        # Race has 26 laps remaining (30 - 5 + 1), so needs pit stop!
        self.assertTrue(status.needs_pit_stop)
        # Pit window should be around Lap 5 + 10 = Lap 15
        self.assertIsNotNone(status.pit_window_lap)
        self.assertTrue(14 <= status.pit_window_lap <= 16)
        # Fuel needed should be positive
        self.assertTrue(status.fuel_needed_liters > 30.0)

    def test_tire_wear_and_limiting_corner(self):
        """Verifies per-corner wear rate and limiting tyre identification."""
        tracker = TireTracker()

        # Simulate 2 laps of wear: FL wears faster (3.0% / lap), others wear ~1.5%
        tracker.on_lap_started(1, (10.0, 10.0, 10.0, 10.0))
        tracker.on_lap_started(2, (13.0, 11.5, 11.4, 11.6))
        tracker.on_lap_started(3, (16.0, 13.0, 12.8, 13.2))

        status = tracker.analyze(
            compound="Soft",
            tread_temps=(88.0, 89.0, 85.0, 86.0),
            pressures=(1.8, 1.8, 1.8, 1.8),
            wear=(16.0, 13.0, 12.8, 13.2)
        )

        # Limiting tire must be Front-Left
        self.assertIn("FL", status.limiting_tire)
        self.assertIn("Front-Left", status.limiting_tire)
        # FL wear rate should be ~3.0%/lap
        self.assertAlmostEqual(status.corner_wear_rates[0], 3.0, delta=0.2)
        # Remaining margin to 50% life cliff: (84 - 50) = 34% / 3.0 = ~11.3 laps
        self.assertAlmostEqual(status.estimated_laps_remaining, 11.3, delta=1.5)

    def test_parse_audio_response(self):
        """Verifies transcription and engineer response separation."""
        sample_output = """TRANSCRIPT: What's the gap to the car behind?
RESPONSE: Gap behind is two point four seconds, opening by two tenths."""

        transcript, response = RaceEngineerBrain._parse_audio_response(sample_output)
        self.assertEqual(transcript, "What's the gap to the car behind?")
        self.assertEqual(response, "Gap behind is two point four seconds, opening by two tenths.")

        # Test fallback when model doesn't use tags
        raw_output = "Tires are in the window, keep pushing."
        t2, r2 = RaceEngineerBrain._parse_audio_response(raw_output)
        self.assertEqual(t2, "")
        self.assertEqual(r2, "Tires are in the window, keep pushing.")


if __name__ == "__main__":
    unittest.main()
