"""The race's major incidents, worked out from the race log and its timing file, for the camera to cut to.

The AI camera director plans one pass over hundreds of log lines and mostly follows the front, so crashes
further down the field were never on screen (and the event filter only keeps what is on screen). This finds
them in code instead:

- a car stopped on track (the logger's "slow/stopped on track" alerts), or
- a car losing MAJOR_PLACES or more places in PLACES_WINDOW seconds without stopping (a spin or an off it
  drove away from).

Each incident is anchored to when it started going wrong, not when the logger noticed: a car only counts as
stopped once it is below 2 m/s, a few seconds after the spin began. The cut comes CUT_LEAD seconds before
that, so the shot looks like a broadcast reacting to it.

Stops close together in time and on track are one pile-up. The camera follows the car that was highest
placed when it started, as that's usually where the contact came from.

Run it on a race to see what it finds:  python incidents.py "Races/<race>/01_race_log.txt"
"""
import json
import os
import re
import sys

MAJOR_PLACES = 5       # places lost in PLACES_WINDOW (without stopping) that count as a major incident
PLACES_WINDOW = 20.0   # seconds
ONSET_LOOKBACK = 10.0  # how far before a stop alert to look for the car starting to lose places
STOP_LAG = 3.0         # seconds from a spin starting to the stop alert, when the log shows nothing earlier
CUT_LEAD = 2.0         # the cut comes this long before the incident starts
MIN_HOLD = 8.0         # seconds the camera stays on an incident
MAX_HOLD = 15.0
PILEUP_MAX_HOLD = 20.0
# A stop is only major (worth a cut) if it cost this many places, the car retired, or it was running in the
# top few. Cheaper ones (a lone spin at the back) are minor: a word from the commentators, no cut.
MAJOR_STOP_PLACES = 2
COST_WINDOW = 40.0     # seconds after a stop that its cost in places is measured over
TOP_PLACES = 3
RETIRE_WITHIN = 150.0  # a retirement this soon after a stop makes the stop major
REPEAT_STOP = 60.0     # a car stopping again this soon after its last stop is the same incident
FINISH_BEFORE = 15.0   # no incident cuts from this long before the winner takes the flag ...
FINISH_AFTER = 10.0    # ... to this long after
MIN_RESUME = 10.0      # don't go back to the planned shot if the plan's next shot starts within this
PILEUP_TIME = 8.0      # incidents starting this close together ...
PILEUP_METRES = 250.0  # ... and this close on track are one pile-up

TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2}) - \[(\w+)\] (.*)$")
OVERTAKE_RE = re.compile(r"^(.+?) overtook (.+?) for P(\d+)")
STOP_RE = re.compile(r"Alert: (.+?) \(P(\d+).*?slow/stopped on track(?: at ([^!]+))?!")
MOVING_RE = re.compile(r"Notice: (.+?) is back on the move")


