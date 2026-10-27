"""
Tests for the reworked LLM usage in RaceEngineerBrain: structured output, notebook,
session-scoped prompts, no duplicated history, fast/fallback models and token accounting.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from engineer.brain import RaceEngineerBrain, RESPONSE_SCHEMA
from race.events import RaceEvent, EventType, EventPriority


def _resp(payload, prompt=1000, cached=0, thoughts=50, out=40):
    return SimpleNamespace(
        text=json.dumps(payload) if isinstance(payload, dict) else payload,
        usage_metadata=SimpleNamespace(prompt_token_count=prompt, cached_content_token_count=cached,
                                       thoughts_token_count=thoughts, candidates_token_count=out),
    )


def _brain(**kw):
    b = RaceEngineerBrain(api_key=None, thinking_level="LOW", **kw)
    b.client = MagicMock()
    return b


class TestBrainLLMUsage(unittest.TestCase):
    def test_structured_output_parsed_and_config_sent(self):
        b = _brain()
        b.client.models.generate_content.return_value = _resp({
            "intent": "gaps", "emotion": "quietly confident", "delivery": "brisk",
            "response": "Gap to Payne is one point two, closing.", "remember": "",
        })
        text, emo, dlv = b.respond_to_driver_text("What's the gap ahead?", "[SESSION] RACE")
        self.assertEqual(text, "Gap to Payne is one point two, closing.")
        self.assertEqual(emo, "quietly confident")
        cfg = b.client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(cfg.response_mime_type, "application/json")
        self.assertEqual(cfg.response_json_schema, RESPONSE_SCHEMA)
        self.assertEqual(str(cfg.thinking_config.thinking_level).upper().split(".")[-1], "LOW")
        self.assertEqual(cfg.temperature, 1.0)

    def test_text_format_fallback_when_json_invalid(self):
        b = _brain()
        b.client.models.generate_content.return_value = _resp(
            "EMOTION: tense\nDELIVERY: clipped\nRESPONSE: Box this lap.")
        text, emo, _ = b.respond_to_driver_text("Box?", "ctx")
        self.assertEqual(text, "Box this lap.")
        self.assertEqual(emo, "tense")

    def test_notebook_remember_and_injected_next_call(self):
        b = _brain()
        b.set_session_group("PRACTICE")
        b.client.models.generate_content.return_value = _resp({
            "intent": "car_feedback", "emotion": "curious", "delivery": "relaxed",
            "response": "Copy, understeer into Turn 3. We'll look at front pressures.",
            "remember": "Driver reports mid-corner understeer at Turn 3",
        })
        b.respond_to_driver_text("Car understeers in turn 3", "ctx")
        self.assertEqual(len(b.notebook), 1)
        self.assertIn("Turn 3", b.notebook[0])

        b.respond_to_driver_text("Anything else?", "ctx")
        sent = b.client.models.generate_content.call_args.kwargs["contents"][-1].parts[-1].text
        self.assertIn("ENGINEER'S NOTEBOOK", sent)
        self.assertIn("understeer at Turn 3", sent)

        # duplicates are not re-added; notebook partly survives a session reset
        b.remember("Driver reports mid-corner understeer at Turn 3")
        self.assertEqual(len(b.notebook), 1)
        b.reset_session()
        self.assertEqual(len(b.notebook), 1)

    def test_history_sent_once_as_turns_not_in_prompt_text(self):
        b = _brain()
        b.history.append({"speaker": "Driver", "text": "How is the fuel looking?"})
        b.history.append({"speaker": "Engineer", "text": "Fuel is fine to the flag."})
        b.client.models.generate_content.return_value = _resp({
            "intent": "tyres", "emotion": "calm", "delivery": "even", "response": "Tyres are fine."})
        b.respond_to_driver_text("And tyres?", "ctx")
        contents = b.client.models.generate_content.call_args.kwargs["contents"]
        prompt_text = contents[-1].parts[-1].text
        self.assertNotIn("Fuel is fine to the flag", prompt_text)
        self.assertTrue(any("Fuel is fine to the flag" in p.text for c in contents[:-1] for p in c.parts))

    def test_acknowledgement_gets_no_reply(self):
        b = _brain()
        b.client.models.generate_content.return_value = _resp({
            "transcript": "Copy that", "intent": "acknowledgement", "emotion": "calm",
            "delivery": "even", "response": ""})
        text, _, _ = b.respond_to_driver_audio(b"RIFF....", "ctx")
        self.assertEqual(text, "")

    def test_session_scoped_system_prompt(self):
        b = _brain()
        full = len(b.system_prompt)
        b.set_session_group("RACE")
        self.assertLess(len(b.system_prompt), full * 0.6)
        self.assertNotIn("SETUP REFERENCE MATERIAL", b.system_prompt)
        self.assertNotIn("QUALIFYING MODE", b.system_prompt)
        b.set_session_group("PRACTICE")
        self.assertIn("SETUP REFERENCE MATERIAL", b.system_prompt)

    def test_fast_model_for_routine_events_and_fallback_on_quota(self):
        b = _brain(fast_model="gemini-3.5-flash-lite", fast_thinking_level="MINIMAL",
                   fallback_model="gemini-3.5-flash")
        ok = _resp({"intent": "alert", "emotion": "brisk", "delivery": "quick", "response": "Five minutes left."})
        b.client.models.generate_content.return_value = ok
        ev = RaceEvent(EventType.TIME_WARNING_5MIN, EventPriority.HIGH, 0.0, "5 minutes remaining")
        b.generate_event_advice(ev, "ctx")
        self.assertEqual(b.client.models.generate_content.call_args.kwargs["model"], "gemini-3.5-flash-lite")

        # Non-routine event on main model; 429 -> fallback model
        def side(model, contents, config):
            if model == "gemini-3.8-flash":
                raise Exception("429 RESOURCE_EXHAUSTED")
            return ok
        b.client.models.generate_content.side_effect = side
        ev2 = RaceEvent(EventType.BOX_THIS_LAP, EventPriority.CRITICAL, 0.0, "Box this lap")
        text, _, _ = b.generate_event_advice(ev2, "ctx")
        self.assertEqual(text, "Five minutes left.")
        self.assertEqual(b.client.models.generate_content.call_args.kwargs["model"], "gemini-3.5-flash")

    def test_usage_accounting(self):
        b = _brain()
        b.client.models.generate_content.return_value = _resp(
            {"intent": "sitrep", "emotion": "calm", "delivery": "even", "response": "P5, all good."},
            prompt=3000, cached=2000, thoughts=100, out=30)
        b.generate_race_update("ctx", session_group="RACE")
        b.generate_race_update("ctx", session_group="RACE")
        self.assertEqual(b.usage_totals["calls"], 2)
        self.assertEqual(b.usage_totals["cached"], 4000)
        self.assertIn("2 calls", b._usage_summary())

    def test_laughter_stripped_unless_persona_allows(self):
        b = _brain()
        b.client.models.generate_content.return_value = _resp({
            "intent": "joke", "emotion": "laughing", "delivery": "light",
            "response": "<laugh> Good one. [chuckles]"})
        text, emo, _ = b.respond_to_driver_text("tell me a joke", "ctx")
        self.assertNotIn("<laugh>", text)
        self.assertNotIn("[chuckles]", text)
        self.assertNotIn("laugh", emo)


if __name__ == "__main__":
    unittest.main()
