"""
Variety & mood engine for the race engineer.

Real engineers don't open every call with "You're running P8 mate" in the same focused tone.
This module keeps a short memory of what the engineer has recently said and how he said it,
tracks a slowly-decaying pit-wall mood driven by race events, and produces a compact guidance
block that is injected into every prompt so the LLM varies its wording and emotional colour.
"""

from __future__ import annotations

import random
import re
import time
from collections import Counter, deque
from typing import Deque, Dict, List, Optional, Tuple

# Phrases the model over-uses; tracked in addition to the persona's own vocabulary.
DEFAULT_CLICHES = [
    "keep your head down", "head down", "bring it home", "looks untouchable", "stay focused",
    "focus on your exits", "hunt him down", "on your gearbox", "keep pushing", "good job",
    "copy that", "nice work", "you're running", "heads up", "authoritative", "no drama",
]

# Event -> (mood delta, tension delta)
_EVENT_MOOD: Dict[str, Tuple[float, float]] = {
    "position_gained": (0.35, 0.0),
    "position_lost": (-0.3, 0.15),
    "collision_damage": (-0.45, 0.4),
    "tire_puncture_risk": (-0.2, 0.4),
    "fuel_emergency": (-0.2, 0.5),
    "fuel_critical": (-0.1, 0.3),
    "flag_black": (-0.5, 0.3),
    "engine_overheat": (-0.2, 0.35),
    "battle_entered": (0.0, 0.45),
    "box_this_lap": (0.0, 0.3),
    "box_next_lap": (0.0, 0.2),
    "final_lap": (0.05, 0.35),
    "laps_warning_2laps": (0.0, 0.25),
    "time_warning_1min": (0.0, 0.3),
    "time_warning_2min": (0.0, 0.2),
    "weather_rain_started": (-0.05, 0.35),
    "weather_rain_intensified": (-0.1, 0.4),
    "weather_rain_stopped": (0.1, -0.1),
    "flag_chequered": (0.25, -0.6),
    "quali_lap_completed": (0.05, 0.0),
    "lap_invalidated": (-0.2, 0.1),
}


def _opener(text: str, n_words: int = 3) -> str:
    words = re.sub(r"<[^>]+>|\[[^\]]+\]", "", text).strip().split()
    return " ".join(words[:n_words]).strip(",.!?—-").lower()


