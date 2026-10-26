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
from google import genai
from google.genai import types

# Shared memory reader lives with the RaceRoom logger
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Logger_Raceroom"))
from r3e_shared import R3ESharedMemory, decode

# Disable pydirectinput's safety pause and failsafe
pydirectinput.PAUSE = 0.01
pydirectinput.FAILSAFE = False

# Numpad digits are missing from pydirectinput's scancode table
pydirectinput.KEYBOARD_MAPPING.update({
    "num0": 0x52, "num1": 0x4F, "num2": 0x50, "num3": 0x51, "num4": 0x4B,
    "num5": 0x4C, "num6": 0x4D, "num7": 0x47, "num8": 0x48, "num9": 0x49,
})

CATEGORIES = ("EXTERNAL", "ONBOARD", "DRIVER")

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
VK_NAMES = {96: "num0", 97: "num1", 98: "num2", 99: "num3", 100: "num4", 101: "num5", 102: "num6",
            103: "num7", 104: "num8", 105: "num9", 106: "num*", 107: "num+", 109: "num-", 110: "num.", 111: "num/"}
DIRECT_KEYS = {"pgup": "pageup", "pgdn": "pagedown", "pgdown": "pagedown", "del": "delete", "ins": "insert",
               "num+": "add", "num-": "subtract", "num*": "multiply", "num/": "divide", "num.": "decimal"}


def key_to_str(key):
    if key in KEY_NAMES:
        return KEY_NAMES[key]
    vk = getattr(key, "vk", None)
    if vk in VK_NAMES:
        return VK_NAMES[vk]
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


