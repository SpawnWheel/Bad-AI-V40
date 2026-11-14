"""
Race radar: measured gaps to the cars directly ahead and behind.

The gap to each neighbour is sampled once per lap as we cross the line, so the trend ("closing four
tenths a lap") is measured over real laps instead of guessed from two lap times. From that it gives:

  * brief(snapshot)  a short block for the engineer's context - who is ahead / behind, the gap now,
                     the trend and when they meet. Clear enough that he can't mix up ahead and behind.
  * pace plan        what the pit wall wants from the driver right now - PUSH (defend / attack),
                     HOLD this pace, MANAGE (gaps are safe) or SAVE fuel - with a target lap time and how far
                     the last lap was from it.
  * candidates(...)  reasons worth an unprompted radio call at the end of a lap (a car closing in from
                     behind, a car ahead we are catching, a car we've just broken away from, the pace
                     instruction changing). Each reason has a key so the same story isn't called out again
                     a lap later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

TREND_SAMPLES = 4          # up to 4 line crossings (3 laps) for the trend
MIN_TREND_SAMPLES = 3      # a trend needs at least 2 full laps behind the same car


@dataclass
class RivalTrend:
    name: str
    position: int
    gap: float                 # seconds, always positive
    rate: Optional[float]      # change of the gap per lap: negative = gap shrinking
    laps: int                  # laps the rate is measured over
    lapped: bool               # a lap or more away - not a fight

    def laps_until(self, target_gap: float) -> Optional[float]:
        """Laps until the gap shrinks to target_gap at the current rate, or None if it isn't shrinking."""
        if self.rate is None or self.rate > -0.05 or self.gap <= target_gap:
            return None
        return (self.gap - target_gap) / -self.rate


@dataclass
class RadarReason:
    key: str
    text: str
    urgent: bool = False


def _surname(name: str) -> str:
    return name.split()[-1] if name else "the car"


def _tenths(x: float) -> str:
    return f"{abs(x):.1f}s"


# Fuel saving the driver can realistically do with lift-and-coast to skip a stop (fraction of the burn)
MAX_FUEL_SAVE = 0.07


@dataclass
class PacePlan:
    mode: str                    # PUSH_DEFEND | PUSH_ATTACK | HOLD_ATTACK | MANAGE | SAVE | NONE
    target_lap: Optional[float]  # seconds
    last_lap: Optional[float]
    text: str                    # one line for the context / the radio reason

    @property
    def delta(self) -> Optional[float]:
        """Last lap minus target: positive = slower than the target."""
        if self.target_lap and self.last_lap:
            return self.last_lap - self.target_lap
        return None


def fmt_lap(sec: Optional[float]) -> str:
    if not sec or sec <= 0:
        return "--"
    m, r = divmod(sec, 60.0)
    return f"{int(m)}:{r:06.3f}" if m else f"{r:.3f}s"


