"""Per-project graphics configuration (graphics_config.json) and project helpers."""
import copy
import json
import os
import re
import shutil
import time

import sys

from . import race_data

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
import racefiles  # noqa: E402
GRAPHICS_DIR = os.path.join(APP_ROOT, 'Graphics')
CAR_PREVIEW_DIR = os.path.join(GRAPHICS_DIR, 'CarPreviews')
LOGO_DIR = os.path.join(GRAPHICS_DIR, 'Logos')
FLAG_DIR = os.path.join(GRAPHICS_DIR, 'Flags')
COMMENTATOR_DIR = os.path.join(GRAPHICS_DIR, 'Commentators')
IMAGE_EXTS = ('.png', '.dds', '.jpg', '.jpeg', '.webp', '.bmp', '.tga')

CONFIG_NAME = racefiles.FILES['graphics_config']
# Branding, layout and driver links shared by every new project (one league, many races)
DEFAULTS_PATH = os.path.join(GRAPHICS_DIR, 'graphics_defaults.json')
# Race-specific settings that are never carried over to other projects
PROJECT_ONLY_KEYS = ('sync',)
PLAN_NAME = racefiles.FILES['graphics_plan']

# Popup types the Graphic Creator can schedule (label shown in the setup page)
POPUP_TYPES = {
    'driver_card': 'Driver ID card (car preview, position, team)',
    'battle': 'Head-to-head battle',
    'overtake': 'Position change',
    'race_control': 'Race Control message',
    'stat': 'Data stat card',
    'movers': 'Positions gained since start',
    'headline': 'Paddock news / storyline',
    'gaps': 'Gaps on track (the field and its packs)',
    'insight': 'Analysis and circuit notes: track guide, strategy, where the track is, its history and famous races',
}

# Insight tabs about the circuit itself: a third line of text and the event flag behind it
CIRCUIT_LABELS = ('THE CIRCUIT', 'HISTORY', 'FROM THE ARCHIVE', 'LOCAL KNOWLEDGE')

# Graphics the renderer adds on its own from the race data
AUTO_TYPES = {
    'title_card': 'Opening title card',
    'commentators': 'AI commentator names and bios',
    'commentator_info': 'Commentator info box: a paragraph the AI researches each race on who the commentators '
                        'are, why they are iconic and their link to the track (a third of the way in)',
    'start_ticker': 'Starting grid ticker at the green flag',
    'standings_ticker': 'Standings ticker on every leaderboard update',
    'start_movers': 'Biggest gainer and loser a minute after the start',
    'fastest_lap': 'Fastest lap of the race (top centre, from lap 2 on)',
    'new_leader': 'New race leader banner (top centre, from 20 seconds into the race)',
    'pit_stops': 'Pit stops: a compact timer for the top 4, a small "in pits" tag for the rest',
    'gap_tracker': 'Last 4 laps: lap times of a car steadily catching (or dropping back from) the one ahead, '
                   'side by side, from lap 5',
    'radio': 'Team radio quotes (middle right)',
    'chequered': 'Chequered flag banner (top centre; the timing tower comes off)',
    'results': 'Final classification panel',
}

