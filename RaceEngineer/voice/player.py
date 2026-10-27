"""
Audio playback engine for AMS2 Race Engineer.
Plays TTS PCM audio and radio sound cues via sounddevice.
"""

import re
import time
import queue
import threading
from typing import List, Optional
import numpy as np
import sounddevice as sd
from utils.logging import logger

try:
    from scipy.signal import butter, lfilter
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class AudioPlayer:
    def __init__(self, volume: float = 1.0, radio_effect: bool = False, gain_boost: float = 1.6):
        self.volume = max(0.1, min(3.0, volume))
        self.gain_boost = max(1.0, min(3.0, gain_boost))
        self.radio_effect = radio_effect and HAS_SCIPY
        self._lock = threading.Lock()
        self._playback_lock = threading.Lock()
        self._is_playing = False
        self._active_stream: Optional["PCMStream"] = None
        # Learned across calls by PCMStream: TTS audio arrival speed (x real time) and speaking rate
        self.tts_rate = 1.0
        self.tts_rate_learned = False
        self.samples_per_char = 24000 / 14.0

    def is_playing(self) -> bool:
        return self._is_playing

    def open_stream(self, sample_rate: int = 24000, prebuffer_sec: float = 0.15) -> "PCMStream":
        """Starts a streaming playback session; push int16 chunks as they arrive from TTS."""
        return PCMStream(self, sample_rate=sample_rate, prebuffer_sec=prebuffer_sec)

    def interrupt(self):
        """Immediately halts any currently playing audio stream."""
        active = self._active_stream
        if active is not None:
            active.abort()
        try:
            sd.stop()
        except Exception:
            pass
        with self._lock:
            self._is_playing = False

    def _apply_radio_filter(self, data: np.ndarray, sample_rate: int) -> np.ndarray:
        """Applies bandpass filter (300Hz - 3200Hz) to simulate cockpit team radio."""
        if not HAS_SCIPY:
            return data
        try:
            lowcut = 350.0
            highcut = 3200.0
            nyq = 0.5 * sample_rate
            low = lowcut / nyq
            high = highcut / nyq
            b, a = butter(2, [low, high], btype='band')
            filtered = lfilter(b, a, data.astype(np.float32))
            # Subtle saturation / radio warmth
            clipped = np.clip(filtered * 1.2, -32000, 32000)
            return clipped.astype(np.int16)
        except Exception:
            return data

    def play_pcm(self, audio_data: np.ndarray, sample_rate: int = 24000, block: bool = True):
        """Plays 16-bit PCM audio numpy array."""
        if audio_data is None or len(audio_data) == 0:
            return

        with self._playback_lock:
            with self._lock:
                self._is_playing = True

            try:
                # Pre-gain amplification & volume scaling so voice cuts through engine audio
                scaled = audio_data.astype(np.float32) * self.volume * self.gain_boost
                # Soft-clip using hyperbolic tangent saturation to avoid harsh digital square clipping
                peak = np.max(np.abs(scaled)) if len(scaled) > 0 else 0
                if peak > 32767.0:
                    clipped = np.clip(np.tanh(scaled / 32767.0) * 32767.0, -32768, 32767).astype(np.int16)
                else:
                    clipped = np.clip(scaled, -32768, 32767).astype(np.int16)

                if self.radio_effect:
                    clipped = self._apply_radio_filter(clipped, sample_rate)

                sd.play(clipped, samplerate=sample_rate, blocking=block)
            except Exception as e:
                logger.error(f"Error during audio playback: {e}")
            finally:
                with self._lock:
                    self._is_playing = False

    def play_radio_beep(self, open_mic: bool = True):
        """Generates and plays a quick low-latency radio transmission beep."""
        sample_rate = 24000
        duration = 0.045  # 45ms
        freq = 1350 if open_mic else 850
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        # Soft envelope at start/end to avoid clicking
        envelope = np.sin(np.pi * np.linspace(0, 1, len(t)))
        tone = (np.sin(2 * np.pi * freq * t) * envelope * 0.15 * 32767).astype(np.int16)

        try:
            sd.play(tone, samplerate=sample_rate, blocking=False)
        except Exception:
            pass


