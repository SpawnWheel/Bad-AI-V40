"""Low-level Pillow helpers: fonts, anti-aliased shapes, text fitting and image assets."""
import functools
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = [
    'C:/Windows/Fonts/bahnschrift.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
]
FALLBACK_BOLD = ['C:/Windows/Fonts/segoeuib.ttf', 'C:/Windows/Fonts/arialbd.ttf']
FALLBACK_REGULAR = ['C:/Windows/Fonts/segoeui.ttf', 'C:/Windows/Fonts/arial.ttf']

# Style name -> Bahnschrift named instance
STYLES = {
    'light': 'Light',
    'regular': 'Regular',
    'semibold': 'SemiBold',
    'bold': 'Bold',
    'cond': 'Condensed',
    'semibold_cond': 'SemiBold Condensed',
    'bold_cond': 'Bold Condensed',
}


@functools.lru_cache(maxsize=512)
def font(size, style='bold'):
    size = max(6, int(round(size)))
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            f = ImageFont.truetype(path, size)
            try:
                f.set_variation_by_name(STYLES.get(style, 'Bold'))
            except Exception:
                pass
            return f
    bold = style in ('bold', 'semibold', 'semibold_cond', 'bold_cond')
    for path in (FALLBACK_BOLD if bold else FALLBACK_REGULAR):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def rgba(hex_color, alpha=1.0):
    h = (hex_color or '#000000').lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, int(round(255 * max(0.0, min(1.0, alpha)))))


def text_w(f, s):
    return f.getlength(s)


def text_box(f, s):
    """(width, ascent-to-baseline height) for vertical centring."""
    l, t, r, b = f.getbbox(s or 'Ag')
    return r - l, b - t


def fit_font(s, max_w, size, style='bold', min_size=12):
    while size > min_size and text_w(font(size, style), s) > max_w:
        size -= 1
    return font(size, style)


def ellipsize(s, f, max_w):
    if text_w(f, s) <= max_w:
        return s
    while s and text_w(f, s + '…') > max_w:
        s = s[:-1]
    return s.rstrip() + '…'


