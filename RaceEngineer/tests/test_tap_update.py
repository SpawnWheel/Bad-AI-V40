"""
Unit tests for Dual-Action PTT button (Short Tap for Update vs Long Hold to Speak).
"""

import unittest
import time
from voice.ptt import PushToTalkManager
from engineer.brain import RaceEngineerBrain


class TestDualActionButton(unittest.TestCase):
    def test_tap_vs_hold_callback_logic(self):
        tap_called = []
        audio_called = []

        ptt = PushToTalkManager(
            mode="keyboard",
            hold_threshold_sec=0.20,  # 200ms threshold for fast test
            beep_feedback=False,
            on_tap=lambda: tap_called.append(True),
            on_audio_ready=lambda wav: audio_called.append(wav)
        )

        # 1. Simulate short tap (<200ms)
        ptt._on_press()
        time.sleep(0.05)  # 50ms hold
        ptt._on_release()
        time.sleep(0.05)

        self.assertTrue(len(tap_called) > 0, "Short tap should trigger on_tap callback")
        self.assertEqual(len(audio_called), 0, "Short tap should NOT trigger on_audio_ready")

        # 2. Simulate long hold (>200ms)
        tap_called.clear()
        ptt._on_press()
        time.sleep(0.25)  # 250ms hold
        ptt._on_release()
        time.sleep(0.05)

        self.assertEqual(len(tap_called), 0, "Long hold should NOT trigger on_tap")

    def test_generate_race_update_structure(self):
        brain = RaceEngineerBrain()
        update_text, emotion, delivery = brain.generate_race_update(
            "Track: Silverstone | P3 | Gap ahead: +2.1s | Fuel: 15L (5 laps) | Tires: 92°C"
        )
        self.assertIsNotNone(update_text)
        self.assertIn("informative", emotion)
        self.assertIsNotNone(delivery)


if __name__ == "__main__":
    unittest.main()
