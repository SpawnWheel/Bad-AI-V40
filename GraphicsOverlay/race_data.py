"""Parses the race log into a timeline the graphics can query.

All public times are VIDEO seconds (log time + sync offset), so graphics line up
with the recorded video and the commentary audio.
"""
import bisect
import os
import re

RAW_LINE_RE = re.compile(r'^\s*(\d+):(\d{2}):(\d{2})\s*-\s*\[([A-Z][A-Z _]*)\]\s*(.*)$')
TAG_RE = re.compile(r'\[[^\]]*\]')

OVERTAKE_RE = re.compile(r'^(.+?) overtook (.+?) for P(\d+) in (.+?)(?: at (.+?))?\.\s*$')
STANDING_RE = re.compile(r'^P(\d+):\s*(.+?)\s*\(([^()]*)\)\s*$')
GRID_RE = re.compile(r'^P(\d+):\s*(.+?)(?:\s*\[([^\]]*)\])?\s*$')
# AMS2: entered / arrived at box / leaving.  Raceroom: "entered Pit Lane." / "exited Pit Lane."
PIT_RE = re.compile(r'^(.+?) (has entered the pit lane|has arrived at their pit box|is leaving the pits'
                    r'|entered Pit Lane|exited Pit Lane)')
ACCIDENT_RE = re.compile(r'^Alert:\s*(.+?) \(P(\d+)[^)]*\) is slow/stopped on track(?: at (.+?))?!')
RECOVER_RE = re.compile(r'^Notice:\s*(.+?) is back on the move')
FINISH_LEADER_RE = re.compile(r'Checkered Flag!\s*(.+?) took P(\d+)')
FINISH_RE = re.compile(r'Checkered Flag!\s*(.+?) comes past the line to take P(\d+)')
R3E_WIN_RE = re.compile(r'^(.+?) has taken the win')
R3E_FINISH_RE = re.compile(r'^(.+?) has finished the race in P(\d+)')
CLASS_PREFIX_RE = re.compile(r'^\[[^\]]*\]\s*(?=P\d)')
VEHICLE_SUFFIX_RE = re.compile(r'\s*\[[^\]]*\]\s*$')
WATCH_RE = re.compile(r'Watching (.+?) \(P(\d+)\)')
PASSIVE_LEAD = 8.0      # stopped/pit alerts can be logged a few seconds after cars start passing
PIT_EXIT_GRACE = 3.0
FASTEST_LAP_RE =re.compile(r'^(.+?) \(P(\d+)\) (?:holds the|sets a new) fastest lap of the race: ([\d:.]+)'
                            r'(?: on lap (\d+))?(?:, ([\d.]+)s quicker than (.+?) ([\d:.]+))?\.\s*$')
# Radio messages written by the Refinement step: (Radio) Thommo to engineer: "..." Engineer: "..."
RADIO_RE = re.compile(r'\(Radio\)\s*(.*)$', re.I)
RADIO_SEGMENT_RE = re.compile(r'([^"“”:]{1,60}?):\s*["“]([^"“”]+)["”]')
WORD_COUNT_RE = re.compile(r'\s*Commentate in \d+ words\.?\s*$', re.I)