class VarietyTracker:
    def __init__(
        self,
        address_terms: Optional[List[str]] = None,
        vocabulary: Optional[List[str]] = None,
        address_frequency: float = 0.3,
        memory: int = 10,
        rng: Optional[random.Random] = None,
    ):
        self.address_terms = [a for a in (address_terms or []) if a]
        self.vocabulary = [v for v in (vocabulary or []) if v]
        self.address_frequency = address_frequency
        self.rng = rng or random.Random()

        self.recent_lines: Deque[str] = deque(maxlen=memory)
        self.recent_emotions: Deque[str] = deque(maxlen=6)
        self._used_vocab: Deque[str] = deque(maxlen=6)

        self.mood = 0.0      # -1 (grim) .. +1 (buoyant)
        self.tension = 0.0   # 0 (relaxed) .. 1 (on edge)
        self._mood_t = time.time()

    # ------------------------------------------------------------------ state updates
    def reset(self):
        self.recent_lines.clear()
        self.recent_emotions.clear()
        self._used_vocab.clear()
        self.mood = 0.0
        self.tension = 0.0
        self._mood_t = time.time()

    def _decay(self):
        now = time.time()
        dt = max(0.0, now - self._mood_t)
        self._mood_t = now
        # Mood halves roughly every 3 minutes, tension every 90 seconds
        self.mood *= 0.5 ** (dt / 180.0)
        self.tension *= 0.5 ** (dt / 90.0)

    def on_event(self, event_type: str):
        self._decay()
        dm, dt = _EVENT_MOOD.get(str(event_type), (0.0, 0.0))
        self.mood = max(-1.0, min(1.0, self.mood + dm))
        self.tension = max(0.0, min(1.0, self.tension + dt))

    def record(self, line: str, emotion: str = ""):
        if line:
            self.recent_lines.append(line)
            lower = line.lower()
            for v in self.vocabulary:
                if v.lower() in lower:
                    self._used_vocab.append(v)
        if emotion:
            self.recent_emotions.append(emotion.split(",")[0].strip().lower())

    # ------------------------------------------------------------------ analysis
    def overused_phrases(self) -> List[str]:
        joined = " ".join(self.recent_lines).lower()
        watch = DEFAULT_CLICHES + self.vocabulary
        counts = {p: len(re.findall(r"\b" + re.escape(p.lower()) + r"\b", joined)) for p in watch}
        overused = [p for p, c in counts.items() if c >= 2]
        # Anything said in the very last call shouldn't be repeated immediately either
        if self.recent_lines:
            last = self.recent_lines[-1].lower()
            overused += [p for p in watch if p.lower() in last and p not in overused]
        return overused[:8]

    def address_budget_exhausted(self) -> bool:
        if not self.address_terms or not self.recent_lines:
            return False
        window = list(self.recent_lines)[-4:]
        uses = sum(
            1 for line in window
            if any(re.search(r"\b" + re.escape(a.lower()) + r"\b", line.lower()) for a in self.address_terms)
        )
        return uses / max(1, len(window)) > self.address_frequency

    def mood_description(self) -> str:
        self._decay()
        m, t = self.mood, self.tension
        if t > 0.6:
            base = "on edge — things are happening fast"
        elif t > 0.3:
            base = "switched on and a bit tense"
        else:
            base = "settled"
        if m > 0.4:
            tone = "buoyant; the last few minutes have gone our way"
        elif m > 0.15:
            tone = "quietly pleased with how it's going"
        elif m < -0.4:
            tone = "frustrated; it's been a rough spell, but stay professional"
        elif m < -0.15:
            tone = "a little concerned after recent setbacks"
        else:
            tone = "neutral"
        return f"{base}, {tone}"

    # ------------------------------------------------------------------ prompt block
    def guidance(self) -> str:
        lines = ["\n[DELIVERY VARIETY & PIT-WALL MOOD — follow quietly, never mention]:"]
        lines.append(f"- Pit-wall mood right now: {self.mood_description()}. Let it colour EMOTION and wording where it fits the situation.")

        openers = [o for o in dict.fromkeys(_opener(l) for l in self.recent_lines) if o][-5:]
        if openers:
            lines.append("- Your recent calls opened with: " + "; ".join(f'"{o}…"' for o in openers)
                         + ". Open this one differently (lead with the key fact, a name, the action, or a reaction).")

        overused = self.overused_phrases()
        if overused:
            lines.append("- Worn-out phrases, avoid this time: " + ", ".join(f'"{p}"' for p in overused) + ".")

        if self.address_terms:
            if self.address_budget_exhausted():
                lines.append("- Do NOT address the driver by name/nickname in this call.")
            else:
                lines.append(f"- You may address the driver as {' / '.join(repr(a) for a in self.address_terms)} if natural — not every call.")

        emos = list(self.recent_emotions)
        if len(emos) >= 2:
            common, n = Counter(emos).most_common(1)[0]
            if n >= 2:
                lines.append(f"- Recent emotional tones: {', '.join(emos[-4:])}. Unless the situation truly demands it again, "
                             f"pick a different, more specific shade than '{common}'.")

        fresh_vocab = [v for v in self.vocabulary if v not in self._used_vocab]
        if fresh_vocab:
            picks = self.rng.sample(fresh_vocab, k=min(2, len(fresh_vocab)))
            lines.append("- Optional flavour if (and only if) it fits naturally: " + " | ".join(f'"{p}"' for p in picks) + ".")
        return "\n".join(lines)
