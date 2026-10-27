"""
Push-to-Talk (PTT) manager for steering wheels and keyboards.
Dual-Action Button:
  - Short Tap (<350ms): Requests comprehensive Race Situation Update (SitRep).
  - Long Hold (>350ms): Opens team radio microphone for voice questions with Gemini.
Zero background listening when button is idle.
Zero disk writes (all audio kept strictly in RAM).
"""

import os
import sys
import time
import io
import wave
import threading
from typing import Optional, Callable, List

# Background events must be enabled BEFORE pygame initializes
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame
import sounddevice as sd
from pynput import keyboard

from utils.logging import logger

SAMPLE_RATE = 16000  # 16 kHz mono PCM
CHANNELS = 1         # Mono
BLOCK_SIZE = 1024
DEFAULT_HOLD_THRESHOLD = 0.35  # 350ms threshold between tap and hold


class PushToTalkManager:
    def __init__(
        self,
        mode: str = "auto",
        joystick_index: int = 0,
        button_index: int = 0,
        keyboard_key: str = "caps_lock",
        hold_threshold_sec: float = DEFAULT_HOLD_THRESHOLD,
        beep_feedback: bool = True,
        on_audio_ready: Optional[Callable[[bytes], None]] = None,
        on_tap: Optional[Callable[[], None]] = None,
        beep_callback: Optional[Callable[[bool], None]] = None
    ):
        self.mode = mode
        self.joystick_index = joystick_index
        self.button_index = button_index
        self.keyboard_key_name = keyboard_key.lower()
        self.hold_threshold = max(0.15, min(1.0, hold_threshold_sec))
        self.beep_feedback = beep_feedback
        self.on_audio_ready = on_audio_ready
        self.on_tap = on_tap
        self.beep_callback = beep_callback

        # Audio recording state (ONLY active while button is pressed)
        self._is_recording = False
        self._is_holding = False
        self._press_time = 0.0
        self._active_chunks: List[bytes] = []
        self._audio_stream: Optional[sd.RawInputStream] = None

        self._running = False
        self._lock = threading.Lock()
        self._wheel_thread: Optional[threading.Thread] = None
        self._kb_listener: Optional[keyboard.Listener] = None

        self.wheel_name = "None"
        self.wheel_connected = False

    @staticmethod
    def list_joysticks() -> List[dict]:
        """Lists all detected joystick and steering wheel devices."""
        pygame.init()
        pygame.joystick.init()
        devices = []
        count = pygame.joystick.get_count()
        for i in range(count):
            try:
                j = pygame.joystick.Joystick(i)
                j.init()
                devices.append({
                    "index": i,
                    "name": j.get_name(),
                    "num_buttons": j.get_numbuttons(),
                    "guid": j.get_guid()
                })
            except Exception:
                pass
        return devices

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice only when stream is active during button press."""
        if self._is_recording:
            self._active_chunks.append(bytes(indata))

    def _compile_wav(self, chunks: List[bytes]) -> bytes:
        """Converts raw 16-bit PCM chunks into an in-memory WAV byte stream (no disk writes)."""
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b''.join(chunks))
        wav_io.seek(0)
        return wav_io.getvalue()

    def _on_press(self):
        with self._lock:
            if self._is_recording:
                return
            self._is_recording = True
            self._is_holding = False
            self._press_time = time.time()
            self._active_chunks.clear()

            # Start mic stream in case user is holding to speak
            try:
                self._audio_stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_SIZE,
                    dtype='int16',
                    channels=CHANNELS,
                    callback=self._audio_callback
                )
                self._audio_stream.start()
            except Exception as e:
                logger.error(f"Failed to open microphone: {e}")
                self._is_recording = False
                return

        # Delayed thread to confirm hold status and sound the radio open beep
        def hold_detector():
            time.sleep(self.hold_threshold)
            with self._lock:
                if self._is_recording and not self._is_holding:
                    self._is_holding = True
                    logger.radio("Radio OPEN (Hold detected - Speak now...)")
                    if self.beep_feedback and self.beep_callback:
                        self.beep_callback(True)

        threading.Thread(target=hold_detector, name="PTT-HoldDetect", daemon=True).start()

    def _on_release(self):
        duration = 0.0
        is_hold = False
        captured = []

        with self._lock:
            if not self._is_recording:
                return
            self._is_recording = False
            duration = time.time() - self._press_time
            is_hold = self._is_holding

            # Stop mic stream immediately
            if self._audio_stream:
                try:
                    self._audio_stream.stop()
                    self._audio_stream.close()
                except Exception:
                    pass
                self._audio_stream = None

            captured = list(self._active_chunks)
            self._active_chunks.clear()

        # 1. SHORT TAP (< hold threshold): Request Race Update
        if duration < self.hold_threshold and not is_hold:
            logger.radio(f"Button Tapped ({duration*1000:.0f}ms) -> Requesting Race Situation Update...")
            if self.beep_feedback and self.beep_callback:
                self.beep_callback(False)

            if self.on_tap:
                threading.Thread(target=self.on_tap, name="PTT-TapUpdate", daemon=True).start()
            return

        # 2. LONG HOLD (> hold threshold): Driver Voice Radio Call
        audio_len_sec = (len(captured) * BLOCK_SIZE) / SAMPLE_RATE
        logger.radio(f"Radio CLOSED ({audio_len_sec:.1f}s recorded). Processing question...")

        if self.beep_feedback and self.beep_callback:
            self.beep_callback(False)

        if captured and self.on_audio_ready:
            wav_bytes = self._compile_wav(captured)
            threading.Thread(
                target=self.on_audio_ready,
                args=(wav_bytes,),
                name="PTT-Dispatch",
                daemon=True
            ).start()

    def _run_wheel_loop(self):
        """Background loop polling wheel button via Pygame DirectInput with auto-reconnect."""
        pygame.init()
        pygame.joystick.init()

        wheel = None
        button_was_down = False
        last_logged_missing = 0.0

        while self._running:
            if wheel is None:
                # Re-enumerate joysticks periodically until device is found
                try:
                    pygame.joystick.quit()
                    pygame.joystick.init()
                    num_joy = pygame.joystick.get_count()
                    if num_joy > self.joystick_index:
                        wheel = pygame.joystick.Joystick(self.joystick_index)
                        wheel.init()
                        self.wheel_name = wheel.get_name()
                        self.wheel_connected = True
                        logger.info(f"Push-to-Talk active on '{self.wheel_name}' (Button {self.button_index}).")
                        logger.info(f"  * Short tap  (<{self.hold_threshold*1000:.0f}ms): Gives complete Race Update")
                        logger.info(f"  * Hold down (>{self.hold_threshold*1000:.0f}ms): Speak to your race engineer")
                    else:
                        now = time.time()
                        if (now - last_logged_missing) > 30.0:
                            logger.info(f"Waiting for steering wheel on index {self.joystick_index} ({num_joy} detected). Keyboard PTT active.")
                            last_logged_missing = now
                except Exception as e:
                    logger.debug(f"Joystick scan error: {e}")

                if wheel is None:
                    time.sleep(1.5)
                    continue

            pygame.event.pump()
            try:
                button_is_down = bool(wheel.get_button(self.button_index))
            except Exception:
                logger.warning(f"Lost connection to wheel '{self.wheel_name}'. Waiting for device...")
                wheel = None
                self.wheel_connected = False
                button_was_down = False
                time.sleep(1.0)
                continue

            if button_is_down and not button_was_down:
                self._on_press()
            elif not button_is_down and button_was_down:
                self._on_release()

            button_was_down = button_is_down
            time.sleep(0.01)  # 100Hz polling = 10ms response, ~0% CPU

    def _on_kb_press(self, key):
        try:
            name = key.name if hasattr(key, 'name') else str(key)
        except Exception:
            return

        if name.lower() == self.keyboard_key_name:
            self._on_press()

    def _on_kb_release(self, key):
        try:
            name = key.name if hasattr(key, 'name') else str(key)
        except Exception:
            return

        if name.lower() == self.keyboard_key_name:
            self._on_release()

    def start(self):
        """Starts wheel/keyboard button monitoring (Mic only turns on when button pressed)."""
        if self._running:
            return
        self._running = True

        # 1. Wheel setup
        if self.mode in ("auto", "wheel"):
            self._wheel_thread = threading.Thread(
                target=self._run_wheel_loop,
                name="WheelPTT",
                daemon=True
            )
            self._wheel_thread.start()

        # 2. Keyboard fallback listener
        if self.mode in ("auto", "keyboard"):
            try:
                self._kb_listener = keyboard.Listener(
                    on_press=self._on_kb_press,
                    on_release=self._on_kb_release
                )
                self._kb_listener.daemon = True
                self._kb_listener.start()
                logger.info(f"Keyboard PTT active on key '{self.keyboard_key_name}'.")
                logger.info(f"  * Short tap  (<{self.hold_threshold*1000:.0f}ms): Gives complete Race Update")
                logger.info(f"  * Hold down (>{self.hold_threshold*1000:.0f}ms): Speak to your race engineer")
            except Exception as e:
                logger.warning(f"Could not hook keyboard PTT: {e}")

    def stop(self):
        """Stops all PTT monitoring and ensures mic is closed."""
        self._running = False
        with self._lock:
            self._is_recording = False
            if self._audio_stream:
                try:
                    self._audio_stream.stop()
                    self._audio_stream.close()
                except Exception:
                    pass
                self._audio_stream = None

        if self._kb_listener:
            try:
                self._kb_listener.stop()
            except Exception:
                pass

        pygame.quit()
        logger.info("Push-to-Talk manager stopped.")