def wrap(s, f, max_w, max_lines=3):
    words = (s or '').split()
    lines, cur = [], ''
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_w(f, trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = ellipsize(lines[-1] + ' …', f, max_w)
    return lines


def draw_text(img, xy, s, f, fill, anchor='la'):
    ImageDraw.Draw(img).text(xy, s, font=f, fill=fill, anchor=anchor)


def text_vcenter(img, x, cy, s, f, fill, anchor_x='l'):
    """Draws text vertically centred on cy using the cap height."""
    ImageDraw.Draw(img).text((x, cy), s, font=f, fill=fill, anchor=anchor_x + 'm')


SS = 4  # supersampling factor for shape masks


def round_rect(img, box, radius, fill, corners=None):
    """Anti-aliased rounded rectangle composited onto img."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    mask = Image.new('L', (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=max(0, radius) * SS,
                                           fill=255, corners=corners)
    mask = mask.resize((w, h), Image.LANCZOS)
    layer = Image.new('RGBA', (w, h), fill)
    alpha = Image.new('L', (w, h), 0)
    alpha.paste(layer.getchannel('A'), mask=mask)
    layer.putalpha(alpha)
    img.alpha_composite(layer, (x0, y0))


def round_outline(img, box, radius, color, width=2):
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = x1 - x0, y1 - y0
    mask = Image.new('L', (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS,
                                           outline=255, width=width * SS)
    mask = mask.resize((w, h), Image.LANCZOS)
    layer = Image.new('RGBA', (w, h), color)
    alpha = Image.new('L', (w, h), 0)
    alpha.paste(layer.getchannel('A'), mask=mask)
    layer.putalpha(alpha)
    img.alpha_composite(layer, (x0, y0))


def polygon(img, points, fill):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = int(min(xs)), int(min(ys))
    w, h = int(max(xs)) - x0 + 2, int(max(ys)) - y0 + 2
    mask = Image.new('L', (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).polygon([((x - x0) * SS, (y - y0) * SS) for x, y in points], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    layer = Image.new('RGBA', (w, h), fill)
    alpha = Image.new('L', (w, h), 0)
    alpha.paste(layer.getchannel('A'), mask=mask)
    layer.putalpha(alpha)
    img.alpha_composite(layer, (x0, y0))


def h_gradient(w, h, left, right):
    """Horizontal RGBA gradient image between two rgba tuples."""
    grad = Image.linear_gradient('L').rotate(90).resize((w, h))  # 255 on the left -> 0 right
    a = Image.new('RGBA', (w, h), left)
    b = Image.new('RGBA', (w, h), right)
    return Image.composite(a, b, grad)


def add_shadow(img, margin=18, blur=10, alpha=0.55):
    """Returns (new_img, margin): img padded with a soft drop shadow."""
    w, h = img.size
    size = (w + margin * 2, h + margin * 2)
    sh = Image.new('L', size, 0)
    sh.paste(img.getchannel('A').point(lambda v: int(v * alpha)), (margin, margin + 4))
    out = Image.new('RGBA', size, (0, 0, 0, 255))
    out.putalpha(sh.filter(ImageFilter.GaussianBlur(blur)))
    out.alpha_composite(img, (margin, margin))
    return out, margin


@functools.lru_cache(maxsize=128)
def load_image(path, tint_white=False, autocrop=True):
    """Loads PNG/DDS/etc as RGBA, cropping to visible pixels."""
    if not path or not os.path.exists(path):
        return None
    try:
        im = Image.open(path)
        im.load()
        im = im.convert('RGBA')
    except Exception as e:
        print(f"  Could not load image {path}: {e}")
        return None
    if autocrop:
        bbox = im.getchannel('A').point(lambda v: 255 if v > 8 else 0).getbbox()
        if bbox:
            im = im.crop(bbox)
    if tint_white:
        white = Image.new('RGBA', im.size, (255, 255, 255, 255))
        white.putalpha(im.getchannel('A'))
        im = white
    return im


def fit_image(im, max_w, max_h):
    if im is None:
        return None
    scale = min(max_w / im.width, max_h / im.height)
    return im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)


def ease_out(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def ease_in(p):
    p = max(0.0, min(1.0, p))
    return p ** 3


def with_opacity(img, opacity):
    if opacity >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel('A').point(lambda v: int(v * max(0.0, opacity))))
    return out



def wave_cloth(img, phase=0.0, amp=None, wavelength=None, shade=0.28):
    """img rippled like a flag in the wind: columns shift up and down along a sine and the folds
    are lit and shaded. Returns a new image the same size (edges stay covered)."""
    import math
    w, h = img.size
    amp = amp if amp is not None else max(2, h * 0.035)
    wavelength = wavelength or max(60, w * 0.45)
    # Stretch a little so the shifted columns never show a gap at the top or bottom
    src = img.resize((w, int(h + amp * 2 + 2)), Image.LANCZOS)
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    light = Image.new('L', (w, h), 0)
    dark = Image.new('L', (w, h), 0)
    ld, dd = ImageDraw.Draw(light), ImageDraw.Draw(dark)
    for x in range(w):
        a = 2 * math.pi * x / wavelength + phase
        dy = amp * math.sin(a)
        out.paste(src.crop((x, int(amp + dy), x + 1, int(amp + dy) + h)), (x, 0))
        k = math.cos(a)
        if k > 0:
            ld.line((x, 0, x, h), fill=int(255 * shade * k))
        else:
            dd.line((x, 0, x, h), fill=int(255 * shade * -k))
    alpha = out.getchannel('A')
    white = Image.new('RGBA', (w, h), (255, 255, 255, 255))
    white.putalpha(ImageChops.multiply(light, alpha))
    black = Image.new('RGBA', (w, h), (0, 0, 0, 255))
    black.putalpha(ImageChops.multiply(dark, alpha))
    out.alpha_composite(white)
    out.alpha_composite(black)
    return out


def cover_image(im, w, h):
    """im scaled and centre-cropped to fill w x h exactly."""
    scale = max(w / im.width, h / im.height)
    im = im.resize((max(1, int(im.width * scale + 0.5)), max(1, int(im.height * scale + 0.5))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))
