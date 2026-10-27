"""Broadcast graphic templates, drawn with Pillow in 1920x1080 space."""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import config as gcfg
from .drawing import (add_shadow, ellipsize, fit_font, fit_image, font, h_gradient, load_image, logo_outline, polygon,
                      rgba, round_outline, round_rect, text_vcenter, text_w, wrap)
from .race_data import fmt_clock

LABEL_H = 30
MAX_PASSED = 4  # most passed drivers one position-change card stacks
FASTEST_PURPLE = '#A239FF'
PODIUM = {1: '#E8B923', 2: '#C3CBD6', 3: '#C9844A'}
FLAG_COLORS = {
    'green': '#19C463', 'yellow': '#FFD200', 'double yellow': '#FFD200',
    'chequered': '#FFFFFF', 'red': '#E3242B', 'blue': '#2D7FF9', 'white': '#FFFFFF',
}


class Theme:
    def __init__(self, cfg):
        t = cfg['theme']
        self.accent = rgba(t['accent'])
        self.accent2 = rgba(t['accent2'])
        self.panel = rgba(t['panel'], t.get('panel_alpha', 0.9))
        self.panel_solid = rgba(t['panel'])
        self.panel_hi = rgba('#1B2130', t.get('panel_alpha', 0.9))
        self.text = rgba(t['text'])
        self.muted = rgba(t['muted'])
        self.dark_text = rgba('#0B0E14')
        self.border = (255, 255, 255, 70)
        self.green = rgba('#19C463')
        self.red = rgba('#FF3B3B')


def contrast_text(color):
    r, g, b = color[:3]
    return (11, 14, 20, 255) if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else (255, 255, 255, 255)


