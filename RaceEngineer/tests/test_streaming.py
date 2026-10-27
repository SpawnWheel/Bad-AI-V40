"""
Tests for streaming mode: LLM text streaming -> sentence-aware segmentation -> TTS streaming
-> gap-free playback.
"""

import base64
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from engineer.brain import RaceEngineerBrain
from engineer.persona import persona_from_dict
from voice.stream import RadioTransmission, ReplyStreamSink, find_first_sentence_end
from voice.tts import GeminiTTS
from voice.player import AudioPlayer, PCMStream


# ---------------------------------------------------------------- helpers
class FakeTx:
    def __init__(self):
        self.spoken = []
        self.calls = []

    @property
    def started(self):
        return bool(self.spoken)

    def say(self, text, emotion, delivery):
        if text:
            self.spoken.append(text)
            self.calls.append((text, emotion, delivery))


def _chunks(s, n):
    return [s[i:i + n] for i in range(0, len(s), n)]


class TestSentenceSplitting(unittest.TestCase):
    def test_first_sentence_boundary(self):
        t = "Gap to Payne is one point two seconds. He's on old tyres, go get him."
        idx = find_first_sentence_end(t, 5)
        self.assertEqual(t[:idx].strip(), "Gap to Payne is one point two seconds.")

    def test_short_fragment_not_split(self):
        t = "Copy. Box this lap, box box."
        self.assertEqual(find_first_sentence_end(t, 5), -1)

    def test_decimals_and_lap_times_not_split(self):
        t = "Last lap was a 1:12.4, gap 1.2 seconds"
        self.assertEqual(find_first_sentence_end(t, 3), -1)

    def test_no_split_mid_sentence(self):
        self.assertEqual(find_first_sentence_end("You're P5 now and the gap behind is", 3), -1)


class TestReplyStreamSink(unittest.TestCase):
    def test_waits_for_emotion_then_splits_once(self):
        tx = FakeTx()
        sink = ReplyStreamSink(tx, min_first_sentence_words=4)
        sink.update("Gap to Payne is one point two. He's", "", "", emotion_known=False)
        self.assertEqual(tx.spoken, [])  # emotion not known yet -> silence
        sink.update("Gap to Payne is one point two. He's", "tense", "clipped", emotion_known=True)
        self.assertEqual(tx.spoken, ["Gap to Payne is one point two."])
        sink.update("Gap to Payne is one point two. He's on old tyres. Push now.", "tense", "clipped", True)
        self.assertEqual(len(tx.spoken), 1)  # no further splitting
        sink.complete("Gap to Payne is one point two. He's on old tyres. Push now.", "tense", "clipped")
        self.assertEqual(tx.spoken, ["Gap to Payne is one point two.", "He's on old tyres. Push now."])
        self.assertEqual(tx.calls[0][1], tx.calls[1][1])  # same emotion both segments

    def test_single_segment_when_split_disabled(self):
        tx = FakeTx()
        sink = ReplyStreamSink(tx, split_first_sentence=False)
        sink.update("One full sentence here now. Another one.", "calm", "even", True)
        self.assertEqual(tx.spoken, [])
        sink.complete("One full sentence here now. Another one.", "calm", "even")
        self.assertEqual(tx.spoken, ["One full sentence here now. Another one."])

    def test_empty_reply_speaks_nothing(self):
        tx = FakeTx()
        sink = ReplyStreamSink(tx)
        sink.complete("", "calm", "even")
        self.assertFalse(sink.started)

    def test_emotion_resolver_applied(self):
        tx = FakeTx()
        sink = ReplyStreamSink(tx, emotion_resolver=lambda e: "calm and composed")
        sink.complete("Box this lap.", "panicked", "fast")
        self.assertEqual(tx.calls[0][1], "calm and composed")