DEFAULT_CONFIG = {
    'version': 1,
    'enabled': True,
    'base_resolution': [1920, 1080],
    'sync': {'offset_sec': None, 'auto': True},
    'championship': {
        'name': '',
        'short_name': '',
        'season': '',
        'round': '',
        'event_name': '',
        'logo': '',
        'logo_tint_white': False,
        # Flag image faded in behind the opening race card (Graphics/Flags, or any image)
        'event_flag': '',
    },
    'theme': {
        'accent': '#E10600',
        'accent2': '#FFC400',
        'panel': '#0B0E14',
        'panel_alpha': 0.9,
        'text': '#FFFFFF',
        'muted': '#9AA3B2',
    },
    'layout': {
        # Rectangles are in 1920x1080 space, matching the in-game HUD positions
        'leaderboard': {'enabled': True, 'x': 10, 'y': 110, 'w': 420, 'row_h': 25, 'pad': 15,
                        'rows': None, 'header': True, 'footer': True},
        'map': {'enabled': True, 'x': 1597, 'y': 113, 'w': 306, 'h': 299, 'header': True},
        # Full timing tower drawn from the recorded live timing (RaceRoom replays show no leaderboard
        # or map, so the frames above are not used there; AMS2 uses the leaderboard frame only with the
        # tower off). Rows shrink to fit above 'bottom', the width shrinks to fit the names (up to 'w');
        # 'cycle' lists the value columns shown in turn (interval, gap, name). 'class_groups' joins classes
        # that race each other: 'all', or 'A + B; C + D' (a group can be named first: 'DTM = A + B').
        'tower': {'enabled': True, 'x': 24, 'y': 24, 'w': 400, 'row_h': 34, 'bottom': 820, 'rows': 0,
                  'cycle': 'interval,gap,name', 'cycle_sec': 15, 'class_groups': ''},
        'logo': {'enabled': True, 'x': 1903, 'y': 14, 'h': 58, 'anchor': 'right'},
        'extra_logos': [],
        'ticker': {'enabled': True, 'y': 1018, 'h': 46, 'speed': 150, 'max_duration': 40,
                   'extra_hold': 10},
        # Pop-up cards sit centred above 'bottom' (clear of the timing tower)
        'popups': {'x': 40, 'bottom': 1000, 'max_w': 780},
        # Team radio quotes, F1 style: anchored by their right edge and vertical centre
        'radio': {'right': 1880, 'cy': 640, 'w': 400},
        # New race leader banner, centred at the top
        'leader': {'y': 24},
        # Pit stop timers and IN PITS tags: top left corner (x 0 = just right of the timing tower)
        'pits': {'x': 0, 'y': 150},
        # AI commentators list: right edge and top (0 = lined up under the map on the right)
        'commentators': {'right': 0, 'y': 0},
    },
    'auto_graphics': {k: True for k in AUTO_TYPES},
    'popup_types': {k: True for k in POPUP_TYPES},
    'popup_density': 'medium',
    'popup_duration': 7.0,
    # Graphics are rendered at this frame rate on export (capped at the video's), then repeated to the
    # video's: fewer frames to draw, so the export is quicker
    'render_fps': 15,
    'drivers': {},
    # Sponsors: name and/or logo each, always on another graphic, never on their own. Title: race card,
    # chequered flag and results. Timing: the timing tower and gap trends. Partner: after the
    # start, gaps on track and the pit stop timer. Only the logo is shown, no caption.
    'sponsors': {k: {'name': '', 'logo': ''} for k in ('title', 'timing', 'partner')},
    # AI commentator name (as in the commentary) -> {'bio': short line shown under their name}
    'commentators': {},
    # One picture down the right of the AI commentators card (a cut-out PNG with a transparent background
    # reads best); empty = no picture, the names use the full width
    'commentator_photo': '',
    # Shown under the AI commentators card (empty hides it)
    'commentator_disclaimer': None,
}

DEFAULT_DISCLAIMER = ("The commentary is generated by AI. The drivers and the racing on track are real people "
                      "racing for real.")

DENSITY_GAP = {'low': 75, 'medium': 45, 'high': 28}

# Position-change cards are shown now and then, not for every pass
OVERTAKE_MAX = 8
OVERTAKE_MIN_GAP = 120.0  # seconds between two of them


def thin_overtakes(graphics):
    """Keeps the few overtake cards worth showing: front-runners first, well spread out."""
    ranked = sorted([g for g in graphics if g.get('type') == 'overtake'],
                    key=lambda g: ((g.get('data') or {}).get('pos') or 99, float(g['time'])))
    keep = []
    for g in ranked:
        if len(keep) < OVERTAKE_MAX and all(abs(float(g['time']) - float(k['time'])) >= OVERTAKE_MIN_GAP
                                            for k in keep):
            keep.append(g)
    kept = {id(g) for g in keep}
    return [g for g in graphics if g.get('type') != 'overtake' or id(g) in kept]


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def resolve_path(path):
    if not path:
        return ''
    return path if os.path.isabs(path) else os.path.join(APP_ROOT, path)


