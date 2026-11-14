"""The full-screen starting grid shown before the start (see racefiles.GRID_TAG), drawn as one animated scene.

The held replay frame fills the background, blurred and darkened under a slow push-in, so the sim's own HUD on it
never shows. Everything comes in with masked wipes, broadcast style, never sliding objects about: the header builds
(logo, the label tab wiping open, the event name rising from behind a line), then the cars in grid order, each bar
wiped open behind a bright leading edge with its details gliding into place inside it. At the end the bars wipe shut
while the blur pulls into focus on the same frame the video holds underneath, so the replay simply starts moving.
"""
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import config as gcfg
from racefiles import format_lap_time
from .drawing import (cover_image, ease_in, ellipsize, fit_font, fit_image, font, load_image, rgba, text_vcenter,
                      text_w, wave_cloth)

HEADER_AT = 0.0       # header build (seconds into the scene)
CARS_AT = 0.55        # first car's bar starts wiping open
CAR_STAGGER = 0.075   # ... each car in grid order after the one before
WIPE = 0.32           # a bar wiping open
CONTENT_DELAY = 0.10  # its number, logo and name start gliding in this long into the wipe
CONTENT_DUR = 0.42
CONTENT_SHIFT = 26    # ... from this far to the left
CAR_DELAY = 0.16      # the car settles into the bar
CAR_DUR = 0.45
CAR_SHIFT = 34
GLINT_AT = 0.55       # a glint crosses the bar this long after it starts opening
GLINT_DUR = 0.55
EXIT_DUR = 0.9        # bars wipe shut and the blur pulls into focus, ending as the replay starts
EXIT_WIPE = 0.38
EXIT_STAGGER = 0.022
PUSH_IN = 0.06        # background zoom over the whole scene
BLUR = 16
SKEW = 0.22           # slant of the car slots (share of their height)


def paste(frame, src, x, y):
    """alpha_composite that clips src to the frame (it may be sliding in from past an edge)."""
    x, y = int(x), int(y)
    cx0, cy0 = max(0, -x), max(0, -y)
    cx1, cy1 = min(src.width, frame.width - x), min(src.height, frame.height - y)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    if (cx0, cy0, cx1, cy1) != (0, 0, src.width, src.height):
        src = src.crop((cx0, cy0, cx1, cy1))
    frame.alpha_composite(src, (x + cx0, y + cy0))


def ease_in_out(p):
    p = max(0.0, min(1.0, p))
    return 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def ease_out_quint(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 5


def fade(img, a):
    if a >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel('A').point(lambda v: int(v * max(0.0, a))))
    return out


def wipe(img, p, skew=0):
    """img revealed left to right up to share p, along an edge slanted like the car slots."""
    if p >= 0.999:
        return img
    if p <= 0.001:
        return None
    w, h = img.size
    edge = int((w + skew) * p)
    m = Image.new('L', (w, h), 0)
    ImageDraw.Draw(m).polygon([(0, 0), (edge, 0), (edge - skew, h), (0, h)], fill=255)
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel('A'), m))
    return out