class _Segment:
    """One TTS request's worth of audio inside a transmission."""

    def __init__(self, expected_samples: Optional[int]):
        self.expected = expected_samples      # estimated total length (None = unknown)
        self.chunks: List[np.ndarray] = []
        self.available = 0                    # samples received but not yet played
        self.received = 0
        self.ended = False
        self.t_first: Optional[float] = None  # arrival time of first chunk
        self.t_last: Optional[float] = None
        self.first_len = 0
        self.max_gap = 0.0                    # longest wait between chunks (network/TTS jitter)
        self.playing = False
        self.fade_in = False
        self.wait_started: Optional[float] = None
        self.waited = 0.0
        self.rebuffers = 0

    def add(self, chunk: np.ndarray):
        now = time.time()
        if self.t_first is None:
            self.t_first = now
            self.first_len = len(chunk)
        else:
            self.max_gap = max(self.max_gap, now - self.t_last)
        self.t_last = now
        self.chunks.append(chunk)
        self.available += len(chunk)
        self.received += len(chunk)

    def take(self, n: int) -> np.ndarray:
        out, need = [], n
        while need > 0 and self.chunks:
            c = self.chunks[0]
            if len(c) <= need:
                out.append(c)
                self.chunks.pop(0)
                need -= len(c)
            else:
                out.append(c[:need])
                self.chunks[0] = c[need:]
                need = 0
        data = np.concatenate(out) if out else np.zeros(0, dtype=np.int16)
        self.available -= len(data)
        return data

    def peek(self, n: int) -> np.ndarray:
        out, need = [], n
        for c in self.chunks:
            if need <= 0:
                break
            out.append(c[:need])
            need -= len(out[-1])
        return np.concatenate(out) if out else np.zeros(0, dtype=np.int16)


