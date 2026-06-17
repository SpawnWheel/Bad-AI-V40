import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pynput import keyboard
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

class KeyRecorder:
    def __init__(self, on_update, on_done):
        self.on_update = on_update
        self.on_done = on_done
        self.recorded_steps = []
        self.current_step_keys = set()
        self.listener = None
        self.is_recording = False

    def start(self):
        self.recorded_steps = []
        self.current_step_keys = set()
        self.is_recording = True
        self.listener = Listener(on_press=self.on_press, on_release=self.on_release, suppress=True)
        self.listener.start()

    def stop(self):
        self.is_recording = False
        if self.listener: self.listener.stop()
        self.on_done(self.get_string())

    def on_press(self, key):
        if key == Key.enter:
            self.stop()
            return False
        k_str = self._key_to_str(key)
        if k_str and k_str not in self.current_step_keys:
            self.current_step_keys.add(k_str)
            self.on_update(self.get_string(include_current=True))

    def on_release(self, key):
        if key == Key.enter: return False
        k_str = self._key_to_str(key)
        if k_str in self.current_step_keys:
            step_str = "+".join(sorted(list(self.current_step_keys)))
            if step_str not in self.recorded_steps:
                self.recorded_steps.append(step_str)
            self.current_step_keys.clear()
            self.on_update(self.get_string())

    def _key_to_str(self, key):
        mapping = {
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
        if key in mapping: return mapping[key]
        try:
            if hasattr(key, 'vk'):
                vk_map = {96:"num0", 97:"num1", 98:"num2", 99:"num3", 100:"num4", 101:"num5", 102:"num6", 103:"num7", 104:"num8", 105:"num9", 106:"num*", 107:"num+", 109:"num-", 110:"num.", 111:"num/"}
                if key.vk in vk_map: return vk_map[key.vk]
            if hasattr(key, 'char') and key.char: return key.char.lower()
        except: pass
        return str(key).replace("Key.", "").lower()

    def get_string(self, include_current=False):
        steps = list(self.recorded_steps)
        if include_current and self.current_step_keys:
            curr = "+".join(sorted(list(self.current_step_keys)))
            if curr not in steps: steps.append(curr)
        return ", ".join(steps)

class CameraController:
    def __init__(self, root):
        self.root = root
        self.root.title("Camera Controller")
        self.root.geometry("380x800")
        self.root.attributes("-topmost", True)
        
        self.keyboard = Controller()
        self.config_file = "config_v2.json"
        self.data = self.load_config()
        
        self.target_window_name = tk.StringVar()
        self.active_category = None 
        self.cat_states = { "EXTERNAL": 0, "ONBOARD": 0, "DRIVER": 0 }
        
        self.compact_mode = False
        self.edit_mode = False
        self.settings_mode = False
        self.recording_path = None
        self.recorder = None
        self.status_msg = tk.StringVar(value="Ready")
        
        self.sequence_data = None
        self.is_playing_sequence = False
        self.logic_lock = threading.Lock()
        
        # Playback Timing
        self.playback_start_time = datetime.now()
        self.time_offset = tk.DoubleVar(value=0.0)
        self.timer_display = tk.StringVar(value="00:00:00")
        
        # Manual driver focus tracking
        self.current_driver = None
        self.is_paused = False
        self.pause_start_time = None
        self.is_logging = False
        self.pending_log_timecode = "00:00:00"
        self.session_log_filename = None
        
        # Performance: Pre-define key mappings for global listener
        self._key_map = {
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
        self._vk_map = {96:"num0", 97:"num1", 98:"num2", 99:"num3", 100:"num4", 101:"num5", 102:"num6", 103:"num7", 104:"num8", 105:"num9", 106:"num*", 107:"num+", 109:"num-", 110:"num.", 111:"num/"}

        self.driver_change_event = threading.Event()
        self.global_listener = Listener(on_press=self._on_global_key)
        self.global_listener.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.setup_ui()
        self.refresh_windows()

    def _get_key_str(self, key):
        if key in self._key_map: return self._key_map[key]
        try:
            if hasattr(key, 'vk') and key.vk in self._vk_map:
                return self._vk_map[key.vk]
            if hasattr(key, 'char') and key.char: return key.char.lower()
        except: pass
        return str(key).replace("Key.", "").lower()

    def _on_global_key(self, key):
        if self.is_logging:
            return
        try:
            k_str = self._get_key_str(key)
            log_key_str = self.data.get("settings", {}).get("director_notes_key", "l")
            
            if key == Key.space:
                if self.is_playing_sequence:
                    self.toggle_pause()
            elif k_str == log_key_str:
                self.root.after(0, self.trigger_director_note)
        except: pass

    def trigger_director_note(self):
        if self.is_logging: return
        self.is_logging = True
        
        # Stop global listener IMMEDIATELY so it doesn't catch our own 'space' press
        if self.global_listener:
            self.global_listener.stop()
            self.global_listener = None

        self.paused_by_log = False
        # Pause everything
        if not self.is_paused:
            if self._simulate_pause_press():
                self.toggle_pause()
                self.paused_by_log = True

        # Calculate exact timecode from the internal clock rather than the UI display
        if self.is_paused and self.pause_start_time:
            elapsed = self.pause_start_time - self.playback_start_time
        else:
            elapsed = datetime.now() - self.playback_start_time
            
        try:
            offset_val = self.time_offset.get()
            effective_elapsed = elapsed + timedelta(seconds=offset_val)
        except:
            effective_elapsed = elapsed
        
        ts = int(effective_elapsed.total_seconds())
        if ts < 0: ts = 0
        self.pending_log_timecode = f"{ts//3600:02}:{(ts%3600)//60:02}:{ts%60:02}"
        
        # Bring main app back to focus before showing popup
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        self.root.after(100, self.show_log_popup)

    def show_log_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Director Notes")
        popup.geometry("300x120")
        popup.attributes("-topmost", True)
        popup.transient(self.root)
        
        # Center popup
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 60
        popup.geometry(f"+{x}+{y}")
        
        tk.Label(popup, text=f"Sim Time: {self.pending_log_timecode}", font=("Consolas", 10, "bold")).pack(pady=5)
        entry = ttk.Entry(popup, width=40)
        entry.pack(padx=10, pady=5)
        
        def submit(event=None):
            val = entry.get().strip()
            if val:
                self.save_director_note(self.pending_log_timecode, val)
                self.log_message(f"DIRECTOR NOTE: {val}")
            cleanup()

        def cleanup(event=None):
            if not getattr(self, 'is_logging', False): return
            self.is_logging = False
            
            try:
                popup.grab_release()
                popup.destroy()
            except: pass
            
            # Restart global listener after a short delay
            def restart_listener():
                if not self.global_listener:
                    self.global_listener = Listener(on_press=self._on_global_key)
                    self.global_listener.start()
            self.root.after(100, restart_listener)
            
            # Resume if we paused it for the log
            if getattr(self, 'paused_by_log', False):
                if self.is_paused:
                    if self._simulate_pause_press():
                        self.toggle_pause()
                self.paused_by_log = False
            
            self.status_msg.set("Ready")

        entry.bind("<Return>", submit)
        entry.bind("<Escape>", cleanup)
        popup.protocol("WM_DELETE_WINDOW", cleanup)
        
        popup.update_idletasks()
        popup.lift()
        popup.focus_force()
        entry.focus_set()
        popup.grab_set()

    def save_director_note(self, timecode, text):
        if not self.session_log_filename:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H-%M-%S")
            filename = f"{date_str}_{time_str}_director_notes.txt"
            project_path = os.environ.get("R3E_PROJECT_PATH")
            if project_path:
                self.session_log_filename = os.path.join(project_path, filename)
            else:
                self.session_log_filename = filename
        
        log_line = f"{timecode} - [DIRECTOR NOTES] {text}\n"
        try:
            with open(self.session_log_filename, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            self.log_message(f"Error saving log file: {e}")

    def _play_click(self):
        # Generate a clean 1000Hz sine wave click (0.05s) if it doesn't exist
        temp_dir = os.environ.get("TEMP", os.path.dirname(os.path.abspath(__file__)))
        click_wav = os.path.join(temp_dir, "cam_ctrl_click.wav")
        
        if not os.path.exists(click_wav):
            try:
                sample_rate = 44100
                freq = 1000.0
                duration = 0.05 # 50ms is very sharp
                with wave.open(click_wav, 'w') as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(sample_rate)
                    for i in range(int(duration * sample_rate)):
                        value = int(32767.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
                        f.writeframesraw(struct.pack('<h', value))
            except: pass
        
        try:
            winsound.PlaySound(click_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except:
            try:
                winsound.MessageBeep(-1)
            except: pass

    def toggle_pause(self):
        # Determine new state first to avoid race conditions with the sequence thread
        target_paused_state = not self.is_paused
        self._play_click()
        
        if target_paused_state:
            # Pausing: Set pause_start_time BEFORE setting is_paused to True
            self.pause_start_time = datetime.now()
            self.is_paused = True
            self.log_message("Playback Paused")
        else:
            # Resuming: Update playback_start_time BEFORE setting is_paused to False
            if self.pause_start_time and self.playback_start_time:
                pause_duration = datetime.now() - self.pause_start_time
                self.playback_start_time += pause_duration
            self.is_paused = False
            self.driver_change_event.set()
            self.log_message("Playback Resumed")

    def on_closing(self):
        self.is_playing_sequence = False
        self.driver_change_event.set()
        if hasattr(self, 'global_listener'):
            self.global_listener.stop()
        self.root.destroy()

    def load_config(self):
        defaults = {
            "settings": {
                "hold_time": 0.05,
                "cycle_gap": 0.1,
                "category_wait": 0.25,
                "director_notes_key": "l"
            },
            "EXTERNAL": {"key": "pagedown", "cycle_key": "end", "cameras": [{"label": "TV", "fav": True}]},
            "ONBOARD": {"key": "home", "cycle_key": "home", "cameras": [{"label": "Dash", "fav": True}]},
            "DRIVER": {"key": "insert", "cycle_key": "insert", "cameras": [{"label": "Cockpit", "fav": True}]}
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if "settings" not in loaded: loaded["settings"] = defaults["settings"]
                    return loaded
            except: pass
        return defaults

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    def setup_ui(self):
        self.main_frame = ttk.Frame(self.root, padding="5")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        win_f = ttk.Frame(self.main_frame); win_f.pack(fill=tk.X, pady=2)
        self.win_combo = ttk.Combobox(win_f, textvariable=self.target_window_name, state="readonly")
        self.win_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(win_f, text="O", width=2, command=self.refresh_windows).pack(side=tk.RIGHT)

        head_f = ttk.Frame(self.main_frame); head_f.pack(fill=tk.X, pady=5)
        self.topmost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(head_f, text="Top", variable=self.topmost_var, command=self.toggle_topmost).pack(side=tk.LEFT)
        ttk.Button(head_f, text="Min", width=4, command=self.toggle_compact).pack(side=tk.RIGHT)
        self.settings_btn = ttk.Button(head_f, text="Set", width=4, command=self.toggle_settings)
        self.settings_btn.pack(side=tk.RIGHT, padx=2)
        self.edit_btn = ttk.Button(head_f, text="Edit", width=4, command=self.toggle_edit)
        self.edit_btn.pack(side=tk.RIGHT, padx=2)

        # Timer and Offset
        timer_f = ttk.Frame(self.main_frame); timer_f.pack(fill=tk.X, pady=5)
        tk.Label(timer_f, textvariable=self.timer_display, font=("Consolas", 14, "bold"), bg="#111", fg="#0f0", width=10).pack(side=tk.LEFT, padx=2)
        ttk.Label(timer_f, text="Offset:").pack(side=tk.LEFT, padx=(10, 2))
        ttk.Entry(timer_f, textvariable=self.time_offset, width=6).pack(side=tk.LEFT)
        ttk.Label(timer_f, text="s").pack(side=tk.LEFT)

        # Quick Sync Tool
        sync_f = ttk.LabelFrame(self.main_frame, text="Quick Sync (Green Flag)")
        sync_f.pack(fill=tk.X, pady=5)
        
        lgf_f = ttk.Frame(sync_f); lgf_f.pack(fill=tk.X, pady=2)
        ttk.Label(lgf_f, text="Log GF Time:").pack(side=tk.LEFT, padx=2)
        self.log_gf_time = tk.StringVar(value="00:00:00")
        ttk.Entry(lgf_f, textvariable=self.log_gf_time, width=10).pack(side=tk.LEFT, padx=2)
        
        sgf_f = ttk.Frame(sync_f); sgf_f.pack(fill=tk.X, pady=2)
        ttk.Label(sgf_f, text="Sim GF Time:").pack(side=tk.LEFT, padx=2)
        self.sim_gf_time = tk.StringVar(value="00:00:00")
        ttk.Entry(sgf_f, textvariable=self.sim_gf_time, width=10).pack(side=tk.LEFT, padx=2)
        
        btn_f = ttk.Frame(sync_f); btn_f.pack(fill=tk.X, pady=2)
        ttk.Button(btn_f, text="CALC OFFSET", command=self._calculate_and_set_offset).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        ttk.Button(btn_f, text="SYNC NOW", command=self._set_sim_gf_now, width=8).pack(side=tk.RIGHT, padx=2)

        seq_f = ttk.Frame(self.main_frame); seq_f.pack(fill=tk.X, pady=2)
        ttk.Button(seq_f, text="Load", width=6, command=self.load_sequence_file).pack(side=tk.LEFT, padx=1)
        ttk.Button(seq_f, text="Gen", width=6, command=self.generate_sequence_with_gemini).pack(side=tk.LEFT, padx=1)
        self.play_btn = ttk.Button(seq_f, text="Play Sequence", command=self.start_sequence_playback, state="disabled")
        self.play_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)

        self.status_lbl = tk.Label(self.main_frame, textvariable=self.status_msg, font=("Helvetica", 8), bg="#222", fg="#0f0", anchor="w")
        self.status_lbl.pack(fill=tk.X, pady=2)

        # Log Area
        log_f = ttk.LabelFrame(self.main_frame, text="Activity Log")
        log_f.pack(fill=tk.X, pady=5)
        self.log_text = tk.Text(log_f, height=8, font=("Consolas", 8), bg="#1e1e1e", fg="#00ff00", state="disabled")
        self.log_text.pack(fill=tk.X)

        self.canvas = tk.Canvas(self.main_frame, bg="#2d2d2d", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.main_frame, orient="vertical", command=self.canvas.yview)
        self.scroll_frame = ttk.Frame(self.canvas)
        self.scroll_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw", width=350)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        self.render_content()

    def load_sequence_file(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not path: return
        self._load_sequence_from_path(path)

    def generate_sequence_with_gemini(self):
        # 1. Get API Key
        api_key = self._get_gemini_api_key()
        if not api_key:
            messagebox.showerror("Error", "Gemini API Key not found.")
            return

        # 2. Get Race Data File
        initial_dir = os.environ.get("R3E_PROJECT_PATH")
        if initial_dir and os.path.exists(os.path.join(initial_dir, "Filtered")):
            initial_dir = os.path.join(initial_dir, "Filtered")
            
        data_path = filedialog.askopenfilename(
            title="Select Race Data (Filtered Log)", 
            initialdir=initial_dir,
            filetypes=[("Text files", "*.txt"), ("JSONL files", "*.jsonl"), ("All files", "*.*")]
        )
        if not data_path: return

        # 3. Read Prompt
        try:
            prompt_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Prompt.txt")
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_text = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read Prompt.txt: {e}")
            return

        # 4. Read Data
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                data_content = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read data file: {e}")
            return
            
        # 4b. Optional: Project Specific Notes
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
                except: pass

        # 5. Call Gemini (in a thread)
        self.status_msg.set("Generating sequence with Gemini...")
        full_prompt = f"{prompt_text}{extra_context}\n\nRace Data:\n{data_content}"
        
        threading.Thread(target=self._call_gemini_api, args=(api_key, full_prompt, data_path), daemon=True).start()

    def _get_gemini_api_key(self):
        # 0. Check Environment Variable (passed from launcher)
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            return env_key

        base_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(base_dir, "API_Key.txt")):
            with open(os.path.join(base_dir, "API_Key.txt"), "r") as f: return f.read().strip()
        
        alt_path = os.path.join(base_dir, "..", "2_Gemini Filtering", "API_Key.txt")
        if os.path.exists(alt_path):
             with open(alt_path, "r") as f: return f.read().strip()
             
        config_path = os.path.join(base_dir, "..", "1_Logger", "commentator_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                    key = cfg.get("gemini", {}).get("api_key")
                    if key: return key
            except: pass
        return None

    def _call_gemini_api(self, api_key, full_prompt, original_data_path):
        try:
            client = genai.Client(api_key=api_key)
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
            
            # Save Output
            project_path = os.environ.get("R3E_PROJECT_PATH")
            if project_path:
                output_dir = project_path
            else:
                output_dir = os.path.dirname(os.path.abspath(original_data_path))
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = os.path.basename(original_data_path).replace("filtered_", "").replace(".txt", "").replace(".jsonl", "")
            output_filename = os.path.join(output_dir, f"camera_sequence_{base_name}_{timestamp}.json")
            
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=4)
                
            self.root.after(0, lambda: self._on_generation_success(output_filename))
            
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Generation Error", f"An error occurred: {e}"))
            self.root.after(0, lambda: self.status_msg.set("Generation failed"))

    def _on_generation_success(self, filename):
        self.status_msg.set(f"Sequence saved: {os.path.basename(filename)}")
        if messagebox.askyesno("Success", f"Sequence generated and saved to {filename}\n\nDo you want to load it now?"):
            self._load_sequence_from_path(filename)

    def _load_sequence_from_path(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "sequence" in data:
                    self.sequence_data = data["sequence"]
                    self.play_btn.config(state="normal")
                    self.status_msg.set(f"Loaded: {os.path.basename(path)}")
                    
                    # Auto-set Log GF to first item if available
                    if self.sequence_data:
                        self.log_gf_time.set(self.sequence_data[0]["timecode"])
                else:
                    messagebox.showerror("Error", "Invalid sequence file format.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    def _parse_timecode_to_seconds(self, tc):
        try:
            parts = list(map(int, tc.strip().split(":")))
            if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
            elif len(parts) == 2: return parts[0]*60 + parts[1]
            else: return int(parts[0])
        except: return 0

    def _calculate_and_set_offset(self):
        log_sec = self._parse_timecode_to_seconds(self.log_gf_time.get())
        sim_sec = self._parse_timecode_to_seconds(self.sim_gf_time.get())
        
        new_offset = log_sec - sim_sec
        self.time_offset.set(round(new_offset, 2))
        self.log_message(f"OFFSET UPDATED: {new_offset}s (Log {log_sec}s - Sim {sim_sec}s)")

    def _set_sim_gf_now(self):
        # Captures current elapsed time as the sim green flag time
        if self.is_paused and self.pause_start_time:
            elapsed = self.pause_start_time - self.playback_start_time
        else:
            elapsed = datetime.now() - self.playback_start_time
            
        ts = int(elapsed.total_seconds())
        if ts < 0: ts = 0
        self.sim_gf_time.set(f"{ts//3600:02}:{(ts%3600)//60:02}:{ts%60:02}")
        self._calculate_and_set_offset()

    def start_sequence_playback(self):
        if self.is_playing_sequence:
            self.is_playing_sequence = False
            self.is_paused = False
            self._play_click()
            self.driver_change_event.set() 
            self.play_btn.config(text="Play Sequence")
            return
        
        self.is_playing_sequence = True
        self.is_paused = True # Start paused during countdown
        self._play_click()
        self.pause_start_time = datetime.now()
        self.playback_start_time = datetime.now()
        self.driver_change_event.clear()
        
        self.active_category = None 
        self.play_btn.config(text="Stop Sequence")
        threading.Thread(target=self._run_sequence_with_countdown, daemon=True).start()

    def _run_sequence_with_countdown(self):
        for i in range(5, 0, -1):
            if not self.is_playing_sequence: return
            self.status_msg.set(f"Starting in {i} seconds...")
            time.sleep(1)
        
        if not self.is_playing_sequence: return
        self.status_msg.set("Sequence Running...")
        
        # Unpause both if still paused
        if self.is_paused:
            self._simulate_pause_press()
            # Start clock immediately
            self.playback_start_time = datetime.now()
            self.is_paused = False
            self._play_click()
            self.driver_change_event.set()

        self.current_driver = None 
        
        # Timer thread
        def update_timer():
            while self.is_playing_sequence:
                if not self.is_paused:
                    elapsed = datetime.now() - self.playback_start_time
                    try:
                        offset_val = self.time_offset.get()
                        effective_elapsed = elapsed + timedelta(seconds=offset_val)
                    except:
                        effective_elapsed = elapsed
                    
                    ts = int(effective_elapsed.total_seconds())
                    if ts < 0: ts = 0
                    self.timer_display.set(f"{ts//3600:02}:{(ts%3600)//60:02}:{ts%60:02}")
                time.sleep(0.2) # Faster update for better precision display

        threading.Thread(target=update_timer, daemon=True).start()

        for item in self.sequence_data:
            if not self.is_playing_sequence: break
            
            try:
                parts = list(map(int, item["timecode"].split(":")))
                if len(parts) == 3: target_delta = timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
                elif len(parts) == 2: target_delta = timedelta(minutes=parts[0], seconds=parts[1])
                else: target_delta = timedelta(seconds=parts[0])
            except: continue
            
            while self.is_playing_sequence:
                if self.is_paused:
                    self.driver_change_event.wait(0.1)
                    continue

                elapsed = datetime.now() - self.playback_start_time
                try: offset_val = self.time_offset.get()
                except: offset_val = 0.0
                effective_elapsed = elapsed + timedelta(seconds=offset_val)

                if effective_elapsed >= target_delta:
                    # Check for Driver Change
                    target_driver = item.get("driver")
                    if target_driver and target_driver != self.current_driver:
                        self.log_message(f"--- DRIVER CHANGE: {target_driver} ---")
                        self.status_msg.set(f"PAUSE: Select {target_driver}")
                        
                        # Pause both
                        if not self.is_paused:
                            self._simulate_pause_press()
                            self.is_paused = True
                            self._play_click()
                            self.pause_start_time = datetime.now()
                        
                        self.driver_change_event.clear()
                        while self.is_playing_sequence and self.is_paused:
                            self.driver_change_event.wait(0.1)
                        
                        if not self.is_playing_sequence: break
                        self.current_driver = target_driver
                        self.status_msg.set("Sequence Running...")

                    # Run Camera Logic
                    cat = item["category"]
                    label = item["label"]
                    idx = -1
                    if cat in self.data:
                        for i, cam in enumerate(self.data[cat]["cameras"]):
                            if cam["label"] == label:
                                idx = i; break
                    
                    if idx != -1: 
                        self._run_logic(cat, idx)
                        # Automatic log for camera switch
                        target_driver = item.get("driver", "Unknown")
                        auto_msg = f"Watching {target_driver} ({cat} - {label})"
                        self.save_director_note(item["timecode"], auto_msg)
                    break
                time.sleep(0.1)

        self.is_playing_sequence = False
        self.root.after(0, lambda: self.play_btn.config(text="Play Sequence"))
        self.status_msg.set("Sequence Finished")

    def refresh_windows(self):
        titles = [w.title for w in gw.getAllWindows() if w.title]
        self.win_combo['values'] = sorted(titles)
        for t in titles:
            if "raceroom" in t.lower(): self.target_window_name.set(t); break

    def log_message(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._append_to_log, f"[{now}] {msg}\n")

    def _append_to_log(self, text):
        if hasattr(self, 'log_text'):
            self.log_text.config(state="normal")
            self.log_text.insert(tk.END, text)
            self.log_text.delete("1.0", "end-50l") 
            self.log_text.see(tk.END)
            self.log_text.config(state="disabled")

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

    def render_content(self):
        for w in self.scroll_frame.winfo_children(): w.destroy()
        if self.settings_mode: self.render_settings(); return

        for cat_name, cat_data in self.data.items():
            if cat_name == "settings": continue
            is_active_cat = (self.active_category == cat_name)
            bg_color = "#3d3d5d" if is_active_cat else "#222"
            cat_f = tk.Frame(self.scroll_frame, bg=bg_color, pady=5)
            cat_f.pack(fill=tk.X, pady=(10, 2))
            tk.Label(cat_f, text=cat_name, font=("Helvetica", 9, "bold"), fg="#aaa", bg=bg_color).pack(side=tk.LEFT, padx=5)
            tk.Button(cat_f, text="SYNC", font=("Helvetica", 7), bg="#444", fg="white", bd=0, command=lambda c=cat_name: self.sync_cat(c)).pack(side=tk.RIGHT, padx=5)

            if self.edit_mode:
                k_f = ttk.Frame(self.scroll_frame); k_f.pack(fill=tk.X)
                ttk.Label(k_f, text="Key:").pack(side=tk.LEFT)
                ttk.Button(k_f, text=cat_data["key"] or "Record", width=10, command=lambda c=cat_name: self.start_rec(c, -1)).pack(side=tk.LEFT, padx=2)
                ttk.Label(k_f, text="Cycle:").pack(side=tk.LEFT)
                ttk.Button(k_f, text=cat_data["cycle_key"] or "Record", width=10, command=lambda c=cat_name: self.start_rec(c, -2)).pack(side=tk.LEFT, padx=2)

            for i, cam in enumerate(cat_data["cameras"]):
                if not self.edit_mode and not cam.get("fav", True): continue
                f = tk.Frame(self.scroll_frame, bg="#2d2d2d"); f.pack(fill=tk.X, pady=1)
                is_active_cam = (is_active_cat and self.cat_states[cat_name] == i)
                is_remembered_cam = (not is_active_cat and self.cat_states[cat_name] == i)
                btn_bg = "#2e7d32" if is_active_cam else ("#555" if is_remembered_cam else "#424242")
                
                if self.edit_mode:
                    fav_var = tk.BooleanVar(value=cam.get("fav", True))
                    ttk.Checkbutton(f, variable=fav_var, command=lambda c=cat_name, idx=i, v=fav_var: self.update_fav(c, idx, v.get())).pack(side=tk.LEFT)
                    ent = ttk.Entry(f); ent.insert(0, cam["label"]); ent.pack(side=tk.LEFT, fill=tk.X, expand=True)
                    ent.bind("<FocusOut>", lambda e, c=cat_name, idx=i, v=ent: self.update_label(c, idx, v.get()))
                    tk.Button(f, text="X", fg="red", bg="#2d2d2d", bd=0, command=lambda c=cat_name, idx=i: self.delete_cam(c, idx)).pack(side=tk.RIGHT)
                else:
                    btn = tk.Button(f, text=cam["label"], bg=btn_bg, fg="white", bd=1, relief="raised", command=lambda c=cat_name, idx=i: self.select_camera(c, idx))
                    btn.pack(fill=tk.X, ipady=3)

    def render_settings(self):
        ttk.Label(self.scroll_frame, text="SETTINGS", font=("Helvetica", 10, "bold")).pack(pady=10)
        for label, key in [("Hold Time:", "hold_time"), ("Cycle Gap:", "cycle_gap"), ("Cat Wait:", "category_wait")]:
            f = ttk.Frame(self.scroll_frame, padding=5); f.pack(fill=tk.X)
            ttk.Label(f, text=label).pack(side=tk.LEFT)
            ent = ttk.Entry(f); ent.insert(0, str(self.data["settings"].get(key, ""))); ent.pack(side=tk.RIGHT, fill=tk.X, expand=True)
            ent.bind("<FocusOut>", lambda e, k=key, v=ent: self.update_setting(k, v.get()))
        
        # Director Notes Key with Record button
        f = ttk.Frame(self.scroll_frame, padding=5); f.pack(fill=tk.X)
        ttk.Label(f, text="Director Notes Key:").pack(side=tk.LEFT)
        ttk.Button(f, text=self.data["settings"].get("director_notes_key", "l"), command=lambda: self.start_rec("settings", -3)).pack(side=tk.RIGHT)

    def sync_cat(self, cat):
        self.cat_states[cat] = 0
        self.status_msg.set(f"Synced {cat} memory to index 0")
        self.render_content()
        self.status_msg.set(f"Synced {cat} memory to index 0")
        self.render_content()

    def select_camera(self, category, target_idx):
        self.status_msg.set(f"Targeting: {category} -> {self.data[category]['cameras'][target_idx]['label']}")
        threading.Thread(target=self._run_logic, args=(category, target_idx), daemon=True).start()

    def _run_logic(self, category, target_idx):
        with self.logic_lock:
            if not self.focus_target_window():
                self.log_message("ERROR: Target window not found!")
                return
            time.sleep(0.2)
            cat_data = self.data[category]
            all_cameras = cat_data["cameras"]
            num_cams = len(all_cameras)
            sets = self.data["settings"]
            label = all_cameras[target_idx]['label']
            self.log_message(f"START: {category} -> {label}")
            if self.active_category != category:
                self.log_message(f"Action: Switch to {category} category [{cat_data['key']}]")
                self._send_keys(cat_data["key"])
                self.active_category = category
                time.sleep(sets["category_wait"])
            current_pos = self.cat_states[category]
            steps = (target_idx - current_pos) % num_cams
            if steps > 0:
                cycle_key = cat_data.get("cycle_key") or cat_data["key"]
                self.log_message(f"Action: Cycle {steps} times using [{cycle_key}]")
                for _ in range(steps):
                    self._send_keys(cycle_key)
                    time.sleep(sets["cycle_gap"])
            self.cat_states[category] = target_idx
            self.log_message(f"FINISH: {category} is now at {label}")
            self.root.after(0, self.render_content)

    def _simulate_pause_press(self):
        if self.focus_target_window():
            time.sleep(0.2) # Buffer to ensure window is ready for input
            hold = float(self.data["settings"].get("hold_time", 0.05))
            pydirectinput.keyDown('space')
            time.sleep(hold)
            pydirectinput.keyUp('space')
            return True
        return False

    def _send_keys(self, keys_str, hold_override=None):
        if not keys_str: return
        hold = hold_override if hold_override is not None else float(self.data["settings"]["hold_time"])
        for step in keys_str.split(','):
            parts = step.lower().replace(" ", "").split('+')
            try:
                for p in parts:
                    dk = self.get_direct_key(p)
                    pydirectinput.keyDown(dk)
                time.sleep(hold)
                for p in reversed(parts):
                    dk = self.get_direct_key(p)
                    pydirectinput.keyUp(dk)
                time.sleep(0.02)
            except Exception as e:
                self.log_message(f"  ERROR sending {step}: {e}")

    def get_direct_key(self, k):
        m = {"pageup": "pageup", "pgup": "pageup", "pagedown": "pagedown", "pgdn": "pagedown", "pgdown": "pagedown", "delete": "delete", "del": "delete", "insert": "insert", "ins": "insert", "end": "end", "home": "home"}
        return m.get(k, k)

    def start_rec(self, cat, idx):
        if self.recorder and self.recorder.is_recording: return
        self.recording_path = (cat, idx); self.render_content()
        self.recorder = KeyRecorder(on_update=lambda s: None, on_done=self.finish_rec); self.recorder.start()

    def finish_rec(self, s):
        cat, idx = self.recording_path
        if idx == -1: self.data[cat]["key"] = s
        elif idx == -2: self.data[cat]["cycle_key"] = s
        elif idx == -3: self.data["settings"]["director_notes_key"] = s
        self.save_config(); self.root.after(0, self.render_content)

    def toggle_settings(self): self.settings_mode = not self.settings_mode; self.edit_mode = False; self.render_content()
    def toggle_edit(self): self.edit_mode = not self.edit_mode; self.settings_mode = False; self.render_content()
    def toggle_topmost(self): self.root.attributes("-topmost", self.topmost_var.get())
    def update_fav(self, c, i, v): self.data[c]["cameras"][i]["fav"] = v; self.save_config()
    def update_label(self, c, i, v): self.data[c]["cameras"][i]["label"] = v; self.save_config()
    def add_cam(self, c): self.data[c]["cameras"].append({"label": "New Cam", "fav": True}); self.render_content()
    def delete_cam(self, c, i): self.data[c]["cameras"].pop(i); self.save_config(); self.render_content()
    def update_setting(self, k, v):
        try:
            if k == "director_notes_key":
                self.data["settings"][k] = v
            else:
                self.data["settings"][k] = float(v)
            self.save_config()
        except: pass

    def toggle_compact(self):
        self.compact_mode = not self.compact_mode
        if self.compact_mode:
            self.main_frame.pack_forget(); self.root.geometry("150x250")
            self.restore_btn = tk.Button(self.root, text="+", command=self.toggle_compact, bg="#444", fg="white", bd=0)
            self.restore_btn.place(relx=1.0, rely=0.0, anchor="ne")
        else:
            if hasattr(self, 'restore_btn'): self.restore_btn.destroy()
            self.main_frame.pack(fill=tk.BOTH, expand=True); self.root.geometry("380x800")

if __name__ == "__main__":
    root = tk.Tk(); style = ttk.Style(); style.theme_use('clam')
    style.configure("TFrame", background="#2d2d2d"); style.configure("TLabel", background="#2d2d2d", foreground="white")
    root.configure(bg="#2d2d2d"); app = CameraController(root); root.mainloop()