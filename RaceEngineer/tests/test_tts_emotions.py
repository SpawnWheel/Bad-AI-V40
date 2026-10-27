"""
Unit tests for TTS emotional steering and British persona prompts.
"""

import unittest
from voice.tts import GeminiTTS
from engineer.brain import RaceEngineerBrain
from race.events import RaceEvent, EventType, EventPriority


class TestTTSEmotions(unittest.TestCase):
    def test_synthesis_prompt_steering(self):
        tts = GeminiTTS(
            voice_name="Puck",
            accent="British",
            gender="male",
            delivery="casual, witty, relaxed, and dry"
        )
        prompt = tts.build_synthesis_prompt("Box this lap for fuel.", emotion="alert, sharp, and attentive")

        self.assertIn("British", prompt)
        self.assertIn("male", prompt)
        self.assertIn("alert, sharp, and attentive", prompt)
        self.assertIn("casual, witty, relaxed, and dry", prompt)
        self.assertIn("Box this lap for fuel.", prompt)

    def test_brain_event_emotion_mapping(self):
        # Yellow flag -> alert
        yellow_event = RaceEvent(
            event_type=EventType.FLAG_YELLOW,
            priority=EventPriority.CRITICAL,
            timestamp=0.0,
            message="Yellow flag",
            details={}
        )
        emotion = RaceEngineerBrain.get_event_emotion(yellow_event)
        self.assertIn("alert", emotion)

        # Chequered flag -> pleased / dry humor / relaxed
        chequered_event = RaceEvent(
            event_type=EventType.FLAG_CHEQUERED,
            priority=EventPriority.CRITICAL,
            timestamp=0.0,
            message="Chequered flag",
            details={}
        )
        emotion = RaceEngineerBrain.get_event_emotion(chequered_event)
        self.assertIn("relaxed", emotion)

        # Routine lap completed -> relaxed, witty, and calm
        lap_event = RaceEvent(
            event_type=EventType.LAP_COMPLETED,
            priority=EventPriority.MEDIUM,
            timestamp=0.0,
            message="Lap 5 completed",
            details={}
        )
        emotion = RaceEngineerBrain.get_event_emotion(lap_event)
        self.assertIn("relaxed", emotion)

    def test_canonical_director_prompt_with_delivery_and_tags(self):
        tts = GeminiTTS(
            voice_name="Puck",
            accent="British",
            gender="male",
            delivery="casual, witty, relaxed, and dry"
        )
        prompt = tts.build_synthesis_prompt(
            text="[urgent] Yellow flag, sector two! Watch for debris.",
            emotion="urgent and alert",
            delivery="tense rapid-fire radio call"
        )

        self.assertIn("#### TRANSCRIPT", prompt)
        self.assertIn("[urgent] Yellow flag, sector two! Watch for debris.", prompt)
        self.assertIn("tense rapid-fire radio call", prompt)
        self.assertIn("urgent and alert", prompt)
        self.assertIn("British", prompt)

    def test_llm_response_parsing_all_fields(self):
        sample = (
            "TRANSCRIPT: What is my gap to Norris?\n"
            "EMOTION: focused\n"
            "DELIVERY: crisp and direct over radio\n"
            "RESPONSE: [calm] Gap to Norris is two point four seconds, closing fast."
        )
        transcript, emotion, delivery, response = RaceEngineerBrain._parse_llm_response(sample)
        self.assertEqual(transcript, "What is my gap to Norris?")
        self.assertEqual(emotion, "focused")
        self.assertEqual(delivery, "crisp and direct over radio")
        self.assertEqual(response, "[calm] Gap to Norris is two point four seconds, closing fast.")

    def test_llm_response_parsing_fallback(self):
        sample_plain = "Yellow flag turn 3, slow down mate."
        transcript, emotion, delivery, response = RaceEngineerBrain._parse_llm_response(sample_plain)
        self.assertEqual(transcript, "")
        self.assertEqual(emotion, "")
        self.assertEqual(delivery, "")
        self.assertEqual(response, "Yellow flag turn 3, slow down mate.")

    def test_thinking_level_resolution(self):
        brain_high = RaceEngineerBrain(thinking_level="HIGH")
        self.assertEqual(brain_high.thinking_level, "HIGH")

        brain_budget_high = RaceEngineerBrain(thinking_level=None, thinking_budget=4000)
        self.assertEqual(brain_budget_high.thinking_level, "HIGH")

        brain_budget_low = RaceEngineerBrain(thinking_level=None, thinking_budget=1000)
        self.assertEqual(brain_budget_low.thinking_level, "LOW")

    def test_multi_turn_conversation_construction(self):
        brain = RaceEngineerBrain()
        brain.history.append({"speaker": "Driver", "text": "Box this lap?"})
        brain.history.append({"speaker": "Engineer", "text": "Copy, boxing this lap."})

        from google.genai import types
        current_parts = [types.Part.from_text(text="Current Telemetry: Lap 10")]
        contents = brain._build_conversation_contents(current_parts)

        # Should have 3 turns: user, model, user
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0].role, "user")
        self.assertIn("Box this lap?", contents[0].parts[0].text)
        self.assertEqual(contents[1].role, "model")
        self.assertIn("Copy, boxing this lap.", contents[1].parts[0].text)
        self.assertEqual(contents[2].role, "user")
        self.assertIn("Current Telemetry: Lap 10", contents[2].parts[0].text)


if __name__ == "__main__":
    unittest.main()
