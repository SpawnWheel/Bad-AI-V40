"""Graphics Setup: place the overlay frames, pick logos/colours, link drivers to car images,
and preview everything on top of the race video before rendering.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageTk  # noqa: E402

from GraphicsOverlay import config as gcfg  # noqa: E402
from GraphicsOverlay import creator  # noqa: E402
from GraphicsOverlay import race_data  # noqa: E402
from GraphicsOverlay import renderer as rnd  # noqa: E402
from GraphicsOverlay.drawing import load_image  # noqa: E402

BG = "#0f1117"
CARD = "#1a1d27"
INPUT = "#252836"
FG = "#e2e4e9"
DIM = "#6b7280"
MUTED = "#9ca3af"
ACCENT = "#6366f1"
ACCENT_LIGHT = "#818cf8"
SUCCESS = "#22c55e"
WARN = "#f59e0b"
BORDER = "#2d3142"
FONT = "Segoe UI"
MONO = "Consolas"

PREVIEW_W, PREVIEW_H = 960, 540
SCALE = 1920 / PREVIEW_W

REGIONS = {
    'leaderboard': ('Leaderboard frame', '#22c55e'),
    'map': ('Track map frame', '#38bdf8'),
    'logo': ('Championship logo', '#f59e0b'),
    'popups': ('Pop-up graphics anchor', '#ec4899'),
    'ticker': ('Bottom ticker', '#a78bfa'),
    'radio': ('Team radio (middle right)', '#f43f5e'),
}

REGION_FIELDS = {
    'leaderboard': [('enabled', 'Show frame', bool), ('x', 'Left (x)', int), ('y', 'Top of board (y)', int),
                    ('w', 'Width', int), ('row_h', 'Row height', int), ('pad', 'Extra padding', int),
                    ('rows', 'Rows (0 = one per driver)', int), ('header', 'Header bar', bool),
                    ('footer', 'Flag/track footer', bool)],
    'map': [('enabled', 'Show frame', bool), ('x', 'Left (x)', int), ('y', 'Top (y)', int), ('w', 'Width', int),
            ('h', 'Height', int), ('header', 'Track name header', bool)],
    'logo': [('enabled', 'Show logo', bool), ('x', 'Right edge (x)', int), ('y', 'Top (y)', int),
             ('h', 'Height', int)],
    'popups': [('x', 'Left (x)', int), ('bottom', 'Bottom (y)', int)],
    'ticker': [('enabled', 'Show tickers', bool), ('y', 'Top (y)', int), ('h', 'Height', int),
               ('speed', 'Scroll speed (px/s)', int), ('max_duration', 'Max crawl seconds', int),
               ('extra_hold', 'Extra seconds held on screen', int)],
    'radio': [('right', 'Right edge (x)', int), ('cy', 'Vertical centre (y)', int), ('w', 'Width', int)],
}

DRIVER_COLS = [('log', 'Log name', 150), ('display', 'Display name', 140), ('short', 'Short', 90),
               ('number', '#', 40), ('country', 'Ctry', 50), ('team', 'Team / livery', 160),
               ('color', 'Colour', 70), ('car_image', 'Car image', 170)]


def styled_button(parent, text, cmd, primary=False):
    bg = ACCENT if primary else INPUT
    hover = ACCENT_LIGHT if primary else "#363a4d"
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg="#ffffff" if primary else FG, relief="flat",
                  font=(FONT, 9, "bold" if primary else "normal"), padx=10, pady=4, cursor="hand2",
                  activebackground=hover, activeforeground="#ffffff", bd=0, highlightthickness=0)
    b.bind("<Enter>", lambda e: b.config(bg=hover))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


class GraphicsSetup:
    def __init__(self, root, project_path):
        self.root = root
        self.project_path = project_path
        self.files = gcfg.project_files(project_path)
        self.cfg = gcfg.load_config(project_path)
        self.first_run = not os.path.exists(os.path.join(project_path, gcfg.CONFIG_NAME))
        self.plan = gcfg.load_plan(project_path)

        detected = race_data.detect_offset(self.files['raw_log'], self.files['video_timed'])
        self.detected_offset = detected
        if self.cfg['sync'].get('offset_sec') is None:
            self.cfg['sync']['offset_sec'] = detected if detected is not None else 0.0
        self.race = race_data.RaceData(self.files['raw_log'], self.cfg['sync']['offset_sec'],
                                       self.files['director_notes'])
        gcfg.ensure_drivers(self.cfg, self.race.drivers)
        if self.first_run:
            self._first_run_defaults()

        self.video = self.files['video_file']
        self.video_duration = self._probe_duration()
        self.tmp_png = os.path.join(tempfile.gettempdir(), "graphics_setup_frame.png")
        self.photo = None
        self.region = tk.StringVar(value='leaderboard')
        self.show_overlay = tk.BooleanVar(value=True)
        self.show_guides = tk.BooleanVar(value=True)
        self.drag_start = None
        self.drag_rect = None
        self.region_vars = {}
        self.preview_job = None
        self._read_saved_championship()

        root.title("Graphics Setup — BAD AI Commentary Suite")
        root.configure(bg=BG)
        root.geometry("1560x940")
        self._style()
        self._build()
        start = (self.race.green_flag_video or 0) + 4
        self.time_var.set(round(min(start, max(0, self.video_duration - 1)), 1))
        self.refresh_preview()
        self._poll_championship_dirty()

    # ------------------------------------------------------------------ setup
    def _first_run_defaults(self):
        logos = gcfg.list_images(gcfg.LOGO_DIR)
        if logos and not self.cfg['championship'].get('logo'):
            white = [f for f in logos if 'white' in f.lower()]
            self.cfg['championship']['logo'] = f"Graphics/Logos/{(white or logos)[0]}"
        self.cfg['layout']['leaderboard']['rows'] = 0

    def _probe_duration(self):
        if not self.video:
            return 3600.0
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  self.video], capture_output=True, text=True).stdout.strip()
            return float(out)
        except Exception:
            return 3600.0

    def _style(self):
        st = ttk.Style()
        st.theme_use('clam')
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=CARD, foreground=DIM, padding=[12, 6], font=(FONT, 9, "bold"))
        st.map("TNotebook.Tab", background=[('selected', ACCENT)], foreground=[('selected', '#ffffff')])
        st.configure("TFrame", background=CARD)
        st.configure("TCombobox", fieldbackground=INPUT, background=INPUT, foreground=FG)
        st.map("TCombobox", fieldbackground=[('readonly', INPUT)])
        st.configure("Treeview", background=INPUT, fieldbackground=INPUT, foreground=FG, rowheight=24,
                     font=(FONT, 9), borderwidth=0)
        st.configure("Treeview.Heading", background=CARD, foreground=MUTED, font=(FONT, 9, "bold"))
        st.map("Treeview", background=[('selected', ACCENT)])

    def _build(self):
        header = tk.Frame(self.root, bg=CARD, height=48)
        header.pack(fill='x')
        header.pack_propagate(False)
        tk.Label(header, text="GRAPHICS SETUP", bg=CARD, fg=ACCENT, font=(FONT, 15, "bold")).pack(side='left', padx=16)
        tk.Label(header, text=os.path.basename(self.project_path), bg=CARD, fg=DIM, font=(FONT, 10)).pack(side='left')
        styled_button(header, "Save & Close", self.save_and_close, primary=True).pack(side='right', padx=(6, 16), pady=8)
        styled_button(header, "Save", self.save).pack(side='right', pady=8)
        if not hasattr(self, 'save_default_var'):
            self.save_default_var = tk.BooleanVar(value=True)
        tk.Checkbutton(header, text="Also use as the default for future races", variable=self.save_default_var,
                       bg=CARD, fg=MUTED, selectcolor=INPUT, activebackground=CARD, activeforeground=FG,
                       font=(FONT, 9)).pack(side='right', padx=12)
        if self.first_run and os.path.exists(gcfg.DEFAULTS_PATH):
            tk.Label(header, text="Loaded your saved graphics defaults", bg=CARD, fg=SUCCESS,
                     font=(FONT, 9)).pack(side='left', padx=16)
        self._build_championship_bar()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill='both', expand=True, padx=12, pady=10)

        left = tk.Frame(body, bg=BG)
        left.pack(side='left', fill='y')
        self.canvas = tk.Canvas(left, width=PREVIEW_W, height=PREVIEW_H, bg="#000000", highlightthickness=1,
                                highlightbackground=BORDER, cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        ctl = tk.Frame(left, bg=BG)
        ctl.pack(fill='x', pady=(8, 0))
        self.time_var = tk.DoubleVar(value=0)
        scale = tk.Scale(ctl, from_=0, to=self.video_duration, orient='horizontal', resolution=0.5,
                         variable=self.time_var, showvalue=False, length=PREVIEW_W, bg=BG, fg=FG,
                         troughcolor=INPUT, highlightthickness=0, bd=0, sliderrelief='flat')
        scale.pack(fill='x')
        scale.bind("<ButtonRelease-1>", lambda e: self.refresh_preview())
        row = tk.Frame(left, bg=BG)
        row.pack(fill='x', pady=4)
        self.time_label = tk.Label(row, text="", bg=BG, fg=FG, font=(MONO, 11), width=26, anchor='w')
        self.time_label.pack(side='left')
        for txt, d in (("◀ 10s", -10), ("◀ 1s", -1), ("1s ▶", 1), ("10s ▶", 10)):
            styled_button(row, txt, lambda d=d: self.nudge(d)).pack(side='left', padx=2)
        tk.Checkbutton(row, text="Overlays", variable=self.show_overlay, command=self.refresh_preview, bg=BG, fg=FG,
                       selectcolor=INPUT, activebackground=BG, activeforeground=FG).pack(side='left', padx=(12, 0))
        tk.Checkbutton(row, text="Region guides", variable=self.show_guides, command=self.draw_guides, bg=BG, fg=FG,
                       selectcolor=INPUT, activebackground=BG, activeforeground=FG).pack(side='left')
        styled_button(row, "Render 30s test clip", self.render_test_clip, primary=True).pack(side='right')

        tips = ("Pick a region on the Layout tab, then drag a box over the video to place it. "
                "Move the slider to a moment where the in-game leaderboard and map are visible.")
        tk.Label(left, text=tips, bg=BG, fg=DIM, font=(FONT, 9), wraplength=PREVIEW_W, justify='left').pack(anchor='w')
        self.status = tk.Label(left, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor='w')
        self.status.pack(fill='x', pady=(4, 0))

        right = tk.Frame(body, bg=BG)
        right.pack(side='left', fill='both', expand=True, padx=(12, 0))
        nb = ttk.Notebook(right)
        nb.pack(fill='both', expand=True)
        self.tab_layout = self._tab(nb, "Layout")
        self.tab_brand = self._tab(nb, "Branding")
        self.tab_drivers = self._tab(nb, "Drivers")
        self.tab_graphics = self._tab(nb, "Graphics & Sync")
        self.tab_plan = self._tab(nb, "Planned Graphics")
        self._build_layout_tab()
        self._build_brand_tab()
        self._build_drivers_tab()
        self._build_graphics_tab()
        self._build_plan_tab()

    # ------------------------------------------------------------------ championships
    def _build_championship_bar(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill='x', pady=(1, 0))
        tk.Label(bar, text="WORKING IN", bg=BG, fg=MUTED, font=(FONT, 9, "bold")).pack(side='left',
                                                                                    padx=(16, 8), pady=8)
        self.champ_name_label = tk.Label(bar, text="", bg=BG, font=(FONT, 13, "bold"))
        self.champ_name_label.pack(side='left')
        self.champ_dirty_label = tk.Label(bar, text="", bg=BG, fg=WARN, font=(FONT, 9, "bold"))
        self.champ_dirty_label.pack(side='left', padx=12)

        styled_button(bar, "Delete", self.delete_championship).pack(side='right', padx=(4, 16))
        styled_button(bar, "Save as new championship…", self.save_championship_as).pack(side='right', padx=4)
        self.champ_save_btn = styled_button(bar, "Save championship", self.save_championship, primary=True)
        self.champ_save_btn.pack(side='right', padx=4)
        self.champ_switch_var = tk.StringVar()
        self.champ_box = ttk.Combobox(bar, textvariable=self.champ_switch_var, state='readonly', width=36)
        self.champ_box.pack(side='right', padx=(4, 12))
        self.champ_box.bind("<<ComboboxSelected>>", lambda e: self.load_championship(self.champ_switch_var.get()))
        tk.Label(bar, text="Switch to", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side='right')
        self.refresh_championship_bar()

    def _read_saved_championship(self):
        """Snapshot of the active championship as saved on disk, to spot unsaved changes."""
        name = self.cfg.get(gcfg.PRESET_KEY)
        champ = gcfg.load_championship(name) if name else None
        self.champ_saved = gcfg.championship_snapshot(champ) if champ else None

    def championship_dirty(self):
        if not self.cfg.get(gcfg.PRESET_KEY):
            return False
        return self.champ_saved is None or gcfg.championship_snapshot(self.cfg) != self.champ_saved

    def refresh_championship_bar(self):
        name = self.cfg.get(gcfg.PRESET_KEY)
        self.champ_name_label.config(text=name or "No championship (this race only)", fg=ACCENT_LIGHT if name else DIM)
        self.champ_box.config(values=[n for n in gcfg.list_championships() if n != name])
        self.champ_switch_var.set('')
        self.champ_save_btn.config(text=f"Save to \"{name}\"" if name else "Save championship")
        self.update_championship_dirty()
        self.root.title(f"Graphics Setup — {name or 'no championship'} — BAD AI Commentary Suite")

    def update_championship_dirty(self):
        if self.cfg.get(gcfg.PRESET_KEY) and self.champ_saved is None:
            text = "● Not saved yet"
        else:
            text = "● Unsaved changes" if self.championship_dirty() else ""
        self.champ_dirty_label.config(text=text)

    def _poll_championship_dirty(self):
        try:
            self.update_championship_dirty()
        except tk.TclError:
            return
        self.root.after(800, self._poll_championship_dirty)

    def ask_save_championship_changes(self, action):
        """Yes saves the active championship, No leaves it as it was; False means Cancel."""
        name = self.cfg.get(gcfg.PRESET_KEY)
        if not self.championship_dirty():
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved championship changes",
            f"You changed the branding, layout or drivers of\n\n    {name}\n\n"
            f"Save those changes to \"{name}\" before {action}?\n\n"
            "Yes = update that championship\nNo = leave the championship as it was", parent=self.root)
        if answer:
            self.save_championship()
        return answer is not None

    def load_championship(self, name):
        current = self.cfg.get(gcfg.PRESET_KEY, '')
        if not name or name == current:
            return
        if not self.ask_save_championship_changes(f"switching to \"{name}\""):
            self.champ_switch_var.set('')
            return
        champ = gcfg.load_championship(name)
        if champ is None:
            messagebox.showerror("Switch championship", f"Could not find \"{name}\".", parent=self.root)
            self.champ_switch_var.set('')
            return
        self.cfg = gcfg.apply_championship(self.cfg, champ)
        self.cfg[gcfg.PRESET_KEY] = name
        gcfg.ensure_drivers(self.cfg, self.race.drivers)
        self.champ_saved = gcfg.championship_snapshot(champ)
        self.rebuild()
        self.set_status(f"Switched to \"{name}\". Click Save to use it for this race.")

    def save_championship(self):
        name = self.cfg.get(gcfg.PRESET_KEY)
        if not name:
            return self.save_championship_as()
        gcfg.save_championship(name, self.cfg)
        self._read_saved_championship()
        self.refresh_championship_bar()
        self.set_status(f"Saved championship \"{name}\" ({len(self.cfg['drivers'])} drivers). "
                        "The previous version is in Graphics/Championships/Backups.")

    def save_championship_as(self):
        from tkinter import simpledialog
        c = self.cfg['championship']
        suggestion = ' '.join(x for x in (c.get('name'), c.get('season')) if x)
        name = simpledialog.askstring("Save as new championship",
                                      "Name for the new championship (branding, layout and drivers as they are now):",
                                      initialvalue=suggestion, parent=self.root)
        name = (name or '').strip()
        if not name:
            return
        existing = next((n for n in gcfg.list_championships() if n.lower() == name.lower()), None)
        if existing is None and gcfg.championship_exists(name):
            existing = name
        if existing and not messagebox.askyesno(
                "Championship already exists",
                f"There is already a championship called\n\n    {existing}\n\nReplace it with what you have now? "
                "(The old version is kept in Graphics/Championships/Backups.)", icon='warning', default='no',
                parent=self.root):
            return
        self.cfg[gcfg.PRESET_KEY] = name
        self.save_championship()

    def delete_championship(self):
        name = self.cfg.get(gcfg.PRESET_KEY)
        if not name:
            return
        if not messagebox.askyesno("Delete championship", f"Delete the saved championship \"{name}\"?\n\n"
                                   "This race keeps its current settings. A copy is kept in "
                                   "Graphics/Championships/Backups.", icon='warning', default='no',
                                   parent=self.root):
            return
        gcfg.delete_championship(name)
        self.cfg.pop(gcfg.PRESET_KEY, None)
        self.champ_saved = None
        self.refresh_championship_bar()
        self.set_status(f"Deleted championship \"{name}\".")

    def rebuild(self):
        """Redraws every tab from self.cfg (after switching championship)."""
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        t = float(self.time_var.get())
        for w in self.root.winfo_children():
            w.destroy()
        self.region_vars = {}
        self._build()
        self.time_var.set(t)
        self.refresh_preview()

    def _tab(self, nb, title):
        f = tk.Frame(nb, bg=CARD, padx=14, pady=12)
        nb.add(f, text=f"  {title}  ")
        return f

    def label(self, parent, text, **grid):
        lbl = tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=(FONT, 9), anchor='w')
        lbl.grid(**grid)
        return lbl

    def entry(self, parent, var, width=28):
        return tk.Entry(parent, textvariable=var, bg=INPUT, fg=FG, insertbackground=FG, relief='flat',
                        font=(MONO, 10), width=width, highlightthickness=0)

    # ------------------------------------------------------------------ layout tab
    def _build_layout_tab(self):
        f = self.tab_layout
        tk.Label(f, text="Region to place", bg=CARD, fg=FG, font=(FONT, 11, "bold")).pack(anchor='w')
        radios = tk.Frame(f, bg=CARD)
        radios.pack(anchor='w', pady=(4, 10))
        for key, (name, color) in REGIONS.items():
            tk.Radiobutton(radios, text=name, value=key, variable=self.region, command=self.show_region_fields,
                           bg=CARD, fg=color, selectcolor=INPUT, activebackground=CARD, activeforeground=color,
                           font=(FONT, 10)).pack(anchor='w')
        self.region_frame = tk.Frame(f, bg=CARD)
        self.region_frame.pack(fill='x')
        tk.Label(f, text=("The leaderboard frame wraps the in-game timing tower. With rows set to 0 its height "
                          f"follows the number of drivers in the race ({len(self.race.drivers)} here): "
                          "rows × row height + padding."), bg=CARD, fg=DIM, font=(FONT, 9), wraplength=480,
                 justify='left').pack(anchor='w', pady=(12, 0))
        self.show_region_fields()

    def show_region_fields(self):
        for w in self.region_frame.winfo_children():
            w.destroy()
        key = self.region.get()
        section = self.cfg['layout'][key]
        self.region_vars = {}
        for i, (field, label, typ) in enumerate(REGION_FIELDS[key]):
            if typ is bool:
                var = tk.BooleanVar(value=bool(section.get(field, True)))
                tk.Checkbutton(self.region_frame, text=label, variable=var, bg=CARD, fg=FG, selectcolor=INPUT,
                               activebackground=CARD, activeforeground=FG,
                               command=lambda f=field, v=var: self.set_region_value(f, v.get())
                               ).grid(row=i, column=0, columnspan=2, sticky='w')
            else:
                var = tk.StringVar(value=str(section.get(field) or 0))
                self.label(self.region_frame, label, row=i, column=0, sticky='w', pady=2)
                sp = tk.Spinbox(self.region_frame, from_=0, to=4000, textvariable=var, width=8, bg=INPUT, fg=FG,
                                buttonbackground=INPUT, relief='flat', font=(MONO, 10),
                                command=lambda f=field, v=var: self.set_region_value(f, v.get()))
                sp.grid(row=i, column=1, sticky='w', padx=8, pady=2)
                sp.bind("<KeyRelease>", lambda e, f=field, v=var: self.set_region_value(f, v.get()))
            self.region_vars[field] = var
        self.draw_guides()

    def set_region_value(self, field, value):
        key = self.region.get()
        if isinstance(value, bool):
            self.cfg['layout'][key][field] = value
        else:
            try:
                self.cfg['layout'][key][field] = int(float(value))
            except ValueError:
                return
        self.schedule_preview()

    def sync_region_vars(self):
        section = self.cfg['layout'][self.region.get()]
        for field, var in self.region_vars.items():
            if isinstance(var, tk.BooleanVar):
                var.set(bool(section.get(field)))
            else:
                var.set(str(section.get(field) or 0))

    # ------------------------------------------------------------------ branding tab
    def _build_brand_tab(self):
        f = self.tab_brand
        champ = self.cfg['championship']
        self.brand_vars = {}
        fields = [('name', 'Championship name'), ('short_name', 'Short name (e.g. RCV)'),
                  ('season', 'Season (e.g. Season 15)'), ('round', 'Round (e.g. Round 1)'),
                  ('event_name', 'Event title (blank = track name)')]
        for i, (k, label) in enumerate(fields):
            self.label(f, label, row=i, column=0, sticky='w', pady=3)
            var = tk.StringVar(value=champ.get(k, ''))
            var.trace_add('write', lambda *a, k=k, v=var: (champ.__setitem__(k, v.get()), self.schedule_preview()))
            self.entry(f, var, 34).grid(row=i, column=1, columnspan=2, sticky='w', padx=8, pady=3, ipady=3)
            self.brand_vars[k] = var

        r = len(fields)
        self.label(f, "Championship logo", row=r, column=0, sticky='w', pady=(12, 3))
        self.logo_var = tk.StringVar(value=champ.get('logo', ''))
        cb = ttk.Combobox(f, textvariable=self.logo_var, width=32,
                          values=[f"Graphics/Logos/{x}" for x in gcfg.list_images(gcfg.LOGO_DIR)])
        cb.grid(row=r, column=1, sticky='w', padx=8, pady=(12, 3))
        cb.bind("<<ComboboxSelected>>", lambda e: self.set_logo(self.logo_var.get()))
        styled_button(f, "Browse…", lambda: self.set_logo(self.browse_image(gcfg.LOGO_DIR))).grid(
            row=r, column=2, sticky='w', pady=(12, 3))
        self.tint_var = tk.BooleanVar(value=bool(champ.get('logo_tint_white')))
        tk.Checkbutton(f, text="Recolour logo white (for dark logos)", variable=self.tint_var, bg=CARD, fg=FG,
                       selectcolor=INPUT, activebackground=CARD, activeforeground=FG,
                       command=lambda: (champ.__setitem__('logo_tint_white', self.tint_var.get()),
                                        self.schedule_preview())).grid(row=r + 1, column=1, sticky='w', padx=8)

        r += 2
        tk.Label(f, text="Colours", bg=CARD, fg=FG, font=(FONT, 11, "bold")).grid(row=r, column=0, sticky='w',
                                                                               pady=(16, 4))
        self.color_buttons = {}
        for i, (k, label) in enumerate((('accent', 'Accent (tabs, rails)'), ('accent2', 'Highlight (numbers)'),
                                        ('panel', 'Panel background'))):
            self.label(f, label, row=r + 1 + i, column=0, sticky='w', pady=3)
            b = tk.Button(f, text=self.cfg['theme'][k], bg=self.cfg['theme'][k], fg='#ffffff', relief='flat', width=12,
                          command=lambda k=k: self.pick_theme_color(k))
            b.grid(row=r + 1 + i, column=1, sticky='w', padx=8, pady=3)
            self.color_buttons[k] = b
        r += 4
        self.label(f, "Panel opacity", row=r, column=0, sticky='w')
        alpha = tk.DoubleVar(value=self.cfg['theme'].get('panel_alpha', 0.9))
        tk.Scale(f, from_=0.5, to=1.0, resolution=0.02, orient='horizontal', variable=alpha, bg=CARD, fg=FG,
                 highlightthickness=0, troughcolor=INPUT, length=200,
                 command=lambda v: (self.cfg['theme'].__setitem__('panel_alpha', float(v)),
                                    self.schedule_preview())).grid(row=r, column=1, sticky='w', padx=8)

        r += 1
        tk.Label(f, text="Extra logos (sponsors, car maker…)", bg=CARD, fg=FG, font=(FONT, 11, "bold")).grid(
            row=r, column=0, columnspan=3, sticky='w', pady=(16, 4))
        self.extra_list = tk.Listbox(f, height=4, bg=INPUT, fg=FG, selectbackground=ACCENT, relief='flat',
                                     highlightthickness=0, width=46)
        self.extra_list.grid(row=r + 1, column=0, columnspan=2, sticky='w')
        self.extra_list.bind("<<ListboxSelect>>", lambda e: self.show_extra_fields())
        btns = tk.Frame(f, bg=CARD)
        btns.grid(row=r + 1, column=2, sticky='nw')
        styled_button(btns, "Add…", self.add_extra_logo).pack(fill='x', pady=1)
        styled_button(btns, "Remove", self.remove_extra_logo).pack(fill='x', pady=1)
        self.extra_frame = tk.Frame(f, bg=CARD)
        self.extra_frame.grid(row=r + 2, column=0, columnspan=3, sticky='w', pady=4)
        self.refresh_extra_list()

    def browse_image(self, initial):
        path = filedialog.askopenfilename(initialdir=initial, filetypes=[
            ("Images", "*.png *.dds *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        return gcfg.relative_path(path) if path else None

    def set_logo(self, path):
        if path is None:
            return
        self.logo_var.set(path)
        self.cfg['championship']['logo'] = path
        self.schedule_preview()

    def pick_theme_color(self, key):
        c = colorchooser.askcolor(color=self.cfg['theme'][key], parent=self.root)[1]
        if c:
            self.cfg['theme'][key] = c
            self.color_buttons[key].config(text=c, bg=c)
            self.schedule_preview()

    def refresh_extra_list(self):
        self.extra_list.delete(0, 'end')
        for e in self.cfg['layout']['extra_logos']:
            self.extra_list.insert('end', os.path.basename(e.get('path', '?')))

    def add_extra_logo(self):
        path = self.browse_image(gcfg.LOGO_DIR)
        if not path:
            return
        self.cfg['layout']['extra_logos'].append({'path': path, 'x': 1903, 'y': 950, 'h': 40, 'anchor': 'right',
                                                  'opacity': 0.85, 'tint_white': False})
        self.refresh_extra_list()
        self.extra_list.selection_set('end')
        self.show_extra_fields()
        self.schedule_preview()

    def remove_extra_logo(self):
        sel = self.extra_list.curselection()
        if sel:
            del self.cfg['layout']['extra_logos'][sel[0]]
            self.refresh_extra_list()
            self.show_extra_fields()
            self.schedule_preview()

    def show_extra_fields(self):
        for w in self.extra_frame.winfo_children():
            w.destroy()
        sel = self.extra_list.curselection()
        if not sel:
            return
        item = self.cfg['layout']['extra_logos'][sel[0]]
        for i, (k, label) in enumerate((('x', 'x'), ('y', 'y'), ('h', 'height'), ('opacity', 'opacity'))):
            tk.Label(self.extra_frame, text=label, bg=CARD, fg=MUTED).grid(row=0, column=i * 2, padx=(0, 4))
            var = tk.StringVar(value=str(item.get(k, '')))
            e = self.entry(self.extra_frame, var, 6)
            e.grid(row=0, column=i * 2 + 1, padx=(0, 10))

            def update(*a, k=k, v=var):
                try:
                    item[k] = float(v.get()) if k == 'opacity' else int(float(v.get()))
                    self.schedule_preview()
                except ValueError:
                    pass
            var.trace_add('write', update)
        anchor = tk.BooleanVar(value=item.get('anchor') == 'right')
        tint = tk.BooleanVar(value=bool(item.get('tint_white')))
        tk.Checkbutton(self.extra_frame, text="x is right edge", variable=anchor, bg=CARD, fg=FG, selectcolor=INPUT,
                       activebackground=CARD, command=lambda: (item.__setitem__('anchor', 'right' if anchor.get()
                                                                                 else 'left'), self.schedule_preview())
                       ).grid(row=1, column=0, columnspan=4, sticky='w')
        tk.Checkbutton(self.extra_frame, text="Recolour white", variable=tint, bg=CARD, fg=FG, selectcolor=INPUT,
                       activebackground=CARD, command=lambda: (item.__setitem__('tint_white', tint.get()),
                                                               self.schedule_preview())
                       ).grid(row=1, column=4, columnspan=4, sticky='w')
        tk.Label(self.extra_frame, text="Coordinates are in 1920×1080 space; the preview updates as you type.",
                 bg=CARD, fg=DIM, font=(FONT, 8)).grid(
            row=2, column=0, columnspan=8, sticky='w')

    # ------------------------------------------------------------------ drivers tab
    def _build_drivers_tab(self):
        f = self.tab_drivers
        tk.Label(f, text=("Link each name from the race log to how it should appear on screen and to its car preview "
                          "image (Graphics/CarPreviews). Short is used on the ticker and battle cards."),
                 bg=CARD, fg=DIM, font=(FONT, 9), wraplength=560, justify='left').pack(anchor='w')
        tree_frame = tk.Frame(f, bg=CARD)
        tree_frame.pack(fill='both', expand=True, pady=6)
        self.tree = ttk.Treeview(tree_frame, columns=[c[0] for c in DRIVER_COLS], show='headings', height=10)
        for key, title, width in DRIVER_COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=False)
        xs = ttk.Scrollbar(tree_frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(xscrollcommand=xs.set)
        self.tree.pack(fill='both', expand=True)
        xs.pack(fill='x')
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_driver_form())

        self.driver_form = tk.Frame(f, bg=CARD)
        self.driver_form.pack(fill='x')
        btns = tk.Frame(f, bg=CARD)
        btns.pack(fill='x', pady=(8, 0))
        styled_button(btns, "Re-match car images", self.rematch_images).pack(side='left')
        styled_button(btns, "AI fill from project notes", self.ai_fill_drivers, primary=True).pack(side='left', padx=6)
        styled_button(btns, "Show on preview", self.preview_selected_driver).pack(side='left')
        self.refresh_driver_tree()

    def refresh_driver_tree(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for name in self.race.drivers:
            d = self.cfg['drivers'][name]
            self.tree.insert('', 'end', iid=name, values=[name] + [
                os.path.basename(d.get(k, '')) if k == 'car_image' else d.get(k, '') for k, _, _ in DRIVER_COLS[1:]])
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])

    def show_driver_form(self):
        for w in self.driver_form.winfo_children():
            w.destroy()
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        d = self.cfg['drivers'][name]
        tk.Label(self.driver_form, text=name, bg=CARD, fg=FG, font=(FONT, 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky='w', pady=(4, 6))
        fields = [('display', 'Display name', 22), ('short', 'Short (caps)', 12), ('number', 'Car number', 6),
                  ('country', 'Country code', 6), ('team', 'Team / livery', 26)]
        for i, (k, label, width) in enumerate(fields):
            r, c = 1 + i // 2, (i % 2) * 2
            self.label(self.driver_form, label, row=r, column=c, sticky='w', pady=2)
            var = tk.StringVar(value=d.get(k, ''))

            def update(*a, k=k, v=var):
                d[k] = v.get().upper() if k in ('short', 'country') else v.get()
                self.update_tree_row(name)
                self.schedule_preview()
            var.trace_add('write', update)
            self.entry(self.driver_form, var, width).grid(row=r, column=c + 1, sticky='w', padx=6, pady=2, ipady=2)

        r = 4
        self.label(self.driver_form, "Car image", row=r, column=0, sticky='w', pady=2)
        images = ['(none)'] + [f"Graphics/CarPreviews/{x}" for x in gcfg.list_images(gcfg.CAR_PREVIEW_DIR)]
        img_var = tk.StringVar(value=d.get('car_image') or '(none)')
        cb = ttk.Combobox(self.driver_form, textvariable=img_var, values=images, width=34, state='readonly')
        cb.grid(row=r, column=1, columnspan=2, sticky='w', padx=6)

        def set_image(path):
            if path is None:
                return
            d['car_image'] = '' if path == '(none)' else path
            img_var.set(path)
            self.update_tree_row(name)
            self.show_car_thumb(d['car_image'])
            self.schedule_preview()
        cb.bind("<<ComboboxSelected>>", lambda e: set_image(img_var.get()))
        styled_button(self.driver_form, "Browse…", lambda: set_image(self.browse_image(gcfg.CAR_PREVIEW_DIR))).grid(
            row=r, column=3, sticky='w')

        self.label(self.driver_form, "Colour", row=r + 1, column=0, sticky='w', pady=2)
        color_btn = tk.Button(self.driver_form, text=d.get('color') or 'theme accent', relief='flat', width=14,
                              bg=d.get('color') or INPUT, fg='#ffffff')
        color_btn.grid(row=r + 1, column=1, sticky='w', padx=6)

        def pick():
            c = colorchooser.askcolor(color=d.get('color') or '#E10600', parent=self.root)[1]
            if c:
                d['color'] = c
                color_btn.config(text=c, bg=c)
                self.update_tree_row(name)
                self.schedule_preview()
        color_btn.config(command=pick)
        self.thumb = tk.Label(self.driver_form, bg=CARD)
        self.thumb.grid(row=r + 1, column=2, columnspan=2, rowspan=2, sticky='w', pady=4)
        self.show_car_thumb(d.get('car_image'))

    def show_car_thumb(self, path):
        im = load_image(gcfg.resolve_path(path)) if path else None
        if im is None:
            self.thumb.config(image='', text='no image', fg=DIM)
            return
        im = im.copy()
        im.thumbnail((160, 80))
        self._thumb_photo = ImageTk.PhotoImage(im)
        self.thumb.config(image=self._thumb_photo, text='')

    def update_tree_row(self, name):
        d = self.cfg['drivers'][name]
        self.tree.item(name, values=[name] + [
            os.path.basename(d.get(k, '')) if k == 'car_image' else d.get(k, '') for k, _, _ in DRIVER_COLS[1:]])

    def rematch_images(self):
        files = gcfg.list_images(gcfg.CAR_PREVIEW_DIR)
        n = 0
        for name in self.race.drivers:
            m = gcfg.match_car_image(name, files)
            if m and not self.cfg['drivers'][name].get('car_image'):
                self.cfg['drivers'][name]['car_image'] = f"Graphics/CarPreviews/{m}"
                n += 1
        self.refresh_driver_tree()
        self.set_status(f"Matched {n} more car images.")

    def ai_fill_drivers(self):
        api_key = os.environ.get("GEMINI_API_KEY") or self._launcher_setting('gemini_api_key')
        if not api_key:
            messagebox.showwarning("No API key", "Set the Gemini API key in the launcher Settings first.")
            return
        notes = ''
        if os.path.exists(self.files['project_notes']):
            with open(self.files['project_notes'], 'r', encoding='utf-8') as fh:
                notes = fh.read()
        model = os.environ.get("GEMINI_MODEL_NAME") or self._launcher_setting('gemini_model') or 'gemini-3.8-flash'
        self.set_status("Asking the AI to read the project notes…")

        def work():
            try:
                ask = creator.make_gemini_asker(api_key, model, 'LOW', 8192)
                info = creator.ai_driver_info(self.race.drivers, notes, ask)
                n = creator.merge_driver_info(self.cfg, info)
                self.root.after(0, lambda: (self.refresh_driver_tree(), self.schedule_preview(),
                                            self.set_status(f"AI filled {n} driver fields. Check them, then Save.")))
            except Exception as e:
                self.root.after(0, lambda: self.set_status(f"AI fill failed: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _launcher_setting(self, key):
        path = os.path.join(gcfg.APP_ROOT, "launcher_config.json")
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                return json.load(fh).get('global_settings', {}).get(key)
        except Exception:
            return None

    def preview_selected_driver(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.preview_extra = {'graphics': [{'id': 'setup_demo', 'type': 'driver_card', 'driver': sel[0],
                                            'time': self.time_var.get() - 1, 'duration': 30,
                                            'title': 'Driver', 'text': self.cfg['drivers'][sel[0]].get('team', ''),
                                            'data': {'pos': self.race.position_at(sel[0], self.time_var.get())}}]}
        self.refresh_preview()
        self.preview_extra = None

    # ------------------------------------------------------------------ graphics tab
    def _build_graphics_tab(self):
        f = self.tab_graphics
        self.enabled_var = tk.BooleanVar(value=bool(self.cfg.get('enabled', True)))
        tk.Checkbutton(f, text="Add graphics to the final video (Combine Video and Commentary)",
                       variable=self.enabled_var, bg=CARD, fg=SUCCESS, selectcolor=INPUT, activebackground=CARD,
                       font=(FONT, 10, "bold"),
                       command=lambda: self.cfg.__setitem__('enabled', self.enabled_var.get())).grid(
            row=0, column=0, columnspan=3, sticky='w')

        tk.Label(f, text="Sync", bg=CARD, fg=FG, font=(FONT, 11, "bold")).grid(row=1, column=0, sticky='w',
                                                                            pady=(14, 2))
        det = f"{self.detected_offset:+.1f}s" if self.detected_offset is not None else "not found"
        tk.Label(f, text=(f"Video time = race log time + offset. Detected from the reviewed events: {det}. "
                          "Adjust if graphics appear early/late."), bg=CARD, fg=DIM, font=(FONT, 9),
                 wraplength=520, justify='left').grid(row=2, column=0, columnspan=3, sticky='w')
        self.auto_sync = tk.BooleanVar(value=bool(self.cfg['sync'].get('auto', True)))
        tk.Checkbutton(f, text="Auto-detect", variable=self.auto_sync, bg=CARD, fg=FG, selectcolor=INPUT,
                       activebackground=CARD, command=self.on_sync_change).grid(row=3, column=0, sticky='w')
        self.offset_var = tk.StringVar(value=f"{self.cfg['sync']['offset_sec']:.1f}")
        tk.Spinbox(f, from_=-36000, to=36000, increment=0.5, textvariable=self.offset_var, width=10, bg=INPUT,
                   fg=FG, relief='flat', font=(MONO, 10), buttonbackground=INPUT,
                   command=self.on_sync_change).grid(row=3, column=1, sticky='w')
        self.offset_var.trace_add('write', lambda *a: self.on_sync_change())

        tk.Label(f, text="Pop-ups", bg=CARD, fg=FG, font=(FONT, 11, "bold")).grid(row=4, column=0, sticky='w',
                                                                               pady=(14, 2))
        self.label(f, "Density", row=5, column=0, sticky='w')
        dens = tk.StringVar(value=self.cfg.get('popup_density', 'medium'))
        cb = ttk.Combobox(f, textvariable=dens, values=list(gcfg.DENSITY_GAP), state='readonly', width=10)
        cb.grid(row=5, column=1, sticky='w')
        cb.bind("<<ComboboxSelected>>", lambda e: self.cfg.__setitem__('popup_density', dens.get()))
        self.label(f, "Default seconds on screen", row=6, column=0, sticky='w')
        dur = tk.StringVar(value=str(self.cfg.get('popup_duration', 7)))
        tk.Spinbox(f, from_=4, to=12, increment=0.5, textvariable=dur, width=6, bg=INPUT, fg=FG, relief='flat',
                   buttonbackground=INPUT,
                   command=lambda: self.cfg.__setitem__('popup_duration', float(dur.get()))).grid(
            row=6, column=1, sticky='w')

        tk.Label(f, text="Automatic graphics", bg=CARD, fg=FG, font=(FONT, 11, "bold")).grid(
            row=7, column=0, sticky='w', pady=(14, 2))
        r = 8
        for k, label in gcfg.AUTO_TYPES.items():
            var = tk.BooleanVar(value=bool(self.cfg['auto_graphics'].get(k, True)))
            tk.Checkbutton(f, text=label, variable=var, bg=CARD, fg=FG, selectcolor=INPUT, activebackground=CARD,
                           command=lambda k=k, v=var: (self.cfg['auto_graphics'].__setitem__(k, v.get()),
                                                       self.schedule_preview())).grid(
                row=r, column=0, columnspan=3, sticky='w')
            r += 1
        tk.Label(f, text="Graphic Creator (AI) may use", bg=CARD, fg=FG, font=(FONT, 11, "bold")).grid(
            row=r, column=0, sticky='w', pady=(14, 2))
        r += 1
        for k, label in gcfg.POPUP_TYPES.items():
            var = tk.BooleanVar(value=bool(self.cfg['popup_types'].get(k, True)))
            tk.Checkbutton(f, text=label, variable=var, bg=CARD, fg=FG, selectcolor=INPUT, activebackground=CARD,
                           command=lambda k=k, v=var: self.cfg['popup_types'].__setitem__(k, v.get())).grid(
                row=r, column=0, columnspan=3, sticky='w')
            r += 1

    def on_sync_change(self):
        self.cfg['sync']['auto'] = self.auto_sync.get()
        if self.auto_sync.get() and self.detected_offset is not None:
            if self.offset_var.get() != f"{self.detected_offset:.1f}":
                self.offset_var.set(f"{self.detected_offset:.1f}")
        try:
            self.cfg['sync']['offset_sec'] = float(self.offset_var.get())
        except ValueError:
            return
        self.race.offset = self.cfg['sync']['offset_sec']
        self.schedule_preview()

    # ------------------------------------------------------------------ plan tab
    def _build_plan_tab(self):
        f = self.tab_plan
        tk.Label(f, text=("Graphics from the Graphic Creator step. Double-click to jump the preview to one; "
                          "untick to leave it out of the render."), bg=CARD, fg=DIM, font=(FONT, 9),
                 wraplength=560, justify='left').pack(anchor='w')
        cols = [('on', 'On', 34), ('at', 'Time', 70), ('type', 'Type', 100), ('driver', 'Driver', 120),
                ('title', 'Title', 260)]
        self.plan_tree = ttk.Treeview(f, columns=[c[0] for c in cols], show='headings', height=22)
        for key, title, width in cols:
            self.plan_tree.heading(key, text=title)
            self.plan_tree.column(key, width=width, stretch=key == 'title')
        self.plan_tree.pack(fill='both', expand=True, pady=6)
        self.plan_tree.bind("<Double-1>", lambda e: self.jump_to_plan())
        btns = tk.Frame(f, bg=CARD)
        btns.pack(fill='x')
        styled_button(btns, "Toggle on/off", self.toggle_plan_item).pack(side='left')
        styled_button(btns, "Reload plan", self.reload_plan).pack(side='left', padx=6)
        styled_button(btns, "Open plan file", self.open_plan_file).pack(side='left')
        self.refresh_plan_tree()

    def refresh_plan_tree(self):
        self.plan_tree.delete(*self.plan_tree.get_children())
        for i, g in enumerate(self.plan.get('graphics', [])):
            self.plan_tree.insert('', 'end', iid=str(i), values=(
                '' if g.get('disabled') else '✓', g.get('at') or race_data.fmt_clock(g.get('time', 0)),
                g.get('type'), g.get('driver', ''), g.get('title', '')))

    def jump_to_plan(self):
        sel = self.plan_tree.selection()
        if sel:
            g = self.plan['graphics'][int(sel[0])]
            self.time_var.set(float(g['time']) + 1.5)
            self.refresh_preview()

    def toggle_plan_item(self):
        for iid in self.plan_tree.selection():
            g = self.plan['graphics'][int(iid)]
            g['disabled'] = not g.get('disabled')
        self.save_plan()
        self.refresh_plan_tree()
        self.schedule_preview()

    def reload_plan(self):
        self.plan = gcfg.load_plan(self.project_path)
        self.refresh_plan_tree()
        self.schedule_preview()

    def open_plan_file(self):
        path = os.path.join(self.project_path, gcfg.PLAN_NAME)
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showinfo("No plan yet", "Run the Graphic Creator step to make one.")

    def save_plan(self):
        if self.plan.get('graphics'):
            with open(os.path.join(self.project_path, gcfg.PLAN_NAME), 'w', encoding='utf-8') as fh:
                json.dump(self.plan, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ preview
    def nudge(self, d):
        self.time_var.set(max(0, min(self.video_duration, self.time_var.get() + d)))
        self.refresh_preview()

    def schedule_preview(self):
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
        self.preview_job = self.root.after(350, self.refresh_preview)

    preview_extra = None

    def refresh_preview(self):
        self.preview_job = None
        t = float(self.time_var.get())
        gf = self.race.green_flag_video
        rt = f"  race {race_data.fmt_clock(t - gf)}" if gf is not None and t >= gf else ''
        self.time_label.config(text=f"video {race_data.fmt_clock(t)}{rt}")
        try:
            if self.video and rnd.extract_frame(self.video, t, self.tmp_png):
                frame = Image.open(self.tmp_png).convert('RGBA').resize((1920, 1080))
            else:
                frame = Image.new('RGBA', (1920, 1080), (40, 44, 52, 255))
            if self.show_overlay.get():
                plan = self.preview_extra or self.plan
                r = rnd.OverlayRenderer(self.project_path, copy.deepcopy(self.cfg), plan, race=self.race)
                frame.alpha_composite(r.render_frame(t))
                active = [it['type'] for it in r._active(t)]
                self.set_status(f"On screen: {', '.join(active) if active else 'frames only'}")
            img = frame.convert('RGB').resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
            self.photo = ImageTk.PhotoImage(img)
            self.canvas.delete('all')
            self.canvas.create_image(0, 0, image=self.photo, anchor='nw')
            self.draw_guides()
        except Exception as e:
            self.set_status(f"Preview failed: {e}")

    def region_rect(self, key):
        lay = self.cfg['layout']
        if key == 'leaderboard':
            lb = lay['leaderboard']
            rows = lb.get('rows') or len(self.race.drivers)
            return lb['x'], lb['y'], lb['x'] + lb['w'], lb['y'] + rows * lb['row_h'] + lb['pad']
        if key == 'map':
            m = lay['map']
            return m['x'], m['y'], m['x'] + m['w'], m['y'] + m['h']
        if key == 'logo':
            lg = lay['logo']
            return lg['x'] - lg['h'] * 2.6, lg['y'], lg['x'], lg['y'] + lg['h']
        if key == 'popups':
            p = lay['popups']
            return p['x'], p['bottom'] - 200, p['x'] + p.get('max_w', 780), p['bottom']
        if key == 'ticker':
            tk_ = lay['ticker']
            return 0, tk_['y'], 1920, tk_['y'] + tk_['h']
        if key == 'radio':
            r = lay['radio']
            return r['right'] - r['w'], r['cy'] - 100, r['right'], r['cy'] + 100

    def draw_guides(self):
        self.canvas.delete('guide')
        if not self.show_guides.get():
            return
        for key, (name, color) in REGIONS.items():
            x0, y0, x1, y1 = [v / SCALE for v in self.region_rect(key)]
            sel = key == self.region.get()
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=2 if sel else 1,
                                         dash=() if sel else (4, 3), tags='guide')
            if sel:
                below = y1 + 16 < PREVIEW_H
                self.canvas.create_text(x0 + 4, y1 + 2 if below else y0 - 2, text=name, fill=color,
                                        anchor='nw' if below else 'sw', font=(FONT, 9, 'bold'), tags='guide')

    def on_press(self, e):
        self.drag_start = (e.x, e.y)
        color = REGIONS[self.region.get()][1]
        self.drag_rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=color, width=2, tags='drag')

    def on_drag(self, e):
        if self.drag_rect:
            self.canvas.coords(self.drag_rect, *self.drag_start, e.x, e.y)

    def on_release(self, e):
        if not self.drag_start:
            return
        x0, y0 = self.drag_start
        self.canvas.delete('drag')
        self.drag_start = self.drag_rect = None
        if abs(e.x - x0) < 4 and abs(e.y - y0) < 4:
            return
        bx0, bx1 = sorted((x0 * SCALE, e.x * SCALE))
        by0, by1 = sorted((y0 * SCALE, e.y * SCALE))
        bx0, by0, bx1, by1 = int(bx0), int(by0), int(bx1), int(by1)
        key = self.region.get()
        lay = self.cfg['layout']
        if key == 'leaderboard':
            lb = lay['leaderboard']
            rows = lb.get('rows') or len(self.race.drivers)
            lb.update(x=bx0, y=by0, w=bx1 - bx0)
            lb['row_h'] = max(10, int(round((by1 - by0 - lb['pad']) / max(1, rows))))
        elif key == 'map':
            lay['map'].update(x=bx0, y=by0, w=bx1 - bx0, h=by1 - by0)
        elif key == 'logo':
            lay['logo'].update(x=bx1, y=by0, h=by1 - by0)
        elif key == 'popups':
            lay['popups'].update(x=bx0, bottom=by1)
        elif key == 'ticker':
            lay['ticker'].update(y=by0, h=max(30, by1 - by0))
        elif key == 'radio':
            lay['radio'].update(right=bx1, cy=(by0 + by1) // 2, w=max(320, bx1 - bx0))
        self.sync_region_vars()
        self.refresh_preview()

    def set_status(self, msg):
        self.status.config(text=msg)

    # ------------------------------------------------------------------ test clip / save
    def render_test_clip(self):
        audio = self.files['final_audio']
        if not self.video or not audio:
            messagebox.showwarning("Missing files", "The test clip needs the video file and the combined "
                                   "commentary audio (run Combine Audio first).")
            return
        self.save(ask_championship=False)
        start = max(0.0, float(self.time_var.get()) - 2)
        script = os.path.join(gcfg.APP_ROOT, "VideoCombiner", "combine_video.py")
        env = os.environ.copy()
        env["R3E_PROJECT_PATH"] = self.project_path
        out = os.path.join(self.project_path, "graphics_preview.mp4")
        if os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass
        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        proc = subprocess.Popen([sys.executable, script, self.video, audio, "--preview", f"{start:.2f}", "30"],
                                cwd=os.path.dirname(script), env=env, creationflags=flags)
        self.set_status("Rendering a 30 second test clip…")

        def poll():
            if proc.poll() is None:
                self.root.after(1000, poll)
            elif os.path.exists(out):
                self.set_status("Test clip ready.")
                os.startfile(out)
            else:
                self.set_status("Test clip failed - see the console window.")
        self.root.after(1000, poll)

    def save(self, ask_championship=True):
        """Saves this race. The championship is only updated after asking (or with its own button)."""
        if ask_championship and not self.ask_save_championship_changes("saving this race"):
            return None
        self.cfg['sync']['auto'] = self.auto_sync.get()
        path = gcfg.save_config(self.project_path, self.cfg)
        msg = f"Saved {os.path.basename(path)}"
        if self.save_default_var.get():
            gcfg.save_defaults(self.cfg)
            msg += " and the defaults for future races"
        self.set_status(msg)
        return path

    def save_and_close(self):
        if self.save():
            self.root.destroy()


def main():
    project_path = os.environ.get("R3E_PROJECT_PATH")
    if not project_path:
        project_path = filedialog.askdirectory(title="Select the project folder")
    if not project_path or not os.path.isdir(project_path):
        print("No project folder selected.")
        return
    files = gcfg.project_files(project_path)
    if not files['raw_log']:
        print("Error: this project has no race log yet (raw_log). Log a race first.")
        return
    root = tk.Tk()
    GraphicsSetup(root, project_path)
    root.mainloop()


if __name__ == "__main__":
    main()
