"""
Race radar: measured gaps to the cars directly ahead and behind.

The gap to each neighbour is sampled at the line and at every sector boundary, so the trend ("closing
four tenths a lap") is measured on the road within about two thirds of a lap of a car arriving, and we
know in which sector the time goes. A car that turns up behind on clearly faster laps is flagged at once
from the lap times, before any trend exists (Oulton, Nov 2026: Bill closed and passed in a lap and a
half without a word, because the old per-lap trend needed two full laps). From that it gives:

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

TREND_WINDOW_LAPS = 2.0    # the trend uses the samples from the last two laps
MIN_TREND_SAMPLES = 4      # ... at least four of them ...
MAX_SANE_RATE = 4.0        # s/lap: anything bigger is our own spin / a moment off, not pace
MIN_TREND_SPAN = 0.6       # ... spanning at least 0.6 of a lap
LAPTIME_THREAT_DELTA = 0.3 # a new car behind this much quicker per lap is a threat before any trend exists
# "Faster car on its way": cars further back (up to INCOMING_MAX_GAP s, INCOMING_DEPTH places) that are
# reeling us in. Called once, early, so the driver knows it's coming - not when it's already on him.
INCOMING_MAX_GAP = 15.0
INCOMING_MIN_GAP = 1.0
INCOMING_DEPTH = 6
INCOMING_MIN_RATE = 0.25   # s/lap
INCOMING_MIN_SPAN = 1.0    # measured over at least a lap


@dataclass
class RivalTrend:
    name: str
    position: int
    gap: float                 # seconds, always positive
    rate: Optional[float]      # change of the gap per lap: negative = gap shrinking
    laps: float                # laps the rate is measured over
    lapped: bool               # a lap or more away - not a fight
    estimated: bool = False    # rate guessed from lap times (no on-road trend yet)
    hot_sector: int = 0        # 1-3: where the gap changes most in his favour (0 = unknown)

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


def robust_rate(samples: List[Tuple[float, float]]) -> Optional[float]:
    """
    Change of the gap per lap from (lap position, gap) samples: the median of the rates between consecutive
    samples. One sudden jump - our own spin or a moment off track - moves one segment, not the answer
    (with a straight-line fit our spin at Oulton made every car behind "close" at 7 s a lap).
    """
    rates = []
    for (x0, y0), (x1, y1) in zip(samples, samples[1:]):
        if x1 - x0 > 1e-6:
            rates.append((y1 - y0) / (x1 - x0))
    if not rates:
        return None
    rates.sort()
    n = len(rates)
    r = rates[n // 2] if n % 2 else 0.5 * (rates[n // 2 - 1] + rates[n // 2])
    return r if abs(r) <= MAX_SANE_RATE else None


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


def quick_pace(lap_times: List[float]) -> Optional[float]:
    """
    What a car can do on a clean lap: the average of its two quickest of the last five laps (laps over 5% off
    are ignored). Out-laps, traffic and moments don't drag it down the way a median does.
    """
    laps = [t for t in (lap_times or [])[-5:] if t and t > 20]
    if not laps:
        return None
    best = min(laps)
    clean = sorted(t for t in laps if t <= best * 1.05)[:2]
    return sum(clean) / len(clean)


class RaceRadar:
    def __init__(self):
        self.reset()

    def reset(self):
        # side -> [(lap position, name, gap, lapped, sector that just ended 1-3)]
        self._hist: Dict[str, List[Tuple[float, str, float, bool, int]]] = {"ahead": [], "behind": []}
        self._my_ref_pace: Optional[float] = None
        self._my_quick_pace: Optional[float] = None
        # name -> [(lap position, gap behind us)] for the cars a few places back
        self._field: Dict[str, List[Tuple[float, float]]] = {}
        self._field_pos: Dict[str, int] = {}
        self._field_laps: Dict[str, List[float]] = {}     # their recent lap times
        # cars on another lap near us on the road: (name, position, road gap s, laps between)
        self.lappers: List[Tuple[str, int, float, int]] = []
        self.backmarkers: List[Tuple[str, int, float, int]] = []
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

    def sample_field(self, lap_pos: float, behind: List[Tuple], dirty: bool = False):
        """
        behind: (name, position, gap in seconds[, their last lap time]) for the few cars behind us, from the
        gap clock. Lap times are kept so pace can be compared on clean laps - an out-lap or a moment in
        traffic can hide a much quicker car from the on-road trend (Bill at Oulton: ~0.8 s a lap quicker,
        while the gap sat still during his out-lap).
        """
        if dirty:
            self._field.clear()
            return
        seen = set()
        for entry in behind:
            name, pos, gap = entry[0], entry[1], entry[2]
            last = entry[3] if len(entry) > 3 else 0.0
            if last and last > 20:
                laps = self._field_laps.setdefault(name, [])
                if not laps or abs(laps[-1] - last) > 1e-3:
                    laps.append(float(last))
                    del laps[:-6]
            if gap is None or gap <= 0 or gap > INCOMING_MAX_GAP * 1.5:
                continue
            seen.add(name)
            h = self._field.setdefault(name, [])
            if h and lap_pos <= h[-1][0]:
                continue
            h.append((lap_pos, float(gap)))
            while h and lap_pos - h[0][0] > TREND_WINDOW_LAPS + 1e-6:
                h.pop(0)
            self._field_pos[name] = pos
        for name in list(self._field):
            if name not in seen:
                del self._field[name]

    def note_laps(self, participants):
        """Keep every car's recent lap times (call at the line / sector boundaries)."""
        for p in participants or ():
            last = getattr(p, "last_lap_time", 0.0) or 0.0
            if not p.name or last <= 20:
                continue
            laps = self._field_laps.setdefault(p.name, [])
            if not laps or abs(laps[-1] - last) > 1e-3:
                laps.append(float(last))
                del laps[:-6]

    def set_traffic(self, lappers, backmarkers):
        self.lappers, self.backmarkers = list(lappers or []), list(backmarkers or [])

    def _laps_until(self, name: str, road_gap: float, coming_to_us: bool) -> Optional[float]:
        """Laps until we meet on the road, from clean-lap pace (None if nobody is closing)."""
        pd = self._pace_delta(name)          # positive = he's quicker than us
        if pd is None:
            return None
        closing = pd if coming_to_us else -pd
        if closing < 0.2:
            return None
        return road_gap / closing

    def traffic_lines(self) -> List[str]:
        out = []
        for name, pos, gap, laps in self.lappers[:2]:
            meet = self._laps_until(name, gap, True)
            when = f", with you in ~{max(1, round(meet))} laps" if meet is not None else ""
            out.append(f"  COMING TO LAP YOU: P{pos} {name} ({laps} lap{'s' if laps > 1 else ''} up) is {gap:.1f}s behind on the road{when}")
        for name, pos, gap, laps in self.backmarkers[:2]:
            meet = self._laps_until(name, gap, False)
            when = f", you'll be on him in ~{max(1, round(meet))} laps" if meet is not None else ""
            out.append(f"  BACKMARKER AHEAD: P{pos} {name} ({laps} lap{'s' if laps > 1 else ''} down) is {gap:.1f}s up the road{when}")
        return out

    def _pace_delta(self, name: str) -> Optional[float]:
        """How much quicker per lap he is on clean laps (positive = he's quicker), or None if not known."""
        laps = self._field_laps.get(name) or []
        mine = self._my_quick_pace or self._my_ref_pace
        if mine is None or len(laps) < 2:
            return None
        theirs = quick_pace(laps)
        if theirs is None:
            return None
        return mine - theirs

    def incoming(self, laps_to_go: Optional[int] = None) -> List[Tuple[str, int, float, float, Optional[float]]]:
        """Cars behind that are quicker and coming: (name, position, gap, closing s/lap, laps until with us)."""
        out = []
        for name, h in self._field.items():
            if not h:
                continue
            span = h[-1][0] - h[0][0] if h else 0.0
            enough = (len(h) >= MIN_TREND_SAMPLES and span >= INCOMING_MIN_SPAN) or (len(h) >= 3 and span >= 1.99)
            road = robust_rate(h) if enough else None
            pace = self._pace_delta(name)
            # Clean-lap pace is the truer measure of who is quicker; the road trend is the fallback
            if pace is not None and pace >= INCOMING_MIN_RATE:
                rate = -min(pace, MAX_SANE_RATE)
            elif pace is not None and pace < 0.1:
                continue            # he isn't quicker on clean laps, whatever the gap did lately
            else:
                rate = road
            if rate is None:
                continue
            gap = h[-1][1]
            if rate > -INCOMING_MIN_RATE or not (INCOMING_MIN_GAP <= gap <= INCOMING_MAX_GAP):
                continue
            meet = max(0.0, gap - 0.5) / -rate
            out.append((name, self._field_pos.get(name, 0), gap, -rate, meet))
        out.sort(key=lambda r: r[4] if r[4] is not None else 99)
        return out

    def set_my_pace(self, ref_pace: Optional[float], my_laps: Optional[List[float]] = None):
        """Our representative clean lap, for judging a new car behind by its lap times."""
        self._my_ref_pace = ref_pace if ref_pace and ref_pace > 20 else None
        q = quick_pace(my_laps) if my_laps else None
        self._my_quick_pace = q or self._my_ref_pace

    @staticmethod
    def _rival_pace(rival) -> Optional[float]:
        last, best = getattr(rival, "last_lap_time", 0.0) or 0.0, getattr(rival, "best_lap_time", 0.0) or 0.0
        if last > 20 and (best <= 20 or last <= best * 1.03):
            return last
        return best if best > 20 else None

    def sample(self, completed_lap: int, snapshot, pace: float, dirty: bool = False,
               lap_pos: Optional[float] = None, sector_ended: int = 3):
        """
        Call when we cross the line (lap_pos = completed laps) and at each sector boundary
        (lap_pos = completed laps + fraction). dirty=True (our pit lap) restarts the trends.
        """
        if lap_pos is None:
            lap_pos = float(completed_lap)
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
            if hist and lap_pos <= hist[-1][0]:
                continue                  # same point sampled twice
            lapped = gap >= 0.9 * pace
            hist.append((lap_pos, rival.name, float(gap), lapped, int(sector_ended)))
            while hist and lap_pos - hist[0][0] > TREND_WINDOW_LAPS + 1e-6:
                hist.pop(0)
            rate, laps, estimated = None, 0.0, False
            span = hist[-1][0] - hist[0][0]
            if (len(hist) >= MIN_TREND_SAMPLES and span >= MIN_TREND_SPAN) or (len(hist) >= 3 and span >= 1.99):
                rate = robust_rate([(h[0], h[2]) for h in hist])
                laps = span
            elif side == "behind" and not lapped and self._my_ref_pace and gap <= 5.0:
                # No on-road trend yet: a car arriving behind on clearly quicker laps is closing
                theirs = self._rival_pace(rival)
                if theirs and self._my_ref_pace - theirs >= LAPTIME_THREAT_DELTA:
                    rate, estimated = -(self._my_ref_pace - theirs), True
            hot = 0
            if len(hist) >= 2:
                by_sector: Dict[int, float] = {}
                for prev_h, h in zip(hist, hist[1:]):
                    by_sector[h[4]] = by_sector.get(h[4], 0.0) + (h[2] - prev_h[2])
                if by_sector:
                    # behind: the sector where the gap shrinks most; ahead: where we close most
                    sec, delta = min(by_sector.items(), key=lambda kv: kv[1])
                    if delta <= -0.1:
                        hot = sec
            setattr(self, side, RivalTrend(rival.name, rival.position, float(gap), rate, laps, lapped, estimated, hot))

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
            return head + " | trend: measuring (a few more corners)"
        r = t.rate
        over = "from his lap times - no on-road trend yet" if t.estimated else f"measured over the last {t.laps:.1f} laps"
        if abs(r) < 0.1:
            trend = f"gap steady ({over})"
        elif side == "ahead":
            trend = (f"YOU are closing {abs(r):.1f}s/lap ({over})" if r < 0
                     else f"HE is pulling away {r:.1f}s/lap ({over})")
        else:
            trend = (f"HE is closing on you {abs(r):.1f}s/lap ({over})" if r < 0
                     else f"YOU are pulling away {r:.1f}s/lap ({over})")
        if t.hot_sector and r < -0.1:
            trend += (f"; he gains most in Sector {t.hot_sector}" if side == "behind"
                      else f"; you gain most in Sector {t.hot_sector}")
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
        if b and not b.lapped and b.rate is not None and b.rate <= -0.1 and b.gap <= 3.0:
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
        lines = ["[WHAT MATTERS NOW — CARS AROUND YOU, gaps measured on the road at the line and every sector]"]
        a = self._neighbour(snapshot, -1)
        b = self._neighbour(snapshot, +1)
        if a is None:
            lines.append("  Car AHEAD: none - you are leading" if snapshot.position == 1 else "  Car AHEAD: unknown")
        else:
            lines.append(self._describe("ahead", a.name, a.position, snapshot.split_time_ahead))
        if a is not None and 0 < snapshot.split_time_ahead <= 3.0:
            v = self._ahead_line(a.name)
            if v:
                lines.append(f"  PACE vs CAR AHEAD: {v}")
        if b is None:
            lines.append("  Car BEHIND: none - you are last")
        else:
            lines.append(self._describe("behind", b.name, b.position, snapshot.split_time_behind))
            pd = self._pace_delta(b.name)
            if pd is not None and pd >= 0.2 and 0 < snapshot.split_time_behind <= 4.0:
                lines.append(f"  PACE vs CAR BEHIND: he is {pd:.1f}s/lap quicker on clean laps")
        for name, pos, gap, rate, meet in self.incoming()[:2]:
            if b is not None and name == b.name and gap <= 3.0:
                continue
            lines.append(f"  FASTER CAR COMING: P{pos} {name} {gap:.1f}s back, about {rate:.1f}s/lap quicker on clean laps"
                         f" -> with you in ~{max(1, round(meet))} laps at this rate")
        lines.extend(self.traffic_lines())
        if self.pace.text:
            lines.append(f"  PACE PLAN (what the pit wall wants): {self.pace.text}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ proactive triggers
    FINAL_LAPS_DEFEND = 4     # in the last few laps a position is worth defending; before that, let a quicker car go

    def _behind_advice(self, quicker_by: float, laps_to_go: Optional[int]) -> str:
        """Facts only - the engineer decides (defend, or let him by and use his tow)."""
        left = f"{laps_to_go} laps left" if laps_to_go is not None else "laps left unknown"
        return f"Your call on the plan for when he arrives ({left})"

    def _ahead_line(self, name: str) -> str:
        """Clean-lap pace of the car in front vs ours, as a fact, or ""."""
        pd = self._pace_delta(name)
        if pd is None:
            return ""
        if abs(pd) < 0.1:
            return "he runs your pace on clean laps (within a tenth)"
        return f"he is {abs(pd):.1f}s/lap {'QUICKER' if pd > 0 else 'SLOWER'} than you on clean laps"

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

    def urgent_threat(self, lap: int, laps_to_go: Optional[int] = None) -> List[RadarReason]:
        """Mid-lap check: only a new, close car closing in from behind is worth breaking in for."""
        return [r for r in self.candidates(lap, laps_to_go, include_pace=False)
                if r.key.startswith("threat:") and r.urgent]

    def candidates(self, lap: int, laps_to_go: Optional[int] = None, include_pace: bool = True) -> List[RadarReason]:
        """Things worth an unprompted call after this lap. Doesn't mark them; call mark_announced()."""
        out: List[RadarReason] = []
        left = f" ({laps_to_go} laps left)" if laps_to_go else ""
        for name, pos, gap, rate, meet in self.incoming(laps_to_go)[:1]:
            if laps_to_go and meet is not None and meet > laps_to_go - 0.5:
                continue          # he runs out of laps before he gets to us: not worth the radio
            if not self._fresh(f"incoming:{name}", lap, 8) or not self._fresh(f"threat:{name}", lap, 4):
                continue
            out.append(RadarReason(
                f"incoming:{name}",
                f"HEADS-UP, faster car on its way: P{pos} {name} is {gap:.1f}s behind and quicker by {rate:.1f}s a lap - "
                f"with you in about {max(1, round(meet))} laps{left}. Not a fight yet - tell him it's coming and the plan "
                f"for when he arrives: {self._behind_advice(rate, laps_to_go)}"))
        b = self.behind
        if b and not b.lapped and b.rate is not None:
            meet = b.laps_until(0.5)
            already = any(r.key == f"incoming:{b.name}" for r in out)
            # Not when he's already on the gearbox (under ~0.8 s): the driver sees him, a call adds nothing
            if b.rate <= -0.15 and 0.8 <= b.gap <= 3.0 and not already and self._fresh(f"threat:{b.name}", lap, 4) \
                    and self._fresh(f"incoming:{b.name}", lap, 3):
                if laps_to_go and meet is not None and meet > laps_to_go:
                    when = f"at this rate he runs out of laps before he's on you{left}"
                elif meet is not None:
                    when = f"on your gearbox in ~{max(1, round(meet))} laps{left}"
                else:
                    when = f"already right with you{left}"
                where = f", gaining most in Sector {b.hot_sector}" if b.hot_sector else ""
                how = " (judged from his lap times)" if b.estimated else ""
                out.append(RadarReason(
                    f"threat:{b.name}",
                    f"P{b.position} {b.name} behind is {_tenths(b.gap)} back and closing {abs(b.rate):.1f}s/lap{how}{where} - {when}. "
                    f"{self._behind_advice(abs(b.rate), laps_to_go)}",
                    urgent=b.gap <= 2.0))
            elif b.rate >= 0.3 and b.gap >= 2.0 and self._fresh(f"escaped:{b.name}", lap, 10):
                recent = [h[2] for h in self._hist["behind"] if h[1] == b.name]
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
        # The car in front, once we're with him (e.g. rejoining behind someone after the stop): is he holding us up
        # (fight him) or as quick as us (sit in the tow)?
        if a and not a.lapped and a.gap <= 2.0 and self._fresh(f"ahead:{a.name}", lap, 10) \
                and not any(r.key == f"catch:{a.name}" for r in out):
            verdict = self._ahead_line(a.name)
            if verdict:
                out.append(RadarReason(f"ahead:{a.name}",
                                       f"You're with P{a.position} {a.name} ahead ({_tenths(a.gap)}): {verdict}{left}"))
        # The pace instruction changed (e.g. fight over -> manage, or fuel saving now skips the stop).
        # PUSH modes ride along with the threat / catch reasons above, so only the calm ones need their own call.
        # Lapped traffic: a heads-up a couple of laps before it matters, once per car
        for name, pos, gap, laps in self.lappers[:1]:
            meet = self._laps_until(name, gap, True)
            if (meet is not None and meet <= 4.0) or gap <= 6.0:
                if self._fresh(f"lapper:{name}", lap, 6) and (not laps_to_go or (meet or 0) < laps_to_go):
                    with_him = [n for n, _, g, _ in self.lappers[1:] if abs(g - gap) <= 1.5]
                    fight = f", fighting {with_him[0]} as they come" if with_him else ""
                    when = f"about {max(1, round(meet))} laps away" if meet is not None else "close behind"
                    out.insert(0, RadarReason(f"lapper:{name}",
                                              f"Blue flags coming: P{pos} {name} ({laps} lap up) is {gap:.1f}s behind on the road, "
                                              f"{when}{fight}{left}"))
        for name, pos, gap, laps in self.backmarkers[:1]:
            meet = self._laps_until(name, gap, False)
            if meet is not None and meet <= 4.0 and self._fresh(f"backmarker:{name}", lap, 6):
                out.append(RadarReason(f"backmarker:{name}",
                                       f"Backmarker ahead: P{pos} {name} ({laps} lap down) is {gap:.1f}s up the road, "
                                       f"you'll be on him in ~{max(1, round(meet))} laps{left}"))
        out = out[:2]          # one radio call, two topics at most
        mode = self.pace.mode
        if not include_pace:
            return out
        if mode in ("MANAGE", "SAVE", "HOLD_ATTACK") and mode != self._announced_pace_mode and self._fresh(f"pace:{mode}", lap, 6):
            if mode != "HOLD_ATTACK" or not any(r.key.startswith("catch:") for r in out):
                out.append(RadarReason(f"pace:{mode}", self.pace.text, urgent=(mode == "SAVE")))
        elif mode.startswith("PUSH") and out:
            out[0].text += f" | {self.pace.text}"
        return out
