"""Broadcast graphic templates, drawn with Pillow in 1920x1080 space."""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import config as gcfg
from .drawing import (add_shadow, cover_image, ease_out, ellipsize, fit_font, fit_image, font, h_gradient, load_image,
                      polygon, rgba, round_outline, round_rect, text_vcenter, text_w, wave_cloth,
                      wrap)
from .race_data import fmt_clock

LABEL_H = 30
TAG_SLANT = 12   # the slanted end of a card's title tag
CHEV_W = 24      # the chevrons at the start of a title tag
GRAIN_ALPHA = 3  # the faint diagonal grain on the cards' near-black bar
CHEVRON_ALPHA = 20  # the big chevrons fading in from the left of the bar
CAR_SINK = 26    # how far a car standing on a card comes down into it
MAX_TEXT_W = 580  # widest a text card's lines run; longer text adds lines instead
SPONSOR_TAB_H = 58  # sponsor tab on the top right of a card: taller than the label so the logo reads
MAX_PASSED = 4  # most passed drivers one position-change card stacks
# How the gaps card fits its space, tried in turn: marker radius, links (shortest, growth per root second,
# longest) and the most packs shown
_L1, _L2, _L3, _L4, _L5 = (92, 32, 236), (72, 22, 172), (64, 15, 130), (60, 12, 112), (56, 10, 96)
GAPS_FITS = ((22, _L1, 8), (22, _L2, 8), (22, _L3, 8), (19, _L3, 8), (19, _L4, 8), (22, _L4, 6), (19, _L4, 6),
             (16, _L4, 8), (16, _L5, 6), (19, _L5, 5), (16, _L5, 5), (14, _L5, 5), (14, _L5, 4), (14, _L5, 3))