def relative_path(path):
    if not path:
        return ''
    try:
        rel = os.path.relpath(path, APP_ROOT)
        if not rel.startswith('..'):
            return rel.replace('\\', '/')
    except ValueError:
        pass
    return path


def load_project_state(project_path):
    path = os.path.join(project_path, 'project_state.json')
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def latest_file(folder, prefix):
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.startswith(prefix) and f.endswith('.txt')]
    except FileNotFoundError:
        return None
    return max(files, key=os.path.getmtime) if files else None


def project_files(project_path):
    """The pipeline files the graphics need, by their fixed names in the race folder."""
    def existing(key):
        p = racefiles.race_file(project_path, key)
        return p if os.path.exists(p) else None

    video = racefiles.read_race(project_path).get('video')
    return {
        'raw_log': existing('race_log'),
        'director_notes': existing('director_notes'),
        'video_file': video if video and os.path.exists(video) else None,
        'final_audio': existing('commentary_audio'),
        'commentary_text': existing('commentary'),
        # Video-timed logs used to detect the sync offset
        'video_timed': [p for p in (existing('events_reviewed'), existing('events_spiced'),
                                    existing('events_timed'), existing('commentary')) if p],
        'project_notes': racefiles.race_file(project_path, 'race_notes'),
    }


def load_defaults():
    if os.path.exists(DEFAULTS_PATH):
        try:
            with open(DEFAULTS_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  Could not read {DEFAULTS_PATH}: {e}")
    return {}


def save_defaults(cfg):
    """Stores this project's setup as the starting point for future races."""
    data = {k: v for k, v in cfg.items() if k not in PROJECT_ONLY_KEYS}
    # Keep drivers from earlier races too, so the whole league builds up over time.
    # A championship's roster is complete already, so it replaces them (no mixing between leagues).
    drivers = {} if cfg.get(PRESET_KEY) else dict(load_defaults().get('drivers', {}))
    drivers.update(cfg.get('drivers', {}))
    data['drivers'] = drivers
    os.makedirs(GRAPHICS_DIR, exist_ok=True)
    with open(DEFAULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return DEFAULTS_PATH


# ---------------------------------------------------------------- championships
# Saved branding, layout and drivers per championship, so you can swap between leagues
CHAMPIONSHIP_DIR = os.path.join(GRAPHICS_DIR, 'Championships')
CHAMPIONSHIP_BACKUP_DIR = os.path.join(CHAMPIONSHIP_DIR, 'Backups')
CHAMPIONSHIP_BACKUPS_KEPT = 10
# The project remembers which championship it was set up from
PRESET_KEY = 'championship_preset'
RACE_ONLY_CHAMPIONSHIP_KEYS = ('round', 'event_name')


def championship_file(name):
    slug = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_') or 'championship'
    return os.path.join(CHAMPIONSHIP_DIR, f"{slug}.json")


def list_championships():
    """Saved championship names, sorted."""
    if not os.path.isdir(CHAMPIONSHIP_DIR):
        return []
    names = []
    for f in os.listdir(CHAMPIONSHIP_DIR):
        if f.lower().endswith('.json'):
            try:
                with open(os.path.join(CHAMPIONSHIP_DIR, f), 'r', encoding='utf-8') as fh:
                    names.append(json.load(fh).get(PRESET_KEY) or os.path.splitext(f)[0])
            except Exception as e:
                print(f"  Could not read championship {f}: {e}")
    return sorted(names, key=str.lower)


def championship_exists(name):
    return os.path.exists(championship_file(name))


def backup_championship(name):
    """Copies the saved file to Championships/Backups before it is overwritten (keeps the last few)."""
    path = championship_file(name)
    if not os.path.exists(path):
        return None
    os.makedirs(CHAMPIONSHIP_BACKUP_DIR, exist_ok=True)
    slug = os.path.splitext(os.path.basename(path))[0]
    dest = os.path.join(CHAMPIONSHIP_BACKUP_DIR, f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}.json")
    shutil.copy2(path, dest)
    pattern = re.compile(rf"{re.escape(slug)}_\d{{8}}_\d{{6}}\.json")
    old = sorted(f for f in os.listdir(CHAMPIONSHIP_BACKUP_DIR) if pattern.fullmatch(f))
    for f in old[:-CHAMPIONSHIP_BACKUPS_KEPT]:
        os.remove(os.path.join(CHAMPIONSHIP_BACKUP_DIR, f))
    return dest


def championship_snapshot(data):
    """The part of a config a championship stores, normalised so a race config and a saved file compare equal."""
    out = deep_merge(DEFAULT_CONFIG, {k: v for k, v in data.items() if k not in PROJECT_ONLY_KEYS})
    out.pop('sync', None)
    out.pop(PRESET_KEY, None)
    for k in RACE_ONLY_CHAMPIONSHIP_KEYS:
        out['championship'].pop(k, None)
    out['layout']['leaderboard'].pop('rows', None)
    out['drivers'] = copy.deepcopy(data.get('drivers', {}))
    return out


def save_championship(name, cfg):
    """Stores everything but the race-specific sync as a named championship (backing up the old file)."""
    data = {k: v for k, v in cfg.items() if k not in PROJECT_ONLY_KEYS}
    data[PRESET_KEY] = name
    # The field size changes every race, so a championship always stores rows = 0 (one per driver)
    data['layout'] = copy.deepcopy(data.get('layout', {}))
    data['layout'].setdefault('leaderboard', {})['rows'] = 0
    os.makedirs(CHAMPIONSHIP_DIR, exist_ok=True)
    backup_championship(name)
    path = championship_file(name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def load_championship(name):
    path = championship_file(name)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def apply_championship(cfg, champ):
    """New config from a saved championship; keeps this race's sync and uses only that championship's drivers."""
    data = {k: v for k, v in champ.items() if k not in PROJECT_ONLY_KEYS}
    out = deep_merge(DEFAULT_CONFIG, data)
    out['sync'] = copy.deepcopy(cfg['sync'])
    # Round and event title belong to this race, not the championship
    for k in RACE_ONLY_CHAMPIONSHIP_KEYS:
        out['championship'][k] = cfg['championship'].get(k, '')
    out['layout']['leaderboard']['rows'] = cfg['layout']['leaderboard'].get('rows') or 0
    out['drivers'] = copy.deepcopy(data.get('drivers', {}))
    return out


# What a new championship keeps from the one before: where things sit on the video and which graphics run (they
# belong to the sim and the broadcast), and the AI commentators. Branding, colours, sponsors and drivers start over.
NEW_CHAMPIONSHIP_KEEPS = ('base_resolution', 'layout', 'auto_graphics', 'popup_types', 'popup_density',
                          'popup_duration', 'render_fps', 'commentators', 'commentator_photo',
                          'commentator_disclaimer')


def new_championship(cfg, name):
    """A clean config for a new championship: the built-in branding, theme and sponsors and no drivers (add
    this race's with ensure_drivers), keeping only NEW_CHAMPIONSHIP_KEEPS and this race's sync."""
    out = copy.deepcopy(DEFAULT_CONFIG)
    for k in NEW_CHAMPIONSHIP_KEEPS:
        if k in cfg:
            out[k] = copy.deepcopy(cfg[k])
    out['sync'] = copy.deepcopy(cfg['sync'])
    # This race's round, event title and the event's flag
    for k in RACE_ONLY_CHAMPIONSHIP_KEYS + ('event_flag',):
        out['championship'][k] = cfg['championship'].get(k, '')
    out['championship']['name'] = name
    out['drivers'] = {}
    out[PRESET_KEY] = name
    return out


def delete_championship(name):
    if backup_championship(name):
        os.remove(championship_file(name))


def load_config(project_path):
    """Built-in defaults <- league defaults (Graphics/graphics_defaults.json) <- this project's file, then the
    championship the race page chose for this race (racefiles.race_championship): that championship's setup
    when the project isn't on it yet, or a blank slate for an invitational."""
    path = os.path.join(project_path, CONFIG_NAME)
    data = {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    defaults = {k: v for k, v in load_defaults().items() if k not in PROJECT_ONLY_KEYS}
    cfg = deep_merge(deep_merge(DEFAULT_CONFIG, defaults), data)
    chosen = racefiles.race_championship(project_path)
    if data.get('drivers') is not None:
        if chosen == racefiles.INVITATIONAL or (
                data.get(PRESET_KEY) and data.get(PRESET_KEY) != defaults.get(PRESET_KEY)):
            # An invitational, or set up from another championship than the defaults: only its own drivers, so
            # a save never picks up another league's roster (the Mini drivers in Super Touring)
            cfg['drivers'] = copy.deepcopy(data['drivers'])
        else:
            # Project entries win, but drivers only known from earlier races are still available
            cfg['drivers'] = {**defaults.get('drivers', {}), **data['drivers']}
    if chosen == racefiles.INVITATIONAL:
        if not data or data.get(PRESET_KEY):
            # Blank slate: the layout and commentators stay, no championship's branding, sponsors or drivers
            cfg = new_championship(cfg, '')
            cfg.pop(PRESET_KEY, None)
    elif chosen and data.get(PRESET_KEY) != chosen:
        champ = load_championship(chosen)
        if champ is not None:
            cfg = apply_championship(cfg, champ)
            cfg[PRESET_KEY] = chosen
    return cfg


def save_config(project_path, cfg):
    path = os.path.join(project_path, CONFIG_NAME)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return path


def load_plan(project_path):
    path = os.path.join(project_path, PLAN_NAME)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'graphics': []}


def build_race(project_path, cfg):
    files = project_files(project_path)
    offset = cfg['sync'].get('offset_sec')
    if cfg['sync'].get('auto', True) or offset is None:
        detected = race_data.detect_offset(files['raw_log'], files['video_timed'])
        offset = detected if detected is not None else (offset or 0.0)
    return race_data.RaceData(files['raw_log'], offset, files['director_notes'])


# ---------------------------------------------------------------- driver defaults
def list_images(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder) if f.lower().endswith(IMAGE_EXTS))


def match_car_image(log_name, files=None):
    """Matches '[RCV]Dom.Payne' to 'Dom Payne.dds', 'kf.MTG' to 'MTG.dds', etc."""
    files = files if files is not None else list_images(CAR_PREVIEW_DIR)
    key = race_data.norm_key(log_name)
    if not key:
        return None
    best, best_len = None, 0
    for f in files:
        fk = race_data.norm_key(os.path.splitext(f)[0])
        if len(fk) < 3:
            continue
        if fk == key:
            return f
        if (fk in key or key in fk) and len(fk) > best_len:
            best, best_len = f, len(fk)
    return best


def match_grid_car(names, prefer=(), files=None):
    """The car preview for a driver known by any of `names` (log name, display name, short name). When the
    car is known (`prefer`, e.g. 'mini' or 'BMW'), only a file that also names it counts ('Thommo_Mini.dds'):
    a preview named after the driver alone ('Daniel Born.dds') could be their car from any series, so it is
    left for the Drivers tab to pick. Then an exact name wins, then the longest match."""
    files = files if files is not None else list_images(CAR_PREVIEW_DIR)
    keys = {k for k in (race_data.norm_key(n) for n in names if n) if len(k) >= 4}
    # Words of the names ('GrizzlyCliff' -> grizzly, 'muzzer82' -> muzzer, 'kf.MTG' -> mtg) only count at the
    # start of a file name ('Grizzly_Mini', 'MTG_Mini')
    words = set()
    for n in names:
        for w in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])', race_data.strip_tags(n or '')):
            if len(w) >= 3:
                words.add(w.lower())
    prefer = [p for p in (race_data.norm_key(w) for w in prefer if w) if len(p) >= 3]
    # A driver without a preview named after this car gets no car rather than their car from another series
    if prefer:
        files = [f for f in files if any(p in race_data.norm_key(f) for p in prefer)]
    best, best_score = None, 0
    for f in files:
        fk = race_data.norm_key(os.path.splitext(f)[0])
        if len(fk) < 3:
            continue
        hits = [k for k in keys if k == fk or k in fk or fk in k] + [w for w in words if fk.startswith(w)]
        if not hits:
            continue
        score = (100 if any(p in fk for p in prefer) else 0) + (50 if fk in keys else 0) + max(map(len, hits))
        if score > best_score:
            best, best_score = f, score
    return best


