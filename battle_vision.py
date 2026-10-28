"""What a battle actually looks like, for the loggers' [BATTLE] lines.

The loggers used to pick a random phrase ("closing in under braking", "trying to force a mistake") that had
nothing to do with what the cars were doing, so the commentary reacted to things that never happened. This
watches each close pair every tick and describes only what it measured, in words rather than numbers so the
commentators never read out gaps: how close the car behind is, whether it's closing or dropping back over the
last few seconds, whether that's happening under braking or on a straight, and where on the track.

Message shapes stay the same for the graphics parser: "Name (Pn)" for both cars, and "Side by side" in the
side-by-side lines.
"""
import collections

TREND_WINDOW = 5.0     # seconds the closing/dropping-back trend is measured over
TREND_METRES = 2.0     # the gap has to change by this much to count as closing or dropping back
HISTORY_SEC = 12.0
WATCH_METRES = 40.0    # pairs further apart than this are forgotten
BRAKING_DECEL = 4.0    # m/s per second of slowing that counts as braking
START_BATTLE_DELAY = 10.0  # battles are logged from this long after the green flag (the opening lap included)
START_REPORT_AT = 25.0     # seconds after the green flag for the "who gained/lost at the start" report
STRAIGHT_SHARE = 0.85  # this share of the car's top speed (so far this race) counts as a straight


class BattleVision:
    def __init__(self):
        self.hist = {}   # pair key -> deque of (t, gap metres, speed of the car behind)
        self.vmax = {}   # driver -> top speed seen this session

    def reset(self):
        self.hist.clear()
        self.vmax.clear()

    def observe(self, key, t, gap, behind, speed_behind):
        """Called every tick for adjacent cars."""
        self.vmax[behind] = max(self.vmax.get(behind, 0.0), speed_behind)
        if gap > WATCH_METRES:
            self.hist.pop(key, None)
            return
        h = self.hist.setdefault(key, collections.deque())
        h.append((t, gap, speed_behind))
        while h and t - h[0][0] > HISTORY_SEC:
            h.popleft()

    def _trend(self, key, t, gap):
        """'closing', 'dropping' or 'holding' over the last TREND_WINDOW seconds."""
        old = next((s for s in self.hist.get(key, ()) if t - s[0] <= TREND_WINDOW), None)
        if old is None or t - old[0] < 2.0:
            return 'holding'
        if gap - old[1] < -TREND_METRES:
            return 'closing'
        if gap - old[1] > TREND_METRES:
            return 'dropping'
        return 'holding'

    def _phase(self, key, t, behind, speed):
        """'braking', 'straight' or 'corner' for the car behind right now."""
        h = self.hist.get(key, ())
        prev = next((s for s in reversed(h) if 0.8 <= t - s[0] <= 2.0), None)
        if prev and (prev[2] - speed) / (t - prev[0]) > BRAKING_DECEL:
            return 'braking'
        if speed > STRAIGHT_SHARE * self.vmax.get(behind, 0.0) and self.vmax.get(behind, 0.0) > 20:
            return 'straight'
        return 'corner'

    @staticmethod
    def _where(phase, corner):
        if corner:
            return f" into {corner}" if phase == 'braking' else f" through {corner}"
        return " on the straight" if phase == 'straight' else ""

    def side_by_side(self, key, t, behind, pos_b, ahead, pos_a, speed, corner, was_drafting, count):
        where = self._where(self._phase(key, t, behind, speed), corner)
        if count > 1:
            return f"Side by side again! {behind} (P{pos_b}) and {ahead} (P{pos_a}){where}!"
        if was_drafting:
            return f"Side by side! {behind} (P{pos_b}) pulls alongside {ahead} (P{pos_a}){where}!"
        return f"Side by side! {behind} (P{pos_b}) is alongside {ahead} (P{pos_a}){where}!"

    def drafting(self, key, t, gap, behind, pos_b, ahead, pos_a, speed, corner, count):
        trend = self._trend(key, t, gap)
        phase = self._phase(key, t, behind, speed)
        if gap < 6:
            near = f"right on the gearbox of {ahead} (P{pos_a})"
        elif gap < 10:
            near = f"a car length or two behind {ahead} (P{pos_a})"
        else:
            near = f"a few lengths behind {ahead} (P{pos_a})"
        if trend == 'closing':
            move = {'braking': ', closing up under braking', 'straight': ', closing in the slipstream'}.get(
                phase, ', closing')
        elif trend == 'dropping':
            move = ', but dropping back'
        else:
            move = ', holding station' if count > 1 else ''
        return f"{behind} (P{pos_b}) is {near}{move}{self._where(phase, corner)}."


def start_report(grid, positions):
    """'Start report' line: who gained and lost the most since the grid. grid = names in grid order,
    positions = {name: current position}. None if nobody has moved."""
    moves = []
    for g, name in enumerate(grid, 1):
        pos = positions.get(name)
        if pos:
            moves.append((g - pos, name, g, pos))
    if not moves or all(m[0] == 0 for m in moves):
        return None
    moves.sort(key=lambda m: -m[0])
    bits = []
    gainers = [m for m in moves if m[0] > 0][:3]
    losers = [m for m in reversed(moves) if m[0] < 0][:2]
    if gainers:
        bits.append("gained: " + ", ".join(f"{n} P{g} to P{p} (+{d})" for d, n, g, p in gainers))
    if losers:
        bits.append("lost: " + ", ".join(f"{n} P{g} to P{p} ({d})" for d, n, g, p in losers))
    leader = next((n for n, p in positions.items() if p == 1), None)
    if leader:
        bits.append(f"{leader} leads")
    return "Start report: " + "; ".join(bits) + "."
