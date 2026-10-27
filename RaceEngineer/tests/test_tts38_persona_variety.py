"""
Tests for the Gemini 3.8 TTS migration, config-driven personas, and the variety/mood engine.
"""

import base64
import io
import json
import random
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import yaml

from engineer.persona import load_persona, persona_from_dict
from engineer.variety import VarietyTracker
from engineer.brain import RaceEngineerBrain
from engineer.prompts import get_system_prompt
from voice.tts import GeminiTTS


def _wav_b64(n=1200, sr=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes((np.arange(n) % 100).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


class TestPersonaConfig(unittest.TestCase):
    def setUp(self):
        self.cfg = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))

    def test_every_preset_loads(self):
        for key in self.cfg["personas"]:
            cfg = dict(self.cfg, engineer=dict(self.cfg["engineer"], persona=key))
            p = load_persona(cfg)
            self.assertEqual(p.key, key)
            self.assertTrue(p.accent)
            self.assertIn(p.expressiveness, ("subtle", "natural", "expressive"))
            self.assertTrue(p.voice_design_prompt())

    def test_overrides_apply(self):
        cfg = dict(self.cfg, engineer=dict(self.cfg["engineer"], persona="aussie_laidback",
                                            persona_overrides={"address_terms": ["Daniel"], "expressiveness": "subtle"}))
        p = load_persona(cfg)
        self.assertEqual(p.address_terms, ["Daniel"])
        self.assertEqual(p.expressiveness, "subtle")
        self.assertEqual(p.language_code, "en-AU")

    def test_legacy_flat_config(self):
        legacy = {"engineer": {"personality": "Old school", "accent": "Scottish", "delivery": "gruff"},
                  "gemini": {"tts_voice": "Orus"}}
        p = load_persona(legacy)
        self.assertEqual(p.accent, "Scottish")
        self.assertEqual(p.baseline_delivery, "gruff")
        self.assertEqual(p.voice.mode, "prebuilt")
        self.assertEqual(p.voice.prebuilt, "Orus")

    def test_laughter_gate(self):
        p = persona_from_dict("x", {"allow_laughter": False, "vocal_events": ["sigh", "laugh", "chuckle"]})
        self.assertNotIn("laugh", p.allowed_vocal_events)
        self.assertNotIn("chuckle", p.allowed_vocal_events)
        p2 = persona_from_dict("y", {"allow_laughter": True, "vocal_events": ["sigh"]})
        self.assertIn("chuckle", p2.allowed_vocal_events)

    def test_system_prompt_uses_persona(self):
        cfg = dict(self.cfg, engineer=dict(self.cfg["engineer"], persona="italian_passionate"))
        p = load_persona(cfg)
        sp = get_system_prompt(persona=p, reference_material="")
        self.assertIn("Italian", sp)
        self.assertIn("<gasp>", sp)
        self.assertIn('"No worries"', sp)
        self.assertNotIn("{", sp.split("YOUR OPERATIONAL RULES")[0])


