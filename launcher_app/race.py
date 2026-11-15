"""One race: a guided path from the race log to the finished replay, one stage at a time."""
import datetime
import os
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import racefiles
from racefiles import race_file
from . import pipeline
from . import theme as t

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi")
STATE_PILL = {
    "done": ("✓ Done", t.SUCCESS, t.SUCCESS_SOFT),
    "outdated": ("Out of date", t.WARNING, t.WARNING_SOFT),
    "ready": ("Ready", t.INFO, t.INFO_SOFT),
    "waiting": ("Waiting", t.DIM, t.NEUTRAL_SOFT),
    "running": ("Running…", "#ffffff", t.INFO),
    "optional": ("Optional", t.DIM, t.NEUTRAL_SOFT),
}


class RaceScreen(tk.Frame):
    def __init__(self, parent, app, folder):
        super().__init__(parent, bg=t.BG)
        self.app = app
        self.folder = folder
        self.meta = racefiles.read_race(folder)
        self.stage_id = pipeline.next_stage(folder)["id"]
        if self.stage_id == "film" and not os.path.exists(race_file(folder, "race_notes")) \
                and pipeline.step_status(folder, "camera")[0] != "done":
            self.stage_id = "notes"  # a fresh race starts with the notes
        self._stepper_sig = None
        self._detail_sig = None
        self._notes_jobs = {}
        self._notes_widgets = {}

        self._build_header()
        body = tk.Frame(self, bg=t.BG)
        body.pack(fill="both", expand=True, padx=36, pady=(4, 16))
        self.stepper = tk.Frame(body, bg=t.BG, width=250)
        self.stepper.pack(side="left", fill="y", padx=(0, 22))
        self.stepper.pack_propagate(False)
        self.detail = t.ScrollFrame(body)
        self.detail.pack(side="left", fill="both", expand=True)
        self.refresh(force=True)
        self._poll()

    # ------------------------------------------------------------------ header

    def _build_header(self):
        s = self.meta.get("summary") or racefiles.summarize_log(race_file(self.folder, "race_log"))
        self.banner = b = t.PhotoCanvas(self, focus=0.35, fade=0.5, height=184, on_layout=self._layout_banner)
        b.pack(fill="x")
        b.link(36, 30, "←  My races", lambda: self.app.show("library"), size=9, anchor="w",
               colour=t.MUTED, hover="#ffffff")
        img = t.logo_badge(self.meta.get("sim", ""), 76)
        if img:
            b.create_image(36, 92, image=img, anchor="w")
        b.create_text(130, 80, text=t.pretty_track(s.get("track") or self.meta.get("track")), fill=t.TEXT,
                      font=(t.FONT, 22, "bold"), anchor="w")
        bits = []
        try:
            when = datetime.datetime.fromisoformat(self.meta.get("started_at"))
            bits.append(f"{when:%A %d %B %Y, %H:%M}")
        except (TypeError, ValueError):
            pass
        if s.get("duration_sec"):
            bits.append(f"{t.minutes(s['duration_sec'])} race")
        if s.get("drivers"):
            bits.append(f"{s['drivers']} drivers")
        if racefiles.race_label(s):
            bits.append(racefiles.race_label(s))
        if s.get("winner"):
            bits.append(f"won by {s['winner']}")
        b.create_text(130, 112, text="  ·  ".join(bits), fill=t.MUTED, font=(t.FONT, 10), anchor="w")
        b.create_window(130, 150, anchor="w", window=self._championship_picker(b))
        self.folder_win = b.create_window(0, 92, anchor="e", window=t.button(
            b, "Open race folder", lambda: os.startfile(self.folder), size=9))

    # ------------------------------------------------------------------ championship

    @staticmethod
    def _championships():
        """The saved graphics championships, by name."""
        try:
            import sys
            if racefiles.APP_DIR not in sys.path:
                sys.path.insert(0, racefiles.APP_DIR)
            from GraphicsOverlay import config as gcfg
            return gcfg.list_championships()
        except Exception:
            return []

    def _championship_picker(self, parent):
        """Which championship the race belongs to: chosen before any step runs, so the graphics and the drivers'
        details go to the right one (or nowhere, for an invitational)."""
        f = tk.Frame(parent, bg=t.CARD, padx=10, pady=5, highlightthickness=1, highlightbackground=t.BORDER)
        chosen = racefiles.race_championship(self.folder)
        names = self._championships()
        options = names + [racefiles.INVITATIONAL_LABEL]
        if chosen and chosen != racefiles.INVITATIONAL and chosen not in names:
            options.insert(0, chosen)  # deleted since: still shown, so the race says what it was made for
        shown = racefiles.INVITATIONAL_LABEL if chosen == racefiles.INVITATIONAL else chosen
        self._champ_label = t.label(f, "Championship", size=9, weight="bold",
                                    colour=t.MUTED if chosen else t.WARNING)
        self._champ_label.pack(side="left", padx=(0, 8))
        var = tk.StringVar(value=shown)
        box = ttk.Combobox(f, textvariable=var, values=options, state="readonly", width=34)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e: self._choose_championship(var.get()))
        self._champ_hint = t.label(f, "" if chosen else "Choose one before you start", size=9, colour=t.WARNING)
        self._champ_hint.pack(side="left", padx=(8, 0))
        return f

    def _choose_championship(self, label):
        value = racefiles.INVITATIONAL if label == racefiles.INVITATIONAL_LABEL else label
        racefiles.set_race_championship(self.folder, value)
        self.meta = racefiles.read_race(self.folder)
        self._champ_label.config(fg=t.MUTED)
        self._champ_hint.config(text="")
        # The notes stage shows that championship's notes: save any typing, then redraw it
        self.flush_notes()
        self.refresh(force=True)
        self.app.toast(f"This race is part of {label}." if value != racefiles.INVITATIONAL else
                       "This race is a one-off: it starts with a blank slate and keeps its drivers to itself.")

    def _layout_banner(self, w, h):
        self.banner.coords(self.folder_win, w - 36, 92)

    # ------------------------------------------------------------------ refresh

    def _poll(self):
        if self.winfo_exists():
            self.refresh()
            self.after(2000, self._poll)

    def _signature(self):
        steps = tuple((sid, pipeline.step_status(self.folder, sid), bool(self.app.pm.find_step(self.folder, sid)))
                      for sid in pipeline.STEPS)
        return steps, pipeline.input_path(self.folder, "video")

    def refresh(self, force=False):
        sig = self._signature()
        notes = os.path.exists(race_file(self.folder, "race_notes"))
        if force or (sig, notes, self.stage_id) != self._stepper_sig:
            self._stepper_sig = (sig, notes, self.stage_id)
            self._build_stepper()
        if force or (sig, self.stage_id) != self._detail_sig:
            self._detail_sig = (sig, self.stage_id)
            self._build_detail()

    def select(self, stage_id):
        self.stage_id = stage_id
        self.refresh()
        self.detail.to_top()

    # ------------------------------------------------------------------ stepper

    def _build_stepper(self):
        for w in self.stepper.winfo_children():
            w.destroy()
        t.label(self.stepper, "MAKE THE REPLAY", size=8, weight="bold", colour=t.DIM).pack(anchor="w", pady=(4, 10))
        for i, stage in enumerate(pipeline.STAGES):
            state, done, total = pipeline.stage_status(self.folder, stage)
            running = any(self.app.pm.find_step(self.folder, s) for s in stage["steps"])
            selected = stage["id"] == self.stage_id
            row = tk.Frame(self.stepper, bg=t.CARD if selected else t.BG, padx=10, pady=8, cursor="hand2",
                           highlightthickness=1, highlightbackground=t.BORDER if selected else t.BG)
            row.pack(fill="x", pady=2)
            if state == "done":
                dot_bg, dot_fg, mark = t.SUCCESS, "#ffffff", "✓"
            elif running:
                dot_bg, dot_fg, mark = t.INFO, "#ffffff", "…"
            elif selected:
                dot_bg, dot_fg, mark = t.ACCENT, "#ffffff", str(i)
            else:
                dot_bg, dot_fg, mark = t.NEUTRAL_SOFT, t.MUTED, str(i)
            if stage["id"] == "notes" and state != "done":
                mark = "✎"
            tk.Label(row, text=mark, bg=dot_bg, fg=dot_fg, font=(t.FONT, 9, "bold"), width=3).pack(side="left", padx=(0, 10))
            words = tk.Frame(row, bg=row.cget("bg"))
            words.pack(side="left", fill="x", expand=True)
            t.label(words, stage["title"], size=10, weight="bold",
                    colour=t.TEXT if (selected or state != "waiting") else t.MUTED).pack(anchor="w")
            if running:
                sub = "Running…"
            elif stage["id"] == "notes":
                sub = "Written" if state == "done" else "Optional, but worth it"
            elif state == "done":
                sub = "Done"
            elif done:
                sub = f"{done} of {total} done"
            else:
                sub = "Ready to start" if state == "ready" else "Not yet"
            t.label(words, sub, size=8, colour=t.INFO if running else t.DIM).pack(anchor="w")
            t.bind_tree(row, "<Button-1>", lambda e, sid=stage["id"]: self.select(sid))

        video = race_file(self.folder, "final_video")
        if os.path.exists(video):
            t.button(self.stepper, "▶  Watch the replay", lambda: os.startfile(video), kind="success",
                     size=10).pack(fill="x", pady=(16, 0))

    # ------------------------------------------------------------------ stage detail

    def _build_detail(self):
        inner = self.detail.inner
        for w in inner.winfo_children():
            w.destroy()
        stage = next(s for s in pipeline.STAGES if s["id"] == self.stage_id)
        index = pipeline.STAGES.index(stage)
        pad = tk.Frame(inner, bg=t.BG)
        pad.pack(fill="both", expand=True, padx=(0, 16))

        top = tk.Frame(pad, bg=t.BG)
        top.pack(fill="x")
        t.label(top, f"STEP {index} OF {len(pipeline.STAGES) - 1}" if index else "BEFORE YOU START",
                size=8, weight="bold", colour=t.ACCENT).pack(anchor="w")
        t.label(top, stage["title"], size=18, weight="bold").pack(anchor="w", pady=(2, 6))
        t.label(top, stage["about"], size=10, colour=t.MUTED, wraplength=760).pack(anchor="w")

        autos = [s for s in stage["steps"] if pipeline.STEPS[s]["kind"] == "auto"]
        if len(autos) > 1:
            pending = [s for s in autos if pipeline.step_status(self.folder, s)[0] != "done"]
            runnable = pending and pipeline.step_status(self.folder, pending[0])[0] in ("ready", "outdated")
            busy = any(self.app.pm.find_step(self.folder, s) for s in autos)
            if runnable and not busy and len(pending) > 1:
                tk.Frame(pad, bg=t.BG, height=6).pack()
                t.button(pad, f"▶  Run all {len(pending)} automatic steps",
                         lambda: self.app.run_chain(self.folder, pending), kind="primary", size=10).pack(anchor="w", pady=(8, 0))

        tk.Frame(pad, bg=t.BG, height=10).pack()
        if stage["id"] == "notes":
            self._notes_editors(pad)
        if stage["id"] == "commentary":
            self._grid_intro_card(pad).pack(fill="x", pady=5)
            self._qualifying_card(pad).pack(fill="x", pady=5)
        for sid in stage["steps"]:
            self._step_card(pad, sid).pack(fill="x", pady=5)
            if sid == "camera":
                self._video_card(pad).pack(fill="x", pady=5)

        # Move on
        state, _, _ = pipeline.stage_status(self.folder, stage)
        nav = tk.Frame(pad, bg=t.BG)
        nav.pack(fill="x", pady=(18, 10))
        if index + 1 < len(pipeline.STAGES):
            nxt = pipeline.STAGES[index + 1]
            t.button(nav, f"Next: {nxt['title']}  →", lambda: self.select(nxt["id"]),
                     kind="primary" if state in ("done", "optional") else "secondary").pack(side="right")
        if index > 0:
            prev = pipeline.STAGES[index - 1]
            t.button(nav, f"←  {prev['title']}", lambda: self.select(prev["id"]), kind="ghost").pack(side="left")
        if stage["id"] == "export" and os.path.exists(race_file(self.folder, "final_video")):
            done = tk.Frame(pad, bg=t.SUCCESS_SOFT, padx=16, pady=14)
            done.pack(fill="x", pady=(6, 0))
            t.label(done, "Your replay is ready!", size=12, weight="bold", colour=t.SUCCESS).pack(anchor="w")
            t.label(done, race_file(self.folder, "final_video"), size=9, colour=t.MUTED).pack(anchor="w")
            t.button(done, "▶  Watch it", lambda: os.startfile(race_file(self.folder, "final_video")),
                     kind="success").pack(anchor="w", pady=(8, 0))

    def _step_card(self, parent, sid):
        step = pipeline.STEPS[sid]
        state, detail = pipeline.step_status(self.folder, sid)
        proc = self.app.pm.find_step(self.folder, sid)
        c = t.card(parent, padx=18, pady=14)
        c.columnconfigure(0, weight=1)

        head = tk.Frame(c, bg=t.CARD)
        head.grid(row=0, column=0, sticky="ew")
        text, fg, bg = STATE_PILL["running" if proc else state]
        t.pill(head, text, fg, bg).pack(side="left", padx=(0, 10))
        t.label(head, step["title"], size=11, weight="bold").pack(side="left")
        t.label(head, "  ·  opens a window" if step["kind"] == "tool" else "  ·  automatic", size=9,
                colour=t.DIM).pack(side="left")

        t.label(c, step["hint"], size=9, colour=t.MUTED, wraplength=560).grid(row=1, column=0, sticky="w", pady=(6, 0))
        if detail and not proc:
            t.label(c, detail, size=9, colour=t.WARNING if state == "outdated" else t.DIM).grid(
                row=2, column=0, sticky="w", pady=(4, 0))

        prompts = pipeline.prompts_for(step)
        if prompts:
            row = tk.Frame(c, bg=t.CARD)
            row.grid(row=3, column=0, sticky="w", pady=(10, 0))
            t.label(row, "Prompt", size=9, colour=t.DIM).pack(side="left", padx=(0, 8))
            var = tk.StringVar(value=self.app.prompt_choice(sid))
            box = ttk.Combobox(row, textvariable=var, values=prompts, state="readonly", width=40)
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", lambda e, s=sid, v=var: self.app.set_prompt_choice(s, v.get()))
            t.button(row, "Copy prompt", lambda s=sid: self.app.copy_prompt(self.folder, s), kind="link",
                     size=9).pack(side="left", padx=(8, 0))

        btns = tk.Frame(c, bg=t.CARD)
        btns.grid(row=0, column=1, rowspan=4, sticky="ne", padx=(16, 0))
        if proc:
            t.button(btns, "Show progress", lambda: self.app.show_activity(proc), kind="secondary").pack(fill="x")
            t.button(btns, "Stop", lambda: self.app.pm.stop(proc), kind="ghost", size=9).pack(fill="x", pady=(4, 0))
        else:
            verb = "Open" if step["kind"] == "tool" else "Run"
            if state == "done":
                verb = f"{verb} again"
            kind = "primary" if state in ("ready", "outdated") else "secondary"
            b = t.button(btns, f"▶  {verb}" if kind == "primary" else verb,
                         lambda: self.app.run_step(self.folder, sid), kind=kind)
            b.pack(fill="x")
            if state == "waiting":
                b.config(state="disabled", bg=t.NEUTRAL_SOFT, fg=t.DIM, cursor="arrow")
                b._colours = (t.NEUTRAL_SOFT, t.NEUTRAL_SOFT)
            out = pipeline.output_path(self.folder, step)
            if os.path.exists(out):
                t.button(btns, "View result", lambda: os.startfile(out), kind="ghost", size=9).pack(fill="x", pady=(4, 0))
        return c

    def _video_card(self, parent):
        c = t.card(parent, padx=18, pady=14)
        video = pipeline.input_path(self.folder, "video")
        head = tk.Frame(c, bg=t.CARD)
        head.pack(fill="x")
        text, fg, bg = STATE_PILL["done" if video else "ready"]
        t.pill(head, text, fg, bg).pack(side="left", padx=(0, 10))
        t.label(head, "Replay video", size=11, weight="bold").pack(side="left")
        t.button(head, "Choose file…", self.browse_video, kind="secondary" if video else "primary").pack(side="right")

        if video:
            t.label(c, video, size=9, colour=t.MUTED).pack(anchor="w", pady=(6, 0))
        else:
            t.label(c, "The screen recording of the replay, used for reviewing the events, the graphics and the "
                       "final video.", size=9, colour=t.MUTED, wraplength=560).pack(anchor="w", pady=(6, 0))

        suggestions = [v for v in self.recent_videos() if v[0] != video]
        if suggestions:
            t.label(c, "Recent recordings in " + self.app.videos_folder(), size=8, weight="bold",
                    colour=t.DIM).pack(anchor="w", pady=(12, 4))
            for path, when, size in suggestions:
                row = tk.Frame(c, bg=t.INPUT, padx=10, pady=6)
                row.pack(fill="x", pady=2)
                t.label(row, os.path.basename(path), size=9, weight="bold").pack(side="left")
                t.label(row, f"   {when:%a %d %b, %H:%M}  ·  {size / 1e9:.1f} GB", size=9, colour=t.MUTED).pack(side="left")
                t.button(row, "Use this", lambda p=path: self.set_video(p), kind="link", size=9, padx=4,
                         pady=0).pack(side="right")
        return c

    # ------------------------------------------------------------------ grid intro

    def _grid_settings(self):
        """This race's grid intro settings; a race that has none yet takes the last ones used."""
        if "grid_intro" not in self.meta:
            remembered = self.app.settings.get("grid_intro") or {}
            racefiles.update_race(self.folder, grid_intro={**racefiles.GRID_INTRO_DEFAULTS, **remembered})
            self.meta = racefiles.read_race(self.folder)
        return racefiles.grid_intro_settings(self.folder)

    def _save_grid(self, **changes):
        s = {**self._grid_settings(), **changes}
        racefiles.update_race(self.folder, grid_intro=s)
        self.meta = racefiles.read_race(self.folder)
        self.app.settings["grid_intro"] = s  # the next race starts with the same choices
        self.app.save_settings()

    def _grid_intro_card(self, parent):
        s = self._grid_settings()
        c = t.card(parent, padx=18, pady=14)
        head = tk.Frame(c, bg=t.CARD)
        head.pack(fill="x")
        on = tk.BooleanVar(value=s["enabled"])
        tk.Checkbutton(head, variable=on, bg=t.CARD, activebackground=t.CARD, selectcolor=t.INPUT,
                       fg=t.TEXT, activeforeground=t.TEXT, font=(t.FONT, 11, "bold"), cursor="hand2",
                       text="  Starting grid before the start", bd=0, highlightthickness=0,
                       command=lambda: self._save_grid(enabled=on.get())).pack(side="left")
        t.label(head, "  ·  optional", size=9, colour=t.DIM).pack(side="left")
        t.label(c, "The video opens on a full-screen starting grid while the commentators set the scene, then "
                   "fades into the replay. The replay is held on one frame under it, so the sim's menus never "
                   "show. Nothing after the start moves. Change it before running the steps below.",
                size=9, colour=t.MUTED, wraplength=640).pack(anchor="w", pady=(6, 8))

        def spin(row, var, lo, hi, step, save):
            box = tk.Spinbox(row, from_=lo, to=hi, increment=step, textvariable=var, width=5, bg=t.INPUT, fg=t.TEXT,
                             buttonbackground=t.CARD, relief="flat", insertbackground=t.TEXT, justify="center",
                             font=(t.FONT, 10), command=save)
            box.bind("<FocusOut>", lambda e: save())
            box.bind("<Return>", lambda e: save())
            return box

        row = tk.Frame(c, bg=t.CARD)
        row.pack(fill="x", pady=2)
        secs = tk.StringVar(value=str(s["seconds"]))
        lo, hi = racefiles.GRID_SECONDS_RANGE

        def save_secs():
            try:
                self._save_grid(seconds=max(lo, min(hi, int(float(secs.get())))))
            except ValueError:
                pass
            secs.set(str(racefiles.grid_intro_settings(self.folder)["seconds"]))
        t.label(row, "Grid shown for", size=9, colour=t.DIM, width=16, anchor="w").pack(side="left")
        spin(row, secs, lo, hi, 1, save_secs).pack(side="left")
        t.label(row, f"  seconds before the green flag ({lo}–{hi})", size=9, colour=t.MUTED).pack(side="left")

        row = tk.Frame(c, bg=t.CARD)
        row.pack(fill="x", pady=2)
        lead = tk.StringVar(value=f"{s['hold_lead']:g}")

        def save_lead():
            try:
                self._save_grid(hold_lead=max(0.0, min(10.0, float(lead.get()))))
            except ValueError:
                pass
            lead.set(f"{racefiles.grid_intro_settings(self.folder)['hold_lead']:g}")
        t.label(row, "Replay starts", size=9, colour=t.DIM, width=16, anchor="w").pack(side="left")
        spin(row, lead, 0, 10, 0.5, save_lead).pack(side="left")
        t.label(row, "  seconds before the green flag (the frame held under the grid)", size=9,
                colour=t.MUTED).pack(side="left")
        t.button(row, "Check frame", self.show_hold_frame, kind="link", size=9).pack(side="left", padx=(8, 0))

        row = tk.Frame(c, bg=t.CARD)
        row.pack(fill="x", pady=2)
        t.label(row, "Music", size=9, colour=t.DIM, width=16, anchor="w").pack(side="left")
        music = tk.StringVar(value=s["music"] or "(none)")
        box = ttk.Combobox(row, textvariable=music, values=["(none)"] + racefiles.list_music(), state="readonly",
                           width=34)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>",
                 lambda e: self._save_grid(music="" if music.get() == "(none)" else music.get()))
        t.button(row, "Add track…", self.add_music, kind="secondary", size=9).pack(side="left", padx=(8, 0))
        level = tk.StringVar(value=s["music_level"])
        lv = ttk.Combobox(row, textvariable=level, values=["Quiet", "Normal", "Loud"], state="readonly", width=8)
        lv.pack(side="left", padx=(12, 0))
        lv.bind("<<ComboboxSelected>>", lambda e: self._save_grid(music_level=level.get()))
        t.label(c, "Plays under the grid and fades out as the replay starts. Tracks are kept in the Music folder. "
                   "Changing the music or the replay start only needs a new export.",
                size=8, colour=t.DIM, wraplength=640).pack(anchor="w", pady=(4, 0))
        return c

    # ------------------------------------------------------------------ qualifying times

    def _qualifying_card(self, parent):
        c = t.card(parent, padx=18, pady=14)
        head = tk.Frame(c, bg=t.CARD)
        head.pack(fill="x")
        t.label(head, "Qualifying times", size=11, weight="bold").pack(side="left")
        t.label(head, "  ·  optional", size=9, colour=t.DIM).pack(side="left")
        data = racefiles.read_qualifying(self.folder)
        if data:
            rows = data["drivers"]
            pole = rows[0]["time"]
            src = {"recorded": "Saved by the recorder from qualifying",
                   "screenshot": "Read from a screenshot by the AI"}.get(data.get("source"), "Pasted in")

            def shown(r):
                return racefiles.format_lap_time(r["time"]) if r["pos"] == 1 else f"+{r['time'] - pole:.3f}"
            listing = "   ".join(f"{r['pos']}. {r['name']} {shown(r)}" for r in rows)
            t.label(c, f"{src}: {len(rows)} drivers.", size=9, colour=t.SUCCESS).pack(anchor="w", pady=(6, 2))
            t.label(c, listing, size=9, colour=t.MUTED, wraplength=700, justify="left").pack(anchor="w")
        else:
            t.label(c, "None yet, so the starting grid shows no times. Race with the recorder on during qualifying "
                       "and the race after it picks them up, or paste the results below.",
                    size=9, colour=t.MUTED, wraplength=640).pack(anchor="w", pady=(6, 0))
        t.label(c, "Paste the qualifying results (from the results screen, the league site or a spreadsheet): one "
                   "driver a line with their lap time, like 1:42.315, or gap, like +0.214. Or paste a screenshot "
                   "of them (Ctrl+V), or pick one with Read screenshot, and the AI reads it. Pasted times replace "
                   "the recorded ones. A screenshot also fills in the drivers' countries from their flags: one "
                   "of the game's driver list at the start works too, even without any times.",
                size=8, colour=t.DIM, wraplength=640).pack(anchor="w", pady=(10, 4))
        box = tk.Text(c, height=5, bg=t.INPUT, fg=t.TEXT, relief="flat", bd=0, font=(t.FONT, 10), wrap="none",
                      padx=10, pady=8, insertbackground=t.TEXT, highlightthickness=1, highlightbackground=t.BORDER,
                      highlightcolor=t.ACCENT)
        box.pack(fill="x")
        row = tk.Frame(c, bg=t.CARD)
        row.pack(fill="x", pady=(8, 0))
        result = t.label(row, getattr(self, "_quali_note", ""), size=9, colour=t.DIM, wraplength=480, justify="left")
        self._quali_note = ""
        t.button(row, "Read pasted results", lambda: self.read_pasted_qualifying(box, result),
                 kind="secondary", size=9).pack(side="left")
        t.button(row, "Read screenshot…", lambda: self.pick_qualifying_screenshot(result),
                 kind="secondary", size=9).pack(side="left", padx=(8, 0))
        box.bind("<<Paste>>", lambda e: self.paste_qualifying_screenshot(result))
        if data:
            t.button(row, "Clear times", self.clear_qualifying, kind="ghost", size=9).pack(side="left", padx=(8, 0))
        result.pack(side="left", padx=(12, 0))
        return c

    def _driver_aliases(self):
        """{log name: names the results may use for them} from the race log and the Drivers tab."""
        names = racefiles.grid_names(self.folder)
        drivers = {}
        try:
            import sys
            if racefiles.APP_DIR not in sys.path:
                sys.path.insert(0, racefiles.APP_DIR)
            from GraphicsOverlay import config as gcfg
            drivers = gcfg.load_config(self.folder).get("drivers") or {}
        except Exception:
            pass
        return {n: [(drivers.get(n) or {}).get("display"), (drivers.get(n) or {}).get("short")] for n in names}

    def read_pasted_qualifying(self, box, result):
        text = box.get("1.0", "end").strip()
        if not text:
            result.config(text="Paste the results into the box first.", fg=t.WARNING)
            return
        aliases = self._driver_aliases()
        if not aliases:
            result.config(text="The race log has no starting grid to match the names to.", fg=t.WARNING)
            return
        rows, unmatched = racefiles.parse_pasted_qualifying(text, aliases)
        if not rows:
            result.config(text="No driver from this race with a lap time was found in the pasted text.", fg=t.WARNING)
            return
        racefiles.save_qualifying(self.folder, rows, "pasted")
        missing = [n for n in aliases if n not in {r["name"] for r in rows}]
        note = f"Read {len(rows)} of {len(aliases)} drivers."
        if missing:
            note += " No time for: " + ", ".join(missing[:6]) + ("…" if len(missing) > 6 else "") + "."
        if unmatched:
            note += f" {len(unmatched)} line(s) with a time matched no driver."
        self._quali_note = note
        self.app.toast(note)
        self.refresh(force=True)

    def pick_qualifying_screenshot(self, result):
        path = filedialog.askopenfilename(parent=self, title="Choose a screenshot of the qualifying results",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if not path:
            return
        try:
            from PIL import Image
            image = Image.open(path)
            image.load()
        except Exception as e:
            result.config(text=f"Could not open the image: {e}", fg=t.WARNING)
            return
        self.read_qualifying_screenshot(image, result)

    def paste_qualifying_screenshot(self, result):
        """Ctrl+V in the results box: a screenshot on the clipboard goes to the AI; text pastes as usual."""
        try:
            from PIL import Image, ImageGrab
            clip = ImageGrab.grabclipboard()
        except Exception:
            return None
        if isinstance(clip, list):  # files copied in Explorer
            clip = next((p for p in clip if str(p).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))), None)
            if clip:
                try:
                    clip = Image.open(clip)
                    clip.load()
                except Exception:
                    clip = None
        if clip is None or not hasattr(clip, "size"):
            return None
        self.read_qualifying_screenshot(clip, result)
        return "break"

    def read_qualifying_screenshot(self, image, result):
        """Sends a screenshot of the results to Gemini in the background and saves what it reads."""
        api_key = self.app.settings.get("gemini_api_key")
        if not api_key:
            result.config(text="Set the Gemini API key in Settings first.", fg=t.WARNING)
            return
        if not racefiles.race_championship(self.folder):
            # The flags it reads are saved to the race's championship, so that has to be known, not guessed
            result.config(text="Choose the race's championship at the top of the page first: the drivers' "
                               "countries are saved to it.", fg=t.WARNING)
            return
        aliases = self._driver_aliases()
        if not aliases:
            result.config(text="The race log has no starting grid to match the names to.", fg=t.WARNING)
            return
        model = self.app.settings.get("gemini_model") or "gemini-4-hydrogen"
        result.config(text="Reading the screenshot with the AI…", fg=t.INFO)
        import threading
        from . import qualifying_ai

        def work():
            try:
                rows, unmatched, _, countries = qualifying_ai.read_screenshot(image, aliases, api_key, model)
                self.after(0, lambda: self._screenshot_read(rows, unmatched, aliases, result, countries))
            except Exception as e:
                msg = f"The AI couldn't read the screenshot: {e}"
                self.after(0, lambda: result.winfo_exists() and result.config(text=msg, fg=t.WARNING))
        threading.Thread(target=work, daemon=True).start()

    def _save_countries(self, countries):
        """The flags read off the screenshot go to the graphics drivers (this race and its championship).
        Returns a sentence for the note, or ''."""
        if not countries:
            return ""
        try:
            import sys
            if racefiles.APP_DIR not in sys.path:
                sys.path.insert(0, racefiles.APP_DIR)
            from GraphicsOverlay import config as gcfg
            changed = gcfg.set_driver_countries(self.folder, countries)
        except Exception as e:
            return f" Couldn't save the countries: {e}."
        where = ("this race only" if racefiles.race_championship(self.folder) == racefiles.INVITATIONAL
                 else racefiles.race_championship(self.folder))
        if not changed:
            return f" The {len(countries)} flags it read match the graphics drivers already."
        return (f" Flags: read {len(countries)}, set {len(changed)} countries for {where} ("
                + ", ".join(f"{n} {new}" for n, (_, new) in list(changed.items())[:5])
                + ("…" if len(changed) > 5 else "") + ").")

    def _screenshot_read(self, rows, unmatched, aliases, result, countries=None):
        flags_note = self._save_countries(countries)
        if not rows:
            # A grid set before the session (RaceRoom's leaderboard qualifying) has no times: the flags still count
            note = ("No lap times in the screenshot, so the grid shows none." + flags_note if countries else
                    "The AI found no lap times or flags for this race's drivers in the screenshot.")
            if result.winfo_exists():
                result.config(text=note, fg=t.SUCCESS if countries else t.WARNING)
            if countries:
                self.app.toast(note)
            return
        racefiles.save_qualifying(self.folder, rows, "screenshot")
        missing = [n for n in aliases if n not in {r["name"] for r in rows}]
        note = f"Read {len(rows)} of {len(aliases)} drivers from the screenshot. Check them below."
        if missing:
            note += " No time for: " + ", ".join(missing[:6]) + ("…" if len(missing) > 6 else "") + "."
        if unmatched:
            note += f" {len(unmatched)} driver(s) in it aren't in this race."
        note += flags_note
        self._quali_note = note
        self.app.toast(note)
        if self.winfo_exists():
            self.refresh(force=True)

    def clear_qualifying(self):
        racefiles.save_qualifying(self.folder, [], "pasted")
        self.app.toast("Qualifying times cleared: the grid shows no times.")
        self.refresh(force=True)

    def add_music(self):
        path = filedialog.askopenfilename(parent=self, title="Choose a music track for the starting grid",
                                          filetypes=[("Audio files", " ".join("*" + e for e in racefiles.MUSIC_EXTS)),
                                                     ("All files", "*.*")])
        if not path:
            return
        os.makedirs(racefiles.MUSIC_DIR, exist_ok=True)
        name = os.path.basename(path)
        dest = os.path.join(racefiles.MUSIC_DIR, name)
        try:
            if os.path.abspath(path) != os.path.abspath(dest):
                shutil.copy2(path, dest)
        except OSError as e:
            messagebox.showerror("Could not add the track", str(e), parent=self)
            return
        self._save_grid(music=name)
        self.app.toast(f"Music added: {name}")
        self.refresh(force=True)

    def show_hold_frame(self):
        video = pipeline.input_path(self.folder, "video")
        green = racefiles.green_flag_time(self.folder)
        if not video or green is None:
            messagebox.showinfo("Check frame", "Needs the replay video and the reviewed events (with the green "
                                               "flag) first.", parent=self)
            return
        t_hold = max(0.0, green - racefiles.grid_intro_settings(self.folder)["hold_lead"])
        out = os.path.join(self.folder, racefiles.DEBUG_DIR, "grid_hold_frame.jpg")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t_hold:.3f}", "-i", video, "-frames:v", "1", out],
                           check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            os.startfile(out)
        except (OSError, subprocess.CalledProcessError) as e:
            messagebox.showerror("Check frame", f"Could not take the frame: {e}", parent=self)

    def recent_videos(self, limit=4):
        """Video files in the recordings folder made after the race started, newest first."""
        folder = self.app.videos_folder()
        try:
            started = datetime.datetime.fromisoformat(self.meta.get("started_at")).timestamp()
        except (TypeError, ValueError):
            started = 0
        found = []
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if name.lower().endswith(VIDEO_EXTS) and os.path.isfile(path):
                    mtime = os.path.getmtime(path)
                    if mtime >= started:
                        found.append((path, datetime.datetime.fromtimestamp(mtime), os.path.getsize(path)))
        except OSError:
            return []
        found.sort(key=lambda v: v[1], reverse=True)
        return found[:limit]

    def browse_video(self):
        path = filedialog.askopenfilename(parent=self, title="Choose the replay video",
                                          initialdir=self.app.videos_folder(),
                                          filetypes=[("Video files", "*.mp4 *.mkv *.mov *.avi"), ("All files", "*.*")])
        if path:
            self.set_video(path)

    def set_video(self, path):
        racefiles.update_race(self.folder, video=os.path.abspath(path))
        self.meta = racefiles.read_race(self.folder)
        self.app.toast(f"Replay video set: {os.path.basename(path)}")
        self.refresh(force=True)

    # ------------------------------------------------------------------ notes

    def _championship_notes_card(self, parent):
        """The championship's own notes, carried over to every race in it; or why there are none here."""
        champ = racefiles.race_championship(self.folder)
        path = racefiles.championship_notes_path(champ)
        if path:
            return ("championship_notes", f"Championship notes  ·  {champ}",
                    "Everything that carries over from race to race: the drivers and their personalities, rivalries, "
                    "standings, running jokes, how to pronounce names. Shared by every race in this championship "
                    "and read before the race notes by the spice, commentary and graphics steps.", path)
        c = t.card(parent, padx=18, pady=14)
        c.pack(fill="x", pady=5)
        t.label(c, "Championship notes", size=11, weight="bold").pack(anchor="w")
        t.label(c, "An invitational or special event has no championship notes: put everything in the race notes."
                if champ else "Choose the race's championship at the top of the page to write notes that carry "
                              "over to every race in it.", size=9, colour=t.MUTED if champ else t.WARNING,
                wraplength=640).pack(anchor="w", pady=(4, 0))
        return None

    def _notes_editors(self, parent):
        self._notes_paths = {}
        editors = [self._championship_notes_card(parent)]
        for key, title, about in (
                ("race_notes", "Race notes", "This race only: storylines, who to focus on, what happened in "
                                             "qualifying. Used by the spice, commentary and graphics steps."),
                ("camera_notes", "Camera notes", "Only for the camera director - e.g. 'Focus on car #24 whenever it "
                                                 "is battling for position.'"),
                ("graphics_notes", "Graphics notes", "Background facts for the pop-up graphics, one per line. The "
                                                     "graphics AI picks the ones that fit - e.g. 'Thommo has won "
                                                     "three of the last four rounds here.'")):
            editors.append((key, title, about, race_file(self.folder, key)))
        for editor in editors:
            if editor is None:
                continue
            key, title, about, path = editor
            self._notes_paths[key] = path
            c = t.card(parent, padx=18, pady=14)
            c.pack(fill="x", pady=5)
            head = tk.Frame(c, bg=t.CARD)
            head.pack(fill="x")
            t.label(head, title, size=11, weight="bold").pack(side="left")
            saved = t.label(head, "", size=8, colour=t.DIM)
            saved.pack(side="right")
            t.label(c, about, size=9, colour=t.MUTED, wraplength=640).pack(anchor="w", pady=(4, 8))
            text = tk.Text(c, height=7 if key in ("race_notes", "championship_notes") else 4, bg=t.INPUT, fg=t.TEXT,
                           relief="flat", bd=0,
                           font=(t.FONT, 10), wrap="word", padx=10, pady=8, insertbackground=t.TEXT,
                           highlightthickness=1, highlightbackground=t.BORDER, highlightcolor=t.ACCENT, undo=True)
            text.pack(fill="x")
            existing = ""
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    existing = f.read()
            text.insert("1.0", existing.rstrip("\n"))
            text.edit_reset()
            self._notes_widgets[key] = (text, saved)
            text.bind("<KeyRelease>", lambda e, k=key, w=text, s=saved: self._queue_save(k, w, s))
            text.bind("<FocusOut>", lambda e, k=key, w=text, s=saved: self._save_notes(k, w, s))

    def _queue_save(self, key, widget, saved_label):
        if key in self._notes_jobs:
            self.after_cancel(self._notes_jobs[key])
        saved_label.config(text="Editing…")
        self._notes_jobs[key] = self.after(800, lambda: self._save_notes(key, widget, saved_label))

    def _save_notes(self, key, widget, saved_label):
        self._notes_jobs.pop(key, None)
        if not widget.winfo_exists():
            return
        content = widget.get("1.0", "end-1c").strip()
        path = self._notes_paths.get(key) or race_file(self.folder, key)
        if not content and not os.path.exists(path):
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n" if content else "")
            saved_label.config(text="Saved")
        except OSError as e:
            saved_label.config(text=f"Not saved: {e}")
        self.refresh()

    def flush_notes(self):
        """Saves notes still waiting on the typing delay (when leaving the screen)."""
        for key, job in list(self._notes_jobs.items()):
            self.after_cancel(job)
            widget, saved_label = self._notes_widgets[key]
            self._save_notes(key, widget, saved_label)
