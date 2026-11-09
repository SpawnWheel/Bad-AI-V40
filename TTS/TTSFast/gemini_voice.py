"""Designs voices with Gemini 3.8 Flash TTS and records sample lines with them.

Qwen3-TTS VoiceDesign falls back to an American accent whatever it is asked for, so new voices are designed with
Gemini (the same Voice Design the race engineer uses), which keeps accents. The design gives a stored Gemini voice
id; the sample line spoken with it becomes the reference clip that Qwen clones in the commentary, so the voice
and its accent stay the same in every race. The voice id is kept in the voice's .voice.json, so the same voice
can record a new sample line later.

The API key comes from GEMINI_API_KEY (the launcher passes it) or the launcher's settings file.
"""
import base64
import io
import json
import os
import wave

import numpy as np

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TTS_MODEL = "gemini-3.8-flash-tts"
SAMPLE_RATE = 24000


def api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        with open(os.path.join(APP_DIR, "launcher_config.json"), "r", encoding="utf-8") as f:
            return (json.load(f).get("global_settings") or {}).get("gemini_api_key") or None
    except (OSError, ValueError):
        return None


def _client(timeout_ms):
    from google import genai
    from google.genai import types
    key = api_key()
    if not key:
        raise RuntimeError("No Gemini API key: add it in the launcher's Settings.")
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))


def _decode(data):
    """Gemini audio (base64 or bytes, WAV or raw 16-bit PCM) -> float32 mono at SAMPLE_RATE."""
    if data is None:
        return None
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if not raw:
        return None
    sr = SAMPLE_RATE
    if raw[:4] == b"RIFF":
        with wave.open(io.BytesIO(raw), "rb") as wf:
            sr, ch = wf.getframerate(), wf.getnchannels()
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1)
    else:
        audio = np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype=np.int16)
    audio = audio.astype(np.float32) / 32768.0
    if sr != SAMPLE_RATE and len(audio) > 1:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(SAMPLE_RATE, sr)
        audio = resample_poly(audio, SAMPLE_RATE // g, sr // g).astype(np.float32)
    return audio


def _interaction_audio(interaction):
    out = getattr(interaction, "output_audio", None)
    if out is not None and getattr(out, "data", None):
        return out.data
    for attr in ("steps", "outputs"):
        for step in getattr(interaction, attr, None) or []:
            contents = getattr(step, "content", None) or getattr(step, "contents", None) or [step]
            for c in contents if isinstance(contents, list) else [contents]:
                if getattr(c, "type", None) == "audio" and getattr(c, "data", None):
                    return c.data
    return None


def design_voice(description, gender, accent, language_code, display_name):
    """Creates and stores a Gemini voice from the description. Returns (voice id, its own sample audio or None)."""
    voice = {"type": "prompted", "model": TTS_MODEL, "display_name": display_name[:60], "gender": gender,
             "language_code": language_code, "prompted": {"input": description}}
    if accent:
        voice["accent"] = accent
    client = _client(180_000)  # keep a reference: a dropped client closes its connection mid-request
    created = client.voices.create(store=True, voice=voice)
    voice_id = getattr(created, "id", None)
    if not voice_id:
        raise RuntimeError("Gemini returned no voice id.")
    sample = getattr(created, "sample_audio", None)
    return voice_id, _decode(getattr(sample, "data", None) if sample is not None else None)


def speak(voice_id, text, language_code, style="Natural, conversational delivery, close to the microphone."):
    """The text spoken with a stored Gemini voice, as float32 mono at SAMPLE_RATE."""
    client = _client(120_000)
    kwargs = dict(
        model=TTS_MODEL,
        input=[{"type": "user_input", "content": [{
            "type": "text", "text": text, "annotations": [{"type": "speech_metadata", "style": style}]}]}],
        response_format={"type": "audio", "mime_type": "audio/wav", "sample_rate": SAMPLE_RATE},
        generation_config={"speech_config": [{"voice": voice_id, "language": language_code}]},
        store=False,
    )
    try:
        interaction = client.interactions.create(**kwargs)
    except Exception as e:
        if "store" not in str(e).lower():
            raise
        kwargs.pop("store")
        interaction = client.interactions.create(**kwargs)
    audio = _decode(_interaction_audio(interaction))
    if audio is None or not len(audio):
        raise RuntimeError("Gemini returned no audio.")
    return audio