class RaceroomCameraController:
    def __init__(self, root):
        self.root = root
        self.root.title("RaceRoom Camera Controller")
        self.root.geometry("400x1060")
        self.root.attributes("-topmost", True)

        self.config_file = "config_raceroom.json"
        self.data = self.load_config()
        self.shm = R3ESharedMemory()

        self.target_window_name = tk.StringVar()
        self.status_msg = tk.StringVar(value="Ready")

        self.sequence_data = None
        self.is_playing_sequence = False
        self.logic_lock = threading.Lock()

        # Playback Timing
        self.playback_start_time = datetime.now()
        self.time_offset = tk.DoubleVar(value=0.0)
        self.timer_display = tk.StringVar(value="00:00:00")

        self.current_driver = None
        self.is_paused = False
        self.pause_start_time = None
        self.ignore_space_until = 0.0 # Our own simulated Space presses must not toggle the pause

        # Camera memory: which category is live and which camera each category was left on
        self.active_category = None
        self.cat_states = {c: 0 for c in CATEGORIES}

        self.session_log_filename = None

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
                "category_wait": 0.25,
                "car_ahead_key": "num+",
                "car_behind_key": "num-",
                "manual_fallback": True,
                "api_key": ""
            },
            "EXTERNAL": {"key": "pagedown", "cycle_key": "end", "cameras": [{"label": "TV", "fav": True}]},
            "ONBOARD": {"key": "home", "cycle_key": "home", "cameras": [{"label": "Dash", "fav": True}]},
            "DRIVER": {"key": "insert", "cycle_key": "insert", "cameras": [{"label": "Cockpit", "fav": True}]}
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                # Merge defaults
                for k, v in defaults.items():
                    cfg.setdefault(k, v)
                for k, v in defaults["settings"].items():
                    cfg["settings"].setdefault(k, v)
                return cfg
            except: pass
        return defaults

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

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

        row2 = ttk.Frame(set_f); row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Camera Wait:").pack(side=tk.LEFT, padx=2)
        self.cat_wait_ent = ttk.Entry(row2, width=8)
        self.cat_wait_ent.insert(0, str(self.data["settings"]["category_wait"]))
        self.cat_wait_ent.pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="Save Settings", command=self.update_settings).pack(side=tk.RIGHT, padx=2)

        # Driver Switching
        drv_f = ttk.LabelFrame(self.main_frame, text="Driver Switching")
        drv_f.pack(fill=tk.X, pady=5)

        self.key_buttons = {}
        for label, setting in (("Car Ahead Key:", "car_ahead_key"), ("Car Behind Key:", "car_behind_key")):
            row = ttk.Frame(drv_f); row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label).pack(side=tk.LEFT, padx=2)
            btn = ttk.Button(row, text=self.data["settings"][setting], width=12,
                             command=lambda s=setting: self.record_setting_key(s))
            btn.pack(side=tk.RIGHT, padx=2)
            self.key_buttons[setting] = btn

        self.manual_fallback_var = tk.BooleanVar(value=self.data["settings"]["manual_fallback"])
        ttk.Checkbutton(drv_f, text="Pause for manual pick if auto-switch fails", variable=self.manual_fallback_var,
                        command=self._save_manual_fallback).pack(anchor="w", padx=2)

        drv_btn_f = ttk.Frame(drv_f); drv_btn_f.pack(fill=tk.X, pady=2)
        ttk.Button(drv_btn_f, text="Check Focused Car", command=self.check_focus).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
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
            self.data["settings"]["category_wait"] = float(self.cat_wait_ent.get())
            self.save_config()
            messagebox.showinfo("Success", "Settings updated")
        except:
            messagebox.showerror("Error", "Invalid numeric values")

    def _save_manual_fallback(self):
        self.data["settings"]["manual_fallback"] = self.manual_fallback_var.get()
        self.save_config()

    def record_setting_key(self, setting):
        btn = self.key_buttons[setting]
        btn.config(text="Press a key...")

        def done(name):
            self.data["settings"][setting] = name
            self.save_config()
            self.root.after(0, lambda: btn.config(text=name))
        record_key(done)

    def refresh_windows(self):
        titles = [w.title for w in gw.getAllWindows() if w.title]
        self.win_combo['values'] = sorted(titles)
        for t in titles:
            if "raceroom" in t.lower(): self.target_window_name.set(t); break

    def log_message(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._append_to_log, f"[{now}] {msg}\n")

    def _append_to_log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ------------------------------------------------------------------ cameras

    def render_cameras(self):
        for w in self.cam_f.winfo_children():
            w.destroy()
        self.camera_vars = {}
        for cat in CATEGORIES:
            favs = [c["label"] for c in self.data[cat]["cameras"] if c.get("fav", True)]
            row = ttk.Frame(self.cam_f); row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"{cat.title()}:", width=9).pack(side=tk.LEFT, padx=2)
            var = tk.StringVar(value=favs[0] if favs else "")
            self.camera_vars[cat] = var
            ttk.Combobox(row, textvariable=var, values=favs, state="readonly", width=22).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            ttk.Button(row, text="Go", width=4, command=lambda c=cat: self.select_camera(c, self.camera_vars[c].get())).pack(side=tk.RIGHT, padx=2)

        btn_row = ttk.Frame(self.cam_f); btn_row.pack(fill=tk.X, pady=2)
        ttk.Button(btn_row, text="Edit Cameras...", command=self.open_camera_editor).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_row, text="Reset Camera Memory", command=self.reset_camera_memory).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=2)

    def reset_camera_memory(self):
        """Use after changing camera by hand in the game: assumes every category is back on its first camera."""
        self.active_category = None
        self.cat_states = {c: 0 for c in CATEGORIES}
        self.log_message("Camera memory reset (every category assumed on its first camera)")

    def open_camera_editor(self):
        win = tk.Toplevel(self.root)
        win.title("Edit Cameras")
        win.geometry("380x600")
        win.attributes("-topmost", True)
        body = ttk.Frame(win, padding=5); body.pack(fill=tk.BOTH, expand=True)

        def rebuild():
            for w in body.winfo_children():
                w.destroy()
            ttk.Label(body, text="Cameras are listed in the order the cycle key steps through them.\n"
                                 "Untick a camera to hide it from the AI and the dropdowns.",
                      wraplength=360).pack(fill=tk.X, pady=(0, 6))
            for cat in CATEGORIES:
                cat_data = self.data[cat]
                frame = ttk.LabelFrame(body, text=cat); frame.pack(fill=tk.X, pady=4)
                keys = ttk.Frame(frame); keys.pack(fill=tk.X, pady=2)
                for label, field in (("Key:", "key"), ("Cycle:", "cycle_key")):
                    ttk.Label(keys, text=label).pack(side=tk.LEFT, padx=2)
                    btn = ttk.Button(keys, text=cat_data[field] or "Record", width=10)
                    btn.config(command=lambda c=cat, f=field, b=btn: self._record_camera_key(c, f, b))
                    btn.pack(side=tk.LEFT, padx=2)
                for i, cam in enumerate(cat_data["cameras"]):
                    row = ttk.Frame(frame); row.pack(fill=tk.X)
                    fav = tk.BooleanVar(value=cam.get("fav", True))
                    ttk.Checkbutton(row, variable=fav, command=lambda c=cat, idx=i, v=fav: self._set_cam(c, idx, "fav", v.get())).pack(side=tk.LEFT)
                    ent = ttk.Entry(row); ent.insert(0, cam["label"]); ent.pack(side=tk.LEFT, fill=tk.X, expand=True)
                    ent.bind("<FocusOut>", lambda e, c=cat, idx=i, v=ent: self._set_cam(c, idx, "label", v.get()))
                    ttk.Button(row, text="X", width=2, command=lambda c=cat, idx=i: (self.data[c]["cameras"].pop(idx), self._cameras_changed(), rebuild())).pack(side=tk.RIGHT)
                ttk.Button(frame, text="+ Add Camera", command=lambda c=cat: (self.data[c]["cameras"].append({"label": "New Cam", "fav": True}), self._cameras_changed(), rebuild())).pack(anchor="w", pady=2)

        rebuild()

    def _record_camera_key(self, cat, field, btn):
        btn.config(text="Press a key...")

        def done(name):
            self.data[cat][field] = name
            self.save_config()
            self.root.after(0, lambda: btn.config(text=name))
        record_key(done)

    def _set_cam(self, cat, idx, field, value):
        self.data[cat]["cameras"][idx][field] = value
        self._cameras_changed()

    def _cameras_changed(self):
        self.save_config()
        self.render_cameras()

    def select_camera(self, category, label):
        if not label: return
        threading.Thread(target=self._switch_camera, args=(category, label), daemon=True).start()

    def _switch_camera(self, category, label):
        """Enter the camera category, then cycle within it from the remembered camera to the target."""
        cameras = [c["label"] for c in self.data.get(category, {}).get("cameras", [])]
        if label not in cameras:
            self.log_message(f"Warning: unknown camera {category} - {label}")
            return False
        target_idx = cameras.index(label)
        with self.logic_lock:
            if not self.focus_target_window():
                self.log_message("Error: RaceRoom window not found")
                return False
            time.sleep(0.1)
            sets = self.data["settings"]
            cat_data = self.data[category]
            if self.active_category != category:
                self._send_keys(cat_data["key"])
                self.active_category = category
                time.sleep(sets["category_wait"])
            steps = (target_idx - self.cat_states[category]) % len(cameras)
            cycle_key = cat_data.get("cycle_key") or cat_data["key"]
            for _ in range(steps):
                self._send_keys(cycle_key)
                time.sleep(sets["step_gap"])
            self.cat_states[category] = target_idx
        self.log_message(f"Camera: {category} - {label}")
        return True

    # ------------------------------------------------------------------ driver switching

    def _read_field(self):
        """Current cars and focused slot from RaceRoom shared memory: ({slot: (name, place)}, focused_slot)."""
        header, drivers = self.shm.read()
        if header is None:
            return None, None
        cars = {d.driver_info.slot_id: (decode(d.driver_info.name), d.place) for d in drivers if d.place > 0}
        return cars, header.vehicle_info.slot_id

    @staticmethod
    def _find_slot(cars, driver, position):
        """Match the sequence's driver name to a car; fall back to the position it gives."""
        wanted = (driver or "").strip().lower()
        if wanted:
            for slot, (name, _) in cars.items():
                if name.lower() == wanted:
                    return slot
            for slot, (name, _) in cars.items():
                if wanted in name.lower() or name.lower() in wanted:
                    return slot
        if position is not None:
            for slot, (_, place) in cars.items():
                if place == int(position):
                    return slot
        return None

    def check_focus(self):
        cars, focused = self._read_field()
        if cars is None:
            self.log_message("RaceRoom shared memory not available (is the game running?)")
        elif focused in cars:
            name, place = cars[focused]
            self.log_message(f"Focused car: {name} (P{place}) - {len(cars)} cars in session")
        else:
            self.log_message(f"Focused slot {focused} not found among {len(cars)} cars")

    def go_to_driver_prompt(self):
        cars, _ = self._read_field()
        if not cars:
            messagebox.showerror("Error", "RaceRoom shared memory not available (is the game running?)")
            return
        driver = simpledialog.askstring("Go To Driver", "Driver name or position (e.g. 3):", parent=self.root)
        if not driver: return
        position = int(driver) if driver.strip().isdigit() else None
        name = None if position else driver
        threading.Thread(target=self._switch_driver, args=(name, position), daemon=True).start()

    def _switch_driver(self, driver, position):
        """Steps the replay focus with the car ahead/behind keys until RaceRoom reports the target car in view."""
        sets = self.data["settings"]
        with self.logic_lock:
            cars, focused = self._read_field()
            if cars is None:
                self.log_message("Error: RaceRoom shared memory not available")
                return False
            target = self._find_slot(cars, driver, position)
            if target is None:
                self.log_message(f"Warning: {driver or ('P' + str(position))} not found in the session")
                return False
            target_name = cars[target][0]
            if focused == target:
                return True
            if not self.focus_target_window():
                self.log_message("Error: RaceRoom window not found")
                return False
            time.sleep(0.1)

            presses, unchanged = 0, 0
            max_presses = len(cars) + 2
            while focused != target and presses < max_presses:
                current_place = cars.get(focused, ("", 999))[1]
                key = sets["car_ahead_key"] if cars[target][1] < current_place else sets["car_behind_key"]
                self._send_keys(key)
                presses += 1
                time.sleep(sets["step_gap"])
                new_cars, new_focused = self._read_field()
                if new_cars is None:
                    break
                unchanged = unchanged + 1 if new_focused == focused else 0
                if unchanged >= 3:
                    self.log_message("Driver keys are not changing the focused car - check the key bindings")
                    break
                cars, focused = new_cars, new_focused

        if focused == target:
            self.log_message(f"Now watching {target_name} (P{cars[target][1]}) after {presses} key presses")
            return True
        self.log_message(f"Could not reach {target_name} automatically")
        return False

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
            parts = [DIRECT_KEYS.get(p, p) for p in step.lower().replace(" ", "").split('+') if p]
            # "num+" splits into "num" and "" - rebuild it
            if step.strip().lower() in DIRECT_KEYS:
                parts = [DIRECT_KEYS[step.strip().lower()]]
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
                if "sequence" in data:
                    self.sequence_data = data["sequence"]
                    self.play_btn.config(state="normal")
                    self.status_msg.set(f"Loaded: {os.path.basename(path)}")
                    if self.sequence_data:
                        self.log_gf_time.set(self.sequence_data[0]["timecode"])
                else:
                    messagebox.showerror("Error", "Invalid sequence file format.")
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
        lines = []
        for cat in CATEGORIES:
            favs = [c["label"] for c in self.data[cat]["cameras"] if c.get("fav", True)]
            if favs:
                lines.append(f"- {cat}: " + ", ".join(f'"{f}"' for f in favs))
        return "\n".join(lines)

    def generate_sequence_with_gemini(self):
        api_key = self._get_gemini_api_key()
        if not api_key:
            api_key = simpledialog.askstring("API Key", "Enter your Gemini API Key:", show='*')
            if not api_key: return
            api_key_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "API_Key.txt")
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
            notes_path = os.path.join(project_path, "project_notes.txt")
            if os.path.exists(notes_path):
                try:
                    with open(notes_path, 'r', encoding='utf-8') as f:
                        extra_context = f"\n\nProject Specific Instructions:\n{f.read().strip()}"
                    self.log_message(f"Loaded project notes from: {os.path.basename(notes_path)}")
                except Exception as e:
                    self.log_message(f"Warning: Failed to read project notes: {e}")

        self.status_msg.set("Generating sequence with Gemini...")
        full_prompt = f"{prompt_text}{extra_context}\n\nRace Data:\n{data_content}"
        threading.Thread(target=self._call_gemini_api, args=(api_key, full_prompt), daemon=True).start()

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

    def _call_gemini_api(self, api_key, full_prompt):
        try:
            client = genai.Client(api_key=api_key)
            model_name = os.environ.get('GEMINI_MODEL_NAME', 'gemini-3.8-flash')
            max_tokens_env = os.environ.get("GEMINI_MAX_OUTPUT_TOKENS")
            try:
                max_output_tokens = int(max_tokens_env) if max_tokens_env else 65536
            except ValueError:
                max_output_tokens = 65536

            is_gemini_3 = any(k in model_name.lower() for k in ["gemini-3", "3.8", "3.7", "3.6", "3.5", "3.1"])
            thinking_config = types.ThinkingConfig(
                include_thoughts=True,
                thinking_level="HIGH"
            ) if is_gemini_3 else None

            config = types.GenerateContentConfig(
                max_output_tokens=max_output_tokens,
                thinking_config=thinking_config
            )

            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=config
            )

            # Print thinking to console
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.thought:
                        print("\n" + "="*60)
                        print("GEMINI THINKING:")
                        print("="*60)
                        print(part.text)
                        print("="*60 + "\n")
                        self.log_message("Gemini thinking printed to console")

            result_text = response.text
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            json_data = json.loads(result_text)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"camera_sequence_raceroom_{timestamp}.json"

            project_path = os.environ.get("R3E_PROJECT_PATH")
            if project_path:
                output_filename = os.path.join(project_path, output_filename)

            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=4)

            self.root.after(0, lambda: self._on_generation_success(output_filename))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Generation Error", f"An error occurred: {e}"))
            self.root.after(0, lambda: self.status_msg.set("Generation failed"))

    @staticmethod
    def _note_text(item):
        driver = item.get("driver", "Unknown")
        pos = item.get("position")
        text = f"Watching {driver} (P{pos})" if pos is not None else f"Watching {driver}"
        if item.get("label"):
            text += f" - {item.get('category', '').title()} {item['label']} camera"
        return text

    def _generate_director_notes_from_sequence(self, sequence_data):
        """Generate the director notes .txt file directly from the sequence data,
        without requiring playback."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        txt_filename = f"director_notes_raceroom_{timestamp}.txt"

        project_path = os.environ.get("R3E_PROJECT_PATH")
        if project_path:
            txt_filename = os.path.join(project_path, txt_filename)

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
        if not project_path:
            return
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
            return

        self.is_playing_sequence = True
        self.is_paused = False
        self.playback_start_time = datetime.now()
        self.current_driver = None

        # Create session log file
        filename = f"director_notes_raceroom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        project_path = os.environ.get("R3E_PROJECT_PATH")
        self.session_log_filename = os.path.join(project_path, filename) if project_path else filename

        self.play_btn.config(text="Stop Sequence")
        threading.Thread(target=self._run_sequence, daemon=True).start()

    def _save_director_note(self, timecode, text):
        if not self.session_log_filename: return
        try:
            with open(self.session_log_filename, "a", encoding="utf-8") as f:
                f.write(f"{timecode} - [DIRECTOR NOTES] {text}\n")
        except: pass

    def _run_sequence(self):
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
        if driver != self.current_driver:
            self.log_message(f"Switching to {driver} (P{pos})")
            if self._switch_driver(driver, pos):
                self.current_driver = driver
            elif self.data["settings"].get("manual_fallback", True):
                self.current_driver = driver
                self._manual_driver_pick(driver)
        category, label = item.get("category"), item.get("label")
        if category and label:
            self._switch_camera(category, label)


if __name__ == "__main__":
    root = tk.Tk()
    style = ttk.Style()
    style.theme_use('clam')
    app = RaceroomCameraController(root)
    root.mainloop()
