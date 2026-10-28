"""Colours, fonts, the background photo and the small widgets every screen is built from."""
import os
import tkinter as tk
from tkinter import ttk

import racefiles

# Dark, blue-tinted palette that sits on the darkened background photo
BG = "#0b1323"
CARD = "#131e33"
CARD_HOVER = "#18253f"
BORDER = "#22314f"
BORDER_STRONG = "#3a4f78"
TEXT = "#eef2f8"
MUTED = "#a6b2c8"
DIM = "#6f7d98"
ACCENT = "#e0352b"         # racing red, shared by both sim logos
ACCENT_HOVER = "#c22a21"
ACCENT_SOFT = "#3a161a"
SUCCESS = "#33c27a"
SUCCESS_SOFT = "#123323"
WARNING = "#f0a63a"
WARNING_SOFT = "#3a2b12"
INFO = "#5b9bff"
INFO_SOFT = "#16284a"
NEUTRAL_SOFT = "#1d2a44"
INPUT = "#0e182b"
HEADER = "#080e1a"

CONSOLE_BG = "#070c16"
CONSOLE_FG = "#d5d9e0"
CONSOLE_DIM = "#6f7d98"
CONSOLE_SEL = "#1d2a44"

FONT = "Segoe UI"
FONT_MONO = "Consolas"

LOGO_DIR = os.path.join(racefiles.APP_DIR, "Graphics", "Logos")
BACKGROUND = os.path.join(racefiles.APP_DIR, "Graphics", "Launcher", "background.jpg")
SIM_LOGOS = {"AMS2": "AMS2Logo.png", "RaceRoom": "Raceroom.png"}
SIM_NAMES = {"AMS2": "Automobilista 2", "RaceRoom": "RaceRoom"}

OVERLAY = (6, 16, 40)       # the blue the photo is darkened towards
OVERLAY_ALPHA = 0.72
CIRCLE = (236, 241, 249)    # the light circle behind the sim logos

_image_cache = {}


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


# ---------------------------------------------------------------- the background photo

_base = None


def _base_photo():
    """The background photo, blurred a touch and darkened with the blue overlay (once)."""
    global _base
    if _base is None:
        from PIL import Image, ImageFilter
        try:
            im = Image.open(BACKGROUND).convert("RGB")
        except OSError:
            im = Image.new("RGB", (1600, 900), _rgb(BG))
        im = im.filter(ImageFilter.GaussianBlur(1.2))
        _base = Image.blend(im, Image.new("RGB", im.size, OVERLAY), OVERLAY_ALPHA)
    return _base


def background(width, height, focus=0.5, fade=None):
    """The darkened photo cropped to cover width x height. fade (0-1) fades its lower part into BG."""
    key = ("bg", width, height, focus, fade)
    if key in _image_cache:
        return _image_cache[key]
    from PIL import Image, ImageTk
    base = _base_photo()
    scale = max(width / base.width, height / base.height)
    im = base.resize((max(1, int(base.width * scale + 0.5)), max(1, int(base.height * scale + 0.5))), Image.BILINEAR)
    left = (im.width - width) // 2
    top = int((im.height - height) * focus)
    im = im.crop((left, top, left + width, top + height))
    if fade:
        # Transparent above the fade, then rising to solid BG at the bottom edge
        start = int(height * (1 - fade))
        column = Image.new("L", (1, height))
        column.putdata([0 if y < start else int(255 * (y - start) / max(1, height - 1 - start)) for y in range(height)])
        im = Image.composite(Image.new("RGB", im.size, _rgb(BG)), im, column.resize((width, height)))
    img = ImageTk.PhotoImage(im)
    if len(_image_cache) > 60:
        for k in [k for k in _image_cache if k[0] == "bg"][:20]:
            del _image_cache[k]
    _image_cache[key] = img
    return img


# ---------------------------------------------------------------- logos

def _logo_image(sim):
    from PIL import Image
    im = Image.open(os.path.join(LOGO_DIR, SIM_LOGOS.get(sim, ""))).convert("RGBA")
    box = im.getbbox()
    return im.crop(box) if box else im


def logo(sim, max_w, max_h):
    """The sim's logo as a Tk image fitted into max_w x max_h, drawn as-is (or None)."""
    key = ("logo", sim, max_w, max_h)
    if key not in _image_cache:
        try:
            from PIL import Image, ImageTk
            im = _logo_image(sim)
            im.thumbnail((max_w, max_h), Image.LANCZOS)
            _image_cache[key] = ImageTk.PhotoImage(im)
        except Exception:
            _image_cache[key] = None
    return _image_cache[key]


def logo_badge(sim, size, ring=None):
    """The sim's logo on a light circle, size x size, with an optional coloured ring (hover)."""
    key = ("badge", sim, size, ring)
    if key in _image_cache:
        return _image_cache[key]
    try:
        from PIL import Image, ImageDraw, ImageTk
        k = 4  # draw large and shrink, for smooth edges
        big = size * k
        im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        if ring:
            draw.ellipse((0, 0, big - 1, big - 1), fill=_rgb(ring) + (255,))
            inset = int(big * 0.035)
        else:
            inset = 0
        draw.ellipse((inset, inset, big - 1 - inset, big - 1 - inset), fill=CIRCLE + (255,))
        im = im.resize((size, size), Image.LANCZOS)
        lg = _logo_image(sim)
        lg.thumbnail((int(size * 0.66), int(size * 0.58)), Image.LANCZOS)
        im.alpha_composite(lg, ((size - lg.width) // 2, (size - lg.height) // 2))
        img = ImageTk.PhotoImage(im)
    except Exception:
        img = None
    _image_cache[key] = img
    return img


def setup_styles(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TCombobox", fieldbackground=INPUT, background=NEUTRAL_SOFT, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED, padding=4,
                    selectbackground=INPUT, selectforeground=TEXT)
    style.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", INPUT)], selectforeground=[("readonly", TEXT)],
              background=[("active", BORDER)])
    style.configure("Vertical.TScrollbar", background=BORDER, troughcolor=BG, bordercolor=BG,
                    arrowcolor=DIM, lightcolor=BORDER, darkcolor=BORDER, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", BORDER_STRONG), ("!active", BORDER)])
    style.configure("Console.Vertical.TScrollbar", background="#1d2a44", troughcolor=CONSOLE_BG,
                    bordercolor=CONSOLE_BG, arrowcolor=CONSOLE_DIM, lightcolor=CONSOLE_BG, darkcolor=CONSOLE_BG)
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))


