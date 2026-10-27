"""Full timing tower, drawn from recorded live timing (RaceRoom replays show no leaderboard).

WEC-style: logo and race clock on top, an accent title bar, one row per car (position, make chip,
car number, name and interval/gap), class bars in multi-class races and a flag/event footer.
The value column cycles through the configured modes, and cars that change places slide
past each other.
"""
import colorsys
import hashlib
import os

from PIL import Image

from . import config as gcfg
from .drawing import (add_shadow, ease_in, ease_out, ellipsize, fit_font, fit_image, font, h_gradient,
                      load_image, rgba, round_rect, text_vcenter, text_w)
from .race_data import norm_key
from .templates import FLAG_COLORS, contrast_text
from .timing import FINISH_FINISHED, row_value

SWAP_DUR = 0.8     # seconds for two rows to trade places
FLASH_DUR = 3.0    # a car that gained/lost places stays tinted this long
FLASH_STEPS = 10
HEADER_H = 92
TITLE_H = 28
FOOTER_H = 30
CLASS_H = 26
GAP = 3            # space between rows
MODE_LABELS = {'interval': 'INTERVAL', 'gap': 'LEADER', 'name': '', 'team': ''}
MAKE_DIR = os.path.join(gcfg.GRAPHICS_DIR, 'Manufacturers')
# Recognisable chip colours for common makes; anything else gets a stable colour from its name
MAKE_COLORS = {
    'audi': '#BB0A30', 'bmw': '#1C69D4', 'porsche': '#F2F2F2', 'ferrari': '#DC0000', 'mercedes': '#00A19C',
    'mercedesamg': '#00A19C', 'nissan': '#C3002F', 'bentley': '#2A5C45', 'mclaren': '#FF8000',
    'lamborghini': '#D4A017', 'aston': '#00665E', 'astonmartin': '#00665E', 'chevrolet': '#D1A000',
    'cadillac': '#C9A227', 'ford': '#1D3F8F', 'toyota': '#EB0A1E', 'honda': '#CC0000', 'hyundai': '#002C5F',
    'alpine': '#0055B8', 'renault': '#FFCC00', 'peugeot': '#9FD62B', 'volkswagen': '#001E50',
    'opel': '#F7D417', 'lexus': '#1A1A1A', 'mazda': '#9B1B30', 'ktm': '#FF6600', 'radical': '#1E9E3E',
    'cupra': '#B17457', 'lotus': '#00573F', 'alfaromeo': '#9B0E1E', 'corvette': '#D1A000', 'ruf': '#C8A951',
    'tatuus': '#D61F26', 'saleen': '#1E1E1E', 'mclarenautomotive': '#FF8000', 'callaway': '#16365C',
}
CLASS_COLORS = ['#E10600', '#19C463', '#2D7FF9', '#FFC400', '#A239FF', '#FF7A00']


def stable_color(text):
    h = int(hashlib.md5(text.encode('utf-8')).hexdigest()[:6], 16)
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, 0.42, 0.65)
    return '#%02X%02X%02X' % (int(r * 255), int(g * 255), int(b * 255))


def initials(make):
    words = [w for w in make.replace('-', ' ').split() if w]
    if len(words) > 1:
        return ''.join(w[0] for w in words[:3]).upper()
    return make[:3].upper()


