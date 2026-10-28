"""Live timing recorded alongside the race log, for the full timing tower graphic.

The RaceRoom logger writes <log name>_timing.jsonl (see Logger_Raceroom/leaderboard_recorder.py).
Without that file the tower falls back to the race log: the running order from overtakes and
the gaps from the periodic standings.

States are in LOG seconds; Timing.state_at() takes video seconds like the rest of RaceData.
"""
import bisect
import json
import os

ROW_FIELDS = ("name", "pos", "pos_class", "laps", "frac", "interval", "gap", "laps_down",
              "last", "best", "pit", "stops", "finish")
FINISH_FINISHED, FINISH_DNF, FINISH_DNQ, FINISH_DNS, FINISH_DQ = 1, 2, 3, 4, 5
PHASE_GREEN, PHASE_CHECKERED = 5, 6
LENGTH_LAPS = 1


def timing_path_for(log_path):
    return os.path.splitext(log_path)[0] + "_timing.jsonl" if log_path else None


class Timing:
    def __init__(self, race):
        self.race = race
        self.entries = {}      # name -> {'num', 'car', 'class', 'team', 'mfr'}
        self.states = []       # dicts: t (log), rows [dict], phase, rem, laps, fmt, yel
        self.recorded = False  # True when built from a live timing file
        path = timing_path_for(race.raw_log_path)
        if path and os.path.exists(path):
            self._load(path)
        if not self.states:
            self._from_log()
        self.times = [s['t'] for s in self.states]
        self.classes = []
        for name in self.order_names():
            c = self.entries.get(name, {}).get('class') or race.classes.get(name) or ''
            if c not in self.classes:
                self.classes.append(c)
        self.multiclass = self.recorded and len(self.classes) > 1

    # ------------------------------------------------------------------ loading
    def _load(self, path):
        segment, entries = [], {}
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # a half-written last line if the logger was killed
                kind = rec.get('type')
                if kind == 'session':
                    # The clock restarts each session; keep the last race, like RaceData does
                    segment = []
                elif kind == 'entry':
                    entries[rec['name']] = rec
                elif kind == 'timing' and rec.get('rows'):
                    segment.append(rec)
        prev_t = None
        for rec in segment:
            rows = [dict(zip(ROW_FIELDS, r)) for r in rec['rows']]
            # An order change is shown from when it first appeared, never before the previous record
            t = rec.get('ot', rec['t'])
            if prev_t is not None:
                t = max(t, prev_t)
            prev_t = rec['t']
            self.states.append({'t': t, 'rows': rows, 'phase': rec.get('phase'), 'rem': rec.get('rem'),
                                'rem_t': rec['t'], 'laps': rec.get('laps'), 'fmt': rec.get('fmt'),
                                'yel': rec.get('yel') or []})
        self.entries = entries
        self.recorded = bool(self.states)

    def _from_log(self):
        """Order from RaceData's order changes, gaps from the latest standings snapshot."""
        race = self.race
        snaps = list(race.snapshots)
        times = sorted({t for t, _ in race.order_changes})
        si = -1
        for t in times:
            while si + 1 < len(snaps) and snaps[si + 1][0] <= t:
                si += 1
            order = race.order_at(race.to_video(t))
            gaps = {}
            if si >= 0:
                for _, name, gap in snaps[si][1]:
                    gaps[name] = gap
            rows = []
            for i, name in enumerate(order):
                rows.append({'name': name, 'pos': i + 1, 'pos_class': i + 1, 'interval': None, 'gap': None,
                             'laps_down': 0, 'pit': 0, 'finish': 0, 'text': gaps.get(name)})
            self.states.append({'t': t, 'rows': rows, 'phase': None, 'rem': None, 'laps': None, 'fmt': None,
                                'yel': []})

    # ------------------------------------------------------------------ queries
    def order_names(self):
        return [r['name'] for r in self.states[-1]['rows']] if self.states else list(self.race.grid)

    def index_at(self, t_video):
        return bisect.bisect_right(self.times, self.race.to_log(t_video)) - 1

    def state(self, idx):
        return self.states[idx] if 0 <= idx < len(self.states) else None

    def entry(self, name):
        return self.entries.get(name, {})

    def driver_class(self, name):
        return self.entry(name).get('class') or self.race.classes.get(name) or ''

    def clock_text(self, idx, t_video):
        """Header clock: time remaining, LAP x/y, or the race time when nothing was recorded."""
        race = self.race
        st = self.state(idx)
        t_log = race.to_log(t_video)
        lf = race.leader_finish_video
        if lf is not None and t_video >= lf:
            return 'FINISH'
        if st and st.get('laps') and st.get('fmt') == LENGTH_LAPS:
            lead = st['rows'][0].get('laps') or 0
            return f"LAP {min(st['laps'], lead + 1)}/{st['laps']}"
        if st and st.get('rem') is not None:
            rem = st['rem']
            if st.get('phase') == PHASE_GREEN:
                rem -= max(0.0, t_log - st.get('rem_t', st['t']))
            if rem <= 0:
                return 'FINAL LAP'
            return _hms(rem)
        gf = race.green_flag_video
        return _hms(t_video - gf) if gf is not None and t_video >= gf else '00:00:00'

    def lap_text(self, idx, t_video):
        """'LAP 7' (the leader's lap) next to the clock in timed races; '' where the clock already shows
        the lap, before the start, after the finish or without recorded timing."""
        st = self.state(idx)
        if not st or not self.recorded or (st.get('laps') and st.get('fmt') == LENGTH_LAPS):
            return ''
        gf, lf = self.race.green_flag_video, self.race.leader_finish_video
        if gf is None or t_video < gf or (lf is not None and t_video >= lf):
            return ''
        lead = st['rows'][0].get('laps')
        return f"LAP {lead + 1}" if lead is not None else ''

    def laps_at(self, name, t_video):
        """Laps completed by a car at a moment, or None without recorded timing."""
        st = self.state(self.index_at(t_video))
        if not st or not self.recorded:
            return None
        return next((r.get('laps') for r in st['rows'] if r['name'] == name), None)

    def spread_at(self, t_video, split=1.5):
        """([(name, gap to the leader)], [[names] per pack]) for the cars still racing, the packs split
        wherever the interval to the car ahead is more than `split` seconds. ([], []) without gaps."""
        st = self.state(self.index_at(t_video))
        rows = []
        if st and self.recorded:
            for r in st['rows']:
                if r.get('pit') or r.get('finish') or (r.get('laps_down') or 0) > 0:
                    continue
                rows.append((r['name'], 0.0 if r['pos'] == 1 else r.get('gap')))
        else:
            snap = self.race.snapshot_at(t_video)
            total = 0.0
            for pos, name, gap in (snap[1] if snap else []):
                try:
                    total += 0.0 if pos == 1 else float(gap.rstrip('s').lstrip('+'))
                except ValueError:
                    continue
                rows.append((name, total))
        rows = [(n, g) for n, g in rows if g is not None]
        packs = []
        prev = None
        for name, gap in rows:
            if prev is None or gap - prev > split:
                packs.append([])
            packs[-1].append(name)
            prev = gap
        return rows, packs

    def lap_time(self):
        """A typical racing lap in seconds (median of the best laps), or None."""
        best = sorted({r.get('best') for s in self.states[-40:] for r in s['rows'] if r.get('best')})
        return best[len(best) // 2] if best else None


def _hms(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def row_value(row, seconds, laps_down):
    """Value column text: a status, laps down, or +seconds (to the car ahead or the leader, worked out by
    the caller). None for the leader's own cell."""
    fin = row.get('finish') or 0
    if fin == FINISH_DNF:
        return 'DNF'
    if fin == FINISH_DQ:
        return 'DSQ'
    if fin == FINISH_DNS:
        return 'DNS'
    if row.get('pit') and fin != FINISH_FINISHED:
        return 'PIT'
    if row.get('text') is not None:  # fallback from the race log's standings
        txt = row['text']
        return None if txt.lower() == 'leader' else txt.rstrip('s').upper()
    if laps_down:
        return f"+{laps_down} LAP" + ('S' if laps_down > 1 else '')
    return f"+{seconds:.3f}" if seconds is not None else ''
