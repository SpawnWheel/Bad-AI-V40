"""Broadcast graphic templates, drawn with Pillow in 1920x1080 space."""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import config as gcfg
from .drawing import (add_shadow, cover_image, ease_out, ellipsize, fit_font, fit_image, font, h_gradient, load_image,
                      polygon, rgba, round_outline, round_rect, text_vcenter, text_w, wave_cloth,
                      wrap)
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
        """Team/make logo as it is, sized to sit inside a card (never over its edge).
        No tile or colour strip behind it."""
        path = self.logo_path(name)
        logo = load_image(path) if path else None
        if logo is None:
            return None
        h = int(h)
        return fit_image(logo, int(h * 1.5), h)

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
            w += text_w(font(17, 'bold_cond'), f"#{m['number']}") + 16 + 8
        flag = self.flag_img(m.get('country'), 20)
        if flag:
            w += flag.width + 10
        elif m.get('country'):
            w += text_w(font(17, 'bold_cond'), m['country'].upper()) + 16 + 8
        if m.get('team'):
            w += text_w(font(19, 'semibold_cond'), m['team'])
        return min(max_w, w)

    # ------------------------------------------------------------------ sponsors
    def sponsor(self, kind):
        """(name, logo image or None) of the 'title', 'timing' or 'partner' sponsor, or None when unset."""
        sp = (self.cfg.get('sponsors') or {}).get(kind) or {}
        name = (sp.get('name') or '').strip()
        logo = load_image(gcfg.resolve_path(sp.get('logo'))) if sp.get('logo') else None
        return (name, logo) if name or logo else None

    def sponsor_w(self, kind, h, caption):
        sp = self.sponsor(kind)
        if not sp:
            return 0
        name, logo = sp
        cw = text_w(font(14, 'semibold_cond'), caption) + 10 if caption else 0
        return int(cw + (fit_image(logo, h * 4, h).width if logo else text_w(font(h * 0.8, 'bold_cond'), name.upper())))

    def sponsor_mark(self, img, right, cy, kind, h, caption='POWERED BY', color=None):
        """'POWERED BY' + the sponsor's logo (or name), right-aligned at right. Returns the width used."""
        sp = self.sponsor(kind)
        if not sp:
            return 0
        name, logo = sp
        x = right
        if logo:
            lg = fit_image(logo, h * 4, h)
            x -= lg.width
            img.alpha_composite(lg, (int(x), int(cy - lg.height / 2)))
        else:
            f = font(h * 0.8, 'bold_cond')
            x -= text_w(f, name.upper())
            text_vcenter(img, x, cy, name.upper(), f, color or self.th.text)
        if caption:
            f = font(14, 'semibold_cond')
            x -= 10
            text_vcenter(img, x, cy, caption, f, self.th.muted, 'r')
            x -= text_w(f, caption)
        return int(right - x)

    def pos_number(self, img, cx, cy, pos, size=64, color=None):
        """Big bare position number, F1 style (no box, no P)."""
        label = str(pos) if pos else '–'
        text_vcenter(img, cx, cy, label, fit_font(label, size * 1.3, size, 'bold'), color or self.th.text, 'm')

    def name_with_flag_w(self, name, size):
        m = self.meta(name)
        flag = self.flag_img(m.get('country'), int(size * 0.55))
        return text_w(font(size, 'bold_cond'), m['short'].upper()) + (flag.width + 10 if flag else 0)

    def name_with_flag(self, img, x, cy, name, size, color=None, anchor='l', max_w=None):
        """Flag then SHORT NAME on one line; anchor 'r' right-aligns the pair at x. Returns the width."""
        m = self.meta(name)
        label = m['short'].upper()
        flag = self.flag_img(m.get('country'), int(size * 0.55))
        f = fit_font(label, max_w - (flag.width + 10 if flag else 0), size, 'bold_cond') if max_w else font(size, 'bold_cond')
        w = text_w(f, label) + (flag.width + 10 if flag else 0)
        x0 = x - w if anchor == 'r' else x
        if flag:
            img.alpha_composite(flag, (int(x0), int(cy - flag.height / 2)))
            x0 += flag.width + 10
        text_vcenter(img, x0, cy, label, f, color or self.th.text)
        return w

    @staticmethod
    def lines_w(lines, f):
        return max([text_w(f, ln) for ln in lines] or [0])

    def meta_row(self, img, x, cy, name, max_w):
        m = self.meta(name)
        cx = x
        if m.get('number'):
            cx += self.chip(img, cx, cy, f"#{m['number']}", self.th.text, size=17) + 8
        flag = self.flag_img(m.get('country'), 20)
        if flag:
            img.alpha_composite(flag, (int(cx), int(cy - flag.height / 2)))
            cx += flag.width + 10
        elif m.get('country'):
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
        """F1-style driver card: big bare position number, colour bar, first name over SURNAME, then the
        number, flag and team on one line and at most one line of text. Wide and low."""
        th = self.th
        name = g['driver']
        d = g.get('data', {})
        car = self.car(name, 280, 120)
        f_text = font(20, 'regular')
        text = ' '.join((g.get('text') or '').split())
        # One line, as wide as it needs (two only for a long caption), so the card stays low
        lines = (wrap(text, f_text, 900, 1) if text_w(f_text, text) <= 900 else wrap(text, f_text, 720, 2)) if text else []
        panel_h = 112 + (8 + 26 * len(lines) if lines else 0)
        badge = self.card_badge(name, 112, 84)
        num_w = 96
        x0 = num_w + 18
        used = max(self.name_block_w(name, 520, 42, 22), self.meta_row_w(name, 520), self.lines_w(lines, f_text), 240)
        W = x0 + int(used) + 28 + (car.width + 10 if car else 0) + self.badge_w(badge)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'DRIVER').upper(),
                                    top_extra=LABEL_H + (max(0, car.height - panel_h + 10) if car else 0))
        col = self.driver_color(name)
        self.pos_number(img, num_w / 2 + 4, top + 50, d.get('pos'), 66)
        if d.get('gained') not in (None, 0):
            self.delta(img, num_w / 2 + 4, top + 96, d['gained'], 16)
        round_rect(img, (num_w, top + 14, num_w + 5, top + 98), 2, col)
        y = self.name_block(img, x0, top + 8, name, 520, 42, 22)
        self.meta_row(img, x0, min(y + 14, top + 92), name, 520)
        if lines:
            round_rect(img, (14, top + 110, W - 14, top + 111), 0, (255, 255, 255, 28))
            for i, ln in enumerate(lines):
                text_vcenter(img, 18, top + 127 + 26 * i, ln, f_text, th.muted)
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + 112 - car.height + 6)))
        self.place_badge(img, badge, top, 112)
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
        lines = []  # the title says it; no explainer under a battle
        panel_h = 112
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
        lines = []  # the rows tell the story; no explainer underneath
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
        f_pos = font(38 if row_h > 52 else 32, 'bold')
        for s in range(n + 1):
            y = slot_y(s)
            self.pos_number(img, (pad + 84) / 2, y + row_h / 2 + 1, pos + s, f_pos.size, top_col if s == 0 else th.text)
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
        flag = self.flag_img(self.meta(name).get('country'), 20)
        if flag:
            row.alpha_composite(flag, (x, int(cy - flag.height / 2)))
            x += flag.width + 12
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

    def card_stat(self, g):
        th = self.th
        d = g.get('data', {})
        """One big number, who it belongs to and a two- or three-word caption. Nothing to read."""
        value = str(d.get('value', ''))
        name = g.get('driver')
        caption = (d.get('short') or d.get('label') or '').upper()
        panel_h = 104
        car = self.car(name, 220, 95) if name else None
        fval = fit_font(value, 240, 76, 'bold')
        vw = int(text_w(fval, value)) + 48
        f_cap = fit_font(caption, 420, 22, 'semibold_cond')
        if name:
            head = self.meta(name)['short'].upper()
            f_head = fit_font(head, 420, 46, 'bold_cond')
            flag = self.flag_img(self.meta(name).get('country'), 24)
        else:
            head, flag = (g.get('title') or '').upper(), None
            f_head = fit_font(head, 520, 34, 'bold_cond')
        head_w = text_w(f_head, head) + (flag.width + 12 if flag else 0)
        badge = self.card_badge(name, panel_h)
        used = max(head_w, text_w(f_cap, caption), 160)
        W = vw + 20 + int(used) + 28 + (car.width + 10 if car else 0) + self.badge_w(badge)
        img, top = self.card_canvas(W, panel_h, (g.get('label') or 'RACE STATS').upper(),
                                    top_extra=LABEL_H + (max(0, car.height - panel_h + 10) if car else 0),
                                    stripe=self.driver_color(name) if name else th.accent2)
        text_vcenter(img, 14 + vw / 2, top + panel_h / 2, value, fval, th.accent2, 'm')
        round_rect(img, (vw + 4, top + 18, vw + 6, top + panel_h - 18), 0, (255, 255, 255, 50))
        x = vw + 20
        if head:
            if flag:
                img.alpha_composite(flag, (int(x), int(top + 40 - flag.height / 2)))
            text_vcenter(img, x + (flag.width + 12 if flag else 0), top + 40, head, f_head, th.text)
        text_vcenter(img, x, top + (78 if head else panel_h / 2), caption, f_cap, th.muted)
        if car:
            img.alpha_composite(car, (int(W - car.width - 6), int(top + panel_h - car.height + 4)))
        self.place_badge(img, badge, top, panel_h)
        return img

    def card_movers(self, g):
        th = self.th
        rows = g.get('data', {}).get('rows', [])[:4]
        if not rows:
            return None
        row_h, gap, pad = 50, 4, 10
        f_name = font(28, 'bold_cond')
        f_from = font(18, 'semibold_cond')
        name_w = max(self.name_with_flag_w(r['driver'], 28) for r in rows)
        W = int(pad + 64 + 18 + name_w + 24 + text_w(f_from, 'FROM P00') + 20 + 84 + pad)
        panel_h = pad * 2 + row_h * len(rows) + gap * (len(rows) - 1)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'POSITIONS GAINED').upper())
        y = top + pad
        for r in rows:
            name, start, now, delta = r['driver'], r['grid'], r['pos'], r['gained']
            col = th.green if delta > 0 else th.red if delta < 0 else th.muted
            round_rect(img, (pad, y, W - pad, y + row_h), 4, th.panel_hi)
            cy = y + row_h / 2
            self.pos_number(img, pad + 32, cy + 1, now, 34)
            round_rect(img, (pad + 64, y + 8, pad + 68, y + row_h - 8), 0, self.driver_color(name))
            x = pad + 82
            x += self.name_with_flag(img, x, cy + 1, name, 28) + 24
            text_vcenter(img, x, cy + 1, f"FROM P{start}", f_from, th.muted)
            round_rect(img, (W - pad - 84, y + 9, W - pad - 10, y + row_h - 9), 4, col)
            self.delta(img, W - pad - 47, cy, delta, 22, contrast_text(col))
            y += row_h + gap
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

    def card_title_card(self, g, anim=1.0):
        """Opening race card. The event flag (Branding tab) fills the right of the card as waving cloth,
        fading out towards the text; anim 0..1 sweeps it in while it ripples, then it settles."""
        th = self.th
        champ = self.cfg['championship']
        d = g.get('data', {})
        anim = 1.0 if anim is None else anim
        e = 1 - (1 - anim) ** 3
        W, H = 940, 226
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        shape = Image.new('L', (W, H), 0)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, W - 1, H - 1), radius=12, fill=255)
        round_rect(img, (0, 0, W, H), 12, th.panel)

        # Text first: the flag and the slashes start where it ends
        x = 34
        name = (champ.get('name') or '').strip().upper()
        f_name = fit_font(name, W * 0.6 - x, 26, 'bold_cond') if name else None
        track = (g.get('title') or champ.get('event_name') or gcfg.clean_track_name(self.race.track)).upper()
        f_track = fit_font(track, W * 0.6 - x, 76, 'bold')
        info = (g.get('text') or '  ·  '.join(s_ for s_ in (
            gcfg.clean_track_name(self.race.track_layout),
            f"{self.race.track_length_m / 1000:.3f} KM" if self.race.track_length_m else '',
            f"{d.get('drivers')} DRIVERS" if d.get('drivers') else '') if s_)).upper()
        f_info = font(19, 'semibold_cond')
        text_right = x + max(text_w(f_track, track), text_w(f_name, name) if name else 0, 52 + text_w(f_info, info))
        fx = int(min(W * 0.62, max(W * 0.36, text_right - 10)))
        fw = W - fx

        # Flag (or an accent glow without one), cut to the card's shape
        art = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        flag = load_image(gcfg.resolve_path(champ.get('event_flag')), autocrop=False)
        if flag is not None:
            cloth = wave_cloth(cover_image(flag, fw + 40, H + 20), phase=(1 - e) * 2.6 + 0.6)
            art.alpha_composite(cloth, (fx - int((1 - e) * 40), -10))
            # Fade in from the left, keep it a backdrop rather than the main act
            ramp = Image.linear_gradient('L').rotate(90).resize((int(fw * 0.6), H))  # 0 left -> 255 right
            mask = Image.new('L', (W, H), 0)
            mask.paste(ramp, (fx, 0))
            mask.paste(255, (fx + ramp.width, 0, W, H))
            strength = 0.78 * min(1.0, e * 1.3)
            art.putalpha(ImageChops.multiply(art.getchannel('A'), mask.point(lambda v: int(v * strength))))
            # Darken the bottom so the info line and the logo stay readable on bright flags
            shade = Image.linear_gradient('L').resize((W, H)).point(lambda v: int(v * 0.55))
            dim = Image.new('RGBA', (W, H), (0, 0, 0, 255))
            dim.putalpha(ImageChops.multiply(shade, mask.point(lambda v: int(v * strength))))
            art.alpha_composite(dim)
        else:
            glow = Image.new('RGBA', (W, H), th.accent[:3] + (255,))
            mask = Image.new('L', (W, H), 0)
            mask.paste(Image.linear_gradient('L').rotate(90).resize((fw, H)), (fx, 0))
            glow.putalpha(mask.point(lambda v: int(v * 0.3 * e)))
            art.alpha_composite(glow)
        art.putalpha(ImageChops.multiply(art.getchannel('A'), shape))
        img.alpha_composite(art)

        # Twin slashes where the flag begins, sliding in with it
        sx = fx + 6 - int((1 - e) * 60)
        for off, col, a in ((0, th.accent, 255), (22, th.accent2, 230)):
            polygon(img, [(sx + off + 46, 0), (sx + off + 58, 0), (sx + off + 12, H), (sx + off, H)],
                    col[:3] + (int(a * min(1.0, e * 1.5)),))
        polygon(img, [(sx + 78, 0), (sx + 82, 0), (sx + 36, H), (sx + 32, H)], (255, 255, 255, int(70 * e)))

        # Text on the left
        label = (g.get('label') or 'RACE').upper()
        f_lab = font(17, 'bold_cond')
        lw = int(text_w(f_lab, label) + 22)
        round_rect(img, (x, 24, x + lw, 50), 4, th.accent)
        text_vcenter(img, x + lw / 2, 38, label, f_lab, contrast_text(th.accent), 'm')
        sub = '  ·  '.join(s_.upper() for s_ in (champ.get('season'), champ.get('round')) if s_)
        if sub:
            text_vcenter(img, x + lw + 12, 38, sub, font(18, 'semibold_cond'), th.muted)
        if name:
            ImageDraw.Draw(img).text((x, 62), name, font=f_name, fill=th.accent2)
        # Soft shadow under the track name so it holds over the flag
        tshadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(tshadow).text((x + 2, 98), track, font=f_track, fill=(0, 0, 0, 170))
        img.alpha_composite(tshadow.filter(ImageFilter.GaussianBlur(4)))
        ImageDraw.Draw(img).text((x, 94), track, font=f_track, fill=th.text)
        round_rect(img, (x, 186, x + 40, 189), 1, th.accent2)
        text_vcenter(img, x + 52, 188, ellipsize(info, f_info, fx - x - 62), f_info, th.text[:3] + (215,))
        if self.logo:
            lg = fit_image(self.logo, 190, 64)
            img.alpha_composite(lg, (W - lg.width - 26, H - lg.height - 20))
        self.sponsor_mark(img, W - 26, 40, 'title', 34, 'PRESENTED BY')
        round_outline(img, (0, 0, W, H), 12, (255, 255, 255, 40), 1)
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
        label = (g.get('label') or 'AI COMMENTATORS').upper()
        f_head = font(24, 'bold_cond')
        text_vcenter(img, 20, head_h / 2 + 1, label, f_head, fg)
        # LIVE tag on the right
        f_live = font(16, 'bold_cond')
        lw = int(text_w(f_live, 'LIVE') + 30)
        lx = W - 16 - lw
        round_rect(img, (lx, head_h / 2 - 12, lx + lw, head_h / 2 + 12), 12, (0, 0, 0, 90))
        round_rect(img, (lx + 9, head_h / 2 - 4, lx + 17, head_h / 2 + 4), 4, (255, 255, 255, 255))
        text_vcenter(img, lx + 22, head_h / 2 + 1, 'LIVE', f_live, (255, 255, 255, 255))
        round_rect(img, (0, head_h, W, head_h + 3), 0, th.accent2)

        n = len(cols)
        # anim runs over the whole time the card is up: the columns reveal in the first 1.4 s, then each
        # commentator's waveform takes a turn "talking" while the others idle
        secs = anim * float(d.get('anim_dur') or 1.4)
        reveal = min(1.0, secs / 1.4)
        talker = int(max(0.0, secs - 1.0) / 1.6) % n
        for i, (name, first, last, lines) in enumerate(cols):
            # Each column fades and rises in, one after another
            p = max(0.0, min(1.0, reveal * (n + 1.5) - i))
            e = 1 - (1 - p) ** 3
            if e <= 0:
                continue
            col = Image.new('RGBA', (col_w, body_h), (0, 0, 0, 0))
            level = 1.0 if i == talker or secs < 1.0 else 0.3
            self._commentator_column(col, name, first, last, lines, pad, mono, f_first, f_last, f_bio, secs, i, level)
            if e < 1:
                col.putalpha(col.getchannel('A').point(lambda v, e=e: int(v * e)))
            cx, cy = (i % per_row) * col_w, head_h + (i // per_row) * body_h + int((1 - e) * 14)
            img.alpha_composite(col, (cx, cy))
            if i % per_row:
                round_rect(img, (cx, head_h + 18, cx + 1, head_h + body_h - 14), 0, (255, 255, 255, 34))
        round_outline(img, (0, 0, W, H), 10, (255, 255, 255, 40), 1)
        return img

    def _commentator_column(self, col, name, first, last, lines, pad, mono, f_first, f_last, f_bio, secs, idx, level):
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
        # Voice waveform under the name: live while this commentator talks, idling otherwise
        self._waveform(col, tx, y + mono + 4, min(avail, 170), 22, secs, idx, level)
        self.text_lines(col, x, y + mono + 28, lines, f_bio, th.muted, 23)

    def _waveform(self, img, x, cy, w, h, secs, seed, level=1.0):
        import math
        bars, gap = 16, 4
        bw = max(2, (w - gap * (bars - 1)) / bars)
        for b in range(bars):
            base = 0.35 + 0.65 * abs(math.sin(b * 1.7 + seed * 2.3)) * (0.6 + 0.4 * math.sin(b * 0.45))
            wobble = 0.35 + 0.65 * abs(math.sin(secs * 7.5 + b * 0.8 + seed * 1.9) * math.sin(secs * 3.1 + b * 0.3))
            amp = base * (0.18 + 0.82 * wobble * level)
            bh = max(2, h * amp)
            bx = x + b * (bw + gap)
            fade = int(255 * (1 - 0.55 * b / bars) * (0.55 + 0.45 * level))
            round_rect(img, (bx, cy - bh / 2, bx + bw, cy + bh / 2), bw / 2, self.th.accent2[:3] + (fade,))

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
        used = max(self.name_block_w(name, 400, 36, 20), self.lines_w(lines, f) + self.sponsor_w('timing', 20, 'POWERED BY')
                   + 20, 180)
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
        self.sponsor_mark(img, W - self.badge_w(badge) - 16, top + panel_h - 20, 'timing', 20)
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

    def _checker_cloth(self, w, h, phase):
        cell = max(8, h // 6)
        cloth = Image.new('RGBA', (w + 40, h + 20), (0, 0, 0, 0))
        self._checker(cloth, 0, 0, cloth.width, cloth.height, cell)
        return wave_cloth(cloth, phase=phase, amp=h * 0.06, wavelength=w * 0.9, shade=0.4).crop((0, 10, w, h + 10))

    def card_chequered(self, g, anim=1.0):
        """Top-centre finish banner: waving chequered flags either side of the winner's name, a sweep of
        light across it, and the title sponsor if there is one. anim 0..1 over its time on screen."""
        th = self.th
        name = g.get('driver')
        anim = 1.0 if anim is None else anim
        e = ease_out(min(1.0, anim * 3))
        H, flag_w = 150, 170
        f_head = font(24, 'bold_cond')
        head = (g.get('label') or 'CHEQUERED FLAG').upper()
        if name:
            m = self.meta(name)
            parts = m['display'].split()
            first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', m['display'])
            title = last.upper()
            sub = f"{first.upper() + ' ' if first else ''}WINS AT {gcfg.clean_track_name(self.race.track).upper()}"
        else:
            title, sub = 'RACE COMPLETE', gcfg.clean_track_name(self.race.track).upper()
        f_title = fit_font(title, 640, 72, 'bold')
        f_sub = font(22, 'semibold_cond')
        flag = self.flag_img(self.meta(name).get('country'), 34) if name else None
        badge = self.badge(name, 70) if name else None
        mid_w = max(text_w(f_title, title) + (flag.width + 18 if flag else 0), text_w(f_sub, sub),
                    text_w(f_head, head) + 60) + (badge.width + 28 if badge else 0)
        W = int(flag_w * 2 + mid_w + 80)
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, H), 10, th.panel_solid)
        phase = anim * 14
        shape = Image.new('L', (W, H), 0)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, W - 1, H - 1), radius=10, fill=255)
        for side in (0, 1):
            cloth = self._checker_cloth(flag_w, H, phase + side * 1.3)
            ramp = Image.linear_gradient('L').rotate(90 if side else -90).resize((flag_w, H))
            cloth.putalpha(ImageChops.multiply(cloth.getchannel('A'), ramp.point(lambda v: int(v * 0.95))))
            layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            layer.alpha_composite(cloth, (0 if side == 0 else W - flag_w, 0))
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'), shape))
            img.alpha_composite(layer)
        cx = W / 2
        # Header pill
        hw = text_w(f_head, head) + 36
        round_rect(img, (cx - hw / 2, 12, cx + hw / 2, 42), 15, (255, 255, 255, 255))
        text_vcenter(img, cx, 28, head, f_head, th.dark_text, 'm')
        # Winner, rising in
        dy = int((1 - e) * 18)
        tw = text_w(f_title, title) + (flag.width + 18 if flag else 0)
        x = cx - tw / 2 - (badge.width + 28 if badge else 0) / 2
        if flag:
            img.alpha_composite(flag, (int(x), int(84 + dy - flag.height / 2)))
            x += flag.width + 18
        text_vcenter(img, x, 84 + dy, title, f_title, th.text[:3] + (int(255 * e),))
        if badge:
            img.alpha_composite(badge, (int(x + text_w(f_title, title) + 28), int(84 - badge.height / 2)))
        text_vcenter(img, cx, 128, sub, f_sub, th.accent2[:3] + (int(255 * e),), 'm')
        round_rect(img, (flag_w, H - 4, W - flag_w, H), 0, th.accent2)
        if 0.15 < anim < 0.6:
            sx = -200 + (anim - 0.15) / 0.45 * (W + 400)
            mask = Image.new('L', (W, H), 0)
            ImageDraw.Draw(mask).polygon([(sx, 0), (sx + 80, 0), (sx + 30, H), (sx - 50, H)], fill=90)
            mask = ImageChops.multiply(mask.filter(ImageFilter.GaussianBlur(10)), img.getchannel('A'))
            light = Image.new('RGBA', (W, H), (255, 255, 255, 255))
            light.putalpha(mask)
            img.alpha_composite(light)
        round_outline(img, (0, 0, W, H), 10, (255, 255, 255, 60), 1)
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
        right = W - 24
        if self.logo:
            lg = fit_image(self.logo, 130, 54)
            img.alpha_composite(lg, (W - lg.width - 24, int(head_h / 2 - lg.height / 2)))
            right -= lg.width + 24
        self.sponsor_mark(img, right, head_h / 2, 'title', 34, 'PRESENTED BY', fg)
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
            flag = self.flag_img(m.get('country'), int(row_h * 0.5))
            if flag:
                img.alpha_composite(flag, (nx, int(cy - flag.height / 2)))
            nx += int(row_h * 0.75) + 12
            if m.get('number'):
                text_vcenter(img, nx, cy, f"#{m['number']}", fsmall, th.muted)
                nx += 52
            text_vcenter(img, nx, cy + 1, ellipsize(m['display'].upper(), fname, 500 - nx), fname, th.text)
            tx = 520
            logo = self.badge(name, row_h - 10)
            if logo is not None:
                logo = fit_image(logo, 64, row_h - 10)
                img.alpha_composite(logo, (tx, int(cy - logo.height / 2)))
                tx += 76
            if m.get('team'):
                text_vcenter(img, tx, cy, ellipsize(m['team'], fsmall, 720 - tx), fsmall, th.muted)
            delta = r.get('gained')
            if delta is not None:
                self.delta(img, 760, cy, delta, 20)
            stops = r.get('stops')
            if stops is not None:
                text_vcenter(img, W - 30, cy, f"{stops} STOP{'S' if stops != 1 else ''}", fsmall, th.muted, 'r')
            y += row_h
        return img


    # ------------------------------------------------------------------ data-driven cards
    def card_gap_tracker(self, g, anim=1.0):
        """Battle forecast: the car behind closing on (or dropping away from) the car ahead. The interval
        counts from where it was to where it is now while the two markers slide together or apart."""
        th = self.th
        d = g.get('data', {})
        behind, ahead = g['driver'], g.get('driver2')
        if not ahead:
            return None
        anim = 1.0 if anim is None else anim
        e = ease_out(anim)
        closing = d.get('mode', 'closing') == 'closing'
        gap = d['gap_from'] + (d['gap_now'] - d['gap_from']) * e
        W, panel_h = 920, 158
        img, top = self.card_canvas(W, panel_h, (g.get('title') or ('BATTLE FORECAST' if closing else 'PULLING AWAY')))
        cx = W / 2
        green, red = th.green, th.red
        # Drivers either side: the car behind on the left, the car ahead on the right
        for side, name, pos in ((0, behind, d.get('pos_behind')), (1, ahead, d.get('pos_ahead'))):
            col = self.driver_color(name)
            if side == 0:
                round_rect(img, (20, top + 18, 25, top + 70), 2, col)
                self.name_with_flag(img, 38, top + 34, name, 34, max_w=cx - 200)
                sub = f"CHASING P{d.get('pos_ahead')}" if closing else f"P{pos}"
                text_vcenter(img, 38, top + 60, sub, font(19, 'semibold_cond'), th.muted)
            else:
                round_rect(img, (W - 25, top + 18, W - 20, top + 70), 2, col)
                self.name_with_flag(img, W - 38, top + 34, name, 34, anchor='r', max_w=cx - 200)
                sub = f"P{pos}" if closing else f"P{pos}  ·  EXTENDING"
                text_vcenter(img, W - 38, top + 60, sub, font(19, 'semibold_cond'), th.muted, 'r')
        # Forecast box
        bx0, bx1 = cx - 165, cx + 165
        round_rect(img, (bx0, top + 12, bx1, top + 84), 6, th.panel_hi)
        round_outline(img, (bx0, top + 12, bx1, top + 84), 6, (th.accent2 if closing else red)[:3] + (220,), 2)
        laps = d.get('laps_to')
        if closing:
            head = 'STRIKING DISTANCE' if laps else 'WITHIN STRIKING DISTANCE'
            big = (f"IN {laps} LAP{'S' if laps != 1 else ''}" if laps else 'NOW')
        else:
            head, big = 'GAP GROWING', f"+{abs(d.get('rate', 0)):.2f}s / LAP"
        text_vcenter(img, cx, top + 28, head, font(18, 'bold_cond'), th.accent2 if closing else red, 'm')
        text_vcenter(img, cx, top + 60, big, fit_font(big, bx1 - bx0 - 20, 38, 'bold'), th.text, 'm')
        # Gap strip: two markers sliding together/apart with the interval between them
        y = top + 122
        x0, x1 = 60, W - 60
        span = max(d['gap_from'], d['gap_now'], 0.5) * 1.15
        round_rect(img, (x0, y - 2, x1, y + 2), 2, (255, 255, 255, 40))
        xa = x1 - 20
        xb = xa - (x1 - x0 - 40) * min(1.0, gap / span)
        round_rect(img, (xb, y - 3, xa, y + 3), 3, (green if closing else red)[:3] + (180,))
        for x, name in ((xb, behind), (xa, ahead)):
            round_rect(img, (x - 9, y - 9, x + 9, y + 9), 9, self.driver_color(name))
            round_outline(img, (x - 9, y - 9, x + 9, y + 9), 9, (255, 255, 255, 230), 2)
        val = f"{gap:.3f}s"
        text_vcenter(img, (xa + xb) / 2, y - 22, val, font(24, 'bold'), th.text, 'm')
        rate = abs(d.get('rate', 0))
        trend = f"{rate:.2f}s PER LAP"
        ty = top + panel_h - 14
        if closing:  # down arrow: the gap is shrinking
            polygon(img, [(38, ty - 5), (50, ty - 5), (44, ty + 5)], green)
        else:
            polygon(img, [(38, ty + 5), (50, ty + 5), (44, ty - 5)], red)
        text_vcenter(img, 58, ty, ('CLOSING ' if closing else 'LOSING ') + trend, font(16, 'semibold_cond'), th.muted)
        self.sponsor_mark(img, W - 24, top + panel_h - 14, 'timing', 22)
        return img

    def card_pit_timer(self, g, anim=1.0):
        """Pit stop with the stationary time ticking up while the car is in its box, then held in green."""
        th = self.th
        d = g.get('data', {})
        name = g['driver']
        anim = 1.0 if anim is None else anim
        secs = d.get('stationary', 0) * anim
        done = anim >= 1.0
        panel_h = 98
        f_team = font(19, 'semibold_cond')
        team = self.meta(name).get('team') or ''
        used = max(self.name_with_flag_w(name, 36), text_w(f_team, team), 200)
        badge = self.card_badge(name, panel_h, 70)
        timer_w = 190
        W = int(96 + 18 + used + 30 + self.badge_w(badge) + timer_w + 12)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'PIT STOP').upper())
        self.pos_number(img, 52, top + panel_h / 2, d.get('pos'), 56)
        round_rect(img, (96, top + 14, 101, top + panel_h - 14), 2, self.driver_color(name))
        self.name_with_flag(img, 116, top + 36, name, 36)
        if team:
            text_vcenter(img, 116, top + 70, team, f_team, th.muted)
        if badge is not None:
            img.alpha_composite(badge, (int(W - timer_w - 12 - badge.width - 12),
                                        int(top + (panel_h - badge.height) / 2)))
        tx0 = W - timer_w - 10
        box = th.green if done else th.panel_hi
        round_rect(img, (tx0, top + 10, W - 10, top + panel_h - 10), 6, box)
        fg = contrast_text(box) if done else th.text
        text_vcenter(img, (tx0 + W - 10) / 2, top + 28, 'STATIONARY', font(15, 'bold_cond'),
                     fg if done else th.muted, 'm')
        text_vcenter(img, (tx0 + W - 10) / 2, top + 62, f"{secs:.1f}s", font(40, 'bold'), fg, 'm')
        return img

    def card_gaps(self, g):
        """The whole field on one line by gap to the leader, with the packs it has split into."""
        th = self.th
        d = g.get('data', {})
        rows = d.get('rows') or []
        if len(rows) < 2:
            return None
        W, panel_h = 1180, 176
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'GAPS ON TRACK').upper())
        x0, x1 = 60, W - 60
        cap = max(r[1] for r in rows) or 1.0
        xof = lambda gap: x0 + (x1 - x0) * min(1.0, gap / cap)
        y = top + 92
        round_rect(img, (x0 - 20, y - 2, x1 + 20, y + 2), 2, (255, 255, 255, 50))
        # Packs underneath
        for i, pack in enumerate(d.get('packs') or []):
            gaps = [gp for n, gp in rows if n in pack]
            a, b = xof(min(gaps)) - 16, xof(max(gaps)) + 16
            round_rect(img, (a, y + 18, b, y + 22), 2, th.accent2 if i == 0 else (255, 255, 255, 120))
            label = ('LEADING GROUP' if i == 0 else f"GROUP {i + 1}") if len(pack) > 1 else ('LEADER' if i == 0 else 'ALONE')
            label += f"  ·  {len(pack)}" if len(pack) > 1 else ''
            text_vcenter(img, (a + b) / 2, y + 40, label, font(16, 'bold_cond'),
                         th.accent2 if i == 0 else th.muted, 'm')
        # Markers, labels staggered above the line so close cars stay readable
        f = font(19, 'bold_cond')
        lanes = [-1e9, -1e9, -1e9]
        for name, gap in sorted(rows, key=lambda r: r[1]):
            x = xof(gap)
            round_rect(img, (x - 8, y - 8, x + 8, y + 8), 8, self.driver_color(name))
            round_outline(img, (x - 8, y - 8, x + 8, y + 8), 8, (255, 255, 255, 220), 2)
            label = self.meta(name)['short'].upper()
            lw = text_w(f, label)
            lane = next((i for i, end in enumerate(lanes) if x - lw / 2 > end + 8), len(lanes) - 1)
            lanes[lane] = x + lw / 2
            ly = y - 26 - lane * 22
            round_rect(img, (x - 1, ly + 9, x + 1, y - 9), 0, (255, 255, 255, 70))
            text_vcenter(img, x, ly, label, f, th.text, 'm')
        text_vcenter(img, W - 24, top + panel_h - 14, f"LEADER TO LAST  {cap:.1f}s", font(16, 'semibold_cond'),
                     th.muted, 'r')
        return img

    def card_insight(self, g):
        """Track guide / strategy / analysis: a bold line and one short line under it."""
        th = self.th
        title = (g.get('title') or '').upper()
        text = ' '.join((g.get('text') or '').split())
        f_title, f_text = font(32, 'bold_cond'), font(20, 'regular')
        title = wrap(title, f_title, 860, 1)[0] if title else ''
        lines = wrap(text, f_text, 860, 2) if text else []
        panel_h = 22 + 36 + (len(lines) * 26 + 6 if lines else 0) + 14
        W = int(48 + max(text_w(f_title, title), self.lines_w(lines, f_text), 260))
        img, top = self.card_canvas(W, panel_h, (g.get('label') or 'ANALYSIS').upper(), stripe=th.accent2)
        ImageDraw.Draw(img).text((26, top + 18), title, font=f_title, fill=th.text)
        self.text_lines(img, 26, top + 62, lines, f_text, th.muted, 26)
        return img

    def card_sponsor(self, g):
        """A sponsor's moment on screen: the caption and their logo (or name), nothing else."""
        th = self.th
        kind = g.get('data', {}).get('kind', 'title')
        sp = self.sponsor(kind)
        if not sp:
            return None
        name, logo = sp
        caption = (g.get('title') or {'title': 'BROUGHT TO YOU BY', 'timing': 'OFFICIAL TIMING PARTNER'}
                   .get(kind, 'OFFICIAL PARTNER')).upper()
        f_cap = font(20, 'bold_cond')
        lg = fit_image(logo, 420, 74) if logo else None
        f_name = font(48, 'bold_cond')
        body_w = lg.width if lg else text_w(f_name, name.upper())
        W = int(max(body_w, text_w(f_cap, caption)) + 70)
        panel_h = 128
        img, top = self.card_canvas(W, panel_h, None)
        text_vcenter(img, W / 2, top + 22, caption, f_cap, th.muted, 'm')
        if lg:
            img.alpha_composite(lg, (int((W - lg.width) / 2), int(top + 76 - lg.height / 2)))
        else:
            text_vcenter(img, W / 2, top + 76, name.upper(), f_name, th.text, 'm')
        round_rect(img, (W / 2 - 30, top + panel_h - 6, W / 2 + 30, top + panel_h - 3), 1, th.accent2)
        return img


def race_time_label(race, t_video):
    gf = race.green_flag_video
    return fmt_clock(t_video - gf) if gf is not None and t_video >= gf else ''
