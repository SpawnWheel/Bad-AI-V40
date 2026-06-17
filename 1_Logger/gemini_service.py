import google.generativeai as genai
from google import genai as genai_v2
from google.genai import types
import os
import threading
import time
import json
import queue
import pygame
import pyttsx3
import tempfile
import wave
from io import BytesIO
from settings_manager import settings

# Initialize Pygame Mixer for Audio
try:
    pygame.mixer.init()
    AUDIO_AVAILABLE = True
except Exception as e:
    print(f"Audio init failed: {e}")
    AUDIO_AVAILABLE = False

class GeminiService:
    def __init__(self):
        api_key_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "API_Key.txt")
        file_key = ""
        if os.path.exists(api_key_path):
            with open(api_key_path, "r") as f:
                file_key = f.read().strip()
        self.api_key = os.environ.get("GEMINI_API_KEY") or file_key or settings.get("gemini", "api_key")
        self.model_name = settings.get("gemini", "llm_model", "gemini-3-flash-preview")
        self.thinking_level = settings.get("gemini", "thinking_level", "HIGH")
        self.tts_model = settings.get("gemini", "tts_model", "gemini-2.0-flash-exp") 
        
        self.client_configured = False
        self.v2_client_configured = False
        self.setup_client()
        
        # Audio Queue for Streaming Playback
        self.audio_queue = queue.Queue()
        self.stop_audio_event = threading.Event()
        threading.Thread(target=self._audio_player_loop, daemon=True).start()

        # Initialize pyttsx3 engine
        try:
            self.engine = pyttsx3.init()
        except Exception as e:
            print(f"pyttsx3 init failed: {e}")
            self.engine = None

    def setup_client(self):
        if self.api_key:
            try:
                # V2 Client (Unified for Text & TTS now for modern models)
                self.client_v2 = genai_v2.Client(api_key=self.api_key)
                self.v2_client_configured = True
                
                # Keep V1 for fallback/legacy if needed, but 3.0 requires V2 usually
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel(self.model_name)
                self.client_configured = True
                
                print(f"Gemini Service Configured. Model: {self.model_name}, Thinking: {self.thinking_level}")
            except Exception as e:
                print(f"Failed to configure Gemini: {e}")
                self.client_configured = False
                self.v2_client_configured = False
        else:
            print("Gemini API Key missing.")

    def update_settings(self):
        """Reloads settings and reconfigures if necessary."""
        api_key_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "API_Key.txt")
        new_key = ""
        if os.path.exists(api_key_path):
            with open(api_key_path, "r") as f:
                new_key = f.read().strip()
        new_key = os.environ.get("GEMINI_API_KEY") or new_key or settings.get("gemini", "api_key")
        new_model = settings.get("gemini", "llm_model")
        new_thinking = settings.get("gemini", "thinking_level")
        new_tts_model = settings.get("gemini", "tts_model")
        
        if new_key != self.api_key or new_model != self.model_name or new_thinking != self.thinking_level:
            self.api_key = new_key
            self.model_name = new_model
            self.thinking_level = new_thinking
            self.setup_client()

        if new_tts_model != self.tts_model:
            self.tts_model = new_tts_model

    def list_voices(self):
        """Returns a list of available voices (System + Gemini 2.5)."""
        voices_list = []
        
        # Gemini 2.5 Voices
        gemini_voices = ["Puck", "Charon", "Kore", "Fenrir", "Aoede"]
        for v in gemini_voices:
            voices_list.append(f"Gemini 2.5: {v}")

        # System Voices (pyttsx3)
        if self.engine:
            try:
                system_voices = self.engine.getProperty('voices')
                for v in system_voices:
                    voices_list.append(f"System: {v.name} [{v.id}]")
            except Exception as e:
                print(f"Error listing system voices: {e}")
        
        return voices_list

    def clear_queue(self):
        """
        Clears pending audio clips from the queue.
        Does NOT stop the currently playing sound (allowing it to finish naturally).
        """
        with self.audio_queue.mutex:
            self.audio_queue.queue.clear()
            print("Audio Queue Cleared.")

    def _audio_player_loop(self):
        """
        Background thread to play audio chunks sequentially.
        """
        while True:
            temp_path = None
            try:
                # Get audio data (BytesIO or similar)
                audio_source, is_system = self.audio_queue.get()
                
                if is_system:
                    self._speak_system(audio_source)
                else:
                    # Gemini/Bytes Audio
                    if AUDIO_AVAILABLE:
                        try:
                            # Wait if something is currently playing (serializing)
                            while pygame.mixer.music.get_busy():
                                time.sleep(0.05)

                            raw_data = audio_source.getvalue()
                            is_wav = raw_data.startswith(b'RIFF')
                            
                            # Write to temp file
                            # If Raw PCM, wrap in WAV container
                            if not is_wav:
                                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fp:
                                    temp_path = fp.name
                                    with wave.open(fp, 'wb') as wav_file:
                                        wav_file.setnchannels(1) # Mono
                                        wav_file.setsampwidth(2) # 16-bit
                                        wav_file.setframerate(24000) # Gemini default
                                        wav_file.writeframes(raw_data)
                            else:
                                # Already WAV, just write bytes
                                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fp:
                                    fp.write(raw_data)
                                    temp_path = fp.name
                            
                            print(f"Audio Player: Playing {temp_path}...")
                            pygame.mixer.music.load(temp_path)
                            pygame.mixer.music.play()
                            
                            # Wait for playback to finish
                            while pygame.mixer.music.get_busy():
                                time.sleep(0.05)
                                
                            print("Audio Player: Clip finished.")
                                
                        except Exception as e:
                            print(f"Playback Error: {e}")
                        finally:
                            # Clean up temp file
                            if temp_path and os.path.exists(temp_path):
                                try:
                                    pygame.mixer.music.unload()
                                    os.remove(temp_path)
                                except Exception as cleanup_err:
                                    print(f"Failed to remove temp file: {cleanup_err}")

                self.audio_queue.task_done()
            except Exception as e:
                print(f"Audio Player Error: {e}")
                time.sleep(1)
    def generate_and_speak_stream(self, event_data, text_callback=None):
        """
        Streams text from LLM, buffers sentences, and streams audio to queue.
        """
        if not self.v2_client_configured:
            return

        prompt = self._construct_prompt(event_data)
        
        level_map = {
            "HIGH": types.ThinkingLevel.HIGH,
            "MEDIUM": types.ThinkingLevel.MEDIUM,
            "LOW": types.ThinkingLevel.LOW,
            "MINIMAL": types.ThinkingLevel.MINIMAL
        }
        selected_level = level_map.get(self.thinking_level.upper(), types.ThinkingLevel.HIGH)

        full_text = ""
        sentence_buffer = ""
        
        try:
            # STREAMING REQUEST
            response_stream = self.client_v2.models.generate_content_stream(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_level=selected_level
                    )
                )
            )
            
            for chunk in response_stream:
                if chunk.text:
                    text_chunk = chunk.text
                    full_text += text_chunk
                    sentence_buffer += text_chunk
                    
                    if text_callback:
                        text_callback(full_text)
                    
                    # Simple sentence detection
                    if any(punct in text_chunk for punct in ['.', '!', '?', '\n']):
                        # Split by punctuation to find complete sentences
                        import re
                        sentences = re.split(r'(?<=[.!?])\s+', sentence_buffer)
                        
                        # The last part might be incomplete
                        if len(sentences) > 1:
                            # We have complete sentences
                            for s in sentences[:-1]:
                                if s.strip():
                                    self.speak(s.strip(), queue_only=True)
                            
                            # Keep the remainder
                            sentence_buffer = sentences[-1]
                        elif sentence_buffer.strip().endswith(('.', '!', '?')):
                             # Buffer is exactly one sentence
                             self.speak(sentence_buffer.strip(), queue_only=True)
                             sentence_buffer = ""

            # Flush remaining buffer
            if sentence_buffer.strip():
                self.speak(sentence_buffer.strip(), queue_only=True)
                
        except Exception as e:
            print(f"LLM Streaming Error: {e}")
            # Fallback
            self.speak(event_data.get('message', ''), queue_only=True)

    def generate_commentary(self, event_data):
        """
        Legacy/Blocking method. Kept for compatibility or fallback.
        """
        # Re-implemented to use the blocking call but we should prefer streaming now.
        if not self.v2_client_configured:
            return event_data.get('message', '')

        prompt = self._construct_prompt(event_data)
        level_map = {"HIGH": types.ThinkingLevel.HIGH, "MEDIUM": types.ThinkingLevel.MEDIUM, "LOW": types.ThinkingLevel.LOW, "MINIMAL": types.ThinkingLevel.MINIMAL}
        selected_level = level_map.get(self.thinking_level.upper(), types.ThinkingLevel.HIGH)

        try:
            config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level=selected_level)
            ) if "gemini-3" in self.model_name.lower() else None

            response = self.client_v2.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config
            )
            return response.text.strip() if response.text else event_data.get('message', '')
        except Exception as e:
            print(f"LLM Gen Error: {e}")
            return event_data.get('message', '')

    def _construct_prompt(self, event):
        category = event.get('category', 'Generic')
        message = event.get('message', '')
        base_prompt = settings.get("gemini", "persona_prompt", 
            "You are an exciting motorsport commentator. Generate a SINGLE, short, enthusiastic sentence (under 20 words).")
        return f"{base_prompt}\n\nEvent Category: {category}\nEvent Details: {message}\nCommentary:"

    def speak(self, text, queue_only=False):
        """
        Converts text to speech.
        If queue_only=True, it adds to the background audio queue.
        If False, it might block or queue depending on implementation (now defaulting to queue for consistency).
        """
        if not settings.get("gemini", "tts_enabled", True):
            print(f"TTS Disabled: {text}")
            return

        print(f"Queueing Audio: {text}")
        voice_id = settings.get("gemini", "voice_id", "")
        voice_engine = settings.get("gemini", "voice_engine", "system")
        
        if voice_engine == "gemini" or "Gemini" in voice_id:
            # Generate Audio Data (Blocking the generation thread, but not the UI thread if called from thread)
            self._generate_gemini_audio_and_queue(text, voice_id)
        else:
            # System TTS
            self.audio_queue.put((text, True))

    def _generate_gemini_audio_and_queue(self, text, voice_choice):
        if not self.v2_client_configured:
            return

        # Extract voice name (Handle "Gemini 2.5: Puck" or just "Puck")
        if ": " in voice_choice:
             voice_name = voice_choice.split(": ")[1]
        else:
             voice_name = voice_choice
        
        try:
            # FIXED: response_modalities=["AUDIO"]
            response = self.client_v2.models.generate_content(
                model=self.tts_model, 
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"], # Fixed Plural
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name))
                    )
                )
            )
            
            audio_data = None
            if hasattr(response, 'candidates') and response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.inline_data:
                        audio_data = part.inline_data.data
                        break
            
            if audio_data:
                print(f"Gemini TTS: Received audio data ({len(audio_data)} bytes)")
                print(f"Header: {audio_data[:10]}")
                mp3_fp = BytesIO(audio_data)
                self.audio_queue.put((mp3_fp, False))
            else:
                print("Gemini TTS: No audio data.")

        except Exception as e:
            print(f"Gemini TTS Exception: {e}")

    def _speak_system(self, text):
        if not self.engine: return
        try:
            voice_id_str = settings.get("gemini", "voice_id", "")
            if voice_id_str and "[" in voice_id_str:
                clean_id = voice_id_str.split("[")[1].split("]")[0]
                self.engine.setProperty('voice', clean_id)
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as e:
            print(f"System TTS Error: {e}")

# Global Instance
gemini_service = GeminiService()