# The results: the background fades up over this long, the table rises in over this long, and a car's row
# flashes for this long as it crosses the line
RESULTS_FADE_IN, RESULTS_CARD_IN, RESULTS_FLASH = 1.0, 0.7, 1.4
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
        # The near-black bar the pop-up cards are drawn on (the battle card's look): nearly opaque whatever the
        # panel opacity, so the text never fights the picture behind
        self.bar = rgba(t['panel'], 238 / 255)
        # Paragraph text on the bar: near-white, so sentences read at a glance (muted grey is for short labels)
        self.body = (228, 232, 240, 255)
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
        race.timing.group_classes(cfg['layout']['tower'].get('class_groups'))
        self.tower = None
        # RaceRoom replays show no leaderboard, so it always gets the tower; AMS2 can wrap its own instead
        if cfg['layout']['tower'].get('enabled', True) or not race.has_hud:
            from .tower import TimingTower  # tower imports this module
            self.tower = TimingTower(self)

    # ------------------------------------------------------------------ helpers
    def left_clear_x(self):
        """The first x right of the leaderboard on the left (the timing tower, or the AMS2 leaderboard frame
        with the tower off), or 0 when nothing is there. Pop-ups never reach left of it."""
        if self.tower:
            return self.tower.x + self.tower.w + 16
        lb = self.cfg['layout'].get('leaderboard') or {}
        if lb.get('enabled') and self.race.has_hud:
            return lb['x'] + lb['w'] + 16
        return 0

    def popup_max_w(self, screen_w=1920, right_margin=24):
        """Widest a pop-up can be and still sit clear of the leaderboard (see renderer: pop-ups shift right of it)."""
        return screen_w - max(self.left_clear_x(), right_margin) - right_margin

    def meta(self, name):
        return gcfg.driver_meta(self.cfg, name)

    def driver_color(self, name):
        """The Drivers tab colour, else the brand colour of their make (BMW blue, Renault yellow), else the
        theme accent."""
        m = self.meta(name)
        c = m.get('color')
        if not c and name:
            entry = self.race.timing.entry(name) if getattr(self.race, 'timing', None) else {}
            c = gcfg.make_color(gcfg.driver_make(m, entry))
        return rgba(c) if c else self.th.accent

    def _image_path(self, name):
        return gcfg.resolve_path(self.meta(name).get('car_image')) if name else ''

    def car(self, name, max_w, max_h):
        """The driver's car image (from CarPreviews or a sim's livery folder), which may stand out of its card;
        None without one (or when the car image is really a logo, see config.car_image_is_car)."""
        path = self._image_path(name)
        if not path or not gcfg.car_image_is_car(self.meta(name)):
            return None
        return fit_image(load_image(path), max_w, max_h)

    def logo_path(self, name):
        """The driver's team/make logo: the Drivers tab Logo, else a car image that is really a logo, or ''."""
        logo = gcfg.resolve_path(self.meta(name).get('logo')) if name else ''
        if logo and os.path.exists(logo):
            return logo
        path = self._image_path(name)
        if path and not gcfg.car_image_is_car(self.meta(name)) and os.path.exists(path):
            return path
        return ''

    def badge(self, name, h):
        """Team/make logo as it is, sized to sit inside a card (never over its edge).
        No tile or colour strip behind it."""
        path = self.logo_path(name)
        logo = load_image(path) if path else None
        if logo is None:
            return None
        h = int(h)
        return fit_image(logo, int(h * 1.5), h)

    @staticmethod
    def badge_w(badge):
        """Room the badge takes at the right-hand end of a card; a car image goes to the left of it."""
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

    @staticmethod
    def tag_w(label):
        """Width label_tab() takes for this label."""
        return int(11 + CHEV_W + 10 + text_w(font(17, 'bold_cond'), label) + 14 + TAG_SLANT)

    def label_tab(self, img, x, y, label, chevron=None):
        """Title tab (LABEL_H tall from y) joined to the top left of a card's bar, in the bar's near-black with a
        slanted end: chevrons (white, or the chevron colour: green for a position change, amber for race
        control) and the label in white."""
        w = self.tag_w(label)
        polygon(img, [(x, y + 2), (x + w, y + 2), (x + w - TAG_SLANT, y + LABEL_H), (x, y + LABEL_H)], self.th.bar)
        self.grain(img, (x, y + 2, x + w, y + LABEL_H))
        cy = y + LABEL_H / 2 + 1
        self.chevrons(img, x + 11, cy, 14, chevron or self.th.text)
        text_vcenter(img, x + 11 + CHEV_W + 10, cy + 1, label, font(17, 'bold_cond'), self.th.text)
        return w

    def card_canvas(self, w, panel_h, label, top_extra=0, chevron=None, wash=None, sponsor=None, chevrons='l'):
        """Blank card: the near-black bar with square top corners, washed from the left in the `wash` colour, the
        label in a title tab joined to its top left, and the `sponsor` kind's logo in a tab on the top right when
        that sponsor is set. chevrons=None leaves the big chevrons out, for cards that are mostly paragraphs.
        Returns (img, panel_top)."""
        tab = self.sponsor_tab(sponsor) if sponsor else None
        top = max(LABEL_H if label else 0, top_extra, tab.height if tab else 0)
        img = Image.new('RGBA', (int(w), int(top + panel_h)), (0, 0, 0, 0))
        self.bar(img, (0, top, w, top + panel_h), chevrons=chevrons)
        if wash:
            self.colour_wash(img, 0, top, w, panel_h, wash)
        if label:
            self.label_tab(img, 0, top - LABEL_H, label, chevron)
        if tab:
            img.alpha_composite(tab, (int(w - tab.width), int(top - tab.height)))
        return img, top

    def bar(self, img, box, corners=(False, False, True, True), chevrons='l'):
        """The cards' near-black bar over box, with its faint grain and big faint chevrons fading in from the left
        (`chevrons` 'l'; 'both' also from the right, pointing back; None for none). Drawn first, so the texture
        stays under the text."""
        round_rect(img, box, 3, self.th.bar, corners=corners)
        self.grain(img, box)
        if chevrons:
            self.chevron_underlay(img, box, chevrons)

    @staticmethod
    def chevron_underlay(img, box, side='l', alpha=CHEVRON_ALPHA):
        """Big right-pointing chevrons across the left of box, fading out by its middle like the colour wash, only
        where img is drawn. side 'both' adds a mirrored set coming in from the right."""
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        w, h = x1 - x0, y1 - y0
        if w < 2 or h < 2:
            return
        ss = 3  # drawn larger and scaled down for smooth edges
        size = min(h, 150)                 # tall cards keep chevrons of a bar's size, repeated down the card
        t = max(9.0, size * 0.2)           # stroke thickness
        depth = size * 0.32                # how far each point sticks out
        step = t * 2.3
        reach = min(w * 0.6, 420)          # they fade out by here
        mask = Image.new('L', (w * ss, h * ss), 0)
        d = ImageDraw.Draw(mask)
        rows = max(1, int(round(h / size)))
        for r in range(rows):
            top = h * r / rows
            cy, half = top + h / rows / 2, h / rows / 2 * 1.15
            x = -depth
            while x < reach:
                pts = [(x, cy - half), (x + t, cy - half), (x + t + depth, cy), (x + t, cy + half), (x, cy + half),
                       (x + depth, cy)]
                d.polygon([(px * ss, py * ss) for px, py in pts], fill=255)
                x += step
        mask = mask.resize((w, h), Image.LANCZOS)
        fade = Image.new('L', (w, h), 0)
        ramp = Image.linear_gradient('L').rotate(-90).resize((int(reach), h))  # 255 at the left -> 0
        fade.paste(ramp, (0, 0))
        mask = ImageChops.multiply(mask, fade)
        if side == 'both':
            mask = ImageChops.lighter(mask, mask.transpose(Image.FLIP_LEFT_RIGHT))
        mask = mask.point(lambda v: v * alpha // 255)
        mask = ImageChops.multiply(mask, img.crop((x0, y0, x1, y1)).getchannel('A'))
        layer = Image.new('RGBA', (w, h), (255, 255, 255, 255))
        layer.putalpha(mask)
        img.alpha_composite(layer, (x0, y0))

    @staticmethod
    def grain(img, box, alpha=GRAIN_ALPHA, step=8):
        """A faint diagonal grain over box, only where img is already drawn."""
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        w, h = x1 - x0, y1 - y0
        if w < 1 or h < 1:
            return
        layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for x in range(-h, w, step):
            d.line((x, h, x + h, 0), fill=(255, 255, 255, alpha), width=1)
        layer.putalpha(ImageChops.multiply(layer.getchannel('A'), img.crop((x0, y0, x1, y1)).getchannel('A')))
        img.alpha_composite(layer, (x0, y0))

    @staticmethod
    def chevrons(img, x, cy, h, col, n=3):
        """n chevrons pointing right from x, h tall, fading up from the first. Returns their width."""
        k = h / 24
        for i in range(n):
            px = x + i * 13 * k
            a = int(col[3] * (110 + 70 * i) / 250) if len(col) > 3 else 110 + 70 * i
            polygon(img, [(px, cy - 12 * k), (px + 6 * k, cy - 12 * k), (px + 15 * k, cy), (px + 6 * k, cy + 12 * k),
                          (px, cy + 12 * k), (px + 9 * k, cy)], col[:3] + (min(255, a),))
        return int((13 * (n - 1) + 15) * k)

    @staticmethod
    def car_on_top(img, car, cx, floor):
        """Draws a car image centred on cx with its wheels at y=floor, on a soft contact shadow (the shadow only
        where the card is)."""
        shadow = Image.new('L', img.size, 0)
        ImageDraw.Draw(shadow).ellipse((cx - car.width * 0.42, floor - 9, cx + car.width * 0.42, floor + 5), fill=150)
        shadow = ImageChops.multiply(shadow.filter(ImageFilter.GaussianBlur(7)), img.getchannel('A'))
        dark = Image.new('RGBA', img.size, (0, 0, 0, 255))
        dark.putalpha(shadow)
        img.alpha_composite(dark)
        img.alpha_composite(car, (int(cx - car.width / 2), int(floor - car.height)))

    @staticmethod
    def team_block(img, x, y, w, h, col, slant=16, side='l'):
        """Solid colour block (a position or value) with a slanted inner edge, '/' like the cards' other cuts.
        side 'l': the block's left edge is at x and its right edge slants; 'r': its right edge is at x and its left
        edge slants. Very dark colours are lifted to slate so the block shows. Returns the ink colour for it."""
        blk = col if sum(col[:3]) > 90 else (58, 64, 78, 255)
        if side == 'l':
            pts = [(x, y), (x + w + slant, y), (x + w, y + h), (x, y + h)]
        else:
            pts = [(x - w, y), (x, y), (x, y + h), (x - w - slant, y + h)]
        polygon(img, pts, blk)
        return contrast_text(blk)

    @staticmethod
    def colour_wash(img, x, y, w, h, col, flip=False):
        """A soft wash of col fading out to the right (to the left with flip)."""
        if w < 1 or h < 1:
            return
        # h_gradient puts its first colour on the right
        wash = h_gradient(int(w), int(h), col[:3] + (0,), col[:3] + (50,))
        if flip:
            wash = wash.transpose(Image.FLIP_LEFT_RIGHT)
        img.alpha_composite(wash, (int(x), int(y)))

    @staticmethod
    def logo_column(img, badge, x, w, y, h, low=False, divider='l'):
        """The team logo as it is, centred in a w-wide column from x with a thin slanted divider on its `divider`
        side. low puts it at the bottom of the column, clear of a car standing above."""
        dx = x if divider == 'l' else x + w - 10
        polygon(img, [(dx + 8, y + 14), (dx + 10, y + 14), (dx + 2, y + h - 14), (dx, y + h - 14)], (255, 255, 255, 60))
        if badge is not None:
            ly = y + h - 12 - badge.height if low else y + (h - badge.height) / 2
            off = 2 if divider == 'l' else -2
            img.alpha_composite(badge, (int(x + (w - badge.width) / 2 + off), int(ly)))

    def sponsor_plate(self, kind, w, h, corners=None):
        """w x h panel with the sponsor's logo (or name) fitted inside, or None. The panel is white behind a
        dark logo and dark behind a light one, so any logo reads."""
        sp = self.sponsor(kind)
        if not sp:
            return None
        name, logo = sp
        bg = self.th.panel_hi[:3] + (255,) if logo is not None and _is_light(logo) else (255, 255, 255, 255)
        plate = Image.new('RGBA', (int(w), int(h)), (0, 0, 0, 0))
        round_rect(plate, (0, 0, w, h), 6, bg, corners=corners)
        if logo:
            lg = fit_image(logo, w - 24, h - 12)
            plate.alpha_composite(lg, (int((w - lg.width) / 2), int((h - lg.height) / 2)))
        else:
            label = name.upper()
            text_vcenter(plate, w / 2, h / 2, label, fit_font(label, w - 20, int(h * 0.55), 'bold_cond'),
                         contrast_text(bg), 'm')
        return plate

    def sponsor_tab(self, kind):
        """The sponsor's logo on its own tab (rounded on top, sitting on a card's top right), or None."""
        sp = self.sponsor(kind)
        if not sp:
            return None
        name, logo = sp
        h = SPONSOR_TAB_H
        if logo:
            w = fit_image(logo, 260, h - 12).width + 32
        else:
            w = int(text_w(font(int(h * 0.55), 'bold_cond'), name.upper()) + 32)
        return self.sponsor_plate(kind, w, h, corners=(True, True, False, False))

    def name_block(self, img, x, y, name, max_w, big=40, small=24, color=None):
        """First name light above an uppercase bold surname (in color, white by default)."""
        display = self.meta(name)['display']
        parts = display.split()
        first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', display)
        if first:
            f1 = fit_font(first, max_w, small, 'regular')
            ImageDraw.Draw(img).text((x, y), first, font=f1, fill=self.th.text)
            y += small + 2
        f2 = fit_font(last.upper(), max_w, big, 'bold')
        ImageDraw.Draw(img).text((x, y), last.upper(), font=f2, fill=color or self.th.text)
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

    def sponsor_w(self, kind, h):
        sp = self.sponsor(kind)
        if not sp:
            return 0
        name, logo = sp
        return int(fit_image(logo, h * 4, h).width if logo else text_w(font(h * 0.8, 'bold_cond'), name.upper()))

    def sponsor_mark(self, img, right, cy, kind, h, color=None):
        """The sponsor's logo (or name without one), right-aligned at right, no caption. Returns the width used."""
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
    def text_card_w(title_w, text_w_, minimum):
        """Line width for a text card: the title's, or the text on one line when it fits in MAX_TEXT_W, else on
        about two, never past MAX_TEXT_W (longer text adds lines)."""
        body = text_w_ if text_w_ <= MAX_TEXT_W else text_w_ / 2 + 60
        return int(min(MAX_TEXT_W, max(title_w, body, minimum)))

    @staticmethod
    def lines_w(lines, f):
        return max([text_w(f, ln) for ln in lines] or [0])

    def meta_row(self, img, x, cy, name, max_w, show_flag=True):
        """#number, flag (the country code without one) and team on one line. show_flag=False leaves the flag
        out for graphics that already show it (the starting grid's card undertone)."""
        m = self.meta(name)
        cx = x
        if m.get('number'):
            cx += self.chip(img, cx, cy, f"#{m['number']}", self.th.text, size=17) + 8
        flag = self.flag_img(m.get('country'), 20)
        if flag and not show_flag:
            pass  # already shown elsewhere on the graphic
        elif flag:
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

    # ------------------------------------------------------------------ starting grid (full screen)
    def starting_grid(self, size, quali=None):
        """The full-screen starting grid scene (starting_grid.StartingGrid), made once."""
        if getattr(self, '_starting_grid', None) is None:
            from .starting_grid import StartingGrid
            self._starting_grid = StartingGrid(self, size, quali)
        return self._starting_grid

    def results_scene(self, size, g, local, t, backdrop_path):
        """The results: the classification table over the race, every place listed from the start and a car's
        name filling in, with a flash, the moment it crosses the line (until then its place is an empty slot, so
        nobody still racing is given away). As the last car crosses (g['backdrop_at']) a background fades up
        behind the table to hide the game's interface when the replay stops: the frame where the winner crossed
        the line, blurred and darkened under a slow push-in like the starting grid."""
        from .starting_grid import PUSH_IN
        W, H = size
        frame = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        since_bd = t - g.get('backdrop_at', g['time'])
        if since_bd >= 0:
            grid = self.starting_grid(size)  # its blurred backdrop and shading
            bd = grid.backdrop(backdrop_path)
            if bd is not None:
                z = PUSH_IN * max(0.0, min(1.0, since_bd / 60.0))
                cw, ch = int(W * (1 + z)), int(H * (1 + z))
                ox, oy = (bd.width - cw) // 2, (bd.height - ch) // 2
                frame = bd.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR)
            else:
                frame = grid.flat_backdrop().copy()
            frame.alpha_composite(grid.shade())
            if since_bd < RESULTS_FADE_IN:
                frame.putalpha(int(255 * ease_out(since_bd / RESULTS_FADE_IN)))
        rows = [dict(r, pending=r.get('at') is not None and t < r['at']) for r in g.get('data', {}).get('rows', [])]
        cache = self.__dict__.setdefault('_results_cards', {})
        key = (g.get('title'), tuple(r['pending'] for r in rows))
        if key not in cache:
            card = self.card_results({'title': g.get('title'), 'data': {'rows': rows}})
            cache[key] = add_shadow(card) if card is not None else None
        if cache[key] is not None:
            card, margin = cache[key]
            p_in = ease_out(min(1.0, local / RESULTS_CARD_IN))
            x, y = (W - card.width) // 2, int((H - card.height) // 2 + (1 - p_in) * 30)
            if p_in < 0.999:
                card = card.copy()
                card.putalpha(card.getchannel('A').point(lambda v: int(v * p_in)))
            frame.alpha_composite(card, (x, y))
            # A flash across the row of a car that has just crossed the line
            row_h = 42 if len(rows) <= 12 else 32
            for i, r in enumerate(rows):
                since = t - r['at'] if r.get('at') is not None else None
                if since is not None and 0 <= since < RESULTS_FLASH:
                    ry = y + margin + 92 + 8 + i * row_h
                    a = int(150 * (1 - since / RESULTS_FLASH))
                    round_rect(frame, (x + margin + 12, ry, x + card.width - margin - 12, ry + row_h), 4,
                               self.th.accent[:3] + (a,))
        return frame

    def grid_car(self, name, max_w, max_h):
        """The car on the starting grid: the Drivers tab car image when it is a car preview, else the preview in
        Graphics/CarPreviews that matches the driver (preferring one named after this race's car)."""
        car = self.car(name, max_w, max_h)
        if car is not None or self.meta(name).get('no_car'):
            # The Drivers tab car image set to (none): no car, not one matched by name
            return car
        if not hasattr(self, '_car_files'):
            self._car_files = gcfg.list_images(gcfg.CAR_PREVIEW_DIR)
        m = self.meta(name)
        # The car the logger saw on the grid (e.g. 'MINI Cooper JCW') and the Drivers tab make
        words = [m.get('make')] + (self.race.classes.get(name) or '').split()
        f = gcfg.match_grid_car((name, m.get('display'), m.get('short')), words, self._car_files)
        return fit_image(load_image(os.path.join(gcfg.CAR_PREVIEW_DIR, f)), max_w, max_h) if f else None

    def card_driver_card(self, g):
        """Compact driver ID card in the style of current F1/WEC packages. The car stands on a team-colour line
        along the top edge, with the title in a slanted tag to its left. Below that: a team-colour position block
        with a slanted cut, first name over SURNAME with the number, flag and team, and the team logo in its own
        column under the car. The caption wraps in a strip underneath, so the card grows down rather than out."""
        th = self.th
        name = g['driver']
        d = g.get('data', {})
        col = self.driver_color(name)
        body_h, slant, sink = 118, 16, CAR_SINK
        car = self.car(name, 280, 118)
        # The logo sits low in its column, clear of the car's wheels
        badge = self.badge(name, body_h - (sink if car else 0) - 20 if car else body_h - 30)
        tag = (g.get('title') or 'DRIVER').upper()
        tag_w = self.tag_w(tag)
        # Position block, then the name, then the logo column (as wide as the logo, at least as wide as the
        # car standing on it so the car sits over its own logo)
        pos_w = 98
        x0 = pos_w + slant + 14
        name_w = max(self.name_block_w(name, 330, 44, 22), self.meta_row_w(name, 330), 150)
        # Without a logo the car still gets a column of its own, so its wheels never land on the text
        logo_col = badge.width + 40 if badge is not None else int(car.width * 0.6) if car else 0
        W = x0 + int(name_w) + 24 + logo_col
        # The tag and the car share the top edge without touching
        W = max(W, tag_w + 24 + (car.width + 16 if car else 0), 440)
        f_text = font(20, 'regular')
        text = ' '.join((g.get('text') or '').split())
        # A short caption widens the card a little to stay on one line; a long one to keep to two
        if text and text_w(f_text, text) + 40 <= 640:
            W = max(W, int(text_w(f_text, text)) + 44)
        elif text:
            W = max(W, min(640, int(text_w(f_text, text) / 2) + 80))
        lines = wrap(text, f_text, W - 40, 99) if text else []
        cap_h = 18 + 26 * len(lines) if lines else 0
        top = max(LABEL_H, (car.height - sink) if car else 0)
        H = top + body_h + cap_h
        img = Image.new('RGBA', (int(W), int(H)), (0, 0, 0, 0))
        by = top  # body top
        # The bar, with a soft wash of the team colour from the left and a darker caption strip below
        self.bar(img, (0, by, W, by + body_h + cap_h))
        self.colour_wash(img, 0, by, W, body_h, col)
        if lines:
            round_rect(img, (0, by + body_h, W, by + body_h + cap_h), 3, (0, 0, 0, 90),
                       corners=(False, False, True, True))
            for i, ln in enumerate(lines):
                text_vcenter(img, 20, by + body_h + 22 + 26 * i, ln, f_text, (235, 238, 244, 255))
        # The title tab above the bar's left end
        self.label_tab(img, 0, top - LABEL_H, tag)
        # Position block: solid team colour with a slanted right edge
        ink = self.team_block(img, 0, by, pos_w, body_h, col, slant)
        gained = d.get('gained') not in (None, 0)
        self.pos_number(img, pos_w / 2 + 2, by + (44 if gained else body_h / 2), d.get('pos'), 64, ink)
        if gained:
            round_rect(img, (pos_w / 2 - 26, by + 78, pos_w / 2 + 30, by + 100), 3, (11, 14, 20, 210))
            self.delta(img, pos_w / 2 + 2, by + 89, d['gained'], 16)
        # First name over SURNAME, then number, flag and team
        has_first = len(self.meta(name)['display'].split()) > 1
        y = self.name_block(img, x0, by + (12 if has_first else 20), name, 330, 44, 22, self._bright(col))
        self.meta_row(img, x0, min(y + 16, by + body_h - 18), name, 330)
        # Logo column: a slanted divider, then the logo as it is, under the car
        if logo_col:
            self.logo_column(img, badge, W - logo_col, logo_col, by, body_h, low=car is not None)
        if car:
            # Centred over the logo column, wheels down in the card on a soft contact shadow
            cx = W - logo_col / 2 if logo_col >= car.width * 0.6 else W - 12 - car.width / 2
            cx = min(W - car.width / 2 - 4, max(tag_w + 16 + car.width / 2, cx))
            self.car_on_top(img, car, cx, by + sink)
        return img

    BATTLE_CARS = True  # cars standing on the battle card's ends

    @staticmethod
    def ordinal(n):
        """1 -> '1ST', 2 -> '2ND', 11 -> '11TH'."""
        n = int(n)
        suffix = 'TH' if 10 <= n % 100 <= 20 else {1: 'ST', 2: 'ND', 3: 'RD'}.get(n % 10, 'TH')
        return f"{n}{suffix}"

    def card_battle(self, g):
        """Battle for a position, F1 style: one slim dark bar, mirrored. The car ahead on the left, the car behind
        on the right, each as first name over SURNAME in their team colour with the team logo and name under it;
        in the middle, chevrons, BATTLE / FOR 5TH and the interval. With BATTLE_CARS each car stands on its end
        of the bar, the two facing each other."""
        th = self.th
        a, b = g['driver'], g.get('driver2')
        if not b:
            return None
        d = g.get('data', {})
        H, pad = 72, 22
        names = (a, b)
        cols = [self._bright(self.driver_color(n)) for n in names]
        f_first, f_last, f_team = font(16, 'semibold_cond'), font(32, 'bold_cond'), font(15, 'semibold_cond')
        logos = [self.badge(n, 21) for n in names]
        sides = []
        for name, logo in zip(names, logos):
            m = self.meta(name)
            parts = m['display'].split()
            first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', m['display'])
            team = (m.get('team') or '').upper()
            team_w = (logo.width + 7 if logo is not None else 0) + min(220, text_w(f_team, team))
            sides.append((first.upper(), last.upper(), team, max(text_w(f_first, first.upper()), text_w(f_last, last.upper()), team_w)))
        cars = [self.car(n, 180, 80) if self.BATTLE_CARS else None for n in names]
        side_w = int(max([s[3] for s in sides] + [c.width for c in cars if c is not None]) + pad + 26)
        # The middle: chevrons, the title on two lines and the interval
        title = (g.get('title') or '').upper()
        if title and ' FOR ' in title:
            t1, t2 = title.split(' FOR ', 1)
            t2 = 'FOR ' + t2
        elif title:
            t1, t2 = title, ''
        else:
            t1, t2 = 'BATTLE', f"FOR {self.ordinal(d['pos'])}" if d.get('pos') else ''
        f_title = font(24, 'bold_cond')
        title_w = max(text_w(f_title, t1), text_w(f_title, t2))
        gap = d.get('gap')
        f_gap = font(26, 'bold')
        gap_w = max(text_w(f_gap, gap), text_w(font(13, 'semibold_cond'), 'INTERVAL')) if gap else 0
        chev_w = 44
        centre_w = int(chev_w + 16 + title_w + (34 + gap_w if gap else 0) + 56)
        W = 2 * side_w + centre_w
        top = max([c.height - 2 for c in cars if c is not None] + [0])
        img = Image.new('RGBA', (W, int(top + H)), (0, 0, 0, 0))
        # The bar: near-black with a faint diagonal grain, each end washed in its team colour
        bar = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        self.bar(bar, (0, 0, W, H), corners=None, chevrons='both')
        self.colour_wash(bar, 0, 0, side_w * 1.3, H, cols[0])
        self.colour_wash(bar, W - side_w * 1.3, 0, side_w * 1.3, H, cols[1], flip=True)
        # The middle panel, a shade lighter, with slanted sides
        s = 14
        polygon(bar, [(side_w + s, 0), (W - side_w + s, 0), (W - side_w - s, H), (side_w - s, H)], (255, 255, 255, 12))
        bar.putalpha(ImageChops.multiply(bar.getchannel('A'), Image.new('L', (W, H), 255)))
        img.alpha_composite(bar, (0, int(top)))
        y0 = top
        for side, ((first, last, team, _), logo, col) in enumerate(zip(sides, logos, cols)):
            x, anchor = (pad, 'l') if side == 0 else (W - pad, 'r')
            if first:
                text_vcenter(img, x, y0 + 15, first, f_first, th.text, anchor)
            ly, ty = (y0 + 38, y0 + 60) if first else (y0 + 30, y0 + 56)
            text_vcenter(img, x, ly, last, fit_font(last, side_w - pad - 20, 32, 'bold_cond'), col, anchor)
            # Team logo as it is, then the team name
            tx = x if side == 0 else x - (logo.width + 7 if logo is not None else 0) - min(220, text_w(f_team, team))
            if logo is not None:
                img.alpha_composite(logo, (int(tx), int(ty - logo.height / 2)))
                tx += logo.width + 7
            if team:
                text_vcenter(img, tx, ty, ellipsize(team, f_team, 220), f_team, (255, 255, 255, 200))
        # Chevrons into the title, then the interval, as one group centred in the middle
        group = chev_w + 16 + title_w + (34 + gap_w if gap else 0)
        cx0 = side_w + (centre_w - group) / 2
        cy = y0 + H / 2
        self.chevrons(img, cx0, cy, 24, th.text)
        tx = cx0 + chev_w + 16
        if t2:
            text_vcenter(img, tx, cy - 13, t1, f_title, th.text)
            text_vcenter(img, tx, cy + 13, t2, f_title, th.text)
        else:
            text_vcenter(img, tx, cy, t1, f_title, th.text)
        if gap:
            gx = tx + title_w + 18
            round_rect(img, (gx, cy - 20, gx + 2, cy + 20), 0, (255, 255, 255, 50))
            text_vcenter(img, gx + 16, cy - 13, 'INTERVAL', font(13, 'semibold_cond'), th.muted)
            text_vcenter(img, gx + 16, cy + 11, gap, f_gap, th.accent2)
        for side, car in enumerate(cars):
            if car is None:
                continue
            if side == 1:  # the car behind faces back towards the car ahead
                car = car.transpose(Image.FLIP_LEFT_RIGHT)
            ccx = pad + car.width / 2 - 6 if side == 0 else W - pad - car.width / 2 + 6
            self.car_on_top(img, car, ccx, top + 3)
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
        """F1-style position change: boxed position numbers, and the overtaker's white row slides up over
        each passed driver's dark row in turn. Gains and losses show in coloured chips at the end of the rows, and
        each driver's team logo rides just right of their row, on the dark panel.

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
        row_w, row_h, gap, pad, num_w = 440, (54 if n <= 2 else 46), 5, 10, 56
        head_h = LABEL_H
        rows_h = row_h * (n + 1) + gap * n
        panel_h = pad * 2 + rows_h
        zone = self.row_logo_zone([name] + victims, row_h - 14)
        x0 = pad + num_w + 8
        title = (g.get('title') or 'POSITION CHANGE').upper()
        # 20 leaves room for the row lifting out
        W = int(max(x0 + row_w + zone + 20, self.tag_w(title) + 24))
        top = head_h
        img = Image.new('RGBA', (int(W), int(top + panel_h)), (0, 0, 0, 0))
        self.bar(img, (0, top, W, top + panel_h))
        self.label_tab(img, 0, top - head_h, title, th.green)
        slot_y = lambda s: top + pad + s * (row_h + gap)
        # Boxed position slots; the top one fills green as the overtaker arrives
        arrive = smooth(max(0.0, min(1.0, 1 - n * (1 - e))))
        f_pos = font(32 if row_h > 50 else 28, 'bold')
        base = (255, 255, 255, 26)
        for s in range(n + 1):
            y = slot_y(s)
            box = (pad, y, pad + num_w, y + row_h)
            if s == 0:
                fill = tuple(int(a + (b - a) * arrive) for a, b in zip(base, th.green[:3] + (255,)))
                col = tuple(int(a + (b - a) * arrive) for a, b in zip(th.text, th.dark_text))
            else:
                fill, col = base, th.text
            round_rect(img, box, 4, fill)
            self.pos_number(img, pad + num_w / 2, y + row_h / 2 + 1, pos + s, f_pos.size, col)
        chips = max(0.0, min(1.0, (swap - 0.8) / 0.2))
        # The overtaker climbs from the bottom slot to the top; each passed driver drops one slot
        # as it goes by, and the overtaker lifts out to the right while it passes over them
        ov_slot = n * (1 - e)
        for i, victim in enumerate(victims):
            drop = smooth(max(0.0, min(1.0, i + 1 - ov_slot)))
            row = self._overtake_row(victim, row_w, row_h, False, -1, chips, zone)
            img.alpha_composite(row, (x0, int(round(slot_y(i + drop)))))
        lift = int(round(16 * min(1.0, 4 * e * (1 - e) * 1.5)))
        ovr = self._overtake_row(name, row_w, row_h, True, n, chips, zone)
        img.alpha_composite(ovr, (x0 + lift, int(round(slot_y(ov_slot)))))
        return img

    def row_logo_zone(self, names, logo_h):
        """Room for a logo column right of a stack of driver rows: the widest of their logos plus a gap (0 when
        none has one), so the logos line up."""
        widths = [b.width for b in (self.badge(n, logo_h) for n in names) if b is not None]
        return max(widths) + 14 if widths else 0

    def _overtake_row(self, name, w, h, highlight, delta, chip_alpha, zone=0):
        """One driver's row: white with dark text for the overtaker, dark for the cars passed. Team colour on
        the left edge, flag, first name and SURNAME, and a green/red chip with the places gained or lost. With a
        zone, the team logo sits in that much room right of the row (on the card's dark panel, so a white logo
        still shows), moving with the row."""
        th = self.th
        row = Image.new('RGBA', (w + zone, h), (0, 0, 0, 0))
        if zone:
            logo = self.badge(name, h - 14)
            if logo is not None:
                row.alpha_composite(logo, (int(w + 14 + (zone - 14 - logo.width) / 2), int((h - logo.height) / 2)))
        if highlight:
            bg, fg, fg_first = (244, 246, 249, 255), th.dark_text, (70, 76, 90, 255)
        else:
            bg, fg, fg_first = tuple(int(c * 0.8) for c in th.panel_hi[:3]) + (255,), th.text, th.muted
        round_rect(row, (0, 0, w, h), 4, bg)
        team = self.driver_color(name)
        if highlight and contrast_text(team)[0] < 128:  # a light team colour would vanish on the white row
            team = tuple(int(c * 0.55) for c in team[:3]) + (255,)
        round_rect(row, (0, 0, 6, h), 0, team, corners=(True, False, False, True))
        display = self.meta(name)['display']
        parts = display.split()
        first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', display)
        x, cy = 20, h / 2 + 1
        flag = self.flag_img(self.meta(name).get('country'), 20)
        if flag:
            row.alpha_composite(flag, (x, int(cy - flag.height / 2)))
            x += flag.width + 12
        avail = w - x - 72
        if first:
            f1 = font(20, 'regular')
            text_vcenter(row, x, cy, ellipsize(first, f1, avail * 0.4), f1, fg_first)
            x += min(text_w(f1, first), avail * 0.4) + 10
        f2 = fit_font(last.upper(), w - x - 72, 30, 'bold')
        text_vcenter(row, x, cy, last.upper(), f2, fg)
        if chip_alpha > 0:
            col = th.green if delta > 0 else th.red
            a = int(255 * chip_alpha)
            round_rect(row, (w - 62, cy - 14, w - 10, cy + 14), 4, col[:3] + (a,))
            txt = contrast_text(col)
            self.delta(row, w - 36, cy, delta, 20, txt[:3] + (a,))
        return row

    def _overtake_single(self, g):
        """Position change without the cars it passed: the same green header and line as the full card, the new
        position in a green block with an up chevron, the driver's name with their team logo beside it, and
        who/where they passed."""
        th = self.th
        name, d = g['driver'], g.get('data', {})
        victim = g.get('driver2') or d.get('victim')
        sub = []
        if victim:
            sub.append(f"PASSES {self.meta(victim)['short'].upper()}")
        if d.get('corner'):
            sub.append(d['corner'].upper())
        f = font(19, 'semibold_cond')
        lines = wrap(g.get('text') or '  ·  '.join(sub), f, 400, 99)
        head_h, blk_w, slant = LABEL_H, 84, 14
        panel_h = 108 + 24 * max(0, len(lines) - 1)
        has_first = len(self.meta(name)['display'].split()) > 1
        badge = self.badge(name, 54 if has_first else 44)
        title = (g.get('title') or 'POSITION CHANGE').upper()
        x0 = blk_w + slant + 16
        name_w = self.name_block_w(name, 400, 36, 20)
        used = max(name_w + (badge.width + 18 if badge is not None else 0), self.lines_w(lines, f), 180)
        W = int(max(x0 + used + 24, self.tag_w(title) + 24))
        top = head_h
        img = Image.new('RGBA', (W, int(top + panel_h)), (0, 0, 0, 0))
        self.bar(img, (0, top, W, top + panel_h))
        by, bh = top, panel_h
        self.colour_wash(img, 0, by, W, bh, self.driver_color(name))
        self.label_tab(img, 0, top - head_h, title, th.green)
        ink = self.team_block(img, 0, by, blk_w, bh, th.green, slant)
        cx, cy = blk_w / 2 + 2, by + bh / 2
        pos = f"P{d['pos']}" if d.get('pos') else ''
        if pos:
            polygon(img, [(cx - 11, cy - 16), (cx, cy - 27), (cx + 11, cy - 16)], ink)
            text_vcenter(img, cx, cy + 8, pos, fit_font(pos, blk_w - 10, 34, 'bold'), ink, 'm')
        else:  # no position known: just a bigger chevron
            polygon(img, [(cx - 20, cy + 10), (cx, cy - 12), (cx + 20, cy + 10)], ink)
        y = self.name_block(img, x0, by + 10, name, 400, 36, 20, self._bright(self.driver_color(name)))
        if badge is not None:  # beside the name, centred on it
            img.alpha_composite(badge, (int(x0 + name_w + 18), int((by + 10 + y) / 2 - badge.height / 2)))
        self.text_lines(img, x0, y + 8, lines, f, th.muted, 24)
        return img

    def card_new_leader(self, g, shine=1.0):
        """Top-centre banner for a change of race leader, F1 style: a gold NEW RACE LEADER strip over the
        driver's name, and their logo. shine 0..1 sweeps a highlight across it."""
        th = self.th
        name = g['driver']
        m = self.meta(name)
        gold = rgba(PODIUM[1])
        head_h, body_h, slant = 32, 78, 16
        H = head_h + body_h
        pic = self.badge(name, body_h - 18) or self.car(name, 240, body_h - 8)
        pic_w = pic.width + 24 if pic else 0
        parts = m['display'].split()
        first, last = (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('', m['display'])
        f_first, f_last = font(30, 'regular'), fit_font(last.upper(), 640, 48, 'bold')
        flag = self.flag_img(m.get('country'), 26)
        name_w = (flag.width + 14 if flag else 0) + (text_w(f_first, first) + 12 if first else 0) + text_w(f_last, last.upper())
        f_head = font(21, 'bold_cond')
        head = 'NEW RACE LEADER'
        head_w = int(text_w(f_head, head) + 24 + 22 + slant)
        x0 = 26
        W = int(max(head_w + 40, x0 + name_w + 28 + pic_w + slant))
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        # Body: dark panel with a slanted right end, gold line along its top, driver colour on the left
        polygon(img, [(0, head_h), (W, head_h), (W - slant, H), (0, H)], th.panel)
        round_rect(img, (0, head_h, W - 1, head_h + 2), 0, gold)
        round_rect(img, (0, head_h, 6, H), 0, self.driver_color(name))
        # Header: gold strip with a slanted end
        polygon(img, [(0, 0), (head_w, 0), (head_w - slant, head_h), (0, head_h)], gold)
        text_vcenter(img, 22, head_h / 2 + 1, head, f_head, th.dark_text)
        # Name: flag, first name, SURNAME on one line
        cy = head_h + body_h / 2 + 1
        x = x0
        if flag:
            img.alpha_composite(flag, (int(x), int(cy - flag.height / 2)))
            x += flag.width + 14
        if first:
            text_vcenter(img, x, cy, first, f_first, th.text)
            x += text_w(f_first, first) + 12
        text_vcenter(img, x, cy, last.upper(), f_last, th.text)
        if pic:
            img.alpha_composite(pic, (int(W - slant - 14 - pic.width), int(cy - pic.height / 2)))
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
        """Race control message, FIA style: amber tag and line, the flag as a solid block down the left (chequered
        for the chequered flag, split for double yellow), the message in bold capitals and the detail under it, and
        the race time in a small tab on the top right. Long messages wrap rather than widen the card."""
        th = self.th
        d = g.get('data', {})
        msg = (g.get('title') or 'RACE CONTROL MESSAGE').upper()
        sub = ' '.join((g.get('text') or '').split())
        fmsg, fsub = font(26, 'bold_cond'), font(19, 'regular')
        max_text = 540
        lines = wrap(msg, fmsg, max_text, 99)
        sub_lines = wrap(sub, fsub, max_text, 99) if sub else []
        flag_w, slant = 58, 14
        x0 = flag_w + slant + 18
        amber = rgba('#FFC400')
        label = (g.get('label') or 'RACE CONTROL').upper()
        race_time = d.get('race_time') or ''
        f_time = font(17, 'bold_cond')
        time_w = int(text_w(f_time, race_time) + 24) if race_time else 0
        W = int(max(x0 + max(self.lines_w(lines, fmsg), self.lines_w(sub_lines, fsub), 220) + 28,
                    self.tag_w(label) + time_w + 30))
        content_h = len(lines) * 32 + (6 + len(sub_lines) * 24 if sub_lines else 0)
        panel_h = max(88, content_h + 34)
        img, top = self.card_canvas(W, panel_h, label, chevron=amber)
        by, bh = top, panel_h
        # The flag block
        flag = d.get('flag', 'yellow')
        fcol = rgba(FLAG_COLORS.get(flag, '#FFD200'))
        shape = [(0, by), (flag_w + slant, by), (flag_w, by + bh), (0, by + bh)]
        if flag == 'chequered':
            layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
            self._checker(layer, 0, int(by), flag_w + slant + 1, int(bh) + 1, 12)
            mask = Image.new('RGBA', img.size, (0, 0, 0, 0))
            polygon(mask, shape, (255, 255, 255, 255))
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'), mask.getchannel('A')))
            img.alpha_composite(layer)
        else:
            polygon(img, shape, fcol)
            if flag == 'double yellow':  # two flags: a dark cut across the middle
                m = by + bh / 2
                polygon(img, [(0, m - 2), (flag_w + slant / 2 + 2, m - 2), (flag_w + slant / 2 + 1, m + 2), (0, m + 2)],
                        th.panel_solid)
        # The message, centred down the card
        y = by + (bh - content_h) / 2 - 2
        for ln in lines:
            ImageDraw.Draw(img).text((x0, y), ln, font=fmsg, fill=th.text)
            y += 32
        self.text_lines(img, x0, y + 6, sub_lines, fsub, th.body, 24)
        if race_time:
            round_rect(img, (W - time_w, top - 24, W, top), 3, th.panel_solid, corners=(True, True, False, False))
            text_vcenter(img, W - time_w / 2, top - 11, race_time, f_time, th.text, 'm')
        return img

    def card_stat(self, g):
        """One big number in a colour block (the driver's colour, or gold for a race stat), who it belongs to and
        a two- or three-word caption. A driver's car stands on the card over their team logo. Nothing to read."""
        th = self.th
        d = g.get('data', {})
        value = str(d.get('value', ''))
        name = g.get('driver')
        caption = (d.get('short') or d.get('label') or '').upper()
        body_h, slant = 104, 16
        col = self.driver_color(name) if name else th.accent2
        car = self.car(name, 260, 110) if name else None
        badge = self.badge(name, body_h - CAR_SINK - 20 if car else body_h - 30) if name else None
        fval = fit_font(value, 200, 70, 'bold')
        val_w = max(92, int(text_w(fval, value)) + 40)
        x0 = val_w + slant + 18
        if name:
            head = self.meta(name)['short'].upper()
            f_head = fit_font(head, 380, 44, 'bold_cond')
            flag = self.flag_img(self.meta(name).get('country'), 24)
        else:
            head, flag = (g.get('title') or '').upper(), None
            f_head = fit_font(head, 420, 38, 'bold_cond')
        f_cap = fit_font(caption, 420, 22, 'semibold_cond')
        head_w = text_w(f_head, head) + (flag.width + 12 if flag else 0)
        logo_col = badge.width + 40 if badge is not None else int(car.width * 0.6) if car else 0
        label = (g.get('label') or 'RACE STATS').upper()
        W = x0 + int(max(head_w, text_w(f_cap, caption), 140)) + 24 + logo_col
        W = int(max(W, self.tag_w(label) + 24 + (car.width + 16 if car else 0)))
        img, top = self.card_canvas(W, body_h, label, top_extra=(car.height - CAR_SINK) if car else 0)
        by, bh = top, body_h
        self.colour_wash(img, 0, by, W, bh, col)
        ink = self.team_block(img, 0, by, val_w, bh, col, slant)
        text_vcenter(img, val_w / 2 + 2, by + bh / 2, value, fval, ink, 'm')
        if head:
            if flag:
                img.alpha_composite(flag, (int(x0), int(by + 36 - flag.height / 2)))
            text_vcenter(img, x0 + (flag.width + 12 if flag else 0), by + 36, head, f_head,
                         self._bright(col) if name else th.text)
        text_vcenter(img, x0, by + (74 if head else bh / 2), caption, f_cap, th.muted)
        if logo_col:
            self.logo_column(img, badge, W - logo_col, logo_col, by, bh, low=car is not None)
        if car:
            cx = W - logo_col / 2 if logo_col >= car.width * 0.6 else W - 12 - car.width / 2
            cx = min(W - car.width / 2 - 4, max(self.tag_w(label) + 16 + car.width / 2, cx))
            self.car_on_top(img, car, cx, by + CAR_SINK)
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
            x += self.name_with_flag(img, x, cy + 1, name, 28, self._bright(self.driver_color(name))) + 24
            text_vcenter(img, x, cy + 1, f"FROM P{start}", f_from, th.muted)
            round_rect(img, (W - pad - 84, y + 9, W - pad - 10, y + row_h - 9), 4, col)
            self.delta(img, W - pad - 47, cy, delta, 22, contrast_text(col))
            y += row_h + gap
        return img

    def card_headline(self, g):
        """A news line in bold capitals and a sentence or two under it, at most MAX_TEXT_W of text wide: longer
        text adds lines rather than width."""
        th = self.th
        fh = font(30, 'bold_cond')
        fb = font(21, 'regular')
        title, text = (g.get('title') or '').upper(), ' '.join((g.get('text') or '').split())
        W = self.text_card_w(text_w(fh, title), text_w(fb, text), 300) + 50
        head = wrap(title, fh, W - 50, 99)
        body = wrap(text, fb, W - 50, 99)
        panel_h = 22 + len(head) * 34 + (8 + len(body) * 26 if body else 0) + 14
        img, top = self.card_canvas(W, panel_h, (g.get('label') or 'PADDOCK NEWS').upper(), wash=th.accent2,
                                    chevrons=None)
        y = top + 16
        for ln in head:
            ImageDraw.Draw(img).text((24, y), ln, font=fh, fill=th.text)
            y += 34
        y += 8
        for ln in body:
            ImageDraw.Draw(img).text((24, y), ln, font=fb, fill=th.body)
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
        self.sponsor_mark(img, W - 26, 40, 'title', 34)
        round_outline(img, (0, 0, W, H), 12, (255, 255, 255, 40), 1)
        return img

    COMM_W = 440       # the commentator list is a narrow vertical card under the map on the right
    COMM_PHOTO_H = 140  # tallest the picture above the header may be
    COMM_BIO_LINE = 20

    def card_commentators(self, g, anim=1.0):
        """The AI commentator line-up, F1 TV analyst style, as a vertical list for the right of the screen:
        their picture standing on top of the card (a pre-race shot of them side by side), a dark header with
        the partner sponsor, one maroon panel listing the commentators (name, voice waveform, bio), then a line
        saying the commentary is AI and the racing is real. anim 0..1 brings the card in, then the rows one
        after another, while the waveforms take turns talking."""
        th = self.th
        d = g.get('data', {})
        names = d.get('names', [])
        if not names:
            return None
        anim = 1.0 if anim is None else anim
        W, pad, head_h = self.COMM_W, 10, 48
        body_w = W - pad * 2
        # The picture, bios and disclaimer come from the config at draw time, so Graphics Setup edits show at once
        path = gcfg.resolve_path(gcfg.commentator_group_photo(self.cfg))
        photo = self._commentator_picture(load_image(path) if path else None, W - 40, self.COMM_PHOTO_H)
        ph_h = photo.height if photo is not None else 0
        f_bio = font(16, 'regular')
        bios = [wrap(gcfg.commentator_bio(self.cfg, n), f_bio, body_w - 46, 2) for n in names]
        # Each row is as tall as its own bio needs
        row_hs = [66 + self.COMM_BIO_LINE * len(b) + (8 if b else 0) for b in bios]
        row_ys = [sum(row_hs[:i]) for i in range(len(names))]
        f_disc = font(15, 'regular')
        disc = gcfg.commentator_disclaimer(self.cfg)
        disc_lines = wrap(disc, f_disc, W - pad * 2 - 8, 3) if disc else []
        disc_h = (18 + len(disc_lines) * 19) if disc_lines else 0
        body_h = sum(row_hs)
        H = head_h + pad + body_h + pad + disc_h
        img = Image.new('RGBA', (W, ph_h + H), (0, 0, 0, 0))
        card = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(card, (0, 0, W, H), 10, th.panel)

        # Header: maroon into the panel colour, twin accent slashes, the label and the partner's logo as it is
        band = h_gradient(W, head_h, self._tint(th.accent, 0.38), th.panel_solid)
        shape = Image.new('L', (W, head_h), 0)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, W - 1, head_h + 12), radius=10, fill=255)
        band.putalpha(shape)
        card.alpha_composite(band)
        for off, col in ((14, th.accent), (28, th.accent2)):
            polygon(card, [(off + 10, 10), (off + 17, 10), (off + 7, head_h - 10), (off, head_h - 10)], col)
        label = (g.get('label') or 'AI COMMENTATORS').upper()
        sp_w = self.sponsor_mark(card, W - 16, head_h / 2, 'partner', 28)
        f_head = fit_font(label, W - 70 - (sp_w + 16 if sp_w else 0), 26, 'bold_cond')
        text_vcenter(card, 56, head_h / 2 + 1, label, f_head, th.text)
        card.alpha_composite(h_gradient(W, 3, th.accent, th.accent2), (0, head_h - 3))
        round_outline(card, (0, 0, W, H), 10, (255, 255, 255, 40), 1)
        img.alpha_composite(card, (0, ph_h))

        # anim runs over the whole time the card is up: the panel and picture come in first, the rows follow
        # one after another, then each commentator's waveform takes a turn "talking" while the others idle
        n = len(names)
        secs = anim * float(d.get('anim_dur') or 1.4)
        talker = int(max(0.0, secs - 1.2) / 1.6) % n
        top = ph_h + head_h + pad
        p = max(0.0, min(1.0, secs / 0.35))
        if p > 0:
            img.alpha_composite(self._commentator_body(body_w, body_h, p), (pad, top))
        if photo is not None:
            # Rises up out of the header, right-aligned like the F1 TV analyst
            p = max(0.0, min(1.0, (secs - 0.1) / 0.45))
            e = 1 - (1 - p) ** 3
            if e > 0:
                rise = int((1 - e) * 24)
                pic = photo.crop((0, 0, photo.width, photo.height - rise))
                if e < 1:
                    pic.putalpha(pic.getchannel('A').point(lambda v, e=e: int(v * e)))
                img.alpha_composite(pic, (W - 20 - photo.width, rise))
        for i, name in enumerate(names):
            p = max(0.0, min(1.0, (secs - 0.15 - i * 0.22) / 0.45))
            e = 1 - (1 - p) ** 3
            if e <= 0:
                continue
            level = 1.0 if i == talker or secs < 1.2 else 0.3
            row = self._commentator_row(name, bios[i], body_w, row_hs[i], secs, i, level, divider=i > 0)
            if e < 1:
                row.putalpha(row.getchannel('A').point(lambda v, e=e: int(v * e)))
            img.alpha_composite(row, (pad, int(top + row_ys[i] + (1 - e) * 12)))
        if disc_lines:
            p = max(0.0, min(1.0, (secs - 0.3 - n * 0.22) / 0.4))
            if p > 0:
                foot = Image.new('RGBA', (W, disc_h), (0, 0, 0, 0))
                round_rect(foot, (pad, 0, W - pad, 1), 0, (255, 255, 255, 34))
                self.text_lines(foot, pad + 4, 10, disc_lines, f_disc, th.muted, 19)
                if p < 1:
                    foot.putalpha(foot.getchannel('A').point(lambda v, p=p: int(v * p)))
                img.alpha_composite(foot, (0, int(top + body_h + pad + 2)))
        return img

    @staticmethod
    def _tint(color, k, base=(11, 14, 20)):
        """color mixed k of the way from the dark panel base."""
        return tuple(int(b + (c - b) * k) for c, b in zip(color[:3], base)) + (255,)

    def _commentator_picture(self, photo, max_w, max_h):
        """The commentators' picture for the top of the card, or None. A cut-out PNG (transparent background)
        is used as it is, to stand on the header; any other picture is shown whole on a plate with a slanted
        left edge and an accent2 sliver, rounded on top."""
        if photo is None:
            return None
        a = photo.getchannel('A')
        corners = [(0, 0), (photo.width - 1, 0), (0, photo.height - 1), (photo.width - 1, photo.height - 1)]
        if min(a.getpixel(c) for c in corners) < 250:
            return fit_image(photo, max_w, max_h)
        slant = 22
        pic = fit_image(photo, max_w - slant - 8, max_h)
        w, h = pic.width + slant + 8, pic.height
        out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        polygon(out, [(slant, 0), (slant + 6, 0), (6, h), (0, h)], self.th.accent2)
        m = Image.new('L', (pic.width * 4, h * 4), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, pic.width * 4 - 1, h * 4 + 40), radius=24, fill=255)
        ImageDraw.Draw(m).polygon([(0, 0), (slant * 4, 0), (0, h * 4)], fill=0)
        m = m.resize((pic.width, h), Image.LANCZOS)
        pic.putalpha(ImageChops.multiply(pic.getchannel('A'), m))
        out.alpha_composite(pic, (8, 0))
        return out

    def _commentator_body(self, w, h, opacity=1.0):
        """The maroon panel behind the commentator rows, with faint diagonal stripes like the F1 TV panel."""
        th = self.th
        bg = h_gradient(w, h, self._tint(th.accent, 0.30), self._tint(th.accent, 0.12))
        stripes = ImageDraw.Draw(bg)
        for sx in range(-h, w, 14):
            stripes.line((sx, h, sx + h, 0), fill=self._tint(th.accent, 0.36), width=1)
        mask = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        round_rect(mask, (0, 0, w, h), 6, (255, 255, 255, int(255 * opacity)))
        bg.putalpha(mask.getchannel('A'))
        return bg

    def _commentator_row(self, name, lines, w, h, secs, idx, level, divider=False):
        """One commentator on a transparent strip: accent bar, first name and waveform, SURNAME and up to two
        lines of bio (a thin line above it after the first)."""
        th = self.th
        row = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        if divider:
            round_rect(row, (12, 0, w - 12, 1), 0, (255, 255, 255, 30))
        # Surname particles stay with the surname: Pedro / DE LA ROSA
        parts = name.split()
        cut = len(parts) - 1
        while cut > 1 and parts[cut - 1].lower() in ('de', 'la', 'del', 'van', 'von', 'der', 'da', 'di', 'le', 'dos'):
            cut -= 1
        first, last = (' '.join(parts[:cut]), ' '.join(parts[cut:])) if len(parts) > 1 else ('', name)
        tx, top = 22, 10
        avail = w - tx - 24
        # Accent bar beside the name, red into gold
        bar_h = 50
        bar = Image.new('RGBA', (4, bar_h), th.accent)
        bar.paste(Image.new('RGBA', (4, bar_h), th.accent2), (0, 0), Image.linear_gradient('L').resize((4, bar_h)))
        row.alpha_composite(bar, (10, top + (0 if first else 4)))
        f_first = font(18, 'regular')
        wx = tx
        if first:
            first_t = ellipsize(first, f_first, avail - 90)
            text_vcenter(row, tx, top + 9, first_t, f_first, (255, 255, 255, 200))
            wx = tx + int(text_w(f_first, first_t)) + 12
        f_last = fit_font(last.upper(), avail, 30, 'bold')
        text_vcenter(row, tx, top + (36 if first else 29), last.upper(), f_last, th.text)
        # Voice waveform beside the first name: live while this commentator talks, idling otherwise
        self._waveform(row, wx, top + 9, 72, 16, secs, idx, level)
        self.text_lines(row, tx, top + 56, lines, font(16, 'regular'), (255, 255, 255, 175), self.COMM_BIO_LINE)
        return row

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

    COMM_INFO_NOTE = "The voices you're hearing are AI recreations of these commentators."

    def card_commentator_info(self, g):
        """Commentator info box for under the map on the right: the commentators' names, one short paragraph
        about them (written for this race by the graphics step's AI) and a line saying the voices are AI."""
        th = self.th
        d = g.get('data') or {}
        text = d.get('text') or ''
        if not text:
            return None
        W, pad = self.COMM_W, 18
        f_names = font(18, 'bold_cond')
        f_text = font(19, 'regular')
        f_note = font(15, 'regular')
        names = wrap('  ·  '.join(n.upper() for n in d.get('names') or []), f_names, W - pad * 2, 2)
        lines = wrap(text, f_text, W - pad * 2, 9)
        note = wrap(self.COMM_INFO_NOTE, f_note, W - pad * 2, 2)
        panel_h = (14 + (22 * len(names) + 8 if names else 0) + 26 * len(lines) + 12 + 1 + 10
                   + 19 * len(note) + 12)
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'THE COMMENTARY TEAM').upper(), wash=th.accent,
                                    chevrons=None)
        y = top + 14
        if names:
            y = self.text_lines(img, pad, y, names, f_names, th.accent2, 22) + 8
        y = self.text_lines(img, pad, y, lines, f_text, th.text, 26) + 12
        round_rect(img, (pad, y, W - pad, y + 1), 0, (255, 255, 255, 30))
        self.text_lines(img, pad, y + 10, note, f_note, (196, 202, 214, 255), 19)
        return img

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
        title = (g.get('title') or 'AFTER THE START').upper()
        tab = self.sponsor_tab('partner')
        if tab:
            # Room for the label and the partner's tab side by side
            W = max(W, int(text_w(font(18, 'bold_cond'), title) + 28 + 16 + tab.width))
        img, top = self.card_canvas(W, panel_h, title, sponsor='partner')
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
            text_vcenter(img, x + 134, top + 62, self.meta(name)['short'].upper(), f,
                         self._bright(self.driver_color(name)))
            text_vcenter(img, x + 134, top + 94, f"P{r['grid']} › P{r['pos']}", font(22, 'semibold_cond'), th.muted)
        return img

    def card_fastest_lap(self, g):
        """Compact F1-style strip: purple FASTEST LAP block with a chevron, then the driver's colour bar, name
        and lap time. One line, nothing else."""
        th = self.th
        name, d = g['driver'], g.get('data', {})
        purple = rgba(FASTEST_PURPLE)
        deep = rgba('#5B1A9E')
        H = 58
        f_label = font(21, 'bold_cond')
        label = (g.get('title') or 'FASTEST LAP').upper().split()
        top_word, low_word = (' '.join(label[:-1]), label[-1]) if len(label) > 1 else (label[0], '')
        label_w = max(text_w(f_label, top_word), text_w(f_label, low_word))
        P = int(label_w + 28)       # purple block, up to where its arrow starts
        C, C2 = 16, 13             # arrow depth, width of the darker chevron behind it
        x0 = P + C2 + C + 14        # where the driver part starts
        short = self.meta(name)['short'].upper()
        f_name = fit_font(short, 300, 32, 'bold')
        lap_time = d.get('lap_time', '') or ''
        f_time = font(26, 'semibold_cond')
        name_w = text_w(f_name, short)
        time_w = text_w(f_time, lap_time) + 18 if lap_time else 0
        W = int(x0 + 16 + name_w + time_w + 20)

        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, H), 0, rgba('#15161C', 0.94))
        polygon(img, [(P, 0), (P + C2, 0), (P + C2 + C, H / 2), (P + C2, H), (P, H), (P + C, H / 2)], deep)
        polygon(img, [(0, 0), (P, 0), (P + C, H / 2), (P, H), (0, H)], purple)
        if low_word:
            text_vcenter(img, 14, H / 2 - 11, top_word, f_label, (255, 255, 255, 255))
            text_vcenter(img, 14, H / 2 + 11, low_word, f_label, (255, 255, 255, 255))
        else:
            text_vcenter(img, 14, H / 2, top_word, f_label, (255, 255, 255, 255))
        round_rect(img, (x0, 13, x0 + 6, H - 13), 2, self.driver_color(name))
        text_vcenter(img, x0 + 16, H / 2, short, f_name, th.text)
        if lap_time:
            text_vcenter(img, x0 + 16 + name_w + 18, H / 2, lap_time, f_time, th.muted)
        # Slightly rounded corners on the whole strip
        mask = Image.new('L', (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), radius=4, fill=255)
        img.putalpha(ImageChops.multiply(img.getchannel('A'), mask))
        return img

    @staticmethod
    def _bright(color):
        """A livery colour lifted until it reads as text on the near-black bar (dark blues go light blue)."""
        r, g, b = Templates._readable(color)[:3]
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        if lum < 150:
            k = (150 - lum) / (255 - lum)
            r, g, b = (int(c + (255 - c) * k) for c in (r, g, b))
        return (r, g, b, 255)

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
        # The team/make logo next to the number, as it is
        logo = self.badge(name, 64) if name else None
        logo_w = logo.width + 18 if logo is not None else 0
        f_head = fit_font(surname, W - pad * 2 - num_w - logo_w, 44, 'bold')
        f_radio = font(44, 'bold')
        base_name, base_radio = 66, 112  # text baselines
        head_h = base_radio + 22

        f_line = font(23, 'bold_cond')
        line_h = 27
        text_max = W - pad * 2
        # A reply from someone else on the driver's radio (their engineer) says who is talking above the quote
        f_who = font(17, 'bold_cond')
        who_h = 22
        blocks = []
        # A driver's rant runs long: the card grows downwards to fit it rather than cutting it short. The card has
        # room for 12 quote lines in all (it stays on screen), taken from the longest quote when they need more.
        quotes = [f"“{seg['quote'].upper()}”" for seg in segments]
        room = [len(wrap(q, f_line, text_max - 20, 99)) for q in quotes]
        while sum(room) > 12 and max(room) > 2:
            room[room.index(max(room))] -= 1
        for seg, q, n in zip(segments, quotes, room):
            lines = wrap(q, f_line, text_max - 20, n)
            who = (seg.get('label') or '').upper() if not seg.get('is_driver') else ''
            blocks.append((seg.get('is_driver'), lines, who))
        body_h = (sum(len(ls) * line_h + (who_h if who else 0) for _, ls, who in blocks)
                  + 12 * (len(blocks) - 1) + 30)
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
        if logo is not None:
            # Centred on the SURNAME / RADIO block
            cy = (base_name - 34 + base_radio) / 2
            img.alpha_composite(logo, (int(pad + num_w), int(cy - logo.height / 2)))

        # Body: driver lines right-aligned in their colour, replies left-aligned in white
        y = head_h + 12
        for is_driver, lines, who in blocks:
            if who:
                ImageDraw.Draw(img).text((pad, y), who, font=f_who, fill=th.muted, anchor='la')
                y += who_h
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
        self.sponsor_mark(img, right, head_h / 2, 'title', 34, fg)
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
            if r.get('pending'):
                # A place nobody has taken yet (the car is still racing to the line): just the number, dimmed
                round_rect(img, (24, cy - 15, 64, cy + 15), 4, (255, 255, 255, 40))
                text_vcenter(img, 44, cy + 1, str(pos), fpos, th.muted, 'm')
                round_rect(img, (90, cy - 1, 260, cy + 1), 1, (255, 255, 255, 40))
                y += row_h
                continue
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
    def card_last_laps(self, g, anim=1.0):
        """Last 4 laps, F1 style: the car ahead's lap times on top, the car behind's difference to each one
        under them (green where it was quicker, red where slower), and the timing sponsor in a tab on the top
        right. The differences come in lap by lap."""
        th = self.th
        d = g.get('data', {})
        behind, ahead = g['driver'], g.get('driver2')
        laps, ta, tb = d.get('laps') or [], d.get('ahead_times') or [], d.get('behind_times') or []
        if not ahead or not laps or len(ta) != len(laps) or len(tb) != len(laps):
            return None
        anim = 1.0 if anim is None else anim
        shown = len(laps) if anim >= 1 else int(anim * len(laps) + 0.999)
        f_head = font(26, 'semibold_cond')
        f_lap = font(26, 'bold_cond')
        f_name = font(30, 'bold_cond')
        f_time = font(30, 'regular')
        f_delta = font(30, 'semibold')
        col_w = 150
        head_h, row_h = 50, 52
        rows = ((d.get('pos_ahead'), ahead), (d.get('pos_behind'), behind))
        badges = [self.badge(name, 26) for _, name in rows]
        logo_x = 24 + 34 + 10
        logo_w = max(self.badge_w(bg) for bg in badges)  # one column for the logos, so the names line up
        name_x = logo_x + logo_w
        name_w = max(text_w(f_head, 'LAST 4 LAPS') - (name_x - 24),
                     *(text_w(f_name, self.meta(n)['short'].upper()) for _, n in rows))
        x_cols = name_x + int(name_w) + 40
        W = x_cols + col_w * len(laps) + 10
        panel_h = head_h + row_h * 2 + 8
        img, top = self.card_canvas(W, panel_h, None, sponsor='timing')
        text_vcenter(img, 24, top + head_h / 2 + 2, f'LAST {len(laps)} LAPS', f_head, th.text)
        for k, n in enumerate(laps):
            text_vcenter(img, x_cols + col_w * (k + 0.5), top + head_h / 2 + 2, f'LAP {n}', f_lap, th.text, 'm')
        green, red = th.green, th.red
        for r, ((pos, name), bg) in enumerate(zip(rows, badges)):
            cy = top + head_h + row_h * r + row_h / 2
            if r:
                round_rect(img, (16, cy - row_h / 2, W - 16, cy - row_h / 2 + 1), 0, (255, 255, 255, 28))
            text_vcenter(img, 24 + 17, cy, str(pos or '–'), font(26, 'semibold'), th.text, 'm')
            if bg is not None:
                img.alpha_composite(bg, (int(logo_x + (logo_w - 18 - bg.width) / 2), int(cy - bg.height / 2)))
            text_vcenter(img, name_x, cy, self.meta(name)['short'].upper(), f_name, th.text)
            for k in range(len(laps)):
                cx = x_cols + col_w * (k + 0.5)
                if r == 0:
                    text_vcenter(img, cx, cy, _lap_clock(ta[k]), f_time, th.text[:3] + (215,), 'm')
                elif k < shown:
                    diff = tb[k] - ta[k]
                    col = green if diff < -0.0005 else red if diff > 0.0005 else th.muted
                    text_vcenter(img, cx, cy, f"{'-' if diff < 0 else '+'}{abs(diff):.3f}", f_delta, col, 'm')
        return img

    def card_pit_timer(self, g, anim=1.0):
        """Compact F1-style pit timer for a top-4 car: position, team logo and name on top, IN PIT with the
        stationary time ticking up under it (green once the car leaves its box), and the partner sponsor's
        logo along the bottom when one is set."""
        th = self.th
        d = g.get('data', {})
        name = g['driver']
        anim = 1.0 if anim is None else anim
        secs = d.get('stationary', 0) * anim
        done = anim >= 1.0
        head_h, body_h = 38, 50
        label = self.meta(name)['short'].upper()
        f_name = font(24, 'bold_cond')
        badge = self.badge(name, 22)
        pos_w = 38
        name_x = pos_w + 12 + (badge.width + 8 if badge is not None else 0)
        W = int(max(name_x + text_w(f_name, label) + 16, 200))
        sp = self.sponsor('partner')
        strip_h = 30 if sp else 0
        H = head_h + body_h + strip_h
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, H), 6, th.panel)
        # Header: position, logo and name, with the driver's colour along its bottom
        round_rect(img, (0, 0, W, head_h), 6, th.panel_hi, corners=(True, True, False, False))
        round_rect(img, (0, 0, pos_w, head_h), 6, th.text, corners=(True, False, False, False))
        pos = str(d.get('pos') or '')
        text_vcenter(img, pos_w / 2, head_h / 2 + 1, pos, font(24, 'bold'), th.dark_text, 'm')
        if badge is not None:
            img.alpha_composite(badge, (pos_w + 10, int((head_h - badge.height) / 2)))
        text_vcenter(img, name_x, head_h / 2 + 1, label, f_name, th.text)
        round_rect(img, (0, head_h - 3, W, head_h), 0, self.driver_color(name))
        # IN PIT and the time
        cy = head_h + body_h / 2
        text_vcenter(img, 14, cy, 'PIT STOP' if done else 'IN PIT', font(20, 'semibold_cond'), th.muted)
        text_vcenter(img, W - 14, cy, f"{secs:.1f}", font(34, 'bold'), th.green if done else th.text, 'r')
        if sp:
            sname, logo = sp
            sy = head_h + body_h
            round_rect(img, (10, sy, W - 10, sy + 1), 0, (255, 255, 255, 40))
            if logo:
                lg = fit_image(logo, W - 28, strip_h - 10)
                img.alpha_composite(lg, (int((W - lg.width) / 2), int(sy + (strip_h - lg.height) / 2)))
            else:
                text_vcenter(img, W / 2, sy + strip_h / 2, sname.upper(), font(16, 'bold_cond'), th.muted, 'm')
        return img

    def card_pit_lane(self, g):
        """Small IN PITS tag for cars outside the top 4: one line per car (position and name)."""
        th = self.th
        cars = g.get('data', {}).get('cars') or []
        if not cars:
            return None
        head_h, row_h = 26, 28
        f_pos, f_name = font(19, 'bold'), font(20, 'bold_cond')
        names = [self.meta(n)['short'].upper() for _, n in cars]
        W = int(max(130, 48 + max(text_w(f_name, s) for s in names) + 14))
        H = head_h + 4 + row_h * len(cars) + 4
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        round_rect(img, (0, 0, W, H), 6, th.panel)
        round_rect(img, (0, 0, W, head_h), 6, th.accent2, corners=(True, True, False, False))
        text_vcenter(img, 12, head_h / 2 + 1, 'IN PITS', font(16, 'bold_cond'), contrast_text(th.accent2))
        for k, ((pos, name), label) in enumerate(zip(cars, names)):
            cy = head_h + 4 + k * row_h + row_h / 2
            text_vcenter(img, 20, cy + 1, str(pos or ''), f_pos, th.text, 'm')
            round_rect(img, (36, cy - 9, 39, cy + 9), 0, self.driver_color(name))
            text_vcenter(img, 48, cy + 1, label, f_name, th.text)
        return img

    def card_gaps(self, g):
        """The field as its packs, left to right in race order: each pack a capsule of driver markers headed by the
        places it covers, and between packs a link with the time gap written on it. Links grow with the gap on a
        compressed scale (square root, capped), so every gap stays visible and a lone car half a minute up the road
        no longer squeezes everyone else into one wall; a capped link shows a break in the line. The card is as
        wide as its packs need."""
        th = self.th
        d = g.get('data', {})
        rows = sorted(d.get('rows') or [], key=lambda r: r[1])
        if len(rows) < 2:
            return None
        gap_of = dict(rows)
        order = {name: i for i, (name, _) in enumerate(rows, 1)}
        packs = [[n for n in p if n in gap_of] for p in (d.get('packs') or [])]
        natural = [p for p in packs if p] or [[n for n, _ in rows]]
        pos = d.get('pos') or {}

        def place(name):
            return pos.get(name) or order[name]

        def at_most(n):
            """The packs, or for a big strung-out field (14 packs in a 26-car Super Touring race) the field split
            at its n - 1 biggest gaps only."""
            if len(natural) <= n:
                return natural
            cuts = sorted(sorted(range(1, len(rows)), key=lambda i: rows[i][1] - rows[i - 1][1])[-(n - 1):])
            return [[name for name, _ in rows[a:b]] for a, b in zip([0] + cuts, cuts + [len(rows)])]

        margin, pad, inner = 36, 9, 6
        # A driver with a team/make logo is shown by it, as it is, with his three letters under it (cars of one make
        # share a logo); a driver without one gets a circle in his colour with the letters inside
        logos = any(self.logo_path(n) for n, _ in rows)
        # The card has to fit right of the timing tower: shorter links and a little smaller markers first, then
        # fewer packs (the smallest gaps join their packs), then smaller markers still (see GAPS_FITS)
        max_content = self.popup_max_w() - 2 * margin
        for R, (link_min, link_k, link_max), most in GAPS_FITS:
            packs = at_most(most)
            links = [gap_of[b[0]] - gap_of[a[-1]] for a, b in zip(packs, packs[1:])]
            S = int(2 * R * 1.25) if logos else 2 * R  # room a marker takes along the line
            pack_w = [len(p) * S + (len(p) - 1) * inner + 2 * pad for p in packs]
            link_w = [min(link_max, link_min + link_k * max(0.0, s) ** 0.5) for s in links]
            content = sum(pack_w) + sum(link_w)
            if content <= max_content:
                break
        W = max(760, int(content + 2 * margin))
        panel_h = 168 if logos else 156
        img, top = self.card_canvas(W, panel_h, (g.get('title') or 'GAPS ON TRACK').upper(), sponsor='partner')
        cy = top + (86 if logos else 80)
        half = R + 14 if logos else R + pad - 2  # half the capsule's height (logos have their letters under them)
        my = cy - 8 if logos else cy             # the markers' centre line
        x = (W - content) / 2
        f_code = font(19 if R >= 19 else 16, 'bold_cond')
        f_chip = font(18, 'semibold_cond')
        line = (255, 255, 255, 70)
        for i, pack in enumerate(packs):
            a, b = x, x + pack_w[i]
            lead = i == 0
            # The link to the next pack: a line with the gap on a chip, broken when the scale was compressed
            if i + 1 < len(packs):
                l0, l1 = b, b + link_w[i]
                round_rect(img, (l0, cy - 1, l1, cy + 1), 1, line)
                if link_w[i] >= link_max:
                    for bx in (l0 + (l1 - l0) * 0.22, l1 - (l1 - l0) * 0.22):
                        round_rect(img, (bx - 7, cy - 4, bx + 7, cy + 4), 0, th.bar[:3] + (255,))
                        for off in (-4, 3):
                            polygon(img, [(bx + off - 3, cy + 9), (bx + off, cy + 9), (bx + off + 4, cy - 9),
                                          (bx + off + 1, cy - 9)], (255, 255, 255, 150))
                chip = f"+{links[i]:.1f}s"
                cw = text_w(f_chip, chip) + 18
                mx = (l0 + l1) / 2
                round_rect(img, (mx - cw / 2, cy - 14, mx + cw / 2, cy + 14), 14, th.bar[:3] + (255,))
                round_outline(img, (mx - cw / 2, cy - 14, mx + cw / 2, cy + 14), 14, (255, 255, 255, 90), 1)
                text_vcenter(img, mx, cy + 1, chip, f_chip, th.text, 'm')
            # The pack: a soft capsule behind its markers, the lead pack outlined in the accent
            box = (a, cy - half, b, cy + half)
            round_rect(img, box, min(half, R + pad), (255, 255, 255, 34 if lead else 22))
            if lead:
                round_outline(img, box, min(half, R + pad), th.accent2[:3] + (220,), 2)
            first, last = place(pack[0]), place(pack[-1])
            if lead:
                label = 'LEADER' if len(pack) == 1 else f"LEAD PACK  ·  P1–{last}"
            else:
                label = f"P{first}" if len(pack) == 1 else f"P{first}–{last}"
            fl = fit_font(label, pack_w[i] + min(link_w + [0]) * 0.6, 17, 'bold_cond', 12)
            text_vcenter(img, (a + b) / 2, top + 26, label, fl, th.accent2 if lead else th.muted, 'm')
            mx = a + pad + S / 2
            for name in pack:
                code = ''.join(c for c in self.meta(name)['short'].upper() if c.isalnum())[:3]
                logo = self.badge(name, 2 * R - 6) if logos else None
                if logo is not None:
                    logo = fit_image(logo, S, 2 * R - 6)
                    img.alpha_composite(logo, (int(mx - logo.width / 2), int(my - logo.height / 2)))
                    text_vcenter(img, mx, cy + R + 4, code, font(15, 'bold_cond'), th.text, 'm')
                else:
                    r = R - 3 if logos else R
                    col = self.driver_color(name)
                    round_rect(img, (mx - r, my - r, mx + r, my + r), r, col)
                    round_outline(img, (mx - r, my - r, mx + r, my + r), r, (255, 255, 255, 210), 2)
                    text_vcenter(img, mx, my + 1, code, fit_font(code, 2 * r - 8, f_code.size, 'bold_cond', 10),
                                 contrast_text(col), 'm')
                mx += S + inner
            x = b + (link_w[i] if i < len(link_w) else 0)
        spread = rows[-1][1]
        foot = f"{len(packs)} PACKS  ·  LEADER TO LAST  {spread:.1f}s" if len(packs) > 1 else \
            f"ONE PACK  ·  LEADER TO LAST  {spread:.1f}s"
        text_vcenter(img, W - 22, top + panel_h - 15, foot, font(16, 'semibold_cond'), th.muted, 'r')
        return img

    def card_insight(self, g):
        """Track guide / strategy / analysis: a bold line and one short line under it. Circuit notes (the
        CIRCUIT_LABELS tabs) take up to three lines, with the event flag fading in on the right."""
        th = self.th
        label = (g.get('label') or 'ANALYSIS').upper()
        circuit = label in gcfg.CIRCUIT_LABELS
        title = (g.get('title') or '').upper()
        text = ' '.join((g.get('text') or '').split())
        f_title, f_text = font(32, 'bold_cond'), font(20, 'regular')
        # As wide as the title, or enough for the text on about two lines, never past MAX_TEXT_W: more text
        # adds lines
        max_w = self.text_card_w(text_w(f_title, title), text_w(f_text, text), 260)
        title = wrap(title, f_title, max_w, 1)[0] if title else ''
        lines = wrap(text, f_text, max_w, 99) if text else []
        panel_h = 22 + 36 + (len(lines) * 26 + 6 if lines else 0) + 14
        flag = (load_image(gcfg.resolve_path(self.cfg['championship'].get('event_flag')), autocrop=False)
                if circuit else None)
        flag_w = 160 if flag is not None else 0
        W = int(48 + max(text_w(f_title, title), self.lines_w(lines, f_text), 260) + flag_w)
        img, top = self.card_canvas(W, panel_h, label, wash=th.accent2, chevrons=None)
        if flag is not None:
            fw = flag_w + 60
            art = cover_image(flag, fw, int(panel_h))
            ramp = Image.linear_gradient('L').rotate(90).resize((fw, int(panel_h))).point(lambda v: int(v * 0.6))
            art.putalpha(ImageChops.multiply(art.getchannel('A'), ramp))
            shape = Image.new('L', img.size, 0)  # the panel's rounded right edge
            ImageDraw.Draw(shape).rounded_rectangle((0, top, W - 1, top + panel_h - 1), 6, fill=255)
            layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
            layer.alpha_composite(art, (W - fw, int(top)))
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'), shape))
            img.alpha_composite(layer)
        ImageDraw.Draw(img).text((26, top + 18), title, font=f_title, fill=th.text)
        self.text_lines(img, 26, top + 62, lines, f_text, th.body, 26)
        return img

def _is_light(logo):
    """True for a mostly white/pale logo (it needs a dark tab behind it)."""
    small = logo.resize((32, max(1, int(32 * logo.height / max(1, logo.width)))))
    px = [(r, g, b) for r, g, b, a in small.getdata() if a > 128]
    if not px:
        return False
    return sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px) / len(px) > 170


def race_time_label(race, t_video):
    gf = race.green_flag_video
    return fmt_clock(t_video - gf) if gf is not None and t_video >= gf else ''


def _lap_clock(sec):
    """1:21.530"""
    m, s = divmod(max(0.0, sec), 60)
    return f"{int(m)}:{s:06.3f}"
