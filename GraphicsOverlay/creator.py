"""Graphic Creator: builds the AI brief, validates the AI's picks against the race data,
and provides a rule-based plan when no AI is available.

The AI decides WHEN to show a graphic, WHICH type and writes the words. Every number
on screen (positions, gaps, pit times, stats) is filled in here from the race data.
"""
import json
import os
import re

from . import config as gcfg
from .race_data import commentator_names, fmt_clock, norm_key, parse_clock, read_commentary
from .renderer import OverlayRenderer
from .templates import race_time_label

RACE_CONTROL_FLAGS = ('yellow', 'double yellow', 'blue', 'white', 'red', 'green', 'chequered')


# ---------------------------------------------------------------- name resolution
class NameResolver:
    def __init__(self, cfg, roster):
        self.lookup = {}
        for name in roster:
            meta = gcfg.driver_meta(cfg, name)
            for alias in (name, meta.get('display'), meta.get('short')):
                if alias:
                    self.lookup.setdefault(norm_key(alias), name)

    def __call__(self, text):
        if not text:
            return None
        key = norm_key(str(text))
        if key in self.lookup:
            return self.lookup[key]
        hits = [n for k, n in self.lookup.items() if len(key) >= 3 and (key in k or k in key)]
        return hits[0] if len(set(hits)) == 1 else None


# ---------------------------------------------------------------- facts
def build_facts(race, cfg):
    """Data-backed stats the AI may put on a stat card, each valid from its 'as_of' time."""
    facts = []
    checkpoints = [race.to_video(t) for t, _ in race.snapshots]
    if race.leader_finish_video:
        checkpoints.append(race.leader_finish_video + 1)
    gf = race.green_flag_video or 0
    for cp in checkpoints:
        if cp < gf + 60:
            continue
        st = race.stats_until(cp)
        as_of = fmt_clock(cp)

        def add(label, value, driver=None, note=''):
            facts.append({'id': f"F{len(facts) + 1}", 'as_of': as_of, 'as_of_sec': cp, 'label': label,
                          'value': value, 'driver': driver, 'note': note})

        add('Overtakes in the race so far', st['total_overtakes'])
        if st['overtakes_by']:
            top, n = max(st['overtakes_by'].items(), key=lambda kv: kv[1])
            add('Most overtakes by one driver', n, top)
        add('Lead changes so far', st['lead_changes'])
        if st['lead_time']:
            leader, secs = max(st['lead_time'].items(), key=lambda kv: kv[1])
            race_secs = max(1, cp - gf)
            add('Share of the race spent leading', f"{min(100, round(100 * secs / race_secs))}%", leader,
                f"{fmt_clock(secs)} of {fmt_clock(race_secs)}")
        movers = race.movers_at(cp)
        if movers and movers[0][1] > 0:
            add('Positions gained since the start', f"+{movers[0][1]}", movers[0][0])
        if movers and movers[-1][1] < 0:
            add('Positions lost since the start', str(movers[-1][1]), movers[-1][0])
        stops = [(s, race.stop_stationary(s)) for s in race.pits if race.to_video(s['enter']) <= cp]
        stops = [(s, v) for s, v in stops if v]
        if stops:
            best = min(stops, key=lambda x: x[1])
            add('Quickest pit stop (stationary)', f"{best[1]:.0f}s", best[0]['driver'])
        if st['incidents_by']:
            who, n = max(st['incidents_by'].items(), key=lambda kv: kv[1])
            if n > 1:
                add('Times stopped or slow on track', n, who)
    return facts


# ---------------------------------------------------------------- AI brief
def _dedupe_battles(events):
    out = []
    last = {}
    for e in events:
        if e['type'] != 'battle':
            continue
        key = (e['ahead'], e['behind'])
        prev = last.get(key)
        if prev and e['t'] - prev['end'] < 40:
            prev['end'] = e['t']
            prev['count'] += 1
            prev['side_by_side'] |= e['side_by_side']
            continue
        rec = {'t': e['t'], 'end': e['t'], 'ahead': e['ahead'], 'behind': e['behind'], 'pos': e['pos'],
               'count': 1, 'side_by_side': e['side_by_side']}
        last[key] = rec
        out.append(rec)
    return out


