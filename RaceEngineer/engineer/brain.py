"""
Gemini 3.8 Flash Brain for AMS2 AI Race Engineer.
Handles real-time telemetry analysis, strategic reasoning, and voice radio Q&A
with a config-driven persona (see engineer/persona.py), accent, delivery, and emotional steering.

How the LLM is used (kept on LOW thinking to save tokens):
  * Session-scoped system prompt: only the current session's rules are sent, and the large setup
    reference guide only in PRACTICE. The system prompt is stable within a session so Gemini's
    implicit prompt caching can discount it on repeat calls.
  * Structured JSON output (response schema) instead of free-text parsing: transcript, intent,
    emotion, delivery, response, and an optional "remember" note. Regex parsing stays as a fallback.
  * Dialogue history is sent once, as native multi-turn contents (it used to be duplicated inside
    the prompt text as well).
  * Engineer's notebook: the model can jot down facts worth keeping (driver feedback, agreed plans,
    car issues) that survive beyond the 10-message radio history.
  * Driver acknowledgements ("copy", "thanks") can get no reply at all, like on a real radio.
  * Optional fast model for routine proactive calls, fallback model on quota/overload, request
    timeout + transient retry, and per-call token accounting in the interaction logs.
"""

import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple

from google import genai
from google.genai import types

from .prompts import (
    get_system_prompt,
    PROACTIVE_EVENT_PROMPT,
    DRIVER_QUESTION_PROMPT,
    DRIVER_AUDIO_DIRECT_PROMPT,
    RACE_UPDATE_PROMPT,
    PRACTICE_BRIEFING_PROMPT,
    QUALI_SITREP_PROMPT,
    PRACTICE_PIT_DEBRIEF_PROMPT
)
from race.events import RaceEvent, EventType
from .intent import classify_driver_intent
from .variety import VarietyTracker
from utils.logging import logger
from utils.interaction_logger import interaction_logger


INTENTS = [
    "sitrep", "fuel", "strategy", "gaps", "position", "tyres", "lap_times", "setup", "car_feedback",
    "incident", "declaration", "command", "radio_check", "acknowledgement", "joke", "chat",
    "unclear", "alert", "briefing", "other",
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "transcript": {
            "type": "string",
            "description": "Driver audio calls only: the driver's exact words. Empty string otherwise.",
        },
        "intent": {"type": "string", "enum": INTENTS, "description": "What the driver/trigger wants."},
        "emotion": {"type": "string", "description": "Specific situational feeling for the voice actor."},
        "delivery": {"type": "string", "description": "Pace, volume, energy and rhythm of the line."},
        "response": {
            "type": "string",
            "description": "Exact words spoken over the radio. Empty string ONLY if no reply is needed.",
        },
        "remember": {
            "type": "string",
            "description": "Optional short fact worth remembering for the rest of the session. Empty if nothing new.",
        },
        "car_fact": {
            "type": "string",
            "description": "Practice: a lasting fact about THIS CAR the driver just told you (what can / cannot be adjusted, setup ranges, current values). Empty otherwise.",
        },
        "setup_change": {
            "type": "string",
            "description": "Practice: a setup change the driver confirmed he has made / is making before the next run, with the amount. Empty otherwise.",
        },
        "setup_conclusion": {
            "type": "string",
            "description": "Practice: a verdict from comparing runs (e.g. 'front -0.05 bar: mid-corner push reduced, 0.2s faster - keep'). Empty otherwise.",
        },
    },
    "required": ["intent", "emotion", "delivery", "response"],
}

JSON_MODE_SUFFIX = """

REPLY AS JSON with the fields above plus:
- intent: the closest category for this call
- remember: ONLY if something new is worth keeping for later in the session (driver's feedback on the car, a plan you agreed, a recurring problem, a rival to watch). One short line, else "".
- response: if the driver is only acknowledging you ("copy", "thanks", "roger") and nothing needs answering, you may leave it "" — real engineers don't reply to a "copy"."""

PRACTICE_JSON_SUFFIX = """
- car_fact: if the driver told you something lasting about this car (e.g. "camber is fixed", "ARBs not adjustable", "pressures adjust in 0.01 bar steps", "front pressure is 1.70 bar cold"), one short line, else "".
- setup_change: if the driver confirmed a change he made / is making for the next run (e.g. "front cold pressures -0.05 bar"), one short line with the amount, else "".
- setup_conclusion: if you just judged a change using run-vs-run data, the verdict in one line (keep / revert and why), else ""."""

# Proactive events that are routine enough for the optional fast model
ROUTINE_EVENTS = {
    EventType.TIME_WARNING_10MIN, EventType.TIME_WARNING_5MIN, EventType.TIME_WARNING_2MIN,
    EventType.TIME_WARNING_1MIN, EventType.LAPS_WARNING_5LAPS, EventType.LAPS_WARNING_2LAPS,
    EventType.LEADER_PITTED, EventType.LEADER_FINISHED, EventType.RIVAL_PITTED,
    EventType.PIT_EXIT_PROXIMITY, EventType.WEATHER_RAIN_STOPPED, EventType.QUALI_LAP_COMPLETED,
}


@dataclass
class EngineerReply:
    transcript: str = ""
    intent: str = ""
    emotion: str = ""
    delivery: str = ""
    response: str = ""
    remember: str = ""
    car_fact: str = ""
    setup_change: str = ""
    setup_conclusion: str = ""
    ok: bool = False          # a model reply was obtained and parsed
    latency: float = 0.0
    model: str = ""
    tokens: str = ""