def hms_to_sec(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


def fmt_clock(sec):
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_clock(text):
    """Accepts 'SS', 'MM:SS' or 'H:MM:SS' (optionally with fractional seconds)."""
    parts = str(text).strip().split(':')
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def read_raw_lines(path):
    """Yields (log_seconds, category, message) from a logger-format text file."""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = RAW_LINE_RE.match(line.rstrip('\n'))
            if m:
                out.append((hms_to_sec(*m.group(1, 2, 3)), m.group(4).strip(), m.group(5).strip()))
    return out


def strip_tags(name):
    return TAG_RE.sub('', name).strip()


def norm_key(name):
    return re.sub(r'[^a-z0-9]', '', strip_tags(name).lower())


class RaceData:
    def __init__(self, raw_log_path, offset=0.0, director_notes_path=None):
        self.raw_log_path = raw_log_path
        self.offset = float(offset or 0.0)
        self.track = ''
        self.track_layout = ''
        self.track_length_m = None
        self.grid = []                 # [name] in grid order
        self.classes = {}              # name -> class label
        self.drivers = []              # roster in grid order (+ any late arrivals)
        self.green_flag_log = None
        self.events = []               # dicts with 't' in LOG seconds
        self.snapshots = []            # (t_log, [(pos, name, gap_str)])
        self.order_changes = []        # (t_log, tuple(order))
        self.pits = []                 # dicts per stop
        self.finish = []               # (t_log, name, pos)
        self.camera = []               # (t_log, name)
        self._parse(read_raw_lines(raw_log_path))
        if director_notes_path:
            self._parse_director(read_raw_lines(director_notes_path))

    # ------------------------------------------------------------------ parsing
    def _add_driver(self, name):
        if name and name not in self.drivers:
            self.drivers.append(name)

    def _match_driver(self, text):
        """Finds the roster name at the start of text (handles names with spaces/brackets)."""
        for name in sorted(self.drivers, key=len, reverse=True):
            if text.startswith(name):
                return name
        return text

    def _names_in(self, text):
        """Returns [(name, pos)] for every 'Name (Pn)' mention, in order."""
        found = []
        for name in sorted(self.drivers, key=len, reverse=True):
            for m in re.finditer(re.escape(name) + r' \(P(\d+)\)', text):
                found.append((m.start(), name, int(m.group(1))))
        found.sort()
        # Drop shorter names that matched inside a longer one at the same position
        result, last_end = [], -1
        for start, name, pos in found:
            if start < last_end:
                continue
            result.append((name, pos))
            last_end = start + len(name)
        return result

    def _parse(self, lines):
        # Only keep the last race session if the log contains several
        race_start_idx = 0
        for i, (_, cat, msg) in enumerate(lines):
            if cat == 'LEADERBOARD' and msg.startswith('Starting Grid'):
                race_start_idx = i
        order = []
        open_pits = {}
        stopped = {}
        for i, (t, cat, msg) in enumerate(lines):
            if cat == 'TRACK':
                if msg.startswith('Track Loaded:'):
                    full = msg.split(':', 1)[1].strip()
                    track, _, layout = full.partition(' - ')
                    self.track, self.track_layout = track.strip(), layout.strip()
                elif msg.startswith('Track Length:'):
                    m = re.search(r'([\d.]+)', msg)
                    if m:
                        self.track_length_m = float(m.group(1))
                continue
            if i < race_start_idx:
                continue

            if cat == 'LEADERBOARD':
                body = msg.split(':', 1)[1] if ':' in msg else msg
                if msg.startswith('Starting Grid'):
                    self.grid = []
                    for part in body.split('|'):
                        m = GRID_RE.match(CLASS_PREFIX_RE.sub('', part.strip()))
                        if m:
                            name = m.group(2).strip()
                            self.grid.append(name)
                            self._add_driver(name)
                            if m.group(3):
                                self.classes[name] = m.group(3)
                    order = list(self.grid)
                    self.order_changes.append((t, tuple(order)))
                    self.events.append({'t': t, 'type': 'grid', 'order': list(order)})
                else:
                    rows = []
                    for part in body.split('|'):
                        # Raceroom adds "[Class] " before a class's first entry and " [car - livery]" after names
                        m = STANDING_RE.match(CLASS_PREFIX_RE.sub('', part.strip()))
                        if m:
                            name = VEHICLE_SUFFIX_RE.sub('', m.group(2)).strip()
                            rows.append((int(m.group(1)), name, m.group(3).strip()))
                            self._add_driver(name)
                    if rows:
                        rows.sort()
                        self.snapshots.append((t, rows))
                        order = [r[1] for r in rows]
                        self.order_changes.append((t, tuple(order)))
                        self.events.append({'t': t, 'type': 'standings', 'rows': rows})
                continue

            if cat == 'SESSION' and 'Green Flag' in msg:
                self.green_flag_log = t
                self.events.append({'t': t, 'type': 'green'})
                continue

            if cat == 'OVERTAKE':
                m = OVERTAKE_RE.match(msg)
                if not m:
                    continue
                a, pos = m.group(1).strip(), int(m.group(3))
                # Multi-car passes are logged as "A overtook B, C for P2"
                victims = [self._match_driver(v.strip()) for v in m.group(2).split(', ')]
                b = victims[0]
                self._add_driver(a)
                for v in victims:
                    self._add_driver(v)
                # Running order before the pass, so graphics can stack the passed cars correctly
                victims = sorted(victims, key=lambda v: order.index(v) if v in order else len(order))
                if a in order:
                    order.remove(a)
                order.insert(min(pos - 1, len(order)), a)
                self.order_changes.append((t, tuple(order)))
                self.events.append({'t': t, 'type': 'overtake', 'driver': a, 'victim': b, 'victims': victims,
                                    'pos': pos, 'corner': (m.group(5) or '').strip()})
                continue

            if cat == 'BATTLE':
                pairs = self._names_in(msg)
                if len(pairs) >= 2:
                    (n1, p1), (n2, p2) = pairs[0], pairs[1]
                    ahead, behind = ((n1, p1), (n2, p2)) if p1 < p2 else ((n2, p2), (n1, p1))
                    self.events.append({'t': t, 'type': 'battle', 'ahead': ahead[0], 'behind': behind[0],
                                        'pos': ahead[1], 'side_by_side': 'Side by side' in msg})
                continue

            if cat == 'PIT':
                m = PIT_RE.match(msg)
                if not m:
                    continue
                name, action = self._match_driver(m.group(1).strip()), m.group(2)
                if action.startswith('has entered') or action == 'entered Pit Lane':
                    stop = {'driver': name, 'enter': t, 'box': None, 'leave': None,
                            'pos_before': self._pos_in(order, name)}
                    open_pits[name] = stop
                    self.pits.append(stop)
                    self.events.append({'t': t, 'type': 'pit_in', 'driver': name, 'pos': stop['pos_before']})
                elif name in open_pits:
                    stop = open_pits[name]
                    if action.startswith('has arrived'):
                        stop['box'] = t
                    else:
                        stop['leave'] = t
                        del open_pits[name]
                        self.events.append({'t': t, 'type': 'pit_out', 'driver': name, 'stop': stop})
                continue

            if cat == 'ACCIDENT':
                m = ACCIDENT_RE.match(msg)
                if m:
                    name = self._match_driver(m.group(1).strip())
                    stopped[name] = t
                    self.events.append({'t': t, 'type': 'incident', 'driver': name,
                                        'pos': int(m.group(2)), 'where': (m.group(3) or '').strip()})
                else:
                    m = RECOVER_RE.match(msg)
                    if m:
                        name = self._match_driver(m.group(1).strip())
                        self.events.append({'t': t, 'type': 'recover', 'driver': name,
                                            'stopped_for': t - stopped.pop(name, t)})
                continue

            if cat == 'FLAG':
                flag = msg.split(':', 1)[1].strip() if ':' in msg else msg
                self.events.append({'t': t, 'type': 'flag', 'flag': flag})
                continue

            if cat == 'FASTEST_LAP':
                m = FASTEST_LAP_RE.match(msg)
                if m:
                    name = self._match_driver(m.group(1).strip())
                    # "X's 1:24.000" or "their own 1:24.000"
                    prev = (m.group(6) or '').strip()
                    prev = name if prev == 'their own' else (self._match_driver(prev[:-2]) if prev.endswith("'s")
                                                             else prev) or None
                    self.events.append({'t': t, 'type': 'fastest_lap', 'driver': name, 'pos': int(m.group(2)),
                                        'lap_time': m.group(3), 'lap': int(m.group(4)) if m.group(4) else None,
                                        'gain': m.group(5), 'prev_driver': prev, 'prev_time': m.group(7)})
                continue

            if cat == 'FINISH':
                m = FINISH_LEADER_RE.search(msg) or FINISH_RE.search(msg) or R3E_FINISH_RE.search(msg)
                win = R3E_WIN_RE.search(msg)
                if m or win:
                    name, pos = (win.group(1).strip(), 1) if win else (m.group(1).strip(), int(m.group(2)))
                    if not any(f[1] == name for f in self.finish):
                        self.finish.append((t, name, pos))
                        self.events.append({'t': t, 'type': 'finish', 'driver': name, 'pos': pos})
                continue

        self.order_change_times = [c[0] for c in self.order_changes]
        self.events.sort(key=lambda e: e['t'])
        self._drop_passive_passes()

    def _drop_passive_passes(self):
        """Passes on cars in the pit lane or stopped on track are not real overtakes.

        The passed cars are removed from each overtake; one with nobody left becomes 'pass_off'
        (ignored by graphics and stats). The running order already reflects the change."""
        out_of_race = []  # (driver, from, until) in log time
        for s in self.pits:
            out_of_race.append((s['driver'], s['enter'] - PASSIVE_LEAD, (s['leave'] or 10 ** 9) + PIT_EXIT_GRACE))
        stopped = {}
        for e in self.events:
            if e['type'] == 'incident':
                stopped[e['driver']] = e['t']
            elif e['type'] == 'recover' and e['driver'] in stopped:
                out_of_race.append((e['driver'], stopped.pop(e['driver']) - PASSIVE_LEAD, e['t']))
        out_of_race += [(name, t - PASSIVE_LEAD, 10 ** 9) for name, t in stopped.items()]

        def racing(name, t):
            return not any(n == name and a <= t <= b for n, a, b in out_of_race)

        for e in self.events:
            if e['type'] != 'overtake':
                continue
            victims = [v for v in e['victims'] if racing(v, e['t'])]
            if not victims or not racing(e['driver'], e['t']):
                e['type'] = 'pass_off'
            elif victims != e['victims']:
                e['victims'] = victims
                if e['victim'] not in victims:
                    e['victim'] = victims[0]

    def _parse_director(self, lines):
        for t, cat, msg in lines:
            if cat == 'DIRECTOR NOTES':
                m = WATCH_RE.search(msg)
                if m:
                    self.camera.append((t, self._match_driver(m.group(1).strip())))

    @staticmethod
    def _pos_in(order, name):
        return order.index(name) + 1 if name in order else None

    # ------------------------------------------------------------------ queries (video time)
    def to_video(self, t_log):
        return t_log + self.offset

    def to_log(self, t_video):
        return t_video - self.offset

    @property
    def green_flag_video(self):
        return self.to_video(self.green_flag_log) if self.green_flag_log is not None else None

    @property
    def leader_finish_video(self):
        return self.to_video(self.finish[0][0]) if self.finish else None

    def order_at(self, t_video):
        t = self.to_log(t_video)
        idx = bisect.bisect_right(self.order_change_times, t) - 1
        if idx < 0:
            return list(self.grid)
        return list(self.order_changes[idx][1])

    def position_at(self, name, t_video):
        order = self.order_at(t_video)
        return order.index(name) + 1 if name in order else None

    def grid_position(self, name):
        return self.grid.index(name) + 1 if name in self.grid else None

    def snapshot_at(self, t_video):
        """Most recent standings snapshot at or before t, as (t_video, rows)."""
        t = self.to_log(t_video)
        best = None
        for ts, rows in self.snapshots:
            if ts <= t:
                best = (self.to_video(ts), rows)
        return best

    def interval_between(self, ahead, behind, t_video, max_age=90):
        """Interval from a recent snapshot if the two cars were adjacent in it."""
        snap = self.snapshot_at(t_video)
        if not snap or t_video - snap[0] > max_age:
            return None
        names = [r[1] for r in snap[1]]
        if ahead in names and behind in names and names.index(behind) == names.index(ahead) + 1:
            return snap[1][names.index(behind)][2]
        return None

    def events_video(self):
        for e in self.events:
            ev = dict(e)
            ev['t'] = self.to_video(e['t'])
            yield ev

    def final_classification(self):
        """[(pos, name)] from finish events, falling back to the final running order."""
        if self.finish:
            rows = sorted(((pos, name) for _, name, pos in self.finish))
            finished = {n for _, n in rows}
            last_order = list(self.order_changes[-1][1]) if self.order_changes else self.grid
            for name in last_order:
                if name not in finished:
                    rows.append((len(rows) + 1, name))
            return rows
        order = list(self.order_changes[-1][1]) if self.order_changes else self.grid
        return [(i + 1, n) for i, n in enumerate(order)]

    def pit_stops_for(self, name, until_video=None):
        out = []
        for s in self.pits:
            if s['driver'] != name:
                continue
            if until_video is not None and self.to_video(s['enter']) > until_video:
                continue
            out.append(s)
        return out

    @staticmethod
    def stop_stationary(stop):
        if stop.get('box') is not None and stop.get('leave') is not None:
            return stop['leave'] - stop['box']
        return None

    @staticmethod
    def stop_lane_time(stop):
        if stop.get('leave') is not None:
            return stop['leave'] - stop['enter']
        return None

    def camera_at(self, t_video):
        t = self.to_log(t_video)
        current = None
        for ts, name in self.camera:
            if ts <= t:
                current = name
            else:
                break
        return current

    def flag_state_at(self, t_video, hold=20):
        """Returns 'green', 'yellow', 'chequered' or None (pre-race)."""
        lf = self.leader_finish_video
        if lf is not None and t_video >= lf:
            return 'chequered'
        gf = self.green_flag_video
        if gf is None or t_video < gf:
            return None
        last = None
        for e in self.events:
            if e['type'] == 'flag' and self.to_video(e['t']) <= t_video:
                last = e
        if last is None or 'green' in last['flag'].lower():
            return 'green'
        if t_video < self.to_video(last['t']) + hold:
            return 'double yellow' if 'double' in last['flag'].lower() else 'yellow'
        return 'green'

    # ------------------------------------------------------------------ stats
    def stats_until(self, t_video):
        """Counts used for data-backed stat graphics."""
        t_log = self.to_log(t_video)
        overtakes_by = {}
        lead_changes = 0
        total_overtakes = 0
        incidents_by = {}
        for e in self.events:
            if e['t'] > t_log:
                break
            if e['type'] == 'overtake':
                total_overtakes += 1
                overtakes_by[e['driver']] = overtakes_by.get(e['driver'], 0) + 1
                if e['pos'] == 1:
                    lead_changes += 1
            elif e['type'] == 'incident':
                incidents_by[e['driver']] = incidents_by.get(e['driver'], 0) + 1
        # Time spent leading
        lead_time = {}
        prev_t, prev_leader = None, None
        for ts, order in self.order_changes:
            if ts > t_log:
                break
            if prev_leader is not None and self.green_flag_log is not None:
                start = max(prev_t, self.green_flag_log)
                lead_time[prev_leader] = lead_time.get(prev_leader, 0) + max(0, ts - start)
            prev_t, prev_leader = ts, order[0] if order else None
        if prev_leader is not None and self.green_flag_log is not None:
            start = max(prev_t, self.green_flag_log)
            lead_time[prev_leader] = lead_time.get(prev_leader, 0) + max(0, t_log - start)
        return {
            'total_overtakes': total_overtakes,
            'overtakes_by': overtakes_by,
            'lead_changes': lead_changes,
            'incidents_by': incidents_by,
            'lead_time': lead_time,
        }

    def movers_at(self, t_video):
        """[(name, gained)] sorted by positions gained since the start."""
        order = self.order_at(t_video)
        out = []
        for i, name in enumerate(order):
            g = self.grid_position(name)
            if g is not None:
                out.append((name, g - (i + 1)))
        out.sort(key=lambda x: -x[1])
        return out


def detect_offset(raw_log_path, video_timed_paths):
    """Works out video_time - log_time by matching anchor lines that appear in both.

    The refined/commentary logs are synced to the video in the Review Events step,
    while the raw log is in logger time.
    """
    raw = read_raw_lines(raw_log_path)
    anchors = {}
    for t, cat, msg in raw:
        if cat == 'SESSION' and 'Green Flag' in msg:
            anchors.setdefault('green', t)
        elif cat == 'FINISH':
            if FINISH_LEADER_RE.search(msg) or R3E_WIN_RE.search(msg):
                anchors.setdefault('winner', t)
    if not anchors:
        return None
    offsets = []
    for path in video_timed_paths:
        if not path or not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                m = re.match(r'^\s*(\d+):(\d{2}):(\d{2})\s*-\s*(.*)$', line)
                if not m:
                    continue
                t = hms_to_sec(*m.group(1, 2, 3))
                body = m.group(4)
                if 'green' in anchors and 'Green Flag' in body:
                    offsets.append(t - anchors.pop('green'))
                elif 'winner' in anchors and (FINISH_LEADER_RE.search(body) or 'has taken the win' in body):
                    offsets.append(t - anchors.pop('winner'))
        if offsets:
            break
    if not offsets:
        return None
    return float(sorted(offsets)[len(offsets) // 2])


def read_commentary(path):
    """[(t_video, speaker, text)] from the commentary text file."""
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = re.match(r'^\s*(\d+):(\d{2}):(\d{2})\s*-\s*\[([^\]]+)\]\s*(.*)$', line.strip())
            if m:
                out.append((hms_to_sec(*m.group(1, 2, 3)), m.group(4).strip(), m.group(5).strip()))
    return out


def read_radio_calls(paths):
    """[{'t', 'speaker', 'segments': [(label, quote)]}] from the first video-timed log with (Radio) entries.

    The refined/filtered logs are already in video time.
    """
    for path in paths:
        calls = []
        for t, _, msg in read_raw_lines(path):
            m = RADIO_RE.search(msg)
            if not m:
                continue
            body = WORD_COUNT_RE.sub('', m.group(1))
            segments = [(lab.strip(' .,;-'), q.strip()) for lab, q in RADIO_SEGMENT_RE.findall(body)]
            if segments:
                calls.append({'t': t, 'speaker': segments[0][0], 'segments': segments})
        if calls:
            return calls
    return []


def commentator_names(commentary):
    names = []
    for _, speaker, _ in commentary:
        if speaker.isupper() or speaker in names:
            continue
        names.append(speaker)
    return names
