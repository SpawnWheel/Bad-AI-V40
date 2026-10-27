"""
Unit tests for Context Builder.
"""

import unittest
from engineer.context import build_telemetry_context
from race.fuel import FuelStatus
from race.tires import TireStatus
from race.gaps import GapAnalysis


class TestContextBuilder(unittest.TestCase):
    def test_context_generation_without_crash(self):
        fuel = FuelStatus(
            current_liters=35.0,
            capacity_liters=60.0,
            level_pct=58.3,
            avg_per_lap=3.2,
            laps_remaining_fuel=10.9,
            laps_remaining_race=15,
            fuel_deficit_laps=4.1,
            target_consumption=2.33,
            needs_pit_stop=True,
            is_critical=False,
            is_empty_risk=False
        )

        tires = TireStatus(
            compound="Soft",
            tread_temps_c=(95.0, 94.0, 92.0, 91.0),
            pressures_bar=(1.8, 1.8, 1.8, 1.8),
            wear_pct=(20.0, 22.0, 18.0, 19.0),
            life_pct=(80.0, 78.0, 82.0, 81.0),
            wear_rate_per_lap=2.5,
            estimated_laps_remaining=24.0,
            is_overheating=False,
            is_cold=False,
            overheated_tires=[],
            cold_tires=[],
            balance_note=None,
            is_gone_off=False,
            is_very_worn=False,
            is_critical_wear=False
        )

        gaps = GapAnalysis(
            car_ahead=None,
            car_behind=None,
            leader_name="Verstappen",
            gap_to_leader=None,
            in_battle=False,
            battle_target=None
        )

        context_str = build_telemetry_context(None, fuel, tires, gaps)
        self.assertIn("NO TELEMETRY AVAILABLE", context_str)

    def test_lap_history_formatting(self):
        from engineer.context import format_lap_history
        lap_times = [95.0, 93.5, 92.8, 92.0]
        history_str = format_lap_history(lap_times, 92.0, 92.0)

        self.assertIn("[YOUR TIMING & LAP HISTORY]", history_str)
        self.assertIn("Lap 1: 1:35.000", history_str)
        self.assertIn("Lap 2: 1:33.500 (-1.50s)", history_str)
        self.assertIn("Lap 4: 1:32.000 (-0.80s (PB))", history_str)
        self.assertIn("Improving pace", history_str)


if __name__ == "__main__":
    unittest.main()
