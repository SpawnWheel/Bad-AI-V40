"""Per-project graphics configuration (graphics_config.json) and project helpers."""
import copy
import json
import os
import re

from . import race_data

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPHICS_DIR = os.path.join(APP_ROOT, 'Graphics')
CAR_PREVIEW_DIR = os.path.join(GRAPHICS_DIR, 'CarPreviews')
LOGO_DIR = os.path.join(GRAPHICS_DIR, 'Logos')
FLAG_DIR = os.path.join(GRAPHICS_DIR, 'Flags')
IMAGE_EXTS = ('.png', '.dds', '.jpg', '.jpeg', '.webp', '.bmp', '.tga')

CONFIG_NAME = 'graphics_config.json'
# Branding, layout and driver links shared by every new project (one league, many races)
DEFAULTS_PATH = os.path.join(GRAPHICS_DIR, 'graphics_defaults.json')
# Race-specific settings that are never carried over to other projects
PROJECT_ONLY_KEYS = ('sync',)
PLAN_NAME = 'graphics_plan.json'

# Popup types the Graphic Creator can schedule (label shown in the setup page)
POPUP_TYPES = {
    'driver_card': 'Driver ID card (car preview, position, team)',
    'battle': 'Head-to-head battle',
    'overtake': 'Position change',
    'race_control': 'Race Control message',
    'stat': 'Data stat card',
    'movers': 'Positions gained since start',
    'headline': 'Paddock news / storyline',
}

# Graphics the renderer adds on its own from the race data
AUTO_TYPES = {
    'title_card': 'Opening title card',
    'commentators': 'Commentator names',
    'start_ticker': 'Starting grid ticker at the green flag',
    'standings_ticker': 'Standings ticker on every leaderboard update',
    'start_movers': 'Biggest gainer and loser a minute after the start',
    'fastest_lap': 'Fastest lap of the race (from lap 5)',
    'pit_stops': 'Pit stop pop-up when a car pits (cars pitting together share one)',
    'radio': 'Team radio quotes (middle right)',
    'chequered': 'Chequered flag banner',
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
        'logo': {'enabled': True, 'x': 1903, 'y': 14, 'h': 58, 'anchor': 'right'},
        'extra_logos': [],
        'ticker': {'enabled': True, 'y': 1018, 'h': 46, 'speed': 150, 'max_duration': 40,
                   'extra_hold': 10},
        'popups': {'x': 40, 'bottom': 1000, 'max_w': 780},
        # Team radio quotes, F1 style: anchored by their right edge and vertical centre
        'radio': {'right': 1880, 'cy': 640, 'w': 400},
    },
    'auto_graphics': {k: True for k in AUTO_TYPES},
    'popup_types': {k: True for k in POPUP_TYPES},
    'popup_density': 'medium',
    'popup_duration': 7.0,
    'drivers': {},
}

DENSITY_GAP = {'low': 75, 'medium': 45, 'high': 28}


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
    """Resolves the pipeline files the graphics need from project_state.json."""
    st = load_project_state(project_path)

    def existing(key):
        p = st.get(key)
        return p if p and os.path.exists(p) else None

    return {
        'raw_log': existing('raw_log'),
        'director_notes': existing('director_notes') or latest_file(project_path, 'director_notes'),
        'video_file': existing('video_file'),
        'final_audio': existing('final_audio'),
        'commentary_text': existing('commentary_text'),
        # Video-timed logs used to detect the sync offset
        'video_timed': [p for p in (existing('refined_log'), existing('post_filtered_log'),
                                    existing('calc_log'), existing('commentary_text')) if p],
        'project_notes': os.path.join(project_path, 'project_notes.txt'),
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
    # Keep drivers from earlier races too, so the whole league builds up over time
    drivers = dict(load_defaults().get('drivers', {}))
    drivers.update(cfg.get('drivers', {}))
    data['drivers'] = drivers
    os.makedirs(GRAPHICS_DIR, exist_ok=True)
    with open(DEFAULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return DEFAULTS_PATH


def load_config(project_path):
    """Built-in defaults <- league defaults (Graphics/graphics_defaults.json) <- this project's file."""
    path = os.path.join(project_path, CONFIG_NAME)
    data = {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    defaults = {k: v for k, v in load_defaults().items() if k not in PROJECT_ONLY_KEYS}
    cfg = deep_merge(deep_merge(DEFAULT_CONFIG, defaults), data)
    if data.get('drivers') is not None:
        # Project entries win, but drivers only known from earlier races are still available
        cfg['drivers'] = {**defaults.get('drivers', {}), **data['drivers']}
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


def tidy_name(name):
    name = race_data.strip_tags(name)
    if '.' in name and ' ' not in name:
        parts = [p for p in name.split('.') if p]
        if len(parts) == 2 and all(len(p) > 2 for p in parts):
            name = ' '.join(parts)
    words = [w.capitalize() if w.islower() and ' ' in name else w for w in name.split()]
    return ' '.join(words) or name


def default_driver(log_name, files=None):
    image = match_car_image(log_name, files)
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
        'color': '',
        'car_image': f"Graphics/CarPreviews/{image}" if image else '',
    }


def ensure_drivers(cfg, roster):
    files = list_images(CAR_PREVIEW_DIR)
    for name in roster:
        if name not in cfg['drivers']:
            cfg['drivers'][name] = default_driver(name, files)
        else:
            cfg['drivers'][name] = {**default_driver(name, files), **cfg['drivers'][name]}
    return cfg


def driver_meta(cfg, name):
    d = cfg['drivers'].get(name)
    if d is None:
        d = default_driver(name)
    return d


def clean_track_name(track):
    return re.sub(r'\s+', ' ', track.replace('_', ' ')).strip()
