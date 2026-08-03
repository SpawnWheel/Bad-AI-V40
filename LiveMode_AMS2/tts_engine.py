import logging
from typing import List, Dict
from google import genai

from .config import LiveModeConfig

logger = logging.getLogger(__name__)

class TTSEngine:
    def __init__(self, config: LiveModeConfig):
        self.config = config
        self.client = None

    def initialize(self):
        self.client = genai.Client(api_key=self.config.gemini_api_key)

    def estimate_duration(self, text: str) -> float:
        """Estimate seconds from word count (~2.5 words/sec)."""
        words = len(text.split())
        return max(1.0, words / 2.5)

    def generate_audio(self, commentary_lines: List[Dict[str, str]]) -> bytes:
        if not commentary_lines:
            return b""

        voice_name = self.config.commentator_voice

        # Build Style Notes
        style_notes = [self.config.commentator_style]
        if self.config.commentator_accent:
            style_notes.append(f"Speak with a {self.config.commentator_accent} accent.")
        if self.config.voice_refinement_text:
            style_notes.append(self.config.voice_refinement_text)
            
        style_note = " ".join(style_notes)

        # Build TTS text
        tts_text_parts = [f"Director's Note: {style_note}\n\n"]
        for line in commentary_lines:
            text = line.get("text", "")
            emotion = line.get("emotion", "")
            
            if not text.strip():
                continue
                
            if emotion and self.config.emotion_tags_enabled:
                tts_text_parts.append(f"[{emotion}] {text}")
            else:
                tts_text_parts.append(text)

        tts_text = "\n".join(tts_text_parts)

        # Truncate text to avoid hitting the TTS model's 1-minute maximum limit (approx 1200 chars)
        MAX_CHARS = 1200
        if len(tts_text) > MAX_CHARS:
            # Find the last sentence boundary before MAX_CHARS
            trunc_point = max(
                tts_text.rfind('.', 0, MAX_CHARS),
                tts_text.rfind('!', 0, MAX_CHARS),
                tts_text.rfind('?', 0, MAX_CHARS)
            )
            if trunc_point == -1:
                trunc_point = MAX_CHARS
            else:
                trunc_point += 1 # Include the punctuation
            
            original_len = len(tts_text)
            tts_text = tts_text[:trunc_point]
            logger.warning(f"Truncated TTS text from {original_len} to {trunc_point} characters to fit API limits.")

        try:
            response = self.client.models.generate_content(
                model=self.config.tts_model,
                contents=tts_text,
                config={
                    'response_modalities': ['AUDIO'],
                    'speech_config': {
                        'voice_config': {
                            'prebuilt_voice_config': {
                                'voice_name': voice_name
                            }
                        }
                    }
                }
            )

            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        return part.inline_data.data

        except Exception as e:
            logger.error(f"Error generating TTS: {e}")

        return b""
