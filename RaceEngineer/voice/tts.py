"""
Gemini 3.8 Flash TTS integration for the AMS2 race engineer.

What changed vs. the 3.1 preview integration
--------------------------------------------
* 3.8 TTS runs on the Interactions API (client.interactions.create), not generate_content.
* The input text is treated as a *verbatim transcript*: anything in it is spoken.
  Director's notes / "[urgent]" style tags must NOT go in the text any more.
* Per-line emotion & delivery go in a `speech_metadata` annotation (`style` field).
* Point-in-time vocal sounds use angle-bracket events inside the transcript:
  <sigh>, <breath>, <exhales>, <short pause>, <tsk>, ...
* Accent / age / timbre are best fixed once with Voice Design (a custom voice id),
  so the engineer sounds like the same person on every call.

This module:
  1. Designs (once) and caches a custom voice for the active persona.
  2. Converts the brain's EMOTION + DELIVERY (+ any legacy [tags]) into a style direction.
  3. Sanitises the transcript so only real speech + allowed vocal events are sent.
  4. Falls back 3.8 Flash -> 3.8 Flash-Lite -> legacy 3.1 preview (generate_content) on errors/quota.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import wave
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

import numpy as np
from google import genai
from google.genai import types

from utils.logging import logger

try:  # persona is optional so the module still works standalone
    from engineer.persona import Persona, VoiceSettings
except Exception:  # pragma: no cover
    Persona = None  # type: ignore
    VoiceSettings = None  # type: ignore

TTS_SAMPLE_RATE = 24000  # Playback rate. Gemini TTS returns 24 kHz mono 16-bit.

DEFAULT_TTS_MODEL = "gemini-3.8-flash-tts"
DEFAULT_FALLBACK_MODELS = ["gemini-3.8-flash-lite-tts"]
DEFAULT_LEGACY_MODEL = "gemini-3.1-flash-tts-preview"

DEFAULT_ALLOWED_EVENTS = ["breath", "exhales", "sigh", "tsk", "short pause", "long pause", "throat-clearing"]

# Legacy square-bracket tags (3.1-era prompts) -> 3.8 inline vocal events
_LEGACY_TAG_TO_EVENT = {
    "sigh": "sigh", "sighs": "sigh",
    "pause": "short pause", "short pause": "short pause", "long pause": "long pause", "beat": "short pause",
    "breath": "breath", "breathes": "breath", "inhales": "breath", "deep breath": "heavy breath",
    "exhale": "exhales", "exhales": "exhales",
    "tsk": "tsk", "clears throat": "throat-clearing", "throat-clearing": "throat-clearing",
    "gasp": "gasp", "gasps": "gasp",
    "laugh": "laugh", "laughs": "laugh", "chuckle": "chuckle", "chuckles": "chuckle",
    "cheer": "cheer", "cheers": "cheer",
    "groan": "groan", "groans": "groan",
}
# Legacy tags that are really *emotions/deliveries* -> fold into the style direction instead
_LEGACY_TAG_TO_STYLE = {
    "urgent": "urgent", "calm": "calm", "focused": "focused", "excited": "excited",
    "relieved": "relieved", "shouts": "raised voice, almost shouting", "shouting": "raised voice",
    "whispers": "hushed, close to the mic", "whispering": "hushed", "serious": "serious",
    "frustrated": "frustrated", "annoyed": "mildly annoyed", "happy": "happy", "proud": "proud",
    "sarcastic": "sarcastic", "deadpan": "deadpan", "tense": "tense", "warm": "warm",
}

_EXPRESSIVENESS_NOTE = {
    "subtle": "Keep the emotion understated and controlled; a professional who rarely lets it show.",
    "natural": "Let the emotion come through naturally, like a real person on the radio.",
    "expressive": "Let the emotion come through clearly and vividly; he wears his heart on his sleeve.",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "voice"


class GeminiTTS:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = DEFAULT_TTS_MODEL,
        voice_name: str = "Charon",
        accent: str = "British",
        gender: str = "male",
        delivery: str = "measured, dry, and focused pit-wall delivery",
        persona: Optional["Persona"] = None,
        fallback_models: Optional[List[str]] = None,
        legacy_model: Optional[str] = DEFAULT_LEGACY_MODEL,
        voice_cache_path: str = "data/voice_cache.json",
        voice_samples_dir: str = "data/voice_samples",
        radio_context: bool = True,
        request_timeout_sec: float = 20.0,
        stream_first_audio_timeout_sec: float = 6.0,
        stream_stall_timeout_sec: float = 4.0,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.persona = persona

        # Persona wins over loose kwargs when present
        self.accent = persona.accent if persona else accent
        self.gender = persona.gender if persona else gender
        self.delivery = persona.baseline_delivery if persona else delivery
        self.age = persona.age if persona else ""
        self.language_code = persona.language_code if persona else "en-GB"
        self.expressiveness = persona.expressiveness if persona else "natural"
        self.allowed_events = set(persona.allowed_vocal_events if persona else DEFAULT_ALLOWED_EVENTS)

        voice_cfg = persona.voice if persona else None
        self.voice_mode = (voice_cfg.mode if voice_cfg else "prebuilt").lower()
        self.voice_name = (voice_cfg.prebuilt if voice_cfg and voice_cfg.prebuilt else voice_name)
        self._explicit_voice_id = (voice_cfg.voice_id if voice_cfg else "") or ""

        self.model_name = model_name
        self.primary_model = model_name
        self.fallback_models = list(fallback_models) if fallback_models is not None else list(DEFAULT_FALLBACK_MODELS)
        self.fallback_models = [m for m in self.fallback_models if m and m != model_name]
        self.legacy_model = legacy_model or None
        # Back-compat attribute (older code/tests referenced it)
        self.fallback_model = self.fallback_models[0] if self.fallback_models else (self.legacy_model or model_name)

        self.radio_context = radio_context
        self.request_timeout_sec = float(request_timeout_sec)
        self.stream_first_audio_timeout = float(stream_first_audio_timeout_sec)
        self.stream_stall_timeout = float(stream_stall_timeout_sec)
        self.voice_cache_path = Path(voice_cache_path)
        self.voice_samples_dir = Path(voice_samples_dir)

        self._exhausted_models: set[str] = set()
        self._stream_failures = 0
        self._voice_id: Optional[str] = None
        self._voice_lock = threading.Lock()
        self._interactions_store_supported = True
        self._quota_exceeded_for_session = False
        self._current_model = self.primary_model
        self.client: Optional[genai.Client] = None

        if self.api_key:
            try:
                # Timeout (ms) so a stalled HTTP stream can never hang the radio forever
                self.client = genai.Client(
                    api_key=self.api_key,
                    http_options=types.HttpOptions(timeout=int(self.request_timeout_sec * 1000)),
                )
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client for TTS: {e}")

    # ================================================================ session
    def reset_session(self):
        """New session: give the primary model another chance after quota fallbacks."""
        if self._exhausted_models:
            logger.info(f"New session detected: restoring TTS model '{self.primary_model}'.")
        self._exhausted_models.clear()
        self._stream_failures = 0
        self._quota_exceeded_for_session = False
        self._current_model = self.primary_model

    # ================================================================ voice
    def _voice_signature(self) -> str:
        p = self.persona
        payload = json.dumps({
            "desc": p.voice_design_prompt() if p else "",
            "gender": self.gender, "lang": self.language_code, "accent": self.accent,
        }, sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _load_voice_cache(self) -> dict:
        try:
            if self.voice_cache_path.exists():
                return json.loads(self.voice_cache_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning(f"Could not read voice cache {self.voice_cache_path}: {e}")
        return {}

    def _save_voice_cache(self, cache: dict):
        try:
            self.voice_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.voice_cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not write voice cache {self.voice_cache_path}: {e}")

    def _invalidate_cached_voice(self):
        cache = self._load_voice_cache()
        sig = self._voice_signature()
        if sig in cache:
            cache.pop(sig, None)
            self._save_voice_cache(cache)
        self._voice_id = None

    def design_voice(self, force: bool = False) -> Optional[str]:
        """
        Creates the persona's custom voice with Gemini Voice Design (store=True) and caches the id
        in data/voice_cache.json, keyed by the voice description. Changing accent / description in
        config.yaml produces a new signature, so a new voice is designed automatically.
        Returns the voice id, or None on failure.
        """
        if not self.client or not self.persona:
            return None
        sig = self._voice_signature()
        cache = self._load_voice_cache()
        if not force and sig in cache and cache[sig].get("id"):
            return cache[sig]["id"]

        p = self.persona
        prompt = p.voice_design_prompt()
        logger.info(f"Designing Gemini 3.8 voice for persona '{p.display_name}' ({self.accent}) ... one-off, cached afterwards.")
        try:
            voice_req: dict = {
                "type": "prompted",
                "model": self.primary_model if "3.8" in self.primary_model else DEFAULT_TTS_MODEL,
                "display_name": f"AMS2 Engineer - {p.display_name}"[:60],
                "gender": self.gender,
                "language_code": self.language_code,
                "prompted": {"input": prompt},
            }
            if self.accent:
                voice_req["accent"] = self.accent
            created = self.client.voices.create(store=True, voice=voice_req)
            voice_id = getattr(created, "id", None)
            if not voice_id:
                logger.error("Voice design returned no id; falling back to prebuilt voice.")
                return None

            cache[sig] = {
                "id": voice_id,
                "persona": p.key,
                "display_name": p.display_name,
                "description": prompt,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._save_voice_cache(cache)

            sample = getattr(created, "sample_audio", None)
            sample_data = getattr(sample, "data", None) if sample is not None else None
            if sample_data:
                try:
                    self.voice_samples_dir.mkdir(parents=True, exist_ok=True)
                    raw = base64.b64decode(sample_data) if isinstance(sample_data, str) else bytes(sample_data)
                    out = self.voice_samples_dir / f"{_slug(p.key)}_{sig}.wav"
                    out.write_bytes(raw)
                    logger.info(f"Voice sample saved: {out}")
                except Exception as e:
                    logger.debug(f"Could not save voice sample: {e}")

            logger.info(f"Custom voice ready: {voice_id}")
            return voice_id
        except Exception as e:
            logger.error(f"Voice design failed ({e}). Using prebuilt voice '{self.voice_name}' with accent steering instead.")
            return None

    def ensure_voice(self) -> str:
        """Resolves the voice to synthesise with (designed id, explicit id, or prebuilt name)."""
        if self._voice_id:
            return self._voice_id
        with self._voice_lock:
            if self._voice_id:
                return self._voice_id
            voice = None
            if self.voice_mode == "voice_id" and self._explicit_voice_id:
                voice = self._explicit_voice_id
            elif self.voice_mode == "designed":
                voice = self.design_voice()
            if not voice:
                voice = self.voice_name
                if self.voice_mode == "designed":
                    self.voice_mode = "prebuilt"  # so the style carries the accent from now on
            self._voice_id = voice
            return voice

    @property
    def uses_custom_voice(self) -> bool:
        v = self._voice_id or ""
        return v.startswith("voice_") or v.startswith("voicekey_")

    # ================================================================ text
    def prepare_transcript(self, text: str) -> Tuple[str, List[str]]:
        """
        Turns the brain's radio line into a clean 3.8 transcript.
        Returns (transcript, style_cues). Legacy [emotion] tags become style cues; legacy
        [sighs]-style tags become <sigh> events; unsupported/banned events are dropped.
        """
        cues: List[str] = []
        s = (text or "").strip()

        def legacy_sub(m: re.Match) -> str:
            tag = m.group(1).strip().lower()
            if tag in _LEGACY_TAG_TO_EVENT:
                ev = _LEGACY_TAG_TO_EVENT[tag]
                return f" <{ev}> " if ev in self.allowed_events else " "
            if tag in _LEGACY_TAG_TO_STYLE:
                cues.append(_LEGACY_TAG_TO_STYLE[tag])
            return " "

        # [urgent] / [sighs] ...
        s = re.sub(r"\[([a-zA-Z][a-zA-Z \-]{0,24})\]", legacy_sub, s)
        # *sighs* / (sighs) stage directions
        s = re.sub(r"\*([a-zA-Z][a-zA-Z \-]{0,24})\*", legacy_sub, s)
        s = re.sub(r"\((sighs?|laughs?|chuckles?|exhales?|pause|breathes?|clears throat)\)", legacy_sub, s, flags=re.IGNORECASE)

        def event_sub(m: re.Match) -> str:
            raw = m.group(1).strip().lower()
            ev = _LEGACY_TAG_TO_EVENT.get(raw, raw)
            if ev in self.allowed_events:
                return f" <{ev}> "
            if raw in _LEGACY_TAG_TO_STYLE:
                cues.append(_LEGACY_TAG_TO_STYLE[raw])
            return " "

        s = re.sub(r"<([a-zA-Z][a-zA-Z \-]{0,24})>", event_sub, s)

        # Collapse runs of events (max 1 in a row) and whitespace
        s = re.sub(r"(<[^>]+>)(\s*<[^>]+>)+", r"\1", s)
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"\s+([,.!?;:])", r"\1", s)
        # Drop a lone trailing event after the last sentence (sounds odd over radio)
        s = re.sub(r"\s*<(short pause|long pause)>\s*$", "", s)

        s = self._normalise_for_speech(s)
        return s, cues

    @staticmethod
    def _normalise_for_speech(s: str) -> str:
        """Small fixes so numbers read the way an engineer would say them."""
        # 1.4s -> 1.4 seconds ; 1s -> 1 second
        s = re.sub(r"\b(\d+(?:\.\d+)?)\s?s\b(?![a-z])", lambda m: f"{m.group(1)} {'second' if m.group(1) == '1' else 'seconds'}", s)
        # "~" approx
        s = s.replace("~", "about ")
        # Strip markdown emphasis
        s = s.replace("**", "").replace("__", "")
        return s

    def build_style(
        self,
        emotion: Optional[str] = None,
        delivery: Optional[str] = None,
        cues: Optional[List[str]] = None,
    ) -> str:
        """Builds the speech_metadata.style direction for one radio call."""
        emotion = (emotion or "").strip().rstrip(".")
        delivery = (delivery or self.delivery or "").strip().rstrip(".")
        parts: List[str] = []

        mood_bits = [b for b in [emotion] + list(cues or []) if b]
        if mood_bits:
            # de-dup while keeping order
            seen, uniq = set(), []
            for b in mood_bits:
                if b.lower() not in seen:
                    seen.add(b.lower())
                    uniq.append(b)
            parts.append(", ".join(uniq).capitalize() + ".")
        if delivery:
            parts.append(delivery[0].upper() + delivery[1:] + ".")

        parts.append(_EXPRESSIVENESS_NOTE.get(self.expressiveness, _EXPRESSIVENESS_NOTE["natural"]))

        g = (self.gender or "").lower()
        pron = "his" if g == "male" else "her" if g == "female" else "their"
        if not self.uses_custom_voice and self.accent:
            who = f"A {g + ' ' if g in ('male', 'female') else ''}race engineer"
            if self.age:
                who += f" in {pron} {self.age}"
            parts.append(f"{who}, with a natural {self.accent} accent.")
        if self.radio_context:
            parts.append(f"Speaking to {pron} driver over pit-to-car team radio, headset mic, pit wall in the background.")
        return " ".join(parts)

    def build_synthesis_prompt(
        self,
        text: str,
        emotion: Optional[str] = None,
        delivery: Optional[str] = None
    ) -> str:
        """
        LEGACY (3.1 / 2.5 preview, generate_content) director prompt. Only used by the legacy
        fallback path -- 3.8 models would read these notes aloud.
        """
        clean_text = text.strip().replace('"', '')
        emotion_desc = emotion or "relaxed, witty, and calm"
        effective_delivery = delivery if delivery else self.delivery

        return (
            f"Synthesize speech for the performance defined below. The profile, scene, performance notes, and context are direction only. Do NOT speak them.\n"
            f"Audio Profile: {self.accent} {self.gender} race engineer chatting with driver over team radio ({self.delivery}).\n"
            f"Scene: High-stakes live motorsport racing session over pit-to-car radio.\n"
            f"Director's Notes: Delivery: {effective_delivery}. Vocal emotion: {emotion_desc}.\n"
            f"#### TRANSCRIPT\n"
            f"{clean_text}"
        )

    # ================================================================ audio decode
    @staticmethod
    def _decode_audio(data: Any) -> Optional[np.ndarray]:
        if data is None:
            return None
        if isinstance(data, str):
            try:
                raw = base64.b64decode(data)
            except Exception:
                return None
        else:
            raw = bytes(data)
        if not raw:
            return None

        if raw[:4] == b"RIFF":
            with wave.open(io.BytesIO(raw), "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())
            if sw != 2:
                logger.warning(f"Unexpected TTS sample width {sw}; attempting to continue.")
            audio = np.frombuffer(frames, dtype=np.int16)
            if ch > 1:
                audio = audio.reshape(-1, ch).mean(axis=1).astype(np.int16)
        else:
            sr = TTS_SAMPLE_RATE
            audio = np.frombuffer(raw[: len(raw) - (len(raw) % 2)], dtype=np.int16)

        if sr != TTS_SAMPLE_RATE and len(audio) > 1:
            n_out = int(len(audio) * TTS_SAMPLE_RATE / sr)
            x_old = np.linspace(0, 1, len(audio), endpoint=False)
            x_new = np.linspace(0, 1, n_out, endpoint=False)
            audio = np.interp(x_new, x_old, audio.astype(np.float32)).astype(np.int16)
        return audio

    @staticmethod
    def _extract_interaction_audio(interaction: Any) -> Any:
        out = getattr(interaction, "output_audio", None)
        if out is not None and getattr(out, "data", None):
            return out.data
        # Defensive: walk steps/outputs for an audio content block
        for attr in ("steps", "outputs"):
            for step in getattr(interaction, attr, None) or []:
                contents = getattr(step, "content", None) or getattr(step, "contents", None) or [step]
                if not isinstance(contents, list):
                    contents = [contents]
                for c in contents:
                    if getattr(c, "type", None) == "audio" and getattr(c, "data", None):
                        return c.data
        return None

    # ================================================================ synthesis
    @staticmethod
    def _is_quota_error(e: Exception) -> bool:
        s = str(e)
        return (
            getattr(e, "code", None) == 429 or "429" in s or "RESOURCE_EXHAUSTED" in s
            or "quota" in s.lower() or "rate limit" in s.lower()
        )

    @staticmethod
    def _is_voice_missing_error(e: Exception) -> bool:
        s = str(e).lower()
        return ("voice" in s) and ("not found" in s or "404" in s or "invalid" in s or "expired" in s)

    @staticmethod
    def _is_legacy_model(model_id: str) -> bool:
        m = model_id.lower()
        return m.startswith("gemini-2") or "-preview-tts" in m or m.endswith("-tts-preview")

    def _interaction_kwargs(self, model_id: str, transcript: str, style: str, voice: str, mime: str) -> dict:
        kwargs = dict(
            model=model_id,
            input=[{
                "type": "user_input",
                "content": [{
                    "type": "text",
                    "text": transcript,
                    "annotations": [{"type": "speech_metadata", "style": style}],
                }],
            }],
            response_format={"type": "audio", "mime_type": mime, "sample_rate": TTS_SAMPLE_RATE},
            generation_config={"speech_config": [{"voice": voice, "language": self.language_code}]},
        )
        if self._interactions_store_supported:
            kwargs["store"] = False
        return kwargs

    def _create_interaction(self, kwargs: dict):
        try:
            return self.client.interactions.create(**kwargs)
        except Exception as e:
            if self._interactions_store_supported and "store" in str(e).lower():
                self._interactions_store_supported = False
                kwargs.pop("store", None)
                return self.client.interactions.create(**kwargs)
            raise

    def _synthesize_interactions(self, model_id: str, transcript: str, style: str, voice: str) -> Optional[np.ndarray]:
        interaction = self._create_interaction(self._interaction_kwargs(model_id, transcript, style, voice, "audio/wav"))
        return self._decode_audio(self._extract_interaction_audio(interaction))

    def _synthesize_legacy(self, model_id: str, text: str, emotion: Optional[str], delivery: Optional[str]) -> Optional[np.ndarray]:
        plain = re.sub(r"<[^>]+>", " ", text)
        prompt = self.build_synthesis_prompt(re.sub(r"\s+", " ", plain), emotion=emotion, delivery=delivery)
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            ),
        )
        response = self.client.models.generate_content(model=model_id, contents=prompt, config=config)
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    return np.frombuffer(part.inline_data.data, dtype=np.int16)
        return None

    def synthesize(
        self,
        text: str,
        emotion: Optional[str] = None,
        delivery: Optional[str] = None
    ) -> Optional[np.ndarray]:
        """
        Synthesises one radio call. Returns 24 kHz int16 mono numpy audio, or None on failure.
        """
        if not self.client:
            logger.warning("No Gemini API key available for TTS synthesis.")
            return None

        transcript, cues = self.prepare_transcript(text)
        if not re.sub(r"<[^>]+>", "", transcript).strip():
            return None

        voice = self.ensure_voice()
        style = self.build_style(emotion, delivery, cues)
        logger.debug(f"TTS style: {style}")

        chain = [self.primary_model] + self.fallback_models
        if self.legacy_model and self.legacy_model not in chain:
            chain.append(self.legacy_model)
        chain = [m for m in chain if m not in self._exhausted_models] or [chain[-1]]

        retried_voice = False
        for model_id in chain:
            try:
                if self._is_legacy_model(model_id):
                    audio = self._synthesize_legacy(model_id, text, emotion, delivery)
                else:
                    audio = self._synthesize_interactions(model_id, transcript, style, voice)
                if audio is not None and len(audio):
                    self._current_model = model_id
                    return audio
                logger.warning(f"TTS model '{model_id}' returned no audio.")
            except Exception as e:
                if self._is_quota_error(e):
                    logger.warning(f"TTS quota/rate limit on '{model_id}' ({e}). Skipping it for the rest of this session.")
                    self._exhausted_models.add(model_id)
                    if model_id == self.primary_model:
                        self._quota_exceeded_for_session = True
                    continue
                if self.uses_custom_voice and not retried_voice and self._is_voice_missing_error(e):
                    retried_voice = True
                    logger.warning(f"Custom voice '{voice}' unavailable ({e}). Re-designing voice.")
                    self._invalidate_cached_voice()
                    voice = self.ensure_voice()
                    style = self.build_style(emotion, delivery, cues)
                    try:
                        audio = self._synthesize_interactions(model_id, transcript, style, voice)
                        if audio is not None and len(audio):
                            return audio
                    except Exception as e2:
                        logger.error(f"TTS retry failed on '{model_id}': {e2}")
                    continue
                logger.error(f"TTS synthesis failed with model '{model_id}': {e}")
                continue
        return None

    # ================================================================ streaming
    @staticmethod
    def _audio_from_event(event) -> Optional[Any]:
        """Returns the audio payload of a streaming event, or None. Tolerates unknown/raw variants."""
        etype = getattr(event, "event_type", None)
        if etype == "error":
            err = getattr(event, "error", None)
            raise RuntimeError(f"TTS stream error: {getattr(err, 'message', err)}")
        delta = getattr(event, "delta", None)
        if delta is None:
            raw = getattr(event, "raw", None)
            if isinstance(raw, dict):
                if raw.get("event_type") == "error":
                    raise RuntimeError(f"TTS stream error: {raw.get('error')}")
                d = raw.get("delta") or {}
                if isinstance(d, dict) and d.get("type") == "audio":
                    return d.get("data")
            return None
        if getattr(delta, "type", None) == "audio":
            return getattr(delta, "data", None)
        return None

    def _stream_interactions(self, model_id: str, transcript: str, style: str, voice: str,
                             cancel: Optional[threading.Event] = None) -> Iterator[np.ndarray]:
        """
        Yields int16 PCM chunks from a streaming 3.8 TTS interaction as they arrive.
        The HTTP stream is read on a helper thread with a watchdog: if no audio arrives within
        stream_first_audio_timeout, or the stream stalls for stream_stall_timeout, it raises
        TimeoutError so the caller can fall back instead of leaving the engineer silent.
        """
        import queue as _queue
        kwargs = self._interaction_kwargs(model_id, transcript, style, voice, "audio/l16")
        kwargs["stream"] = True
        q: "_queue.Queue" = _queue.Queue()
        holder: dict = {}
        stop = threading.Event()
        t_req = time.time()

        def reader():
            try:
                stream = self._create_interaction(kwargs)
                holder["stream"] = stream
                n_events = 0
                for event in stream:
                    n_events += 1
                    if stop.is_set():
                        break
                    data = self._audio_from_event(event)
                    if data:
                        q.put(("audio", data))
                holder["events"] = n_events
                q.put(("done", None))
            except Exception as e:  # propagate to consumer
                q.put(("error", e))

        th = threading.Thread(target=reader, name=f"TTS-stream-{model_id}", daemon=True)
        th.start()

        carry = b""
        header_checked = False
        got_audio = False
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    return
                timeout = self.stream_stall_timeout if got_audio else self.stream_first_audio_timeout
                try:
                    kind, val = q.get(timeout=timeout)
                except _queue.Empty:
                    what = "stalled mid-line" if got_audio else "sent no audio"
                    raise TimeoutError(f"TTS stream on '{model_id}' {what} for {timeout:.0f}s")
                if kind == "error":
                    raise val
                if kind == "done":
                    if not got_audio:
                        logger.warning(f"TTS stream on '{model_id}' finished with no audio "
                                       f"({holder.get('events', 0)} events).")
                    return
                raw = base64.b64decode(val) if isinstance(val, str) else bytes(val)
                if not header_checked:
                    header_checked = True
                    logger.debug(f"TTS stream '{model_id}': first audio after {time.time() - t_req:.2f}s")
                    if raw[:4] == b"RIFF":  # server sent a WAV header anyway -> skip it
                        idx = raw.find(b"data")
                        raw = raw[idx + 8:] if idx >= 0 else raw[44:]
                raw = carry + raw
                usable = len(raw) - (len(raw) % 2)
                carry = raw[usable:]
                if usable:
                    got_audio = True
                    yield np.frombuffer(raw[:usable], dtype="<i2").astype(np.int16)
        finally:
            stop.set()
            close = getattr(holder.get("stream"), "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def synthesize_stream(
        self,
        text: str,
        emotion: Optional[str] = None,
        delivery: Optional[str] = None,
        cancel: Optional[threading.Event] = None,
    ) -> Iterator[np.ndarray]:
        """
        Streaming counterpart of synthesize(): yields 24 kHz int16 chunks as soon as Gemini
        produces them. Same voice, style and transcript handling as synthesize(), so it sounds
        identical -- it just starts sooner. Falls back model-by-model (and finally to the
        non-streaming path) if a stream fails before any audio was produced.
        """
        if not self.client:
            logger.warning("No Gemini API key available for TTS synthesis.")
            return
        transcript, cues = self.prepare_transcript(text)
        if not re.sub(r"<[^>]+>", "", transcript).strip():
            return

        voice = self.ensure_voice()
        style = self.build_style(emotion, delivery, cues)
        chain = [m for m in [self.primary_model] + self.fallback_models
                 if m not in self._exhausted_models and not self._is_legacy_model(m)]
        if self._stream_failures >= 2:
            chain = []  # streaming keeps failing this session -> go straight to the reliable path

        for model_id in chain:
            produced = False
            try:
                for chunk in self._stream_interactions(model_id, transcript, style, voice, cancel):
                    produced = True
                    yield chunk
                if produced:
                    self._current_model = model_id
                    self._stream_failures = 0
                    return
                if cancel is not None and cancel.is_set():
                    return
                logger.warning(f"TTS stream on '{model_id}' returned no audio.")
            except Exception as e:
                if produced:
                    # Mid-sentence failure: don't restart the line from the top, just stop.
                    logger.error(f"TTS stream on '{model_id}' dropped mid-line: {e}")
                    return
                if self._is_quota_error(e):
                    logger.warning(f"TTS quota/rate limit on '{model_id}' ({e}). Skipping it for the rest of this session.")
                    self._exhausted_models.add(model_id)
                    if model_id == self.primary_model:
                        self._quota_exceeded_for_session = True
                    continue
                logger.warning(f"TTS stream failed on '{model_id}' ({e}); trying next option.")
                continue

        # Last resort: the regular (non-streaming) path incl. legacy model / voice re-design
        if cancel is not None and cancel.is_set():
            return
        if chain:
            self._stream_failures += 1
            if self._stream_failures == 2:
                logger.warning("TTS streaming failed twice in a row; using non-streaming voice for the rest of this session.")
        audio = self.synthesize(text, emotion=emotion, delivery=delivery)
        if audio is not None and len(audio):
            yield audio
