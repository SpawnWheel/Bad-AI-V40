"""
Engineer persona definitions, loaded entirely from config.yaml.

A persona bundles everything that makes the engineer *who he is*:
  - how he thinks and talks (personality, humour, vocabulary, banned phrases)
  - how he sounds (accent, age, gender, language, Gemini 3.8 voice design prompt)
  - how emotional he is allowed to get (expressiveness, laughter, vocal events)

config.yaml layout:

    engineer:
      persona: gp_british          # key into the `personas:` section below
      persona_overrides: {}        # optional one-off tweaks on top of the preset

    personas:
      gp_british:
        display_name: ...
        personality: ...
        accent: ...
        ...

Older configs that only have engineer.personality / accent / gender / delivery keep
working: those keys are folded into a persona automatically.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional

from utils.logging import logger


# Vocal events Gemini 3.8 TTS understands inline (angle brackets, spoken as sounds not words).
# We only let the engineer use the subset that makes sense on a pit wall.
ALL_TTS_VOCAL_EVENTS = {
    "argh", "breath", "heavy breath", "exhales", "cackle", "cheer", "chuckle", "cough",
    "cry", "gasp", "giggle", "groan", "growl", "grunt", "grr", "hiss", "laugh", "moan",
    "pant", "pff", "scream", "shout", "shriek", "sigh", "sneeze", "snicker", "snort",
    "sob", "throat-clearing", "tsk", "whimper", "whispers", "yawn", "short pause", "long pause",
}

DEFAULT_VOCAL_EVENTS = ["breath", "exhales", "sigh", "tsk", "short pause", "long pause", "throat-clearing"]
LAUGHTER_EVENTS = ["chuckle", "laugh", "snort"]
CELEBRATION_EVENTS = ["cheer", "shout"]

EXPRESSIVENESS_LEVELS = ("subtle", "natural", "expressive")


@dataclass
class VoiceSettings:
    # "designed"  -> create (once, then cache) a custom Gemini 3.8 voice from `description`
    # "prebuilt"  -> use a stock voice name (Charon, Puck, Orus, Fenrir, ...), accent steered via style
    # "voice_id"  -> use an existing voice id you already created (voice_... / voicekey_...)
    mode: str = "designed"
    prebuilt: str = "Charon"
    voice_id: str = ""
    description: str = ""  # voice-design prompt: timbre, age, accent (immutable traits only)


@dataclass
class Persona:
    key: str = "custom"
    display_name: str = "Race Engineer"
    personality: str = (
        "Experienced race engineer, calm under pressure, sharp, authoritative, and data-driven."
    )
    accent: str = "British"
    language_code: str = "en-GB"
    language: str = "English"
    gender: str = "male"
    age: str = "mid-40s"

    # Default performance note used when the brain doesn't supply one
    baseline_delivery: str = "measured, dry, and focused pit-wall delivery"

    # How the engineer addresses the driver; used sparingly (see address_frequency)
    address_terms: List[str] = field(default_factory=lambda: ["mate"])
    address_frequency: float = 0.3  # 0..1 share of calls that should include an address term

    # Signature phrases -- offered as optional flavour, a couple at a time, never all at once
    vocabulary: List[str] = field(default_factory=list)
    # Things he should never say
    banned_phrases: List[str] = field(default_factory=list)

    humour: str = "dry, deadpan, understated"
    # subtle | natural | expressive -- how far emotions are allowed to colour the voice
    expressiveness: str = "natural"
    allow_laughter: bool = False
    allow_celebration: bool = True
    vocal_events: List[str] = field(default_factory=lambda: list(DEFAULT_VOCAL_EVENTS))

    voice: VoiceSettings = field(default_factory=VoiceSettings)

    # ------------------------------------------------------------------ helpers
    @property
    def allowed_vocal_events(self) -> List[str]:
        events = [e for e in self.vocal_events if e in ALL_TTS_VOCAL_EVENTS]
        if self.allow_laughter:
            events += [e for e in LAUGHTER_EVENTS if e not in events]
        else:
            events = [e for e in events if e not in LAUGHTER_EVENTS + ["giggle", "cackle", "snicker"]]
        if self.allow_celebration:
            events += [e for e in CELEBRATION_EVENTS if e not in events]
        return events

    def voice_design_prompt(self) -> str:
        """Natural-language voice description for Gemini 3.8 Voice Design (immutable traits only)."""
        if self.voice.description.strip():
            return self.voice.description.strip()
        return (
            f"A {self.age} {self.gender} motorsport race engineer with a natural {self.accent} accent. "
            f"Clear, mid-low voice with a slightly gravelly texture, speaking into a headset microphone "
            f"with calm authority."
        )

    def summary(self) -> str:
        return f"{self.display_name} ({self.gender}, {self.accent}, {self.expressiveness})"


# ---------------------------------------------------------------------- loading
def _coerce_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def persona_from_dict(key: str, data: Dict[str, Any]) -> Persona:
    data = dict(data or {})
    voice_data = data.pop("voice", {}) or {}
    if isinstance(voice_data, str):
        voice_data = {"mode": "prebuilt", "prebuilt": voice_data}

    voice_fields = {f.name for f in fields(VoiceSettings)}
    voice = VoiceSettings(**{k: v for k, v in voice_data.items() if k in voice_fields})
    voice.mode = str(voice.mode or "designed").lower()

    persona_fields = {f.name for f in fields(Persona)} - {"voice", "key"}
    kwargs: Dict[str, Any] = {}
    for k, v in data.items():
        if k not in persona_fields:
            logger.debug(f"Persona '{key}': ignoring unknown key '{k}'")
            continue
        if k in ("address_terms", "vocabulary", "banned_phrases", "vocal_events"):
            v = _coerce_list(v)
        kwargs[k] = v

    persona = Persona(key=key, voice=voice, **kwargs)
    if persona.expressiveness not in EXPRESSIVENESS_LEVELS:
        logger.warning(
            f"Persona '{key}': expressiveness '{persona.expressiveness}' not in {EXPRESSIVENESS_LEVELS}; using 'natural'."
        )
        persona.expressiveness = "natural"
    try:
        persona.address_frequency = max(0.0, min(1.0, float(persona.address_frequency)))
    except (TypeError, ValueError):
        persona.address_frequency = 0.3
    return persona


def load_persona(config: Dict[str, Any]) -> Persona:
    """Resolves the active persona from a full config dict."""
    eng_cfg = config.get("engineer", {}) or {}
    personas_cfg = config.get("personas", {}) or {}
    gemini_cfg = config.get("gemini", {}) or {}

    key = str(eng_cfg.get("persona", "") or "").strip()
    if key and key in personas_cfg:
        data = personas_cfg[key]
    elif key and personas_cfg:
        logger.warning(f"Persona '{key}' not found in config 'personas:' section. Available: {list(personas_cfg)}")
        key, data = next(iter(personas_cfg.items()))
        logger.warning(f"Falling back to first persona: '{key}'")
    elif personas_cfg and not key:
        key, data = next(iter(personas_cfg.items()))
    else:
        # Legacy flat config: build a persona from engineer.* keys
        key = "legacy"
        data = {}
        for legacy_key in ("personality", "accent", "gender"):
            if legacy_key in eng_cfg:
                data[legacy_key] = eng_cfg[legacy_key]
        if "delivery" in eng_cfg:
            data["baseline_delivery"] = eng_cfg["delivery"]
        data["voice"] = {"mode": "prebuilt", "prebuilt": gemini_cfg.get("tts_voice", "Charon")}

    data = _deep_merge(data or {}, eng_cfg.get("persona_overrides", {}) or {})
    persona = persona_from_dict(key, data)
    logger.info(f"Engineer persona loaded: {persona.summary()} [voice mode: {persona.voice.mode}]")
    return persona