def build_brief(project_path, cfg, race, renderer):
    files = gcfg.project_files(project_path)
    commentary = read_commentary(files['commentary_text'])
    champ = cfg['championship']
    lines = []
    lines.append('=== RACE ===')
    lines.append(f"Championship: {champ.get('name') or 'n/a'} | {champ.get('season', '')} {champ.get('round', '')}")
    lines.append(f"Track: {gcfg.clean_track_name(race.track)} ({race.track_layout}), "
                 f"{(race.track_length_m or 0) / 1000:.3f} km")
    if race.green_flag_video is not None:
        lines.append(f"Green flag at video time {fmt_clock(race.green_flag_video)}")
    if race.leader_finish_video is not None:
        lines.append(f"Winner takes the chequered flag at {fmt_clock(race.leader_finish_video)}")
    if commentary:
        lines.append(f"Commentary runs {fmt_clock(commentary[0][0])} - {fmt_clock(commentary[-1][0])} "
                     f"by {', '.join(commentator_names(commentary))}")

    lines.append('\n=== DRIVERS (use the ID exactly in your output) ===')
    final = {n: p for p, n in race.final_classification()}
    stats = race.stats_until(10 ** 7)
    for name in race.drivers:
        m = gcfg.driver_meta(cfg, name)
        stops = race.pit_stops_for(name)
        bits = [f"ID: {name}", f"name: {m['display']}"]
        for k in ('number', 'country', 'team'):
            if m.get(k):
                bits.append(f"{k}: {m[k]}")
        bits.append(f"grid P{race.grid_position(name)}")
        if name in final:
            bits.append(f"finished P{final[name]}")
        bits.append(f"overtakes {stats['overtakes_by'].get(name, 0)}")
        bits.append(f"pit stops {len(stops)}")
        bits.append('car preview image: yes' if m.get('car_image') else 'car preview image: no')
        lines.append(' | '.join(bits))

    lines.append('\n=== RACE TIMELINE (video time) ===')
    battles = _dedupe_battles(race.events)
    timeline = []
    for e in race.events:
        t = race.to_video(e['t'])
        typ = e['type']
        if typ == 'overtake':
            extra = f" at {e['corner']}" if e['corner'] else ''
            timeline.append((t, f"OVERTAKE {e['driver']} passes {', '.join(e['victims'])} for P{e['pos']}{extra}"))
        elif typ == 'pit_in':
            timeline.append((t, f"PIT IN {e['driver']}"))
        elif typ == 'pit_out':
            timeline.append((t, f"PIT OUT {e['driver']}"))
        elif typ == 'incident':
            where = f" at {e['where']}" if e['where'] else ''
            timeline.append((t, f"INCIDENT {e['driver']} (P{e['pos']}) slow/stopped on track{where}"))
        elif typ == 'recover':
            timeline.append((t, f"RECOVERED {e['driver']} back on the move after {e['stopped_for']}s"))
        elif typ == 'flag':
            timeline.append((t, f"FLAG {e['flag']}"))
        elif typ == 'standings':
            rows = ', '.join(f"P{p} {n} ({g})" for p, n, g in e['rows'])
            timeline.append((t, f"STANDINGS {rows}"))
        elif typ == 'finish':
            timeline.append((t, f"FINISH {e['driver']} P{e['pos']}"))
        elif typ == 'fastest_lap':
            lap = f" on lap {e['lap']}" if e.get('lap') else ''
            timeline.append((t, f"FASTEST LAP {e['driver']} {e['lap_time']}{lap}"))
        elif typ == 'green':
            timeline.append((t, 'GREEN FLAG - race start'))
    for b in battles:
        sbs = ', side by side' if b['side_by_side'] else ''
        span = f" until {fmt_clock(race.to_video(b['end']))}" if b['end'] > b['t'] else ''
        timeline.append((race.to_video(b['t']), f"BATTLE {b['ahead']} (P{b['pos']}) defending from "
                                                 f"{b['behind']}{sbs}{span}"))
    for t_log, name in race.camera:
        t = race.to_video(t_log)
        if race.green_flag_video is None or t >= race.green_flag_video - 5:
            timeline.append((t, f"CAMERA now following {name}"))
    timeline.sort(key=lambda x: x[0])
    lines.extend(f"{fmt_clock(t)} {txt}" for t, txt in timeline)

    lines.append('\n=== FACTS (for "stat" graphics: reference fact_id, only at or after its as_of time) ===')
    for f in build_facts(race, cfg):
        drv = f" | driver: {f['driver']}" if f['driver'] else ''
        note = f" ({f['note']})" if f['note'] else ''
        lines.append(f"{f['id']} | as_of {f['as_of']} | {f['label']}: {f['value']}{note}{drv}")

    lines.append('\n=== RESERVED (automatic graphics already on screen, do not overlap the popup slot) ===')
    for it in renderer.items:
        if it['lane'] in ('popup', 'center'):
            lines.append(f"{fmt_clock(it['time'])}-{fmt_clock(it['time'] + min(it['duration'], 40))} {it['type']}")
    radio = [it for it in renderer.items if it['lane'] == 'radio']
    if radio:
        lines.append('(Team radio quotes are shown automatically on the right at: '
                     + ', '.join(fmt_clock(it['time']) for it in radio)
                     + ' - popups may coexist with them; do not repeat a radio quote as a headline.)')
    lines.append('(A standings ticker also runs along the bottom at: '
                 + ', '.join(fmt_clock(it['time']) for it in renderer.items if it['lane'] == 'ticker')
                 + ' - popups may coexist with it.)')

    lines.append('\n=== COMMENTARY TRANSCRIPT (video time) ===')
    lines.extend(f"{fmt_clock(t)} [{s}] {txt}" for t, s, txt in commentary)
    return '\n'.join(lines)


