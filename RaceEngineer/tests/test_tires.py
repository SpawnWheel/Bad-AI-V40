"""
Unit tests for TireTracker.
"""

import unittest
from race.tires import TireTracker


class TestTireTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = TireTracker()

    def test_overheating_detection(self):
        # Front Left overheating at 112°C
        status = self.tracker.analyze(
            compound="Soft",
            tread_temps=(112.0, 95.0, 92.0, 90.0),
            pressures=(1.8, 1.8, 1.8, 1.8),
            wear=(10.0, 10.0, 8.0, 8.0)
        )
        self.assertTrue(status.is_overheating)
        self.assertIn("FL", status.overheated_tires)

    def test_balance_understeer_detection(self):
        # Front tires running significantly hotter than rears
        status = self.tracker.analyze(
            compound="Medium",
            tread_temps=(105.0, 104.0, 85.0, 86.0),
            pressures=(1.8, 1.8, 1.8, 1.8),
            wear=(20.0, 20.0, 15.0, 15.0)
        )
        self.assertIsNotNone(status.balance_note)
        self.assertIn("Front tires running hot", status.balance_note)

    def test_critical_wear_alert(self):
        # Wear at 78% on RR tire
        status = self.tracker.analyze(
            compound="Hard",
            tread_temps=(90.0, 90.0, 92.0, 93.0),
            pressures=(1.9, 1.9, 1.9, 1.9),
            wear=(40.0, 42.0, 45.0, 78.0)
        )
        self.assertTrue(status.is_critical_wear)


if __name__ == "__main__":
    unittest.main()
