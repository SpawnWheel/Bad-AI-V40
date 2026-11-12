"""Works out where a race ended when the game never said so, which is every AMS2 replay.

In a replay AMS2 shares the cars (positions, laps, distance round the lap) but none of the things
that mark the finish: no "finished" race state for the session or for any car, no laps or time
remaining. So the finish is read back from what the cars did, once the recording has stopped:

- The winner is the first car to complete the highest lap count. Every other car finishes the next
  time it crosses the line after that, as in any race.
- That is only taken as the finish when the cars then stopped racing (slowed right down, parked or
  left) or the recording stopped before that could show. Cars still lapping at racing speed mean the
  recording was stopped mid-race, and no finish is written.
- If a replay is left running long enough for a slow lap after the flag to be counted, that lap is
  recognised as the cool-down lap and the finish stays on the lap before it.

infer_finish() takes plain data, so it can be checked against a recorded race without the sim.
"""
import datetime
import re

MIN_EVIDENCE_SECONDS = 30.0  # how long after its line crossing before a car's pace says anything
SLOW_LAP = 1.35              # a lap this many times a car's best lap was not a racing lap
SLOW_PACE = 0.70             # covering under this share of the racing distance since the line: stopped racing
MAX_COOL_DOWN_LAPS = 3       # how many counted laps after the flag are undone at most

LINE_RE = re.compile(r"^\s*(\d+):(\d{2}):(\d{2})\s*-\s*\[(\w+)\]\s*(.*)$")
# Lines that stay even when they name a driver who has already finished
KEEP_CATEGORIES = ("TRACK", "SYSTEM", "FINISH", "FASTEST_LAP")


def _best_lap(crossings, upto):
    """A car's usual lap time over the laps it completed up to crossing number `upto`: the median, so
    one lap timed short (a fast-forwarded replay is read in coarse steps) or long (a pit stop, a spin)
    doesn't move it."""
    laps = sorted(crossings[i][0] - crossings[i - 1][0] for i in range(1, upto + 1)
                  if crossings[i][1] == crossings[i - 1][1] + 1)
    return laps[len(laps) // 2] if laps else None


def _lead_laps(crossings):
    """{laps: (time, driver)} - who completed each lap count first, and when."""
    lead = {}
    for name, cs in crossings.items():
        for t, laps, _pos, _dist in cs:
            if laps not in lead or t < lead[laps][0]:
                lead[laps] = (t, name)
    return lead


def _after_crossing(cs, i, last_dist, end_time, track_length):
    """What a car did after its crossing number i: 'slowed', 'racing', or None when it can't be told."""
    best = _best_lap(cs, i)
    if best is None:
        return None
    if i + 1 < len(cs):  # it completed another lap: was that one a racing lap?
        return "slowed" if cs[i + 1][0] - cs[i][0] > SLOW_LAP * best else "racing"
    elapsed = end_time - cs[i][0]
    if elapsed < MIN_EVIDENCE_SECONDS or last_dist is None:
        return None
    racing_distance = elapsed / best * track_length
    return "slowed" if last_dist - cs[i][3] < SLOW_PACE * racing_distance else "racing"


def _tally(crossings, last_dists, end_time, track_length, flag_time):
    """Takes flag_time as the chequered flag: who finishes (each car's first crossing from then on)
    and how many of them then slowed or kept racing."""
    slowed = racing = 0
    finishers = []
    for name, cs in crossings.items():
        i = next((k for k, c in enumerate(cs) if c[0] >= flag_time), None)
        if i is None:
            continue
        finishers.append((cs[i][0], name, cs[i][2], cs[i][1]))
        state = _after_crossing(cs, i, last_dists.get(name), end_time, track_length)
        slowed += state == "slowed"
        racing += state == "racing"
    return slowed, racing, finishers


def infer_finish(crossings, last_dists, end_time, track_length, safety_car_laps=()):
    """Where the race ended, from what the cars did.

    crossings: {driver: [(time, laps completed, position, total distance), ...]} - one per completed lap
    last_dists: {driver: total distance when last seen}
    end_time: sim time when the recording stopped
    safety_car_laps: lap counts the leader completed behind the safety car (slow, but real, laps)

    Returns (result, reason). result is None when no finish can be claimed, else a dict with
    "winner", "lap", "time" and "finishers" [(time, driver, position)] in crossing order."""
    lead = _lead_laps(crossings)
    if not lead:
        return None, "no car completed a lap"
    track_length = track_length if track_length and track_length > 0 else 4000.0
    lap = max(lead)

    # A replay left running after the flag can count the cool-down lap. Step back over a slow last
    # lap when most cars stopped racing at the end of the lap before it.
    stepped = 0
    while stepped < MAX_COOL_DOWN_LAPS and lap - 1 in lead and lap not in safety_car_laps:
        t, setter = lead[lap]
        cs = crossings[setter]
        i = next(k for k, c in enumerate(cs) if c[1] == lap)
        best = _best_lap(cs, i - 1) if i > 0 else None
        if best is None or t - cs[i - 1][0] <= SLOW_LAP * best:
            break  # the last lap was a racing lap
        slowed, racing, _ = _tally(crossings, last_dists, end_time, track_length, lead[lap - 1][0])
        if slowed + racing == 0 or slowed < 0.6 * (slowed + racing):
            break  # the field raced on after the lap before, so the slow lap was part of the race
        lap -= 1
        stepped += 1

    flag_time, winner = lead[lap]
    slowed, racing, finishers = _tally(crossings, last_dists, end_time, track_length, flag_time)
    if not stepped and racing > slowed:
        return None, "the cars were still racing when the recording stopped"

    finishers.sort(key=lambda f: (f[0], f[1] != winner))
    by_result = sorted(finishers, key=lambda f: (f[1] != winner, -f[3], f[0]))
    rank = {f[1]: n + 1 for n, f in enumerate(by_result)}
    rows = []
    for t, name, pos, _laps in finishers:
        if name == winner:
            pos = 1
        elif pos <= 1:  # the game's positions wobble once the leader is home
            pos = rank[name]
        rows.append((t, name, pos))
    return {"winner": winner, "lap": lap, "time": flag_time, "finishers": rows,
            "slowed": slowed, "racing": racing, "cool_down_laps": stepped}, ""


def finish_line(t, name, pos):
    stamp = datetime.timedelta(seconds=max(0, int(t)))
    if pos == 1:
        return f"{stamp} - [FINISH] Checkered Flag! {name} took P1!"
    return f"{stamp} - [FINISH] Checkered Flag! {name} comes past the line to take P{pos}."


def apply_to_log(lines, finishers):
    """The race log with the finish written in: a [FINISH] line for each car at the moment it crossed
    the line, and nothing logged about a car after it finished (once the leader is home the game's
    positions shuffle, which reads as overtakes, battles and retirements that never happened)."""
    finished_at = {name: int(t) for t, name, _pos in finishers}
    named = {name: re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])")
             for name in finished_at}
    kept = []  # (seconds, line)
    seconds = 0
    for line in lines:
        m = LINE_RE.match(line)
        if m:
            seconds = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if m.group(4) not in KEEP_CATEGORIES and any(
                    seconds > at and named[name].search(m.group(5)) for name, at in finished_at.items()):
                continue
        kept.append((seconds, line))
    for t, name, pos in finishers:
        at = int(t)
        i = len(kept)
        while i > 0 and kept[i - 1][0] > at:
            i -= 1
        kept.insert(i, (at, finish_line(t, name, pos)))
    return [line for _seconds, line in kept]
