"""Schedules graphics on the video timeline and renders them as an overlay stream.

Only frames whose content differs are rendered: static stretches become a single
PNG with a long duration in an ffmpeg concat list, which ffmpeg composites over
the race video in the same pass as the final encode.
"""
import bisect
import math
import multiprocessing
import os
import shutil
import subprocess
import time

from PIL import Image

from . import config as gcfg
from .drawing import ease_in, ease_out
from .race_data import commentator_names, read_commentary, read_radio_calls
from .templates import Templates, race_time_label

POPUP_IN = 0.45
POPUP_OUT = 0.35
TICKER_SLIDE = 0.4
TICKER_HOLD = 2.0
YELLOW_FLAGS = ('yellow', 'double yellow')  # shown by the timing tower turning yellow, not a pop-up
# type -> (start after pop-in begins, duration) of the held animation
# (commentators, chequered and pit timers run for their whole time on screen, see _anim_timing)
ANIMATED = {'overtake': (1.0, 0.7), 'new_leader': (0.4, 1.1), 'commentators': (0.2, 1.4),
            'title_card': (0.0, 1.6), 'chequered': (0.0, 1.0), 'gap_tracker': (0.6, 2.2), 'pit_timer': (0.0, 1.0)}
ANIM_RATE = {'commentators': 15, 'chequered': 15, 'pit_timer': 10}  # steps per second, else the video fps
TOWER_OFF_AFTER = 3.0      # the timing tower comes off this long after the winner crosses the line
FASTEST_LAP_FROM = 5       # fastest laps only count from this lap (early laps are in traffic and on cold tyres)
PIT_TIMER_LEAD = 1.2       # the pit timer card is up this long before the car stops in its box
UNDERCUT_GAP = 3.0         # a stop with a rival this close (seconds) is an undercut/overcut story
GAP_WINDOW_LAPS = 2.0      # closing/pulling away is measured over this many laps
GAP_CHANGE = 1.0           # ... and needs the interval to change by at least this much
STRIKING = 1.0             # 'striking distance': within this many seconds of the car ahead
GAP_SPACING = 150.0        # seconds between two battle forecasts
GAP_PAIR_SPACING = 300.0   # ... and between two about the same pair
GAP_MAX = 6
START_MOVERS_AFTER = 60.0  # seconds after the green flag
LEADER_AFTER = 20.0        # no leader banner for lead changes in the opening seconds
LEADER_SETTLE = 8.0        # if the lead swaps again this soon, only the later change gets a banner
RADIO_SPLIT = (' to ', ' over ', ' on ')
LANE_GAP = 1.5
MAX_SHIFT = 12.0
AUTO_MAX_SHIFT = 30.0
PIT_GROUP_GAP = 12.0  # pit entries this close together share one pop-up
PIT_GROUP_MAX = 5


