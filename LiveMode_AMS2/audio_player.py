import queue
import threading
import struct
import time
import wave
import os
import subprocess
import tempfile

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False
    print("Warning: pyaudio not installed. Falling back to system player.")

class AudioPlayer:
    def __init__(self, sample_rate: int = 24000, sample_width: int = 2, channels: int = 1):
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels
        
        self._audio_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._is_playing = False
        self._total_bytes = 0
        self._elapsed_bytes = 0
        self._bytes_per_second = sample_rate * sample_width * channels
        
        if HAS_PYAUDIO:
            self.p = pyaudio.PyAudio()
            self.stream = self.p.open(
                format=self.p.get_format_from_width(sample_width),
                channels=channels,
                rate=sample_rate,
                output=True
            )
        else:
            self.p = None
            self.stream = None
            
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()

    def play(self, audio_data: bytes):
        """Queue audio data for playback."""
        if not self._is_playing or self._audio_queue.empty() and self._elapsed_bytes >= self._total_bytes:
            self._total_bytes = 0
            self._elapsed_bytes = 0
            
        self._total_bytes += len(audio_data)
        self._is_playing = True
        self._audio_queue.put(audio_data)

    def time_remaining(self) -> float:
        """Returns estimated seconds remaining in current playback."""
        if not self._is_playing or self._total_bytes == 0:
            return 0.0
        remaining_bytes = self._total_bytes - self._elapsed_bytes
        if remaining_bytes < 0:
            return 0.0
        return remaining_bytes / self._bytes_per_second

    def is_playing(self) -> bool:
        """Returns True if audio is currently being played."""
        return self._is_playing

    def stop(self):
        """Stop current playback immediately."""
        self._stop_event.set()
        # Clear the queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        self._is_playing = False
        self._total_bytes = 0
        self._elapsed_bytes = 0

    def shutdown(self):
        """Stop playback, close stream, terminate PyAudio."""
        self.stop()
        if HAS_PYAUDIO and self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.p.terminate()

    def _playback_loop(self):
        """Background thread for continuous playback of queued audio data."""
        while True:
            try:
                # Wait for audio data
                audio_data = self._audio_queue.get(timeout=0.1)
                self._stop_event.clear()
                self._is_playing = True
                
                if HAS_PYAUDIO and self.stream:
                    chunk_size = 4096
                    for i in range(0, len(audio_data), chunk_size):
                        if self._stop_event.is_set():
                            break
                        chunk = audio_data[i:i+chunk_size]
                        self.stream.write(chunk)
                        self._elapsed_bytes += len(chunk)
                else:
                    # Fallback behavior when pyaudio is missing
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        temp_path = f.name
                        with wave.open(f, 'wb') as wf:
                            wf.setnchannels(self.channels)
                            wf.setsampwidth(self.sample_width)
                            wf.setframerate(self.sample_rate)
                            wf.writeframes(audio_data)
                    
                    if os.name == 'nt':
                        # Windows default player
                        subprocess.Popen(['start', '', temp_path], shell=True)
                    elif os.uname().sysname == 'Darwin':
                        # macOS afplay
                        subprocess.Popen(['afplay', temp_path])
                    else:
                        # Linux aplay
                        subprocess.Popen(['aplay', temp_path])
                    
                    # Estimate the wait duration for playback
                    duration = len(audio_data) / self._bytes_per_second
                    start_time = time.time()
                    try:
                        while time.time() - start_time < duration:
                            if self._stop_event.is_set():
                                break
                            time.sleep(0.1)
                            self._elapsed_bytes += int(self._bytes_per_second * 0.1)
                    finally:
                        try:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                        except Exception as e:
                            print(f"Failed to delete temp audio file {temp_path}: {e}")
                        
                if self._audio_queue.empty():
                    self._is_playing = False
                    self._elapsed_bytes = self._total_bytes
                    
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error in playback loop: {e}")
                self._is_playing = False

    def _crossfade(self, old_audio: bytes, new_audio: bytes, fade_ms: int = 500) -> bytes:
        """Linear crossfade between the end of old_audio and start of new_audio."""
        if self.sample_width not in (1, 2):
            return old_audio + new_audio
            
        fade_samples = int((fade_ms / 1000.0) * self.sample_rate)
        fade_bytes = fade_samples * self.sample_width * self.channels
        
        # Adjust if the fade length exceeds available audio length
        if fade_bytes > len(old_audio) or fade_bytes > len(new_audio):
            fade_bytes = min(len(old_audio), len(new_audio))
            fade_samples = fade_bytes // (self.sample_width * self.channels)
            
        if fade_samples == 0:
            return old_audio + new_audio
            
        old_fade = old_audio[-fade_bytes:]
        new_fade = new_audio[:fade_bytes]
        
        fmt = f"<{fade_samples * self.channels}h" if self.sample_width == 2 else f"<{fade_samples * self.channels}B"
        old_unpacked = struct.unpack(fmt, old_fade)
        new_unpacked = struct.unpack(fmt, new_fade)
        
        blended = []
        for i in range(len(old_unpacked)):
            # Ratio based on sample index relative to channel count
            sample_index = i // self.channels
            ratio = sample_index / float(fade_samples - 1 if fade_samples > 1 else 1)
            
            blended_sample = int(old_unpacked[i] * (1.0 - ratio) + new_unpacked[i] * ratio)
            blended.append(blended_sample)
            
        blended_bytes = struct.pack(fmt, *blended)
        
        return old_audio[:-fade_bytes] + blended_bytes + new_audio[fade_bytes:]
