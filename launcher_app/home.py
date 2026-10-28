"""The start screen (pick a sim) and the live screen shown while the engineer and recorder run.

Both are drawn straight on the darkened background photo.
"""
import os
import time
import tkinter as tk

import racefiles
from . import theme as t

SESSION_TEXT = {
    "Invalid": "Waiting for a session to start in the sim.",
    "Unknown": "Waiting for a session to start in the sim.",
}
BADGE = 210
# The start screen's switches: key -> (setting, label)
SWITCHES = {"engineer": ("start_engineer", "Race engineer"), "recorder": ("start_recorder", "Race recorder")}


class HomeScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app
        self._timer = None
        self.canvas = None
        self.build()

    def build(self):
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        for w in self.winfo_children():
            w.destroy()
        self.canvas = t.PhotoCanvas(self, focus=0.45)
        self.canvas.pack(fill="both", expand=True)
        if self.app.session:
            self._build_live()
        else:
            self._build_pick()

    # ------------------------------------------------------------------ pick a sim

    def _build_pick(self):
        c = self.canvas
        self.items = {}
        if not self.app.settings.get("gemini_api_key"):
            self.items["warn"] = c.create_text(0, 0, text="Add your Gemini API key in Settings first - the race engineer "
                                                          "and the AI steps need it.", fill=t.WARNING, font=(t.FONT, 10))
            self.items["warn_link"] = c.link(0, 0, "Open Settings", lambda: self.app.show("settings"), size=10, bold=True)
        self.items["title"] = c.create_text(0, 0, text="Ready to race?", fill=t.TEXT, font=(t.FONT, 32, "bold"))
        self.items["sub"] = c.create_text(0, 0, text="Pick your sim. Your race engineer starts and every race you drive "
                                                    "is recorded in the background - just go and race.",
                                          fill=t.MUTED, font=(t.FONT, 11), width=540, justify="center")
        self.sims = {}
        for sim in ("AMS2", "RaceRoom"):
            badge = c.create_image(0, 0, image=t.logo_badge(sim, BADGE))
            name = c.create_text(0, 0, text=t.SIM_NAMES[sim], fill=t.TEXT, font=(t.FONT, 13, "bold"))
            hint = c.create_text(0, 0, text="", fill=t.MUTED, font=(t.FONT, 9), state="hidden")
            self.sims[sim] = (badge, name, hint)
            for item in (badge, name):
                c.tag_bind(item, "<Enter>", lambda e, s=sim: self._hover(s, True))
                c.tag_bind(item, "<Leave>", lambda e, s=sim: self._hover(s, False))
                c.tag_bind(item, "<Button-1>", lambda e, s=sim: self.app.start_session(s))
        # What a logo click starts: engineer only, recorder only (e.g. logging a race replay), or both
        for key in SWITCHES:
            item = self.items[key] = c.create_text(0, 0, text="", font=(t.FONT, 10, "bold"))
            c.tag_bind(item, "<Button-1>", lambda e, k=key: self._toggle(k))
            c.tag_bind(item, "<Enter>", lambda e: c.config(cursor="hand2"))
            c.tag_bind(item, "<Leave>", lambda e: c.config(cursor=""))
        self.items["switch_help"] = c.create_text(0, 0, text="", fill=t.DIM, font=(t.FONT, 9))
        self._show_switches()
        self.items["line"] = c.create_line(0, 0, 0, 0, fill=t.BORDER_STRONG)
        count = len(racefiles.list_races())
        self.items["count"] = c.create_text(0, 0, text=(f"{count} recorded race{'s' if count != 1 else ''}" if count
                                                        else "No recorded races yet"), fill=t.DIM, font=(t.FONT, 9))
        self.items["link"] = c.link(0, 0, "Make a replay from a recorded race  →", lambda: self.app.show("library"),
                                    size=12, bold=True)
        c.on_layout = self._layout_pick

    def _on(self, key):
        return self.app.settings.get(SWITCHES[key][0], True)

    def _toggle(self, key):
        other = "recorder" if key == "engineer" else "engineer"
        if self._on(key) and not self._on(other):
            # Never both off: switching this one off turns the other one on
            self.app.settings[SWITCHES[other][0]] = True
        self.app.settings[SWITCHES[key][0]] = not self._on(key)
        self.app.save_settings()
        self._show_switches()

    def _show_switches(self):
        c = self.canvas
        for key, (_, name) in SWITCHES.items():
            on = self._on(key)
            c.itemconfigure(self.items[key], text=f"{name}:  {'● ON' if on else '○ OFF'}",
                            fill=t.SUCCESS if on else t.MUTED)
        engineer, recorder = self._on("engineer"), self._on("recorder")
        if engineer and recorder:
            help_text = "Click a switch to start only one - e.g. just the engineer, or just record a replay"
            sub = "Pick your sim. Your race engineer starts and every race you drive is recorded in the background - just go and race."
            hint = "Start engineer + recorder"
        elif recorder:
            help_text = "Recorder only - good for logging a race replay"
            sub = "Pick your sim. Every race - driven live or played back as a replay - is recorded in the background."
            hint = "Start the recorder only"
        else:
            help_text = "Engineer only - your races are not saved for replays"
            sub = "Pick your sim. Your race engineer starts and talks to you over the radio - just go and race."
            hint = "Start the engineer only"
        c.itemconfigure(self.items["switch_help"], text=help_text)
        c.itemconfigure(self.items["sub"], text=sub)
        for _, _, hint_item in self.sims.values():
            c.itemconfigure(hint_item, text=hint)

    def _hover(self, sim, on):
        badge, name, hint = self.sims[sim]
        self.canvas.itemconfigure(badge, image=t.logo_badge(sim, BADGE, ring=t.ACCENT if on else None))
        self.canvas.itemconfigure(name, fill="#ff6a5f" if on else t.TEXT)
        self.canvas.itemconfigure(hint, state="normal" if on else "hidden")
        self.canvas.config(cursor="hand2" if on else "")

    def _layout_pick(self, w, h):
        c = self.canvas
        cx, cy = w / 2, max(h * 0.47, 330)
        if "warn" in self.items:
            c.coords(self.items["warn"], cx - 50, cy - 290)
            c.coords(self.items["warn_link"], cx + 290, cy - 290)
        c.coords(self.items["title"], cx, cy - 225)
        c.coords(self.items["sub"], cx, cy - 172)
        for i, sim in enumerate(("AMS2", "RaceRoom")):
            badge, name, hint = self.sims[sim]
            x = cx + (-1 if i == 0 else 1) * 175
            c.coords(badge, x, cy)
            c.coords(name, x, cy + BADGE / 2 + 24)
            c.coords(hint, x, cy + BADGE / 2 + 46)
        c.coords(self.items["engineer"], cx - 110, cy + 192)
        c.coords(self.items["recorder"], cx + 110, cy + 192)
        c.coords(self.items["switch_help"], cx, cy + 213)
        c.coords(self.items["line"], cx - 280, cy + 240, cx + 280, cy + 240)
        c.coords(self.items["count"], cx, cy + 262)
        c.coords(self.items["link"], cx, cy + 288)

    # ------------------------------------------------------------------ live session

    def _build_live(self):
        s = self.app.session
        c = self.canvas
        self.items = {
            "badge": c.create_image(0, 0, image=t.logo_badge(s["sim"], 104), anchor="w"),
            "kicker": c.create_text(0, 0, text="LIVE SESSION", fill="#ff6a5f", font=(t.FONT, 9, "bold"), anchor="w"),
            "sim": c.create_text(0, 0, text=t.SIM_NAMES[s["sim"]], fill=t.TEXT, font=(t.FONT, 26, "bold"), anchor="w"),
            "hint": c.create_text(0, 0, text=(
                "When you're done racing you can simply close BAD AI - everything stops and every finished race is "
                "saved. Races shorter than 5 minutes are not kept." if s.get("logger") else
                "When you're done racing you can simply close BAD AI - the engineer stops by itself."),
                fill=t.MUTED, font=(t.FONT, 9), anchor="nw"),
        }

        self.status_card = t.card(c, padx=24, pady=20)
        self.pill_holder = tk.Frame(self.status_card, bg=t.CARD)
        self.pill_holder.pack(anchor="w")
        self.headline = t.label(self.status_card, "", size=18, weight="bold")
        self.headline.pack(anchor="w", pady=(10, 2))
        self.detail = t.label(self.status_card, "", size=11, colour=t.MUTED)
        self.detail.pack(anchor="w")

        self.service_cards, self.service_labels = {}, {}
        for key, name in (("engineer", "Race engineer"), ("logger", "Race recorder")):
            card = t.card(c, padx=16, pady=12)
            t.label(card, name, size=9, colour=t.DIM).pack(anchor="w")
            lab = t.label(card, "", size=11, weight="bold")
            lab.pack(anchor="w")
            t.button(card, "Show its output", lambda k=key: self.app.show_activity(self.app.session and self.app.session[k]),
                     kind="link", size=9, padx=0, pady=0).pack(anchor="w", pady=(4, 0))
            self.service_cards[key] = card
            self.service_labels[key] = lab

        self.saved_card = t.card(c, padx=18, pady=12)
        self.stop_btn = t.button(c, "■  Stop session", self.app.stop_session, kind="danger", size=11, padx=18, pady=8)
        self.mapper_btn = t.button(c, "Corner Mapper", lambda: self.app.launch_corner_mapper(s["sim"]), size=10)
        self.windows = {name: c.create_window(0, 0, window=w, anchor="nw") for name, w in (
            ("status", self.status_card), ("engineer", self.service_cards["engineer"]),
            ("logger", self.service_cards["logger"]), ("saved", self.saved_card),
            ("stop", self.stop_btn), ("mapper", self.mapper_btn))}
        c.on_layout = self._layout_live
        self.refresh()

    def _layout_live(self, w=None, h=None):
        c = self.canvas
        w = w or c.winfo_width()
        if w < 300:
            return  # not drawn yet; the canvas lays itself out once it has a size
        width = min(880, w - 120)
        x = (w - width) / 2
        y = 48
        c.coords(self.items["badge"], x, y + 52)
        c.coords(self.items["kicker"], x + 128, y + 30)
        c.coords(self.items["sim"], x + 128, y + 62)
        y += 130
        self.update_idletasks()
        c.coords(self.windows["status"], x, y)
        c.itemconfigure(self.windows["status"], width=width)
        self.detail.config(wraplength=width - 60)
        y += self.status_card.winfo_reqheight() + 12
        half = (width - 12) / 2
        for i, key in enumerate(("engineer", "logger")):
            c.coords(self.windows[key], x + i * (half + 12), y)
            c.itemconfigure(self.windows[key], width=half)
        y += self.service_cards["engineer"].winfo_reqheight() + 12
        if self.saved_card.winfo_children():
            c.itemconfigure(self.windows["saved"], state="normal", width=width)
            c.coords(self.windows["saved"], x, y)
            y += self.saved_card.winfo_reqheight() + 12
        else:
            c.itemconfigure(self.windows["saved"], state="hidden")
        y += 10
        c.coords(self.windows["stop"], x, y)
        c.coords(self.windows["mapper"], x + width - self.mapper_btn.winfo_reqwidth(), y + 4)
        y += self.stop_btn.winfo_reqheight() + 18
        c.coords(self.items["hint"], x, y)
        c.itemconfigure(self.items["hint"], width=width)

    def refresh(self):
        s = self.app.session
        if not s or not hasattr(self, "headline") or not self.headline.winfo_exists():
            return
        for w in self.pill_holder.winfo_children():
            w.destroy()
        state = s.get("state")
        if s.get("stopping"):
            t.pill(self.pill_holder, "STOPPING", t.MUTED, t.NEUTRAL_SOFT).pack(side="left")
            self.headline.config(text="Saving and stopping…")
            self.detail.config(text="Finishing the race log and shutting the engineer down.")
        elif not s.get("logger"):
            t.pill(self.pill_holder, "ENGINEER ONLY", t.SUCCESS, t.SUCCESS_SOFT).pack(side="left")
            self.headline.config(text="Your race engineer is on the radio")
            self.detail.config(text=f"Start {t.SIM_NAMES[s['sim']]} and go racing. Hold Caps Lock (or your wheel "
                                    "button) to talk to your engineer. The race recorder is off, so races are not saved "
                                    "for replays.")
        elif state == "recording":
            t.pill(self.pill_holder, "●  RECORDING", "#ffffff", t.ACCENT).pack(side="left")
            self.headline.config(text=f"Race at {t.pretty_track(s.get('track'))}")
            self.detail.config(text=f"Recording for {t.format_duration(time.time() - s.get('rec_started', time.time()))}"
                                    "  ·  it is saved when the race ends.")
        else:
            t.pill(self.pill_holder, "WAITING", t.MUTED, t.NEUTRAL_SOFT).pack(side="left")
            if s["sim"] == "AMS2":
                how = "Join a race, or play back a race replay from the start."
            else:
                how = "Join a race (RaceRoom replays share no driver data, so record RaceRoom races live)."
            session = s.get("session") or "Unknown"
            if state == "waiting_sim" or session in SESSION_TEXT:
                self.headline.config(text=f"Waiting for {t.SIM_NAMES[s['sim']]}")
                self.detail.config(text=f"Start the sim. {how} Practice and qualifying are not recorded - "
                                        "recording starts by itself when the race does.")
            else:
                self.headline.config(text=f"{session} - not recorded")
                self.detail.config(text="Recording starts by itself when a race session begins.")

        for key, lab in self.service_labels.items():
            proc = s.get(key)
            if proc and proc.running:
                lab.config(text="●  Running", fg=t.SUCCESS)
            elif proc:
                lab.config(text=f"●  {proc.state.capitalize()} - see its output", fg="#ff6a5f")
            else:
                lab.config(text=s.get(f"{key}_off") or "Off", fg=t.DIM)

        notes = s.get("notes", [])
        shown = getattr(self, "_shown_notes", None)
        if shown != len(notes):
            self._shown_notes = len(notes)
            for w in self.saved_card.winfo_children():
                w.destroy()
            if notes:
                t.label(self.saved_card, "THIS SESSION", size=8, weight="bold", colour=t.DIM).pack(anchor="w", pady=(0, 4))
            for kind, text, folder in notes[-4:]:
                row = tk.Frame(self.saved_card, bg=t.CARD)
                row.pack(fill="x", pady=1)
                t.label(row, "✓" if kind == "saved" else "–", size=10,
                        colour=t.SUCCESS if kind == "saved" else t.DIM, width=2).pack(side="left")
                t.label(row, text, size=10, colour=t.TEXT if kind == "saved" else t.DIM).pack(side="left")
                if folder and os.path.isdir(folder):
                    t.button(row, "Make replay →", lambda f=folder: self.app.open_race(f), kind="link", size=9,
                             padx=6, pady=0).pack(side="left")
        self._layout_live()

        if self._timer:
            self.after_cancel(self._timer)
        self._timer = self.after(1000, self.refresh)