def settings_block(cfg, race):
    gf = race.green_flag_video or 0
    lf = race.leader_finish_video or gf + 1800
    gap = gcfg.DENSITY_GAP.get(cfg.get('popup_density', 'medium'), 45)
    target = max(4, int((lf - gf) / gap))
    types = [t for t, on in cfg['popup_types'].items() if on]
    return (f"\n=== SETTINGS ===\n"
            f"Allowed types: {', '.join(types)}\n"
            f"Window: {fmt_clock(gf + 15)} to {fmt_clock(lf)}\n"
            f"Target number of graphics: about {target} (at least {max(8, gap // 3)} seconds between graphics)\n"
            f"Overtake cards: at most {gcfg.OVERTAKE_MAX}, at least {int(gcfg.OVERTAKE_MIN_GAP // 60)} minutes apart"
            f"{'; lead changes are covered automatically, never use overtake for P1' if cfg['auto_graphics'].get('new_leader') else ''}\n"
            f"Default duration: {cfg.get('popup_duration', 7)} seconds\n")


# ---------------------------------------------------------------- validation
def _nearest(events, t, pred, window):
    best, best_d = None, None
    for e in events:
        if not pred(e):
            continue
        d = abs(e['t'] - t)
        if d <= window and (best_d is None or d < best_d):
            best, best_d = e, d
    return best