class TestBrainStreaming(unittest.TestCase):
    def _stream_chunks(self, payload, size=7):
        text = json.dumps(payload)
        chunks = [SimpleNamespace(text=c, usage_metadata=None) for c in _chunks(text, size)]
        chunks[-1].usage_metadata = SimpleNamespace(prompt_token_count=900, cached_content_token_count=0,
                                                    thoughts_token_count=40, candidates_token_count=60)
        return chunks

    def test_partial_json_string(self):
        buf = '{"intent": "gaps", "emotion": "tense", "delivery": "cli'
        self.assertEqual(RaceEngineerBrain._partial_json_string(buf, "emotion"), ("tense", True))
        self.assertEqual(RaceEngineerBrain._partial_json_string(buf, "delivery"), ("cli", False))
        buf2 = '{"response": "He said \\"box\\" and'
        self.assertEqual(RaceEngineerBrain._partial_json_string(buf2, "response"), ('He said "box" and', False))
        self.assertEqual(RaceEngineerBrain._partial_json_string('{"response": "a\\', "response"), ("a", False))

    def test_streamed_reply_spoken_early_and_identical(self):
        b = RaceEngineerBrain(api_key=None, thinking_level="LOW")
        b.client = MagicMock()
        payload = {"intent": "gaps", "emotion": "quietly confident", "delivery": "brisk",
                   "response": "Gap to Payne is one point two seconds. He's on old tyres, reel him in.",
                   "remember": "Payne struggling on old tyres"}
        seen_at = []
        tx = FakeTx()
        orig_say = tx.say

        def say(text, emotion, delivery):
            seen_at.append(len(tx.spoken))
            orig_say(text, emotion, delivery)
        tx.say = say

        b.client.models.generate_content_stream.return_value = iter(self._stream_chunks(payload))
        sink = ReplyStreamSink(tx, min_first_sentence_words=5)
        text, emo, dlv = b.respond_to_driver_text("gap ahead?", "ctx", speaker=sink)

        self.assertEqual(text, payload["response"])            # same words as non-streamed
        self.assertEqual(" ".join(tx.spoken), payload["response"])
        self.assertEqual(len(tx.spoken), 2)
        self.assertEqual(tx.calls[0][1], "quietly confident")  # emotion known from first word
        cfg = b.client.models.generate_content_stream.call_args.kwargs["config"]
        self.assertEqual(cfg.response_mime_type, "application/json")
        self.assertIn("LOW", str(cfg.thinking_config.thinking_level).upper())
        self.assertEqual(b.usage_totals["calls"], 1)
        self.assertIn("Payne struggling", b.notebook[-1])
        b.client.models.generate_content.assert_not_called()

    def test_non_streaming_path_unchanged_without_speaker(self):
        b = RaceEngineerBrain(api_key=None, thinking_level="LOW")
        b.client = MagicMock()
        b.client.models.generate_content.return_value = SimpleNamespace(
            text=json.dumps({"intent": "gaps", "emotion": "calm", "delivery": "even", "response": "All good."}),
            usage_metadata=None)
        text, _, _ = b.respond_to_driver_text("q", "ctx")
        self.assertEqual(text, "All good.")
        b.client.models.generate_content_stream.assert_not_called()


class TestTTSStreaming(unittest.TestCase):
    def _tts(self):
        p = persona_from_dict("t", {"voice": {"mode": "voice_id", "voice_id": "voice_abc"}})
        return GeminiTTS(api_key="fake", persona=p)

    @staticmethod
    def _events(pcm_bytes, sizes):
        evs, i = [SimpleNamespace(event_type="interaction.created")], 0
        for sz in sizes:
            part = pcm_bytes[i:i + sz]
            i += sz
            evs.append(SimpleNamespace(event_type="step.delta",
                                       delta=SimpleNamespace(type="audio", data=base64.b64encode(part).decode())))
        evs.append(SimpleNamespace(event_type="interaction.completed"))
        return evs

    def test_stream_yields_chunks_with_odd_byte_boundaries(self):
        tts = self._tts()
        samples = np.arange(1000, dtype="<i2")
        raw = samples.tobytes()
        tts.client = MagicMock()
        tts.client.interactions.create.return_value = iter(self._events(raw, [333, 501, 1166]))
        out = np.concatenate(list(tts.synthesize_stream("Box this lap.", emotion="urgent", delivery="fast")))
        np.testing.assert_array_equal(out, samples)
        kw = tts.client.interactions.create.call_args.kwargs
        self.assertTrue(kw["stream"])
        self.assertEqual(kw["response_format"]["mime_type"], "audio/l16")
        self.assertIn("Urgent", kw["input"][0]["content"][0]["annotations"][0]["style"])

    def test_stream_falls_back_to_lite_on_quota(self):
        tts = self._tts()
        raw = np.ones(480, dtype="<i2").tobytes()

        def create(**kw):
            if kw["model"] == "gemini-3.8-flash-tts":
                raise Exception("429 RESOURCE_EXHAUSTED")
            return iter(self._events(raw, [len(raw)]))
        tts.client = MagicMock()
        tts.client.interactions.create.side_effect = create
        out = list(tts.synthesize_stream("Box."))
        self.assertEqual(sum(len(c) for c in out), 480)
        self.assertIn("gemini-3.8-flash-tts", tts._exhausted_models)

    def test_stream_error_event_before_audio_uses_unary_fallback(self):
        tts = self._tts()
        tts.fallback_models = []
        tts.legacy_model = None
        tts.client = MagicMock()
        tts.client.interactions.create.side_effect = [
            iter([SimpleNamespace(event_type="error", error=SimpleNamespace(message="boom"))]),
            MagicMock(output_audio=SimpleNamespace(data=base64.b64encode(np.ones(240, dtype="<i2").tobytes()).decode())),
        ]
        out = list(tts.synthesize_stream("Box."))
        self.assertEqual(sum(len(c) for c in out), 240)