class TimingTower:
    def __init__(self, tpl):
        self.tpl = tpl
        self.cfg = tpl.cfg
        self.race = tpl.race
        self.timing = self.race.timing
        self.th = tpl.th
        self.lay = self.cfg['layout']['tower']
        modes = [m.strip().lower() for m in str(self.lay.get('cycle') or 'interval').split(',')]
        # Gaps to the leader need recorded timing; the race log's standings only have intervals
        self.modes = [m for m in modes if m in MODE_LABELS and (m != 'gap' or self.timing.recorded)] or ['interval']
        self.cycle_sec = max(3.0, float(self.lay.get('cycle_sec') or 15))
        self._cache = {}
        self._make_logos = {}
        self._make_files = gcfg.list_images(MAKE_DIR)
        self.fps = 30.0
        self.geom = self._geometry()

    # ------------------------------------------------------------------ layout
    def _class_color(self, cls):
        custom = (self.lay.get('class_colors') or {}).get(cls)
        if custom:
            return rgba(custom)
        idx = self.timing.classes.index(cls) if cls in self.timing.classes else 0
        return rgba(CLASS_COLORS[idx % len(CLASS_COLORS)])

    def _visible(self, rows):
        """Rows to show, grouped by class when multi-class: [(class or None, [row, ...])]."""
        limit = int(self.lay.get('rows') or 0)
        if not self.timing.multiclass:
            return [(None, rows[:limit] if limit else rows)]
        groups = []
        for cls in self.timing.classes:
            members = [r for r in rows if self.timing.driver_class(r['name']) == cls]
            if members:
                groups.append((cls, members))
        if limit:
            per = max(1, limit // max(1, len(groups)))
            groups = [(c, m[:per]) for c, m in groups]
        return groups

    def _geometry(self):
        """Row height that fits every visible row above the tower's bottom limit."""
        names = self.timing.order_names() or self.race.drivers
        rows = [{'name': n} for n in names]
        groups = self._visible(rows)
        n = sum(len(m) for _, m in groups)
        bars = len(groups) if self.timing.multiclass else 0
        top = self.lay['y']
        avail = self.lay.get('bottom', 1000) - top - HEADER_H - TITLE_H - FOOTER_H - bars * (CLASS_H + GAP) - 8
        row_h = int(min(self.lay.get('row_h', 34), max(20, avail / max(1, n))))
        body_h = n * row_h + bars * (CLASS_H + GAP)
        return {'row_h': row_h, 'body_h': body_h, 'h': HEADER_H + TITLE_H + 6 + body_h + 4 + FOOTER_H}

    def mode_at(self, t):
        return int(max(0.0, t) // self.cycle_sec) % len(self.modes)

    # ------------------------------------------------------------------ animation state
    def _swap(self, idx, t):
        """(previous state index, progress 0..1) while an order change plays out, else (None, 1)."""
        st = self.timing.state(idx)
        prev = self.timing.state(idx - 1)
        if not st or not prev:
            return None, 1.0
        if [r['name'] for r in st['rows']] == [r['name'] for r in prev['rows']]:
            return None, 1.0
        local = self.race.to_log(t) - st['t']
        if local >= max(SWAP_DUR, FLASH_DUR):
            return None, 1.0
        return idx - 1, local

    def _flash_source(self, idx, t):
        """Most recent order change still flashing: (previous index, seconds since)."""
        for back in range(0, 4):
            i = idx - back
            prev_i, local = self._swap(i, t)
            if prev_i is not None:
                return prev_i, i, local
        return None, idx, None

    def key(self, t):
        idx = self.timing.index_at(t)
        prev_i, cur_i, local = self._flash_source(idx, t)
        anim = flash = None
        if prev_i is not None:
            steps = max(1, int(SWAP_DUR * self.fps))
            anim = min(steps, int(local / SWAP_DUR * steps))
            flash = min(FLASH_STEPS, int(local / FLASH_DUR * FLASH_STEPS))
        return (idx, self.mode_at(t), prev_i, anim, flash, self.timing.clock_text(idx, t),
                self.race.flag_state_at(t))

    # ------------------------------------------------------------------ drawing
    def draw(self, frame, t):
        k = self.key(t)
        idx, mode_i, prev_i, anim, flash, clock, flag = k
        body_key = k[:5] + (flag,)
        if body_key not in self._cache:
            if len(self._cache) > 400:
                self._cache.clear()
            steps = max(1, int(SWAP_DUR * self.fps))
            p_move = 1.0 if anim is None else anim / steps
            p_flash = None if flash is None else flash / FLASH_STEPS
            self._cache[body_key] = add_shadow(self._body(idx, self.modes[mode_i], prev_i, p_move, p_flash, flag))
        img, margin = self._cache[body_key]
        x, y = self.lay['x'], self.lay['y']
        frame.alpha_composite(img, (int(x - margin), int(y - margin)))
        # Clock under the logo, drawn per frame (it ticks every second)
        f = font(30, 'bold')
        text_vcenter(frame, x + self.lay['w'] / 2, y + HEADER_H - 22, clock, f, self.th.text, 'm')

    def _rows_for(self, idx):
        st = self.timing.state(idx)
        if st:
            return st['rows']
        return [{'name': n, 'pos': i + 1, 'pos_class': i + 1} for i, n in enumerate(self.race.grid)]

    def _slots(self, rows):
        """name -> y of the row (relative to the body top), plus [(class, y)] for the class bars."""
        groups = self._visible(rows)
        rh = self.geom['row_h']
        y = 0
        slots, bars = {}, []
        for cls, members in groups:
            if cls is not None:
                bars.append((cls, y))
                y += CLASS_H + GAP
            for r in members:
                slots[r['name']] = y
                y += rh
        return slots, bars, groups

    def _body(self, idx, mode, prev_i, p_move, p_flash, flag):
        th, lay = self.th, self.lay
        w = lay['w']
        geom = self.geom
        img = Image.new('RGBA', (w, geom['h']), (0, 0, 0, 0))
        self._header(img, w)
        body_top = HEADER_H + TITLE_H + 6
        rows = self._rows_for(idx)
        slots, bars, groups = self._slots(rows)
        prev_slots, prev_pos = {}, {}
        if prev_i is not None:
            prev_rows = self._rows_for(prev_i)
            prev_slots = self._slots(prev_rows)[0]
            prev_pos = {r['name']: r['pos'] for r in prev_rows}
        e = ease_out(p_move) if p_move < 1 else 1.0
        # Panel behind the rows
        round_rect(img, (0, body_top - 3, w, body_top + geom['body_h'] + 3), 0, th.panel)
        for cls, by in bars:
            self._class_bar(img, body_top + by, w, cls)
        values = self._values(groups, mode)
        drawn = []
        for _, members in groups:
            for r in members:
                name = r['name']
                y_new = slots[name]
                y_old = prev_slots.get(name, y_new)
                y = y_old + (y_new - y_old) * e
                change = 0
                if p_flash is not None and name in prev_pos:
                    change = prev_pos[name] - r['pos']
                drawn.append((change, y, r))
        # Cars moving up are drawn last so they slide over the cars they pass
        for change, y, r in sorted(drawn, key=lambda d: d[0]):
            self._row(img, body_top + y, w, r, mode, values.get(r['name']), change, p_flash)
        self._footer(img, w, geom['h'] - FOOTER_H, flag)
        return img

    def _values(self, groups, mode):
        """name -> value text for the interval/gap modes, measured within each class."""
        out = {}
        if mode not in ('interval', 'gap'):
            return out
        for _, members in groups:
            lead = members[0] if members else None
            prev = None
            for r in members:
                if r is lead:
                    out[r['name']] = None
                    prev = r
                    continue
                if mode == 'gap':
                    secs = _diff(r.get('gap'), lead.get('gap'))
                    down = (r.get('laps_down') or 0) - (lead.get('laps_down') or 0)
                else:
                    secs = r.get('interval') if not self.timing.multiclass else _diff(r.get('gap'), prev.get('gap'))
                    down = (r.get('laps_down') or 0) - (prev.get('laps_down') or 0) if secs is None else 0
                out[r['name']] = row_value(r, secs, max(0, down))
                prev = r
        return out

    def _header(self, img, w):
        th = self.th
        tpl = self.tpl
        round_rect(img, (0, 0, w, HEADER_H), 8, th.panel_solid, corners=(True, True, False, False))
        if tpl.logo:
            lg = fit_image(tpl.logo, w - 60, 40)
            img.alpha_composite(lg, (int(w / 2 - lg.width / 2), int(30 - lg.height / 2)))
        else:
            champ = self.cfg['championship']
            title = (champ.get('short_name') or champ.get('name') or 'LIVE TIMING').upper()
            text_vcenter(img, w / 2, 28, title, fit_font(title, w - 40, 30, 'bold'), th.text, 'm')
        # Title bar
        y = HEADER_H
        darker = tuple(int(c * 0.62) for c in th.accent[:3]) + (255,)
        img.alpha_composite(h_gradient(w, TITLE_H, th.accent, darker), (0, y))
        label = 'RACE'
        if len(self.timing.classes) == 1 and self.timing.classes[0] and self.timing.recorded:
            label = self.timing.classes[0].upper()
        f = fit_font(label, w - 40, 20, 'bold_cond')
        text_vcenter(img, w / 2, y + TITLE_H / 2 + 1, label, f, (255, 255, 255, 255), 'm')

    def _class_bar(self, img, y, w, cls):
        col = self._class_color(cls)
        darker = tuple(int(c * 0.6) for c in col[:3]) + (255,)
        img.alpha_composite(h_gradient(w - 8, CLASS_H, col, darker), (4, int(y)))
        label = (cls or 'CLASS').upper()
        f = fit_font(label, w - 30, 18, 'bold_cond')
        text_vcenter(img, w / 2, y + CLASS_H / 2 + 1, label, f, contrast_text(col), 'm')

    def _make(self, name):
        meta = self.tpl.meta(name)
        return meta.get('make') or self.timing.entry(name).get('mfr') or ''

    def _make_logo(self, make, size):
        key = (norm_key(make), size)
        if key not in self._make_logos:
            logo = None
            mk = norm_key(make)
            for f in self._make_files:
                if mk and norm_key(os.path.splitext(f)[0]) == mk:
                    logo = fit_image(load_image(os.path.join(MAKE_DIR, f)), size - 6, size - 6)
                    break
            self._make_logos[key] = logo
        return self._make_logos[key]

    def _chip_color(self, name, make):
        c = self.tpl.meta(name).get('color')
        if c:
            return rgba(c)
        mk = norm_key(make)
        for k, v in MAKE_COLORS.items():
            if mk == k or (mk.startswith(k) and len(k) > 3):
                return rgba(v)
        return rgba(stable_color(make or name))

    def _row(self, img, y, w, r, mode, value, change, p_flash):
        th = self.th
        rh = self.geom['row_h']
        h = rh - GAP
        y = int(round(y))
        name = r['name']
        meta = self.tpl.meta(name)
        # Row background, tinted for a car that just gained (green) or lost (red) places
        bg = th.panel_hi
        if change and p_flash is not None:
            tint = th.green if change > 0 else th.red
            a = (1 - ease_in(p_flash)) * 0.55
            bg = tuple(int(bg[i] * (1 - a) + tint[i] * a) for i in range(3)) + (bg[3],)
        round_rect(img, (4, y, w - 4, y + h), 3, bg)
        cy = y + h / 2
        fs = max(12, int(h * 0.62))
        x = 4
        # Position
        pos = r.get('pos_class') if self.timing.multiclass else r.get('pos')
        pos_w = int(h * 1.25)
        text_vcenter(img, x + pos_w / 2, cy + 1, str(pos or ''), font(fs, 'bold'), th.text, 'm')
        x += pos_w
        # Make chip (logo from Graphics/Manufacturers, else coloured initials)
        make = self._make(name)
        chip = self._chip_color(name, make)
        round_rect(img, (x, y, x + h, y + h), 2, chip)
        logo = self._make_logo(make, h) if make else None
        if logo:
            img.alpha_composite(logo, (int(x + h / 2 - logo.width / 2), int(cy - logo.height / 2)))
        elif make:
            ini = initials(make)
            text_vcenter(img, x + h / 2, cy + 1, ini, fit_font(ini, h - 4, int(h * 0.42), 'bold_cond', 8),
                         contrast_text(chip), 'm')
        x += h + 6
        # Car number
        num = str(meta.get('number') or self.timing.entry(name).get('num') or '')
        num_w = int(h * 1.35)
        if num:
            text_vcenter(img, x + num_w / 2, cy + 1, num, fit_font(num, num_w - 2, fs - 2, 'bold_cond', 9),
                         th.text, 'm')
        x += num_w
        round_rect(img, (x, y + 4, x + 2, y + h - 4), 0, (255, 255, 255, 110))
        x += 9
        # Name / team, and the value box
        val_w = int(w * 0.27) if mode in ('interval', 'gap') else 0
        text_right = w - 4 - val_w - 6
        if mode == 'team':
            label = meta.get('team') or self.timing.entry(name).get('team') or make or meta.get('display', name)
        elif mode == 'name':
            label = meta.get('display', name).upper()
        else:
            label = meta.get('short') or name
        f = font(fs - 1, 'bold_cond')
        text_vcenter(img, x, cy + 1, ellipsize(label.upper(), f, text_right - x), f, th.text)
        finished = r.get('finish') == FINISH_FINISHED
        if val_w:
            vx = w - 4 - val_w
            round_rect(img, (vx, y, w - 4, y + h), 2, (255, 255, 255, 34))
            if value is None:
                value = MODE_LABELS.get(mode, '') if (r.get('pos_class') if self.timing.multiclass
                                                      else r.get('pos')) == 1 else ''
            col = th.text
            if value in ('PIT', 'DNF', 'DSQ', 'DNS'):
                col = th.accent2 if value == 'PIT' else th.red
            fv = fit_font(value, val_w - 14, fs - 3, 'semibold_cond', 9)
            text_vcenter(img, w - 12, cy + 1, value, fv, col, 'r')
        if finished:
            self.tpl._checker(img, text_right - 16, int(cy - 6), 14, 12, 3)

    def _footer(self, img, w, y, flag_state):
        th = self.th
        round_rect(img, (0, y, w, y + FOOTER_H), 8, th.panel_solid, corners=(False, False, True, True))
        state = flag_state or 'pre-race'
        col = rgba(FLAG_COLORS.get(state, '#9AA3B2'))
        stripe = col if state not in ('chequered', 'pre-race') else th.accent
        round_rect(img, (0, y, w, y + 3), 0, stripe)
        if state == 'chequered':
            self.tpl._checker(img, 12, y + 10, 22, 14, 4)
        else:
            round_rect(img, (12, y + 10, 34, y + 24), 3, col)
        label = {'pre-race': 'FORMATION', 'green': 'GREEN', 'yellow': 'YELLOW', 'double yellow': 'YELLOW',
                 'chequered': 'FINISH'}.get(state, state.upper())
        f = font(16, 'bold_cond')
        text_vcenter(img, 42, y + 17, label, f, col if state not in ('chequered', 'pre-race') else th.text)
        champ = self.cfg['championship']
        event = (champ.get('event_name') or gcfg.clean_track_name(self.race.track)).upper()
        fe = font(16, 'semibold_cond')
        lx = 42 + text_w(f, label) + 14
        text_vcenter(img, w - 12, y + 17, ellipsize(event, fe, w - lx - 12), fe, th.muted, 'r')


def _diff(a, b):
    if a is None:
        return None
    return round(a - (b or 0.0), 3)