def _seconds(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


def read_log(path):
    """[(seconds, category, message)] from a race log or combined log (RaceRoom or AMS2)."""
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = TIME_RE.match(line.strip())
            if not m:
                continue
            cat, msg = m.group(4), m.group(5)
            # The clock restarts with a new session or an AMS2 race restart: only the last race counts
            if cat == "SESSION" and (msg.startswith("Session Changed") or msg == "Race restarted."):
                events = [e for e in events if e[1] == "TRACK"]
            events.append((_seconds(*m.group(1, 2, 3)), cat, msg))
    return events


def read_timing(log_path):
    """[(log time, {name: (place, laps + lap fraction)})] for the race (the last session in the file)."""
    path = os.path.splitext(log_path)[0] + "_timing.jsonl"
    if not os.path.exists(path):
        return []
    snapshots = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["type"] == "session":
                snapshots = []
            elif record["type"] == "timing":
                snapshots.append((record["t"], {r[0]: (r[1], r[3] + (r[4] or 0.0)) for r in record["rows"]}))
    return snapshots


def _snapshot_at(timing, t, after=False):
    """The timing snapshot at or before t (or the first one at or after t)."""
    if after:
        return next((rows for st, rows in timing if st >= t), None)
    rows = None
    for st, snap in timing:
        if st > t:
            break
        rows = snap
    return rows


def _track_length(events):
    for _, cat, msg in events:
        m = re.match(r"Track Length: ([\d.]+)", msg)
        if cat == "TRACK" and m:
            return float(m.group(1))
    return 4000.0


def find_incidents(log_path):
    """Incidents, earliest first. Each one:
    {"major", "start", "cut", "hold_until", "driver", "position", "drivers", "kind", "corner", "summary"}
    Only major ones get a cut; minor ones are for the commentators to mention.
    """
    events = read_log(log_path)
    timing = read_timing(log_path)
    track_length = _track_length(events)

    # Every time a car was passed: name -> [log time], and every time it passed someone: name -> [(time, count)]
    passed, passing = {}, {}
    for t, cat, msg in events:
        m = OVERTAKE_RE.match(msg) if cat == "OVERTAKE" else None
        if m:
            victims = [v.strip() for v in m.group(2).split(",")]
            passing.setdefault(m.group(1), []).append((t, len(victims)))
            for v in victims:
                passed.setdefault(v, []).append(t)

    def net_lost(name, t0, t1):
        lost = sum(1 for t in passed.get(name, []) if t0 <= t <= t1)
        return lost - sum(n for t, n in passing.get(name, []) if t0 <= t <= t1)

    def place_before(name, t):
        rows = _snapshot_at(timing, t - 1.0)
        return rows[name][0] if rows and name in rows else None

    def timing_lost(name, t0, t1):
        """Places lost from just before t0 to the worst place by t1, from the game's own order (None without timing)."""
        before = place_before(name, t0)
        places = [rows[name][0] for st, rows in timing if t0 <= st <= t1 + 5.0 and name in rows]
        if before is None or not places:
            return None
        return max(places) - before

    def track_spot(name, t):
        """Metres round the lap where the car was, from the first snapshot after t."""
        rows = _snapshot_at(timing, t, after=True)
        if not rows or name not in rows:
            return None
        return (rows[name][1] % 1.0) * track_length

    found = []

    # Stopped on track
    back_moving = {}
    for t, cat, msg in events:
        m = MOVING_RE.search(msg) if cat == "ACCIDENT" else None
        if m:
            back_moving.setdefault(m.group(1), []).append(t)
    for t, cat, msg in events:
        m = STOP_RE.search(msg) if cat == "ACCIDENT" else None
        if not m:
            continue
        name = m.group(1)
        early = [pt for pt in passed.get(name, []) if t - ONSET_LOOKBACK <= pt <= t]
        start = min(early) if early else t - STOP_LAG
        moving = next((mt for mt in back_moving.get(name, []) if mt >= t), None)
        found.append({"start": start, "driver": name, "kind": "stopped", "corner": (m.group(3) or "").strip(),
                      "moving_again": moving, "spot": track_spot(name, t)})

    # Retired cars: name -> log time (their places lost afterwards aren't an incident)
    retired = {}
    for t, cat, msg in events:
        m = re.match(r"(.+?) (?:has not moved in 2 minutes|has retired|is DNF)", msg) if cat == "SESSION" else None
        if m:
            retired.setdefault(m.group(1), t)

    def while_stopped(name, t):
        """A stopped car losing places is already covered by its stop (until it moves again, or for good)."""
        if name in retired and t >= retired[name] - 125.0:
            return True
        return any(f["driver"] == name and f["start"] - ONSET_LOOKBACK <= t
                   <= max(f["start"], f["moving_again"] or f["start"]) + PLACES_WINDOW for f in found)

    # Big position losses without a stop
    for name, times in passed.items():
        last_end = -1e9
        for i, t0 in enumerate(times):
            if t0 <= last_end or while_stopped(name, t0):
                continue
            lost = net_lost(name, t0, t0 + PLACES_WINDOW)
            if lost < MAJOR_PLACES:
                continue
            confirmed = timing_lost(name, t0, t0 + PLACES_WINDOW)
            if confirmed is not None:
                if confirmed < MAJOR_PLACES:
                    continue
                lost = confirmed
            in_window = [t for t in times[i:] if t <= t0 + PLACES_WINDOW]
            last_end = in_window[-1]
            found.append({"start": t0, "driver": name, "kind": f"lost {lost} places", "corner": "",
                          "moving_again": None, "spot": track_spot(name, t0)})

    # A car stopping again soon after its last stop (stuck, or limping) is the same incident: no new cut
    found.sort(key=lambda f: f["start"])
    last_stop, kept = {}, []
    for f in found:
        prev = last_stop.get(f["driver"]) if f["kind"] == "stopped" else None
        if f["kind"] == "stopped":
            last_stop[f["driver"]] = f["start"]
        if prev is None or f["start"] - prev > REPEAT_STOP:
            kept.append(f)
    found = kept

    for f in found:
        f["position"] = place_before(f["driver"], f["start"])
        # Retiring only makes the car's last stop before it major, not every stop on the way
        later = any(o["driver"] == f["driver"] and f["start"] < o["start"] for o in found)
        f["retired"] = (f["driver"] in retired and not later
                        and retired[f["driver"]] - f["start"] <= RETIRE_WITHIN)
        if f["kind"] == "stopped":
            f["cost"] = timing_lost(f["driver"], f["start"], f["start"] + COST_WINDOW)
            if f["cost"] is None:
                f["cost"] = net_lost(f["driver"], f["start"], f["start"] + COST_WINDOW)
        else:
            f["cost"] = MAJOR_PLACES

    # Group into pile-ups: close in time and on track
    found.sort(key=lambda f: f["start"])
    groups = []
    for f in found:
        g = groups[-1] if groups else None
        # Cars arriving at a scene while an earlier car is still stopped there are part of the same pile-up
        still_stopped = g and f["start"] <= max(x["moving_again"] or x["start"] for x in g)
        if g and (f["start"] - g[-1]["start"] <= PILEUP_TIME or still_stopped) and _near(g, f, track_length):
            g.append(f)
        else:
            groups.append([f])

    incidents = []
    for g in groups:
        start = min(f["start"] for f in g)
        lead = min(g, key=lambda f: (f["position"] or 99, f["start"]))
        names = list(dict.fromkeys(f["driver"] for f in sorted(g, key=lambda f: f["position"] or 99)))
        moving = [f["moving_again"] for f in g if f["moving_again"]]
        longest = PILEUP_MAX_HOLD if len(names) > 1 else MAX_HOLD
        hold_until = min(max([start + MIN_HOLD] + [mt + 2.0 for mt in moving]), start + longest)
        corner = next((f["corner"] for f in g if f["corner"]), "")
        what = "pile-up" if len(names) > 1 else lead["kind"]
        where = f" at {corner}" if corner else ""
        major = any(f["cost"] >= MAJOR_STOP_PLACES or f["retired"] or (f["position"] or 99) <= TOP_PLACES for f in g)
        incidents.append({
            "major": major,
            "start": start,
            "cut": max(0.0, start - CUT_LEAD),
            "hold_until": hold_until,
            "driver": lead["driver"],
            "position": lead["position"],
            "drivers": names,
            "kind": what,
            "corner": corner,
            "summary": f"{what}{where}: {', '.join(names)}",
        })

    # The camera can only be in one place: when two major incidents overlap, keep the one further up the order
    shown = None
    for inc in incidents:
        if not inc["major"]:
            continue
        if shown and inc["cut"] < shown["hold_until"]:
            if (inc["position"] or 99) < (shown["position"] or 99):
                shown["major"], shown = False, inc
            else:
                inc["major"] = False
        else:
            shown = inc
    return incidents


def _near(group, f, track_length):
    if f["spot"] is None:
        return True
    for other in group:
        if other["spot"] is None:
            continue
        d = abs(other["spot"] - f["spot"])
        if min(d, track_length - d) <= PILEUP_METRES:
            return True
    return False


def clock(t):
    t = int(round(t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _timecode_seconds(tc):
    h, m, s = map(int, tc.split(":"))
    return h * 3600 + m * 60 + s


def _place_at(timing, name, t, fallback):
    rows = _snapshot_at(timing, t) or _snapshot_at(timing, t, after=True)
    return rows[name][0] if rows and name in rows else fallback


def add_incident_cuts(sequence, log_path, camera=None):
    """Adds a cut to every major incident to an AI camera plan, then goes back to what the plan was showing.

    camera: the camera for the incident shot (an outside one, e.g. "TV"), or None for a sim whose plan
    has no cameras (AMS2). The controller's own camera rules still run afterwards.
    Returns the new sequence and a list of the changes made.
    """
    try:
        incidents = [i for i in find_incidents(log_path) if i["major"]]
    except (OSError, ValueError) as e:
        return sequence, [f"Incident cuts skipped: {e}"]
    timing = read_timing(log_path)
    winner = next((t for t, cat, msg in read_log(log_path) if cat == "FINISH" and "took P1" in msg), None)

    seq = sorted((dict(item) for item in sequence), key=lambda item: _timecode_seconds(item["timecode"]))
    changes = []
    for inc in incidents:
        cut, end = int(round(inc["cut"])), int(round(inc["hold_until"]))
        # Never cut away from the winner taking the flag
        if winner is not None and cut < winner + FINISH_AFTER and end > winner - FINISH_BEFORE:
            changes.append(f"{clock(cut)}: {inc['summary']} -> no cut, the winner is finishing")
            continue
        times = [_timecode_seconds(item["timecode"]) for item in seq]
        before = [item for item, t in zip(seq, times) if t <= cut]
        inside = [item for item, t in zip(seq, times) if cut < t < end]
        after = [item for item, t in zip(seq, times) if t >= end]
        showing = before[-1] if before else None
        if showing and showing.get("driver") == inc["driver"] and not inside:
            continue # The plan is already on that car

        shot = {"timecode": clock(cut), "driver": inc["driver"],
                "position": _place_at(timing, inc["driver"], cut, inc["position"]), "incident": inc["summary"]}
        if camera:
            shot["camera"] = camera
        if before and _timecode_seconds(before[-1]["timecode"]) == cut:
            before.pop()

        # Back to whatever the plan would be showing by now, unless its next shot is about to start anyway
        resume = (inside or ([showing] if showing else []))[-1:]
        back = []
        next_start = _timecode_seconds(after[0]["timecode"]) if after else None
        if resume and resume[0].get("driver") != inc["driver"] and (next_start is None or next_start - end >= MIN_RESUME):
            back = [dict(resume[0], timecode=clock(end),
                         position=_place_at(timing, resume[0].get("driver"), end, resume[0].get("position")))]
            back[0].pop("incident", None)
        seq = before + [shot] + back + after
        dropped = f", {len(inside)} planned shot(s) replaced" if inside else ""
        changes.append(f"{clock(cut)}-{clock(end)}: cut to {inc['driver']} for {inc['summary']}{dropped}")
    return seq, changes


if __name__ == "__main__":
    for inc in find_incidents(sys.argv[1]):
        pos = f"P{inc['position']}" if inc["position"] else "P?"
        if inc["major"]:
            print(f"MAJOR cut {clock(inc['cut'])} -> {clock(inc['hold_until'])}  follow {inc['driver']} ({pos})  {inc['summary']}")
        else:
            print(f"minor     {clock(inc['start'])}              {inc['driver']} ({pos})  {inc['summary']}")