class StartingGrid:
    def __init__(self, tpl, size, quali=None):
        """quali: {driver: qualifying lap seconds} (racefiles.qualifying_times); without any, no times show."""
        self.tpl = tpl
        self.quali = quali or {}
        self.pole = min(self.quali.values()) if self.quali else None
        self.th = tpl.th
        self.W, self.H = size
        self.grid = list(tpl.race.grid) or list(tpl.race.drivers)
        self._backdrop = {}
        self._slots = None
        self._header = None
        self._shade = None

    # ------------------------------------------------------------------ layout
    def layout(self):
        W, H = self.W, self.H
        top, bottom = 268, H - 44
        rows = max(1, (len(self.grid) + 1) // 2)
        row_h = min(150.0, (bottom - top) / (rows + 0.55))
        slot_h = int(row_h * 0.84)
        margin, gap = 96, 70
        col_w = int((W - 2 * margin - gap) / 2)
        out = []
        for i in range(len(self.grid)):
            col, row = i % 2, i // 2
            x = margin + col * (col_w + gap)
            y = int(top + row * row_h + (row_h * 0.55 if col else 0))
            out.append((x, y, col_w, slot_h))
        return out

    # ------------------------------------------------------------------ static pieces (cached)
    def backdrop(self, path):
        """The held frame blurred, at push-in size, or None without one."""
        if path not in self._backdrop:
            im = load_image(path, autocrop=False) if path and os.path.exists(path) else None
            if im is not None:
                big = (int(self.W * (1 + PUSH_IN)), int(self.H * (1 + PUSH_IN)))
                im = cover_image(im.convert('RGB').convert('RGBA'), *big).filter(ImageFilter.GaussianBlur(BLUR))
            self._backdrop[path] = im
        return self._backdrop[path]

    def shade(self):
        """Darkening over the backdrop: deep on the left where the text is, a vignette all round, tinted
        towards the theme's panel colour."""
        if self._shade is None:
            W, H = self.W, self.H
            panel = self.th.panel_solid[:3]
            lr = Image.linear_gradient('L').rotate(90).resize((W, H))            # 255 left -> 0 right
            tb = Image.linear_gradient('L').resize((W, H))                         # 0 top -> 255 bottom
            a = ImageChops.lighter(lr.point(lambda v: int(150 + v * 0.35)), tb.point(lambda v: int(v * 0.85)))
            vig = Image.new('L', (W, H), 0)
            ImageDraw.Draw(vig).ellipse((-W * 0.25, -H * 0.35, W * 1.25, H * 1.35), fill=255)
            vig = ImageChops.invert(vig.filter(ImageFilter.GaussianBlur(160)))
            a = ImageChops.lighter(a, vig)
            layer = Image.new('RGBA', (W, H), panel + (255,))
            layer.putalpha(a.point(lambda v: min(240, int(v))))
            glow = Image.new('RGBA', (W, H), self.th.accent[:3] + (255,))
            g = Image.new('L', (W, H), 0)
            ImageDraw.Draw(g).ellipse((W * 0.62, -H * 0.5, W * 1.4, H * 0.55), fill=70)
            glow.putalpha(g.filter(ImageFilter.GaussianBlur(220)))
            layer.alpha_composite(glow)
            self._shade = layer
        return self._shade

    def flat_backdrop(self):
        """Without a held frame (e.g. no video): the theme's panel with depth."""
        if 'flat' not in self._backdrop:
            W, H = self.W, self.H
            im = Image.new('RGBA', (W, H), self.th.panel_solid[:3] + (255,))
            dark = Image.new('RGBA', (W, H), (0, 0, 0, 255))
            dark.putalpha(Image.linear_gradient('L').resize((W, H)).point(lambda v: int(v * 0.5)))
            im.alpha_composite(dark)
            self._backdrop['flat'] = im
        return self._backdrop['flat']

    def header(self):
        """The header in pieces, {key: (image, x, y)}, so they can build one after another."""
        if self._header is not None:
            return self._header
        tpl, th = self.tpl, self.th
        champ = tpl.cfg['championship']
        W = self.W
        parts = {}
        # The event flag waving in the top right, fading into the scene
        flag = load_image(gcfg.resolve_path(champ.get('event_flag')), autocrop=False)
        if flag is not None:
            fw, fh = int(W * 0.40), 250
            cloth = wave_cloth(cover_image(flag, fw, fh), phase=1.1)
            ramp = Image.linear_gradient('L').rotate(90).resize((fw, fh))       # 255 left -> 0 right
            ramp = ImageChops.invert(ramp).point(lambda v: int(v * 0.42))
            down = ImageChops.invert(Image.linear_gradient('L').resize((fw, fh)))
            cloth.putalpha(ImageChops.multiply(ImageChops.multiply(cloth.getchannel('A'), ramp), down))
            parts['flag'] = (cloth, W - fw, 0)
        x = 96
        if tpl.logo:
            lg = fit_image(tpl.logo, 250, 118)
            parts['logo'] = (lg, x, int(126 - lg.height / 2))
            x += lg.width + 44
            parts['divider'] = (Image.new('RGBA', (2, 112), (255, 255, 255, 60)), x - 22, 70)
        f_lab = font(24, 'bold_cond')
        label = 'STARTING GRID'
        lw = int(text_w(f_lab, label) + 30)
        tab = Image.new('RGBA', (lw + 22, 30), (0, 0, 0, 0))
        ImageDraw.Draw(tab).polygon([(10, 0), (lw + 20, 0), (lw + 10, 30), (0, 30)], fill=th.accent)
        text_vcenter(tab, 10 + lw / 2, 15, label, f_lab, (255, 255, 255, 255), 'm')
        parts['label'] = (tab, x - 10, 58)
        sub = '   ·   '.join(s.upper() for s in (champ.get('season'), champ.get('round')) if s)
        if sub:
            f_sub = font(24, 'semibold_cond')
            im = Image.new('RGBA', (int(text_w(f_sub, sub)) + 8, 36), (0, 0, 0, 0))
            text_vcenter(im, 0, 18, sub, f_sub, th.text[:3] + (200,))
            parts['sub'] = (im, x + lw + 26, 55)
        right = W - 96 - (tpl.sponsor_w('title', 50) + 36 if tpl.sponsor('title') else 0)
        title = ((champ.get('event_name') or '').strip() or gcfg.clean_track_name(tpl.race.track)).upper()
        f_title = fit_font(title, right - x, 92, 'bold')
        im = Image.new('RGBA', (int(text_w(f_title, title)) + 24, 118), (0, 0, 0, 0))
        shadow = Image.new('RGBA', im.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((3, 4), title, font=f_title, fill=(0, 0, 0, 200))
        im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
        ImageDraw.Draw(im).text((0, 0), title, font=f_title, fill=th.text)
        parts['title'] = (im, x, 96)
        info = '   ·   '.join(s for s in (
            (champ.get('name') or '').strip().upper(),
            gcfg.clean_track_name(tpl.race.track_layout).upper() if tpl.race.track_layout else '') if s)
        if info:
            f_info = font(24, 'semibold_cond')
            iw = int(min(right - x, 60 + text_w(f_info, info) + 8))
            im = Image.new('RGBA', (iw, 36), (0, 0, 0, 0))
            ImageDraw.Draw(im).rectangle((0, 16, 46, 19), fill=th.accent2)
            text_vcenter(im, 60, 18, ellipsize(info, f_info, iw - 60), f_info, th.accent2)
            parts['info'] = (im, x, 196)
        if tpl.sponsor('title'):
            im = Image.new('RGBA', (W, 120), (0, 0, 0, 0))
            tpl.sponsor_mark(im, W - 96, 60, 'title', 50)
            bbox = im.getbbox()
            if bbox:
                parts['sponsor'] = (im.crop(bbox), bbox[0], 20 + bbox[1])
        self._header = parts
        return parts

    def slot(self, name, pos, w, h):
        """One car's slot as layers: the slanted bar (dark glass with the driver's flag waving under the right
        end, the team colour on its leading edge), the details on it (position, logo, name, number, flag, team)
        and the car standing out of its right end."""
        tpl, th = self.tpl, self.th
        m = tpl.meta(name)
        s = int(h * SKEW)
        car = tpl.grid_car(name, int(h * 2.9), int(h * 1.18))
        over = int(car.width * 0.30) if car is not None else 0
        mask = Image.new('L', (w * 4, h * 4), 0)
        ImageDraw.Draw(mask).polygon([(s * 4, 0), (w * 4, 0), ((w - s) * 4, h * 4), (0, h * 4)], fill=255)
        mask = mask.resize((w, h), Image.LANCZOS)
        colour = tpl.driver_color(name)
        bar = Image.new('RGBA', (w, h), (8, 10, 16, 205))
        # Undertone: the driver's flag waving across the right of the bar (else the team colour), fading in from
        # the dark glass behind the name
        ramp = Image.linear_gradient('L').rotate(90).resize((w, h)).point(lambda v: int((v / 255) ** 1.8 * 175))
        flag = self.flag(m.get('country'))
        if flag is not None:
            # Wide enough to show most of the flag's height (a thin strip of the middle reads as stripes)
            fw = int(min(w * 0.62, h * flag.width / flag.height * 1.5))
            cloth = wave_cloth(cover_image(flag, fw, h), phase=pos * 0.9, amp=max(2, h * 0.05),
                               wavelength=max(80, fw * 0.55), shade=0.35)
            # Feathered on its left so it melts into the glass rather than starting at a hard edge
            feather = Image.linear_gradient('L').rotate(90).resize((fw, h))   # 0 left -> 255 right
            feather = feather.point(lambda v: int(255 * min(1.0, v / 255 / 0.65) ** 1.6))
            cloth.putalpha(ImageChops.multiply(cloth.getchannel('A'), feather))
            wash = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            wash.alpha_composite(cloth, (w - fw, 0))
            wash.putalpha(ImageChops.multiply(wash.getchannel('A'), ramp))
        else:
            wash = Image.new('RGBA', (w, h), colour[:3] + (255,))
            wash.putalpha(ramp.point(lambda v: int(v * 0.85)))
        bar.alpha_composite(wash)
        sheen = Image.new('RGBA', (w, h), (255, 255, 255, 255))
        sheen.putalpha(ImageChops.invert(Image.linear_gradient('L').resize((w, h))).point(lambda v: int(v * 0.10)))
        bar.alpha_composite(sheen)
        bar.putalpha(ImageChops.multiply(bar.getchannel('A'), mask))
        d = ImageDraw.Draw(bar)
        d.line((s, 0, w, 0), fill=(255, 255, 255, 110), width=2)           # top edge catches the light
        d.polygon([(s, 0), (s + 7, 0), (7, h), (0, h)], fill=colour)     # team colour edge

        content = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        pw = int(h * 1.0)
        pcol = rgba({1: '#F2C94C', 2: '#D7DEE8', 3: '#D9965B'}.get(pos, '#FFFFFF'))
        label = str(pos)
        f_pos = fit_font(label, pw * 0.9, int(h * 0.66), 'bold')
        text_vcenter(content, s // 2 + pw / 2 + 6, h / 2 + 2, label, f_pos, pcol, 'm')
        x = pw + 20
        right = w - s - 18 - (int(car.width * 0.70) if car is not None else 0)
        logo = tpl.badge(name, int(h * 0.60))
        if logo is not None:
            lw_max = int(h * 1.25)
            logo = fit_image(logo, lw_max, int(h * 0.60))
            content.alpha_composite(logo, (int(x + (lw_max - logo.width) / 2), int((h - logo.height) / 2)))
            x += lw_max + 20
        display = (m.get('display') or name).upper()
        # Qualifying: pole's lap time (in the second accent), everyone else their gap; nothing without a time
        lap = self.quali.get(name)
        lap_img = self.lap_tag(lap, h) if lap is not None else None
        # The same right edge on every card (clear of the widest car), so the times line up down the grid
        lap_right = w - s - 18 - int(h * 2.9 * 0.70)
        if lap_img is not None:
            right = min(right, lap_right - lap_img.width - 16)
        meta_right = right
        if h >= 80:
            f = fit_font(display, right - x, min(46, int(h * 0.36)), 'bold')
            sh = Image.new('RGBA', content.size, (0, 0, 0, 0))
            ImageDraw.Draw(sh).text((x + 2, int(h * 0.13) + 3), display, font=f, fill=(0, 0, 0, 160))
            content.alpha_composite(sh.filter(ImageFilter.GaussianBlur(3)))
            ImageDraw.Draw(content).text((x, int(h * 0.13)), display, font=f, fill=th.text)
            tpl.meta_row(content, x, int(h * 0.73), name, meta_right - x, show_flag=False)
        else:
            f = fit_font(display, (right - x) * 0.55, int(h * 0.42), 'bold')
            text_vcenter(content, x, h / 2, display, f, th.text)
            nw = text_w(f, display) + 16
            tpl.meta_row(content, int(x + nw), h / 2, name, int(meta_right - x - nw), show_flag=False)
        if lap_img is not None:
            # Centred on the card's height, between the name and the car
            content.alpha_composite(lap_img, (int(lap_right - lap_img.width), int((h - lap_img.height) / 2)))

        # The car, grounded with a soft shadow, standing out of the top of the bar (pt above it)
        pt = int(h * 0.3)
        car_layer = None
        if car is not None:
            car_layer = Image.new('RGBA', (w + over, h + pt), (0, 0, 0, 0))
            cx, cy = w + over - car.width - 4, int(pt + h - car.height + h * 0.06)
            shadow = Image.new('RGBA', car_layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(shadow).ellipse((cx + car.width * 0.08, pt + h * 0.84, cx + car.width * 0.95,
                                            pt + h * 1.03), fill=(0, 0, 0, 150))
            car_layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
            car_layer.alpha_composite(car.crop((0, max(0, -cy), car.width, car.height)), (int(cx), int(max(0, cy))))
        return {'bar': bar, 'content': content, 'car': car_layer, 'mask': mask, 'pt': pt, 'skew': s}

    def lap_tag(self, lap, h):
        """A qualifying time, the digits leaning forward like motorsport timing graphics: pole's lap time in black
        on a slanted tag in the second accent, everyone else's gap in white on its own (no box)."""
        th = self.th
        is_pole = abs(lap - self.pole) < 1e-6
        label = format_lap_time(lap) if is_pole else f"+{lap - self.pole:.3f}"
        f = font(min(42, int(h * 0.38)), 'bold')
        tw = int(text_w(f, label))
        th_ = int(f.size * 1.25)
        # The digits, sheared forward (the top leans right)
        lean = 0.2
        text = Image.new('RGBA', (tw + 8, th_), (0, 0, 0, 0))
        fg = (12, 14, 20, 255) if is_pole else th.text
        text_vcenter(text, 4, th_ / 2 + 1, label, f, fg)
        shift = int(lean * th_)
        text = text.transform((text.width + shift, th_), Image.AFFINE, (1, lean, -shift, 0, 1, 0), Image.BICUBIC)
        if not is_pole:
            # Gaps: the digits on their own, a soft shadow keeping them clear of the flag behind
            out = Image.new('RGBA', (text.width + 12, text.height + 12), (0, 0, 0, 0))
            shadow = Image.new('RGBA', text.size, (0, 0, 0, 255))
            shadow.putalpha(text.getchannel('A').point(lambda v: int(v * 0.85)))
            out.alpha_composite(shadow, (8, 8))
            out = out.filter(ImageFilter.GaussianBlur(4))
            out.alpha_composite(text, (6, 6))
            return out
        # Pole: on a tag at the cards' slant, with a bright edge leading it
        tag_h = int(h * 0.5)
        slant = int(tag_h * SKEW)
        pad = 14
        tag_w = text.width + pad * 2 + slant
        tag = Image.new('RGBA', (tag_w + 2, tag_h), (0, 0, 0, 0))
        m = Image.new('L', (tag.width * 4, tag_h * 4), 0)
        ImageDraw.Draw(m).polygon([(slant * 4, 0), (tag_w * 4, 0), ((tag_w - slant) * 4, tag_h * 4), (0, tag_h * 4)],
                                  fill=255)
        m = m.resize(tag.size, Image.LANCZOS)
        fill = Image.new('RGBA', tag.size, th.accent2[:3] + (255,))
        fill.putalpha(ImageChops.multiply(fill.getchannel('A'), m))
        tag.alpha_composite(fill)
        ImageDraw.Draw(tag).polygon([(slant, 0), (slant + 5, 0), (5, tag_h), (0, tag_h)], fill=th.text[:3] + (230,))
        tag.alpha_composite(text, (int(slant / 2 + pad), int((tag_h - th_) / 2)))
        return tag

    @staticmethod
    def flag(country):
        """The full flag image for a country code (Graphics/Flags), or None."""
        if not country:
            return None
        for ext in ('.png', '.jpg'):
            im = load_image(os.path.join(gcfg.FLAG_DIR, f"{country.upper()}{ext}"), autocrop=False)
            if im is not None:
                return im
        return None

    def slots(self):
        if self._slots is None:
            self._slots = []
            for i, (name, (x, y, w, h)) in enumerate(zip(self.grid, self.layout())):
                layers = self.slot(name, i + 1, w, h)
                layers.update(x=x, y=y, w=w, h=h, i=i)
                self._slots.append(layers)
        return self._slots

    # ------------------------------------------------------------------ one frame
    def _header_frame(self, frame, t, out):
        """Builds the header t seconds in; out 0..1 takes it away again."""
        parts = self.header()
        keep = 1 - ease_in(out)
        lift = -18 * ease_in(out)

        def show(key, a=1.0, dx=0, dy=0, img=None):
            if key in parts and a * keep > 0.01:
                im, x, y = parts[key]
                paste(frame, fade(img if img is not None else im, a * keep), x + dx, y + dy + lift)

        show('flag', ease_in_out(t / 1.2))
        e = ease_out_quint(t / 0.6)
        show('logo', e, dx=-24 * (1 - e))
        show('divider', ease_in_out((t - 0.2) / 0.4))
        if 'label' in parts:
            im = wipe(parts['label'][0], ease_in_out((t - 0.1) / 0.32), skew=10)
            if im is not None:
                show('label', img=im)
        e = ease_out_quint((t - 0.4) / 0.5)
        show('sub', e, dx=-14 * (1 - e))
        if 'title' in parts:
            # Rising from behind a line at its baseline
            im = parts['title'][0]
            e = ease_out_quint((t - 0.18) / 0.6)
            rise = int(im.height * 0.9 * (1 - e))
            if rise < im.height - 1:
                show('title', img=im.crop((0, 0, im.width, im.height - rise)), dy=rise)
        if 'info' in parts:
            im = wipe(parts['info'][0], ease_in_out((t - 0.45) / 0.45))
            if im is not None:
                show('info', img=im)
        show('sponsor', ease_out_quint((t - 0.5) / 0.6))
        # Accent rule drawing across under the header
        rw = int(self.W * ease_in_out((t - 0.15) / 0.8) * keep)
        if rw > 2:
            d = ImageDraw.Draw(frame)
            d.rectangle((0, 240, rw, 243), fill=self.th.accent[:3] + (255,))
            d.rectangle((0, 244, int(rw * 0.92), 245), fill=self.th.accent2[:3] + (170,))

    def _slot_frame(self, frame, sl, t, out):
        """One car's slot t seconds after its bar starts opening; out 0..1 wipes it shut again."""
        w, h, pt, skew = sl['w'], sl['h'], sl['pt'], sl['skew']
        x, y = sl['x'], sl['y']
        q = ease_in_out(out)
        p = (1 - q) if q > 0 else ease_in_out(t / WIPE)   # shutting: the whole slot wipes away right to left
        if p <= 0.001:
            return
        bar = wipe(sl['bar'], p, skew)
        # Glint: a band of light crossing the bar once it is open
        g = (t - GLINT_AT) / GLINT_DUR
        if 0 < g < 1 and q == 0:
            bar = bar.copy()
            band = Image.new('L', (w, h), 0)
            bx = int(-h + g * (w + 2 * h))
            ImageDraw.Draw(band).polygon([(bx, 0), (bx + 70, 0), (bx + 70 - h * 0.6, h), (bx - h * 0.6, h)],
                                         fill=int(95 * math.sin(math.pi * g)))
            band = ImageChops.multiply(band.filter(ImageFilter.GaussianBlur(10)), sl['mask'])
            light = Image.new('RGBA', (w, h), (255, 255, 255, 255))
            light.putalpha(band)
            bar.alpha_composite(light)
        paste(frame, bar, x, y)
        # The leading edge: a bright slanted line riding the wipe
        if 0.02 < p < 0.98:
            ex = x + int((w + skew) * p)
            a = int(230 * math.sin(math.pi * p))
            edge = Image.new('RGBA', (skew + 10, h), (0, 0, 0, 0))
            ImageDraw.Draw(edge).polygon([(skew + 2, 0), (skew + 6, 0), (6, h), (2, h)], fill=(255, 255, 255, a))
            paste(frame, edge.filter(ImageFilter.GaussianBlur(1.2)), ex - skew - 4, y)
        # Details glide in inside the opened bar
        e = ease_out_quint((t - CONTENT_DELAY) / CONTENT_DUR)
        if e > 0.01:
            shift = int(-CONTENT_SHIFT * (1 - e))
            content = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            content.alpha_composite(sl['content'].crop((-shift, 0, w, h)), (0, 0))
            content = wipe(content, p, skew)
            if content is not None:
                paste(frame, fade(content, min(1.0, e * 1.3)), x, y)
        # The car settles into its box
        if sl['car'] is not None:
            e = ease_out_quint((t - CAR_DELAY) / CAR_DUR)
            a = min(1.0, e * 1.6) * (1 - ease_in(min(1.0, q * 1.6)))
            if a > 0.01:
                paste(frame, fade(sl['car'], a), x + CAR_SHIFT * (1 - e) - 30 * q, y - pt)

    def frame(self, local, total, backdrop_path=None, enter_at=0.0):
        """The scene `local` seconds into an item of `total` seconds; it starts building at `enter_at`."""
        W, H = self.W, self.H
        t = local - enter_at
        exit_t = EXIT_DUR - (total - local)   # seconds into the exit (negative before it)

        # Background: push-in on the blurred held frame, fading to show the sharp frame underneath on the exit
        bd = self.backdrop(backdrop_path)
        if bd is not None:
            z = PUSH_IN * max(0.0, min(1.0, local / max(1.0, total)))
            cw, ch = int(W * (1 + z)), int(H * (1 + z))
            ox, oy = (bd.width - cw) // 2, (bd.height - ch) // 2
            frame = bd.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR)
        else:
            frame = self.flat_backdrop().copy()
        frame.alpha_composite(self.shade())
        if exit_t > 0:
            frame.putalpha(int(255 * (1 - ease_in_out((exit_t - 0.15) / (EXIT_DUR - 0.15)))))

        self._header_frame(frame, t - HEADER_AT, max(0.0, min(1.0, (exit_t - 0.1) / 0.4)))
        n = len(self.grid)
        for sl in self.slots():
            # Shut bottom of the grid first, quickly
            out = max(0.0, min(1.0, (exit_t - (n - 1 - sl['i']) * EXIT_STAGGER) / EXIT_WIPE))
            self._slot_frame(frame, sl, t - CARS_AT - sl['i'] * CAR_STAGGER, out)
        return frame
