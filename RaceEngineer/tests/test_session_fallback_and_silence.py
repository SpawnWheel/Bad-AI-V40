"""
Unit tests verifying:
1. TTS quota exhaustion (429 / RESOURCE_EXHAUSTED) session fallback to gemini-2.5-flash
2. TTS reset_session restores primary model
3. In-lap complete radio silence after chequered flag
4. Timed race line-crossing final lap logic
"""

import unittest
from unittest.mock import MagicMock
import numpy as np
import time

from voice.tts import GeminiTTS
from race.state import RaceStateManager
from race.events import EventType, RaceEvent
from telemetry.collector import TelemetrySnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode


class TestSessionFallbackAndSilence(unittest.TestCase):

    def test_tts_quota_exhaustion_and_session_fallback(self):
        """
        Verify that hitting a 429 quota error on gemini-3.8-flash-tts causes GeminiTTS to fall back
        to gemini-3.8-flash-lite-tts for the rest of the session, and reset_session() restores the primary.
        """
        import base64, io, wave
        tts = GeminiTTS(api_key="fake-key", model_name="gemini-3.8-flash-tts", voice_name="Charon")

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
            wf.writeframes(np.ones(2400, dtype=np.int16).tobytes())
        wav_b64 = base64.b64encode(buf.getvalue()).decode()

        ok = MagicMock()
        ok.output_audio.data = wav_b64

        def fake_create(**kwargs):
            if kwargs["model"] == "gemini-3.8-flash-tts":
                raise Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
            if kwargs["model"] == "gemini-3.8-flash-lite-tts":
                return ok
            raise ValueError(f"Unexpected model: {kwargs['model']}")

        mock_client = MagicMock()
        mock_client.interactions.create.side_effect = fake_create
        tts.client = mock_client

        audio = tts.synthesize("Radio check, sector two clear.")
        self.assertIsNotNone(audio)
        self.assertEqual(len(audio), 2400)
        self.assertTrue(tts._quota_exceeded_for_session)
        self.assertEqual(tts._current_model, "gemini-3.8-flash-lite-tts")

        mock_client.interactions.create.reset_mock()
        audio2 = tts.synthesize("Box this lap.")
        self.assertIsNotNone(audio2)
        self.assertEqual(mock_client.interactions.create.call_count, 1)
        self.assertEqual(mock_client.interactions.create.call_args.kwargs["model"], "gemini-3.8-flash-lite-tts")

        tts.reset_session()
        self.assertFalse(tts._quota_exceeded_for_session)
        self.assertEqual(tts._current_model, "gemini-3.8-flash-tts")

    def make_snapshot(
        self,
        lap=1,
        pos=1,
        flag=FlagColour.FLAG_GREEN,
        total_laps=0,
        time_rem=300.0,
        fuel=20.0,
        race_state=RaceState.RACESTATE_RACING,
        session_state=SessionState.SESSION_RACE,
        is_finished=False
    ):
        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=session_state,
            race_state=race_state,
            car_name="Porsche 911 GT3",
            car_class_name="GT3",
            speed_kmh=150.0,
            rpm=6000.0,
            max_rpm=8500.0,
            gear=3,
            throttle=0.8,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Silverstone",
            track_variation="Grand Prix",
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=total_laps,
            lap_invalidated=False,
            player_name="Driver",
            position=pos,
            current_lap=lap,
            laps_completed=max(0, lap - 1),
            current_sector=2,
            current_lap_time=45.0,
            last_lap_time=115.0,
            best_lap_time=114.0,
            split_time_ahead=0.0,
            split_time_behind=5.0,
            sector1_time=35.0,
            sector2_time=40.0,
            sector3_time=40.0,
            best_sector1_time=35.0,
            best_sector2_time=40.0,
            best_sector3_time=39.0,
            fuel_level_pct=fuel,
            fuel_liters=fuel,
            fuel_capacity_liters=100.0,
            water_temp_c=90.0,
            oil_temp_c=100.0,
            oil_pressure_kpa=400.0,
            tyre_compound="Soft",
            tyre_tread_temps_c=(95.0, 95.0, 92.0, 92.0),
            tyre_carcass_temps_c=(92.0, 92.0, 90.0, 90.0),
            tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
            tyre_wear_pct=(15.0, 15.0, 12.0, 12.0),
            brake_temps_c=(400.0, 400.0, 380.0, 380.0),
            ambient_temp_c=24.0,
            track_temp_c=32.0,
            rain_density=0.0,
            wind_speed_ms=2.0,
            flag_colour=flag,
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            event_time_remaining_sec=time_rem,
            session_duration_min=15,
            is_race_finished=is_finished,
            participants=()
        )

    def test_post_chequered_flag_radio_silence(self):
        """
        Verify that once the chequered flag is shown, FLAG_CHEQUERED is emitted once,
        and all subsequent proactive alerts (fuel emergency, yellow flags, rival incidents)
        are completely silenced on the in-lap.
        """
        manager = RaceStateManager()
        fired_events = []
        manager.register_event_listener(lambda ev: fired_events.append(ev))

        # Baseline snapshot
        snap1 = self.make_snapshot(lap=10, fuel=5.0)
        manager.on_telemetry(snap1)

        # Chequered flag drops
        snap_chequered = self.make_snapshot(
            lap=10,
            fuel=4.5,
            flag=FlagColour.FLAG_CHEQUERED,
            is_finished=True
        )
        manager.on_telemetry(snap_chequered)

        chequered_events = [e for e in fired_events if e.event_type == EventType.FLAG_CHEQUERED]
        self.assertEqual(len(chequered_events), 1)

        # In-lap: fuel drops below 1 lap (0.8L = empty risk) and yellow flag appears
        snap_inlap = self.make_snapshot(
            lap=10,
            fuel=0.8,
            flag=FlagColour.FLAG_YELLOW,
            is_finished=True
        )
        count_before = len(fired_events)
        manager.on_telemetry(snap_inlap)
        count_after = len(fired_events)

        # Absolutely NO new proactive events should be emitted!
        self.assertEqual(count_before, count_after)
        event_types = [e.event_type for e in fired_events]
        self.assertNotIn(EventType.FUEL_EMERGENCY, event_types)
        self.assertNotIn(EventType.FLAG_YELLOW, event_types)

    def test_timed_race_final_lap_line_crossing(self):
        """
        Verify that in a timed race, final lap does not trigger mid-lap when the clock expires,
        but triggers when crossing the start/finish line into the next lap.
        """
        manager = RaceStateManager()
        fired_events = []
        manager.register_event_listener(lambda ev: fired_events.append(ev))

        # Baseline snapshot with clock running
        manager.on_telemetry(self.make_snapshot(lap=5, time_rem=30.0, total_laps=0))

        # Clock expires while car is still on Lap 5 (time = 0.0)
        manager.on_telemetry(self.make_snapshot(lap=5, time_rem=0.0, total_laps=0))
        self.assertNotIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])

        # Next frame on Lap 5: still in middle of lap
        manager.on_telemetry(self.make_snapshot(lap=5, time_rem=0.0, total_laps=0))
        self.assertNotIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])

        # Crosses start/finish line into Lap 6! Final lap must trigger now
        manager.on_telemetry(self.make_snapshot(lap=6, time_rem=0.0, total_laps=0))
        self.assertIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])


if __name__ == "__main__":
    unittest.main()
