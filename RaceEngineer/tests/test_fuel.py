"""
Unit tests for FuelTracker.
"""

import unittest
from race.fuel import FuelTracker


class TestFuelTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = FuelTracker()

    def test_default_consumption(self):
        self.assertEqual(self.tracker.get_avg_consumption(), 3.5)

    def test_lap_consumption_calculation(self):
        # Start Lap 1 with 50.0L
        self.tracker.on_lap_started(1, 50.0)
        # Start Lap 2 with 46.5L (3.5L burned in Lap 1 - MUST BE IGNORED)
        self.tracker.on_lap_started(2, 46.5)
        self.assertEqual(len(self.tracker._lap_consumptions), 0)
        self.assertAlmostEqual(self.tracker.get_avg_consumption(), 3.5, places=1)  # Nominal baseline

        # Start Lap 3 with 43.1L (3.4L burned in Lap 2 - first counted lap)
        self.tracker.on_lap_started(3, 43.1)
        self.assertEqual(len(self.tracker._lap_consumptions), 1)
        self.assertAlmostEqual(self.tracker.get_avg_consumption(), 3.4, places=2)

        # Start Lap 4 with 39.9L (3.2L burned in Lap 3 - second counted lap)
        self.tracker.on_lap_started(4, 39.9)
        self.assertEqual(len(self.tracker._lap_consumptions), 2)
        self.assertAlmostEqual(self.tracker.get_avg_consumption(), 3.3, places=2)  # (3.4 + 3.2)/2 = 3.3

        # Start Lap 5 with 36.9L (3.0L burned in Lap 4 - third counted lap)
        self.tracker.on_lap_started(5, 36.9)
        self.assertEqual(len(self.tracker._lap_consumptions), 3)
        self.assertAlmostEqual(self.tracker.get_avg_consumption(), 3.2, places=2)  # (3.4 + 3.2 + 3.0)/3 = 3.2

        # Start Lap 6 with 34.1L (2.8L burned in Lap 5 - 4th counted lap, oldest 3.4 dropped)
        self.tracker.on_lap_started(6, 34.1)
        self.assertEqual(len(self.tracker._lap_consumptions), 3)  # Running 3-lap window
        self.assertAlmostEqual(self.tracker.get_avg_consumption(), 3.0, places=2)  # (3.2 + 3.0 + 2.8)/3 = 3.0

    def test_fuel_analysis_deficit(self):
        # 10L left, consuming 2.0L/lap, 10 laps remaining in race
        self.tracker._lap_consumptions = [2.0, 2.0, 2.0]
        status = self.tracker.analyze(current_liters=10.0, capacity_liters=60.0, total_laps=15, current_lap=6)

        self.assertAlmostEqual(status.laps_remaining_fuel, 5.0, places=1)
        self.assertEqual(status.laps_remaining_race, 10)
        self.assertTrue(status.needs_pit_stop)
        self.assertAlmostEqual(status.fuel_deficit_laps, 5.0, places=1)

    def test_fuel_critical_alert(self):
        self.tracker._lap_consumptions = [3.0]
        # Only 4.0L left (1.33 laps) -> is_critical should be True
        status = self.tracker.analyze(current_liters=4.0, capacity_liters=60.0, total_laps=10, current_lap=5)
        self.assertTrue(status.is_critical)
        self.assertFalse(status.is_empty_risk)

        # 2.0L left (0.66 laps) -> is_empty_risk should be True
        status = self.tracker.analyze(current_liters=2.0, capacity_liters=60.0, total_laps=10, current_lap=5)
        self.assertTrue(status.is_empty_risk)

    def test_timed_race_fuel_calculation(self):
        # 1200 seconds (20 mins) left, lap pace is 100 seconds -> ~13 laps remaining
        # Fuel burn: 3.0L / lap, Current fuel: 24.0L (8 laps range)
        self.tracker._lap_consumptions = [3.0, 3.0, 3.0]
        status = self.tracker.analyze(
            current_liters=24.0,
            capacity_liters=60.0,
            total_laps=0,
            current_lap=5,
            event_time_remaining_sec=1200.0,
            lap_pace_sec=100.0
        )

        self.assertAlmostEqual(status.laps_remaining_fuel, 8.0, places=1)
        self.assertAlmostEqual(status.laps_remaining_race, 13.0, places=1)
        self.assertTrue(status.needs_pit_stop)
        self.assertAlmostEqual(status.fuel_deficit_laps, 5.0, places=1)
        self.assertEqual(status.fuel_status_label, "PIT STOP REQUIRED")
        self.assertTrue(status.recommended_refuel_liters > 15.0)

    def test_tight_fuel_surplus_no_pit_recommended(self):
        # 1.0 lap remaining in race, consuming 3.0L/lap, current fuel: 3.9L (+0.3 laps surplus)
        self.tracker._lap_consumptions = [3.0, 3.0, 3.0]
        status = self.tracker.analyze(
            current_liters=3.9,
            capacity_liters=60.0,
            total_laps=10,
            current_lap=10
        )

        self.assertAlmostEqual(status.laps_remaining_fuel, 1.3, places=1)
        self.assertEqual(status.laps_remaining_race, 1)
        self.assertFalse(status.needs_pit_stop)
        self.assertEqual(status.recommended_refuel_liters, 0.0)
        self.assertEqual(status.fuel_required_liters, 0.0)
        self.assertIn("NO PIT REQUIRED", status.fuel_status_label)
        self.assertIn("TIGHT MARGIN", status.fuel_status_label)

    def test_fuel_urgency_calculation(self):
        self.tracker._lap_consumptions = [3.0, 3.0, 3.0]
        # Case A: On the final lap (lap 10 of 10), 3.3L left (1.1 laps range).
        # laps_remaining_race = 1.0. Driver has enough fuel to finish -> is_urgent MUST be False.
        status_safe = self.tracker.analyze(
            current_liters=3.3,
            capacity_liters=60.0,
            total_laps=10,
            current_lap=10
        )
        self.assertTrue(status_safe.is_empty_risk)
        self.assertFalse(status_safe.is_urgent)

        # Case B: On lap 8 of 10 (3 laps remaining in race), 3.0L left (1.0 lap range).
        # Driver will run out before finishing -> is_urgent MUST be True.
        status_urgent = self.tracker.analyze(
            current_liters=3.0,
            capacity_liters=60.0,
            total_laps=10,
            current_lap=8
        )
        self.assertTrue(status_urgent.is_empty_risk)
        self.assertTrue(status_urgent.is_urgent)


if __name__ == "__main__":
    unittest.main()
