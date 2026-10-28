"""One race: a guided path from the race log to the finished replay, one stage at a time."""
import datetime
import os
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
        self.banner = b = t.PhotoCanvas(self, focus=0.35, fade=0.5, height=160, on_layout=self._layout_banner)
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
        self.folder_win = b.create_window(0, 92, anchor="e", window=t.button(
            b, "Open race folder", lambda: os.startfile(self.folder), size=9))

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

    def _notes_editors(self, parent):
        for key, title, about in (
                ("race_notes", "Race notes", "Championship, storylines, rivalries, nicknames, how to pronounce names, "
                                             "who to focus on. Used by every AI step."),
                ("camera_notes", "Camera notes", "Only for the camera director - e.g. 'Focus on car #24 whenever it "
                                                 "is battling for position.'")):
            c = t.card(parent, padx=18, pady=14)
            c.pack(fill="x", pady=5)
            head = tk.Frame(c, bg=t.CARD)
            head.pack(fill="x")
            t.label(head, title, size=11, weight="bold").pack(side="left")
            saved = t.label(head, "", size=8, colour=t.DIM)
            saved.pack(side="right")
            t.label(c, about, size=9, colour=t.MUTED, wraplength=640).pack(anchor="w", pady=(4, 8))
            text = tk.Text(c, height=7 if key == "race_notes" else 4, bg=t.INPUT, fg=t.TEXT, relief="flat", bd=0,
                           font=(t.FONT, 10), wrap="word", padx=10, pady=8, insertbackground=t.TEXT,
                           highlightthickness=1, highlightbackground=t.BORDER, highlightcolor=t.ACCENT, undo=True)
            text.pack(fill="x")
            path = race_file(self.folder, key)
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
        path = race_file(self.folder, key)
        if not content and not os.path.exists(path):
            return
        try:
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