class TestGemini38TTS(unittest.TestCase):
    def _tts(self, **persona_kw):
        data = {"accent": "Australian", "gender": "male", "age": "mid 30s", "voice": {"mode": "prebuilt", "prebuilt": "Puck"}}
        data.update(persona_kw)
        p = persona_from_dict("t", data)
        return GeminiTTS(api_key="fake", persona=p)

    def test_transcript_sanitising(self):
        tts = self._tts()
        text, cues = tts.prepare_transcript("[urgent] Yellow flag, sector two! [sighs] Gap is 1.4s. <laugh> <cheer>")
        self.assertNotIn("[", text)
        self.assertIn("urgent", cues)
        self.assertIn("<sigh>", text)
        self.assertNotIn("<laugh>", text)  # laughter not allowed by default
        self.assertIn("1.4 seconds", text)

    def test_unknown_angle_tags_removed_and_events_collapsed(self):
        tts = self._tts()
        text, _ = tts.prepare_transcript("<sigh> <exhales> Box this lap. <dramatic> Box.")
        self.assertEqual(text.count("<"), 1)
        self.assertNotIn("dramatic", text)

    def test_style_contains_emotion_delivery_and_accent_for_prebuilt(self):
        tts = self._tts()
        tts.ensure_voice()
        style = tts.build_style("quietly pleased", "slow and warm", ["relieved"])
        self.assertIn("Quietly pleased", style)
        self.assertIn("relieved", style)
        self.assertIn("Slow and warm", style)
        self.assertIn("Australian", style)
        self.assertIn("team radio", style)

    def test_style_omits_accent_for_custom_voice(self):
        tts = self._tts(voice={"mode": "voice_id", "voice_id": "voice_abc123"})
        self.assertEqual(tts.ensure_voice(), "voice_abc123")
        self.assertNotIn("Australian", tts.build_style("tense", "clipped"))

    def test_interactions_request_shape(self):
        tts = self._tts(voice={"mode": "voice_id", "voice_id": "voice_abc123"})
        resp = MagicMock()
        resp.output_audio.data = _wav_b64(n=4800)
        tts.client = MagicMock()
        tts.client.interactions.create.return_value = resp

        audio = tts.synthesize("[calm] Box this lap, <short pause> box box.", emotion="clipped and tense", delivery="rapid-fire")
        self.assertIsNotNone(audio)
        self.assertEqual(len(audio), 4800)
        kw = tts.client.interactions.create.call_args.kwargs
        self.assertEqual(kw["model"], "gemini-3.8-flash-tts")
        content = kw["input"][0]["content"][0]
        self.assertNotIn("[calm]", content["text"])
        self.assertIn("<short pause>", content["text"])
        ann = content["annotations"][0]
        self.assertEqual(ann["type"], "speech_metadata")
        self.assertIn("Clipped and tense", ann["style"])
        self.assertIn("calm", ann["style"])
        self.assertEqual(kw["generation_config"]["speech_config"][0]["voice"], "voice_abc123")
        self.assertEqual(kw["response_format"]["type"], "audio")

    def test_resamples_non_24k_audio(self):
        audio = GeminiTTS._decode_audio(_wav_b64(n=1600, sr=16000))
        self.assertEqual(len(audio), 2400)

    def test_voice_design_is_cached(self):
        with tempfile.TemporaryDirectory() as d:
            p = persona_from_dict("gp", {"accent": "British", "voice": {"mode": "designed", "description": "A calm London man."}})
            tts = GeminiTTS(api_key="fake", persona=p,
                            voice_cache_path=f"{d}/cache.json", voice_samples_dir=f"{d}/samples")
            created = MagicMock()
            created.id = "voice_xyz"
            created.sample_audio.data = _wav_b64()
            tts.client = MagicMock()
            tts.client.voices.create.return_value = created

            self.assertEqual(tts.ensure_voice(), "voice_xyz")
            req = tts.client.voices.create.call_args.kwargs
            self.assertTrue(req["store"])
            self.assertEqual(req["voice"]["type"], "prompted")
            self.assertEqual(req["voice"]["prompted"]["input"], "A calm London man.")
            self.assertTrue(any(Path(d, "samples").iterdir()))

            # Second instance reads cache; no new API call
            tts2 = GeminiTTS(api_key="fake", persona=p, voice_cache_path=f"{d}/cache.json")
            tts2.client = MagicMock()
            self.assertEqual(tts2.ensure_voice(), "voice_xyz")
            tts2.client.voices.create.assert_not_called()

            # Changing the description designs a new voice
            p3 = persona_from_dict("gp", {"accent": "British", "voice": {"mode": "designed", "description": "A gravelly Mancunian."}})
            tts3 = GeminiTTS(api_key="fake", persona=p3, voice_cache_path=f"{d}/cache.json")
            tts3.client = MagicMock()
            tts3.client.voices.create.return_value = MagicMock(id="voice_new", sample_audio=None)
            self.assertEqual(tts3.ensure_voice(), "voice_new")
            self.assertEqual(len(json.loads(Path(d, "cache.json").read_text())), 2)

    def test_voice_design_failure_falls_back_to_prebuilt(self):
        with tempfile.TemporaryDirectory() as d:
            p = persona_from_dict("gp", {"voice": {"mode": "designed", "prebuilt": "Charon"}})
            tts = GeminiTTS(api_key="fake", persona=p, voice_cache_path=f"{d}/c.json")
            tts.client = MagicMock()
            tts.client.voices.create.side_effect = Exception("boom")
            self.assertEqual(tts.ensure_voice(), "Charon")
            self.assertIn("accent", tts.build_style("calm", "even"))


class TestVarietyEngine(unittest.TestCase):
    def test_openers_and_overused_phrases(self):
        v = VarietyTracker(address_terms=["mate"], vocabulary=["Simply lovely"], rng=random.Random(1))
        v.record("You're running P10 mate, keep your head down.", "focused")
        v.record("You're running P9 mate, keep your head down.", "focused")
        g = v.guidance()
        self.assertIn("you're running p10", g)
        self.assertIn('"keep your head down"', g)
        self.assertIn("Do NOT address the driver", g)
        self.assertIn("different, more specific shade than 'focused'", g)

    def test_mood_moves_with_events(self):
        v = VarietyTracker()
        self.assertIn("neutral", v.mood_description())
        v.on_event("position_gained"); v.on_event("position_gained")
        self.assertIn("buoyant", v.mood_description())
        v.on_event("collision_damage"); v.on_event("collision_damage"); v.on_event("collision_damage")
        self.assertIn("rough", v.mood_description())

    def test_brain_injects_variety_and_records(self):
        p = persona_from_dict("t", {"address_terms": ["buddy"], "vocabulary": ["Clear"]})
        brain = RaceEngineerBrain(api_key=None, persona=p)
        brain._remember_reply("Clear high, buddy.", "calm")
        hist = brain._format_history()
        self.assertIn("DELIVERY VARIETY", hist)
        brain.reset_session()
        self.assertEqual(len(brain.variety.recent_lines), 0)
        self.assertEqual(brain.temperature, 1.0)


if __name__ == "__main__":
    unittest.main()