def button(parent, text, command, kind="secondary", size=10, padx=14, pady=6, bg=None):
    """A flat button. kind: primary | secondary | ghost | danger | success | link."""
    parent_bg = bg or parent.cget("bg")
    colours = {
        "primary": (ACCENT, ACCENT_HOVER, "#ffffff"),
        "danger": (ACCENT, ACCENT_HOVER, "#ffffff"),
        "success": (SUCCESS, "#26a565", "#ffffff"),
        "secondary": (NEUTRAL_SOFT, BORDER_STRONG, TEXT),
        "ghost": (parent_bg, NEUTRAL_SOFT, MUTED),
        "link": (parent_bg, parent_bg, "#ff6a5f"),
    }
    normal, hover, fg = colours[kind]
    weight = "bold" if kind in ("primary", "danger", "success") else "normal"
    b = tk.Button(parent, text=text, command=command, bg=normal, fg=fg, activebackground=hover,
                  activeforeground=fg, font=(FONT, size, weight), relief="flat", bd=0, highlightthickness=0,
                  padx=padx, pady=pady, cursor="hand2", disabledforeground=DIM)
    b._colours = (normal, hover)

    def enter(_):
        if str(b.cget("state")) != "disabled":
            b.config(bg=b._colours[1], fg="#ffffff" if kind == "link" else fg)

    def leave(_):
        b.config(bg=b._colours[0], fg=fg)

    b.bind("<Enter>", enter)
    b.bind("<Leave>", leave)
    return b


def label(parent, text="", size=10, colour=TEXT, weight="normal", bg=None, **kw):
    return tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=colour, font=(FONT, size, weight),
                    anchor=kw.pop("anchor", "w"), justify=kw.pop("justify", "left"), **kw)


def card(parent, padx=18, pady=16, bg=CARD):
    return tk.Frame(parent, bg=bg, highlightbackground=BORDER, highlightthickness=1, padx=padx, pady=pady)


def pill(parent, text, fg, bg):
    return tk.Label(parent, text=f" {text} ", bg=bg, fg=fg, font=(FONT, 8, "bold"), padx=6, pady=2)


def bind_tree(widget, sequence, func):
    """Binds an event on a widget and all its children (so a whole card is clickable)."""
    widget.bind(sequence, func, add="+")
    for child in widget.winfo_children():
        if not isinstance(child, (tk.Button, tk.Entry, tk.Text, ttk.Combobox)):
            bind_tree(child, sequence, func)


class PhotoCanvas(tk.Canvas):
    """A canvas showing the darkened background photo, redrawn to fit whenever it is resized.

    on_layout(width, height) is called after each resize so the screen can place its items."""

    def __init__(self, parent, focus=0.5, fade=None, on_layout=None, **kw):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0, **kw)
        self.focus = focus
        self.fade = fade
        self.on_layout = on_layout
        self._job = None
        self._size = None
        self._bg_item = self.create_image(0, 0, anchor="nw")
        self.bind("<Configure>", self._configured)

    def _configured(self, event):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(60, self.redraw)

    def redraw(self):
        self._job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        if (w, h) != self._size:
            self._size = (w, h)
            img = background(w, h, self.focus, self.fade)
            self.itemconfigure(self._bg_item, image=img)
            self._img = img
        self.tag_lower(self._bg_item)
        if self.on_layout:
            self.on_layout(w, h)

    def link(self, x, y, text, command, size=10, anchor="center", colour="#ff6a5f", hover="#ffffff", bold=False):
        """Clickable text drawn straight on the photo."""
        item = self.create_text(x, y, text=text, fill=colour, anchor=anchor,
                                font=(FONT, size, "bold" if bold else "normal"))
        self.tag_bind(item, "<Enter>", lambda e: (self.itemconfigure(item, fill=hover), self.config(cursor="hand2")))
        self.tag_bind(item, "<Leave>", lambda e: (self.itemconfigure(item, fill=colour), self.config(cursor="")))
        self.tag_bind(item, "<Button-1>", lambda e: command())
        return item


class ScrollFrame(tk.Frame):
    """A vertically scrolling area; put content in .inner. The wheel scrolls it while hovered."""

    def __init__(self, parent, bg=BG, **kw):
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

    def _on_scroll(self, first, last):
        self.bar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.bar.pack_forget()
        elif not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y")

    def _wheel(self, event):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def to_top(self):
        self.canvas.yview_moveto(0)


def format_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def minutes(seconds):
    m = int(round((seconds or 0) / 60))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def pretty_track(track):
    """'Kyalami - Kyalami_2019' -> 'Kyalami · Kyalami 2019'."""
    parts = [p.strip().replace("_", " ") for p in (track or "Unknown track").split(" - ")]
    return " · ".join(p for p in parts if p)