class PCMStream:
    """
    Stutter-free streaming playback for TTS audio.

    Gemini's TTS stream doesn't always produce audio faster than real time, so simply playing
    chunks as they arrive runs dry a couple of seconds in and you hear the gaps. Instead, each
    segment (one TTS request) is handled like a proper jitter buffer:

    * Before a segment starts, we measure how fast its audio is arriving and estimate how long it
      will be (from the text, learned per voice). Playback starts only once the buffered audio
      will last until the rest has arrived -- so it doesn't run dry mid-sentence. If generation is
      comfortably faster than real time this is ~150 ms; if it's slower, it waits a bit longer.
    * If it would still run dry, it pauses at the quietest point (between words) with a short
      fade instead of cutting mid-word, refills, and fades back in.
    * Waits between segments fall on sentence boundaries, where a pause sounds natural.
    * Arrival rate and speaking rate are remembered (per player) so later calls start smarter.
    * Same gain / soft-limit / radio band-pass as play_pcm(), with filter state carried across
      blocks -- no clicks at joins. The playback lock is held for the whole transmission.
    """

    _END = object()
    BLOCK_SEC = 0.02
    FADE_SEC = 0.006
    LOW_WATER_SEC = 0.25
    MARGIN_SEC = 0.2
    MAX_WAIT_SEC = 3.0
    MAX_PREBUFFER_SEC = 4.0
    IDLE_GIVE_UP_SEC = 25.0

    def __init__(self, player: AudioPlayer, sample_rate: int = 24000, prebuffer_sec: float = 0.15):
        self.player = player
        self.sample_rate = sample_rate
        self.min_prebuffer = int(max(0.0, prebuffer_sec) * sample_rate)
        self.block = int(self.BLOCK_SEC * sample_rate)
        self.fade = max(1, int(self.FADE_SEC * sample_rate))
        self._q: "queue.Queue" = queue.Queue()
        self._aborted = threading.Event()
        self._done = threading.Event()
        self.first_audio_time: Optional[float] = None
        self.samples_played = 0
        self.underruns = 0
        self.stats: List[dict] = []
        self._dev_t0: Optional[float] = None
        self._dev_written = 0
        self._seg_lock = threading.Lock()
        self._next_seg_id = 0

        self._ba = None
        self._zi = None
        if player.radio_effect and HAS_SCIPY:
            try:
                from scipy.signal import lfilter_zi
                nyq = 0.5 * sample_rate
                b, a = butter(2, [350.0 / nyq, 3200.0 / nyq], btype="band")
                self._ba = (b, a)
                self._zi = lfilter_zi(b, a) * 0.0
            except Exception:
                self._ba = None

        self._thread = threading.Thread(target=self._run, name="PCMStream", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ producer API
    def begin_segment(self, text: Optional[str] = None) -> int:
        """
        Registers the next segment (one TTS request) in playback order and returns its id.
        Segments can then be filled concurrently -- audio for segment 2 can arrive while
        segment 1 is still playing, so there's no gap between sentences.
        """
        with self._seg_lock:
            self._next_seg_id += 1
            sid = self._next_seg_id
        if not self._aborted.is_set():
            self._q.put(("seg", (sid, self._estimate_samples(text) if text else None)))
        return sid

    def end_segment(self, seg_id: Optional[int] = None):
        if not self._aborted.is_set():
            self._q.put(("segend", seg_id))

    def push(self, chunk: np.ndarray, seg_id: Optional[int] = None):
        if chunk is not None and len(chunk) and not self._aborted.is_set():
            self._q.put(("audio", (seg_id, chunk)))

    def end(self):
        self._q.put(self._END)

    def abort(self):
        self._aborted.set()
        self._q.put(self._END)

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done.wait(timeout)

    @property
    def aborted(self) -> bool:
        return self._aborted.is_set()

    # ------------------------------------------------------------------ estimates
    def _estimate_samples(self, text: str) -> int:
        pauses = len(re.findall(r"<(short pause|long pause|breath|exhales|sigh)>", text))
        chars = len(re.sub(r"<[^>]+>", "", text).strip())
        return int(chars * self.player.samples_per_char + pauses * 0.35 * self.sample_rate)

    def _rate(self, seg: _Segment) -> float:
        """Estimated arrival speed of this segment's audio, as a multiple of real time."""
        prior = self.player.tts_rate
        if seg.t_first is None:
            return prior
        elapsed = time.time() - seg.t_first
        if elapsed < 0.3:
            return 0.9 * prior / (1.0 + 0.15 * seg.rebuffers)
        # Measure after the first chunk (it tends to arrive as a burst)
        measured = (seg.received - seg.first_len) / self.sample_rate / elapsed
        rate = 0.25 * prior + 0.75 * measured
        return max(0.05, 0.9 * rate / (1.0 + 0.15 * seg.rebuffers))  # 10% safety for jitter

    def _margin(self, seg: _Segment) -> float:
        """Safety buffer: at least MARGIN_SEC, more if chunks arrive in big bursts, more after a pause."""
        return max(self.MARGIN_SEC, 1.1 * seg.max_gap) * (1.0 + 0.5 * seg.rebuffers)

    def _device_level(self) -> float:
        """Seconds of audio already handed to the sound device but not yet heard."""
        if self._dev_t0 is None:
            return 0.0
        return max(0.0, self._dev_written / self.sample_rate - (time.time() - self._dev_t0))

    def _ready(self, seg: _Segment) -> bool:
        if seg.ended:
            return seg.available > 0 or seg.received > 0
        if seg.available <= 0:
            return False
        sr = self.sample_rate
        buffered = seg.available / sr + (self._device_level() if seg.playing or seg.rebuffers else 0.0)
        if buffered >= self.MAX_PREBUFFER_SEC:
            return True
        if seg.wait_started is not None and time.time() - seg.wait_started > self.MAX_WAIT_SEC:
            return True
        if seg.available < self.min_prebuffer:
            return False
        if not self.player.tts_rate_learned and seg.t_first is not None and seg.max_gap == 0.0 \
                and time.time() - seg.t_first < 0.5:
            return False  # first call ever: see a second chunk so we know how fast audio arrives
        if seg.expected is None:
            remaining = 0.5 * sr * (1.0 if self.player.tts_rate < 1.2 else 0.3)
        else:
            remaining = max(seg.expected - seg.received, 0.3 * sr)
        # Buffer b drains at 1x while audio arrives at r x real time. To finish without running dry
        # we need b >= remaining * (1/r - 1). For r >= 1 only the safety margin is needed.
        need = max(0.0, remaining / sr * (1.0 / self._rate(seg) - 1.0))
        return buffered >= need + self._margin(seg)

    def _will_starve(self, seg: _Segment) -> bool:
        """True if what's buffered will run out before the rest of the segment arrives."""
        sr = self.sample_rate
        if seg.expected is None:
            remaining = 0.3 * sr
        else:
            if seg.received >= 0.9 * seg.expected:
                return False  # practically all here; a gap now would land at the sentence end anyway
            remaining = max(seg.expected - seg.received, 0.15 * sr)
        need = max(0.0, remaining / sr * (1.0 / self._rate(seg) - 1.0))
        return seg.available / sr + self._device_level() < need + 0.5 * self._margin(seg)

    def _learn(self, seg: _Segment, text_samples_est: Optional[int]):
        p = self.player
        if seg.t_first and seg.t_last and seg.received > self.sample_rate * 0.5:
            span = seg.t_last - seg.t_first
            if span > 0.2:
                rate = max(0.1, min((seg.received - seg.first_len) / self.sample_rate / span, 10.0))
                p.tts_rate = rate if not p.tts_rate_learned else 0.5 * p.tts_rate + 0.5 * rate
                p.tts_rate_learned = True
        if text_samples_est and seg.received > self.sample_rate * 0.5:
            ratio = seg.received / text_samples_est
            p.samples_per_char = 0.8 * p.samples_per_char + 0.2 * p.samples_per_char * max(0.5, min(ratio, 2.0))

    # ------------------------------------------------------------------ processing
    def _process(self, chunk: np.ndarray) -> np.ndarray:
        p = self.player
        x = chunk.astype(np.float32) * p.volume * p.gain_boost / 32767.0
        # Smooth per-sample limiter (linear below 0.8, tanh knee above) -- chunk-independent
        ax = np.abs(x)
        over = ax > 0.8
        if np.any(over):
            x[over] = np.sign(x[over]) * (0.8 + 0.2 * np.tanh((ax[over] - 0.8) / 0.2))
        y = x * 32767.0
        if self._ba is not None:
            b, a = self._ba
            y, self._zi = lfilter(b, a, y, zi=self._zi)
            y = np.clip(y * 1.2, -32000, 32000)
        return np.clip(y, -32768, 32767).astype(np.int16)

    def _quiet_cut(self, seg: _Segment) -> int:
        """Number of samples to play before pausing: ends at the quietest 10 ms in what's buffered."""
        data = seg.peek(seg.available).astype(np.float32)
        win = max(1, int(0.01 * self.sample_rate))
        if len(data) < 3 * win:
            return len(data)
        energy = np.convolve(data * data, np.ones(win) / win, mode="valid")
        start = win  # keep at least 10 ms
        idx = int(np.argmin(energy[start:])) + start
        return min(len(data), idx + win // 2)

    # ------------------------------------------------------------------ playback thread
    def _run(self):
        p = self.player
        stream = None
        segs: List[_Segment] = []
        seg_est: dict = {}
        by_id: dict = {}
        last_activity = [time.time()]
        ended = False
        sr = self.sample_rate
        silence = np.zeros(self.block, dtype=np.int16)

        def drain(block_timeout: float):
            nonlocal ended
            try:
                item = self._q.get(timeout=block_timeout) if block_timeout > 0 else self._q.get_nowait()
            except queue.Empty:
                return
            while True:
                if item is self._END:
                    ended = True
                    for s_ in segs:
                        s_.ended = True
                else:
                    kind, val = item
                    if kind == "seg":
                        sid, est = val
                        seg_obj = _Segment(est)
                        segs.append(seg_obj)
                        by_id[sid] = seg_obj
                        seg_est[id(seg_obj)] = est
                    elif kind == "segend":
                        target = by_id.get(val) if val is not None else next((s_ for s_ in segs if not s_.ended), None)
                        if target is not None:
                            target.ended = True
                    elif kind == "audio":
                        sid, chunk = val
                        target = by_id.get(sid) if sid is not None else next((s_ for s_ in segs if not s_.ended), None)
                        if target is None:
                            target = _Segment(None)
                            segs.append(target)
                            if sid is not None:
                                by_id[sid] = target
                        target.add(chunk)
                    last_activity[0] = time.time()
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    return

        def write(data: np.ndarray):
            if not len(data):
                return
            out = self._process(data)
            now = time.time()
            if self._dev_t0 is None or self._device_level() <= 0.0:
                # device ran empty (or first write): restart the clock
                self._dev_t0 = now - self._dev_written / sr + (0.0 if self._dev_t0 is None else 0.0)
                if self._device_level() <= 0.0:
                    self._dev_t0 = now - self._dev_written / sr
            underflow = stream.write(out.reshape(-1, 1))
            if underflow:
                self.underruns += 1
            self._dev_written += len(out)
            self.samples_played += len(out)

        try:
            with p._playback_lock:
                if self._aborted.is_set():
                    return
                p._active_stream = self
                while not self._aborted.is_set():
                    drain(0)
                    if not ended and time.time() - last_activity[0] > self.IDLE_GIVE_UP_SEC:
                        logger.warning(f"[AUDIO] no audio or end signal for {self.IDLE_GIVE_UP_SEC:.0f}s - releasing the radio.")
                        break
                    seg = segs[0] if segs else None

                    if seg is None:
                        if ended:
                            break
                        if stream is not None:
                            write(silence)       # between sentences: keep the device fed
                        else:
                            drain(0.01)
                        continue

                    if not seg.playing:
                        if seg.wait_started is None:
                            seg.wait_started = time.time()
                        if seg.ended and seg.available == 0:
                            segs.pop(0)
                            continue
                        if self._ready(seg):
                            seg.waited += time.time() - seg.wait_started
                            seg.wait_started = None
                            seg.playing = True
                            if stream is None:
                                with p._lock:
                                    p._is_playing = True
                                stream = sd.OutputStream(samplerate=sr, channels=1, dtype="int16", latency="high")
                                stream.start()
                                self.first_audio_time = time.time()
                        elif stream is not None:
                            write(silence)
                        else:
                            drain(0.01)
                        continue

                    # ---- playing this segment
                    if seg.ended and seg.available == 0:
                        st = {"sec": round(seg.received / sr, 2), "waited": round(seg.waited, 2),
                              "rebuffers": seg.rebuffers}
                        self._learn(seg, seg_est.get(id(seg)))
                        st["rate_after"] = round(p.tts_rate, 2)
                        self.stats.append(st)
                        segs.pop(0)
                        continue

                    low = seg.available <= self.LOW_WATER_SEC * sr
                    if seg.ended or (not low) or (seg.available >= self.block and not self._will_starve(seg)):
                        data = seg.take(min(self.block, seg.available))
                        if seg.fade_in:
                            n = min(self.fade, len(data))
                            data = data.astype(np.float32)
                            data[:n] *= np.linspace(0.0, 1.0, n)
                            data = data.astype(np.int16)
                            seg.fade_in = False
                        write(data)
                        continue

                    # Low: the sound device still holds a little audio, so wait for more data
                    # while it plays; only if that runs out too do we pause (between words).
                    drain(0.005)
                    while (not seg.ended and seg.available < self.block and self._device_level() > 0.03
                           and not self._aborted.is_set()):
                        drain(0.005)
                    if seg.ended or seg.available > self.LOW_WATER_SEC * sr:
                        continue
                    if seg.available >= self.block and not self._will_starve(seg):
                        continue
                    cut = self._quiet_cut(seg)
                    data = seg.take(cut).astype(np.float32)
                    n = min(self.fade, len(data))
                    if n:
                        data[-n:] *= np.linspace(1.0, 0.0, n)
                    write(data.astype(np.int16))
                    seg.playing = False
                    seg.fade_in = True
                    seg.rebuffers += 1
                    seg.wait_started = time.time()
        except Exception as e:
            logger.error(f"Streaming playback error: {e}")
        finally:
            if stream is not None:
                try:
                    if self._aborted.is_set():
                        stream.abort()
                    else:
                        stream.stop()  # stop() drains what is already queued to the device
                    stream.close()
                except Exception:
                    pass
            if self.samples_played == 0 and not self._aborted.is_set():
                logger.warning("[AUDIO] nothing was played - the voice returned no audio for this call.")
            if self.stats:
                logger.info("[AUDIO] " + " | ".join(
                    f"seg{i + 1}: {s_['sec']}s audio, waited {s_['waited']}s, rebuffers {s_['rebuffers']}, "
                    f"tts {s_['rate_after']}x realtime" for i, s_ in enumerate(self.stats))
                    + (f" | device underruns {self.underruns}" if self.underruns else ""))
            if p._active_stream is self:
                p._active_stream = None
            with p._lock:
                p._is_playing = False
            self._done.set()
