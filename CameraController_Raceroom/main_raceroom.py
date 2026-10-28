import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json
import sys
import os
import threading
import time
from datetime import datetime, timedelta
from pynput.keyboard import Key, Listener
import pydirectinput
import pygetwindow as gw
from PIL import Image, ImageChops, ImageGrab, ImageStat

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR) # camera_gemini.py
sys.path.insert(0, os.path.join(APP_DIR, "Logger_Raceroom")) # Shared memory reader lives with the RaceRoom logger
from r3e_shared import R3ESharedMemory, decode
import camera_gemini

# Disable pydirectinput's safety pause and failsafe
pydirectinput.PAUSE = 0.01
pydirectinput.FAILSAFE = False

# --- RaceRoom's replay bar, in pixels of a 1920x1080 game window (scaled to the real window size) ---
# The Driver Ahead/Behind keys (Numpad +/-) do nothing in 3.6 replays, and the camera keys step through
# every camera in between, so everything is driven through the bar's own controls instead.
BAR_Y = 1018
DRIVER_AHEAD_X = 512 # ">" next to the driver name
DRIVER_BEHIND_X = 271 # "<"
CAMERA_MENU_X = 736 # The camera dropdown's arrow button
CAMERA_ITEM_X = 640
PLAY_BUTTON_X = 960
# The bar's rewind buttons, compared with a saved picture of them to tell whether the bar is showing
# (a plain darkness check is fooled by dark cockpit views). Measured: 0-2 when shown, 23+ when hidden.
BAR_BUTTONS_BOX = (840, 998, 936, 1038)
BAR_BUTTONS_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replay_bar_buttons.png")
BAR_MATCH_LIMIT = 10
CAMERA_ARROW_PROBE = (726, 1008, 746, 1028) # Points down when the menu is closed, up when open
PLAY_ICON_PROBE = (945, 1003, 975, 1033) # White triangle when paused, two bars when playing

# The camera dropdown lists every camera at a fixed height (the list grows upwards from the bar).
# "inside" cameras are the ones in or on the car; the director never cuts straight between two of them.
CAMERAS = {
    "TV": (977, "external"),
    "Action": (947, "external"),
    "Aerial": (917, "external"),
    "Static": (887, "external"),
    "Chase": (831, "external"),
    "Cockpit": (801, "inside"),
    "Hood": (771, "inside"),
    "Nose": (741, "inside"),
    **{f"Onboard {n}": (568 - 30 * (n - 1), "inside") for n in range(1, 13)},
}
DEFAULT_ENABLED = ["TV", "Aerial", "Onboard 1", "Onboard 2", "Cockpit"]
MAX_CARS = 128 # Safety stop when clicking round the driver list
ARROW_CLICK_GAP = 0.03 # RaceRoom takes every arrow click even 0.02s apart, so a switch is one quick burst

# RaceRoom's camera hotkeys (F1 in a replay), as measured:
# - External (Page Down) comes back to the last external camera; End steps TV -> Action -> Aerial -> Static -> TV
#   and only works while an external camera is showing.
# - Onboard (Home) comes back to the last onboard camera, and each further press steps to the next one.
# - Driving (Insert) always starts on Hood, and each further press steps Hood -> Nose -> Chase -> Cockpit.
# The game never reports which camera is live, so the controller keeps count; a menu pick sets the same memory.
EXTERNAL_CYCLE = ["TV", "Action", "Aerial", "Static"]
DRIVING_CYCLE = ["Hood", "Nose", "Chase", "Cockpit"]
DEFAULT_CAMERA_KEYS = {"external": "pagedown", "cycle_external": "end", "onboard": "home", "driving": "insert"}
# Seconds per key down / up. RaceRoom registers presses well under one 60 fps frame apart (measured: 6 ms
# reliable, 3 ms drops presses; 7 ms was 80/80), so an in-between camera exists for ~14 ms: one frame at most.
HOTKEY_TAP = 0.007