class OverlayRenderer:
    def __init__(self, project_path, cfg=None, plan=None, size=(1920, 1080), fps=30.0, race=None):
        self.project_path = project_path
        self.cfg = cfg or gcfg.load_config(project_path)
        self.plan = plan if plan is not None else gcfg.load_plan(project_path)
        self.size = tuple(size)
        self.fps = float(fps)
        self.race = race or gcfg.build_race(project_path, self.cfg)
        gcfg.ensure_drivers(self.cfg, self.race.drivers, self.race.entries())
        if multiprocessing.parent_process() is None:
            # Missing flags are downloaded once, before the render workers start
            from .flags import ensure as ensure_flags
            ensure_flags(gcfg.driver_meta(self.cfg, n).get('country') for n in self.race.drivers)
        self.tpl = Templates(self.cfg, self.race)
        if self.tpl.tower:
            self.tpl.tower.fps = self.fps
        self.static = self.tpl.static_layer(self.size)
        self._cards = {}
        self._strips = {}
        self.items = self._schedule()

    # ------------------------------------------------------------------ scheduling
    def _auto_items(self):
        race, cfg = self.race, self.cfg
        auto = cfg['auto_graphics']
        items = []
        gf = race.green_flag_video
        lf = race.leader_finish_video
        if gf is not None and auto.get('title_card'):
            items.append({'id': 'auto_title', 'type': 'title_card', 'time': gf + 1.0, 'duration': 8.0,
                          'lane': 'popup', 'auto': True, 'data': {'drivers': len(race.drivers)}})
        if gf is not None and auto.get('commentators'):
            names = commentator_names(read_commentary(gcfg.project_files(self.project_path)['commentary_text']))
            if names:
                bios = {n: gcfg.commentator_bio(cfg, n) for n in names}
                # A little longer on screen when there are bios to read
                dur = min(12.0, 6.0 + 2.0 * sum(1 for b in bios.values() if b))
                items.append({'id': 'auto_comm', 'type': 'commentators', 'time': gf + 10.5, 'duration': dur,
                              'lane': 'popup', 'auto': True,
                              'data': {'names': names, 'bios': bios, 'anim_dur': dur - 0.2 - POPUP_OUT}})
        tk = cfg['layout']['ticker']
        if tk['enabled']:
            if gf is not None and auto.get('start_ticker') and race.grid:
                rows = [(i + 1, n, '') for i, n in enumerate(race.grid)]
                items.append(self._ticker_item('auto_grid', gf + 0.5, 'STARTING GRID',
                                               gcfg.clean_track_name(race.track).upper(), rows))
            if auto.get('standings_ticker'):
                for i, (t_log, rows) in enumerate(race.snapshots):
                    t = race.to_video(t_log)
                    if gf is not None and t < gf + 20:
                        continue
                    if lf is not None and t >= lf:
                        continue
                    trows = []
                    for pos, name, gap in rows:
                        gap = 'LEADER' if gap.lower() == 'leader' else gap.rstrip('s')
                        trows.append((pos, name, gap))
                    sub = f"INTERVAL  ·  {race_time_label(race, t)}"
                    items.append(self._ticker_item(f'auto_stand_{i}', t, 'LIVE STANDINGS', sub, trows))
        if gf is not None and auto.get('start_movers'):
            t = gf + START_MOVERS_AFTER
            movers = race.movers_at(t)
            if movers and (movers[0][1] > 0 or movers[-1][1] < 0):
                rows = {}
                for key, (name, gained) in (('gainer', movers[0]), ('loser', movers[-1])):
                    if (gained > 0) if key == 'gainer' else (gained < 0):
                        rows[key] = {'driver': name, 'grid': race.grid_position(name),
                                     'pos': race.position_at(name, t), 'gained': gained}
                items.append({'id': 'auto_start_movers', 'type': 'start_movers', 'time': t, 'duration': 8.0,
                              'lane': 'popup', 'auto': True, 'shift': True, 'data': rows})
        if auto.get('fastest_lap'):
            for i, e in enumerate(race.events_video()):
                if e['type'] != 'fastest_lap' or (gf is not None and e['t'] < gf):
                    continue
                if e.get('lap') is not None and e['lap'] < FASTEST_LAP_FROM:
                    continue
                if lf is not None and e['t'] > lf + 5:
                    continue
                items.append({'id': f'auto_fl_{i}', 'type': 'fastest_lap', 'time': e['t'] + 1.0, 'duration': 7.0,
                              'lane': 'top', 'auto': True, 'driver': e['driver'],
                              'data': {k: e.get(k) for k in ('lap_time', 'lap', 'gain', 'prev_driver')}})
        if gf is not None and auto.get('new_leader'):
            passes = [e for e in race.events_video() if e['type'] == 'overtake' and e['pos'] == 1
                      and e['t'] >= gf + LEADER_AFTER and (lf is None or e['t'] <= lf)]
            for i, e in enumerate(passes):
                if i + 1 < len(passes) and passes[i + 1]['t'] - e['t'] < LEADER_SETTLE:
                    continue
                items.append({'id': f'auto_leader_{i}', 'type': 'new_leader', 'time': e['t'] + 1.0,
                              'duration': 6.5, 'lane': 'top', 'auto': True, 'driver': e['driver'],
                              'driver2': e['victim'], 'data': {'victims': e['victims'], 'corner': e['corner']}})
        if auto.get('pit_stops'):
            items.extend(self._pit_items())
        if auto.get('gap_tracker', True):
            items.extend(self._gap_items())
        if auto.get('sponsors', True):
            items.extend(self._sponsor_items())
        if auto.get('radio'):
            items.extend(self._radio_items())
        if lf is not None and auto.get('chequered'):
            winner = race.finish[0][1]
            items.append({'id': 'auto_cheq', 'type': 'chequered', 'time': lf + 0.3, 'duration': 9.0,
                          'lane': 'top', 'auto': True, 'driver': winner})
        if lf is not None and auto.get('results'):
            rows = []
            for pos, name in race.final_classification():
                g = race.grid_position(name)
                rows.append({'pos': pos, 'driver': name, 'gained': (g - pos) if g else None,
                             'stops': len(race.pit_stops_for(name))})
            # Shown once most of the field has crossed the line
            finish_times = [race.to_video(t) for t, _, _ in race.finish]
            start = max(lf + 9.0, finish_times[min(len(finish_times) - 1, 2)] + 3.0)
            # Long duration: it stays up until the video ends
            items.append({'id': 'auto_results', 'type': 'results', 'time': start, 'duration': 90.0,
                          'lane': 'center', 'auto': True, 'data': {'rows': rows}})
        return items

    def _pit_items(self):
        """Pit stop timers for the stops that matter: the front-runners, and undercut/overcut fights with a
        rival close by. The card comes up as the car reaches its box and the stationary time ticks up."""
        race = self.race
        gf, lf = race.green_flag_video, race.leader_finish_video
        tm = race.timing
        items = []
        for i, stop in enumerate(race.pits):
            stationary = race.stop_stationary(stop)
            if not stationary or stationary > 90 or stop['box'] is None:
                continue
            box = race.to_video(stop['box'])
            if (gf is not None and box < gf) or (lf is not None and box > lf):
                continue
            name = stop['driver']
            pos = stop.get('pos_before')
            label = None
            st = tm.state(tm.index_at(race.to_video(stop['enter']) - 1)) if tm.recorded else None
            me = next((k for k, r in enumerate(st['rows']) if r['name'] == name), None) if st else None
            if me is not None:
                rows = st['rows']
                r = rows[me]
                pos = r['pos']
                ahead = rows[me - 1] if me > 0 else None
                behind = rows[me + 1] if me + 1 < len(rows) else None
                if ahead and (r.get('interval') or 99) < UNDERCUT_GAP and ahead.get('stops') == r.get('stops'):
                    label = f"UNDERCUT ON {self.tpl.meta(ahead['name'])['short'].upper()}"
                elif (behind and (behind.get('interval') or 99) < UNDERCUT_GAP
                      and (behind.get('stops') or 0) > (r.get('stops') or 0)):
                    label = f"RESPONDS TO {self.tpl.meta(behind['name'])['short'].upper()}"
            if label is None and not (pos and pos <= 3):
                continue
            items.append({'id': f'auto_pit_{i}', 'type': 'pit_timer', 'time': box - PIT_TIMER_LEAD,
                          'duration': PIT_TIMER_LEAD + stationary + 3.5, 'lane': 'popup', 'auto': True,
                          'driver': name, 'title': label or 'PIT STOP',
                          'data': {'pos': pos, 'stationary': stationary}, 'rank': (label is None, pos or 99)})
        # A timer has to run while the car is stopped, so it can't move: when stops overlap, the undercut
        # fights and then the higher-placed car win
        kept = []
        for it in sorted(items, key=lambda x: x['rank']):
            if all(it['time'] >= k['time'] + k['duration'] + LANE_GAP or k['time'] >= it['time'] + it['duration'] + LANE_GAP
                   for k in kept):
                kept.append(it)
        return sorted(kept, key=lambda x: x['time'])

    def _gap_items(self):
        """Battle forecasts from the recorded timing: a car closing on the one ahead (or dropping away from it)
        by at least GAP_CHANGE seconds over GAP_WINDOW_LAPS laps."""
        race = self.race
        tm = race.timing
        gf, lf = race.green_flag_video, race.leader_finish_video
        if not tm.recorded or gf is None:
            return []
        lap = tm.lap_time() or ((race.track_length_m or 4000) / 45.0)
        window = lap * GAP_WINDOW_LAPS
        items, last_any, last_pair = [], -1e9, {}
        for i, st in enumerate(tm.states):
            t = race.to_video(st['t'])
            if t < gf + max(90.0, lap * 1.5) or (lf is not None and t > lf - 5) or t - last_any < GAP_SPACING:
                continue
            j = bisect.bisect_right(tm.times, st['t'] - window) - 1
            if j < 0:
                continue
            old_rows = tm.states[j]['rows']
            then = {r['name']: r for r in old_rows}
            then_ahead = {b['name']: a['name'] for a, b in zip(old_rows, old_rows[1:])}
            laps = (st['t'] - tm.states[j]['t']) / lap
            best = None
            for a, r in zip(st['rows'], st['rows'][1:]):
                old = then.get(r['name'])
                if not old or then_ahead.get(r['name']) != a['name']:
                    continue
                if any(x.get('pit') or x.get('finish') or (x.get('laps_down') or 0) for x in (a, r, old)):
                    continue
                if old.get('stops') != r.get('stops') or then[a['name']].get('stops') != a.get('stops'):
                    continue  # a pit stop in the window, not on-track pace
                now_gap, old_gap = r.get('interval'), old.get('interval')
                if now_gap is None or old_gap is None:
                    continue
                rate = (old_gap - now_gap) / max(0.5, laps)
                if old_gap - now_gap >= GAP_CHANGE and 0.3 <= now_gap <= 3.0:
                    mode = 'closing'
                elif now_gap - old_gap >= GAP_CHANGE and old_gap <= 1.5 and now_gap <= 4.0:
                    mode = 'pulling'
                else:
                    continue
                if t - last_pair.get((r['name'], a['name']), -1e9) < GAP_PAIR_SPACING:
                    continue
                if best is None or a['pos'] < best[1]['pos']:
                    best = (r, a, old_gap, now_gap, rate, mode)
            if best is None:
                continue
            r, a, old_gap, now_gap, rate, mode = best
            laps_to = max(0, math.ceil((now_gap - STRIKING) / rate)) if mode == 'closing' and rate > 0 else None
            items.append({'id': f'auto_gap_{i}', 'type': 'gap_tracker', 'time': t + 0.5, 'duration': 9.0,
                          'lane': 'popup', 'auto': True, 'shift': True, 'driver': r['name'], 'driver2': a['name'],
                          'data': {'mode': mode, 'gap_from': old_gap, 'gap_now': now_gap, 'rate': rate,
                                   'laps_to': laps_to, 'pos_behind': r['pos'], 'pos_ahead': a['pos']}})
            last_any = t
            last_pair[(r['name'], a['name'])] = t
            if len(items) >= GAP_MAX:
                break
        return items

    def _sponsor_items(self):
        """Each sponsor gets one moment on screen, spread through the race."""
        gf, lf = self.race.green_flag_video, self.race.leader_finish_video
        if gf is None or lf is None:
            return []
        kinds = [k for k in ('title', 'timing', 'partner') if self.tpl.sponsor(k)]
        items = []
        for n, kind in enumerate(kinds):
            t = gf + (lf - gf) * (n + 1) / (len(kinds) + 1)
            items.append({'id': f'auto_sponsor_{kind}', 'type': 'sponsor', 'time': t, 'duration': 5.5,
                          'lane': 'popup', 'auto': True, 'shift': True, 'data': {'kind': kind}})
        return items

    def _radio_items(self):
        from .creator import NameResolver  # creator imports this module
        resolve = NameResolver(self.cfg, self.race.drivers)
        items = []
        files = gcfg.project_files(self.project_path)
        for i, call in enumerate(read_radio_calls(files['video_timed'])):
            # "Thommo to engineer" / "Engineer to Payne" / "Payne over team radio"
            parts = [call['speaker']]
            for sep in RADIO_SPLIT:
                parts = [p for chunk in parts for p in chunk.split(sep)]
            driver = next((resolve(p) for p in parts if resolve(p)), None)
            segments = []
            for label, quote in call['segments']:
                head = label
                for sep in RADIO_SPLIT:
                    head = head.split(sep)[0]
                is_driver = bool(driver) and resolve(head) == driver
                shown = self.tpl.meta(driver)['short'] if is_driver else head
                segments.append({'label': shown, 'quote': quote, 'is_driver': is_driver})
            words = sum(len(s['quote'].split()) for s in segments[:3])
            dur = max(6.0, min(12.0, 3.0 + words * 0.32))
            items.append({'id': f'auto_radio_{i}', 'type': 'radio', 'time': float(call['t']), 'duration': dur,
                          'lane': 'radio', 'auto': True, 'driver': driver,
                          'data': {'speaker': parts[0], 'segments': segments}})
        return items

    def _ticker_item(self, item_id, t, title, subtitle, rows):
        tk = self.cfg['layout']['ticker']
        strip = self.tpl.ticker_strip('standings', rows, tk['h'])
        self._strips[item_id] = strip
        # Hold, then crawl until every driver has passed through once more
        dur = TICKER_SLIDE * 2 + TICKER_HOLD + (strip.width + Templates.TICKER_GAP) / max(1, tk['speed'])
        dur = max(12.0, min(float(tk.get('max_duration', 40)), dur))
        # Then keep it up (still crawling) so viewers have time to read the gaps
        dur += float(tk.get('extra_hold', 10))
        return {'id': item_id, 'type': 'ticker', 'time': t, 'duration': dur, 'lane': 'ticker',
                'auto': True, 'title': title, 'subtitle': subtitle}

    def _schedule(self):
        auto = self._auto_items()
        enabled = self.cfg['popup_types']
        planned = []
        for i, g in enumerate(self.plan.get('graphics', [])):
            # Types no longer offered to the AI (e.g. pit stops, now automatic) are skipped in old plans
            if g.get('type') not in gcfg.POPUP_TYPES or not enabled.get(g.get('type'), True) or g.get('disabled'):
                continue
            if g['type'] == 'race_control' and (g.get('data') or {}).get('flag', 'yellow') in YELLOW_FLAGS:
                continue
            g = dict(g)
            g.setdefault('id', f'plan_{i}')
            g.setdefault('duration', self.cfg.get('popup_duration', 7.0))
            g['lane'] = 'popup'
            if g['type'] == 'overtake' and not self._refresh_overtake(g):
                continue
            planned.append(g)
        planned = gcfg.thin_overtakes(planned)

        placed = [it for it in auto if it['lane'] == 'ticker']
        blocking = []  # popup + centre lane intervals
        for it in sorted([a for a in auto if a['lane'] in ('popup', 'center') and not a.get('shift')],
                         key=lambda x: x['time']):
            blocking.append((it['time'], it['time'] + it['duration']))
            placed.append(it)
        # Automatic pop-ups that may move a little (pit stops, start movers) get first pick of the free slots
        shiftable = sorted([a for a in auto if a.get('shift')], key=lambda x: x['time'])
        for it in shiftable + sorted(planned, key=lambda x: float(x['time'])):
            if self._place(it, blocking, AUTO_MAX_SHIFT if it.get('auto') else MAX_SHIFT):
                placed.append(it)
        # Team radio and the top centre slot (leader banner, fastest lap) have their own slots, one at a time
        for lane in ('radio', 'top'):
            lane_blocking = []
            for it in sorted([a for a in auto if a['lane'] == lane], key=lambda x: x['time']):
                if self._place(it, lane_blocking, AUTO_MAX_SHIFT):
                    placed.append(it)
        placed.sort(key=lambda x: x['time'])
        return placed

    def _refresh_overtake(self, g):
        """Re-reads a planned overtake from the race data (plans made before passes on pitting or
        stopped cars were filtered out, or before multi-car passes were stored). False = drop it."""
        evs = [e for e in self.race.events_video() if e['type'] == 'overtake' and e['driver'] == g.get('driver')
               and abs(e['t'] - float(g['time'])) <= 25]
        if not evs:
            print(f"  Skipping overtake at {float(g['time']):.0f}s (not a real on-track pass)")
            return False
        ev = min(evs, key=lambda e: abs(e['t'] - float(g['time'])))
        if ev['pos'] == 1 and self.cfg['auto_graphics'].get('new_leader'):
            return False  # the leader banner covers it
        g['driver2'] = ev['victim']
        g['data'] = {**(g.get('data') or {}), 'pos': ev['pos'], 'victim': ev['victim'],
                     'victims': ev['victims'], 'corner': ev['corner']}
        return True

    @staticmethod
    def _place(it, blocking, max_shift):
        """Moves it to the first free slot at or after its time; False if that is too far away."""
        t = float(it['time'])
        start = t
        for _ in range(20):
            clash = next(((s, e) for s, e in blocking
                          if t < e + LANE_GAP and t + it['duration'] + LANE_GAP > s), None)
            if not clash:
                break
            t = clash[1] + LANE_GAP
        if t - start > max_shift:
            print(f"  Skipping {it['type']} at {start:.0f}s (no free slot nearby)")
            return False
        it['time'] = t
        blocking.append((t, t + it['duration']))
        return True

    # ------------------------------------------------------------------ per-frame state
    def _active(self, t):
        return [it for it in self.items if it['time'] <= t < it['time'] + it['duration']]

    def _phase(self, it, t, t_in, t_out):
        """('in'|'hold'|'out', frame index within the transition)."""
        local = t - it['time']
        if local < t_in:
            return 'in', int(local * self.fps)
        if local > it['duration'] - t_out:
            return 'out', int((local - (it['duration'] - t_out)) * self.fps)
        return 'hold', 0

    def tower_shown(self, t):
        """The timing tower comes off a few seconds after the leader takes the chequered flag."""
        lf = self.race.leader_finish_video
        return self.tpl.tower is not None and (lf is None or t < lf + TOWER_OFF_AFTER)

    def frame_key(self, t):
        key = [self.race.flag_state_at(t)]
        if self.tower_shown(t):
            key.append(self.tpl.tower.key(t))
        for it in self._active(t):
            if it['lane'] == 'ticker':
                phase, idx = self._phase(it, t, TICKER_SLIDE, TICKER_SLIDE)
                scroll = int(self._ticker_scroll(it, t))
                key.append((it['id'], phase, idx, scroll))
            else:
                key.append((it['id'],) + self._phase(it, t, POPUP_IN, POPUP_OUT) + (self._anim_step(it, t),))
        return tuple(key)

    def _anim_timing(self, it):
        """(start, duration, frame count) of a card's held animation."""
        start, dur = ANIMATED[it['type']]
        if it['type'] == 'overtake':
            # Passing several cars at once takes a little longer to play out
            dur += 0.3 * (len(Templates.overtake_victims(it)) - 1)
        elif it['type'] in ('commentators', 'chequered'):
            dur = max(0.5, it['duration'] - start - POPUP_OUT)
        elif it['type'] == 'pit_timer':
            start, dur = PIT_TIMER_LEAD, max(0.1, it['data']['stationary'])
        return start, dur, max(1, int(dur * ANIM_RATE.get(it['type'], self.fps)))

    def _anim_step(self, it, t):
        """Frame index of a card's held animation (None for static cards)."""
        if it['type'] not in ANIMATED:
            return None
        start, dur, steps = self._anim_timing(it)
        return int(max(0, min(steps, (t - it['time'] - start) / dur * steps)))

    def _ticker_scroll(self, it, t):
        strip = self._strips[it['id']]
        tk = self.cfg['layout']['ticker']
        local = t - it['time'] - TICKER_SLIDE - TICKER_HOLD
        return max(0.0, local) * tk['speed']

    def _card(self, it, t):
        step = self._anim_step(it, t)
        key = (it['id'], step)
        if key not in self._cards:
            anim = None
            if step is not None:
                anim = step / self._anim_timing(it)[2]
            try:
                self._cards[key] = self.tpl.build(it, anim)
            except Exception as e:
                print(f"  Could not draw {it.get('type')} graphic at {it.get('time', 0):.0f}s: {e}")
                self._cards[key] = None
        return self._cards[key]

    def render_frame(self, t):
        frame = self.static.copy()
        if self.tpl.tower:
            if self.tower_shown(t):
                self.tpl.tower.draw(frame, t)
        elif self.cfg['layout']['leaderboard']['enabled'] and self.race.has_hud:
            self.tpl.leaderboard_footer(frame, self.race.flag_state_at(t))
        for it in self._active(t):
            if it['lane'] == 'ticker':
                phase, _ = self._phase(it, t, TICKER_SLIDE, TICKER_SLIDE)
                local = t - it['time']
                if phase == 'in':
                    slide = ease_out(local / TICKER_SLIDE)
                elif phase == 'out':
                    slide = 1 - ease_in((local - (it['duration'] - TICKER_SLIDE)) / TICKER_SLIDE)
                else:
                    slide = 1.0
                band = self.tpl.ticker_frame(self.size, it['title'], it.get('subtitle', ''),
                                             self._strips[it['id']], self._ticker_scroll(it, t), slide)
                frame.alpha_composite(band)
                continue
            built = self._card(it, t)
            if not built:
                continue
            img, margin = built
            self._composite_card(frame, it, img, margin, t)
        return frame

    def _composite_card(self, frame, it, img, margin, t):
        W, H = self.size
        local = t - it['time']
        phase, _ = self._phase(it, t, POPUP_IN, POPUP_OUT)
        p_in = ease_out(local / POPUP_IN) if phase == 'in' else 1.0
        p_out = ease_in((local - (it['duration'] - POPUP_OUT)) / POPUP_OUT) if phase == 'out' else 0.0
        if it['lane'] == 'center':
            x = (W - img.width) / 2
            y = (H - img.height) / 2 - 30 + (1 - p_in) * 30 - p_out * 20
            alpha = p_in * (1 - p_out)
            src = img
        elif it['lane'] == 'radio':
            # Middle right: slides in from the right edge, wiping open right-to-left
            rz = self.cfg['layout']['radio']
            x = rz['right'] + margin - img.width + (1 - p_in) * 60 + p_out * 30
            y = rz['cy'] - img.height / 2
            alpha = min(1.0, p_in * 1.4) * (1 - p_out)
            src = img
            if phase == 'in':
                vis = max(1, int(img.width * p_in))
                src = img.crop((img.width - vis, 0, img.width, img.height))
                x += img.width - vis
        elif it['lane'] == 'top':
            # Top centre: drops in from above while opening out from the middle
            lz = self.cfg['layout']['leader']
            y = lz['y'] - margin - (1 - p_in) * 24 - p_out * 24
            alpha = min(1.0, p_in * 1.4) * (1 - p_out)
            vis = max(2, int(img.width * p_in)) if phase == 'in' else img.width
            left = (img.width - vis) // 2
            src = img.crop((left, 0, left + vis, img.height))
            x = (W - img.width) / 2 + left
        else:
            # Pop-ups sit centred above the ticker (clear of the timing tower) and open out from the middle
            pz = self.cfg['layout']['popups']
            y = pz['bottom'] - (img.height - margin) + p_out * 20
            alpha = min(1.0, p_in * 1.4) * (1 - p_out)
            vis = max(2, int(img.width * p_in)) if phase == 'in' else img.width
            left = (img.width - vis) // 2
            src = img.crop((left, 0, left + vis, img.height))
            x = (W - img.width) / 2 + left
        if alpha <= 0.01:
            return
        if alpha < 0.999:
            src = src.copy()
            src.putalpha(src.getchannel('A').point(lambda v: int(v * alpha)))
        x, y = int(round(x)), int(round(y))
        # Entirely off-frame (e.g. the radio card sliding in from past the right edge)
        if x >= W or y >= H or x + src.width <= 0 or y + src.height <= 0:
            return
        # Clip to the frame
        cx0, cy0 = max(0, -x), max(0, -y)
        if cx0 or cy0 or x + src.width > W or y + src.height > H:
            src = src.crop((cx0, cy0, min(src.width, W - x), min(src.height, H - y)))
            x, y = x + cx0, y + cy0
        if src.width > 0 and src.height > 0:
            frame.alpha_composite(src, (x, y))

    # ------------------------------------------------------------------ export
    def build_runs(self, start, end):
        """[(key, first_t, n_frames)] covering [start, end) at the renderer fps."""
        n = int(round((end - start) * self.fps))
        runs = []
        for i in range(n):
            t = start + i / self.fps
            k = self.frame_key(t)
            if runs and runs[-1][0] == k:
                runs[-1][2] += 1
            else:
                runs.append([k, t, 1])
        return runs

    def summary(self):
        counts = {}
        for it in self.items:
            counts[it['type']] = counts.get(it['type'], 0) + 1
        return counts