def tidy_name(name):
    name = race_data.strip_tags(name)
    if '.' in name and ' ' not in name:
        parts = [p for p in name.split('.') if p]
        if len(parts) == 2 and all(len(p) > 2 for p in parts):
            name = ' '.join(parts)
    words = [w.capitalize() if w.islower() and ' ' in name else w for w in name.split()]
    return ' '.join(words) or name


def default_driver(log_name, files=None, make=''):
    """A new driver. With the make they raced (RaceRoom's entry list) the car image is only a preview named
    after that make ('Thommo_Mini.dds'), never one from another series."""
    files = files if files is not None else list_images(CAR_PREVIEW_DIR)
    image = match_car_image(log_name, files)
    car = match_grid_car((log_name,), (make,), files) if make else image
    display = tidy_name(os.path.splitext(image)[0]) if image else tidy_name(log_name)
    if len(display) < 3:
        display = tidy_name(log_name)
    short = display.split()[-1].upper() if display.split() else display.upper()
    return {
        'display': display,
        'short': short[:14],
        'number': '',
        'country': '',
        'team': '',
        'make': '',
        'color': '',
        'car_image': f"Graphics/CarPreviews/{car}" if car else '',
        'logo': '',
    }


def _under(path, folder):
    return os.path.normcase(os.path.abspath(resolve_path(path))).startswith(os.path.normcase(folder))


