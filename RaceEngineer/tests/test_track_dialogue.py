"""
Unit tests for track arrival greeting and conversational follow-up.
"""

import unittest
from engineer.brain import RaceEngineerBrain
from race.events import RaceEvent, EventType, EventPriority


class TestTrackDialogue(unittest.TestCase):
    def test_track_loaded_emotion(self):
        event = RaceEvent(
            event_type=EventType.TRACK_LOADED,
            priority=EventPriority.HIGH,
            timestamp=0.0,
            message="We're here at Brands Hatch GP.",
            details={"track_name": "Brands Hatch"}
        )
        emotion = RaceEngineerBrain.get_event_emotion(event)
        self.assertIn("welcoming", emotion)

    def test_multi_turn_history_preservation(self):
        brain = RaceEngineerBrain()

        # Simulate engineer greeting stored in history
        brain.history.append({
            "speaker": "Engineer",
            "text": "We're here at Brands Hatch GP. Radio check loud and clear. Would you like a quick rundown on the circuit?"
        })

        # Format history string
        history_str = brain._format_history()
        self.assertIn("Brands Hatch GP", history_str)
        self.assertIn("rundown on the circuit", history_str)

        # Driver responds: "Yes please, what are the key corners?"
        brain.history.append({
            "speaker": "Driver",
            "text": "Yes please, what are the key corners?"
        })

        # History now preserves both turns
        updated_history = brain._format_history()
        self.assertIn("Engineer:", updated_history)
        self.assertIn("Driver:", updated_history)
        self.assertIn("key corners", updated_history)


if __name__ == "__main__":
    unittest.main()