def resolve_entry(raw, cfg, race, resolve, facts):
    """Turns one AI entry into a render-ready graphic, or returns (None, reason)."""
    typ = str(raw.get('type', '')).strip()
    if typ not in gcfg.POPUP_TYPES:
        return None, f"unknown type '{typ}'"
    if not cfg['popup_types'].get(typ, True):
        return None, f"type '{typ}' disabled"
    try:
        t = parse_clock(raw.get('time'))
    except (TypeError, ValueError):
        return None, f"bad time {raw.get('time')!r}"
    gf = race.green_flag_video or 0
    lf = race.leader_finish_video
    if t < gf or (lf is not None and t > lf + 8):
        return None, f"time {fmt_clock(t)} outside the race"
    dur = float(raw.get('duration') or cfg.get('popup_duration', 7))
    g = {'type': typ, 'time': t, 'duration': max(4.0, min(12.0, dur)), 'source': 'ai',
         'title': str(raw.get('title') or '')[:80], 'text': str(raw.get('text') or '')[:180]}
    if raw.get('label'):
        g['label'] = str(raw['label'])[:28]
    d1 = resolve(raw.get('driver'))
    d2 = resolve(raw.get('driver2'))
    events = list(race.events_video())
    data = {}

    if typ == 'driver_card':
        if not d1:
            return None, 'driver_card needs a driver'
        g['driver'] = d1
        pos = race.position_at(d1, t)
        data = {'pos': pos, 'gained': (race.grid_position(d1) - pos) if pos and race.grid_position(d1) else None}

    elif typ == 'battle':
        if not d1 or not d2 or d1 == d2:
            return None, 'battle needs two drivers'
        p1, p2 = race.position_at(d1, t), race.position_at(d2, t)
        if p1 and p2 and p1 > p2:
            d1, d2, p1, p2 = d2, d1, p2, p1
        if p1 and p2 and p2 - p1 > 2:
            return None, f"{d1} and {d2} are not close (P{p1} vs P{p2})"
        g['driver'], g['driver2'] = d1, d2
        data = {'pos': p1, 'pos2': p2}
        gap = race.interval_between(d1, d2, t)
        if gap:
            data['gap'] = gap.rstrip('s')
        if not g['title']:
            g['title'] = f"BATTLE FOR P{p1}" if p1 else 'BATTLE'

    elif typ == 'overtake':
        if not d1:
            return None, 'overtake needs a driver'
        ev = _nearest(events, t, lambda e: e['type'] == 'overtake' and e['driver'] == d1, 25)
        if not ev:
            return None, f"no overtake by {d1} near {fmt_clock(t)}"
        if ev['pos'] == 1 and cfg['auto_graphics'].get('new_leader'):
            return None, 'lead changes get the automatic new leader banner'
        g['driver'], g['driver2'] = d1, ev['victim']
        g['time'] = max(t, ev['t'] + 1.0)
        data = {'pos': ev['pos'], 'victim': ev['victim'], 'victims': ev['victims'], 'corner': ev['corner']}

    elif typ == 'race_control':
        if not g['title']:
            return None, 'race_control needs a message title'
        flag = str(raw.get('flag') or 'yellow').lower()
        data = {'flag': flag if flag in RACE_CONTROL_FLAGS else 'yellow'}
        if data['flag'] in ('yellow', 'double yellow'):
            return None, 'yellow flags are shown by the timing tower turning yellow'
        if d1:
            g['driver'] = d1

    elif typ == 'stat':
        fact = facts.get(str(raw.get('fact_id', '')).strip())
        if not fact:
            return None, f"unknown fact_id {raw.get('fact_id')!r}"
        if t < fact['as_of_sec'] - 1:
            g['time'] = t = fact['as_of_sec'] + 1
        data = {'value': fact['value'], 'label': fact['label']}
        if not g['title']:
            g['title'] = fact['label']
        drv = d1 or fact.get('driver')
        if drv:
            g['driver'] = drv

    elif typ == 'movers':
        movers = race.movers_at(t)
        rows = [m for m in movers if m[1] > 0][:3]
        losers = [m for m in movers if m[1] < 0]
        if losers and len(rows) < 4:
            rows.append(losers[-1])
        if not rows:
            return None, 'nobody has gained or lost places'
        data = {'rows': [{'driver': n, 'grid': race.grid_position(n), 'pos': race.position_at(n, t), 'gained': gd}
                         for n, gd in rows]}

    elif typ == 'headline':
        if not g['title']:
            return None, 'headline needs a title'
        if d1:
            g['driver'] = d1

    if typ == 'race_control':
        data['race_time'] = race_time_label(race, g['time'])
    g['data'] = data
    return g, None