def car_image_is_car(d):
    """Whether a driver's car image is a car. It is, from wherever it was picked (Graphics/CarPreviews, or a
    sim's livery folder: their preview renders are .dds), unless it is in Graphics/Logos. Any other image
    counts as a car once the driver has a Logo of their own (before the Logo field, logos went here)."""
    img = d.get('car_image') or ''
    if not img or _under(img, LOGO_DIR):
        return False
    if _under(img, CAR_PREVIEW_DIR) or img.lower().endswith('.dds'):
        return True
    return bool(d.get('logo'))


def move_logos_out_of_car_image(drivers):
    """Before the Logo field existed, team logos were set as the car image. Moves such a logo into the empty
    Logo field, so the car image only ever holds a car. Returns how many moved."""
    moved = 0
    for d in drivers.values():
        img = d.get('car_image') or ''
        if img and not car_image_is_car(d) and not d.get('logo'):
            d['logo'], d['car_image'] = img, ''
            moved += 1
    return moved


def same_make(a, b):
    """'MINI' and 'Mini', 'Alfa' and 'Alfa Romeo' are the same make."""
    a, b = race_data.norm_key(a or ''), race_data.norm_key(b or '')
    return bool(a and b and (a == b or a in b or b in a))


def reconcile_car(d, entry):
    """When the race log shows a driver in another make than the saved one (a roster copied from another
    championship, e.g. a Mini livery in a BMW race), the team, number, car image and logo described that
    other car: they make way for what the log recorded. Returns True when the driver changed."""
    mfr = str((entry or {}).get('mfr') or '').strip()
    if not mfr or not d.get('make') or same_make(d['make'], mfr):
        return False
    d['make'] = mfr
    d['team'] = str(entry.get('team') or '')
    d['number'] = str(entry.get('num') or '')
    d['car_image'] = ''
    d['logo'] = ''
    d.pop('no_car', None)
    return True


