"""The pixel font and the top-down GT3 car sprites the race visualizer draws with.

Everything is drawn at a small logical size and scaled up with nearest-neighbour, so it stays crisp
and blocky. Text is cached as masks, sprites per colour, so a frame is mostly pastes.
"""
import unicodedata

from PIL import Image

# 5x7 capitals, digits and punctuation ('#' = ink). Narrow glyphs set their own width.
_FONT = {
    "A": ".###. #...# #...# ##### #...# #...# #...#",
    "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.",
    "D": "####. #...# #...# #...# #...# #...# ####.",
    "E": "##### #.... #.... ####. #.... #.... #####",
    "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####",
    "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.",
    "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#",
    "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.",
    "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.",
    "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.",
    "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.",
    "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####",
    "3": "####. ....# ....# .###. ....# ....# ####.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.",
    "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.",
    "9": ".###. #...# #...# .#### ....# ...#. .##..",
    "+": "..... ..#.. ..#.. ##### ..#.. ..#.. .....",
    "-": "..... ..... ..... ##### ..... ..... .....",
    "/": "....# ...#. ...#. ..#.. .#... .#... #....",
    "#": ".#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.",
    "%": "##..# ##..# ...#. ..#.. .#... #..## #..##",
    "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
    "&": ".##.. #..#. .##.. .#... #.#.# #..#. .##.#",
    "=": "..... ..... ##### ..... ##### ..... .....",
    "_": "..... ..... ..... ..... ..... ..... #####",
    "<": "...#. ..#.. .#... #.... .#... ..#.. ...#.",
    ">": ".#... ..#.. ...#. ....# ...#. ..#.. .#...",
    "[": "### #.. #.. #.. #.. #.. ###",
    "]": "### ..# ..# ..# ..# ..# ###",
    "(": "..# .#. #.. #.. #.. .#. ..#",
    ")": "#.. .#. ..# ..# ..# .#. #..",
    ".": ".. .. .. .. .. ## ##",
    ",": ".. .. .. .. .. .# #.",
    ":": ".. ## ## .. ## ## ..",
    "'": "# # . . . . .",
    "!": "# # # # # . #",
    " ": "... ... ... ... ... ... ...",
}

# 3x5 digits for the car number badges
_TINY = {
    "0": "### #.# #.# #.# ###", "1": ".#. ##. .#. .#. ###", "2": "### ..# ### #.. ###",
    "3": "### ..# .## ..# ###", "4": "#.# #.# ### ..# ..#", "5": "### #.. ### ..# ###",
    "6": "### #.. ### #.# ###", "7": "### ..# ..# .#. .#.", "8": "### #.# ### #.# ###",
    "9": "### #.# ### ..# ###",
}


def _glyphs(table):
    out = {}
    for ch, spec in table.items():
        rows = spec.split(" ")
        img = Image.new("L", (len(rows[0]), len(rows)), 0)
        img.putdata([255 if c == "#" else 0 for row in rows for c in row])
        out[ch] = img
    return out


GLYPHS = _glyphs(_FONT)
TINY_GLYPHS = _glyphs(_TINY)
LINE_H = 7
TINY_H = 5


