"""My races: every recorded race as a card, with how far its replay has got."""
import datetime
import os
import shutil
import threading
import tkinter as tk
from tkinter import messagebox

import racefiles
from . import importer, pipeline
from . import theme as t


def started(meta, name):
    try:
        return datetime.datetime.fromisoformat(meta.get("started_at"))
    except (TypeError, ValueError):
        return None


class LibraryScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app
        self.filter = "All"
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.render_list())
        self.races = []
        self._sig = None

        self.banner = t.PhotoCanvas(self, focus=0.3, fade=0.55, height=150, on_layout=self._layout_banner)
        self.banner.pack(fill="x")
        b = self.banner
        self.b_title = b.create_text(36, 62, text="My races", fill=t.TEXT, font=(t.FONT, 26, "bold"), anchor="w")
        self.b_sub = b.create_text(36, 100, text="Every race you've recorded. Open one to turn it into a replay - "
                                                 "the app walks you through it.", fill=t.MUTED, font=(t.FONT, 10), anchor="w")
        self.folder_btn = t.button(b, "Open races folder", self.open_folder)
        self.import_btn = t.button(b, "", self.import_old, kind="primary")
        self.folder_win = b.create_window(0, 62, window=self.folder_btn, anchor="e")
        self.import_win = b.create_window(0, 62, window=self.import_btn, anchor="e", state="hidden")

        bar = tk.Frame(self, bg=t.BG)
        bar.pack(fill="x", padx=36, pady=(0, 8))
        self.filter_btns = {}
        for name in ("All", "AMS2", "RaceRoom"):
            b = tk.Button(bar, text=name, command=lambda n=name: self.set_filter(n), relief="flat", bd=0,
                          font=(t.FONT, 9, "bold"), padx=14, pady=5, cursor="hand2", highlightthickness=0)
            b.pack(side="left", padx=(0, 6))
            self.filter_btns[name] = b
        search = tk.Entry(bar, textvariable=self.search_var, bg=t.CARD, fg=t.TEXT, relief="flat", bd=0,
                          font=(t.FONT, 10), insertbackground=t.TEXT, highlightthickness=1,
                          highlightbackground=t.BORDER, highlightcolor=t.ACCENT, width=28)
        search.pack(side="right", ipady=5)
        t.label(bar, "Search", size=9, colour=t.DIM).pack(side="right", padx=(0, 8))
        self._style_filters()

        self.scroll = t.ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=(36, 20), pady=(4, 16))
        self.refresh()

    # ------------------------------------------------------------------ data

    def refresh(self, force=False):
        self.races = racefiles.list_races()
        sig = tuple((r["folder"], pipeline.progress(r["folder"])) for r in self.races)
        pending = importer.pending_projects()
        if pending:
            self.import_btn.config(text=f"Import old projects ({len(pending)})")
        self.banner.itemconfigure(self.import_win, state="normal" if pending else "hidden")
        self._layout_banner(self.banner.winfo_width())
        if force or sig != self._sig:
            self._sig = sig
            self.render_list()

    def _layout_banner(self, w, h=None):
        self.banner.coords(self.folder_win, w - 36, 62)
        self.update_idletasks()
        self.banner.coords(self.import_win, w - 36 - self.folder_btn.winfo_reqwidth() - 10, 62)

    def set_filter(self, name):
        self.filter = name
        self._style_filters()
        self.render_list()

    def _style_filters(self):
        for name, b in self.filter_btns.items():
            on = name == self.filter
            b.config(bg=t.TEXT if on else t.NEUTRAL_SOFT, fg=t.BG if on else t.MUTED,
                     activebackground=t.TEXT if on else t.BORDER, activeforeground=t.BG if on else t.TEXT)

    def visible_races(self):
        q = self.search_var.get().strip().lower()
        out = []
        for r in self.races:
            if self.filter != "All" and r["meta"].get("sim") != self.filter:
                continue
            if q:
                s = r["summary"]
                hay = " ".join([r["name"], s.get("track", ""), " ".join(s.get("cars", [])), s.get("winner", "")]).lower()
                if q not in hay:
                    continue
            out.append(r)
        return out

    # ------------------------------------------------------------------ cards

    def render_list(self):
        inner = self.scroll.inner
        for w in inner.winfo_children():
            w.destroy()
        races = self.visible_races()
        if not races:
            empty = tk.Frame(inner, bg=t.BG)
            empty.pack(fill="x", pady=60)
            if self.races:
                t.label(empty, "No races match.", size=13, colour=t.MUTED, anchor="center").pack()
            else:
                t.label(empty, "No races yet", size=16, weight="bold", anchor="center").pack()
                t.label(empty, "Go to Race, pick your sim and drive a race of at least 5 minutes. "
                               "It shows up here by itself.", size=10, colour=t.MUTED, anchor="center").pack(pady=(4, 12))
                t.button(empty, "Start racing", lambda: self.app.show("home"), kind="primary").pack()
            return

        last_month = None
        for race in races:
            when = started(race["meta"], race["name"])
            month = when.strftime("%B %Y") if when else "Earlier"
            if month != last_month:
                t.label(inner, month.upper(), size=8, weight="bold", colour=t.DIM).pack(anchor="w", pady=(14, 6))
                last_month = month
            self._card(inner, race, when).pack(fill="x", pady=4, padx=(0, 14))
        self.scroll.to_top()

    def _card(self, parent, race, when):
        folder, meta, s = race["folder"], race["meta"], race["summary"]
        c = t.card(parent, padx=18, pady=14)
        c.configure(cursor="hand2")
        c.columnconfigure(1, weight=1)

        logo_box = tk.Frame(c, bg=t.CARD, width=62, height=62)
        logo_box.grid(row=0, column=0, rowspan=3, sticky="w", padx=(0, 18))
        logo_box.pack_propagate(False)
        img = t.logo_badge(meta.get("sim", ""), 60)
        if img:
            tk.Label(logo_box, image=img, bg=t.CARD).pack(expand=True)

        t.label(c, t.pretty_track(s.get("track") or meta.get("track")), size=13, weight="bold").grid(row=0, column=1, sticky="w")
        bits = []
        if when:
            bits.append(f"{when:%a %d %b %Y}  ·  {when:%H:%M}")
        if s.get("duration_sec"):
            bits.append(f"{t.minutes(s['duration_sec'])} race")
        if s.get("drivers"):
            bits.append(f"{s['drivers']} drivers")
        car = racefiles.race_label(s)
        if car:
            bits.append(car)
        t.label(c, "  ·  ".join(bits), size=9, colour=t.MUTED).grid(row=1, column=1, sticky="w", pady=(2, 0))
        extra = []
        if s.get("winner"):
            extra.append(f"Winner: {s['winner']}")
        if s.get("race_length"):
            extra.append(s["race_length"])
        if meta.get("imported_from"):
            extra.append(f"imported from {meta['imported_from']}")
        t.label(c, "  ·  ".join(extra), size=9, colour=t.DIM).grid(row=2, column=1, sticky="w")

        done, total = pipeline.progress(folder)
        prog = tk.Frame(c, bg=t.CARD)
        prog.grid(row=0, column=2, rowspan=3, padx=(16, 16), sticky="e")
        if done == total:
            t.label(prog, "✓ Replay finished", size=9, weight="bold", colour=t.SUCCESS).pack(anchor="e")
        elif done == 0:
            t.label(prog, "Not started", size=9, weight="bold", colour=t.DIM).pack(anchor="e")
        else:
            t.label(prog, f"{done} of {total} stages", size=9, weight="bold", colour=t.TEXT).pack(anchor="e")
        segs = tk.Frame(prog, bg=t.CARD)
        segs.pack(anchor="e", pady=(4, 2))
        for i in range(total):
            tk.Frame(segs, bg=t.SUCCESS if i < done else t.BORDER, width=18, height=5).pack(side="left", padx=1)
        if done < total:
            t.label(prog, f"Next: {pipeline.next_stage(folder)['title']}", size=8, colour=t.DIM).pack(anchor="e")

        btns = tk.Frame(c, bg=t.CARD)
        btns.grid(row=0, column=3, rowspan=3, sticky="e")
        video = racefiles.race_file(folder, "final_video")
        if os.path.exists(video):
            t.button(btns, "▶ Watch", lambda: os.startfile(video), kind="success").pack(fill="x")
        t.button(btns, "Continue" if done else "Make replay", lambda: self.app.open_race(folder),
                 kind="secondary" if os.path.exists(video) else "primary").pack(fill="x", pady=(4, 0))
        small = tk.Frame(btns, bg=t.CARD)
        small.pack(fill="x", pady=(4, 0))
        t.button(small, "Folder", lambda: os.startfile(folder), kind="ghost", size=8, padx=6, pady=1).pack(side="left")
        t.button(small, "Archive", lambda: self.archive(race), kind="ghost", size=8, padx=6, pady=1).pack(side="right")

        def hover(on):
            c.config(highlightbackground=t.BORDER_STRONG if on else t.BORDER)

        t.bind_tree(c, "<Enter>", lambda e: hover(True))
        t.bind_tree(c, "<Leave>", lambda e: hover(False))
        for w in (c, logo_box) + tuple(x for x in c.grid_slaves() if isinstance(x, tk.Label)):
            w.bind("<Button-1>", lambda e: self.app.open_race(folder))
        return c

    # ------------------------------------------------------------------ actions

    def open_folder(self):
        os.makedirs(racefiles.RACES_DIR, exist_ok=True)
        os.startfile(racefiles.RACES_DIR)

    def archive(self, race):
        name = t.pretty_track(race["summary"].get("track"))
        if not messagebox.askyesno("Archive race", f"Move the race at {name} to the archive?\n\n"
                                   f"It is moved to Races\\{racefiles.ARCHIVE_DIR_NAME} and disappears from this list. "
                                   "Nothing is deleted.", parent=self):
            return
        archive = os.path.join(racefiles.RACES_DIR, racefiles.ARCHIVE_DIR_NAME)
        os.makedirs(archive, exist_ok=True)
        try:
            shutil.move(race["folder"], racefiles.unique_folder(os.path.join(archive, race["name"])))
        except OSError as e:
            messagebox.showerror("Archive race", f"Could not move it:\n{e}", parent=self)
        self.refresh(force=True)

    def import_old(self):
        pending = importer.pending_projects()
        if not pending:
            return
        if not messagebox.askyesno(
                "Import old projects",
                f"Bring {len(pending)} project(s) from the old launcher into My races?\n\n"
                "Every race log of 5 minutes or more becomes a race, and the files the project made are copied "
                "under the new names so you can carry on where you left off.\n\n"
                "Your Projects folder is not changed.", parent=self):
            return
        self.import_btn.config(text="Importing…", state="disabled")

        def work():
            made, errors = 0, []
            for name in pending:
                try:
                    made += len(importer.import_project(name))
                except Exception as e:
                    errors.append(f"{name}: {e}")
            self.after(0, lambda: self._imported(made, errors))

        threading.Thread(target=work, daemon=True).start()

    def _imported(self, made, errors):
        self.import_btn.config(state="normal")
        self.refresh(force=True)
        self.app.toast(f"Imported {made} race{'s' if made != 1 else ''} from your old projects.")
        if errors:
            messagebox.showwarning("Import old projects", "Some projects could not be imported:\n\n" + "\n".join(errors),
                                   parent=self)