class _FakeOutputStream:
    instances = []

    def __init__(self, **kw):
        self.kw = kw
        self.written = []
        self.started = self.stopped = self.aborted = False
        _FakeOutputStream.instances.append(self)

    def start(self): self.started = True
    def write(self, data): self.written.append(data.copy()); time.sleep(0.001)
    def stop(self): self.stopped = True
    def abort(self): self.aborted = True
    def close(self): pass


class TestStreamingPlayback(unittest.TestCase):
    def setUp(self):
        _FakeOutputStream.instances.clear()
        self.patch = patch("voice.player.sd.OutputStream", _FakeOutputStream)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_pcm_stream_plays_all_audio_in_order(self):
        player = AudioPlayer(volume=1.0, radio_effect=True, gain_boost=1.0)
        pcm = player.open_stream(prebuffer_sec=0.05)
        for _ in range(10):
            pcm.push(np.full(480, 1000, dtype=np.int16))
        pcm.end()
        self.assertTrue(pcm.wait(5))
        s = _FakeOutputStream.instances[0]
        self.assertTrue(s.started and s.stopped)
        self.assertEqual(sum(len(w) for w in s.written), 4800)
        self.assertIsNotNone(pcm.first_audio_time)

    def test_filter_state_is_continuous_across_chunks(self):
        player = AudioPlayer(volume=1.0, radio_effect=True, gain_boost=1.0)
        sig = (np.sin(np.linspace(0, 200 * np.pi, 4800)) * 8000).astype(np.int16)
        a = player.open_stream(prebuffer_sec=0)
        a.push(sig); a.end(); a.wait(5)
        whole = np.concatenate(_FakeOutputStream.instances[-1].written).ravel()
        b = player.open_stream(prebuffer_sec=0)
        for c in np.array_split(sig, 7):
            b.push(c)
        b.end(); b.wait(5)
        split = np.concatenate(_FakeOutputStream.instances[-1].written).ravel()
        self.assertLessEqual(int(np.max(np.abs(whole.astype(int) - split.astype(int)))), 1)

    def test_interrupt_aborts_stream(self):
        player = AudioPlayer()
        pcm = player.open_stream(prebuffer_sec=0)
        pcm.push(np.ones(480, dtype=np.int16))
        time.sleep(0.05)
        player.interrupt()
        self.assertTrue(pcm.wait(5))
        self.assertTrue(pcm.aborted)

    def test_radio_transmission_end_to_end(self):
        class FakeTTS:
            def __init__(self):
                self.calls = []

            def synthesize_stream(self, text, emotion=None, delivery=None, cancel=None):
                self.calls.append((text, emotion))
                for _ in range(3):
                    yield np.full(240, 500, dtype=np.int16)

        tts = FakeTTS()
        player = AudioPlayer(radio_effect=False)
        tx = RadioTransmission(tts, player, prebuffer_sec=0.0)
        tx.say("First sentence here, five words.", "tense", "clipped")
        tx.say("Then the rest of it.", "tense", "clipped")
        tx.finish()
        self.assertTrue(tx.wait(5))
        self.assertEqual(sorted(c[0] for c in tts.calls), sorted(["First sentence here, five words.", "Then the rest of it."]))
        self.assertEqual(len(_FakeOutputStream.instances), 1)  # one continuous output stream
        self.assertEqual(sum(len(w) for w in _FakeOutputStream.instances[0].written), 1440)


if __name__ == "__main__":
    unittest.main()


class _RealtimeDevice:
    """Fake sound card that plays in real time and records dropouts (buffer ran empty mid-audio)."""
    last = None

    def __init__(self, **kw):
        self.sr = 24000
        self.cap = int(0.1 * self.sr)
        self.t0 = None
        self.written = 0
        self.dropouts = 0
        _RealtimeDevice.last = self

    def start(self): self.t0 = time.time()
    def level(self): return self.written - (time.time() - self.t0) * self.sr

    def write(self, d):
        if self.level() < -0.005 * self.sr:
            self.dropouts += 1
            self.t0 = time.time() - self.written / self.sr
        while self.level() + len(d) > self.cap:
            time.sleep(0.002)
        self.written += len(d)
        return False

    def stop(self):
        while self.level() > 0:
            time.sleep(0.005)
    def abort(self): pass
    def close(self): pass