def plain(text):
    """Upper-case ASCII the font can draw (accents dropped, unknown characters as '?')."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().upper()
    return "".join(c if c in GLYPHS else "?" for c in text)


_mask_cache = {}


def text_mask(text, scale=1, tiny=False):
    """An 'L' mask of the text (1 px between letters), cached."""
    key = (text, scale, tiny)
    m = _mask_cache.get(key)
    if m is not None:
        return m
    glyphs = TINY_GLYPHS if tiny else GLYPHS
    chars = [glyphs.get(c) for c in (text if tiny else plain(text))]
    chars = [g for g in chars if g is not None]
    h = TINY_H if tiny else LINE_H
    w = max(1, sum(g.width for g in chars) + max(0, len(chars) - 1))
    m = Image.new("L", (w, h), 0)
    x = 0
    for g in chars:
        m.paste(g, (x, 0))
        x += g.width + 1
    if scale != 1:
        m = m.resize((w * scale, h * scale), Image.NEAREST)
    if len(_mask_cache) > 4000:
        _mask_cache.clear()
    _mask_cache[key] = m
    return m


def text_width(text, scale=1, tiny=False):
    return text_mask(text, scale, tiny).width


def draw_text(img, xy, text, colour, scale=1, anchor="l", tiny=False, shadow=None):
    """Pastes text; anchor l/r/c on x, y is the top. Returns the text width."""
    m = text_mask(text, scale, tiny)
    x, y = int(round(xy[0])), int(round(xy[1]))
    if anchor == "r":
        x -= m.width
    elif anchor == "c":
        x -= m.width // 2
    if shadow:
        img.paste(shadow, (x + scale, y + scale), m)
    img.paste(colour, (x, y), m)
    return m.width


# ---------------------------------------------------------------- colours

def rgb(colour):
    if isinstance(colour, str):
        c = colour.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(colour)


def shade(colour, amount):
    """Lighter (amount > 0) or darker (amount < 0), amount in -1..1."""
    r, g, b = rgb(colour)
    if amount >= 0:
        return tuple(int(c + (255 - c) * amount) for c in (r, g, b))
    return tuple(int(c * (1 + amount)) for c in (r, g, b))


def luminance(colour):
    r, g, b = rgb(colour)
    return 0.299 * r + 0.587 * g + 0.114 * b


def mix(a, b, f):
    a, b = rgb(a), rgb(b)
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


# ---------------------------------------------------------------- the car

TYRE = (14, 14, 17)
WING = (24, 25, 30)
GLASS = (20, 28, 43)
GLASS_HI = (74, 92, 124)
HEADLIGHT = (255, 243, 196)
TAILLIGHT = (255, 58, 58)



# Top halves of a GT3 seen from above, nose pointing right; the bottom half is the mirror image.
# w/W wing and endplates, k tyre, B body, L/D light/dark body, S livery stripe, g/G glass, h/r head/tail
# lights, x wing mounts
_CAR_LARGE = [
    "......kkkkkk..........kkkkkk....",
    "WWW..DLLLLLLLD.......DLLLLLLLD..",
    "www.rLBBBBBBBBLLLLLLLBBBBBBBBBh.",
    "www.DBBBBBBBggBBBBggggBBBBBBBBB.",
    "www.DBBBBBBBggBBBBgGggBBBBBBBBBB",
    "wwwxDBBBBBBBggBBBBggggBBBBBBBBBB",
    "wwwxDSSSSSSSggSSSSggggSSSSSSSSSS",
]
_CAR_SMALL = [
    "....kkkk......kkkk....",
    "WW.DLLLLLD...DLLLLLD..",
    "ww.rBBBBBgBBgggBBBBBh.",
    "wwxDBBBBBgBBgGgBBBBBBB",
    "wwxDSSSSSgSSgggSSSSSSS",
]


def car_layout(large=True):
    top = _CAR_LARGE if large else _CAR_SMALL
    bottom = [row.replace("L", "D").replace("G", "g") for row in reversed(top)]
    return [list(row) for row in top + bottom]


def car_size(large=True):
    top = _CAR_LARGE if large else _CAR_SMALL
    return len(top[0]), len(top) * 2


_sprite_cache = {}


def car_sprite(primary, stripe, large=True):
    key = (rgb(primary), rgb(stripe), large)
    s = _sprite_cache.get(key)
    if s is not None:
        return s
    body = rgb(primary)
    palette = {"w": WING, "W": (70, 74, 84), "k": TYRE, "B": body, "L": shade(body, 0.3),
               "D": shade(body, -0.4), "S": rgb(stripe), "g": GLASS, "G": GLASS_HI, "h": HEADLIGHT,
               "r": TAILLIGHT, "x": (12, 12, 16)}
    grid = car_layout(large)
    img = Image.new("RGBA", (len(grid[0]), len(grid)), (0, 0, 0, 0))
    img.putdata([palette[c] + (255,) if c != "." else (0, 0, 0, 0) for row in grid for c in row])
    _sprite_cache[key] = img
    return img


def stopwatch(colour=(170, 70, 255)):
    """9x9 fastest-lap badge: a stopwatch."""
    spec = ["...ppp...", "....p....", "..ppppp..", ".ppwwwpp.", ".pwwpwwp.", ".pwwpwwp.", ".pwwwwwp.",
            ".ppwwwpp.", "..ppppp.."]
    col = {"p": rgb(colour) + (255,), "w": (255, 255, 255, 255), ".": (0, 0, 0, 0)}
    img = Image.new("RGBA", (9, 9), (0, 0, 0, 0))
    img.putdata([col[c] for row in spec for c in row])
    return img
