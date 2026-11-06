"""Race Visualizer: the whole race replayed in about 90 seconds as a small pixel-art video.

Every car is a top-down GT3 on its own row, ordered by position, and slides left and right with
its real distance behind the leader, while the lap lines scroll past. Cars in the pit lane show
PIT, the fastest lap holder carries a stopwatch, retired cars fall off the back, and at the flag
the finishers line up on the finish line with their gaps.

Built from the live timing the loggers record (01_race_log_timing.jsonl). Tyre life is not
recorded, so the bar next to each car is an estimate from the laps on the current set.

    python visualizer.py "<race folder>" [--out video.mp4] [--seconds 90] [--scale 2]
"""
import argparse
import bisect
import json
import math
import os
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import racefiles  # noqa: E402

from pixelart import (car_size, car_sprite, draw_text, luminance, mix, plain, rgb, shade,  # noqa: E402
                      stopwatch, text_width)

OUTPUT_NAME = "13_race_visualized.mp4"

W, H = 640, 360            # logical canvas; the video is this scaled up with nearest-neighbour
FPS = 30
INTRO = 3.4                # seconds of grid and start lights before the race clock runs
OUTRO = 7.0                # seconds the result is held at the end
HEADER_H = 20
ROWS_TOP = 38
ROWS_BOTTOM = H - 8
LEAD_X_MAX = W - 52        # the leader's nose never goes further right than this
FIELD_X_MIN = 76           # nor the last car's nose further left
VIEW_MIN, VIEW_MAX = 1.0, 2.6   # laps visible across the screen
SWAP_SEC = 1.1             # an overtake takes this long to swap rows
FALL_SEC = 1.6             # retired cars take this long to fall off

FINISH_FINISHED = 1
OUT_STATUS = {2: "DNF", 3: "DNQ", 4: "DNS", 5: "DSQ"}
PHASE_GREEN = 5

BG = (24, 31, 48)
BG_ALT = (27, 35, 54)
HEADER_BG = (9, 13, 24)
ACCENT = (224, 53, 43)
LINE = (47, 59, 86)
LAP_LABEL = (52, 66, 96)
TEXT = (238, 242, 248)
MUTED = (150, 162, 186)
DIM = (86, 99, 128)
PIT = (51, 194, 122)
PIT_BG = (12, 40, 27)
PURPLE = (170, 70, 255)
GOLD = (255, 196, 0)
YELLOW = (255, 214, 0)
RED = (255, 84, 84)

# (body, stripe) per car, in grid order, unless the graphics setup gives a driver a colour
PALETTE = [
    ("#e8433a", "#ffffff"), ("#3a7bf0", "#ffd23f"), ("#ff9a1f", "#1b1b1f"), ("#2fbf63", "#ffffff"),
    ("#f2f2f2", "#e8433a"), ("#a066ff", "#ffffff"), ("#ffd23f", "#1b1b1f"), ("#22c9c0", "#0b3536"),
    ("#ff6fae", "#ffffff"), ("#9dd94f", "#163a1a"), ("#5ec8ff", "#1f3f7f"), ("#c9a13f", "#1b1b1f"),
    ("#ff6f5e", "#ffffff"), ("#b8c0cc", "#2a5bd7"), ("#2f55b0", "#ff9a1f"), ("#a8343f", "#ffd23f"),
    ("#7ff0c4", "#1b1b1f"), ("#d27bff", "#2a1b4b"), ("#8a9a2a", "#ffffff"), ("#a86b3c", "#f2e2c4"),
    ("#35363d", "#36e07a"), ("#ff4fd0", "#1b1b1f"), ("#6d7480", "#ff5a5a"), ("#00a3a3", "#ffd23f"),
]