# -------------------------------------------------------------------- multiprocessing workers
_WORKER = None


def _init_worker(project_path, cfg, plan, size, fps):
    global _WORKER
    _WORKER = OverlayRenderer(project_path, cfg, plan, size, fps)


def _render_task(task):
    idx, t, path = task
    _WORKER.render_frame(t).save(path, compress_level=1)
    return idx


def render_overlay(project_path, start, end, out_dir, fps=30.0, size=(1920, 1080), cfg=None, plan=None,
                   workers=None):
    """Renders the overlay for video time [start, end) and returns the ffconcat path.

    The overlay timeline starts at 0 = video time `start`.
    """
    t0 = time.time()
    cfg = cfg or gcfg.load_config(project_path)
    plan = plan if plan is not None else gcfg.load_plan(project_path)
    base = tuple(cfg.get('base_resolution', [1920, 1080]))
    renderer = OverlayRenderer(project_path, cfg, plan, base, fps)
    print(f"  Graphics scheduled: {renderer.summary()}")
    print(f"  Sync offset: {renderer.race.offset:+.1f}s (video = log + offset)")

    runs = renderer.build_runs(start, end)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    unique = {}
    tasks = []
    for key, t, _ in runs:
        if key not in unique:
            idx = len(unique)
            path = os.path.join(out_dir, f"ov_{idx:06d}.png")
            unique[key] = path
            tasks.append((idx, t, path))
    print(f"  Rendering {len(tasks)} unique overlay frames for {len(runs)} segments...")

    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
    if workers > 1 and len(tasks) > 50:
        ctx = multiprocessing.get_context('spawn')
        with ctx.Pool(workers, initializer=_init_worker,
                      initargs=(project_path, renderer.cfg, plan, base, fps)) as pool:
            done = 0
            for _ in pool.imap_unordered(_render_task, tasks, chunksize=8):
                done += 1
                if done % 250 == 0 or done == len(tasks):
                    print(f"    {done}/{len(tasks)} frames", flush=True)
    else:
        for idx, t, path in tasks:
            renderer.render_frame(t).save(path, compress_level=1)

    concat_path = os.path.join(out_dir, 'overlay.ffconcat')
    with open(concat_path, 'w', encoding='utf-8') as f:
        f.write('ffconcat version 1.0\n')
        last = None
        for key, _, count in runs:
            last = unique[key]
            f.write(f"file '{os.path.basename(last)}'\nduration {count / fps:.6f}\n")
        if last:
            # The concat demuxer ignores the final entry's duration unless repeated
            f.write(f"file '{os.path.basename(last)}'\n")
    print(f"  Overlay ready in {time.time() - t0:.0f}s")
    return concat_path