class RaceEngineerBrain:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-3.8-flash",
        personality: str = "Gianpiero Lambiase (GP) style — sharp, authoritative, dry-witted British race engineer. Candid, blunt, unflappable on the pit wall with a Max Verstappen-style dynamic. Zero fluff, direct data, sarcastic when needed, laser-focused in battles.",
        accent: str = "British",
        delivery: str = "sharp, authoritative, dry, and focused",
        verbosity: str = "balanced",
        thinking_level: Optional[str] = "HIGH",
        thinking_budget: Optional[int] = None,
        max_output_tokens: int = 8192,
        persona=None,
        temperature: float = 1.0,
        report_temperature: Optional[float] = None,
        fast_model: Optional[str] = None,
        fast_thinking_level: Optional[str] = None,
        fallback_model: Optional[str] = None,
        structured_output: bool = True,
        request_timeout_sec: float = 20.0,
        notebook_size: int = 8,
        scoped_system_prompt: bool = True,
        driver_brief: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model_name = model_name
        self.persona = persona
        self.personality = persona.personality if persona else personality
        self.accent = persona.accent if persona else accent
        self.delivery = persona.baseline_delivery if persona else delivery
        # Gemini 3.x models are tuned for temperature 1.0; lower values make calls repetitive.
        self.temperature = float(temperature)
        self.report_temperature = float(report_temperature) if report_temperature is not None else self.temperature
        self.verbosity = verbosity
        self.max_output_tokens = max_output_tokens
        self.fast_model = (fast_model or "").strip() or None
        self.fast_thinking_level = (str(fast_thinking_level).upper() if fast_thinking_level else None)
        self.fallback_model = (fallback_model or "").strip() or None
        self.structured_output = bool(structured_output)
        self.scoped_system_prompt = bool(scoped_system_prompt)
        # The driver's pre-race notes from the launcher (rivalries, planned strategy); blank = none
        self.driver_brief = (driver_brief or "").strip()

        # Resolve thinking configuration for Gemini 3.8 Flash (supports LOW / MEDIUM / HIGH)
        if thinking_level and str(thinking_level).upper() in ("HIGH", "MEDIUM", "LOW", "MINIMAL"):
            self.thinking_level = str(thinking_level).upper()
        elif thinking_budget is not None:
            if thinking_budget >= 3000:
                self.thinking_level = "HIGH"
            elif thinking_budget >= 1500:
                self.thinking_level = "MEDIUM"
            else:
                self.thinking_level = "LOW"
        else:
            self.thinking_level = "HIGH"

        self.thinking_budget = thinking_budget
        self.client: Optional[genai.Client] = None

        # Session-scoped system prompt (rebuilt by set_session_group)
        self.session_group: Optional[str] = None
        self.system_prompt = get_system_prompt(self.personality, self.accent, self.delivery, persona=persona,
                                               driver_brief=self.driver_brief)
        if self.driver_brief:
            logger.info(f"Driver's pre-race brief loaded ({len(self.driver_brief)} chars).")

        # Wording / emotion variety + pit-wall mood memory
        self.variety = VarietyTracker(
            address_terms=persona.address_terms if persona else ["mate"],
            vocabulary=persona.vocabulary if persona else [],
            address_frequency=persona.address_frequency if persona else 0.3,
        )

        # Rolling history of recent radio exchanges (max 10)
        self.history: deque[Dict[str, str]] = deque(maxlen=10)
        # Longer-lived notes the engineer chose to remember
        self.notebook: deque[str] = deque(maxlen=max(1, int(notebook_size)))
        self._note_keys: Dict[str, str] = {}   # note text -> car|track it belongs to
        self.context_key: str = ""             # current car|track (notes from other events are dropped)
        self.knowledge_sink = None             # callable(kind, text) for car_fact / setup_change / setup_conclusion

        # Token accounting for the current session
        self.usage_totals: Dict[str, int] = {"calls": 0, "prompt": 0, "cached": 0, "thoughts": 0, "output": 0}

        if self.api_key:
            try:
                http_options = types.HttpOptions(
                    timeout=int(max(3.0, float(request_timeout_sec)) * 1000),
                    retry_options=types.HttpRetryOptions(attempts=2, http_status_codes=[500, 502, 503, 504]),
                )
                self.client = genai.Client(api_key=self.api_key, http_options=http_options)
                logger.info(
                    f"Gemini Brain initialized ({model_name} | thinking_level={self.thinking_level} | "
                    f"structured={self.structured_output} | fast_model={self.fast_model or '-'} | {self.accent})."
                )
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client: {e}")
        else:
            logger.warning("No GEMINI_API_KEY provided. Engineer will run in mock/silent mode.")

    # ------------------------------------------------------------------ session
    def set_session_group(self, session_group: Optional[str]):
        """Rebuilds the system prompt for PRACTICE / QUALIFY / RACE so only relevant rules are sent."""
        group = (session_group or "").upper() or None
        if group == self.session_group:
            return
        self.session_group = group
        if not self.scoped_system_prompt:
            return
        self.system_prompt = get_system_prompt(
            self.personality, self.accent, self.delivery, persona=self.persona, session_group=group,
            driver_brief=self.driver_brief,
        )
        logger.info(f"Engineer system prompt scoped to {group or 'ALL'} ({len(self.system_prompt)} chars).")

    def reset_session(self):
        """Resets radio conversation history and session memory for a fresh session."""
        if self.usage_totals["calls"]:
            logger.info(f"LLM usage last session: {self._usage_summary()}")
        self.history.clear()
        self.variety.reset()
        # Carry notes over only within the same event (car + track): practice -> quali -> race.
        # Notes from another track/car (e.g. last night's Interlagos race) are dropped.
        recent = [n for n in self.notebook if self._note_keys.get(n, "") == self.context_key][-4:]
        self.notebook.clear()
        self.notebook.extend(recent)
        self._note_keys = {n: self.context_key for n in recent}
        self.usage_totals = {k: 0 for k in self.usage_totals}
        logger.info("Gemini Brain radio history cleared for new session.")

    def set_context_key(self, key: str):
        """car|track of the session about to start (call BEFORE reset_session)."""
        self.context_key = key or ""

    def note_event(self, event_type: str):
        """Feeds race events into the pit-wall mood (called for every event, even silent ones)."""
        try:
            self.variety.on_event(event_type)
        except Exception as e:
            logger.debug(f"Variety tracker event error: {e}")

    def _remember_reply(self, reply: str, emotion: str):
        try:
            self.variety.record(reply, emotion)
        except Exception as e:
            logger.debug(f"Variety tracker record error: {e}")

    def remember(self, note: str):
        note = (note or "").strip().strip('"')
        if not note or len(note) < 4:
            return
        note = note[:160]
        tag = (self.session_group or "SESSION")[:5]
        if any(note.lower() in n.lower() for n in self.notebook):
            return
        entry = f"[{tag}] {note}"
        self.notebook.append(entry)
        self._note_keys[entry] = self.context_key
        logger.info(f"[ENGINEER NOTEBOOK] + {note}")

    # ------------------------------------------------------------------ prompt helpers
    def _format_history(self) -> str:
        """Human-readable radio log (kept for logging/tests; prompts use native multi-turn history)."""
        variety = ""
        try:
            variety = self.variety.guidance()
        except Exception as e:
            logger.debug(f"Variety guidance error: {e}")
        if not self.history:
            return variety
        lines = ["\n[RECENT RADIO & PIT-WALL LOG - LAST EXCHANGES]:"]
        for item in self.history:
            speaker = item.get("speaker", "Radio")
            text = item.get("text", "")
            lines.append(f"  * {speaker}: \"{text}\"")
        return "\n".join(lines) + variety

    def _prompt_extras(self) -> str:
        """Per-call extras: engineer's notebook + variety/mood guidance (history is sent as turns)."""
        parts = []
        if self.notebook:
            parts.append("\n[ENGINEER'S NOTEBOOK — things you noted earlier; use when relevant]:")
            parts.extend(f"  * {n}" for n in self.notebook)
        try:
            parts.append(self.variety.guidance())
        except Exception as e:
            logger.debug(f"Variety guidance error: {e}")
        return "\n".join(parts)

    def _finalise_prompt(self, prompt: str) -> str:
        if not self.structured_output:
            return prompt
        prompt = prompt.replace("Provide your response in EXACTLY this format:", "Fill these JSON fields:")
        suffix = JSON_MODE_SUFFIX + (PRACTICE_JSON_SUFFIX if self.session_group == "PRACTICE" else "")
        return prompt + suffix

    def _usage_summary(self) -> str:
        u = self.usage_totals
        return (f"{u['calls']} calls | prompt {u['prompt']} tok (cached {u['cached']}) | "
                f"thinking {u['thoughts']} | output {u['output']}")

    def _build_conversation_contents(self, current_parts: List[types.Part]) -> List[types.Content]:
        """
        Constructs native Gemini multi-turn conversation contents from recent dialogue history.
        Strictly guarantees:
        1. Multi-turn starts with a 'user' turn.
        2. Roles alternate strictly: user -> model -> user -> model ... -> user.
        3. Only dialogue text is preserved in previous turns to keep audio/telemetry payload light.
        """
        contents: List[types.Content] = []
        last_role = None

        for item in self.history:
            speaker = item.get("speaker", "Driver")
            text = item.get("text", "")
            if not text:
                continue

            target_role = "user" if speaker in ("Driver", "Radio Alert") else "model"

            # Gemini multi-turn conversation MUST begin with a 'user' role
            if not contents and target_role == "model":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="[Pit-wall radio telemetry alert]")]
                ))
                last_role = "user"

            # Enforce strict alternation
            if target_role == last_role:
                if target_role == "model":
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="[Driver acknowledges call]")]
                    ))
                else:
                    contents.append(types.Content(
                        role="model",
                        parts=[types.Part.from_text(text="RESPONSE: Copy that.")]
                    ))

            if target_role == "user":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=f"{speaker}: {text}")]
                ))
            else:
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=f"RESPONSE: {text}")]
                ))
            last_role = target_role

        # The active turn is a 'user' turn containing current_parts.
        # If the last history turn was 'user', insert a model acknowledgment to maintain alternation.
        if contents and contents[-1].role == "user":
            contents.append(types.Content(
                role="model",
                parts=[types.Part.from_text(text="RESPONSE: Copy that.")]
            ))

        contents.append(types.Content(
            role="user",
            parts=current_parts
        ))
        return contents

    @staticmethod
    def get_event_emotion(event: Optional[RaceEvent]) -> str:
        """Determines the appropriate vocal emotion based on the event (used as fallback)."""
        if not event:
            return "calm, focused, and composed"

        if event.event_type == EventType.TRACK_LOADED:
            return "calm, focused, and welcoming"

        if event.event_type in (EventType.FUEL_CRITICAL, EventType.FUEL_EMERGENCY) and event.details.get("urgent") is False:
            return "calm, focused, and composed"

        if event.event_type in (
            EventType.COLLISION_DAMAGE,
            EventType.FLAG_YELLOW,
            EventType.FLAG_DOUBLE_YELLOW,
            EventType.FLAG_BLACK,
            EventType.FUEL_EMERGENCY,
            EventType.ENGINE_OVERHEAT,
            EventType.RIVAL_INCIDENT,
            EventType.BOX_NEXT_LAP,
            EventType.BOX_THIS_LAP,
            EventType.WEATHER_RAIN_STARTED,
            EventType.WEATHER_RAIN_INTENSIFIED,
            EventType.TIME_WARNING_2MIN,
            EventType.TIME_WARNING_1MIN,
        ):
            return "alert, sharp, and attentive"

        if event.event_type in (EventType.FLAG_CHEQUERED, EventType.POSITION_GAINED):
            return "pleased, professional, and relaxed"

        if event.event_type in (
            EventType.BATTLE_ENTERED,
            EventType.FUEL_CRITICAL,
            EventType.TIRE_PUNCTURE_RISK,
            EventType.POSITION_LOST,
            EventType.TIME_WARNING_10MIN,
            EventType.TIME_WARNING_5MIN,
            EventType.LAPS_WARNING_5LAPS,
            EventType.LAPS_WARNING_2LAPS,
            EventType.FINAL_LAP,
            EventType.LEADER_FINISHED,
            EventType.WEATHER_RAIN_STOPPED,
        ):
            return "focused and alert"

        return "calm, focused, and relaxed"

    @staticmethod
    def _parse_llm_response(text: str) -> Tuple[str, str, str, str]:
        """
        Parses TRANSCRIPT:, EMOTION:, DELIVERY:, and RESPONSE: from Gemini multimodal/text response.
        Returns (transcript, emotion, delivery, response).
        """
        transcript = ""
        emotion = ""
        delivery = ""
        response = ""

        t_match = re.search(r"TRANSCRIPT:\s*(.*?)(?=\n(?:EMOTION|DELIVERY|RESPONSE):|$)", text, re.DOTALL | re.IGNORECASE)
        if t_match:
            transcript = t_match.group(1).strip().strip('"\'')

        e_match = re.search(r"EMOTION:\s*(.*?)(?=\n(?:DELIVERY|RESPONSE):|$)", text, re.DOTALL | re.IGNORECASE)
        if e_match:
            emotion = e_match.group(1).strip().strip('"\'')

        d_match = re.search(r"DELIVERY:\s*(.*?)(?=\nRESPONSE:|$)", text, re.DOTALL | re.IGNORECASE)
        if d_match:
            delivery = d_match.group(1).strip().strip('"\'')

        r_match = re.search(r"RESPONSE:\s*(.*)", text, re.DOTALL | re.IGNORECASE)
        if r_match:
            response = r_match.group(1).strip().strip('"\'')
        else:
            cleaned = text
            for prefix in ["TRANSCRIPT:", "EMOTION:", "DELIVERY:", "RESPONSE:"]:
                if cleaned.upper().startswith(prefix):
                    parts = cleaned.split("\n", 1)
                    cleaned = parts[1] if len(parts) > 1 else ""
            response = cleaned.strip().strip('"\'')

        # Sanitize any stray stage directions or laughs from response
        response = re.sub(r"\[(chuckles?|laughs?|sighs?|giggles?)\]", "", response, flags=re.IGNORECASE)
        response = re.sub(r"\*(chuckles?|laughs?|sighs?|giggles?)\*", "", response, flags=re.IGNORECASE)
        response = re.sub(r"\((chuckles?|laughs?|sighs?|giggles?)\)", "", response, flags=re.IGNORECASE)
        response = re.sub(r"^(?:he\s+)?(?:chuckles?|laughs?|sighs?)[,:\s-]*", "", response, flags=re.IGNORECASE).strip()
        response = re.sub(r"\s+", " ", response).strip()

        # Sanitize delivery and emotion strings to prevent chuckle/laugh cues
        if delivery:
            delivery = re.sub(r"\b(chuckle|chuckles|chuckling|laugh|laughs|laughing|witty|giggle)\b", "calm", delivery, flags=re.IGNORECASE)
            delivery = re.sub(r"\s+", " ", delivery).strip()
        if emotion:
            emotion = re.sub(r"\b(chuckle|chuckles|chuckling|laugh|laughs|laughing|witty|giggle)\b", "calm", emotion, flags=re.IGNORECASE)
            emotion = re.sub(r"\s+", " ", emotion).strip()

        return transcript, emotion, delivery, response

    @staticmethod
    def _parse_audio_response(text: str) -> Tuple[str, str]:
        """Parses TRANSCRIPT: and RESPONSE: from Gemini multimodal response."""
        transcript, _, _, response = RaceEngineerBrain._parse_llm_response(text)
        return transcript, response

    # ------------------------------------------------------------------ model calls
    @staticmethod
    def _sanitize_fields(emotion: str, delivery: str, response: str, allow_laughter: bool = False) -> Tuple[str, str, str]:
        response = response or ""
        if not allow_laughter:
            response = re.sub(r"\[(chuckles?|laughs?|giggles?)\]", "", response, flags=re.IGNORECASE)
            response = re.sub(r"<(chuckle|laugh|giggle|snicker|cackle)>", "", response, flags=re.IGNORECASE)
        response = re.sub(r"\*(chuckles?|laughs?|sighs?|giggles?)\*", "", response, flags=re.IGNORECASE)
        response = re.sub(r"\((chuckles?|laughs?|sighs?|giggles?)\)", "", response, flags=re.IGNORECASE)
        response = re.sub(r"^(?:he\s+)?(?:chuckles?|laughs?|sighs?)[,:\s-]+", "", response, flags=re.IGNORECASE)
        response = re.sub(r"\s+", " ", response).strip().strip('"')
        if not allow_laughter:
            pat = r"\b(chuckle|chuckles|chuckling|laugh|laughs|laughing|giggle)\b"
            emotion = re.sub(pat, "amused", emotion or "", flags=re.IGNORECASE)
            delivery = re.sub(pat, "light", delivery or "", flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", emotion or "").strip(), re.sub(r"\s+", " ", delivery or "").strip(), response

    def _parse_model_output(self, raw_text: str) -> EngineerReply:
        reply = EngineerReply()
        data = None
        if self.structured_output:
            try:
                cleaned = raw_text.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
                data = json.loads(cleaned)
                if not isinstance(data, dict):
                    data = None
            except Exception:
                data = None
        if data is not None:
            reply.transcript = str(data.get("transcript", "") or "").strip().strip('"\'')
            reply.intent = str(data.get("intent", "") or "").strip().lower()
            reply.emotion = str(data.get("emotion", "") or "")
            reply.delivery = str(data.get("delivery", "") or "")
            reply.response = str(data.get("response", "") or "")
            reply.remember = str(data.get("remember", "") or "")
            reply.car_fact = str(data.get("car_fact", "") or "").strip()
            reply.setup_change = str(data.get("setup_change", "") or "").strip()
            reply.setup_conclusion = str(data.get("setup_conclusion", "") or "").strip()
        else:
            reply.transcript, reply.emotion, reply.delivery, reply.response = self._parse_llm_response(raw_text)
        allow_laugh = bool(getattr(self.persona, "allow_laughter", False))
        reply.emotion, reply.delivery, reply.response = self._sanitize_fields(
            reply.emotion, reply.delivery, reply.response.replace("\n", " "), allow_laughter=allow_laugh
        )
        reply.ok = True
        return reply

    def _record_usage(self, response) -> str:
        um = getattr(response, "usage_metadata", None)
        if um is None:
            return ""
        p = int(getattr(um, "prompt_token_count", 0) or 0)
        c = int(getattr(um, "cached_content_token_count", 0) or 0)
        t = int(getattr(um, "thoughts_token_count", 0) or 0)
        o = int(getattr(um, "candidates_token_count", 0) or 0)
        u = self.usage_totals
        u["calls"] += 1
        u["prompt"] += p
        u["cached"] += c
        u["thoughts"] += t
        u["output"] += o
        return f"prompt {p} (cached {c}) | thinking {t} | output {o}"

    @staticmethod
    def _is_retryable_with_fallback(e: Exception) -> bool:
        s = str(e)
        return any(k in s for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "overloaded", "DEADLINE", "timed out", "Timeout"))

    def _call_model(self, contents, temperature: float, use_fast: bool = False,
                    on_partial=None) -> Tuple[Optional[str], str, str]:
        """
        Returns (raw_text, model_used, token_summary). Raises on unrecoverable error.
        With `on_partial`, the reply is streamed and on_partial(text_so_far) is called as it grows.
        """
        models: List[Tuple[str, str]] = []
        if use_fast and self.fast_model:
            models.append((self.fast_model, self.fast_thinking_level or self.thinking_level))
        models.append((self.model_name, self.thinking_level))
        if self.fallback_model and self.fallback_model not in [m for m, _ in models]:
            models.append((self.fallback_model, self.fast_thinking_level or self.thinking_level))

        last_err: Optional[Exception] = None
        for model_id, level in models:
            cfg = dict(
                system_instruction=self.system_prompt,
                temperature=temperature,
                max_output_tokens=self.max_output_tokens,
                thinking_config=types.ThinkingConfig(thinking_level=level),
                # No tools are used; also silences the SDK's AFC warning on streaming calls
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            if self.structured_output:
                cfg["response_mime_type"] = "application/json"
                cfg["response_json_schema"] = RESPONSE_SCHEMA
            config = types.GenerateContentConfig(**cfg)
            got_text = False
            try:
                if on_partial is None:
                    response = self.client.models.generate_content(model=model_id, contents=contents, config=config)
                    raw = response.text.strip() if getattr(response, "text", None) else None
                    return raw, model_id, self._record_usage(response)

                buf: List[str] = []
                last_chunk = None
                for chunk in self.client.models.generate_content_stream(model=model_id, contents=contents, config=config):
                    last_chunk = chunk
                    piece = getattr(chunk, "text", None)
                    if piece:
                        got_text = True
                        buf.append(piece)
                        try:
                            on_partial("".join(buf))
                        except Exception as cb_err:
                            logger.debug(f"Streaming callback error: {cb_err}")
                raw = "".join(buf).strip() or None
                return raw, model_id, (self._record_usage(last_chunk) if last_chunk is not None else "")
            except Exception as e:
                last_err = e
                if got_text:
                    raise  # already speaking this reply -- don't start a different one
                if model_id != models[-1][0] and (self._is_retryable_with_fallback(e) or model_id == self.fast_model):
                    logger.warning(f"LLM call on '{model_id}' failed ({e}); trying next model.")
                    continue
                raise
        if last_err:
            raise last_err
        return None, "", ""

    # ------------------------------------------------------------------ streaming helpers
    @staticmethod
    def _partial_json_string(buf: str, key: str) -> Tuple[str, bool]:
        """
        Extracts the (possibly still incomplete) string value of `key` from a partial JSON object.
        Returns (value_so_far, is_complete).
        """
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', buf)
        if not m:
            return "", False
        out: List[str] = []
        i, n = m.end(), len(buf)
        escapes = {'"': '"', "\\": "\\", "/": "/", "b": "", "f": "", "n": " ", "r": " ", "t": " "}
        while i < n:
            ch = buf[i]
            if ch == '"':
                return "".join(out), True
            if ch == "\\":
                if i + 1 >= n:
                    break
                nxt = buf[i + 1]
                if nxt == "u":
                    if i + 6 > n:
                        break
                    try:
                        out.append(chr(int(buf[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                out.append(escapes.get(nxt, nxt))
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out), False

    def _partial_fields(self, buf: str) -> Tuple[str, str, str, bool]:
        """(response_so_far, emotion, delivery, emotion_known) from a partial model output."""
        if self.structured_output and buf.lstrip().startswith(("{", "`")):
            emotion, e_done = self._partial_json_string(buf, "emotion")
            delivery, d_done = self._partial_json_string(buf, "delivery")
            response, _ = self._partial_json_string(buf, "response")
            return response, emotion, delivery, (e_done and d_done)
        # Text format: EMOTION: / DELIVERY: lines, then RESPONSE:
        em = re.search(r"EMOTION:\s*(.*?)\n", buf, re.IGNORECASE)
        dm = re.search(r"DELIVERY:\s*(.*?)\n", buf, re.IGNORECASE)
        rm = re.search(r"RESPONSE:\s*(.*)", buf, re.IGNORECASE | re.DOTALL)
        return (rm.group(1) if rm else ""), (em.group(1).strip() if em else ""), \
               (dm.group(1).strip() if dm else ""), bool(em and dm)

    def _generate(
        self,
        kind: str,
        prompt: str,
        extra_parts: Optional[List[types.Part]] = None,
        temperature: Optional[float] = None,
        use_fast: bool = False,
        speaker=None,
        fallback_emotion: str = "",
        fallback_delivery: str = "",
    ) -> Tuple[EngineerReply, str]:
        """
        Single entry point for every LLM call. Returns (reply, final_prompt_text).
        With a `speaker` (voice.stream.ReplyStreamSink) the reply is streamed and spoken as it is
        written: the first full sentence goes to the voice as soon as it exists, the rest once
        the reply is complete. The text itself is identical to the non-streamed reply.
        """
        final_prompt = self._finalise_prompt(prompt)
        parts = list(extra_parts or []) + [types.Part.from_text(text=final_prompt)]
        contents = self._build_conversation_contents(parts)
        start_t = time.time()
        allow_laugh = bool(getattr(self.persona, "allow_laughter", False))

        on_partial = None
        if speaker is not None:
            def on_partial(buf: str):
                response, emotion, delivery, known = self._partial_fields(buf)
                if not response:
                    return
                emotion, delivery, response = self._sanitize_fields(emotion, delivery, response, allow_laughter=allow_laugh)
                speaker.update(response, emotion or fallback_emotion, delivery or fallback_delivery, known)

        raw, model_used, tokens = self._call_model(
            contents, self.temperature if temperature is None else temperature,
            use_fast=use_fast, on_partial=on_partial,
        )
        if not raw:
            return EngineerReply(latency=time.time() - start_t, model=model_used, tokens=tokens), final_prompt
        reply = self._parse_model_output(raw)
        reply.latency = time.time() - start_t
        reply.model = model_used
        reply.tokens = tokens
        if speaker is not None:
            speaker.complete(reply.response, reply.emotion or fallback_emotion, reply.delivery or fallback_delivery)
        if reply.remember:
            self.remember(reply.remember)
        for kind_, text_ in (("car_fact", reply.car_fact), ("setup_change", reply.setup_change),
                             ("setup_conclusion", reply.setup_conclusion)):
            if text_ and self.knowledge_sink is not None:
                try:
                    self.knowledge_sink(kind_, text_)
                except Exception as e:
                    logger.debug(f"knowledge sink error: {e}")
        if tokens:
            logger.debug(f"[LLM {kind}] {model_used} {reply.latency:.2f}s | {tokens}")
        return reply, final_prompt

    def _log(self, kind: str, driver_input: str, context_str: str, prompt: str, reply: EngineerReply):
        extra = f" | intent={reply.intent}" if reply.intent else ""
        extra += f" | {reply.model}" if reply.model else ""
        extra += f" | TOKENS {reply.tokens}" if reply.tokens else ""
        if reply.remember:
            extra += f" | NOTE: {reply.remember}"
        for lbl, txt in (("CAR FACT", reply.car_fact), ("SETUP CHANGE", reply.setup_change), ("CONCLUSION", reply.setup_conclusion)):
            if txt:
                extra += f" | {lbl}: {txt}"
        interaction_logger.log_interaction(
            interaction_type=kind,
            driver_input=driver_input,
            session_info=context_str.split("\n")[0] if "\n" in context_str else context_str[:60],
            telemetry_context=context_str,
            full_prompt_sent=prompt,
            ai_response_text=reply.response,
            emotion=f"{reply.emotion} | {reply.delivery}{extra}",
            latency_sec=reply.latency,
        )

    # ------------------------------------------------------------------ public API
    def generate_event_advice(self, event: RaceEvent, context_str: str) -> Tuple[Optional[str], str, str]:
        """Proactive radio call for a detected race event. Returns (radio_text, emotion, delivery)."""
        fallback_emotion = self.get_event_emotion(event)
        fallback_delivery = self.delivery
        if not self.client:
            return event.message, fallback_emotion, fallback_delivery

        prompt = PROACTIVE_EVENT_PROMPT.format(
            event_description=event.message,
            telemetry_context=context_str,
            radio_history=self._prompt_extras(),
        )
        try:
            reply, final_prompt = self._generate(
                f"event:{event.event_type}", prompt, use_fast=event.event_type in ROUTINE_EVENTS
            )
            if reply.ok and reply.response:
                emotion = reply.emotion or fallback_emotion
                delivery = reply.delivery or fallback_delivery
                self.history.append({"speaker": "Radio Alert", "text": event.message})
                self.history.append({"speaker": "Engineer", "text": reply.response})
                reply.emotion, reply.delivery = emotion, delivery
                self._log(f"PROACTIVE EVENT ({event.event_type})", event.message, context_str, final_prompt, reply)
                self._remember_reply(reply.response, emotion)
                return reply.response, emotion, delivery
            return event.message, fallback_emotion, fallback_delivery
        except Exception as e:
            logger.error(f"Gemini API error during event advice generation: {e}")
            return event.message, fallback_emotion, fallback_delivery

    def respond_to_driver_audio(self, wav_bytes: bytes, context_str: str, speaker=None) -> Tuple[Optional[str], str, str]:
        """
        Ingests the driver's spoken WAV directly, transcribes it and replies in one call.
        Returns (reply_text, emotion, delivery). reply_text may be "" when no reply is needed.
        """
        fallback_emotion = "calm, clear, and composed"
        fallback_delivery = self.delivery
        if not self.client:
            return "Copy that, but Gemini API key is missing.", fallback_emotion, fallback_delivery

        prompt = DRIVER_AUDIO_DIRECT_PROMPT.format(
            telemetry_context=context_str,
            radio_history=self._prompt_extras(),
        )
        try:
            audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
            reply, final_prompt = self._generate("driver_audio", prompt, extra_parts=[audio_part], speaker=speaker,
                                                  fallback_emotion=fallback_emotion, fallback_delivery=fallback_delivery)
            if not reply.ok:
                return "Say again, radio broke up.", fallback_emotion, fallback_delivery

            emotion = reply.emotion or fallback_emotion
            delivery = reply.delivery or fallback_delivery
            transcript = reply.transcript
            if transcript:
                logger.info(f"[DRIVER RADIO] 🎙️ \"{transcript}\"")
                self.history.append({"speaker": "Driver", "text": transcript})
                intent = reply.intent
                if not intent:
                    intent = classify_driver_intent(transcript)[0].value.lower()
                logger.info(f"[DRIVER INTENT] {intent}")
            else:
                self.history.append({"speaker": "Driver", "text": "[Radio Call]"})

            if not reply.response:
                if reply.intent == "acknowledgement":
                    logger.info("[ENGINEER] (acknowledgement — no reply needed)")
                    self._log("DRIVER VOICE RADIO CALL (PTT HOLD)", f"\"{transcript}\"", context_str, final_prompt, reply)
                    return "", emotion, delivery
                return "Say again, radio broke up.", fallback_emotion, fallback_delivery

            self.history.append({"speaker": "Engineer", "text": reply.response})
            reply.emotion, reply.delivery = emotion, delivery
            self._log("DRIVER VOICE RADIO CALL (PTT HOLD)", f"\"{transcript}\"" if transcript else "[Spoken Radio Call]",
                      context_str, final_prompt, reply)
            self._remember_reply(reply.response, emotion)
            return reply.response, emotion, delivery
        except Exception as e:
            logger.error(f"Gemini audio Q&A error: {e}")
            return "Radio check, message didn't come through.", fallback_emotion, fallback_delivery

    def respond_to_driver_text(self, text_query: str, context_str: str, speaker=None) -> Tuple[Optional[str], str, str]:
        """Responds to a driver text question. Returns (reply_text, emotion, delivery)."""
        fallback_emotion = "calm, clear, and composed"
        fallback_delivery = self.delivery
        if not self.client:
            return "Gemini API key not configured.", fallback_emotion, fallback_delivery

        intent, requires_telem, guidance = classify_driver_intent(text_query)
        logger.info(f"[DRIVER TEXT INTENT] {intent.value}")
        guidance_text = f"Classified Intent: {intent.value}\nGuidance: {guidance}" if guidance else f"Classified Intent: {intent.value}"

        prompt = DRIVER_QUESTION_PROMPT.format(
            driver_question=text_query,
            intent_guidance=guidance_text,
            telemetry_context=context_str if requires_telem else "Driver asked a casual / non-telemetry question.",
            radio_history=self._prompt_extras(),
        )
        try:
            reply, final_prompt = self._generate("driver_text", prompt, speaker=speaker,
                                                  fallback_emotion=fallback_emotion, fallback_delivery=fallback_delivery)
            if reply.ok and reply.response:
                emotion = reply.emotion or fallback_emotion
                delivery = reply.delivery or fallback_delivery
                self.history.append({"speaker": "Driver", "text": text_query})
                self.history.append({"speaker": "Engineer", "text": reply.response})
                reply.emotion, reply.delivery = emotion, delivery
                self._log("DRIVER TEXT QUESTION", text_query, context_str, final_prompt, reply)
                self._remember_reply(reply.response, emotion)
                return reply.response, emotion, delivery
            return "Copy, standing by.", fallback_emotion, fallback_delivery
        except Exception as e:
            logger.error(f"Gemini text Q&A error: {e}")
            return "Say again, missed that call.", fallback_emotion, fallback_delivery

    def generate_practice_briefing(self, context_str: str) -> Tuple[Optional[str], str, str]:
        """Initial setup briefing when entering a Practice session. Returns (reply_text, emotion, delivery)."""
        fallback_emotion = "calm, focused, and welcoming"
        fallback_delivery = "sharp, clear pit-wall delivery"
        if not self.client:
            return "Radio check loud and clear. Let's do an initial 4 to 5 lap run to get a baseline on tyre temps and fuel burn.", fallback_emotion, fallback_delivery

        prompt = PRACTICE_BRIEFING_PROMPT.format(telemetry_context=context_str) + self._prompt_extras()
        try:
            reply, final_prompt = self._generate("practice_briefing", prompt, temperature=self.report_temperature)
            if reply.ok and reply.response:
                emotion = reply.emotion or fallback_emotion
                delivery = reply.delivery or fallback_delivery
                self.history.append({"speaker": "Engineer", "text": reply.response})
                reply.emotion, reply.delivery = emotion, delivery
                self._log("PRACTICE INITIAL BRIEFING", "[Practice Session Loaded -> Auto Briefing]", context_str, final_prompt, reply)
                self._remember_reply(reply.response, emotion)
                return reply.response, emotion, delivery
            return "Radio check loud and clear. Let's head out for 4 to 5 laps to gather baseline data.", fallback_emotion, fallback_delivery
        except Exception as e:
            logger.error(f"Gemini practice briefing generation error: {e}")
            return "Radio check loud and clear. Head out for a 4 to 5 lap baseline run to gather data.", fallback_emotion, fallback_delivery

    def generate_quali_update(self, context_str: str, speaker=None) -> Tuple[Optional[str], str, str]:
        """Pure timing update for Qualifying. Returns (reply_text, emotion, delivery)."""
        fallback_emotion = "focused, sharp, and alert"
        fallback_delivery = "crisp, rapid pit-wall timing call"
        if not self.client:
            return "Qualifying telemetry link active. Check in-car timing display.", fallback_emotion, fallback_delivery

        prompt = QUALI_SITREP_PROMPT.format(telemetry_context=context_str, radio_history=self._prompt_extras())
        try:
            reply, final_prompt = self._generate("quali_update", prompt, temperature=self.report_temperature, speaker=speaker,
                                                  fallback_emotion=fallback_emotion, fallback_delivery=fallback_delivery)
            if reply.ok and reply.response:
                emotion = reply.emotion or fallback_emotion
                delivery = reply.delivery or fallback_delivery
                self.history.append({"speaker": "Driver", "text": "[Requested Qualifying Timing Update]"})
                self.history.append({"speaker": "Engineer", "text": reply.response})
                reply.emotion, reply.delivery = emotion, delivery
                self._log("QUALI TIMING SITREP (PTT TAP)", "[Radio Button Short Tap -> Quali SitRep Request]", context_str, final_prompt, reply)
                self._remember_reply(reply.response, emotion)
                return reply.response, emotion, delivery
            return "Timing link active. Standing by.", fallback_emotion, fallback_delivery
        except Exception as e:
            logger.error(f"Gemini quali update generation error: {e}")
            return "Unable to pull live qualifying times right now.", fallback_emotion, fallback_delivery

    def generate_race_update(self, context_str: str, session_group: str = "RACE", speaker=None) -> Tuple[Optional[str], str, str]:
        """
        Situation report (SitRep) when the driver taps the radio button.
        Routes to the Quali timing update in QUALIFY. Returns (reply_text, emotion, delivery).
        """
        if session_group == "QUALIFY":
            return self.generate_quali_update(context_str, speaker=speaker)

        fallback_emotion = "calm, focused, and informative"
        fallback_delivery = self.delivery
        if not self.client:
            return "Radio check: Check your in-car dash for telemetry numbers.", fallback_emotion, fallback_delivery

        if session_group == "PRACTICE" and ("[PRACTICE RUN DEBRIEF]" in context_str or "PRACTICE / SETUP" in context_str):
            template = PRACTICE_PIT_DEBRIEF_PROMPT
        else:
            template = RACE_UPDATE_PROMPT
        prompt = template.format(telemetry_context=context_str, radio_history=self._prompt_extras())
        try:
            reply, final_prompt = self._generate("sitrep", prompt, temperature=self.report_temperature, speaker=speaker,
                                                  fallback_emotion=fallback_emotion, fallback_delivery=fallback_delivery)
            if reply.ok and reply.response:
                emotion = reply.emotion or fallback_emotion
                delivery = reply.delivery or fallback_delivery
                report_type = "PRACTICE DEBRIEF (PTT TAP)" if session_group == "PRACTICE" else "SITUATION REPORT (PTT TAP)"
                self.history.append({"speaker": "Driver", "text": f"[Requested {report_type}]"})
                self.history.append({"speaker": "Engineer", "text": reply.response})
                reply.emotion, reply.delivery = emotion, delivery
                self._log(report_type, "[Radio Button Short Tap -> SitRep Request]", context_str, final_prompt, reply)
                self._remember_reply(reply.response, emotion)
                return reply.response, emotion, delivery
            return "Telemetry nominal. Standing by.", fallback_emotion, fallback_delivery
        except Exception as e:
            logger.error(f"Gemini race update generation error: {e}")
            return "Unable to pull live telemetry for update at the moment.", fallback_emotion, fallback_delivery