def recorded_entries(project_path):
    """{log name: {'num', 'car', 'class', 'team', 'mfr'}} from the race's live timing file (RaceRoom), or {}."""
    from .timing import timing_path_for
    path = timing_path_for(racefiles.race_file(project_path, 'race_log'))
    entries = {}
    if not path or not os.path.exists(path):
        return entries
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if '"entry"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get('type') == 'entry' and rec.get('name'):
                entries[rec['name']] = rec
    return entries


def ensure_drivers(cfg, roster, entries=None):
    """Adds every log name to cfg['drivers']. entries (RaceRoom's recorded entry list) fills in a
    blank team, car number and make, and replaces those of a driver saved in another make."""
    move_logos_out_of_car_image(cfg['drivers'])
    files = list_images(CAR_PREVIEW_DIR)
    for name in roster:
        entry = (entries or {}).get(name)
        make = str((entry or {}).get('mfr') or '')
        if name not in cfg['drivers']:
            cfg['drivers'][name] = default_driver(name, files, make)
        else:
            cfg['drivers'][name] = {**default_driver(name, files, make), **cfg['drivers'][name]}
        if entry:
            d = cfg['drivers'][name]
            if reconcile_car(d, entry):
                print(f"  {name} raced a {d['make']} here: dropped the team, number, car and logo saved for "
                      "another make")
            for key, src in (('team', 'team'), ('number', 'num'), ('make', 'mfr')):
                if not d.get(key) and entry.get(src):
                    d[key] = str(entry[src])
    return cfg