class Templates:
    TICKER_GAP = 120  # space between the end of the standings and their repeat

    def __init__(self, cfg, race):
        self.cfg = cfg
        self.race = race
        self.th = Theme(cfg)
        champ = cfg['championship']
        self.logo = load_image(gcfg.resolve_path(champ.get('logo')), bool(champ.get('logo_tint_white')))
        self.tower = None
        # RaceRoom replays show no leaderboard, so it always gets the tower; AMS2 can wrap its own instead
        if cfg['layout']['tower'].get('enabled', True) or not race.has_hud:
            from .tower import TimingTower  # tower imports this module
            self.tower = TimingTower(self)

    # ------------------------------------------------------------------ helpers
    def meta(self, name):
        return gcfg.driver_meta(self.cfg, name)

    def driver_color(self, name):
        c = self.meta(name).get('color')
        return rgba(c) if c else self.th.accent

    def _image_path(self, name):
        return gcfg.resolve_path(self.meta(name).get('car_image')) if name else ''

    @staticmethod
    def _is_car_preview(path):
        return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(gcfg.CAR_PREVIEW_DIR))

    def car(self, name, max_w, max_h):
        """Car preview render (Graphics/CarPreviews), which may stand out of its card; None for logos."""
        path = self._image_path(name)
        if not path or not self._is_car_preview(path):
            return None
        return fit_image(load_image(path), max_w, max_h)

    def logo_path(self, name):
        """The driver's team/make logo (any Drivers tab image that is not a car preview), or ''."""
        path = self._image_path(name)
        return path if path and not self._is_car_preview(path) and os.path.exists(path) else ''

    def badge(self, name, h):
        """Team/make logo with a white outline, sized to sit inside a card (never over its edge).
        No tile or colour strip behind it."""
        path = self.logo_path(name)
        logo = load_image(path) if path else None
        if logo is None:
            return None
        h = int(h)
        return logo_outline(fit_image(logo, int(h * 1.5) - 4, h - 4), 2)

    def card_badge(self, name, panel_h, max_h=88):
        """Badge for the right-hand end of a card, or None."""
        return self.badge(name, min(max_h, panel_h - 24)) if name else None

    @staticmethod
    def place_badge(img, badge, top, panel_h, right_pad=12):
        """Puts a badge at the right-hand end of the panel, centred vertically."""
        if badge is not None:
            img.alpha_composite(badge, (int(img.width - badge.width - right_pad),
                                        int(top + (panel_h - badge.height) / 2)))

    @staticmethod
    def badge_w(badge):
        return badge.width + 18 if badge is not None else 0

    def flag_img(self, country, h):
        if not country:
            return None
        for ext in ('.png', '.jpg'):
            im = load_image(f"{gcfg.FLAG_DIR}/{country.upper()}{ext}", autocrop=False)
            if im:
                return fit_image(im, h * 2, h)
        return None

    def chip(self, img, x, cy, label, bg, fg=None, size=18, style='bold_cond', pad=8, h=26):
        f = font(size, style)
        w = int(text_w(f, label) + pad * 2)
        round_rect(img, (x, cy - h // 2, x + w, cy + h // 2), 4, bg)
        text_vcenter(img, x + w / 2, cy, label, f, fg or contrast_text(bg), 'm')
        return w

    def delta(self, img, cx, cy, delta, size, color=None, suffix=''):
        """Draws a triangle + number centred on cx (the font has no arrow glyphs)."""
        th = self.th
        col = color or (th.green if delta > 0 else th.red if delta < 0 else th.muted)
        f = font(size, 'bold')
        txt = (str(abs(delta)) if delta else '–') + suffix
        tri = size * 0.62 if delta else 0
        gap = size * 0.25 if delta else 0
        w = tri + gap + text_w(f, txt)
        x = cx - w / 2
        if delta > 0:
            polygon(img, [(x, cy + tri * 0.45), (x + tri, cy + tri * 0.45), (x + tri / 2, cy - tri * 0.45)], col)
        elif delta < 0:
            polygon(img, [(x, cy - tri * 0.45), (x + tri, cy - tri * 0.45), (x + tri / 2, cy + tri * 0.45)], col)
        text_vcenter(img, x + tri + gap, cy, txt, f, col)

    def label_tab(self, img, x, y, label, bg=None, fg=None):
        bg = bg or self.th.accent
        f = font(18, 'bold_cond')
        w = int(text_w(f, label) + 28)
        round_rect(img, (x, y, x + w, y + LABEL_H), 5, bg, corners=(True, True, False, False))
        text_vcenter(img, x + 14, y + LABEL_H / 2 + 1, label, f, fg or contrast_text(bg))
        return w

    def card_canvas(self, w, panel_h, label, top_extra=0, label_bg=None, label_fg=None, stripe=None):
        """Blank card: label tab on top, dark panel below. Returns (img, panel_top)."""
        top = max(LABEL_H, top_extra)
        img = Image.new('RGBA', (int(w), int(top + panel_h)), (0, 0, 0, 0))
        round_rect(img, (0, top, w, top + panel_h), 6, self.th.panel, corners=(False, True, True, True))
        if stripe:
            round_rect(img, (0, top, 6, top + panel_h), 0, stripe)
        if label:
            self.label_tab(img, 0, top - LABEL_H, label, label_bg, label_fg)
        return img, top

    def name_block(self, img, x, y, name, max_w, big=40, small=24):
        """First name light above an uppercase bold surname."""
        display = self.meta(name)['display']
        parts = display.split()
        first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', display)
        if first:
            f1 = fit_font(first, max_w, small, 'regular')
            ImageDraw.Draw(img).text((x, y), first, font=f1, fill=self.th.text)
            y += small + 2
        f2 = fit_font(last.upper(), max_w, big, 'bold')
        ImageDraw.Draw(img).text((x, y), last.upper(), font=f2, fill=self.th.text)
        return y + big

    def name_block_w(self, name, max_w, big=40, small=24):
        """Width name_block() takes for this driver."""
        parts = self.meta(name)['display'].split()
        first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', ' '.join(parts))
        w = text_w(fit_font(last.upper(), max_w, big, 'bold'), last.upper())
        if first:
            w = max(w, text_w(fit_font(first, max_w, small, 'regular'), first))
        return min(max_w, w)

    def meta_row_w(self, name, max_w):
        """Width meta_row() takes for this driver (the team is cut short past max_w)."""
        m = self.meta(name)
        w = 0
        if m.get('number'):
            w += text_w(font(17, 'bold_cond'), f"#{m['number']}") + 16 + 6
        flag = self.flag_img(m.get('country'), 20)
        if flag:
            w += flag.width + 6
        if m.get('country'):
            w += text_w(font(17, 'bold_cond'), m['country'].upper()) + 16 + 8
        if m.get('team'):
            w += text_w(font(19, 'semibold_cond'), m['team'])
        return min(max_w, w)

    @staticmethod
    def lines_w(lines, f):
        return max([text_w(f, ln) for ln in lines] or [0])

    def meta_row(self, img, x, cy, name, max_w):
        m = self.meta(name)
        cx = x
        if m.get('number'):
            cx += self.chip(img, cx, cy, f"#{m['number']}", self.th.text, size=17) + 6
        flag = self.flag_img(m.get('country'), 20)
        if flag:
            img.alpha_composite(flag, (int(cx), int(cy - flag.height / 2)))
            cx += flag.width + 6
        if m.get('country'):
            cx += self.chip(img, cx, cy, m['country'].upper(), self.th.panel_hi, self.th.text, size=17) + 8
        if m.get('team'):
            f = font(19, 'semibold_cond')
            text_vcenter(img, cx, cy, ellipsize(m['team'], f, x + max_w - cx), f, self.th.muted)

    @staticmethod
    def text_lines(img, x, y, lines, f, fill, line_h, anchor='la'):
        """Draws wrapped lines top-down from y; returns the y below the last line."""
        for ln in lines:
            ImageDraw.Draw(img).text((x, y), ln, font=f, fill=fill, anchor=anchor)
            y += line_h
        return y

    @staticmethod
    def finish(img):
        """Adds the drop shadow; returns (img, margin)."""
        return add_shadow(img)

    # ------------------------------------------------------------------ static frame
    def static_layer(self, size):
        img = Image.new('RGBA', size, (0, 0, 0, 0))
        lay = self.cfg['layout']
        # The frame and map wrap the game's own HUD, which RaceRoom replays don't show
        if lay['leaderboard']['enabled'] and self.race.has_hud and not self.tower:
            self._leaderboard_frame(img)
        if lay['map']['enabled'] and self.race.has_hud:
            self._map_frame(img)
        if lay['logo']['enabled'] and self.logo:
            lg = lay['logo']
            logo = fit_image(self.logo, 420, lg['h'])
            x = lg['x'] - logo.width if lg.get('anchor', 'right') == 'right' else lg['x']
            img.alpha_composite(logo, (int(x), int(lg['y'])))
        for extra in lay.get('extra_logos', []):
            im = load_image(gcfg.resolve_path(extra.get('path')), bool(extra.get('tint_white')))
            if im is None:
                continue
            im = fit_image(im, 800, extra.get('h', 60))
            if extra.get('opacity', 1.0) < 1.0:
                im.putalpha(im.getchannel('A').point(lambda v, o=extra['opacity']: int(v * o)))
            x = extra.get('x', 0) - (im.width if extra.get('anchor') == 'right' else 0)
            img.alpha_composite(im, (int(x), int(extra.get('y', 0))))
        return img

    def leaderboard_rect(self):
        lb = self.cfg['layout']['leaderboard']
        rows = lb.get('rows') or max(1, len(self.race.drivers))
        h = rows * lb['row_h'] + lb['pad']
        return lb['x'], lb['y'], lb['w'], h

    def _leaderboard_frame(self, img):
        lb = self.cfg['layout']['leaderboard']
        x, y, w, h = self.leaderboard_rect()
        th = self.th
        top = y
        if lb.get('header', True):
            hh = 40
            top = y - hh - 4
            round_rect(img, (x, top, x + w, top + hh), 6, th.panel, corners=(True, True, False, False))
            # accent block with the championship logo
            round_rect(img, (x, top, x + 64, top + hh), 6, th.accent, corners=(True, False, False, False))
            if self.logo:
                lg = fit_image(self.logo, 52, 28)
                img.alpha_composite(lg, (int(x + 32 - lg.width / 2), int(top + hh / 2 - lg.height / 2)))
            else:
                text_vcenter(img, x + 32, top + hh / 2, (self.cfg['championship'].get('short_name') or 'LIVE')[:4],
                             font(18, 'bold_cond'), contrast_text(th.accent), 'm')
            champ = self.cfg['championship']
            title = (champ.get('name') or 'LIVE TIMING').upper()
            ftitle = fit_font(title, w - 64 - 90, 21, 'bold_cond')
            text_vcenter(img, x + 76, top + hh / 2, title, ftitle, th.text)
            self.chip(img, x + w - 70, top + hh / 2, 'RACE', th.text, th.dark_text, size=17, h=24)
            round_rect(img, (x, top + hh, x + w, top + hh + 4), 0, th.accent)
        # side accent rail and outline
        bottom = y + h + (34 if lb.get('footer', True) else 0)
        round_rect(img, (x - 7, y, x - 3, y + h), 2, th.accent)
        round_outline(img, (x - 10, top - 3, x + w + 3, bottom + 3), 8, th.border, 2)

    def leaderboard_footer(self, img, flag_state):
        lb = self.cfg['layout']['leaderboard']
        if not (lb['enabled'] and lb.get('footer', True)):
            return
        x, y, w, h = self.leaderboard_rect()
        fy = y + h + 4
        th = self.th
        round_rect(img, (x, fy, x + w, fy + 30), 6, th.panel, corners=(False, False, True, True))
        state = flag_state or 'pre-race'
        col = rgba(FLAG_COLORS.get(state, '#9AA3B2'))
        if state == 'chequered':
            self._checker(img, x + 10, fy + 8, 22, 14, 4)
        else:
            round_rect(img, (x + 10, fy + 8, x + 32, fy + 22), 3, col)
        label = {'pre-race': 'FORMATION', 'green': 'GREEN FLAG', 'yellow': 'YELLOW FLAG',
                 'double yellow': 'DOUBLE YELLOW', 'chequered': 'CHEQUERED FLAG'}.get(state, state.upper())
        text_vcenter(img, x + 42, fy + 16, label, font(17, 'bold_cond'),
                     col if state not in ('chequered', 'pre-race') else th.text)
        track = gcfg.clean_track_name(self.race.track).upper()
        f = font(17, 'semibold_cond')
        text_vcenter(img, x + w - 10, fy + 16, ellipsize(track, f, w / 2), f, th.muted, 'r')

    def _checker(self, img, x, y, w, h, cell):
        d = ImageDraw.Draw(img)
        for i in range(0, w, cell):
            for j in range(0, h, cell):
                c = (255, 255, 255, 255) if (i // cell + j // cell) % 2 == 0 else (20, 20, 20, 255)
                d.rectangle((x + i, y + j, x + min(i + cell, w) - 1, y + min(j + cell, h) - 1), fill=c)

    def _map_frame(self, img):
        mp = self.cfg['layout']['map']
        x, y, w, h = mp['x'], mp['y'], mp['w'], mp['h']
        th = self.th
        top = y
        if mp.get('header', True):
            hh = 32
            top = y - hh - 2
            round_rect(img, (x, top, x + w, top + hh), 6, th.panel, corners=(True, True, False, False))
            round_rect(img, (x, top + hh - 3, x + w, top + hh), 0, th.accent)
            track = gcfg.clean_track_name(self.race.track).upper() or 'TRACK'
            f = fit_font(track, w - 110, 20, 'bold_cond')
            text_vcenter(img, x + 12, top + hh / 2 - 1, track, f, th.text)
            if self.race.track_length_m:
                text_vcenter(img, x + w - 12, top + hh / 2 - 1, f"{self.race.track_length_m / 1000:.2f} KM",
                             font(17, 'semibold_cond'), th.muted, 'r')
        round_outline(img, (x - 3, top - 3, x + w + 3, y + h + 3), 8, th.border, 2)

    # ------------------------------------------------------------------ ticker
    def ticker_strip(self, kind, rows, height):
        """Pre-renders the scrolling content. rows = [(pos, name, gap_text)]."""
        th = self.th
        items = []
        f_pos = font(20, 'bold_cond')
        f_name = font(22, 'bold_cond')
        f_gap = font(19, 'semibold_cond')
        for pos, name, gap in rows:
            short = self.meta(name)['short'].upper()
            w = 34 + 10 + text_w(f_name, short) + (12 + text_w(f_gap, gap) if gap else 0) + 34
            items.append((pos, name, short, gap, int(w)))
        total = sum(i[4] for i in items)
        strip = Image.new('RGBA', (max(1, total), height), (0, 0, 0, 0))
        x = 0
        cy = height / 2
        for pos, name, short, gap, w in items:
            box_bg = rgba(PODIUM[pos]) if pos in PODIUM and kind == 'results' else th.text
            round_rect(strip, (x, cy - 14, x + 34, cy + 14), 4, box_bg)
            text_vcenter(strip, x + 17, cy + 1, str(pos), f_pos, th.dark_text, 'm')
            round_rect(strip, (x + 38, cy - 12, x + 41, cy + 12), 0, self.driver_color(name))
            cx = x + 48
            text_vcenter(strip, cx, cy + 1, short, f_name, th.text)
            cx += text_w(f_name, short) + 12
            if gap:
                col = th.accent2 if gap.upper() in ('LEADER', 'INTERVAL') else th.muted
                text_vcenter(strip, cx, cy + 1, gap, f_gap, col)
            x += w
            # separator
            round_rect(strip, (x - 19, cy - 3, x - 13, cy + 3), 3, (255, 255, 255, 90))
        return strip

    def ticker_frame(self, size, title, subtitle, strip, scroll, slide):
        """Draws the full-width ticker band. slide 0..1 = hidden..shown."""
        tk = self.cfg['layout']['ticker']
        W = size[0]
        h = tk['h']
        th = self.th
        band = Image.new('RGBA', (W, h), (0, 0, 0, 0))
        round_rect(band, (0, 0, W, h), 0, th.panel)
        round_rect(band, (0, 0, W, 3), 0, th.accent)
        # left tab
        f_t = font(21, 'bold_cond')
        f_s = font(15, 'semibold_cond')
        tab_w = int(max(text_w(f_t, title), text_w(f_s, subtitle)) + 30)
        logo_w = 0
        if self.logo:
            lg = fit_image(self.logo, 90, h - 16)
            logo_w = lg.width + 16
        darker = tuple(int(c * 0.7) for c in th.accent[:3]) + (255,)
        band.alpha_composite(h_gradient(tab_w + logo_w + 24, h, th.accent, darker), (0, 0))
        polygon(band, [(tab_w + logo_w + 24, 0), (tab_w + logo_w + 44, 0), (tab_w + logo_w + 24, h)], darker)
        fg = contrast_text(th.accent)
        if self.logo:
            band.alpha_composite(lg, (12, int(h / 2 - lg.height / 2)))
        text_vcenter(band, 14 + logo_w, h / 2 - 8, title, f_t, fg)
        text_vcenter(band, 14 + logo_w, h / 2 + 12, subtitle, f_s, fg)
        area_x = tab_w + logo_w + 60
        area_w = W - area_x - 20
        # scrolling content, looped: copies of the strip side by side make the crawl seamless
        cycle = strip.width + self.TICKER_GAP
        off = int(scroll) % cycle
        view = Image.new('RGBA', (area_w, h), (0, 0, 0, 0))
        for start in range(-off, area_w, cycle):
            if start + strip.width <= 0:
                continue
            src_x = max(0, -start)
            piece = strip.crop((src_x, 0, min(strip.width, src_x + area_w - max(0, start)), h))
            view.alpha_composite(piece, (max(0, start), 0))
        # fade edges
        fade = Image.linear_gradient('L').rotate(90).resize((40, h))  # 0 on the left -> 255 on the right
        mask = Image.new('L', (area_w, h), 255)
        mask.paste(fade, (0, 0))
        mask.paste(fade.transpose(Image.FLIP_LEFT_RIGHT), (area_w - 40, 0))
        va = view.getchannel('A')
        view.putalpha(Image.composite(va, Image.new('L', va.size, 0), mask))
        band.alpha_composite(view, (area_x, 0))
        frame = Image.new('RGBA', size, (0, 0, 0, 0))
        y = tk['y'] + (1 - slide) * (size[1] - tk['y'] + 4)
        frame.alpha_composite(band, (0, int(round(y))))
        return frame

    # ------------------------------------------------------------------ popup cards
    def build(self, g, anim=None):
        """anim: 0..1 progress for cards that animate while held (see renderer.ANIMATED)."""
        fn = getattr(self, f"card_{g['type']}", None)
        if fn is None:
            return None
        img = fn(g) if anim is None else fn(g, anim)
        if img is None:
            return None
        return self.finish(img)

    def card_driver_card(self, g):
        th = self.th
        name = g['driver']
        d = g.get('data', {})
        car = self.car(name, 300, 185)
        text_w_max = 440
        lines = wrap(g.get('text', ''), font(21, 'regular'), text_w_max, 4)
        panel_h = 128 + (len(lines) * 26 + 8 if lines else 0)
        car_w = car.width + 10 if car else 0
        badge = self.card_badge(name, panel_h, 96)
        # Only as wide as the text needs, so the logo sits next to it
        used = max(self.name_block_w(name, text_w_max, 40, 22), self.meta_row_w(name, text_w_max),
                   self.lines_w(lines, font(21, 'regular')), 220)
        W = 110 + int(used) + 24 + car_w + self.badge_w(badge)
        over = max(0, (car.height - panel_h + 10)) if car else 0
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'DRIVER').upper(),
                                    top_extra=LABEL_H + over, stripe=self.driver_color(name))
        col = self.driver_color(name)
        pos = d.get('pos')
        round_rect(img, (14, top + 14, 96, top + 96), 6, col)
        fpos = fit_font(f"P{pos}" if pos else '—', 72, 40, 'bold')
        text_vcenter(img, 55, top + 55, f"P{pos}" if pos else '—', fpos, contrast_text(col), 'm')
        if d.get('gained') not in (None, 0):
            self.delta(img, 55, top + 112, d['gained'], 16, suffix=' ON GRID')
        y = self.name_block(img, 110, top + 12, name, text_w_max, 40, 22)
        self.meta_row(img, 110, y + 18, name, text_w_max)
        ly = top + 128
        for ln in lines:
            ImageDraw.Draw(img).text((110, ly - 6), ln, font=font(21, 'regular'), fill=th.muted)
            ly += 26
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + panel_h - car.height + 6)))
        self.place_badge(img, badge, top, panel_h)
        return img

    def card_battle(self, g):
        th = self.th
        a, b = g['driver'], g.get('driver2')
        if not b:
            return None
        d = g.get('data', {})
        car_a = self.car(a, 190, 92) or self.badge(a, 76)
        car_b = self.car(b, 190, 92) or self.badge(b, 76)
        W = 900
        f_text = font(19, 'regular')
        lines = wrap(g.get('text', ''), f_text, W - 60, 2)
        panel_h = 112 + (12 + len(lines) * 24 if lines else 0)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or f"BATTLE FOR P{d.get('pos', '')}").upper())
        cx = W / 2
        cy = top + 56
        for side, name, pos, car in ((0, a, d.get('pos'), car_a), (1, b, d.get('pos2'), car_b)):
            col = self.driver_color(name)
            if side == 0:
                round_rect(img, (0, top, 6, top + panel_h), 0, col)
            else:
                round_rect(img, (W - 6, top, W, top + panel_h), 0, col, corners=(False, True, True, False))
            m = self.meta(name)
            short = m['short'].upper()
            label = f"P{pos}" if pos else ''
            fchip = font(22, 'bold_cond')
            wchip = int(text_w(fchip, label) + 16)
            # Text starts just inside the car/logo and the name shrinks to stay clear of the interval box
            inset = max(144, 16 + (car.width if car else 0) + 18)
            fname = fit_font(short, cx - 82 - (inset + wchip + 10), 36, 'bold_cond')
            if side == 0:
                if car:
                    img.alpha_composite(car, (16, int(cy - car.height / 2)))
                x = inset
                self.chip(img, x, cy - 14, label, col, size=22, h=32)
                text_vcenter(img, x + wchip + 10, cy - 14, short, fname, th.text)
                if m.get('team'):
                    f = font(17, 'semibold_cond')
                    text_vcenter(img, x, cy + 22, ellipsize(m['team'], f, cx - 90 - x), f, th.muted)
            else:
                if car:
                    if not self.logo_path(name):  # car previews face the middle; logos stay readable
                        car = car.transpose(Image.FLIP_LEFT_RIGHT)
                    img.alpha_composite(car, (int(W - 16 - car.width), int(cy - car.height / 2)))
                x = W - inset
                self.chip(img, x - wchip, cy - 14, label, col, size=22, h=32)
                text_vcenter(img, x - wchip - 10, cy - 14, short, fname, th.text, 'r')
                if m.get('team'):
                    f = font(17, 'semibold_cond')
                    text_vcenter(img, x, cy + 22, ellipsize(m['team'], f, x - cx - 90), f, th.muted, 'r')
        # centre gap badge
        gap = d.get('gap')
        round_rect(img, (cx - 72, cy - 36, cx + 72, cy + 36), 8, th.panel_hi)
        if gap:
            text_vcenter(img, cx, cy - 16, 'INTERVAL', font(15, 'semibold_cond'), th.muted, 'm')
            text_vcenter(img, cx, cy + 12, gap, fit_font(gap, 128, 30, 'bold'), th.accent2, 'm')
        else:
            text_vcenter(img, cx, cy, 'VS', font(36, 'bold'), th.accent2, 'm')
        if lines:
            round_rect(img, (6, top + 108, W - 6, top + 109), 0, (255, 255, 255, 30))
            self.text_lines(img, cx, top + 116, lines, f_text, th.muted, 24, 'ma')
        return img

    @staticmethod
    def overtake_victims(g):
        """Passed drivers, top of the order first (multi-car passes list several)."""
        d = g.get('data', {})
        victims = list(d.get('victims') or [])
        if not victims and (g.get('driver2') or d.get('victim')):
            victims = [g.get('driver2') or d.get('victim')]
        return victims[:MAX_PASSED]

    def card_overtake(self, g, swap=1.0):
        """Mini leaderboard: the overtaker's row slides up over each passed driver's row in turn.

        swap 0..1 = before..after the pass (the renderer animates it)."""
        th = self.th
        name, d = g['driver'], g.get('data', {})
        victims = self.overtake_victims(g)
        pos = d.get('pos')
        if not victims or not pos:
            return self._overtake_single(g)
        n = len(victims)
        smooth = lambda p: p * p * (3 - 2 * p)
        e = smooth(swap)
        car = self.car(name, 260, 110)
        row_w, row_h, gap, pad = 430, (58 if n <= 2 else 50), 6, 12
        f = font(19, 'semibold_cond')
        lines = wrap(g.get('text') or (d.get('corner') or '').upper(), f, row_w + 80, 2)
        rows_h = row_h * (n + 1) + gap * n
        panel_h = pad * 2 + rows_h + (len(lines) * 24 + 4 if lines else 0)
        badge = self.card_badge(name, panel_h, 96)
        # 20 leaves room for the row lifting out
        W = 92 + row_w + 20 + (car.width + 10 if car else 0) + self.badge_w(badge)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'POSITION CHANGE').upper(),
                                    top_extra=LABEL_H + (max(0, car.height - panel_h + 10) if car else 0))
        slot_y = lambda s: top + pad + s * (row_h + gap)
        # Fixed position slots; the top one turns green as the overtaker arrives
        top_col = tuple(int(a + (b - a) * smooth(max(0.0, min(1.0, 1 - n * (1 - e))))) for a, b in zip(th.text, th.green))
        f_pos = font(30 if row_h > 52 else 26, 'bold')
        for s in range(n + 1):
            y = slot_y(s)
            round_rect(img, (pad, y, 84, y + row_h), 6, top_col if s == 0 else th.text)
            text_vcenter(img, (pad + 84) / 2, y + row_h / 2 + 1, f"P{pos + s}", f_pos, th.dark_text, 'm')
        arrows = max(0.0, min(1.0, (swap - 0.8) / 0.2))
        x0 = 92
        # The overtaker climbs from the bottom slot to the top; each passed driver drops one slot
        # as it goes by, and the overtaker lifts out to the right while it passes over them
        ov_slot = n * (1 - e)
        for i, victim in enumerate(victims):
            drop = smooth(max(0.0, min(1.0, i + 1 - ov_slot)))
            row = self._overtake_row(victim, row_w, row_h, False, -1, arrows)
            img.alpha_composite(row, (x0, int(round(slot_y(i + drop)))))
        lift = int(round(16 * min(1.0, 4 * e * (1 - e) * 1.5)))
        ovr = self._overtake_row(name, row_w, row_h, True, n, arrows)
        img.alpha_composite(ovr, (x0 + lift, int(round(slot_y(ov_slot)))))
        if lines:
            self.text_lines(img, pad + 2, top + pad + rows_h + 8, lines, f, th.muted, 24)
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + panel_h - car.height + 4)))
        self.place_badge(img, badge, top, panel_h)
        return img

    def _overtake_row(self, name, w, h, highlight, delta, arrow_alpha):
        th = self.th
        row = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        bg = th.panel_hi[:3] + (255,) if highlight else tuple(int(c * 0.85) for c in th.panel_hi[:3]) + (255,)
        round_rect(row, (0, 0, w, h), 6, bg)
        round_rect(row, (0, 0, 6, h), 0, self.driver_color(name))
        if highlight:
            round_outline(row, (0, 0, w, h), 6, th.green[:3] + (150,), 2)
        display = self.meta(name)['display']
        parts = display.split()
        first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', display)
        x, cy = 20, h / 2 + 1
        avail = w - x - 60
        if first:
            f1 = font(20, 'regular')
            text_vcenter(row, x, cy, ellipsize(first, f1, avail * 0.4), f1, th.text if highlight else th.muted)
            x += min(text_w(f1, first), avail * 0.4) + 10
        f2 = fit_font(last.upper(), w - x - 60, 32, 'bold')
        text_vcenter(row, x, cy, last.upper(), f2, th.text if highlight else th.muted)
        if arrow_alpha > 0:
            col = th.green if delta > 0 else th.red
            self.delta(row, w - 30, cy, delta, 22, col[:3] + (int(255 * arrow_alpha),))
        return row

    def _overtake_single(self, g):
        th = self.th
        name, d = g['driver'], g.get('data', {})
        victim = g.get('driver2') or d.get('victim')
        car = self.car(name, 280, 120)
        sub = []
        if victim:
            sub.append(f"PASSES {self.meta(victim)['short'].upper()}")
        if d.get('corner'):
            sub.append(d['corner'].upper())
        f = font(19, 'semibold_cond')
        lines = wrap(g.get('text') or '  ·  '.join(sub), f, 430, 3)
        panel_h = 112 + 24 * max(0, len(lines) - 1)
        badge = self.card_badge(name, panel_h)
        used = max(self.name_block_w(name, 420, 36, 20), self.lines_w(lines, f), 200)
        W = 110 + int(used) + 24 + (car.width + 10 if car else 0) + self.badge_w(badge)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'POSITION CHANGE').upper(),
                                    top_extra=LABEL_H + (max(0, car.height - panel_h + 10) if car else 0),
                                    stripe=self.driver_color(name))
        round_rect(img, (14, top + 14, 96, top + 98), 6, th.green)
        polygon(img, [(55, top + 22), (75, top + 42), (35, top + 42)], th.dark_text)
        text_vcenter(img, 55, top + 70, f"P{d.get('pos', '')}", font(32, 'bold'), th.dark_text, 'm')
        self.name_block(img, 110, top + 10, name, 420, 36, 20)
        self.text_lines(img, 110, top + 80, lines, f, th.muted, 24)
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + panel_h - car.height + 4)))
        self.place_badge(img, badge, top, panel_h)
        return img

    def card_new_leader(self, g, shine=1.0):
        """Top-centre banner for a change of race leader. shine 0..1 sweeps a highlight across it."""
        th = self.th
        name, d = g['driver'], g.get('data', {})
        gold = rgba(PODIUM[1])
        dark_gold = tuple(int(c * 0.62) for c in gold[:3]) + (255,)
        band_h, body_h = 42, 118
        H = band_h + body_h
        car = self.car(name, 300, 104) or self.badge(name, body_h - 28)
        car_w = car.width + 16 if car else 0
        victims = d.get('victims') or ([g['driver2']] if g.get('driver2') else [])
        sub = []
        if victims:
            sub.append('PASSES ' + ' & '.join(self.meta(v)['short'].upper() for v in victims[:2]))
        if d.get('corner'):
            sub.append(d['corner'].upper())
        f_sub = font(19, 'semibold_cond')
        sub_w = text_w(f_sub, '  ·  '.join(sub)) if sub else self.meta_row_w(name, 600)
        # Only as wide as the name and the line under it (but wide enough for the header)
        used = max(self.name_block_w(name, 600, 44, 22), sub_w)
        W = int(max(560, 140 + used + 24 + car_w + 14))
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, H), 8, th.panel)
        # Gold header band, brightest in the middle
        half = h_gradient(W // 2, band_h, gold, dark_gold)
        band = Image.new('RGBA', (W, band_h), (0, 0, 0, 0))
        band.paste(half, (0, 0))
        band.paste(half.transpose(Image.FLIP_LEFT_RIGHT), (W // 2, 0))
        shape = Image.new('RGBA', (W, band_h), (0, 0, 0, 0))
        round_rect(shape, (0, 0, W, band_h), 8, (255, 255, 255, 255), corners=(True, True, False, False))
        band.putalpha(shape.getchannel('A'))
        img.alpha_composite(band)
        f_head = font(26, 'bold_cond')
        head = 'NEW RACE LEADER'
        text_vcenter(img, W / 2, band_h / 2 + 1, head, f_head, th.dark_text, 'm')
        hw = text_w(f_head, head) / 2 + 22
        for side in (-1, 1):
            # Chevrons pointing in at the title
            for k in range(3):
                x = W / 2 + side * (hw + k * 16)
                pts = [(x, band_h / 2 - 9), (x + side * 8, band_h / 2 - 9), (x, band_h / 2 + 9), (x - side * 8, band_h / 2 + 9)]
                polygon(img, pts, th.dark_text[:3] + (230 - k * 70,))
        top = band_h
        round_rect(img, (0, top, 8, H), 0, self.driver_color(name))
        round_rect(img, (24, top + 14, 118, top + body_h - 14), 8, gold)
        text_vcenter(img, 71, top + body_h / 2 + 1, 'P1', font(48, 'bold'), th.dark_text, 'm')
        text_max = W - 140 - car_w - 16
        y = self.name_block(img, 140, top + 10, name, text_max, 44, 22)
        if sub:
            text_vcenter(img, 140, min(y + 18, H - 16), ellipsize('  ·  '.join(sub), f_sub, text_max), f_sub,
                         th.muted)
        else:
            self.meta_row(img, 140, min(y + 18, H - 16), name, text_max)
        if car:
            cy_car = top + body_h / 2 if self.logo_path(name) else H - car.height / 2 - 6
            img.alpha_composite(car, (int(W - car.width - 14), int(cy_car - car.height / 2)))
        round_outline(img, (0, 0, W, H), 8, gold[:3] + (200,), 2)
        if 0 < shine < 1:
            x = -160 + shine * (W + 320)
            mask = Image.new('L', (W, H), 0)
            ImageDraw.Draw(mask).polygon([(x, 0), (x + 70, 0), (x + 20, H), (x - 50, H)], fill=110)
            mask = ImageChops.multiply(mask.filter(ImageFilter.GaussianBlur(8)), img.getchannel('A'))
            light = Image.new('RGBA', (W, H), (255, 255, 255, 255))
            light.putalpha(mask)
            img.alpha_composite(light)
        return img

    def card_race_control(self, g):
        th = self.th
        d = g.get('data', {})
        msg = (g.get('title') or 'RACE CONTROL MESSAGE').upper()
        sub = g.get('text', '')
        fmsg = font(26, 'bold_cond')
        W = 760
        lines = wrap(msg, fmsg, W - 120, 3)
        fsub = font(19, 'regular')
        sub_lines = wrap(sub, fsub, W - 120, 3)
        panel_h = 30 + len(lines) * 32 + (len(sub_lines) * 24 + 6 if sub_lines else 0) + 10
        amber = rgba('#FFC400')
        img, top = self.card_canvas(W, panel_h, 'RACE CONTROL', label_bg=amber, label_fg=th.dark_text,
                                    stripe=amber)
        flag = d.get('flag', 'yellow')
        fcol = rgba(FLAG_COLORS.get(flag, '#FFD200'))
        if flag == 'chequered':
            self._checker(img, 22, top + 22, 56, 40, 8)
        else:
            round_rect(img, (22, top + 22, 78, top + 62), 3, fcol)
            if flag == 'double yellow':
                round_rect(img, (30, top + 30, 86, top + 70), 3, fcol)
                round_outline(img, (30, top + 30, 86, top + 70), 3, th.panel_solid, 2)
        y = top + 18
        for ln in lines:
            ImageDraw.Draw(img).text((104, y), ln, font=fmsg, fill=th.text)
            y += 32
        self.text_lines(img, 104, y + 2, sub_lines, fsub, th.muted, 24)
        if d.get('race_time'):
            text_vcenter(img, W - 14, top - LABEL_H / 2, d['race_time'], font(16, 'semibold_cond'), th.text, 'r')
        return img

    def card_pit_stop(self, g):
        """Car(s) entering the pit lane; cars pitting together are listed on one card."""
        th = self.th
        names = g.get('data', {}).get('drivers') or [g['driver']]
        single = len(names) == 1
        car = self.car(names[0], 240, 100) if single else None
        row_h = 54 if single else 40
        panel_h = 44 + row_h * len(names) + 8
        badge = self.card_badge(names[0], panel_h, 76) if single else None
        # Only as wide as the widest row (number, name, team) needs
        used = text_w(font(17, 'bold_cond'), 'ENTERS THE PIT LANE' if single else f"{len(names)} CARS IN THE PIT LANE")
        for nm in names:
            m = self.meta(nm)
            x = 36 + (text_w(font(17, 'bold_cond'), f"#{m['number']}") + 26 if m.get('number') else 0)
            label = m['display'].upper()
            x += text_w(fit_font(label, 460 - x, 34 if single else 26, 'bold_cond'), label)
            if m.get('team') and 490 - x - 14 > 60:
                x += 14 + min(490 - x - 14, text_w(font(18, 'semibold_cond'), m['team']))
            used = max(used, x - 22)
        W = int(22 + used + 24) + (car.width if car else 0) + self.badge_w(badge)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or ('PIT STOP' if single else 'PIT STOPS')).upper(),
                                    top_extra=LABEL_H + (max(0, car.height - panel_h + 10) if car else 0),
                                    stripe=self.driver_color(names[0]) if single else th.accent2)
        text_vcenter(img, 22, top + 24, 'ENTERS THE PIT LANE' if single else f"{len(names)} CARS IN THE PIT LANE",
                     font(17, 'bold_cond'), th.accent2)
        y = top + 44
        for name in names:
            m = self.meta(name)
            cy = y + row_h / 2
            round_rect(img, (22, y + 6, 26, y + row_h - 6), 0, self.driver_color(name))
            x = 36
            if m.get('number'):
                x += self.chip(img, x, cy, f"#{m['number']}", th.text, size=17) + 10
            label = m['display'].upper()
            f = fit_font(label, 460 - x, 34 if single else 26, 'bold_cond')
            text_vcenter(img, x, cy, label, f, th.text)
            x += text_w(f, label) + 14
            if m.get('team') and 490 - x > 60:
                ft = font(18, 'semibold_cond')
                text_vcenter(img, x, cy + 2, ellipsize(m['team'], ft, 490 - x), ft, th.muted)
            y += row_h
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + panel_h - car.height + 4)))
        self.place_badge(img, badge, top, panel_h)
        return img

    def card_stat(self, g):
        th = self.th
        d = g.get('data', {})
        value = str(d.get('value', ''))
        title = (g.get('title') or d.get('label') or '').upper()
        cap = g.get('text', '')
        name = g.get('driver')
        car = self.car(name, 220, 95) if name else None
        fval = fit_font(value, 220, 64, 'bold')
        vw = int(text_w(fval, value)) + 40
        f_title = font(26, 'bold_cond')
        title_lines = wrap(title, f_title, 420, 2)
        lines = wrap(cap, font(20, 'regular'), 420, 4)
        body_top = 20 + len(title_lines) * 30
        panel_h = max(112, body_top + len(lines) * 25 + 14)
        badge = self.card_badge(name, panel_h)
        used = max(self.lines_w(title_lines, f_title), self.lines_w(lines, font(20, 'regular')), 200)
        W = vw + 14 + int(used) + 24 + (car.width + 10 if car else 0) + self.badge_w(badge)
        img, top = self.card_canvas(W, panel_h, (g.get('label') or 'RACE STATS').upper(),
                                    top_extra=LABEL_H + (max(0, car.height - panel_h + 10) if car else 0),
                                    stripe=th.accent2)
        text_vcenter(img, 20 + vw / 2 - 10, top + panel_h / 2, value, fval, th.accent2, 'm')
        x = vw + 14
        self.text_lines(img, x, top + 14, title_lines, f_title, th.text, 30)
        self.text_lines(img, x, top + body_top, lines, font(20, 'regular'), th.muted, 25)
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + panel_h - car.height + 4)))
        self.place_badge(img, badge, top, panel_h)
        return img

    def card_movers(self, g):
        th = self.th
        rows = g.get('data', {}).get('rows', [])[:4]
        if not rows:
            return None
        W, row_h = 520, 40
        f_text = font(18, 'regular')
        lines = wrap(g.get('text', ''), f_text, W - 40, 3)
        panel_h = 16 + row_h * len(rows) + (len(lines) * 22 + 12 if lines else 0)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'POSITIONS GAINED').upper(), stripe=th.green)
        y = top + 10
        for r in rows:
            name, start, now, delta = r['driver'], r['grid'], r['pos'], r['gained']
            col = th.green if delta > 0 else th.red if delta < 0 else th.muted
            round_rect(img, (20, y + 6, 23, y + row_h - 6), 0, self.driver_color(name))
            f = font(24, 'bold_cond')
            text_vcenter(img, 34, y + row_h / 2, ellipsize(self.meta(name)['display'].upper(), f, 230), f, th.text)
            text_vcenter(img, 380, y + row_h / 2, f"P{start} › P{now}", font(20, 'semibold_cond'), th.muted, 'r')
            self_w = 80
            round_rect(img, (W - 14 - self_w, y + 6, W - 14, y + row_h - 6), 4, col)
            self.delta(img, W - 14 - self_w / 2, y + row_h / 2, delta, 21, contrast_text(col))
            y += row_h
        self.text_lines(img, 20, y + 6, lines, f_text, th.muted, 22)
        return img

    def card_headline(self, g):
        th = self.th
        W = 820
        fh = font(30, 'bold_cond')
        fb = font(21, 'regular')
        head = wrap((g.get('title') or '').upper(), fh, W - 50, 3)
        body = wrap(g.get('text', ''), fb, W - 50, 5)
        panel_h = 22 + len(head) * 34 + (8 + len(body) * 26 if body else 0) + 14
        img, top = self.card_canvas(W, panel_h, (g.get('label') or 'PADDOCK NEWS').upper(), stripe=th.accent2)
        y = top + 16
        for ln in head:
            ImageDraw.Draw(img).text((24, y), ln, font=fh, fill=th.text)
            y += 34
        y += 8
        for ln in body:
            ImageDraw.Draw(img).text((24, y), ln, font=fb, fill=th.muted)
            y += 26
        return img

    def card_title_card(self, g):
        th = self.th
        champ = self.cfg['championship']
        d = g.get('data', {})
        W, panel_h = 760, 190
        img, top = self.card_canvas(W, panel_h, (g.get('label') or 'RACE').upper(), stripe=th.accent)
        x = 28
        if self.logo:
            lg = fit_image(self.logo, 200, 70)
            img.alpha_composite(lg, (W - lg.width - 24, int(top + 22)))
        name = (champ.get('name') or '').upper()
        if name:
            f = fit_font(name, W - 280, 24, 'bold_cond')
            ImageDraw.Draw(img).text((x, top + 18), name, font=f, fill=th.accent2)
        sub = '  ·  '.join(s.upper() for s in (champ.get('season'), champ.get('round')) if s)
        if sub:
            ImageDraw.Draw(img).text((x, top + 48), sub, font=font(20, 'semibold_cond'), fill=th.muted)
        track = (g.get('title') or champ.get('event_name') or gcfg.clean_track_name(self.race.track)).upper()
        f = fit_font(track, W - 60, 64, 'bold')
        ImageDraw.Draw(img).text((x, top + 76), track, font=f, fill=th.text)
        info = g.get('text') or '  ·  '.join(s for s in (
            gcfg.clean_track_name(self.race.track_layout),
            f"{self.race.track_length_m / 1000:.3f} KM" if self.race.track_length_m else '',
            f"{d.get('drivers')} DRIVERS" if d.get('drivers') else '') if s)
        ImageDraw.Draw(img).text((x, top + 150), info.upper(), font=font(20, 'semibold_cond'), fill=th.muted)
        return img

    def card_commentators(self, g, anim=1.0):
        """The AI commentator line-up: an accent header with a mic and a LIVE tag, then one column per
        commentator (accent bar, name, voice waveform, bio). anim 0..1 reveals the columns one after another
        while the waveforms pulse."""
        th = self.th
        d = g.get('data', {})
        names = d.get('names', [])
        if not names:
            return None
        anim = 1.0 if anim is None else anim
        # Bios come from the config at draw time, so edits in Graphics Setup show straight away
        bios = {n: gcfg.commentator_bio(self.cfg, n) for n in names}
        per_row = min(3, len(names))
        col_w, pad, mono = 340, 22, 66
        f_first, f_last = font(20, 'regular'), font(34, 'bold')
        f_bio = font(18, 'regular')
        cols = []
        for n in names:
            parts = n.split()
            first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', n)
            lines = wrap(bios[n], f_bio, col_w - pad * 2, 3)
            cols.append((n, first, last.upper(), lines))
        body_h = pad + mono + 36 + max([len(c[3]) * 23 for c in cols] + [0]) + pad - 10
        n_rows = (len(cols) + per_row - 1) // per_row
        head_h = 46
        W, H = per_row * col_w, head_h + n_rows * body_h
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, H), 10, th.panel)
        # Header band: accent gradient with a soft highlight along its top edge
        darker = tuple(int(c * 0.55) for c in th.accent[:3]) + (255,)
        band = h_gradient(W, head_h, th.accent, darker)
        shape = Image.new('L', (W, head_h), 0)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, W - 1, head_h + 12), radius=10, fill=255)
        band.putalpha(shape)
        img.alpha_composite(band)
        round_rect(img, (10, 1, W - 10, 3), 1, (255, 255, 255, 60))
        fg = contrast_text(th.accent)
        self._mic(img, 18, head_h / 2, 26, fg)
        label = (g.get('label') or 'AI COMMENTATORS').upper()
        f_head = font(24, 'bold_cond')
        text_vcenter(img, 50, head_h / 2 + 1, label, f_head, fg)
        # LIVE tag on the right
        f_live = font(16, 'bold_cond')
        lw = int(text_w(f_live, 'LIVE') + 30)
        lx = W - 16 - lw
        round_rect(img, (lx, head_h / 2 - 12, lx + lw, head_h / 2 + 12), 12, (0, 0, 0, 90))
        round_rect(img, (lx + 9, head_h / 2 - 4, lx + 17, head_h / 2 + 4), 4, (255, 255, 255, 255))
        text_vcenter(img, lx + 22, head_h / 2 + 1, 'LIVE', f_live, (255, 255, 255, 255))
        round_rect(img, (0, head_h, W, head_h + 3), 0, th.accent2)

        n = len(cols)
        for i, (name, first, last, lines) in enumerate(cols):
            # Each column fades and rises in, one after another
            p = max(0.0, min(1.0, anim * (n + 1.5) - i))
            e = 1 - (1 - p) ** 3
            if e <= 0:
                continue
            col = Image.new('RGBA', (col_w, body_h), (0, 0, 0, 0))
            self._commentator_column(col, name, first, last, lines, pad, mono, f_first, f_last, f_bio, anim, i)
            if e < 1:
                col.putalpha(col.getchannel('A').point(lambda v, e=e: int(v * e)))
            cx, cy = (i % per_row) * col_w, head_h + (i // per_row) * body_h + int((1 - e) * 14)
            img.alpha_composite(col, (cx, cy))
            if i % per_row:
                round_rect(img, (cx, head_h + 18, cx + 1, head_h + body_h - 14), 0, (255, 255, 255, 34))
        round_outline(img, (0, 0, W, H), 10, (255, 255, 255, 40), 1)
        return img

    def _commentator_column(self, col, name, first, last, lines, pad, mono, f_first, f_last, f_bio, anim, idx):
        th = self.th
        x, y = pad, pad
        # Accent bar beside the name, red into gold
        bar = Image.new('RGBA', (5, mono), (0, 0, 0, 0))
        grad = Image.linear_gradient('L').resize((5, mono))  # 0 at the top -> 255 at the bottom
        bar.paste(Image.new('RGBA', (5, mono), th.accent), (0, 0))
        bar.paste(Image.new('RGBA', (5, mono), th.accent2), (0, 0), grad)
        shape = Image.new('RGBA', (5, mono), (0, 0, 0, 0))
        round_rect(shape, (0, 0, 5, mono), 2, (255, 255, 255, 255))
        bar.putalpha(shape.getchannel('A'))
        col.alpha_composite(bar, (x, y))
        tx = x + 18
        avail = col.width - tx - pad
        if first:
            text_vcenter(col, tx, y + 14, ellipsize(first, f_first, avail), f_first, th.muted)
        f2 = fit_font(last, avail, f_last.size, 'bold')
        text_vcenter(col, tx, y + (40 if first else 30), last, f2, th.text)
        # Voice waveform under the name, pulsing while the card animates in
        self._waveform(col, tx, y + mono + 4, min(avail, 170), 22, anim, idx)
        self.text_lines(col, x, y + mono + 28, lines, f_bio, th.muted, 23)

    def _waveform(self, img, x, cy, w, h, anim, seed):
        import math
        bars, gap = 16, 4
        bw = max(2, (w - gap * (bars - 1)) / bars)
        pulse = 1.0 if anim >= 1 else anim
        for b in range(bars):
            base = 0.35 + 0.65 * abs(math.sin(b * 1.7 + seed * 2.3)) * (0.6 + 0.4 * math.sin(b * 0.45))
            wobble = 0.5 + 0.5 * abs(math.sin(anim * 9 + b * 0.8 + seed))
            amp = base * (wobble * (1 - pulse) + pulse) * max(0.15, min(1.0, anim * 2))
            bh = max(2, h * amp)
            bx = x + b * (bw + gap)
            fade = int(255 * (1 - 0.55 * b / bars))
            round_rect(img, (bx, cy - bh / 2, bx + bw, cy + bh / 2), bw / 2, self.th.accent2[:3] + (fade,))

    @staticmethod
    def _mic(img, x, cy, size, color):
        """Microphone icon drawn at 4x and scaled down for smooth edges."""
        ss = 4
        S = size * ss
        icon = Image.new('L', (S, S), 0)
        dr = ImageDraw.Draw(icon)
        cw = S * 0.36
        dr.rounded_rectangle((S / 2 - cw / 2, S * 0.04, S / 2 + cw / 2, S * 0.62), radius=cw / 2, fill=255)
        lw = int(S * 0.08)
        dr.arc((S * 0.2, S * 0.18, S * 0.8, S * 0.74), 0, 180, fill=255, width=lw)
        dr.line((S / 2, S * 0.74, S / 2, S * 0.9), fill=255, width=lw)
        dr.rounded_rectangle((S * 0.3, S * 0.88, S * 0.7, S * 0.96), radius=lw / 2, fill=255)
        icon = icon.resize((size, size), Image.LANCZOS)
        layer = Image.new('RGBA', (size, size), color)
        layer.putalpha(icon)
        img.alpha_composite(layer, (int(x), int(cy - size / 2)))

    def card_start_movers(self, g):
        """Biggest gainer and biggest loser since the grid, side by side."""
        th = self.th
        d = g.get('data', {})
        cols = [(k, d[k]) for k in ('gainer', 'loser') if d.get(k)]
        if not cols:
            return None
        col_w = 330
        W = col_w * len(cols) + 8
        panel_h = 128
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'AFTER THE START').upper(), stripe=th.accent)
        for i, (key, r) in enumerate(cols):
            x = 8 + i * col_w
            if i:
                round_rect(img, (x - 1, top + 16, x, top + panel_h - 16), 0, (255, 255, 255, 40))
            good = key == 'gainer'
            colr = th.green if good else th.red
            text_vcenter(img, x + 18, top + 24, 'BIGGEST GAINER' if good else 'BIGGEST LOSER',
                         font(17, 'bold_cond'), colr)
            # Places gained/lost badge
            round_rect(img, (x + 18, top + 42, x + 106, top + 110), 6, colr)
            self.delta(img, x + 62, top + 76, r['gained'], 34, contrast_text(colr))
            name = r['driver']
            round_rect(img, (x + 120, top + 46, x + 123, top + 106), 0, self.driver_color(name))
            f = fit_font(self.meta(name)['short'].upper(), col_w - 150, 32, 'bold_cond')
            text_vcenter(img, x + 134, top + 62, self.meta(name)['short'].upper(), f, th.text)
            text_vcenter(img, x + 134, top + 94, f"P{r['grid']} › P{r['pos']}", font(22, 'semibold_cond'), th.muted)
        return img

    def card_fastest_lap(self, g):
        th = self.th
        name, d = g['driver'], g.get('data', {})
        purple = rgba(FASTEST_PURPLE)
        car = self.car(name, 260, 110)
        sub = []
        if d.get('lap'):
            sub.append(f"LAP {d['lap']}")
        if d.get('gain') and d.get('prev_driver'):
            prev = 'OWN BEST' if d['prev_driver'] == name else self.meta(d['prev_driver'])['short'].upper()
            sub.append(f"-{d['gain']}s ON {prev}")
        f = font(19, 'semibold_cond')
        lines = wrap(g.get('text') or '  ·  '.join(sub), f, 410, 3)
        panel_h = 112 + 24 * max(0, len(lines) - 1)
        badge = self.card_badge(name, panel_h, 76)
        # Only as wide as the name and the line under it, so the logo sits close to them
        used = max(self.name_block_w(name, 400, 36, 20), self.lines_w(lines, f), 180)
        W = 214 + int(used) + 24 + (car.width + 10 if car else 0) + self.badge_w(badge)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'FASTEST LAP').upper(), label_bg=purple,
                                    top_extra=LABEL_H + (max(0, car.height - panel_h + 10) if car else 0),
                                    stripe=self.driver_color(name))
        # Lap time in the purple block, stopwatch-style
        lap_time = d.get('lap_time', '')
        round_rect(img, (16, top + 14, 196, top + panel_h - 14), 6, purple)
        text_vcenter(img, 106, top + 38, 'LAP TIME', font(15, 'semibold_cond'), (255, 255, 255, 200), 'm')
        text_vcenter(img, 106, top + 68, lap_time, fit_font(lap_time, 164, 36, 'bold'), (255, 255, 255, 255), 'm')
        self.name_block(img, 214, top + 10, name, 400, 36, 20)
        self.text_lines(img, 214, top + 80, lines, f, th.muted, 24)
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + panel_h - car.height + 4)))
        self.place_badge(img, badge, top, panel_h)
        return img

    @staticmethod
    def _readable(color):
        """Lifts dark livery colours so they still read on the dark panel."""
        # Brighten to full value first (keeps reds red), then mix in white only if still too dark
        scale = 255 / max(1, *color[:3])
        r, g, b = (min(255, int(c * scale)) for c in color[:3])
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        if lum < 80:
            k = (80 - lum) / 160
            r, g, b = (int(c + (255 - c) * k) for c in (r, g, b))
        return (r, g, b, 255)

    def card_radio(self, g):
        """Team radio in the style of the F1 radio posts: SURNAME / RADIO header with the car number,
        then the exchange as quoted lines - the driver's in their colour on the right, replies on the left."""
        th = self.th
        name = g.get('driver')
        d = g.get('data', {})
        segments = d.get('segments', [])[:4]
        if not segments:
            return None
        W = int(self.cfg['layout']['radio'].get('w', 400))
        col = self.driver_color(name) if name else th.accent
        if max(col[:3]) - min(col[:3]) < 60:
            # Grey/white/black liveries would blend with the white replies
            col = th.accent
        col = self._readable(col)
        white = th.text
        pad = 24
        meta = self.meta(name) if name else {}
        surname = (meta['display'].split()[-1] if name else d.get('speaker', 'TEAM')).upper()
        number = str(meta.get('number') or '')

        # Header: big number on the left, SURNAME over RADIO on the right
        f_num = font(78, 'bold')
        num_w = int(text_w(f_num, number)) + 18 if number else 0
        f_head = fit_font(surname, W - pad * 2 - num_w, 44, 'bold')
        f_radio = font(44, 'bold')
        base_name, base_radio = 66, 112  # text baselines
        head_h = base_radio + 22

        f_line = font(23, 'bold_cond')
        line_h = 27
        text_max = W - pad * 2
        blocks = []
        for seg in segments:
            lines = wrap(f"“{seg['quote'].upper()}”", f_line, text_max - 20, 3)
            blocks.append((seg.get('is_driver'), lines))
        body_h = sum(len(ls) * line_h for _, ls in blocks) + 12 * (len(blocks) - 1) + 30
        H = head_h + body_h

        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, H), 3, rgba('#16171C', 0.88))
        # Faint speed lines behind the header, fading out to the right
        dr = ImageDraw.Draw(img)
        for i, y in enumerate(range(base_name - 30, base_radio + 4, 6)):
            x1 = int(W * (0.30 + 0.25 * ((i * 7) % 5) / 5))
            dr.line((0, y, x1, y), fill=(255, 255, 255, 7 + (i % 2) * 4), width=2)
        # Thin colour rule along the top
        round_rect(img, (0, 0, W, 4), 0, col)

        right = W - pad
        ImageDraw.Draw(img).text((right, base_name), surname, font=f_head, fill=col, anchor='rs')
        ImageDraw.Draw(img).text((right, base_radio), 'RADIO', font=f_radio, fill=white, anchor='rs')
        if number:
            ImageDraw.Draw(img).text((pad - 4, base_radio), number, font=f_num, fill=col, anchor='ls')

        # Body: driver lines right-aligned in their colour, replies left-aligned in white
        y = head_h + 12
        for is_driver, lines in blocks:
            for ln in lines:
                if is_driver:
                    ImageDraw.Draw(img).text((right, y), ln, font=f_line, fill=col, anchor='ra')
                else:
                    ImageDraw.Draw(img).text((pad, y), ln, font=f_line, fill=white, anchor='la')
                y += line_h
            y += 12
        return img

    def card_chequered(self, g):
        th = self.th
        name = g.get('driver')
        W = 760
        f_text = font(19, 'regular')
        lines = wrap(g.get('text', ''), f_text, W - 130, 2)
        panel_h = 116 + 24 * max(0, len(lines) - 1)
        img, top = self.card_canvas(W, panel_h, None)
        self._checker(img, 0, top, 90, panel_h, 15)
        ImageDraw.Draw(img).text((112, top + 14), (g.get('label') or 'CHEQUERED FLAG').upper(),
                                 font=font(22, 'bold_cond'), fill=th.accent2)
        title = (g.get('title') or (f"{self.meta(name)['display']} WINS" if name else 'RACE COMPLETE')).upper()
        ImageDraw.Draw(img).text((112, top + 42), ellipsize(title, font(44, 'bold'), W - 130),
                                 font=fit_font(title, W - 130, 44, 'bold'), fill=th.text)
        self.text_lines(img, 112, top + 90, lines, f_text, th.muted, 24)
        return img

    def card_results(self, g):
        th = self.th
        rows = g.get('data', {}).get('rows', [])
        if not rows:
            return None
        W = 900
        row_h = 42 if len(rows) <= 12 else 32
        head_h = 92
        panel_h = head_h + row_h * len(rows) + 16
        img = Image.new('RGBA', (W, panel_h), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, panel_h), 10, th.panel)
        round_rect(img, (0, 0, W, head_h), 10, th.accent, corners=(True, True, False, False))
        fg = contrast_text(th.accent)
        x = 26
        if self.logo:
            lg = fit_image(self.logo, 130, 54)
            img.alpha_composite(lg, (W - lg.width - 24, int(head_h / 2 - lg.height / 2)))
        ImageDraw.Draw(img).text((x, 14), (g.get('title') or 'PROVISIONAL CLASSIFICATION').upper(),
                                 font=font(34, 'bold_cond'), fill=fg)
        sub = '  ·  '.join(s.upper() for s in (self.cfg['championship'].get('name'),
                                                gcfg.clean_track_name(self.race.track)) if s)
        ImageDraw.Draw(img).text((x, 56), sub, font=font(19, 'semibold_cond'), fill=fg)
        y = head_h + 8
        fpos = font(22, 'bold')
        fname = font(25 if row_h > 36 else 21, 'bold_cond')
        fsmall = font(18, 'semibold_cond')
        for i, r in enumerate(rows):
            name, pos = r['driver'], r['pos']
            if i % 2 == 0:
                round_rect(img, (12, y, W - 12, y + row_h), 4, (255, 255, 255, 14))
            cy = y + row_h / 2
            box = rgba(PODIUM[pos]) if pos in PODIUM else th.text
            round_rect(img, (24, cy - 15, 64, cy + 15), 4, box)
            text_vcenter(img, 44, cy + 1, str(pos), fpos, th.dark_text, 'm')
            round_rect(img, (74, cy - 13, 78, cy + 13), 0, self.driver_color(name))
            m = self.meta(name)
            nx = 90
            if m.get('number'):
                text_vcenter(img, nx, cy, f"#{m['number']}", fsmall, th.muted)
                nx += 52
            text_vcenter(img, nx, cy + 1, ellipsize(m['display'].upper(), fname, 330), fname, th.text)
            if m.get('team'):
                text_vcenter(img, 520, cy, ellipsize(m['team'], fsmall, 190), fsmall, th.muted)
            delta = r.get('gained')
            if delta is not None:
                self.delta(img, 760, cy, delta, 20)
            stops = r.get('stops')
            if stops is not None:
                text_vcenter(img, W - 30, cy, f"{stops} STOP{'S' if stops != 1 else ''}", fsmall, th.muted, 'r')
            y += row_h
        return img


def race_time_label(race, t_video):
    gf = race.green_flag_video
    return fmt_clock(t_video - gf) if gf is not None and t_video >= gf else ''
