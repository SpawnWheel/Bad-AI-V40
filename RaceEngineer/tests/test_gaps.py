"""
Unit tests for GapTracker.
"""

import unittest
from race.gaps import GapTracker
from telemetry.collector import ParticipantSnapshot


class TestGapTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = GapTracker()

    def test_battle_detection(self):
        participants = (
            ParticipantSnapshot("Hamilton", 1, 5, 2, 2500.0, True),
            ParticipantSnapshot("Driver", 2, 5, 2, 2450.0, True),
            ParticipantSnapshot("Leclerc", 3, 5, 2, 2400.0, True),
        )

        # Gap to P1 ahead is 0.8s (under 1.5s battle threshold)
        analysis = self.tracker.analyze(
            player_pos=2,
            split_ahead=0.8,
            split_behind=3.5,
            participants=participants
        )

        self.assertTrue(analysis.in_battle)
        self.assertIsNotNone(analysis.car_ahead)
        self.assertEqual(analysis.car_ahead.name, "Hamilton")
        self.assertIn("Hamilton", analysis.battle_target)

    def test_trend_closing_detection(self):
        participants = (
            ParticipantSnapshot("Verstappen", 1, 5, 2, 2500.0, True),
            ParticipantSnapshot("Driver", 2, 5, 2, 2400.0, True),
        )

        # Gap was 2.5s, then 2.0s, now 1.6s
        self.tracker.analyze(2, 2.5, -1, participants)
        self.tracker.analyze(2, 2.0, -1, participants)
        analysis = self.tracker.analyze(2, 1.6, -1, participants)

        self.assertEqual(analysis.car_ahead.trend, "closing")

    def test_tailgating_close_battle_suppresses_seconds(self):
        participants = (
            ParticipantSnapshot("Hamilton", 1, 5, 2, 2500.0, True),
            ParticipantSnapshot("Driver", 2, 5, 2, 2480.0, True),
        )

        # Gap to P1 ahead is 0.3s (< 0.6s tailgating threshold)
        analysis = self.tracker.analyze(
            player_pos=2,
            split_ahead=0.3,
            split_behind=-1,
            participants=participants
        )

        self.assertTrue(analysis.in_battle)
        self.assertIn("Right on the gearbox of P1 (Hamilton)", analysis.battle_target)
        self.assertNotIn("0.3s ahead", analysis.battle_target)


if __name__ == "__main__":
    unittest.main()