# ---------------------------------------------------------------- logos by team or make
def logo_files():
    """Every image under Graphics/Logos (subfolders too), as paths relative to the app."""
    out = []
    for root, _dirs, files in os.walk(LOGO_DIR):
        for f in sorted(files):
            if f.lower().endswith(IMAGE_EXTS):
                out.append(relative_path(os.path.join(root, f)))
    return out


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]+', (text or '').lower()) if len(w) >= 3}


def match_logo(d, entry=None, files=None):
    """The logo in Graphics/Logos for a driver: one named after their team ('Caterham' -> caterham.png), else
    after their make ('BMW' -> bmw.png, 'Volvo' -> volvo.png). Variants ('bmw_white.png') count when there is
    no exact file. Returns a relative path or ''."""
    files = files if files is not None else logo_files()
    entry = entry or {}
    stems = [(f, race_data.norm_key(os.path.splitext(os.path.basename(f))[0])) for f in files]
    team = d.get('team') or entry.get('team') or ''
    make = d.get('make') or entry.get('mfr') or ''
    team_key, team_words = race_data.norm_key(team), _words(team)
    make_key = race_data.norm_key(make)
    # Team first (longest name wins, so 'redbullracing' beats 'redbull'), then the make
    if team_key:
        hits = [(len(k), f) for f, k in stems if len(k) >= 4 and (k == team_key or k in team_words
                                                                    or (len(k) >= 5 and k in team_key))]
        if hits:
            return max(hits)[1]
    if make_key:
        exact = [f for f, k in stems if k == make_key]
        if exact:
            return exact[0]
        variants = sorted((f for f, k in stems if k.startswith(make_key) and len(make_key) >= 3), key=len)
        if variants:
            return variants[0]
    return ''


def driver_make(d, entry=None):
    return (d.get('make') or (entry or {}).get('mfr') or '').strip()


# Brand colours for drivers without a colour of their own (the timing tower chips and every driver graphic)
MAKE_COLORS = {
    'audi': '#BB0A30', 'bmw': '#1C69D4', 'porsche': '#F2F2F2', 'ferrari': '#DC0000', 'mercedes': '#00A19C',
    'mercedesamg': '#00A19C', 'nissan': '#C3002F', 'bentley': '#2A5C45', 'mclaren': '#FF8000',
    'lamborghini': '#D4A017', 'aston': '#00665E', 'astonmartin': '#00665E', 'chevrolet': '#D1A000',
    'cadillac': '#C9A227', 'ford': '#1D3F8F', 'toyota': '#EB0A1E', 'honda': '#CC0000', 'hyundai': '#002C5F',
    'alpine': '#0055B8', 'renault': '#FFCC00', 'peugeot': '#9FD62B', 'volkswagen': '#001E50',
    'opel': '#F7D417', 'lexus': '#1A1A1A', 'mazda': '#9B1B30', 'ktm': '#FF6600', 'radical': '#1E9E3E',
    'cupra': '#B17457', 'lotus': '#00573F', 'alfaromeo': '#9B0E1E', 'corvette': '#D1A000', 'ruf': '#C8A951',
    'tatuus': '#D61F26', 'saleen': '#1E1E1E', 'mclarenautomotive': '#FF8000', 'callaway': '#16365C',
    'volvo': '#3E7CB1', 'mini': '#00843D', 'caterham': '#0B6E3F', 'alfa': '#9B0E1E', 'holden': '#C8102E',
    'subaru': '#0039A6', 'seat': '#E5541B', 'skoda': '#4BA82E', 'citroen': '#DA291C', 'kia': '#BB162B',
    'vauxhall': '#F7D417', 'mitsubishi': '#E60012', 'dodge': '#BA0C2F', 'chrysler': '#2B4C7E',
}