def finalize(entries, cfg):
    """Sorts and thins graphics that sit too close together."""
    gap = max(6, gcfg.DENSITY_GAP.get(cfg.get('popup_density', 'medium'), 45) // 4)
    out = []
    for g in sorted(entries, key=lambda x: x['time']):
        if out and g['time'] < out[-1]['time'] + out[-1]['duration'] + 1.5:
            g['time'] = out[-1]['time'] + out[-1]['duration'] + 1.5
        if out and g['time'] - out[-1]['time'] < gap and g['type'] == out[-1]['type']:
            continue
        out.append(g)
    for i, g in enumerate(out):
        g['id'] = f"g{i + 1:03d}"
        g['at'] = fmt_clock(g['time'])
    return out


def parse_ai_json(text):
    text = text.strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if m:
        text = m.group(1)
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        raise ValueError('No JSON object found in the AI response')
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- rule-based fallback
def rule_based(cfg, race):
    """A sensible plan straight from the data, used when the AI is unavailable."""
    picks = []
    gf = race.green_flag_video or 0
    lf = race.leader_finish_video or 10 ** 7
    enabled = cfg['popup_types']
    seen_cam = set()
    for e in race.events_video():
        t = e['t']
        if t < gf + 20 or t > lf:
            continue
        if e['type'] == 'overtake' and 2 <= e['pos'] <= 3 and enabled.get('overtake'):
            picks.append((2, {'type': 'overtake', 'time': t + 1, 'driver': e['driver']}))
        # Yellows (and the stopped cars behind them) turn the timing tower yellow instead of a pop-up
        elif (e['type'] == 'flag' and enabled.get('race_control')
              and not any(k in e['flag'].lower() for k in ('yellow', 'green'))):
            picks.append((1, {'type': 'race_control', 'time': t + 1, 'flag': e['flag'].lower(),
                              'title': f"{e['flag'].upper()} FLAG"}))
    for t_log, name in race.camera:
        t = race.to_video(t_log)
        if gf + 30 < t < lf and name not in seen_cam and enabled.get('driver_card'):
            seen_cam.add(name)
            picks.append((4, {'type': 'driver_card', 'time': t + 3, 'driver': name}))
    if enabled.get('movers'):
        t = gf + 480
        while t < lf - 120:
            picks.append((5, {'type': 'movers', 'time': t}))
            t += 600
    if enabled.get('stat'):
        for i, f in enumerate(build_facts(race, cfg)[:30:6]):
            picks.append((5, {'type': 'stat', 'time': f['as_of_sec'] + 30, 'fact_id': f['id']}))
    return [p for _, p in sorted(picks, key=lambda x: x[0])]


def build_plan(project_path, cfg, race, raw_entries, source):
    renderer = OverlayRenderer(project_path, cfg, {'graphics': []}, race=race)
    resolve = NameResolver(cfg, race.drivers)
    facts = {f['id']: f for f in build_facts(race, cfg)}
    good, dropped = [], 0
    for raw in raw_entries:
        g, reason = resolve_entry(raw, cfg, race, resolve, facts)
        if g:
            good.append(g)
        else:
            dropped += 1
            print(f"  - dropped {raw.get('type')} @ {raw.get('time')}: {reason}")
    # Keep clear of the automatic graphics
    reserved = [(it['time'], it['time'] + it['duration']) for it in renderer.items
                if it['lane'] in ('popup', 'center')]
    good = [g for g in good if not any(s - 2 < g['time'] < e + 1 for s, e in reserved)] if reserved else good
    good = gcfg.thin_overtakes(good)
    plan ={'source': source, 'offset_sec': race.offset, 'graphics': finalize(good, cfg)}
    print(f"  {len(plan['graphics'])} graphics kept, {dropped} dropped")
    return plan


def save_plan(project_path, plan):
    path = os.path.join(project_path, gcfg.PLAN_NAME)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------- driver info from notes
DRIVER_INFO_PROMPT = """You are filling in broadcast graphics data for a sim-racing championship.
From the project notes below, find details for each driver ID in the roster.
Return JSON: {"drivers": {"<driver ID exactly as given>": {"display": "First Last or nickname as fans know them",
"short": "TV ticker name in caps: usually the surname (PAYNE), or the whole handle if it is a nickname (NEW ORDER)",
"number": "car number or empty", "country": "3-letter country code like GBR, RSA, USA, ESP, SCO or empty",
"team": "team or livery description, max 30 characters, or empty", "color": "#RRGGBB main livery colour or empty"}}}
Only use information supported by the notes (you may infer a livery colour from its description). Leave unknown fields empty.

ROSTER:
"""


def ai_driver_info(roster, notes, ask):
    prompt = DRIVER_INFO_PROMPT + '\n'.join(roster) + '\n\nPROJECT NOTES:\n' + (notes or '(none)')
    data = parse_ai_json(ask(prompt))
    return data.get('drivers', {})


def merge_driver_info(cfg, info, overwrite=False):
    """Fills driver fields from AI info. Fields still at their automatic default count as empty."""
    changed = 0
    for name, fields in info.items():
        if name not in cfg['drivers'] or not isinstance(fields, dict):
            continue
        current = cfg['drivers'][name]
        default = gcfg.default_driver(name)
        for k in ('display', 'short', 'number', 'country', 'team', 'color'):
            v = str(fields.get(k) or '').strip()
            if k == 'short':
                v = v.upper()[:14]
            if k == 'color' and v and not re.fullmatch(r'#[0-9a-fA-F]{6}', v):
                continue
            unset = not current.get(k) or current.get(k) == default.get(k)
            if v and v != current.get(k) and (overwrite or unset):
                current[k] = v
                changed += 1
                if k == 'display' and not fields.get('short') and (overwrite or current.get('short') == default['short']):
                    current['short'] = v.split()[-1].upper()[:14]
    return changed


def make_gemini_asker(api_key, model, thinking_level='MEDIUM', max_tokens=32768):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    def ask(prompt):
        thinking = None
        if 'gemini-3' in model.lower() and thinking_level.upper() != 'NONE':
            thinking = types.ThinkingConfig(thinking_level=thinking_level.upper())
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=max_tokens, thinking_config=thinking,
                                               response_mime_type='application/json'))
        return resp.text or ''

    return ask
