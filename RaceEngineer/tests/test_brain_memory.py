"""
Unit tests for RaceEngineerBrain multi-turn conversation memory,
strict role alternation, and dialogue history formatting.
"""

import unittest
from google.genai import types
from engineer.brain import RaceEngineerBrain


class TestBrainMemory(unittest.TestCase):
    def setUp(self):
        self.brain = RaceEngineerBrain(api_key=None)

    def test_build_conversation_contents_empty_history(self):
        current = [types.Part.from_text(text="Active Turn Question")]
        contents = self.brain._build_conversation_contents(current)

        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].role, "user")
        self.assertEqual(contents[0].parts, current)

    def test_build_conversation_contents_starts_with_engineer(self):
        # Scenario: Proactive event advice happened first without a preceding driver question
        self.brain.history.append({"speaker": "Engineer", "text": "Yellow flag Turn 4, stay left."})

        current = [types.Part.from_text(text="Driver asks pace")]
        contents = self.brain._build_conversation_contents(current)

        # Must start with user, alternate user -> model -> user
        roles = [c.role for c in contents]
        self.assertEqual(roles, ["user", "model", "user"])
        self.assertIn("telemetry alert", contents[0].parts[0].text.lower())
        self.assertIn("Yellow flag Turn 4", contents[1].parts[0].text)
        self.assertEqual(contents[2].parts, current)

    def test_build_conversation_contents_consecutive_engineer_calls(self):
        # Scenario: Two proactive events happened in a row
        self.brain.history.append({"speaker": "Radio Alert", "text": "Yellow flag Sector 2"})
        self.brain.history.append({"speaker": "Engineer", "text": "Yellow flag Sector 2, car stopped on left."})
        self.brain.history.append({"speaker": "Engineer", "text": "Track is green, sector 2 clear."})

        current = [types.Part.from_text(text="How is my gap to P2?")]
        contents = self.brain._build_conversation_contents(current)

        roles = [c.role for c in contents]
        # Verify strict alternation: no two consecutive identical roles
        for i in range(len(roles) - 1):
            self.assertNotEqual(roles[i], roles[i + 1], f"Consecutive roles found at index {i}: {roles}")

        self.assertEqual(roles[0], "user")
        self.assertEqual(roles[-1], "user")

    def test_build_conversation_contents_normal_dialogue(self):
        self.brain.history.append({"speaker": "Driver", "text": "What is the gap ahead?"})
        self.brain.history.append({"speaker": "Engineer", "text": "Gap ahead is 2.4 seconds."})

        current = [types.Part.from_text(text="How are my lap times to before?")]
        contents = self.brain._build_conversation_contents(current)

        roles = [c.role for c in contents]
        self.assertEqual(roles, ["user", "model", "user"])
        self.assertEqual(contents[0].role, "user")
        self.assertEqual(contents[1].role, "model")
        self.assertEqual(contents[2].role, "user")

    def test_format_history(self):
        self.brain.history.append({"speaker": "Driver", "text": "What is my fuel?"})
        self.brain.history.append({"speaker": "Engineer", "text": "Fuel safe to end, plus 1.2 laps."})

        formatted = self.brain._format_history()
        self.assertIn("[RECENT RADIO & PIT-WALL LOG - LAST EXCHANGES]:", formatted)
        self.assertIn("Driver: \"What is my fuel?\"", formatted)
        self.assertIn("Engineer: \"Fuel safe to end, plus 1.2 laps.\"", formatted)


if __name__ == "__main__":
    unittest.main()