class TestStutterFreeWhenTTSSlowerThanRealtime(unittest.TestCase):
    def _speech(self, sec, sr=24000):
        t = np.arange(int(sec * sr)) / sr
        env = (np.sin(2 * np.pi * 2.6 * t) > -0.2).astype(float)
        return (np.sin(2 * np.pi * 180 * t) * 6000 * env).astype(np.int16)

    def test_no_dropouts_at_0_8x_realtime_bursty_arrival(self):
        with patch("voice.player.sd.OutputStream", _RealtimeDevice):
            player = AudioPlayer(radio_effect=False)
            audio = self._speech(2.5)
            pcm = player.open_stream(prebuffer_sec=0.15)
            pcm.begin_segment("x" * 36)          # ~2.5 s of speech
            time.sleep(0.2)
            step = int(0.3 * 24000)
            for i in range(0, len(audio), step):
                pcm.push(audio[i:i + step])
                time.sleep(0.3 / 0.8)             # audio arrives at 0.8x real time, in bursts
            pcm.end_segment()
            pcm.end()
            self.assertTrue(pcm.wait(20))
            self.assertEqual(_RealtimeDevice.last.dropouts, 0)
            self.assertLessEqual(pcm.stats[0]["rebuffers"], 1)
            self.assertLess(player.tts_rate, 1.0)   # learned that this voice streams slower than real time


class TestNoSilentHangs(unittest.TestCase):
    def test_hung_tts_stream_falls_back_quickly(self):
        p = persona_from_dict("t", {"voice": {"mode": "voice_id", "voice_id": "voice_abc"}})
        tts = GeminiTTS(api_key="fake", persona=p, stream_first_audio_timeout_sec=0.3)
        tts.fallback_models = []
        tts.legacy_model = None

        def hang_forever():
            threading.Event().wait(60)
            yield None

        unary = MagicMock(output_audio=SimpleNamespace(
            data=base64.b64encode(np.ones(240, dtype="<i2").tobytes()).decode()))
        tts.client = MagicMock()
        tts.client.interactions.create.side_effect = [hang_forever(), unary]
        t0 = time.time()
        out = list(tts.synthesize_stream("Box this lap."))
        self.assertLess(time.time() - t0, 3.0)
        self.assertEqual(sum(len(c) for c in out), 240)

    def test_streaming_disabled_after_two_failures(self):
        p = persona_from_dict("t", {"voice": {"mode": "voice_id", "voice_id": "voice_abc"}})
        tts = GeminiTTS(api_key="fake", persona=p, stream_first_audio_timeout_sec=0.2)
        tts.fallback_models = []
        tts.legacy_model = None
        unary = MagicMock(output_audio=SimpleNamespace(
            data=base64.b64encode(np.ones(240, dtype="<i2").tobytes()).decode()))

        def create(**kw):
            if kw.get("stream"):
                return iter([SimpleNamespace(event_type="interaction.completed")])  # no audio
            return unary
        tts.client = MagicMock()
        tts.client.interactions.create.side_effect = create
        for _ in range(3):
            self.assertEqual(sum(len(c) for c in tts.synthesize_stream("Box.")), 240)
        stream_calls = [c for c in tts.client.interactions.create.call_args_list if c.kwargs.get("stream")]
        self.assertEqual(len(stream_calls), 2)   # third call skipped streaming entirely
        tts.reset_session()
        self.assertEqual(tts._stream_failures, 0)

    def test_second_sentence_synthesised_while_first_plays(self):
        started = {}

        class SlowTTS:
            def synthesize_stream(self, text, emotion=None, delivery=None, cancel=None):
                started[text] = time.time()
                for _ in range(10):
                    time.sleep(0.05)
                    yield np.full(1200, 300, dtype=np.int16)

        with patch("voice.player.sd.OutputStream", _RealtimeDevice):
            player = AudioPlayer(radio_effect=False)
            tx = RadioTransmission(SlowTTS(), player, prebuffer_sec=0.05)
            tx.say("First sentence of the radio call.", "calm", "even")
            time.sleep(0.1)
            tx.say("Second sentence follows right after.", "calm", "even")
            tx.finish()
            self.assertTrue(tx.wait(20))
            gap = started["Second sentence follows right after."] - started["First sentence of the radio call."]
            self.assertLess(gap, 0.3)                      # did not wait for sentence 1 to finish
            self.assertEqual(_RealtimeDevice.last.dropouts, 0)