def _hms(seconds, decimals=1):
    seconds = max(0.0, seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    s = f"{s:0{3 + decimals}.{decimals}f}" if decimals else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{s}" if h >= 1 else f"{int(m)}:{s}"


def short_code(name, taken):
    """Three letters from the surname (FIZ for Kurtt fizer), made unique."""
    clean = plain(name)
    clean = clean.split("]", 1)[-1] if clean.startswith("[") else clean
    words = ["".join(c for c in w if c.isalpha()) for w in clean.replace(".", " ").replace("_", " ").split()]
    words = [w for w in words if w] or ["CAR"]
    last, first = words[-1], words[0]
    candidates = [last[:3]]
    if len(words) > 1:
        candidates += [last[:2] + first[0], first[0] + last[:2]]
    candidates += [last[0] + c for c in (last[1:] + "XYZ") for _ in [0]]
    candidates = [(c + "XX")[:3] for c in candidates]
    for c in candidates:
        if len(c) == 3 and c not in taken:
            return c
    base = candidates[0][:2]
    for i in range(2, 100):
        if f"{base}{i}"[:3] not in taken:
            return f"{base}{i}"[:3]
    return candidates[0]


# ================================================================ race data

class Car:
    def __init__(self, name, index):
        self.name = name
        self.index = index
        self.code = ""
        self.number = ""
        self.body = self.stripe = None
        self.t = []          # sample times (log seconds)
        self.dist = []       # race distance in laps (laps + lap fraction)
        self.pit = []
        self.stops = []
        self.status = []
        self.finish_t = None     # when it crossed the line at the flag
        self.finish_pos = None
        self.finish_dist = None
        self.out_t = None        # when it retired (or vanished)
        self.out_label = ""
        self.stint_starts = []   # (time, distance) a fresh set went on


class Race:
    def __init__(self, folder):
        self.folder = folder
        info = {}
        try:
            with open(os.path.join(folder, racefiles.RACE_JSON), "r", encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            pass
        self.info = info
        self.summary = info.get("summary") or {}
        self.track = info.get("track") or self.summary.get("track") or os.path.basename(folder)
        classes = self.summary.get("classes") or []
        self.subtitle = classes[0] if len(classes) == 1 else ""
        self.entries = {}
        self.states = []
        self._load(racefiles.race_file(folder, "timing"))
        log = racefiles.race_file(folder, "race_log")
        self.log_finishes = self._read_log_finishes(log)
        self.yellows = self._read_log_yellows(log)
        if len(self.states) < 2:
            raise ValueError("This race has no recorded live timing (01_race_log_timing.jsonl), so it can't be "
                             "visualized. Races recorded with the current loggers have it.")
        self._find_green()
        self._build_cars()
        self._find_events()
        self._find_gaps()
        self._build_warp()

    # ------------------------------------------------------------ loading

    def _load(self, path):
        if not os.path.exists(path):
            return
        segment, entries = [], {}
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                kind = rec.get("type")
                if kind == "session":
                    if segment:   # keep the last session that has timing (the race)
                        self._segment = segment
                    segment = []
                elif kind == "entry":
                    entries[rec["name"]] = rec
                elif kind == "timing" and rec.get("rows"):
                    segment.append(rec)
        if not segment:
            segment = getattr(self, "_segment", [])
        self.entries = entries
        fields = ("name", "pos", "pos_class", "laps", "frac", "interval", "gap", "laps_down",
                  "last", "best", "pit", "stops", "finish")
        for rec in segment:
            rows = {}
            for r in rec["rows"]:
                row = dict(zip(fields, r))
                rows[row["name"]] = row
            self.states.append({"t": rec["t"], "phase": rec.get("phase"), "rows": rows})
        self.states.sort(key=lambda s: s["t"])
        self.times = [s["t"] for s in self.states]

    @staticmethod
    def _read_log_finishes(path):
        """{driver: (log time, position)} from each 'Checkered Flag!' line in the race log, which runs on
        the same clock as the timing."""
        out = {}
        pattern = re.compile(r"^(\d+):(\d\d):(\d\d) - \[FINISH\] Checkered Flag! (.+?) "
                             r"(?:took P(\d+)!|comes past the line to take P(\d+)\.)")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pattern.match(line.strip())
                    if m and m.group(4) not in out:
                        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        out[m.group(4)] = (h * 3600 + mi * 60 + se, int(m.group(5) or m.group(6)))
        except OSError:
            pass
        return out

    @staticmethod
    def _read_log_yellows(path):
        """[(start, end, label)] yellow flag periods from the race log's [FLAG] lines, in log seconds.
        RaceRoom names the sectors ('Yellow in sector 2'); AMS2 only says Yellow or Double Yellow."""
        out = []
        start, sectors, double = None, set(), False
        pattern = re.compile(r"^(\d+):(\d\d):(\d\d) - \[FLAG\] Flag: (.+)$")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return out
        t = 0
        for line in lines:
            m = pattern.match(line.strip())
            if not m:
                continue
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            text = m.group(4).lower()
            if text.startswith("green"):
                if start is not None:
                    out.append((start, max(t, start + 1), _yellow_label(sectors, double)))
                start, sectors, double = None, set(), False
            elif "yellow" in text:
                if start is None:
                    start = t
                double = double or "double" in text
                sec = re.search(r"sector (\d)", text)
                if sec:
                    sectors.add(int(sec.group(1)))
        if start is not None:
            out.append((start, t + 5, _yellow_label(sectors, double)))
        return out

    def _find_green(self):
        green = None
        for i, s in enumerate(self.states):
            if s["phase"] == PHASE_GREEN:
                # Recorded before the start: the light went green between this snapshot and the one before
                green = s["t"] if i == 0 else (self.states[i - 1]["t"] + s["t"]) / 2
                break
        g = self.summary.get("green_at")
        if isinstance(g, (int, float)) and self.times[0] <= g <= self.times[-1]:
            green = g if green is None or abs(g - green) < 15 else green
        self.green = green if green is not None else self.times[0]
        # Snapshots from the garage can still carry the previous session's laps: start from the grid
        first = max(0, bisect.bisect_right(self.times, self.green) - 1)
        self.states = self.states[first:]
        self.times = self.times[first:]
        self.grid_state = self.states[0]

    # ------------------------------------------------------------ per car

    def _build_cars(self):
        order = sorted(self.grid_state["rows"].values(), key=lambda r: r["pos"])
        names = [r["name"] for r in order]
        for s in self.states:
            for n in s["rows"]:
                if n not in names:
                    names.append(n)
        self.cars = {n: Car(n, i) for i, n in enumerate(names)}
        self._style_cars()

        for car in self.cars.values():
            prev = None
            stops_prev = None
            for s in self.states:
                row = s["rows"].get(car.name)
                if not row:
                    continue
                laps, frac = row.get("laps") or 0, row.get("frac") or 0.0
                frac = max(0.0, frac)
                d = laps + frac
                if laps == 0 and frac > 0.5:
                    d = frac - 1.0        # on the grid, short of the line
                if prev is not None and abs(prev - d) > 0.5:
                    d += round(prev - d)  # lap count and fraction read a moment apart
                if s["t"] <= self.green and row.get("pit") and laps == 0:
                    d = None              # waiting in the pits to start: placed behind the grid below
                car.t.append(s["t"])
                car.dist.append(d)
                car.pit.append(bool(row.get("pit")))
                stops = row.get("stops") or 0
                car.stops.append(stops)
                car.status.append(row.get("finish") or 0)
                if d is not None:
                    prev = d
                if stops_prev is not None and stops > stops_prev:
                    car.stint_starts.append(s["t"])
                stops_prev = stops

        # Pit-lane starters sit behind the last grid car until the green flag
        grid_d = [d for c in self.cars.values() for d, t in zip(c.dist, c.t) if d is not None and t <= self.green]
        tail = min(grid_d) if grid_d else 0.0
        k = 0
        for car in self.cars.values():
            if any(d is None for d in car.dist):
                k += 1
                car.dist = [tail - 0.006 * k if d is None else d for d in car.dist]

        # On the opening lap nobody is ahead of the leader (pit-lane starters are past the line before the
        # leader is); later a car can be, e.g. stopped in a pit box just past the line
        for i, s in enumerate(self.states):
            lead = min(s["rows"].values(), key=lambda r: r["pos"])["name"]
            lc = self.cars[lead]
            if s["t"] not in lc.t:
                continue
            ld = lc.dist[lc.t.index(s["t"])]
            for car in self.cars.values():
                if car is lc or s["t"] not in car.t:
                    continue
                j = car.t.index(s["t"])
                if car.dist[j] > ld and ld < 1.0:
                    car.dist[j] = ld

        last_t = self.times[-1]
        for car in self.cars.values():
            self._finish(car)
            self._retire(car, last_t)
            car.t = np.array(car.t, dtype=float)
            car.dist = np.array(car.dist, dtype=float)
            # Stints as (time, distance)
            starts = [(car.t[0], float(car.dist[0]))]
            for t in car.stint_starts:
                starts.append((t, float(np.interp(t, car.t, car.dist))))
            car.stint_starts = starts

        # Tyre wear scale: the longest stint anyone ran is "fully worn"
        longest = 0.0
        for car in self.cars.values():
            end_d = car.finish_dist if car.finish_dist is not None else float(car.dist[-1])
            bounds = [d for _, d in car.stint_starts] + [end_d]
            for a, b in zip(bounds, bounds[1:]):
                longest = max(longest, b - a)
        self.stint_ref = max(6.0, longest * 1.08)

    def _style_cars(self):
        setup = {}
        try:
            with open(racefiles.race_file(self.folder, "graphics_config"), "r", encoding="utf-8") as f:
                setup = (json.load(f) or {}).get("drivers") or {}
        except (OSError, ValueError):
            pass
        taken = set()
        for car in sorted(self.cars.values(), key=lambda c: c.index):
            drv = setup.get(car.name) or {}
            car.code = short_code(drv.get("display") or car.name, taken)
            taken.add(car.code)
            car.number = str(drv.get("number") or (self.entries.get(car.name) or {}).get("num") or "")
            body, stripe = PALETTE[car.index % len(PALETTE)]
            if car.index >= len(PALETTE):   # past one round of the palette, swap body and stripe
                body, stripe = stripe if luminance(stripe) > 60 else shade(body, -0.3), body
            if drv.get("color"):
                body = drv["color"]
                stripe = "#ffffff" if luminance(body) < 150 else "#1b1b1f"
            car.body, car.stripe = rgb(body), rgb(stripe)

    def _finish(self, car):
        """Crossing the line at the flag: freeze the car on the line and time the crossing."""
        # The first 'finished' after the start that holds to the end (a car can carry one over from the garage)
        idx = None
        for i in range(len(car.status) - 1, -1, -1):
            if car.status[i] != FINISH_FINISHED or car.t[i] <= self.green + 30:
                break
            idx = i
        if idx is not None:
            state = self.states[bisect.bisect_left(self.times, car.t[idx])]
            row = state["rows"].get(car.name) or {}
            line = float(row.get("laps") or round(car.dist[idx]))
            car.finish_pos = row.get("pos")
            if car.name in self.log_finishes:
                car.finish_pos = self.log_finishes[car.name][1]
            search_from = self.green
        elif car.name in self.log_finishes:
            # No finish in the timing (AMS2 doesn't share other cars' race states): the race log saw it
            logged, car.finish_pos = self.log_finishes[car.name]
            if logged <= self.green + 30 or logged > car.t[-1] + 10:
                return
            line = float(round(np.interp(logged, car.t, car.dist)))
            idx = len(car.t) - 1
            search_from = logged - 15
        else:
            return
        # The moment its distance reached the line, between the two samples either side of it
        k = next((i for i in range(1, idx + 1) if car.dist[i] >= line and car.t[i] > search_from), idx)
        cross = car.t[k]
        if k > 0 and car.dist[k] > car.dist[k - 1]:
            p = (line - car.dist[k - 1]) / (car.dist[k] - car.dist[k - 1])
            cross = car.t[k - 1] + min(1.0, max(0.0, p)) * (car.t[k] - car.t[k - 1])
        car.finish_t, car.finish_dist = cross, line
        keep = [i for i in range(len(car.t)) if car.t[i] < cross]
        car.t = [car.t[i] for i in keep] + [cross, max(cross + 1e-3, self.times[-1])]
        car.dist = [car.dist[i] for i in keep] + [line, line]
        car.pit = [car.pit[i] for i in keep] + [False, False]
        car.stops = [car.stops[i] for i in keep] + [car.stops[-1]] * 2
        car.status = [car.status[i] for i in keep] + [FINISH_FINISHED] * 2

    def _retire(self, car, last_t):
        if car.finish_t is not None:
            return
        last = car.status[-1] if car.status else 0
        if last in OUT_STATUS:
            i = len(car.status) - 1
            while i > 0 and car.status[i - 1] == last:
                i -= 1
            car.out_t, car.out_label = car.t[i], OUT_STATUS[last]
        elif car.t and car.t[-1] < last_t - 20:
            car.out_t, car.out_label = car.t[-1], "OUT"   # left the server

    # ------------------------------------------------------------ events

    def _find_events(self):
        """Fastest laps, lead changes, retirements: [(log time, kind, text, car)]."""
        events = []
        best, holder, leader = None, None, None
        self.fastest = []   # (time, car name, lap time)
        for s in self.states:
            if s["t"] < self.green:
                continue
            rows = s["rows"].values()
            lead_row = min(rows, key=lambda r: r["pos"])
            lead = lead_row["name"]
            if leader is not None and lead != leader and not self.cars[lead].finish_t:
                events.append((s["t"], "lead", f"NEW LEADER {self.cars[lead].code}", lead))
            leader = lead
            for r in rows:
                b = r.get("best")
                if b and (best is None or b < best - 1e-4):
                    best, holder = b, r["name"]
                    self.fastest.append((s["t"], holder, b))
                    # The first laps' bests are just the first times set: not news
                    if (lead_row.get("laps") or 0) >= 3:
                        events.append((s["t"], "fastest", f"FASTEST LAP {self.cars[holder].code} {_hms(b, 3)}",
                                       holder))
        for car in self.cars.values():
            if car.out_t is not None:
                events.append((car.out_t, "out", f"{car.out_label} {car.code}", car.name))
        events.sort(key=lambda e: e[0])
        self.events = events
        self.fastest_t = [f[0] for f in self.fastest]

    def _find_gaps(self):
        """Gaps to the winner at the flag, in finishing order (5-second samples can't swap two cars)."""
        self.gaps = {}
        lf = self.leader_finish()
        if lf is None:
            return
        done = [c for c in self.cars.values() if c.finish_t is not None]
        done.sort(key=lambda c: (c.finish_pos or 999, -c.finish_dist, c.finish_t))
        gap = 0.0
        for c in done:
            gap = max(gap, c.finish_t - lf)
            self.gaps[c.name] = gap

    def _build_warp(self):
        """Track distance -> distance in lap time. A car brakes into a corner the car behind is still flat out
        towards, so the gap in metres shrinks and grows every lap while the gap in seconds holds. Each point
        of the lap is moved to how far into a typical lap (by time) a car is there, so two cars a steady
        second apart stay a steady distance apart on screen. Whole laps stay put.

        The profile comes from every clean lap of every car (no pit stop, near the usual lap time), so a
        leader stopping in its pit box or a slow lap doesn't bend it."""
        bins = np.linspace(0.0, 1.0, 41)
        samples = [[] for _ in bins]
        lap_times = []
        laps = []
        for car in self.cars.values():
            t = np.asarray(car.t, dtype=float)
            d = np.maximum.accumulate(np.asarray(car.dist, dtype=float))
            pit = np.asarray(car.pit, dtype=bool)
            end = car.out_t if car.out_t is not None else (car.finish_t or t[-1])
            for k in range(max(1, int(math.ceil(d[0]))), int(math.floor(d[-1]))):
                t0, t1 = np.interp(k, d, t), np.interp(k + 1, d, t)
                if t0 <= self.green or t1 > end or t1 <= t0:
                    continue
                inside = (t >= t0) & (t <= t1)
                if pit[inside].any() or inside.sum() < 4:
                    continue
                laps.append((t[inside], d[inside], k, t0, t1))
                lap_times.append(t1 - t0)
        if len(laps) < 3:
            self._profile = None
            return
        usual = float(np.median(lap_times))
        for tt, dd, k, t0, t1 in laps:
            if not 0.85 * usual <= t1 - t0 <= 1.2 * usual:
                continue   # incidents, off-track moments, lapping traffic
            frac = dd - k
            share = (tt - t0) / (t1 - t0)
            for f, sh in zip(frac, share):
                samples[int(round(f * (len(bins) - 1)))].append(sh)
        prof = np.array([np.median(sm) if sm else np.nan for sm in samples])
        prof[0], prof[-1] = 0.0, 1.0
        ok = ~np.isnan(prof)
        prof = np.interp(bins, bins[ok], prof[ok])
        prof = np.clip(np.maximum.accumulate(prof), 0.0, 1.0)
        self._profile = (bins, prof)

    def warp(self, d):
        if self._profile is None:
            return d
        whole = np.floor(d)
        return whole + np.interp(d - whole, *self._profile)

    def leader_finish(self):
        times = [c.finish_t for c in self.cars.values() if c.finish_t is not None]
        return min(times) if times else None

    def end_time(self):
        """When the replay's race clock stops: the last finisher, or the end of the data."""
        lf = self.leader_finish()
        if lf is None:
            return self.times[-1]
        last = max(c.finish_t for c in self.cars.values() if c.finish_t is not None)
        # Cars still out there when the data ends would hold the result up forever
        return min(self.times[-1], max(last, lf) + 3)

    def total_laps(self):
        lead = [c for c in self.cars.values() if c.finish_t is not None]
        if lead:
            return int(max(c.finish_dist for c in lead))
        return None


# ================================================================ timeline and layout

class Replay:
    def __init__(self, race, seconds=90.0, title=None, subtitle=None):
        self.race = race
        self.seconds = max(30.0, float(seconds))
        self.title = plain(title if title is not None else race.track)
        self.subtitle = plain(subtitle if subtitle is not None else race.subtitle)
        self.frames = int(round(self.seconds * FPS))
        self.race_span = self.seconds - INTRO - OUTRO
        self.t0, self.t1 = race.green, max(race.end_time(), race.green + 1)
        self.total_laps = race.total_laps()
        self.cars = sorted(race.cars.values(), key=lambda c: c.index)
        n = len(self.cars)
        self.row_h = min(22.0, (ROWS_BOTTOM - ROWS_TOP) / max(1, n))
        self.large = self.row_h >= 19
        self.sprite_len, self.sprite_h = car_size(self.large)
        self.ts = 2 if self.row_h >= 19 else 1      # text scale for the names
        self.watch = stopwatch(PURPLE)
        self._layout()
        self._yellow_periods()

    def _yellow_periods(self):
        """Yellows in video seconds: each shown for at least a second, ones close together merged."""
        spans = []
        for a, b, label in self.race.yellows:
            if b <= self.t0 or a >= self.t1:
                continue
            va, vb = self.video_time(max(a, self.t0)), self.video_time(min(b, self.t1))
            vb = max(vb, va + 1.0)
            if spans and va - spans[-1][1] < 0.8:
                pa, pb, pl = spans[-1]
                spans[-1] = (pa, max(pb, vb), _merge_labels(pl, label))
            else:
                spans.append((va, vb, label))
        self.yellow_spans = spans

    def yellow_at(self, v):
        for a, b, label in self.yellow_spans:
            if a <= v < b:
                return label
        return None

    def race_time(self, v):
        if v <= INTRO:
            return self.t0
        p = min(1.0, (v - INTRO) / self.race_span)
        return self.t0 + p * (self.t1 - self.t0)

    def video_time(self, t):
        return INTRO + (t - self.t0) / (self.t1 - self.t0) * self.race_span

    def _layout(self):
        """Every car's x, y and state on every frame, computed once so frames can be drawn in any order."""
        race, cars = self.race, self.cars
        F, N = self.frames, len(cars)
        v = np.arange(F) / FPS
        t = np.array([self.race_time(x) for x in v])
        self.v, self.t = v, t

        dist = np.zeros((F, N))
        out = np.zeros((F, N), dtype=bool)
        for j, c in enumerate(cars):
            dist[:, j] = np.interp(t, c.t, c.dist)
            if c.out_t is not None:
                out[:, j] = t >= c.out_t
        # On screen: distance in the leader's time, with the last bit of sampling noise smoothed out
        wdist = race.warp(dist)
        for j in range(N):
            wdist[:, j] = _smooth(_smooth(wdist[:, j], 0.4 * FPS), 0.4 * FPS)
        # Game positions per frame (cars missing from a snapshot keep their last place)
        pos = np.zeros((F, N))
        last = {c.name: c.index + 1 for c in cars}
        state_pos = []
        for s in race.states:
            for n, r in s["rows"].items():
                last[n] = r["pos"]
            state_pos.append([last[c.name] for c in cars])
        state_pos = np.array(state_pos, dtype=float)
        idx = np.clip(np.searchsorted(race.times, t, side="right") - 1, 0, len(race.times) - 1)
        pos = state_pos[idx]
        # Across the line, a car holds its finishing place (AMS2 shuffles the order after the flag)
        for j, c in enumerate(cars):
            if c.finish_t is not None and c.finish_pos:
                pos[t >= c.finish_t, j] = c.finish_pos - 0.5
        # Rows: the running order of the cars still in it
        rank = np.zeros((F, N))
        for f in range(F):
            p = np.where(out[f], 1e6 + pos[f], pos[f])
            rank[f, np.argsort(p, kind="stable")] = np.arange(N)
        lead_j = np.argmin(np.where(out, 1e6, pos), axis=1)
        # The camera follows the front-most car, not the game's P1: when the leader pits, P1 flips back and
        # forth between the car in the box and the cars going past, and the whole field would lurch with it
        live = np.where(out, np.inf, wdist)
        lead = np.max(np.where(out, -np.inf, wdist), axis=1)
        lead = np.where(np.isfinite(lead), lead, wdist[np.arange(F), lead_j])
        tail = np.min(live, axis=1)
        span = np.clip(lead - tail, 0, None)

        # Camera: zoom to fit the field (within limits), centre a compact field, smoothed both ways
        avail = LEAD_X_MAX - FIELD_X_MIN
        laps_view = np.clip(span * 1.1, VIEW_MIN, VIEW_MAX)
        ppl = avail / laps_view
        field_w = np.minimum(span * ppl, avail)
        lead_x = np.minimum(LEAD_X_MAX, FIELD_X_MIN + avail / 2 + field_w / 2 + 40)
        ppl = _smooth(np.log(ppl), 2.5 * FPS)
        ppl = np.exp(ppl)
        lead_x = _smooth(lead_x, 2.0 * FPS)

        x = lead_x[:, None] - (lead[:, None] - wdist) * ppl[:, None]
        down = np.floor(lead[:, None] - wdist + 1e-6)
        clamped = x < FIELD_X_MIN
        x = np.maximum(x, FIELD_X_MIN)
        y = ROWS_TOP + _ease_rows(rank, SWAP_SEC * FPS) * self.row_h

        # Retired cars fall off the back and off the bottom
        for j, c in enumerate(cars):
            if c.out_t is None:
                continue
            v_out = self.video_time(c.out_t) if c.out_t > self.t0 else INTRO
            f0 = int(math.ceil(v_out * FPS))
            if f0 >= F:
                continue
            p = np.clip((v[f0:] - v_out) / FALL_SEC, 0, 1)
            # From where it was last racing (a retired car is teleported to its garage)
            y0, x0 = y[max(0, f0 - 1), j], x[max(0, f0 - 1), j]
            y[f0:, j] = y0 + (H + 30 - y0) * p ** 2
            x[f0:, j] = x0 - 260 * p ** 2
        self.dist, self.x, self.y, self.out = dist, x, y, out
        self.lead, self.lead_x, self.ppl, self.down, self.clamped = lead, lead_x, ppl, down, clamped
        self.lead_j = lead_j

    # ------------------------------------------------------------ per car state

    def _row_value(self, car, field, t):
        i = int(np.searchsorted(car.t, t, side="right")) - 1
        if i < 0:
            return field[0]
        return field[min(i, len(field) - 1)]

    def tyre_life(self, car, t, d):
        start = car.stint_starts[0][1]
        for st, sd in car.stint_starts:
            if st <= t:
                start = sd
        return max(0.0, min(1.0, 1 - max(0.0, d - start) / self.race.stint_ref))

    def fastest_at(self, t):
        i = bisect.bisect_right(self.race.fastest_t, t) - 1
        return self.race.fastest[i] if i >= 0 else None

    # ------------------------------------------------------------ drawing

    def render(self, f):
        f = max(0, min(self.frames - 1, f))
        v, t = self.v[f], self.t[f]
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        finished_leader = self.race.leader_finish()
        flag = finished_leader is not None and t >= finished_leader

        self._draw_rows(d, img)
        self._draw_lap_lines(d, img, f)
        self._draw_cars(img, d, f, t, v)
        self._draw_header(img, d, f, t, v, flag)
        self._draw_feed(img, d, v)
        if v < INTRO + 1.0:
            self._draw_lights(img, d, v)
        if v >= self.seconds - OUTRO:
            self._draw_outro(img, d, v)
        return img

    def _draw_rows(self, d, img):
        # Faint banding and position numbers down the left
        n = len(self.cars)
        for i in range(n):
            y0 = ROWS_TOP + i * self.row_h
            if i % 2:
                d.rectangle([0, int(y0), W, int(y0 + self.row_h) - 1], fill=BG_ALT)
            draw_text(img, (14, y0 + (self.row_h - 7) / 2), str(i + 1), DIM, anchor="r")

    def _draw_lap_lines(self, d, img, f):
        lead, lx, ppl = self.lead[f], self.lead_x[f], self.ppl[f]
        first = math.floor(lead - (lx + 40) / ppl)
        last = math.ceil(lead + (W - lx) / ppl)
        total = self.total_laps
        for k in range(first, last + 1):
            if k < 0:
                continue
            x = int(round(lx - (lead - k) * ppl))
            if total is not None and k == total:
                # Chequered finish line
                for yy in range(HEADER_H, H, 3):
                    for xx in (0, 3):
                        c = (230, 230, 230) if ((yy // 3) + xx // 3) % 2 == 0 else (20, 20, 20)
                        d.rectangle([x - 3 + xx, yy, x - 1 + xx, yy + 2], fill=c)
                continue
            d.rectangle([x - 1, HEADER_H, x, H], fill=LINE)
            if total is None or k < total:
                draw_text(img, (x + 5, HEADER_H + 4), f"LAP {k + 1}", LAP_LABEL, scale=2)

    def _draw_cars(self, img, d, f, t, v):
        L, CH = self.sprite_len, self.sprite_h
        fastest = self.fastest_at(t) if v > INTRO else None
        order = np.argsort(self.y[f])
        leader_finish = self.race.leader_finish()
        for j in order[::-1]:
            car = self.cars[j]
            x, y = self.x[f, j], self.y[f, j]
            if y > H + 4 or x < -60:
                continue
            nose = int(round(x))
            cy = int(round(y + (self.row_h - CH) / 2))
            done = car.finish_t is not None and t >= car.finish_t
            gone = self.out[f, j]
            in_pit = not done and not gone and bool(self._row_value(car, car.pit, t))

            # Tyre bar and number badge behind the car
            life = self.tyre_life(car, t, self.dist[f, j])
            bx = nose - L - 2
            badge = car.number[:3] if car.number else ""
            if badge:
                tiny = CH < 11
                bw = text_width(badge, tiny=tiny) + 4
                bx -= bw
                d.rectangle([bx, cy + 1, bx + bw - 1, cy + CH - 2], fill=car.body)
                ink = (16, 16, 20) if luminance(car.body) > 140 else (255, 255, 255)
                draw_text(img, (bx + 2, cy + (CH - (5 if tiny else 7)) / 2), badge, ink, tiny=tiny)
                bx -= 2
            bx -= 4
            d.rectangle([bx, cy, bx + 3, cy + CH - 1], fill=(10, 12, 18))
            fill = int(round((CH - 2) * life))
            if fill:
                d.rectangle([bx + 1, cy + CH - 1 - fill, bx + 2, cy + CH - 2], fill=_tyre_colour(life))

            # The car
            sprite = car_sprite(car.body, car.stripe, self.large)
            img.paste(sprite, (nose - L + 1, cy), sprite)
            if in_pit:
                px0 = nose - L + 1
                d.rectangle([px0 - 1, cy - 1, nose + 1, cy + CH], fill=PIT_BG, outline=PIT)
                draw_text(img, (px0 + L / 2, cy + (CH - 7) / 2), "PIT", PIT, anchor="c")

            # Name and tags
            tx = nose + 4
            ty = cy + (CH - 7) / 2
            colour = TEXT if not gone else RED
            tx += draw_text(img, (tx, cy + (CH - 7 * self.ts) / 2), car.code, colour, scale=self.ts) + 3
            if gone:
                draw_text(img, (tx, ty), car.out_label, RED)
                continue
            if done and self.down[f, j] >= 1 and leader_finish is not None:
                n = int(self.down[f, j])
                tx += draw_text(img, (tx, ty), f"+{n} LAP" + ("S" if n > 1 else ""), MUTED) + 3
            elif self.clamped[f, j] and self.down[f, j] >= 1:
                tx += draw_text(img, (tx, ty), f"+{int(self.down[f, j])}L", DIM) + 3
            if fastest and fastest[1] == car.name:
                img.paste(self.watch, (int(tx), int(cy + (CH - 9) / 2)), self.watch)
            # Result time at the flag, left of everything
            if done and self.down[f, j] < 1 and leader_finish is not None:
                gap = self.race.gaps.get(car.name, 0.0)
                txt = _hms(leader_finish - self.t0, 3) if gap <= 0 else f"+{gap:.3f}"
                draw_text(img, (bx - 4, ty), txt, MUTED if j != self.lead_j[f] else TEXT, anchor="r")

    def _draw_header(self, img, d, f, t, v, flag):
        d.rectangle([0, 0, W, HEADER_H - 1], fill=HEADER_BG)
        yellow = self.yellow_at(v) if INTRO <= v < self.seconds - OUTRO and not flag else None
        if yellow:
            d.rectangle([0, HEADER_H - 1, W, HEADER_H + 1], fill=YELLOW)
        else:
            d.rectangle([0, HEADER_H - 1, W, HEADER_H - 1], fill=ACCENT)
        right = "FINAL RESULT" if v >= self.seconds - OUTRO else ""
        if not right:
            lap = int(math.floor(self.lead[f])) + 1
            if flag:
                right = "CHEQUERED FLAG"
            elif v < INTRO:
                right = "GRID"
            elif self.total_laps:
                right = f"LAP {min(lap, self.total_laps)}/{self.total_laps}"
            else:
                right = f"LAP {max(1, lap)}"
        rw = draw_text(img, (W - 6, 3), right, TEXT if not flag else GOLD, scale=2, anchor="r")
        if yellow:
            # A waving yellow flag and what it covers, left of the lap counter
            x = W - 6 - rw - 10
            x -= draw_text(img, (x, 7), yellow, YELLOW, anchor="r") + 4
            wave = int(v * 6) % 2
            d.rectangle([x - 1, 3, x - 1, 16], fill=MUTED)
            for c in range(9):
                dy = (c // 3 + wave) % 2
                d.rectangle([x - 2 - c, 3 + dy, x - 2 - c, 9 + dy], fill=YELLOW)
            rw += (W - 6 - rw) - (x - 12)
        room = W - 6 - rw - 16
        title = self.title
        scale = 2 if text_width(title, 2) <= room else 1
        while text_width(title, scale) > room and len(title) > 4:
            title = title[:-2]
        tw = draw_text(img, (6, 3 if scale == 2 else 6), title, TEXT, scale=scale)
        if self.subtitle and 6 + tw + 10 + text_width(self.subtitle) < W - rw - 16:
            draw_text(img, (6 + tw + 10, 6 if scale == 1 else 8), self.subtitle, MUTED)

    def _draw_feed(self, img, d, v):
        """The latest few race events, bottom right."""
        show = []
        for (te, kind, text, name) in self.race.events:
            ve = self.video_time(te)
            if ve > v:
                break
            if v - ve <= 3.5:
                show.append((kind, text))
        show = show[-3:]
        y = H - 12 - 11 * (len(show) - 1)
        for kind, text in show:
            colour = {"fastest": PURPLE, "lead": GOLD, "out": RED}.get(kind, TEXT)
            w = text_width(text)
            x0 = W - 8 - w
            d.rectangle([x0 - 6, y - 2, W - 4, y + 8], fill=HEADER_BG)
            d.rectangle([x0 - 6, y - 2, x0 - 5, y + 8], fill=colour)
            draw_text(img, (x0, y), text, TEXT)
            y += 11

    def _draw_lights(self, img, d, v):
        lit = sum(1 for i in range(5) if v >= 0.5 + i * 0.5) if v < INTRO else 0
        size, gap = 14, 4
        x0 = W - 6 - 5 * (size + gap)
        y0 = HEADER_H + 26
        d.rectangle([x0 - 6, y0 - 6, x0 + 5 * (size + gap) - gap + 5, y0 + 2 * (size + gap) + 1], fill=(8, 10, 16))
        for i in range(5):
            for r in range(2):
                cx, cy = x0 + i * (size + gap), y0 + r * (size + gap)
                on = i < lit
                c = (235, 40, 30) if on else (40, 44, 54)
                d.rectangle([cx + 2, cy, cx + size - 3, cy + size - 1], fill=c)
                d.rectangle([cx, cy + 2, cx + size - 1, cy + size - 3], fill=c)
                if on:
                    d.rectangle([cx + 3, cy + 3, cx + 5, cy + 5], fill=(255, 170, 160))

    def _draw_outro(self, img, d, v):
        outs = [c for c in self.cars if c.out_t is not None]
        if not outs:
            return
        text = "OUT  " + "  ".join(f"{c.code} {c.out_label}" for c in outs)
        x = 22
        y = H - 12
        d.rectangle([x - 6, y - 3, x + text_width(text) + 5, y + 9], fill=HEADER_BG)
        draw_text(img, (x, y), text, RED)


def _yellow_label(sectors, double):
    text = "DOUBLE YELLOW" if double else "YELLOW"
    if sectors:
        text += " S" + "/".join(str(s) for s in sorted(sectors))
    return text


def _merge_labels(a, b):
    double = a.startswith("DOUBLE") or b.startswith("DOUBLE")
    sectors = {int(c) for lab in (a, b) for c in re.findall(r"(\d)", lab)}
    return _yellow_label(sectors, double)


def _tyre_colour(life):
    stops = [(0.0, (230, 40, 40)), (0.3, (255, 140, 30)), (0.55, (240, 215, 40)), (0.8, (140, 210, 60)),
             (1.0, (40, 200, 90))]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if life <= b:
            return mix(ca, cb, (life - a) / (b - a))
    return stops[-1][1]


def _smooth(a, window):
    """Centred moving average (edges padded), so the camera never lags behind."""
    w = max(1, int(window) | 1)
    pad = w // 2
    padded = np.concatenate([np.full(pad, a[0]), a, np.full(pad, a[-1])])
    kernel = np.ones(w) / w
    return np.convolve(padded, kernel, mode="valid")


def _ease_rows(rank, frames):
    """Rows glide to new places with an ease in and out over `frames`: three box filters in a row turn
    each step into a smooth S-curve. A swap that flickers back within that time only nudges the cars."""
    out = rank.astype(float).copy()
    width = frames / 3
    for j in range(out.shape[1]):
        for _ in range(3):
            out[:, j] = _smooth(out[:, j], width)
    return out


# ================================================================ export

def ffmpeg_exe():
    return shutil.which("ffmpeg") or "ffmpeg"


def export(replay, out_path, scale=2, progress=None, cancel=None):
    """Renders every frame and pipes it to ffmpeg (H.264, small). progress(done, total) is called as it goes."""
    out_w, out_h = W * scale, H * scale
    cmd = [ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"]
    if scale != 1:
        cmd += ["-vf", f"scale={out_w}:{out_h}:flags=neighbor"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-tune", "animation", "-crf", "24", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out_path]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    try:
        for f in range(replay.frames):
            if cancel and cancel():
                proc.stdin.close()
                proc.kill()
                return False
            proc.stdin.write(replay.render(f).tobytes())
            if progress and (f % 15 == 0 or f == replay.frames - 1):
                progress(f + 1, replay.frames)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err.strip() or proc.returncode}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Visualize a race as a short pixel-art video.")
    ap.add_argument("race", help="the race folder (Races/<date time track>)")
    ap.add_argument("--out", help=f"output video (default: {OUTPUT_NAME} in the race folder)")
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--scale", type=int, default=2, help="1 = 640x360, 2 = 1280x720, 3 = 1920x1080")
    ap.add_argument("--title")
    ap.add_argument("--subtitle")
    ap.add_argument("--frame", type=float, help="save a PNG of this moment (seconds into the video) instead")
    args = ap.parse_args()

    race = Race(args.race)
    replay = Replay(race, args.seconds, args.title, args.subtitle)
    if args.frame is not None:
        out = args.out or os.path.join(args.race, "race_visualized_frame.png")
        img = replay.render(int(args.frame * FPS))
        img.resize((W * args.scale, H * args.scale), Image.NEAREST).save(out)
        print(out)
        return
    out = args.out or os.path.join(args.race, OUTPUT_NAME)

    def progress(done, total):
        print(f"\rRendering {done}/{total} frames", end="", flush=True)

    export(replay, out, args.scale, progress)
    print(f"\nSaved {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
