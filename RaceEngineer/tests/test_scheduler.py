"""
Unit tests for MessageScheduler.
"""

import unittest
from engineer.scheduler import MessageScheduler
from race.events import EventPriority


class TestMessageScheduler(unittest.TestCase):
    def setUp(self):
        self.scheduler = MessageScheduler(quiet_zone_braking=True, min_interval_sec=1.0)

    def test_priority_ordering(self):
        # Push Medium then Critical then High
        self.scheduler.push("Medium message", EventPriority.MEDIUM)
        self.scheduler.push("Critical alert!", EventPriority.CRITICAL)
        self.scheduler.push("High alert", EventPriority.HIGH)

        # Critical must pop first
        msg1 = self.scheduler.poll_next_message(None)
        self.assertEqual(msg1.event_priority, EventPriority.CRITICAL)
        self.assertEqual(msg1.text, "Critical alert!")

        # High must pop second (with min interval bypassed for test if simulated)
        self.scheduler._last_speech_time = 0.0
        msg2 = self.scheduler.poll_next_message(None)
        self.assertEqual(msg2.event_priority, EventPriority.HIGH)


if __name__ == "__main__":
    unittest.main()