def overlay_filter(fps, video_size, base_size=(1920, 1080), overlay_input=2):
    """filter_complex fragment producing [vout] from [0:v] and the overlay input."""
    scale = ''
    if tuple(video_size) != tuple(base_size):
        scale = f",scale={video_size[0]}:{video_size[1]}"
    # Convert (and scale) each overlay image once, before fps repeats it for every video frame, and in the
    # video's own pixel format so the overlay filter blends without converting every frame again
    return (f"[{overlay_input}:v]format=rgba{scale},format=yuva420p,fps={fps}[ov];"
            f"[0:v][ov]overlay=0:0:eof_action=pass:format=yuv420[vout]")


def graphics_enabled(project_path):
    if not project_path or not os.path.exists(os.path.join(project_path, gcfg.CONFIG_NAME)):
        return False
    try:
        return bool(gcfg.load_config(project_path).get('enabled', True))
    except Exception as e:
        print(f"  Could not read graphics config: {e}")
        return False


def probe_size(video_path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", video_path],
                         capture_output=True, text=True).stdout.strip()
    try:
        w, h = out.split(',')[:2]
        return int(w), int(h)
    except ValueError:
        return 1920, 1080


def extract_frame(video_path, t, out_path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t):.3f}", "-i", video_path,
                    "-frames:v", "1", out_path], check=False)
    return os.path.exists(out_path)


def preview_image(renderer, video_path, t, tmp_png):
    """Video frame at t with the overlay composited, as an RGBA image (base resolution)."""
    over = renderer.render_frame(t)
    if video_path and extract_frame(video_path, t, tmp_png):
        bg = Image.open(tmp_png).convert('RGBA').resize(over.size)
    else:
        bg = Image.new('RGBA', over.size, (40, 44, 52, 255))
    bg.alpha_composite(over)
    return bg