def reference_pace(lap_times: List[float]) -> Optional[float]:
    """Median of the last three clean laps (laps more than 5% off the best of them are ignored)."""
    laps = [t for t in lap_times[-5:] if t and t > 20]
    if not laps:
        return None
    best = min(laps)
    clean = [t for t in laps if t <= best * 1.05][-3:]
    clean.sort()
    return clean[len(clean) // 2]


class RaceRadar:
    def __init__(self):
        self.reset()

    def reset(self):
        self._hist: Dict[str, List[Tuple[int, str, float, bool]]] = {"ahead": [], "behind": []}
        self.ahead: Optional[RivalTrend] = None
        self.behind: Optional[RivalTrend] = None
        self._announced: Dict[str, int] = {}
        self.last_sample_lap = 0
        self.pace: PacePlan = PacePlan("NONE", None, None, "")
        self._announced_pace_mode = "NONE"

    # ------------------------------------------------------------------ sampling
    @staticmethod
    def _neighbour(snapshot, offset: int):
        pos = snapshot.position + offset
        if pos <= 0:
            return None
        player = (snapshot.player_name or "").strip().lower()
        for p in snapshot.participants or ():
            if p.is_active and p.position == pos and (not player or p.name.strip().lower() != player):
                return p
        return None

    def sample(self, completed_lap: int, snapshot, pace: float, dirty: bool = False):
        """Call once when we cross the line. dirty=True (our pit lap) restarts the trends."""
        self.last_sample_lap = completed_lap
        pace = pace if pace and pace > 20 else 90.0
        for side, offset, gap in (("ahead", -1, snapshot.split_time_ahead), ("behind", +1, snapshot.split_time_behind)):
            hist = self._hist[side]
            rival = self._neighbour(snapshot, offset)
            if dirty or rival is None or not gap or gap <= 0 or getattr(rival, "pit_mode", 0) > 0:
                hist.clear()
                setattr(self, side, None)
                continue
            if hist and hist[-1][1] != rival.name:
                hist.clear()              # a different car is there now: start a new story
            lapped = gap >= 0.9 * pace
            hist.append((completed_lap, rival.name, float(gap), lapped))
            del hist[:-TREND_SAMPLES]
            rate, laps = None, 0
            if len(hist) >= MIN_TREND_SAMPLES:
                laps = hist[-1][0] - hist[0][0]
                if laps > 0:
                    rate = (hist[-1][2] - hist[0][2]) / laps
            setattr(self, side, RivalTrend(rival.name, rival.position, float(gap), rate, laps, lapped))

    # ------------------------------------------------------------------ context
    def _describe(self, side: str, live_name: str, live_pos: int, live_gap: float) -> str:
        label = "Car AHEAD" if side == "ahead" else "Car BEHIND"
        sign = "+" if side == "ahead" else "-"
        t: Optional[RivalTrend] = getattr(self, side)
        head = f"  {label}: P{live_pos} {live_name}"
        if live_gap > 0:
            head += f" {sign}{live_gap:.1f}s"
        if t is None or t.name != live_name:
            return head + " | trend: not measured yet"
        if t.lapped:
            return head + " | a lap or more away - not a fight"
        if t.rate is None:
            return head + " | trend: needs another lap"
        r = t.rate
        if abs(r) < 0.1:
            trend = f"gap steady over the last {t.laps} laps"
        elif side == "ahead":
            trend = (f"YOU are closing {abs(r):.1f}s/lap (last {t.laps} laps)" if r < 0
                     else f"HE is pulling away {r:.1f}s/lap (last {t.laps} laps)")
        else:
            trend = (f"HE is closing on you {abs(r):.1f}s/lap (last {t.laps} laps)" if r < 0
                     else f"YOU are pulling away {r:.1f}s/lap (last {t.laps} laps)")
        meet = t.laps_until(0.5)
        if meet is not None:
            trend += f" -> within half a second in ~{max(1, round(meet))} laps at this rate"
        return f"{head} | {trend}"

    # ------------------------------------------------------------------ pace plan
    def update_pace_plan(self, lap_times: List[float], laps_to_go: Optional[int] = None,
                         fuel_avg_burn: float = 0.0, fuel_target_burn: Optional[float] = None,
                         needs_pit_stop: bool = False) -> PacePlan:
        """
        Decide what the pit wall wants from the driver: call once per lap after sample().
        lap_times: our race laps so far (pit laps already removed by the caller).
        """
        ref = reference_pace(lap_times)
        last = lap_times[-1] if lap_times else None
        plan = PacePlan("NONE", None, last, "")
        if ref is None:
            self.pace = plan
            return plan
        a, b = self.ahead, self.behind
        chase_hopeless = False

        # 1. Fuel: a small saving that saves a whole pit stop is the best strategy call there is
        save = None
        if needs_pit_stop and fuel_target_burn and fuel_avg_burn > 0 and (laps_to_go or 99) > 2:
            cut = 1.0 - fuel_target_burn / fuel_avg_burn
            if 0.0 < cut <= MAX_FUEL_SAVE:
                save = (f"SAVE FUEL to skip the stop: target {fuel_target_burn:.2f} L/lap (now {fuel_avg_burn:.2f}, "
                        f"{cut * 100:.0f}% less - lift and coast into the big stops). Costs maybe a few tenths a lap.")

        # 2. Under attack from behind
        if b and not b.lapped and b.rate is not None and b.rate <= -0.1 and b.gap <= 2.5:
            target = ref + b.rate        # need to find what he's finding
            plan = PacePlan("PUSH_DEFEND", target, last,
                            f"PUSH to defend: {b.name} behind is {abs(b.rate):.1f}s/lap quicker - target {fmt_lap(target)} or better "
                            f"(your recent pace {fmt_lap(ref)}) to hold the gap")
        # 3. Chasing the car ahead
        elif a and not a.lapped and a.gap <= 6.0 and a.gap > 0.6:
            closing = -(a.rate or 0.0)
            horizon = max(1, (laps_to_go or 9) - 1)
            needed = (a.gap - 0.5) / horizon        # closing rate needed to be with him before the flag
            if a.rate is not None and closing >= needed and closing >= 0.1:
                meet = a.laps_until(0.5)
                plan = PacePlan("HOLD_ATTACK", ref, last,
                                f"HOLD THIS PACE: {fmt_lap(ref)} is enough - you're closing {closing:.1f}s/lap on {a.name}, "
                                f"with him in ~{max(1, round(meet or horizon))} laps")
            elif needed - closing > 0.6:
                chase_hopeless = True     # would need well over half a second a lap more: not on
            else:
                extra = max(0.1, needed - closing)
                target = ref - extra
                plan = PacePlan("PUSH_ATTACK", target, last,
                                f"PUSH to catch {a.name}: need {needed:.1f}s/lap on him"
                                + (f" over the {laps_to_go} laps left" if laps_to_go else "")
                                + f" - target {fmt_lap(target)} (recent {fmt_lap(ref)})")
        # 4. Nobody close: bring it home
        if plan.mode == "NONE":
            safe_behind = (b is None or b.lapped or b.gap >= 3.0) and not (b and b.rate is not None and b.rate <= -0.3)
            out_of_reach = chase_hopeless or a is None or a.lapped or a.gap > 6.0 or (a.rate is not None and a.rate >= 0.2)
            if safe_behind and out_of_reach:
                plan = PacePlan("MANAGE", ref, last,
                                f"MANAGE: gaps are safe both ways - hold around {fmt_lap(ref)}, keep it clean and look after the tyres")
        if save:
            if plan.mode in ("NONE", "MANAGE"):
                plan = PacePlan("SAVE", ref, last, save)
            else:
                plan.text += f". Also: {save}"
        if plan.text and plan.delta is not None:
            plan.text += f" | last lap {fmt_lap(last)} ({plan.delta:+.1f}s vs target)"
        self.pace = plan
        return plan

    def brief(self, snapshot) -> str:
        if snapshot is None or not snapshot.participants:
            return ""
        lines = ["[WHAT MATTERS NOW — CARS AROUND YOU, gap trend measured each lap at the line]"]
        a = self._neighbour(snapshot, -1)
        b = self._neighbour(snapshot, +1)
        if a is None:
            lines.append("  Car AHEAD: none - you are leading" if snapshot.position == 1 else "  Car AHEAD: unknown")
        else:
            lines.append(self._describe("ahead", a.name, a.position, snapshot.split_time_ahead))
        if b is None:
            lines.append("  Car BEHIND: none - you are last")
        else:
            lines.append(self._describe("behind", b.name, b.position, snapshot.split_time_behind))
        if self.pace.text:
            lines.append(f"  PACE PLAN (what the pit wall wants): {self.pace.text}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ proactive triggers
    def _fresh(self, key: str, lap: int, every: int) -> bool:
        last = self._announced.get(key)
        return last is None or lap - last >= every

    def mark_announced(self, keys: List[str], lap: int):
        for k in keys:
            self._announced[k] = lap
            if k.startswith("pace:"):
                self._announced_pace_mode = k.split(":", 1)[1]
        if any(k.startswith(("threat:", "catch:")) for k in keys):
            self._announced_pace_mode = self.pace.mode

    def candidates(self, lap: int, laps_to_go: Optional[int] = None) -> List[RadarReason]:
        """Things worth an unprompted call after this lap. Doesn't mark them; call mark_announced()."""
        out: List[RadarReason] = []
        left = f" ({laps_to_go} laps left)" if laps_to_go else ""
        b = self.behind
        if b and not b.lapped and b.rate is not None:
            meet = b.laps_until(0.5)
            if b.rate <= -0.15 and b.gap <= 2.5 and self._fresh(f"threat:{b.name}", lap, 4):
                if laps_to_go and meet is not None and meet > laps_to_go:
                    when = f"at this rate he runs out of laps before he's on you{left}"
                elif meet is not None:
                    when = f"on your gearbox in ~{max(1, round(meet))} laps{left}"
                else:
                    when = f"already right with you{left}"
                out.append(RadarReason(
                    f"threat:{b.name}",
                    f"P{b.position} {b.name} behind is {_tenths(b.gap)} back and closing {abs(b.rate):.1f}s/lap - {when}",
                    urgent=b.gap <= 1.2))
            elif b.rate >= 0.3 and b.gap >= 2.0 and self._fresh(f"escaped:{b.name}", lap, 10):
                recent = [g for (_, n, g, _) in self._hist["behind"] if n == b.name]
                if recent and min(recent) <= 1.2:
                    out.append(RadarReason(
                        f"escaped:{b.name}",
                        f"you've broken away from P{b.position} {b.name}: gap out to {_tenths(b.gap)}, growing {b.rate:.1f}s/lap{left}"))
        a = self.ahead
        if a and not a.lapped and a.rate is not None and a.rate <= -0.2 and 0.8 < a.gap <= 6.0:
            meet = a.laps_until(0.5)
            horizon = laps_to_go if laps_to_go else 8
            if meet is not None and meet <= horizon and self._fresh(f"catch:{a.name}", lap, 5):
                out.append(RadarReason(
                    f"catch:{a.name}",
                    f"you're catching P{a.position} {a.name} ahead: {_tenths(a.gap)}, closing {abs(a.rate):.1f}s/lap - "
                    f"with him in ~{max(1, round(meet))} laps{left}"))
        # The pace instruction changed (e.g. fight over -> manage, or fuel saving now skips the stop).
        # PUSH modes ride along with the threat / catch reasons above, so only the calm ones need their own call.
        mode = self.pace.mode
        if mode in ("MANAGE", "SAVE", "HOLD_ATTACK") and mode != self._announced_pace_mode and self._fresh(f"pace:{mode}", lap, 6):
            if mode != "HOLD_ATTACK" or not any(r.key.startswith("catch:") for r in out):
                out.append(RadarReason(f"pace:{mode}", self.pace.text, urgent=(mode == "SAVE")))
        elif mode.startswith("PUSH") and out:
            out[0].text += f" | {self.pace.text}"
        return out
