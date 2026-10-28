"""Settings: the Gemini key and models, where replay videos are saved, and the race engineer."""
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import theme as t

ENGINEER_DIR = os.path.join(t.racefiles.APP_DIR, "RaceEngineer")
ENGINEER_LAUNCHER_SETTINGS = os.path.join(ENGINEER_DIR, "config.launcher.json")

# (card title, [(config path, label, kind, choices)]) -- kind: entry | choice | persona | bool | int | float
ENGINEER_SETTINGS = [
    ("Race engineer", [
        (("engineer", "persona"), "Persona", "persona", None),
        (("engineer", "emotion_mode"), "Emotion", "choice", ["dynamic", "calm", "urgent"]),
        (("engineer", "speak_only_when_spoken_to"), "Speak only when spoken to", "bool", None),
        (("engineer", "allow_tactical_alerts_when_silent"), "Tactical alerts while silent", "bool", None),
        (("engineer", "volume"), "Volume", "float", None),
        (("engineer", "radio_effect"), "Radio effect", "bool", None),
    ]),
    ("Race engineer - Gemini", [
        (("gemini", "thinking_model"), "Model", "entry", None),
        (("gemini", "thinking_level"), "Thinking level", "choice", ["LOW", "MEDIUM", "HIGH"]),
        (("gemini", "fast_model"), "Fast model (routine calls, blank = Model)", "entry", None),
        (("gemini", "fallback_model"), "Fallback model (blank = none)", "entry", None),
        (("gemini", "temperature"), "Temperature", "float", None),
        (("tts", "model"), "Voice model", "entry", None),
        (("streaming", "enabled"), "Stream replies (starts talking sooner)", "bool", None),
    ]),
    ("Race engineer - push to talk", [
        (("push_to_talk", "mode"), "Push-to-talk", "choice", ["auto", "wheel", "keyboard"]),
        (("push_to_talk", "keyboard_key"), "Keyboard key", "entry", None),
        (("push_to_talk", "joystick_index"), "Wheel index", "int", None),
        (("push_to_talk", "button_index"), "Wheel button", "int", None),
    ]),
    ("Race engineer - strategy", [
        (("strategy", "fuel_margin_liters"), "Fuel margin at the flag (L)", "float", None),
        (("strategy", "estimated_pit_loss_sec"), "Pit loss until measured (s)", "float", None),
        (("strategy", "pit_lap_preference"), "Pit lap in fuel window", "choice", ["latest", "middle", "earliest"]),
    ]),
]


