"""Schedules graphics on the video timeline and renders them as an overlay stream.

Only frames whose content differs are rendered: static stretches become a single
PNG with a long duration in an ffmpeg concat list, which ffmpeg composites over
the race video in the same pass as the final encode.
"""
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
CENTRED_TYPES = ('battle',)
START_MOVERS_AFTER = 60.0  # seconds after the green flag
RADIO_SPLIT = (' to ', ' over ', ' on ')
LANE_GAP = 1.5
MAX_SHIFT = 12.0
AUTO_MAX_SHIFT = 30.0


class OverlayRenderer:
    def __init__(self, project_path, cfg=None, plan=None, size=(1920, 1080), fps=30.0, race=None):
        self.project_path = project_path
        self.cfg = cfg or gcfg.load_config(project_path)
        self.plan = plan if plan is not None else gcfg.load_plan(project_path)
        self.size = tuple(size)
        self.fps = float(fps)
        self.race = race or gcfg.build_race(project_path, self.cfg)
        gcfg.ensure_drivers(self.cfg, self.race.drivers)
        self.tpl = Templates(self.cfg, self.race)
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
                items.append({'id': 'auto_comm', 'type': 'commentators', 'time': gf + 10.5, 'duration': 6.0,
                              'lane': 'popup', 'auto': True, 'data': {'names': names}})
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
                if lf is not None and e['t'] > lf + 5:
                    continue
                items.append({'id': f'auto_fl_{i}', 'type': 'fastest_lap', 'time': e['t'] + 1.0, 'duration': 7.0,
                              'lane': 'popup', 'auto': True, 'shift': True, 'driver': e['driver'],
                              'data': {k: e.get(k) for k in ('lap_time', 'lap', 'gain', 'prev_driver')}})
        if auto.get('radio'):
            items.extend(self._radio_items())
        if lf is not None and auto.get('chequered'):
            winner = race.finish[0][1]
            items.append({'id': 'auto_cheq', 'type': 'chequered', 'time': lf + 0.5, 'duration': 7.0,
                          'lane': 'popup', 'auto': True, 'driver': winner})
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
            if not enabled.get(g.get('type'), True) or g.get('disabled'):
                continue
            g = dict(g)
            g.setdefault('id', f'plan_{i}')
            g.setdefault('duration', self.cfg.get('popup_duration', 7.0))
            g['lane'] = 'popup'
            planned.append(g)

        placed = [it for it in auto if it['lane'] == 'ticker']
        blocking = []  # popup + centre lane intervals
        for it in sorted([a for a in auto if a['lane'] in ('popup', 'center') and not a.get('shift')],
                         key=lambda x: x['time']):
            blocking.append((it['time'], it['time'] + it['duration']))
            placed.append(it)
        # Automatic pop-ups that may move a little (fastest lap) get first pick of the free slots
        shiftable = sorted([a for a in auto if a.get('shift')], key=lambda x: x['time'])
        for it in shiftable + sorted(planned, key=lambda x: float(x['time'])):
            if self._place(it, blocking, AUTO_MAX_SHIFT if it.get('auto') else MAX_SHIFT):
                placed.append(it)
        # Team radio has its own slot on the right, one message at a time
        radio_blocking = []
        for it in sorted([a for a in auto if a['lane'] == 'radio'], key=lambda x: x['time']):
            if self._place(it, radio_blocking, AUTO_MAX_SHIFT):
                placed.append(it)
        placed.sort(key=lambda x: x['time'])
        return placed

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

    def frame_key(self, t):
        key = [self.race.flag_state_at(t)]
        for it in self._active(t):
            if it['lane'] == 'ticker':
                phase, idx = self._phase(it, t, TICKER_SLIDE, TICKER_SLIDE)
                scroll = int(self._ticker_scroll(it, t))
                key.append((it['id'], phase, idx, scroll))
            else:
                key.append((it['id'],) + self._phase(it, t, POPUP_IN, POPUP_OUT))
        return tuple(key)

    def _ticker_scroll(self, it, t):
        strip = self._strips[it['id']]
        tk = self.cfg['layout']['ticker']
        local = t - it['time'] - TICKER_SLIDE - TICKER_HOLD
        return max(0.0, local) * tk['speed']

    def _card(self, it):
        if it['id'] not in self._cards:
            try:
                self._cards[it['id']] = self.tpl.build(it)
            except Exception as e:
                print(f"  Could not draw {it.get('type')} graphic at {it.get('time', 0):.0f}s: {e}")
                self._cards[it['id']] = None
        return self._cards[it['id']]

    def render_frame(self, t):
        frame = self.static.copy()
        if self.cfg['layout']['leaderboard']['enabled']:
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
            built = self._card(it)
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
        elif it['type'] in CENTRED_TYPES:
            # Head-to-head cards sit centred above the ticker and open out from the middle
            pz = self.cfg['layout']['popups']
            y = pz['bottom'] - (img.height - margin) + p_out * 20
            alpha = min(1.0, p_in * 1.4) * (1 - p_out)
            vis = max(2, int(img.width * p_in)) if phase == 'in' else img.width
            left = (img.width - vis) // 2
            src = img.crop((left, 0, left + vis, img.height))
            x = (W - img.width) / 2 + left
        else:
            pz = self.cfg['layout']['popups']
            x = pz['x'] - margin - (1 - p_in) * 40 - p_out * 30
            y = pz['bottom'] - (img.height - margin)
            alpha = min(1.0, p_in * 1.4) * (1 - p_out)
            src = img
            if phase == 'in':
                # Wipe in from the left edge
                vis = max(1, int(img.width * p_in))
                src = img.crop((0, 0, vis, img.height))
        if alpha <= 0.01:
            return
        if alpha < 0.999:
            src = src.copy()
            src.putalpha(src.getchannel('A').point(lambda v: int(v * alpha)))
        x, y = int(round(x)), int(round(y))
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
    return (f"[{overlay_input}:v]fps={fps},format=rgba{scale}[ov];"
            f"[0:v][ov]overlay=0:0:eof_action=pass:format=auto[vout]")


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
