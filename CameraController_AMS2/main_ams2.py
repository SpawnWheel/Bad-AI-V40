import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pynput.keyboard import Controller, Key, Listener
import pydirectinput
import pygetwindow as gw
from google import genai
from google.genai import types
import winsound
import wave
import struct
import math

# Disable pydirectinput's safety pause and failsafe
pydirectinput.PAUSE = 0.01
pydirectinput.FAILSAFE = False

class AMS2CameraController:
    def __init__(self, root):
        self.root = root
        self.root.title("AMS2 Camera Controller")
        self.root.geometry("400x850")
        self.root.attributes("-topmost", True)
        
        self.keyboard = Controller()
        self.config_file = "config_ams2.json"
        self.data = self.load_config()
        
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
        
        self.session_log_filename = None
        
        self.setup_ui()
        self.refresh_windows()
        
        # Global listener for Space (Pause)
        self.global_listener = Listener(on_press=self._on_global_key)
        self.global_listener.start()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _on_global_key(self, key):
        if key == Key.space:
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
        self.root.destroy()

    def load_config(self):
        defaults = {
            "settings": {
                "hold_time": 0.05,
                "step_gap": 0.05,
                "max_participants": 32,
                "api_key": ""
            }
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    # Merge defaults
                    for k, v in defaults["settings"].items():
                        if k not in cfg["settings"]:
                            cfg["settings"][k] = v
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
        ttk.Label(row2, text="Max Drivers:").pack(side=tk.LEFT, padx=2)
        self.max_ent = ttk.Entry(row2, width=8)
        self.max_ent.insert(0, str(self.data["settings"]["max_participants"]))
        self.max_ent.pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="Save Settings", command=self.update_settings).pack(side=tk.RIGHT, padx=2)

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
            self.data["settings"]["max_participants"] = int(self.max_ent.get())
            self.save_config()
            messagebox.showinfo("Success", "Settings updated")
        except:
            messagebox.showerror("Error", "Invalid numeric values")

    def refresh_windows(self):
        titles = [w.title for w in gw.getAllWindows() if w.title]
        self.win_combo['values'] = sorted(titles)
        for t in titles:
            if "automobilista 2" in t.lower(): self.target_window_name.set(t); break

    def log_message(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._append_to_log, f"[{now}] {msg}\n")

    def _append_to_log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

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

    def generate_sequence_with_gemini(self):
        api_key = self._get_gemini_api_key()
        if not api_key:
            api_key = tk.simpledialog.askstring("API Key", "Enter your Gemini API Key:", show='*')
            if not api_key: return
            api_key_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "API_Key.txt")
            with open(api_key_path, "w") as f:
                f.write(api_key.strip())

        data_path = filedialog.askopenfilename(title="Select Race Data Log", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not data_path: return

        try:
            prompt_file = "Prompt.txt" # Always use root Prompt.txt
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_text = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read Prompt.txt: {e}")
            return

        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                data_content = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read data file: {e}")
            return

        self.status_msg.set("Generating sequence with Gemini...")
        threading.Thread(target=self._call_gemini_api, args=(api_key, prompt_text, data_content, data_path), daemon=True).start()

    def _get_gemini_api_key(self):
        # 0. Check Environment Variable (passed from launcher)
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            return env_key

        if self.data["settings"].get("api_key"):
            return self.data["settings"]["api_key"]
            
        # Look in various places for API_Key.txt
        for p in ["API_Key.txt", "Raceroom Example/API_Key.txt", "../API_Key.txt"]:
            if os.path.exists(p):
                with open(p, "r") as f: return f.read().strip()
        return None

    def _call_gemini_api(self, api_key, prompt_text, data_content, original_data_path):
        try:
            client = genai.Client(api_key=api_key)
            full_prompt = f"{prompt_text}\n\nRace Data:\n{data_content}"
            model_name = os.environ.get('GEMINI_MODEL_NAME', 'gemini-2.5-pro')
            
            config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    include_thoughts=False,
                    thinking_level="HIGH"
                )
            ) if "gemini-3" in model_name.lower() else None

            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=config
            )
            
            result_text = response.text
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                 result_text = result_text.split("```")[1].split("```")[0].strip()

            json_data = json.loads(result_text)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"camera_sequence_ams2_{timestamp}.json"
            
            project_path = os.environ.get("R3E_PROJECT_PATH")
            if project_path:
                output_filename = os.path.join(project_path, output_filename)
            
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=4)
                
            self.root.after(0, lambda: self._on_generation_success(output_filename))
            
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Generation Error", f"An error occurred: {e}"))
            self.root.after(0, lambda: self.status_msg.set("Generation failed"))

    def _on_generation_success(self, filename):
        self.status_msg.set(f"Sequence saved: {filename}")
        if messagebox.askyesno("Success", f"Sequence saved to {filename}\nLoad it now?"):
            self._load_sequence_from_path(filename)

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
        
        # Create session log file
        now = datetime.now()
        filename = f"director_notes_ams2_{now.strftime('%Y%m%d_%H%M%S')}.txt"
        project_path = os.environ.get("R3E_PROJECT_PATH")
        if project_path:
            self.session_log_filename = os.path.join(project_path, filename)
        else:
            self.session_log_filename = filename
        
        self.play_btn.config(text="Stop Sequence")
        threading.Thread(target=self._run_sequence, daemon=True).start()

    def _save_director_note(self, timecode, text):
        if not self.session_log_filename: return
        log_line = f"{timecode} - [DIRECTOR NOTES] {text}\n"
        try:
            with open(self.session_log_filename, "a", encoding="utf-8") as f:
                f.write(log_line)
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
            
            tc = item["timecode"]
            parts = list(map(int, tc.split(":")))
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
        driver = item.get("driver", "Unknown")
        pos = item.get("position")
        tc = item.get("timecode", "00:00:00")
        
        if pos is not None:
            self.log_message(f"Switching to {driver} (P{pos})")
            self._save_director_note(tc, f"Watching {driver} (P{pos})")
            threading.Thread(target=self._switch_driver, args=(pos,), daemon=True).start()
        else:
            self.log_message(f"Warning: No position for {driver}")

    def _switch_driver(self, position):
        with self.logic_lock:
            if not self.focus_target_window():
                self.log_message("Error: AMS2 window not found")
                return
            
            time.sleep(0.1)
            hold = self.data["settings"]["hold_time"]
            gap = self.data["settings"]["step_gap"]
            max_p = self.data["settings"]["max_participants"]
            
            # 1. Reset to Top (P1)
            for _ in range(max_p):
                pydirectinput.keyDown('up')
                time.sleep(hold)
                pydirectinput.keyUp('up')
                time.sleep(gap)
            
            # 2. Move Down to Position
            moves = int(position) - 1
            if moves > 0:
                for _ in range(moves):
                    pydirectinput.keyDown('down')
                    time.sleep(hold)
                    pydirectinput.keyUp('down')
                    time.sleep(gap)
            
            # 3. Select
            pydirectinput.keyDown('enter')
            time.sleep(hold)
            pydirectinput.keyUp('enter')
            
            self.log_message(f"Selected P{position}")

    def focus_target_window(self):
        target = self.target_window_name.get()
        if not target: return False
        try:
            wins = gw.getWindowsWithTitle(target)
            if wins:
                win = wins[0]
                if win.isMinimized: win.restore()
                win.activate()
                return True
        except: pass
        return False


if __name__ == "__main__":
    root = tk.Tk()
    style = ttk.Style()
    style.theme_use('clam')
    app = AMS2CameraController(root)
    root.mainloop()