def read_engineer_config():
    """(config.yaml over the example's defaults, the launcher's saved settings)."""
    import yaml  # the engineer needs PyYAML anyway; see RaceEngineer/requirements.txt

    def merge(into, other):
        for key, value in other.items():
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                merge(into[key], value)
            else:
                into[key] = value

    # An older config.yaml can lack settings added since; the example has them all
    base = {}
    for name in ("config.example.yaml", "config.yaml"):
        path = os.path.join(ENGINEER_DIR, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                merge(base, yaml.safe_load(f) or {})
    overrides = {}
    if os.path.exists(ENGINEER_LAUNCHER_SETTINGS):
        with open(ENGINEER_LAUNCHER_SETTINGS, "r", encoding="utf-8") as f:
            overrides = json.load(f) or {}
    return base, overrides


def config_value(config, path):
    for key in path:
        if not isinstance(config, dict) or key not in config:
            return None
        config = config[key]
    return config


class SettingsScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app
        self.vars = {}
        self.engineer_vars = {}

        self.banner = b = t.PhotoCanvas(self, focus=0.25, fade=0.55, height=130,
                                        on_layout=lambda w, h: b.coords(save_win, w - 36, 60))
        b.pack(fill="x")
        b.create_text(36, 60, text="Settings", fill=t.TEXT, font=(t.FONT, 26, "bold"), anchor="w")
        save_win = b.create_window(0, 60, anchor="e", window=t.button(b, "Save settings", self.save, kind="primary"))

        scroll = t.ScrollFrame(self)
        scroll.pack(fill="both", expand=True, padx=(36, 20), pady=(0, 16))
        self.body = tk.Frame(scroll.inner, bg=t.BG)
        self.body.pack(fill="both", expand=True, padx=(0, 16))

        self._ai_card()
        self._folders_card()
        self._engineer_cards()
        self._tools_card()

    # ------------------------------------------------------------------ cards

    def _card(self, title, about=None):
        c = t.card(self.body, padx=22, pady=18)
        c.pack(fill="x", pady=(0, 14))
        t.label(c, title, size=13, weight="bold").grid(row=0, column=0, columnspan=2, sticky="w")
        if about:
            t.label(c, about, size=9, colour=t.MUTED, wraplength=760).grid(row=1, column=0, columnspan=2,
                                                                          sticky="w", pady=(2, 0))
        c.columnconfigure(0, weight=1)
        c.columnconfigure(1, weight=1)
        return c

    def _field(self, card, row, col, label, var, kind="entry", choices=None, width=34, show=None):
        padx = (0, 16) if col == 0 else (0, 0)
        if kind == "bool":
            tk.Checkbutton(card, text=label, variable=var, bg=t.CARD, fg=t.TEXT, activebackground=t.CARD,
                           activeforeground=t.TEXT, selectcolor=t.CARD, font=(t.FONT, 10), bd=0,
                           highlightthickness=0).grid(row=row + 1, column=col, sticky="w", pady=(0, 12), padx=padx)
            return
        t.label(card, label, size=9, colour=t.MUTED).grid(row=row, column=col, sticky="w", pady=(12, 4), padx=padx)
        if kind == "choice":
            w = ttk.Combobox(card, textvariable=var, values=choices, state="readonly", width=width - 4)
        else:
            w = tk.Entry(card, textvariable=var, bg=t.INPUT, fg=t.TEXT, width=width, font=(t.FONT, 10), relief="flat",
                         bd=0, insertbackground=t.TEXT, highlightthickness=1, highlightbackground=t.BORDER,
                         highlightcolor=t.ACCENT, show=show or "")
        w.grid(row=row + 1, column=col, sticky="w", ipady=4, padx=padx)

    def _ai_card(self):
        s = self.app.settings
        c = self._card("Gemini (AI)", "Used by the race engineer and every AI step of the replay. Get a key from "
                                      "Google AI Studio.")
        self.vars["gemini_api_key"] = tk.StringVar(value=s.get("gemini_api_key", ""))
        self.vars["gemini_model"] = tk.StringVar(value=s.get("gemini_model", "gemini-3.8-flash"))
        self.vars["gemini_max_output_tokens"] = tk.StringVar(value=str(s.get("gemini_max_output_tokens", 65536)))
        self.vars["gemini_thinking_level"] = tk.StringVar(value=s.get("gemini_thinking_level", "HIGH"))
        self._field(c, 2, 0, "API key", self.vars["gemini_api_key"], width=50, show="•")
        self._field(c, 2, 1, "Model for the replay steps", self.vars["gemini_model"])
        self._field(c, 4, 0, "Max output tokens", self.vars["gemini_max_output_tokens"], width=14)
        self._field(c, 4, 1, "Thinking level", self.vars["gemini_thinking_level"], "choice",
                    ["HIGH", "MEDIUM", "LOW", "MINIMAL", "NONE"], width=18)

    def _folders_card(self):
        c = self._card("Replay recordings", "The folder your screen recorder (OBS or similar) saves to. The newest "
                                            "videos there are suggested when you pick a race's replay video.")
        self.vars["videos_folder"] = tk.StringVar(value=self.app.videos_folder())
        row = tk.Frame(c, bg=t.CARD)
        row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        tk.Entry(row, textvariable=self.vars["videos_folder"], bg=t.INPUT, fg=t.TEXT, width=70, font=(t.FONT, 10),
                 relief="flat", bd=0, highlightthickness=1, highlightbackground=t.BORDER,
                 highlightcolor=t.ACCENT).pack(side="left", ipady=4)
        t.button(row, "Browse…", self._browse_videos, size=9).pack(side="left", padx=(8, 0))

    def _browse_videos(self):
        path = filedialog.askdirectory(parent=self, initialdir=self.vars["videos_folder"].get())
        if path:
            self.vars["videos_folder"].set(os.path.normpath(path))

    def _engineer_cards(self):
        try:
            base, overrides = read_engineer_config()
        except Exception as e:
            base, overrides = {}, {}
            self.app.toast(f"Could not read the race engineer config: {e}")
        personas = list((base.get("personas") or {}).keys())
        for title, fields in ENGINEER_SETTINGS:
            c = self._card(title)
            for i, (path, label, kind, choices) in enumerate(fields):
                row, col = 2 + (i // 2) * 2, i % 2
                value = config_value(overrides, path)
                if value is None:
                    value = config_value(base, path)
                var = tk.BooleanVar(value=bool(value)) if kind == "bool" else tk.StringVar(value="" if value is None else str(value))
                field_kind = "choice" if kind in ("choice", "persona") else ("bool" if kind == "bool" else "entry")
                self._field(c, row, col, label, var, field_kind, personas if kind == "persona" else choices,
                            width=34 if kind in ("entry", "choice", "persona") else 12)
                self.engineer_vars[path] = (var, kind, label)
        t.label(self.body, "Saved on top of RaceEngineer\\config.yaml. Personas, voices and every other option are "
                           "edited in config.yaml.", size=9, colour=t.DIM).pack(anchor="w", pady=(0, 14))

    def _tools_card(self):
        c = self._card("Tools", "Map the corner names of a track by driving a lap, so the race log and commentary "
                                "can say where things happened.")
        row = tk.Frame(c, bg=t.CARD)
        row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        for sim in ("AMS2", "RaceRoom"):
            t.button(row, f"Corner Mapper - {t.SIM_NAMES[sim]}", lambda s=sim: self.app.launch_corner_mapper(s),
                     size=9).pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------------ saving

    def save(self, quiet=False):
        s = self.app.settings
        try:
            tokens = int(self.vars["gemini_max_output_tokens"].get())
        except ValueError:
            messagebox.showerror("Settings", "Max output tokens must be a whole number.", parent=self)
            return False
        s["gemini_api_key"] = self.vars["gemini_api_key"].get().strip()
        s["gemini_model"] = self.vars["gemini_model"].get().strip()
        s["gemini_max_output_tokens"] = tokens
        s["gemini_thinking_level"] = self.vars["gemini_thinking_level"].get()
        s["videos_folder"] = self.vars["videos_folder"].get().strip()
        if not self.save_engineer():
            return False
        self.app.save_settings()
        if not quiet:
            self.app.toast("Settings saved.")
        return True

    def save_engineer(self):
        """Writes the settings that differ from config.yaml to the engineer's launcher settings file."""
        try:
            base, _ = read_engineer_config()
        except Exception as e:
            messagebox.showerror("Settings", f"Could not read the race engineer config:\n{e}", parent=self)
            return False
        # The sim is no longer set here: the sim buttons on the start screen choose it for each session
        overrides = {}
        for path, (var, kind, label) in self.engineer_vars.items():
            raw = var.get()
            try:
                value = int(raw) if kind == "int" else float(raw) if kind == "float" else raw
            except ValueError:
                messagebox.showerror("Settings", f"Race engineer - '{label}' must be a number.", parent=self)
                return False
            if value == config_value(base, path):
                continue
            section = overrides
            for key in path[:-1]:
                section = section.setdefault(key, {})
            section[path[-1]] = value
        try:
            with open(ENGINEER_LAUNCHER_SETTINGS, "w", encoding="utf-8") as f:
                json.dump(overrides, f, indent=4)
        except OSError as e:
            messagebox.showerror("Settings", f"Failed to save the race engineer settings:\n{e}", parent=self)
            return False
        return True