def make_color(make):
    """The brand colour for a make ('BMW' -> BMW blue), or ''."""
    mk = race_data.norm_key(make or '')
    if not mk:
        return ''
    if mk in MAKE_COLORS:
        return MAKE_COLORS[mk]
    for k, v in MAKE_COLORS.items():
        if mk.startswith(k) and len(k) > 3:
            return v
    return ''


def commentator_bio(cfg, name):
    return ((cfg.get('commentators') or {}).get(name) or {}).get('bio', '').strip()


def commentator_group_photo(cfg):
    """Path of the one picture on the AI commentators card (a cut-out PNG reads best), or ''."""
    return (cfg.get('commentator_photo') or '').strip()


def commentator_disclaimer(cfg):
    """The line under the AI commentators card ('' hides it)."""
    text = cfg.get('commentator_disclaimer')
    return ' '.join((DEFAULT_DISCLAIMER if text is None else text).split())


def flag_codes():
    """The country codes that have a flag in Graphics/Flags ('GBR', 'ESP', ...)."""
    return sorted({os.path.splitext(f)[0].upper() for f in list_images(FLAG_DIR)})


def set_driver_countries(project_path, countries):
    """Saves {log name: country code} (read off the game's own flags) where this race's drivers live: its
    graphics setup, and the championship the race page chose for it (backed up first) plus the graphics defaults
    when they are that championship. An invitational keeps them to itself. Never guesses a championship: raises
    ValueError when the race has none chosen. Returns {name: (old code, new code)} for the drivers changed."""
    chosen = racefiles.race_championship(project_path)
    if not chosen:
        raise ValueError("choose the race's championship first")
    countries = {n: c.upper() for n, c in countries.items() if c}
    if not countries:
        return {}
    changed = {}

    def apply(drivers):
        n = 0
        for name, code in countries.items():
            d = drivers.get(name)
            if d is None:
                d = drivers[name] = default_driver(name)
            if (d.get('country') or '').upper() != code:
                changed.setdefault(name, ((d.get('country') or '').upper(), code))
                d['country'] = code
                n += 1
        return n

    # This race's setup, as the graphics would load it (already on the chosen championship)
    cfg = load_config(project_path)
    if apply(cfg['drivers']):
        save_config(project_path, cfg)
    preset = None if chosen == racefiles.INVITATIONAL else chosen
    defaults = load_defaults()
    champ = load_championship(preset) if preset else None
    if champ is not None and apply(champ.setdefault('drivers', {})):
        backup_championship(preset)
        with open(championship_file(preset), 'w', encoding='utf-8') as f:
            json.dump(champ, f, indent=2, ensure_ascii=False)
    if defaults and defaults.get(PRESET_KEY) == preset and apply(defaults.setdefault('drivers', {})):
        with open(DEFAULTS_PATH, 'w', encoding='utf-8') as f:
            json.dump(defaults, f, indent=2, ensure_ascii=False)
    return changed


def driver_meta(cfg, name):
    d = cfg['drivers'].get(name)
    if d is None:
        d = default_driver(name)
    return d


def clean_track_name(track):
    return re.sub(r'\s+', ' ', track.replace('_', ' ')).strip()

