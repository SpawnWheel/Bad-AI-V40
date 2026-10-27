"""
Streaming radio pipeline: LLM text stream -> Gemini 3.8 TTS stream -> gap-free playback.

    RadioTransmission   one radio call. Segments of text go in, audio comes out of the speakers
                        as soon as the first TTS chunk arrives. Segment N+1 is synthesised while
                        segment N is still playing, so there are no gaps.
    ReplyStreamSink     sits between the brain's streaming LLM call and a RadioTransmission.
                        It decides WHEN text is ready to speak, keeping quality first:
                          * waits until the emotion + delivery are known (so the very first word
                            already has the right tone),
                          * speaks the first complete sentence early (only if it is a real
                            sentence, >= N words), then the rest of the reply as ONE segment, so
                            intonation stays natural -- at most two TTS requests per reply,
                          * never speaks half-written sentences.

The words themselves are exactly what the model wrote; streaming only changes when he starts
talking, not what he says.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, List, Optional

from utils.logging import logger

# Sentence end: . ! ? followed by whitespace, not part of a decimal like 1.2 / 1:12.4
_SENTENCE_END = re.compile(r"(?<!\d\.\d)(?<=[.!?])[\"')\]]?\s+(?=[A-Z0-9<\"'])")


def find_first_sentence_end(text: str, min_words: int = 5) -> int:
    """Index just after the first sentence that has at least `min_words` words, or -1."""
    for m in _SENTENCE_END.finditer(text):
        head = text[:m.start()]
        # Don't split right after a decimal number ("1.2 seconds" has no space after '.', so fine),
        # or after common abbreviations.
        if re.search(r"\b(vs|approx|No|Nr|T|P)\.$", head):
            continue
        words = re.sub(r"<[^>]+>", " ", head).split()
        if len(words) >= min_words:
            return m.end()
    return -1


class RadioTransmission:
    """
    One outgoing radio call, fed segment by segment.

    Each segment's voice request starts the moment its text is known -- the second sentence is
    being synthesised while the first is still playing -- and the player plays them back in order.
    """

    def __init__(self, tts, player, sample_rate: int = 24000, prebuffer_sec: float = 0.15,
                 t_start: Optional[float] = None, label: str = "radio"):
        self.tts = tts
        self.player = player
        self.label = label
        self.t_start = t_start or time.time()
        self._cancel = threading.Event()
        self.spoken: List[str] = []
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self._finished = False
        self._pcm = player.open_stream(sample_rate=sample_rate, prebuffer_sec=prebuffer_sec)
        self._first_chunk_logged = False
        self._closer: Optional[threading.Thread] = None

    @property
    def started(self) -> bool:
        return bool(self.spoken)

    def say(self, text: str, emotion: Optional[str], delivery: Optional[str]):
        text = (text or "").strip()
        if not text or self._cancel.is_set():
            return
        with self._lock:
            if self._finished:
                logger.debug("RadioTransmission.say() after finish(); ignored")
                return
            self.spoken.append(text)
            seg_id = self._pcm.begin_segment(text)       # fixes playback order now
            th = threading.Thread(target=self._synth, args=(seg_id, text, emotion, delivery),
                                  name=f"RadioTx-TTS-{seg_id}", daemon=True)
            self._threads.append(th)
        th.start()

    def finish(self):
        with self._lock:
            if self._finished:
                return
            self._finished = True
            threads = list(self._threads)

        def closer():
            for t in threads:
                t.join()
            self._pcm.end()

        self._closer = threading.Thread(target=closer, name="RadioTx-close", daemon=True)
        self._closer.start()

    def cancel(self):
        self._cancel.set()
        self._pcm.abort()

    def wait(self, timeout: Optional[float] = None) -> bool:
        done = self._pcm.wait(timeout)
        if not done:
            logger.warning(f"[AUDIO] {self.label}: transmission timed out, cancelling.")
            self.cancel()
            self._pcm.wait(2.0)
        if done and self._pcm.first_audio_time and not getattr(self, "_latency_logged", False):
            self._latency_logged = True
            logger.info(f"[LATENCY] {self.label}: speaking {self._pcm.first_audio_time - self.t_start:.2f}s after trigger")
        return done

    def _synth(self, seg_id: int, text: str, emotion: Optional[str], delivery: Optional[str]):
        try:
            for chunk in self.tts.synthesize_stream(text, emotion=emotion, delivery=delivery, cancel=self._cancel):
                if self._cancel.is_set() or self._pcm.aborted:
                    break
                if not self._first_chunk_logged:
                    self._first_chunk_logged = True
                    logger.info(f"[LATENCY] {self.label}: first TTS audio {time.time() - self.t_start:.2f}s after trigger")
                self._pcm.push(chunk, seg_id)
        except Exception as e:
            logger.error(f"Radio transmission error: {e}")
        finally:
            self._pcm.end_segment(seg_id)


class ReplyStreamSink:
    """
    Receives the growing reply from the brain's streaming LLM call and hands speakable
    segments to a RadioTransmission.
    """

    def __init__(
        self,
        transmission: RadioTransmission,
        emotion_resolver: Optional[Callable[[str], str]] = None,
        split_first_sentence: bool = True,
        min_first_sentence_words: int = 5,
    ):
        self.tx = transmission
        self.resolve = emotion_resolver or (lambda e: e)
        self.split_first_sentence = split_first_sentence
        self.min_words = max(1, int(min_first_sentence_words))
        self._first_len = 0          # chars of the reply already sent to TTS
        self._completed = False
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        return self.tx.started

    def update(self, response_so_far: str, emotion: str, delivery: str, emotion_known: bool):
        """Called repeatedly while the LLM streams. Speaks the first sentence early if ready."""
        if not self.split_first_sentence or self._completed or self._first_len:
            return
        if not emotion_known:
            return  # never start speaking before we know HOW to say it
        idx = find_first_sentence_end(response_so_far, self.min_words)
        if idx <= 0:
            return
        with self._lock:
            if self._first_len or self._completed:
                return
            first = response_so_far[:idx].strip()
            self._first_len = idx
            self.tx.say(first, self.resolve(emotion), delivery)

    def complete(self, final_response: str, emotion: str, delivery: str):
        """Called once with the final, sanitised reply. Speaks whatever hasn't been spoken yet."""
        with self._lock:
            if self._completed:
                return
            self._completed = True
            final_response = (final_response or "").strip()
            if not final_response:
                return
            if self._first_len:
                spoken = self.tx.spoken[0] if self.tx.spoken else ""
                if final_response.startswith(spoken):
                    rest = final_response[len(spoken):]
                else:
                    idx = find_first_sentence_end(final_response, self.min_words)
                    rest = final_response[idx:] if idx > 0 else ""
                self.tx.say(rest.strip(), self.resolve(emotion), delivery)
            else:
                self.tx.say(final_response, self.resolve(emotion), delivery)