def precise_wait(seconds):
    """time.sleep is only accurate to ~15 ms on Windows, too coarse for HOTKEY_TAP."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


def camera_group(camera):
    if camera in EXTERNAL_CYCLE:
        return "external"
    if camera in DRIVING_CYCLE:
        return "driving"
    if camera.startswith("Onboard "):
        return "onboard"
    return None
FALLBACK_CAMERA = "TV"

KEY_NAMES = {
    Key.ctrl: "ctrl", Key.alt: "alt", Key.shift: "shift",
    Key.enter: "enter", Key.space: "space", Key.tab: "tab",
    Key.esc: "esc", Key.backspace: "backspace", Key.delete: "delete",
    Key.up: "up", Key.down: "down", Key.left: "left", Key.right: "right",
    Key.page_up: "pageup", Key.page_down: "pagedown",
    Key.home: "home", Key.end: "end", Key.insert: "insert",
    Key.f1: "f1", Key.f2: "f2", Key.f3: "f3", Key.f4: "f4",
    Key.f5: "f5", Key.f6: "f6", Key.f7: "f7", Key.f8: "f8",
    Key.f9: "f9", Key.f10: "f10", Key.f11: "f11", Key.f12: "f12",
}


def key_to_str(key):
    if key in KEY_NAMES:
        return KEY_NAMES[key]
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    return str(key).replace("Key.", "").lower()


def record_key(on_done):
    """Captures the next key (or combination, e.g. ctrl+f1) pressed anywhere and passes it to on_done."""
    held = []

    def on_press(key):
        name = key_to_str(key)
        if name not in held:
            held.append(name)

    def on_release(key):
        if held:
            on_done("+".join(held))
        return False

    Listener(on_press=on_press, on_release=on_release, suppress=True).start()


def is_inside(camera):
    return CAMERAS.get(camera, (0, "external"))[1] == "inside"


def item_camera(item):
    """Camera name of a sequence entry ("camera", or "label" from older sequence files)."""
    return item.get("camera") or item.get("label")


# A driver change on the same external camera looks like a jump (the game snaps between cars), so swap it
SAME_CAMERA_SWAP = {"TV": "Aerial", "Aerial": "TV", "Action": "TV", "Static": "TV", "Chase": "TV"}


def enforce_camera_rules(sequence):
    """Applies the cutting rules to a sequence:
    - never cut straight from one inside camera to another (onboard, cockpit...): go external first;
    - never change driver on the same external camera (TV -> TV jumps between cars).

    Returns the fixed sequence and a list of the changes made.
    """
    fixed, changes = [], []
    previous, previous_driver = None, None
    for item in sequence:
        item = dict(item)
        camera = item_camera(item)
        driver = item.get("driver")
        if camera and camera not in CAMERAS:
            changes.append(f"{item.get('timecode')}: unknown camera '{camera}' -> {FALLBACK_CAMERA}")
            camera = FALLBACK_CAMERA
        if camera and previous and is_inside(previous) and is_inside(camera):
            if driver == previous_driver:
                # Same car: stay on the inside camera already showing rather than cutting out and back
                changes.append(f"{item.get('timecode')}: {previous} -> {camera} is inside to inside -> stays on {previous}")
                continue
            changes.append(f"{item.get('timecode')}: {previous} -> {camera} is inside to inside -> {FALLBACK_CAMERA}")
            camera = FALLBACK_CAMERA
        if camera and camera == previous and driver != previous_driver and camera in SAME_CAMERA_SWAP:
            swap = SAME_CAMERA_SWAP[camera]
            changes.append(f"{item.get('timecode')}: {camera} -> {camera} on a new driver -> {swap}")
            camera = swap
        if camera:
            item["camera"] = camera
            item.pop("label", None)
            item.pop("category", None)
            previous = camera
        previous_driver = driver
        fixed.append(item)
    return fixed, changes


def load_race_timing(log_path):
    """Running order through the race from the logger's <log>_timing.jsonl: [(log time, [names by place])].

    The ">" driver arrow follows this order, so a switch can be counted out in clicks. Retired and
    disqualified cars are left out, as RaceRoom leaves them out of the arrow cycle.
    """
    if not log_path:
        return []
    base = os.path.splitext(log_path)[0]
    path = base + "_timing.jsonl"
    if not os.path.exists(path): # A combined log: use the newest timing file in the project's Logs folder
        logs = os.path.join(os.path.dirname(log_path), "Logs")
        files = [os.path.join(logs, f) for f in os.listdir(logs) if f.endswith("_timing.jsonl")] if os.path.isdir(logs) else []
        if not files:
            return []
        path = max(files, key=os.path.getmtime)
    snapshots = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                if record["type"] == "session":
                    snapshots = [] # The clock restarts; the race is the last session in the file
                elif record["type"] == "timing":
                    rows = sorted(record["rows"], key=lambda r: r[1])
                    snapshots.append((record["t"], [r[0] for r in rows if r[12] in (0, 1)]))
    except (OSError, ValueError, KeyError, IndexError):
        return []
    return snapshots


class RaceroomCameraController:
    def __init__(self, root):
        self.root = root
        self.root.title("RaceRoom Camera Controller")
        self.root.geometry("400x1000")
        self.root.attributes("-topmost", True)

        self.config_file = "config_raceroom.json"
        self.data = self.load_config()
        self.shm = R3ESharedMemory()

        self.target_window_name = tk.StringVar()
        self.status_msg = tk.StringVar(value="Ready")
        self.camera_display = tk.StringVar(value="Camera: (unknown)")

        self.sequence_data = None
        self.is_playing_sequence = False
        self.logic_lock = threading.RLock()

        # Playback Timing
        self.playback_start_time = datetime.now()
        self.time_offset = tk.DoubleVar(value=0.0)
        self.timer_display = tk.StringVar(value="00:00:00")

        self.current_driver = None
        self.current_camera = None
        self.camera_memory = {"external": None, "onboard": None} # What Page Down / Home would come back to
        self.driver_ring = [] # The replay's driver list in "driver ahead" arrow order: [{slot, name, car, num}]
        self.race_timing = [] # [(log time, running order by place)] from the logger's timing file
        self.is_paused = False
        self.pause_start_time = None
        self.ignore_space_until = 0.0 # Our own simulated Space presses must not toggle the pause

        self.session_log_filename = None
        self.bar_buttons = None # Reference picture of the replay bar, loaded on first use

        # Pre-loaded input file from launcher
        self.preloaded_input_file = None
        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            self.preloaded_input_file = sys.argv[1]
        elif os.environ.get("R3E_INPUT_FILE") and os.path.exists(os.environ.get("R3E_INPUT_FILE")):
            self.preloaded_input_file = os.environ.get("R3E_INPUT_FILE")

        self.setup_ui()
        self.refresh_windows()

        # Global listener for Space (Pause)
        self.global_listener = Listener(on_press=self._on_global_key)
        self.global_listener.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _on_global_key(self, key):
        if key == Key.space and time.time() > self.ignore_space_until:
            if self.is_playing_sequence:
                self.root.after(0, self.toggle_pause)

    def toggle_pause(self):
        if not self.is_playing_sequence: return

        self.is_paused = not self.is_paused
        if self.is_paused:
            self.pause_start_time = datetime.now()
            self.log_message("Paused")
            self.status_msg.set("PAUSED (Space to Resume)")
        else:
            if self.pause_start_time:
                pause_duration = datetime.now() - self.pause_start_time
                self.playback_start_time += pause_duration
            self.log_message("Resumed")
            self.status_msg.set("Running...")

    def on_closing(self):
        self.is_playing_sequence = False
        if hasattr(self, 'global_listener'):
            self.global_listener.stop()
        self.shm.close()
        self.root.destroy()

    def load_config(self):
        defaults = {
            "settings": {
                "hold_time": 0.05,
                "step_gap": 0.15,
                "hide_ui_key": "h",
                "hide_replay_ui": True,
                "manual_fallback": False,
                "camera_method": "hotkeys",
                "camera_keys": dict(DEFAULT_CAMERA_KEYS),
                "gemini_model": camera_gemini.DEFAULT_MODEL,
                "gemini_thinking_level": camera_gemini.DEFAULT_THINKING,
                "api_key": ""
            },
            "cameras": {name: name in DEFAULT_ENABLED for name in CAMERAS},
        }
        cfg = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except: pass
        settings = {**defaults["settings"], **cfg.get("settings", {})}
        settings["camera_keys"] = {**DEFAULT_CAMERA_KEYS, **settings.get("camera_keys", {})}
        for old in ("category_wait", "car_ahead_key", "car_behind_key"):
            settings.pop(old, None)
        cameras = {**defaults["cameras"], **cfg.get("cameras", {})}
        return {"settings": settings, "cameras": {k: v for k, v in cameras.items() if k in CAMERAS}}

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    def enabled_cameras(self):
        return [name for name in CAMERAS if self.data["cameras"].get(name)]

    def setup_ui(self):
        self.main_frame = ttk.Frame(self.root, padding="5")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # Window Selection
        win_f = ttk.Frame(self.main_frame); win_f.pack(fill=tk.X, pady=2)
        ttk.Label(win_f, text="Window:").pack(side=tk.LEFT, padx=2)
        self.win_combo = ttk.Combobox(win_f, textvariable=self.target_window_name, state="readonly")
        self.win_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(win_f, text="R", width=2, command=self.refresh_windows).pack(side=tk.RIGHT)

        # Timer Display
        timer_f = ttk.Frame(self.main_frame); timer_f.pack(fill=tk.X, pady=10)
        tk.Label(timer_f, textvariable=self.timer_display, font=("Consolas", 32, "bold"), bg="#111", fg="#0f0").pack(side=tk.LEFT, expand=True, fill=tk.X)

        # Offset Control
        offset_f = ttk.Frame(self.main_frame); offset_f.pack(fill=tk.X, pady=5)
        ttk.Label(offset_f, text="Time Offset (s):").pack(side=tk.LEFT, padx=2)
        ttk.Entry(offset_f, textvariable=self.time_offset, width=10).pack(side=tk.LEFT, padx=2)

        # Settings (simplified)
        set_f = ttk.LabelFrame(self.main_frame, text="Settings")
        set_f.pack(fill=tk.X, pady=5)

        row1 = ttk.Frame(set_f); row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Hold Time:").pack(side=tk.LEFT, padx=2)
        self.hold_ent = ttk.Entry(row1, width=8)
        self.hold_ent.insert(0, str(self.data["settings"]["hold_time"]))
        self.hold_ent.pack(side=tk.LEFT, padx=2)

        ttk.Label(row1, text="Gap:").pack(side=tk.LEFT, padx=2)
        self.gap_ent = ttk.Entry(row1, width=8)
        self.gap_ent.insert(0, str(self.data["settings"]["step_gap"]))
        self.gap_ent.pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Save Settings", command=self.update_settings).pack(side=tk.RIGHT, padx=2)

        # Replay control (driver + camera)
        drv_f = ttk.LabelFrame(self.main_frame, text="Replay Control")
        drv_f.pack(fill=tk.X, pady=5)

        ttk.Label(drv_f, text="Uses the replay bar's driver arrows and camera menu.",
                  font=("Helvetica", 8)).pack(anchor="w", padx=2)
        row = ttk.Frame(drv_f); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Hide UI Key:").pack(side=tk.LEFT, padx=2)
        self.hide_key_btn = ttk.Button(row, text=self.data["settings"]["hide_ui_key"], width=12,
                                       command=self.record_hide_ui_key)
        self.hide_key_btn.pack(side=tk.RIGHT, padx=2)

        self.hide_ui_var = tk.BooleanVar(value=self.data["settings"]["hide_replay_ui"])
        ttk.Checkbutton(drv_f, text="Hide the replay bar again after switching", variable=self.hide_ui_var,
                        command=lambda: self._save_setting("hide_replay_ui", self.hide_ui_var.get())).pack(anchor="w", padx=2)
        self.manual_fallback_var = tk.BooleanVar(value=self.data["settings"]["manual_fallback"])
        ttk.Checkbutton(drv_f, text="Pause for manual pick if auto-switch fails", variable=self.manual_fallback_var,
                        command=lambda: self._save_setting("manual_fallback", self.manual_fallback_var.get())).pack(anchor="w", padx=2)

        drv_btn_f = ttk.Frame(drv_f); drv_btn_f.pack(fill=tk.X, pady=2)
        ttk.Button(drv_btn_f, text="Check Focused Car", command=self.check_focus).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(drv_btn_f, text="Learn Drivers", command=self.learn_drivers_prompt).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(drv_btn_f, text="Go To Driver...", command=self.go_to_driver_prompt).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=2)

        # Cameras
        self.cam_f = ttk.LabelFrame(self.main_frame, text="Cameras")
        self.cam_f.pack(fill=tk.X, pady=5)
        self.render_cameras()

        # Quick Sync
        sync_f = ttk.LabelFrame(self.main_frame, text="Sync Tools")
        sync_f.pack(fill=tk.X, pady=5)

        self.log_gf_time = tk.StringVar(value="00:00:06")
        self.sim_gf_time = tk.StringVar(value="00:00:00")

        sf1 = ttk.Frame(sync_f); sf1.pack(fill=tk.X, pady=2)
        ttk.Label(sf1, text="Log Green Flag:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(sf1, textvariable=self.log_gf_time, width=10).pack(side=tk.RIGHT, padx=2)

        sf2 = ttk.Frame(sync_f); sf2.pack(fill=tk.X, pady=2)
        ttk.Label(sf2, text="Sim Green Flag:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(sf2, textvariable=self.sim_gf_time, width=10).pack(side=tk.RIGHT, padx=2)

        sf3 = ttk.Frame(sync_f); sf3.pack(fill=tk.X, pady=2)
        ttk.Button(sf3, text="Capture Sim GF", command=self._set_sim_gf_now).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(sf3, text="Apply Sync", command=self._calculate_and_set_offset).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=2)

        # Race Log Input (pre-loaded from launcher)
        input_f = ttk.LabelFrame(self.main_frame, text="Race Log Input")
        input_f.pack(fill=tk.X, pady=5)

        self.input_file_display = tk.StringVar()
        if self.preloaded_input_file:
            self.input_file_display.set(os.path.basename(self.preloaded_input_file))
        else:
            self.input_file_display.set("(no file loaded)")

        inp_row = ttk.Frame(input_f); inp_row.pack(fill=tk.X, pady=2)
        input_lbl = tk.Label(inp_row, textvariable=self.input_file_display,
                             font=("Consolas", 9, "bold"),
                             bg="#1a2e1a" if self.preloaded_input_file else "#2e1a1a",
                             fg="#0f0" if self.preloaded_input_file else "#f55",
                             anchor="w", padx=5, pady=3)
        input_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.input_status_label = input_lbl

        ttk.Button(inp_row, text="Change", command=self._browse_input_file).pack(side=tk.RIGHT, padx=2)

        # Sequence Controls
        seq_f = ttk.LabelFrame(self.main_frame, text="Sequence")
        seq_f.pack(fill=tk.X, pady=5)

        camera_gemini.add_model_settings(seq_f, self.data["settings"], self.save_config)

        btn_f = ttk.Frame(seq_f); btn_f.pack(fill=tk.X, pady=2)
        ttk.Button(btn_f, text="Load JSON", command=self.load_sequence_file).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_f, text="Generate (Gemini)", command=self.generate_sequence_with_gemini).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=2)

        self.play_btn = ttk.Button(seq_f, text="Play Sequence", command=self.start_sequence_playback, state="disabled")
        self.play_btn.pack(fill=tk.X, pady=5, padx=2)

        # Status and Log
        self.status_lbl = tk.Label(self.main_frame, textvariable=self.status_msg, font=("Helvetica", 9), bg="#222", fg="#0f0", anchor="w")
        self.status_lbl.pack(fill=tk.X, pady=2)

        log_f = ttk.LabelFrame(self.main_frame, text="Activity Log")
        log_f.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = tk.Text(log_f, height=10, font=("Consolas", 8), bg="#1e1e1e", fg="#00ff00", state="disabled")
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def update_settings(self):
        try:
            self.data["settings"]["hold_time"] = float(self.hold_ent.get())
            self.data["settings"]["step_gap"] = float(self.gap_ent.get())
            self.save_config()
            messagebox.showinfo("Success", "Settings updated")
        except:
            messagebox.showerror("Error", "Invalid numeric values")

    def _save_setting(self, key, value):
        self.data["settings"][key] = value
        self.save_config()

    def record_hide_ui_key(self):
        self.hide_key_btn.config(text="Press a key...")

        def done(name):
            self._save_setting("hide_ui_key", name)
            self.root.after(0, lambda: self.hide_key_btn.config(text=name))
        record_key(done)

    def refresh_windows(self):
        titles = [w.title for w in gw.getAllWindows() if w.title]
        self.win_combo['values'] = sorted(titles)
        for t in titles:
            if "raceroom racing experience" in t.lower(): self.target_window_name.set(t); break

    def log_message(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._append_to_log, f"[{now}] {msg}\n")

    def _append_to_log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ------------------------------------------------------------------ camera panel

    def render_cameras(self):
        for w in self.cam_f.winfo_children():
            w.destroy()
        ttk.Label(self.cam_f, textvariable=self.camera_display, font=("Helvetica", 9, "bold")).pack(anchor="w", padx=2)
        for kind, title in (("external", "External:"), ("inside", "Inside:")):
            row = ttk.Frame(self.cam_f); row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=title, width=9).pack(side=tk.LEFT, padx=2)
            for name in self.enabled_cameras():
                if CAMERAS[name][1] == kind:
                    ttk.Button(row, text=name, command=lambda n=name: self.select_camera(n)).pack(side=tk.LEFT, padx=1)
        method_row = ttk.Frame(self.cam_f); method_row.pack(fill=tk.X, pady=2)
        ttk.Label(method_row, text="Switch with:").pack(side=tk.LEFT, padx=2)
        self.camera_method_var = tk.StringVar(value=self.data["settings"]["camera_method"])
        for value, text in (("hotkeys", "Hotkeys"), ("menu", "Replay menu")):
            ttk.Radiobutton(method_row, text=text, value=value, variable=self.camera_method_var,
                            command=lambda: self._save_setting("camera_method", self.camera_method_var.get())).pack(side=tk.LEFT, padx=2)
        btn_row = ttk.Frame(self.cam_f); btn_row.pack(fill=tk.X, pady=2)
        ttk.Button(btn_row, text="Sync Cameras", command=self.sync_cameras_prompt).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_row, text="Choose Cameras...", command=self.open_camera_chooser).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=2)

    def open_camera_chooser(self):
        win = tk.Toplevel(self.root)
        win.title("Cameras")
        win.attributes("-topmost", True)
        body = ttk.Frame(win, padding=8); body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Ticked cameras are offered to Gemini and shown as buttons.\n"
                             "The director never cuts straight between two inside cameras.",
                  wraplength=320).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        for col, (kind, title) in enumerate((("external", "External"), ("inside", "Inside (on / in the car)"))):
            frame = ttk.LabelFrame(body, text=title); frame.grid(row=1, column=col, sticky="n", padx=4)
            for name, (_, k) in CAMERAS.items():
                if k != kind:
                    continue
                var = tk.BooleanVar(value=self.data["cameras"].get(name, False))
                ttk.Checkbutton(frame, text=name, variable=var,
                                command=lambda n=name, v=var: self._toggle_camera(n, v.get())).pack(anchor="w")

    def _toggle_camera(self, name, enabled):
        self.data["cameras"][name] = enabled
        self.save_config()
        self.render_cameras()

    def select_camera(self, name):
        threading.Thread(target=self._apply_shot, args=(None, None, name), daemon=True).start()

    # ------------------------------------------------------------------ screen helpers

    def _game_window_rect(self):
        target = self.target_window_name.get()
        wins = gw.getWindowsWithTitle(target) if target else []
        if not wins:
            return None
        w = wins[0]
        return w.left, w.top, w.width, w.height

    @staticmethod
    def _to_screen(rect, x, y):
        left, top, width, height = rect
        return int(left + x * width / 1920), int(top + y * height / 1080)

    def _grab(self, rect, box):
        """Screenshot of a box given in 1920x1080 game pixels, resized back to that size."""
        x0, y0 = self._to_screen(rect, box[0], box[1])
        x1, y1 = self._to_screen(rect, box[2], box[3])
        img = ImageGrab.grab((x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)), all_screens=True)
        return img.resize((box[2] - box[0], box[3] - box[1])).convert("L")

    def _click(self, rect, x, y):
        pydirectinput.click(*self._to_screen(rect, x, y))

    def _replay_bar_visible(self, rect):
        if self.bar_buttons is None:
            self.bar_buttons = Image.open(BAR_BUTTONS_IMAGE).convert("L")
        diff = ImageChops.difference(self._grab(rect, BAR_BUTTONS_BOX), self.bar_buttons)
        return ImageStat.Stat(diff).mean[0] < BAR_MATCH_LIMIT

    def _set_replay_bar(self, rect, visible):
        """Shows or hides the replay bar with the Hide UI key, polling the screen until it has changed."""
        for _ in range(2):
            if self._replay_bar_visible(rect) == visible:
                return True
            self._send_keys(self.data["settings"]["hide_ui_key"])
            deadline = time.time() + 0.6
            while time.time() < deadline:
                time.sleep(0.05)
                if self._replay_bar_visible(rect) == visible:
                    time.sleep(0.05) # Let the bar finish fading in before clicking
                    return True
        return self._replay_bar_visible(rect) == visible

    def _camera_menu_open(self, rect):
        img = self._grab(rect, CAMERA_ARROW_PROBE)
        top = ImageStat.Stat(img.crop((0, 4, 20, 10))).mean[0]
        bottom = ImageStat.Stat(img.crop((0, 10, 20, 16))).mean[0]
        return bottom > top # Arrow points up

    def _replay_playing(self, rect):
        img = self._grab(rect, PLAY_ICON_PROBE)
        return ImageStat.Stat(img.crop((13, 8, 17, 22))).mean[0] < 100 # Gap between the pause bars

    def _wait_for(self, check, timeout=0.6):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return True
            time.sleep(0.03)
        return check()

    # ------------------------------------------------------------------ driver switching

    def _focused_car(self):
        """The car in view. Some replays never fill in the field list, but in a replay the vehicle info and
        player name always describe the car being watched."""
        header, _ = self.shm.read()
        if header is None:
            return None
        v = header.vehicle_info
        return {"slot": v.slot_id, "name": decode(header.player_name), "car": decode(v.name),
                "num": str(v.car_number) if v.car_number > 0 else ""}

    def _ring_index(self, slot):
        return next((i for i, car in enumerate(self.driver_ring) if car["slot"] == slot), None)

    def check_focus(self):
        car = self._focused_car()
        if car is None:
            self.log_message("RaceRoom shared memory not available (is the game running?)")
            return
        i = self._ring_index(car["slot"])
        name = car["name"] or (self.driver_ring[i]["name"] if i is not None else None) or "(not learned yet)"
        self.log_message(f"Focused car: {name} - {car['car']} #{car['num']} (slot {car['slot']})")

    def go_to_driver_prompt(self):
        driver = simpledialog.askstring("Go To Driver", "Driver name:", parent=self.root)
        if driver:
            threading.Thread(target=self._apply_shot, args=(driver.strip(), None, None), daemon=True).start()

    def learn_drivers_prompt(self):
        threading.Thread(target=self._learn_drivers_standalone, daemon=True).start()

    def _learn_drivers_standalone(self):
        with self.logic_lock:
            rect = self._game_window_rect()
            if rect is None or not self.focus_target_window():
                self.log_message("Error: RaceRoom window not found")
                return False
            time.sleep(0.1)
            mouse_home = pydirectinput.position()
            try:
                self._set_replay_bar(rect, True)
                return self._learn_drivers(rect)
            finally:
                if self.data["settings"].get("hide_replay_ui", True):
                    self._set_replay_bar(rect, False)
                pydirectinput.moveTo(*mouse_home)

    def _click_arrow(self, rect, ahead, slot):
        """Clicks a driver arrow and waits for RaceRoom to report a different car in view (~0.05s)."""
        self._click(rect, DRIVER_AHEAD_X if ahead else DRIVER_BEHIND_X, BAR_Y)
        deadline = time.time() + max(0.6, self.data["settings"]["step_gap"])
        car = None
        while time.time() < deadline:
            time.sleep(0.02)
            car = self._focused_car()
            if car and car["slot"] != slot:
                return car
        return car

    def _learn_drivers(self, rect):
        """With the replay bar showing, clicks the "driver ahead" arrow once round the whole field.

        The arrows cycle through the replay's driver list (alphabetical by surname), so this gives the exact
        order, slot and name of every car; later switches then take the short way round.
        """
        car = self._focused_car()
        if car is None:
            self.log_message("Error: RaceRoom shared memory not available")
            return False
        self.log_message("Learning the replay's driver order...")
        ring, seen, stuck = [], set(), 0
        while len(ring) < MAX_CARS:
            new = self._click_arrow(rect, True, car["slot"])
            if not new or new["slot"] == car["slot"]:
                stuck += 1
                if stuck >= 3:
                    self.log_message("The driver arrows are not changing the car in view - is the replay bar where expected?")
                    return False
                continue
            stuck, car = 0, new
            if car["slot"] in seen:
                break
            seen.add(car["slot"])
            ring.append(car)
        # The first car in view may not be in the list (nobody selected yet), so the loop is cut where it closes
        while ring and ring[0]["slot"] != car["slot"]:
            ring.pop(0)
        if not ring:
            return False
        self.driver_ring = ring
        self.log_message(f"Learned {len(ring)} drivers: " + ", ".join(c["name"] for c in ring))
        return True

    def _arrow_order(self, t=None):
        """Driver names in "driver ahead" (>) order: from the timing log at log time t, else as learned."""
        if t is not None and self.race_timing:
            order = self.race_timing[0][1]
            for snap_t, names in self.race_timing:
                if snap_t > t:
                    break
                order = names
            return order[:1] + order[1:][::-1] # > goes to the car one place ahead, and from P1 round to last
        return [c["name"] for c in self.driver_ring]

    @staticmethod
    def _order_index(order, driver):
        wanted = (driver or "").strip().casefold()
        names = [n.casefold() for n in order]
        if wanted in names:
            return names.index(wanted)
        return next((i for i, n in enumerate(names) if wanted and (wanted in n or n in wanted)), None)

    def _click_burst(self, rect, ahead, clicks, slot):
        """Clicks a driver arrow several times in a row, then waits once for the car in view to settle."""
        for _ in range(clicks):
            self._click(rect, DRIVER_AHEAD_X if ahead else DRIVER_BEHIND_X, BAR_Y)
            time.sleep(ARROW_CLICK_GAP)
        deadline = time.time() + 0.5
        car = self._focused_car()
        while car and car["slot"] == slot and time.time() < deadline:
            time.sleep(0.02)
            car = self._focused_car()
        time.sleep(0.05) # Let any last click land before reading the result
        return self._focused_car()

    def _step_to_driver(self, rect, driver, position, log_time=None):
        """With the replay bar showing, clicks the driver arrows the short way round to the target car.

        The clicks are counted from the running order and sent in one burst, then the car in view is checked;
        if RaceRoom's order differed (a car left out of the arrow cycle, positions just changed), it recounts
        from wherever it landed.
        """
        order = self._arrow_order(log_time)
        if not order or self._order_index(order, driver) is None:
            if not self._learn_drivers(rect):
                return False
            order = self._arrow_order()
        target = self._order_index(order, driver)
        if target is None:
            self.log_message(f"Warning: {driver} is not in the replay's driver list")
            return False
        name = order[target]
        car = self._focused_car()
        if car is None:
            return False
        clicks = 0
        for _ in range(4):
            if car["name"].casefold() == name.casefold():
                self.log_message(f"Now watching {name} after {clicks} clicks")
                return True
            current = self._order_index(order, car["name"])
            if current is None: # Nobody selected yet, or a car not in the order: step once and look again
                ahead, steps = True, 1
            else:
                ahead_steps, behind_steps = (target - current) % len(order), (current - target) % len(order)
                ahead, steps = ahead_steps <= behind_steps, min(ahead_steps, behind_steps)
            car = self._click_burst(rect, ahead, steps, car["slot"])
            clicks += steps
            if car is None:
                return False
        self.log_message(f"Could not reach {name} with the driver arrows (staying on {car['name']})")
        return False

    def _pick_camera(self, rect, camera):
        """With the replay bar showing, picks the camera straight from the bar's camera menu (no stepping)."""
        if camera not in CAMERAS:
            self.log_message(f"Warning: unknown camera '{camera}'")
            return False
        if not self._camera_menu_open(rect):
            self._click(rect, CAMERA_MENU_X, BAR_Y)
            if not self._wait_for(lambda: self._camera_menu_open(rect), 0.5):
                self.log_message("Camera menu did not open")
                return False
        self._click(rect, CAMERA_ITEM_X, CAMERAS[camera][0])
        self._wait_for(lambda: not self._camera_menu_open(rect), 0.5)
        self._now_showing(camera)
        return True

    def _now_showing(self, camera):
        self.current_camera = camera
        group = camera_group(camera)
        if group == "external":
            self.camera_memory["external"] = EXTERNAL_CYCLE.index(camera)
        elif group == "onboard":
            self.camera_memory["onboard"] = int(camera.split()[1])
        self.root.after(0, lambda: self.camera_display.set(f"Camera: {camera}"))

    def _hotkey_plan(self, camera):
        """Key presses that reach the camera from the tracked state, or None when the state isn't known."""
        keys = self.data["settings"]["camera_keys"]
        group, current = camera_group(camera), camera_group(self.current_camera or "")
        if group == "external":
            if current == "external":
                start, presses = EXTERNAL_CYCLE.index(self.current_camera), []
            elif self.camera_memory["external"] is not None:
                start, presses = self.camera_memory["external"], [keys["external"]]
            else:
                return None
            return presses + [keys["cycle_external"]] * ((EXTERNAL_CYCLE.index(camera) - start) % len(EXTERNAL_CYCLE))
        if group == "driving":
            if current == "driving":
                steps = (DRIVING_CYCLE.index(camera) - DRIVING_CYCLE.index(self.current_camera)) % len(DRIVING_CYCLE)
                return [keys["driving"]] * steps
            return [keys["driving"]] * (DRIVING_CYCLE.index(camera) + 1)
        if group == "onboard":
            target = int(camera.split()[1])
            if current == "onboard":
                start, presses = int(self.current_camera.split()[1]), []
            elif self.camera_memory["onboard"] is not None:
                start, presses = self.camera_memory["onboard"], [keys["onboard"]]
            else:
                return None
            if target < start:
                return None # Going round the loop needs the car's onboard count, which isn't known
            return presses + [keys["onboard"]] * (target - start)
        return None

    def _hotkey_camera(self, camera):
        """Switches camera with the hotkeys (no replay bar). Returns False when the menu is needed instead."""
        plan = self._hotkey_plan(camera)
        if plan is None:
            return False
        for key in plan:
            pydirectinput.keyDown(key); precise_wait(HOTKEY_TAP)
            pydirectinput.keyUp(key); precise_wait(HOTKEY_TAP)
        self._now_showing(camera)
        return True

    def sync_cameras_prompt(self):
        threading.Thread(target=self._sync_cameras, daemon=True).start()

    def _sync_cameras(self):
        """Puts the camera hotkeys in a known state: Onboard 1 and TV remembered, TV showing. Shows briefly on screen."""
        with self.logic_lock:
            rect = self._game_window_rect()
            if rect is None or not self.focus_target_window():
                self.log_message("Error: RaceRoom window not found")
                return False
            time.sleep(0.1)
            mouse_home = pydirectinput.position()
            try:
                self._set_replay_bar(rect, True)
                ok = self._pick_camera(rect, "Onboard 1") and self._pick_camera(rect, "TV")
            finally:
                if self.data["settings"].get("hide_replay_ui", True):
                    self._set_replay_bar(rect, False)
                pydirectinput.moveTo(*mouse_home)
            self.log_message("Cameras synced (TV)" if ok else "Camera sync failed")
            return ok

    def _apply_shot(self, driver, position, camera, log_time=None):
        """Switches driver and/or camera in one showing of the replay bar. Returns whether the driver was reached."""
        with self.logic_lock:
            rect = self._game_window_rect()
            if rect is None or not self.focus_target_window():
                self.log_message("Error: RaceRoom window not found")
                return False
            time.sleep(0.1)
            mouse_home = pydirectinput.position()
            reached = True
            new_camera = camera if camera and camera != self.current_camera else None
            by_hotkey = (new_camera and self.data["settings"].get("camera_method") == "hotkeys"
                         and self._hotkey_plan(new_camera) is not None)
            if (driver or position is not None) or (new_camera and not by_hotkey):
                try:
                    self._set_replay_bar(rect, True)
                    if driver or position is not None:
                        reached = self._step_to_driver(rect, driver, position, log_time)
                    if new_camera and not by_hotkey and self._pick_camera(rect, new_camera):
                        self.log_message(f"Camera: {new_camera} (menu)")
                finally:
                    if self.data["settings"].get("hide_replay_ui", True):
                        self._set_replay_bar(rect, False)
                    pydirectinput.moveTo(*mouse_home)
            if by_hotkey and self._hotkey_camera(new_camera):
                self.log_message(f"Camera: {new_camera}")
            return reached

    def _set_replay_playing(self, playing):
        """Starts or pauses the RaceRoom replay with the bar's play button. Returns True if it changed."""
        with self.logic_lock:
            rect = self._game_window_rect()
            if rect is None or not self.focus_target_window():
                self.log_message("Error: RaceRoom window not found")
                return False
            time.sleep(0.1)
            mouse_home = pydirectinput.position()
            changed = False
            try:
                self._set_replay_bar(rect, True)
                if self._replay_playing(rect) != playing:
                    self._click(rect, PLAY_BUTTON_X, BAR_Y)
                    changed = self._wait_for(lambda: self._replay_playing(rect) == playing, 1.0)
            finally:
                if self.data["settings"].get("hide_replay_ui", True):
                    self._set_replay_bar(rect, False)
                pydirectinput.moveTo(*mouse_home)
            return changed

    def _manual_driver_pick(self, driver):
        """Fallback: pause the replay so the user can pick the driver, then carry on when they press Space."""
        self.log_message(f"--- SELECT {driver} MANUALLY, then press Space ---")
        if self.focus_target_window():
            time.sleep(0.1)
            self.ignore_space_until = time.time() + 0.5
            self._send_keys("space")
        self.root.after(0, self._enter_manual_pause, driver)

    def _enter_manual_pause(self, driver):
        if not self.is_paused:
            self.toggle_pause()
        self.status_msg.set(f"PAUSED: select {driver} in RaceRoom, then press Space")

    # ------------------------------------------------------------------ keys and window

    def _send_keys(self, keys_str):
        if not keys_str: return
        hold = float(self.data["settings"]["hold_time"])
        for step in keys_str.split(','):
            parts = [p for p in step.lower().replace(" ", "").split('+') if p]
            try:
                for p in parts:
                    pydirectinput.keyDown(p)
                time.sleep(hold)
                for p in reversed(parts):
                    pydirectinput.keyUp(p)
                time.sleep(0.02)
            except Exception as e:
                self.log_message(f"Error sending {step}: {e}")

    def focus_target_window(self):
        target = self.target_window_name.get()
        if not target: return False
        try:
            wins = gw.getWindowsWithTitle(target)
            if wins:
                win = wins[0]
                if win.isMinimized: win.restore()
                if gw.getActiveWindow() != win:
                    win.activate()
                    time.sleep(0.05)
                return True
        except: pass
        return False

    # ------------------------------------------------------------------ sequence files

    def load_sequence_file(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not path: return
        self._load_sequence_from_path(path)

    def _load_sequence_from_path(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "sequence" not in data:
                messagebox.showerror("Error", "Invalid sequence file format.")
                return
            self.sequence_data, changes = enforce_camera_rules(data["sequence"])
            for change in changes:
                self.log_message(f"Camera rule: {change}")
            self.play_btn.config(state="normal")
            self.status_msg.set(f"Loaded: {os.path.basename(path)}" + (f" ({len(changes)} camera fixes)" if changes else ""))
            if self.sequence_data:
                self.log_gf_time.set(self.sequence_data[0]["timecode"])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    def _browse_input_file(self):
        """Let the user change the pre-loaded input file."""
        initial_dir = os.environ.get("R3E_PROJECT_PATH", ".")
        path = filedialog.askopenfilename(
            title="Select Race Data Log",
            initialdir=initial_dir,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            self._set_input_file(path)
            self.log_message(f"Input file changed to: {os.path.basename(path)}")

    def _set_input_file(self, path):
        self.preloaded_input_file = path
        self.input_file_display.set(os.path.basename(path))
        self.input_status_label.config(bg="#1a2e1a", fg="#0f0")

    def _camera_list_for_prompt(self):
        enabled = self.enabled_cameras()
        external = [c for c in enabled if not is_inside(c)] or [FALLBACK_CAMERA]
        inside = [c for c in enabled if is_inside(c)]
        lines = ["- External: " + ", ".join(f'"{c}"' for c in external)]
        if inside:
            lines.append("- Inside (on or in the car): " + ", ".join(f'"{c}"' for c in inside))
        return "\n".join(lines)

    def generate_sequence_with_gemini(self):
        api_key = self._get_gemini_api_key()
        if not api_key:
            api_key = simpledialog.askstring("API Key", "Enter your Gemini API Key:", show='*')
            if not api_key: return
            api_key_path = os.path.join(APP_DIR, "API_Key.txt")
            with open(api_key_path, "w") as f:
                f.write(api_key.strip())

        # Use pre-loaded input file if available, otherwise ask
        data_path = self.preloaded_input_file
        if not (data_path and os.path.exists(data_path) and messagebox.askyesno(
                "Confirm Input", f"Generate sequence from:\n{os.path.basename(data_path)}\n\nProceed?")):
            data_path = filedialog.askopenfilename(
                title="Select Race Data Log",
                initialdir=os.environ.get("R3E_PROJECT_PATH", "."),
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
            )
            if not data_path: return
            self._set_input_file(data_path)

        try:
            with open("Prompt.txt", 'r', encoding='utf-8') as f:
                prompt_text = f.read().replace("{CAMERAS}", self._camera_list_for_prompt())
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read Prompt.txt: {e}")
            return

        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                data_content = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read data file: {e}")
            return

        # Optional: Project Specific Notes
        extra_context = ""
        project_path = os.environ.get("R3E_PROJECT_PATH")
        if not project_path:
            possible_project_path = os.path.dirname(os.path.dirname(os.path.abspath(data_path)))
            if os.path.exists(os.path.join(possible_project_path, "project_notes.txt")):
                project_path = possible_project_path

        if project_path:
            notes_path = camera_gemini.notes_file(project_path, "race_notes", "project_notes.txt")
            if os.path.exists(notes_path):
                try:
                    with open(notes_path, 'r', encoding='utf-8') as f:
                        notes = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#")).strip()
                    if notes:
                        extra_context = f"\n\nProject Specific Instructions:\n{notes}"
                    self.log_message(f"Loaded race notes from: {os.path.basename(notes_path)}")
                except Exception as e:
                    self.log_message(f"Warning: Failed to read race notes: {e}")
            extra_context += self._director_instructions(project_path)

        settings = self.data["settings"]
        try:
            max_tokens = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS") or 65536)
        except ValueError:
            max_tokens = 65536
        model, level = settings["gemini_model"], settings["gemini_thinking_level"]
        self.status_msg.set(f"Generating with {model} (thinking {level})...")
        self.log_message(f"Gemini: {model}, thinking {level}")
        full_prompt = f"{prompt_text}{extra_context}\n\nRace Data:\n{data_content}"
        camera_gemini.generate(self.root, api_key, full_prompt, model, level, max_tokens,
                               self._on_gemini_answer, self._on_gemini_error)

    def _director_instructions(self, project_path):
        """Camera notes from the launcher (camera_notes.txt); # lines are comments."""
        path = camera_gemini.notes_file(project_path, "camera_notes", "camera_instructions.txt")
        if not os.path.exists(path):
            return ""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = [l.rstrip() for l in f if not l.lstrip().startswith("#")]
        except Exception as e:
            self.log_message(f"Warning: Failed to read director notes: {e}")
            return ""
        text = "\n".join(lines).strip()
        if not text:
            return ""
        self.log_message("Loaded director notes for the camera AI")
        return ("\n\nDirector Instructions (from the user - follow these closely, "
                f"they take priority over the general guidance above):\n{text}")

    def _get_gemini_api_key(self):
        # Environment Variable (passed from launcher) first
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            return env_key
        if self.data["settings"].get("api_key"):
            return self.data["settings"]["api_key"]
        for p in ["API_Key.txt", "../API_Key.txt"]:
            if os.path.exists(p):
                with open(p, "r") as f: return f.read().strip()
        return None

    def _on_gemini_error(self, e):
        messagebox.showerror("Generation Error", f"An error occurred: {e}")
        self.status_msg.set("Generation failed")

    def _on_gemini_answer(self, result_text):
        try:
            json_data = camera_gemini.extract_json(result_text)
            json_data["sequence"], changes = enforce_camera_rules(json_data.get("sequence", []))
            for change in changes:
                self.log_message(f"Camera rule: {change}")

            output_filename = camera_gemini.output_path(
                "camera_sequence", f"camera_sequence_raceroom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=4)
            self._on_generation_success(output_filename)
        except Exception as e:
            self._on_gemini_error(e)

    @staticmethod
    def _note_text(item):
        driver = item.get("driver", "Unknown")
        pos = item.get("position")
        text = f"Watching {driver} (P{pos})" if pos is not None else f"Watching {driver}"
        if item_camera(item):
            text += f" - {item_camera(item)} camera"
        return text

    def _generate_director_notes_from_sequence(self, sequence_data):
        """Generate the director notes .txt file directly from the sequence data,
        without requiring playback."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        txt_filename = camera_gemini.output_path("director_notes", f"director_notes_raceroom_{timestamp}.txt")

        try:
            with open(txt_filename, "w", encoding="utf-8") as f:
                for item in sequence_data:
                    f.write(f"{item.get('timecode', '00:00:00')} - [DIRECTOR NOTES] {self._note_text(item)}\n")
            self.log_message(f"Director notes saved: {os.path.basename(txt_filename)}")
            return txt_filename
        except Exception as e:
            self.log_message(f"Warning: Failed to save director notes: {e}")
            return None

    def _update_project_state(self, slot, filepath):
        """Update the project_state.json so the launcher picks up the new file."""
        project_path = os.environ.get("R3E_PROJECT_PATH")
        if not project_path or os.path.exists(os.path.join(project_path, "race.json")):
            return # Race folders use fixed file names, so the launcher needs no state file
        state_path = os.path.join(project_path, "project_state.json")
        try:
            state = {}
            if os.path.exists(state_path):
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            state[slot] = os.path.abspath(filepath)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=4)
            self.log_message(f"Project state updated: {slot}")
        except Exception as e:
            self.log_message(f"Warning: Could not update project state: {e}")

    def _on_generation_success(self, filename):
        self.status_msg.set(f"Sequence saved: {filename}")

        # Generate director notes text file immediately from the JSON
        try:
            with open(filename, "r", encoding="utf-8") as f:
                sequence = json.load(f).get("sequence", [])
            if sequence:
                notes_file = self._generate_director_notes_from_sequence(sequence)
                if notes_file:
                    self._update_project_state("director_notes", notes_file)
        except Exception as e:
            self.log_message(f"Warning: Could not generate director notes: {e}")

        if messagebox.askyesno("Success", f"Sequence saved to {filename}\nLoad it now?"):
            self._load_sequence_from_path(filename)

    # ------------------------------------------------------------------ sync and playback

    def _set_sim_gf_now(self):
        elapsed = datetime.now() - self.playback_start_time
        ts = int(max(0, elapsed.total_seconds()))
        self.sim_gf_time.set(f"{ts//3600:02}:{(ts%3600)//60:02}:{ts%60:02}")

    def _calculate_and_set_offset(self):
        try:
            l_parts = list(map(int, self.log_gf_time.get().split(":")))
            log_sec = l_parts[0]*3600 + l_parts[1]*60 + l_parts[2]
            s_parts = list(map(int, self.sim_gf_time.get().split(":")))
            sim_sec = s_parts[0]*3600 + s_parts[1]*60 + s_parts[2]
            self.time_offset.set(log_sec - sim_sec)
            self.log_message(f"Offset synced: {self.time_offset.get()}s")
        except:
            messagebox.showerror("Error", "Invalid time format (HH:MM:SS)")

    def start_sequence_playback(self):
        if self.is_playing_sequence:
            self.is_playing_sequence = False
            self.is_paused = False
            self.play_btn.config(text="Play Sequence")
            threading.Thread(target=self._set_replay_playing, args=(False,), daemon=True).start()
            return

        self.is_playing_sequence = True
        self.is_paused = False
        self.current_driver = None
        self.current_camera = None

        # Create session log file
        self.session_log_filename = camera_gemini.output_path(
            "director_notes", f"director_notes_raceroom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        open(self.session_log_filename, "w", encoding="utf-8").close() # Notes are appended as the sequence plays

        self.play_btn.config(text="Stop Sequence")
        threading.Thread(target=self._run_sequence, daemon=True).start()

    def _save_director_note(self, timecode, text):
        if not self.session_log_filename: return
        try:
            with open(self.session_log_filename, "a", encoding="utf-8") as f:
                f.write(f"{timecode} - [DIRECTOR NOTES] {text}\n")
        except: pass

    def _run_sequence(self):
        # Start the replay in RaceRoom; the sequence clock starts the moment it is playing
        self.status_msg.set("Starting the replay...")
        if self.data["settings"].get("camera_method") == "hotkeys":
            self._set_replay_playing(False)
            self._sync_cameras() # While the replay is still paused
        self.race_timing = load_race_timing(self.preloaded_input_file)
        if self.race_timing:
            self.log_message(f"Driver order from the timing log ({len(self.race_timing)} snapshots)")
        elif self.sequence_data and any(item.get("driver") for item in self.sequence_data):
            self.status_msg.set("Learning the driver order...")
            self._learn_drivers_standalone() # Also while paused: one lap of the driver arrows
        if self._set_replay_playing(True):
            self.log_message("Replay started in RaceRoom")
        self.playback_start_time = datetime.now()
        self.status_msg.set("Running...")
        self.log_message("Sequence started")

        # Timer update loop
        def update_timer():
            while self.is_playing_sequence:
                if not self.is_paused:
                    elapsed = datetime.now() - self.playback_start_time
                    effective_elapsed = elapsed + timedelta(seconds=self.time_offset.get())
                    ts = int(max(0, effective_elapsed.total_seconds()))
                    self.timer_display.set(f"{ts//3600:02}:{(ts%3600)//60:02}:{ts%60:02}")
                time.sleep(0.2)
        threading.Thread(target=update_timer, daemon=True).start()

        for item in self.sequence_data:
            if not self.is_playing_sequence: break

            parts = list(map(int, item["timecode"].split(":")))
            target_sec = parts[0]*3600 + parts[1]*60 + parts[2]

            while self.is_playing_sequence:
                if self.is_paused:
                    time.sleep(0.5)
                    continue

                elapsed = datetime.now() - self.playback_start_time
                curr_sec = elapsed.total_seconds() + self.time_offset.get()

                if curr_sec >= target_sec:
                    self._execute_item(item)
                    break
                time.sleep(0.1)

        self.is_playing_sequence = False
        self.root.after(0, lambda: self.play_btn.config(text="Play Sequence"))
        self.log_message("Sequence finished")

    def _execute_item(self, item):
        self._save_director_note(item.get("timecode", "00:00:00"), self._note_text(item))
        threading.Thread(target=self._apply_item, args=(item,), daemon=True).start()

    def _apply_item(self, item):
        driver = item.get("driver")
        pos = item.get("position")
        camera = item_camera(item)
        new_driver = driver != self.current_driver
        if new_driver:
            self.log_message(f"Switching to {driver}" + (f" (P{pos})" if pos else ""))
        h, m_, s_ = map(int, item.get("timecode", "0:0:0").split(":"))
        reached = self._apply_shot(driver if new_driver else None, pos if new_driver else None, camera,
                                   log_time=h * 3600 + m_ * 60 + s_) # The timecode is where the replay is now
        if new_driver:
            if reached:
                self.current_driver = driver
            elif self.data["settings"].get("manual_fallback", True):
                self.current_driver = driver
                self._manual_driver_pick(driver)


if __name__ == "__main__":
    root = tk.Tk()
    style = ttk.Style()
    style.theme_use('clam')
    app = RaceroomCameraController(root)
    root.mainloop()
