import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import subprocess
import sys
from settings_manager import settings
from commentary_queue import commentary_queue
from gemini_service import gemini_service

class CommentatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RaceRoom AI Commentator - Filter & Queue")
        self.root.geometry("1000x700")

        # Logger Process
        self.logger_process = None

        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Layout
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        # Tabs
        self.tab_queue = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)
        
        self.notebook.add(self.tab_queue, text="Live Queue")
        self.notebook.add(self.tab_settings, text="Settings")

        # --- Queue Tab ---
        self.build_queue_tab()

        # --- Settings Tab ---
        self.build_settings_tab()

        # --- Status Bar ---
        self.status_var = tk.StringVar()
        self.status_var.set("Initializing...")
        
        # Bottom Frame for Status + Controls
        self.bottom_frame = ttk.Frame(root)
        self.bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Controls (Left)
        initial_text = "Start Commentator"
        self.btn_toggle = ttk.Button(self.bottom_frame, text=initial_text, command=self.toggle_commentator)
        self.btn_toggle.pack(side=tk.LEFT, padx=5, pady=2)
        
        self.btn_clear = ttk.Button(self.bottom_frame, text="Clear Queue", command=self.clear_queue)
        self.btn_clear.pack(side=tk.LEFT, padx=5, pady=2)

        # LLM/TTS Toggle
        self.var_llm_enabled = tk.BooleanVar(value=True)
        self.chk_llm = ttk.Checkbutton(self.bottom_frame, text="Enable LLM/TTS", variable=self.var_llm_enabled)
        self.chk_llm.pack(side=tk.LEFT, padx=5, pady=2)
        
        # Status (Fill)
        self.status_bar = ttk.Label(self.bottom_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=2)

        # Start Processing Loop
        self.running = True
        self.thread = threading.Thread(target=self.backend_loop, daemon=True)
        self.thread.start()
        
        # Start UI Update Loop
        self.root.after(500, self.update_ui)
        
        self.last_spoken_event_id = None
        self.last_activity_time = time.time()
        self.deep_dive_running = False

    def toggle_commentator(self):
        commentary_queue.active = not commentary_queue.active
        if commentary_queue.active:
            self.btn_toggle.configure(text="Stop Commentator")
            # Start Logger
            if self.logger_process is None:
                print("Starting RaceRoom Logger...")
                self.logger_process = subprocess.Popen([sys.executable, "logger.py"])
        else:
            self.btn_toggle.configure(text="Start Commentator")
            # Stop Logger
            if self.logger_process:
                print("Stopping Logger...")
                self.logger_process.terminate()
                self.logger_process = None

    def clear_queue(self):
        commentary_queue.clear()

    def build_queue_tab(self):
        # Frame for Active Event
        self.frame_active = ttk.LabelFrame(self.tab_queue, text="Active Commentary", padding=10)
        self.frame_active.pack(fill=tk.X, padx=10, pady=5)
        
        self.lbl_active_msg = tk.Label(self.frame_active, text="Idle", font=('Helvetica', 12, 'bold'), bg="#d9d9d9", wraplength=800)
        self.lbl_active_msg.pack(fill=tk.X, expand=True)
        
        self.lbl_active_timer = ttk.Label(self.frame_active, text="")
        self.lbl_active_timer.pack(anchor=tk.E)

        # Treeview for Queue
        columns = ("Category", "Priority", "Time Left", "Message")
        self.tree = ttk.Treeview(self.tab_queue, columns=columns, show='headings')
        
        self.tree.heading("Category", text="Category")
        self.tree.column("Category", width=100, anchor=tk.CENTER)
        
        self.tree.heading("Priority", text="Priority")
        self.tree.column("Priority", width=60, anchor=tk.CENTER)
        
        self.tree.heading("Time Left", text="Expires In (s)")
        self.tree.column("Time Left", width=80, anchor=tk.CENTER)
        
        self.tree.heading("Message", text="Event Message")
        self.tree.column("Message", width=600, anchor=tk.W)

        scrollbar = ttk.Scrollbar(self.tab_queue, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def build_settings_tab(self):
        # A canvas with scrollbar for settings
        canvas = tk.Canvas(self.tab_settings)
        scrollbar = ttk.Scrollbar(self.tab_settings, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Config Sections ---
        
        row = 0
        
        # -1. Gemini AI Settings
        ttk.Label(scrollable_frame, text="Gemini AI Settings", font=('Helvetica', 12, 'bold')).grid(row=row, column=0, sticky="w", pady=10, padx=5)
        row += 1
        
        self.gemini_vars = {}
        
        # API Key
        api_key_val = ""
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        api_key_path = os.path.join(root_dir, "API_Key.txt")
        if os.path.exists(api_key_path):
            with open(api_key_path, "r") as f:
                api_key_val = f.read().strip()
                
        var_api = tk.StringVar(value=api_key_val)
        ttk.Label(scrollable_frame, text="API Key:").grid(row=row, column=0, sticky="w", pady=2, padx=10)
        entry_api = ttk.Entry(scrollable_frame, textvariable=var_api, width=40, show="*")
        entry_api.grid(row=row, column=1, sticky="ew", pady=2, padx=10)
        self.gemini_vars["api_key"] = var_api
        row += 1
        
        # LLM Model (Live)
        ttk.Label(scrollable_frame, text="Model Name").grid(row=row, column=0, sticky="w", padx=10)
        var_model = tk.StringVar(value=settings.get("gemini", "llm_model", "gemini-3-flash-preview"))
        entry_model = ttk.Entry(scrollable_frame, textvariable=var_model)
        entry_model.grid(row=row, column=1, padx=10, sticky="w")
        self.gemini_vars["llm_model"] = var_model
        
        btn_test_live = ttk.Button(scrollable_frame, text="Test LLM", command=self.test_live_model, width=8)
        btn_test_live.grid(row=row, column=2, padx=5)
        row += 1

        # Thinking Level
        ttk.Label(scrollable_frame, text="Thinking Level").grid(row=row, column=0, sticky="w", padx=10)
        var_thinking = tk.StringVar(value=settings.get("gemini", "thinking_level", "HIGH"))
        combo_thinking = ttk.Combobox(scrollable_frame, textvariable=var_thinking, values=["HIGH", "MEDIUM", "LOW", "MINIMAL"], width=10)
        combo_thinking.grid(row=row, column=1, padx=10, sticky="w")
        self.gemini_vars["thinking_level"] = var_thinking
        row += 1

        # TTS Enabled
        var_tts_on = tk.BooleanVar(value=settings.get("gemini", "tts_enabled", True))
        chk_tts = ttk.Checkbutton(scrollable_frame, text="Enable TTS", variable=var_tts_on)
        chk_tts.grid(row=row, column=1, sticky="w", padx=10)
        self.gemini_vars["tts_enabled"] = var_tts_on
        row += 1

        # TTS Model
        ttk.Label(scrollable_frame, text="TTS Model").grid(row=row, column=0, sticky="w", padx=10)
        var_tts_model = tk.StringVar(value=settings.get("gemini", "tts_model", "gemini-2.0-flash-exp"))
        entry_tts_model = ttk.Entry(scrollable_frame, textvariable=var_tts_model)
        entry_tts_model.grid(row=row, column=1, padx=10, sticky="w")
        self.gemini_vars["tts_model"] = var_tts_model
        row += 1

        # Voice Selection
        ttk.Label(scrollable_frame, text="Voice").grid(row=row, column=0, sticky="w", padx=10)
        voices = gemini_service.list_voices()
        current_voice = settings.get("gemini", "voice_id", "")
        
        self.combo_voice = ttk.Combobox(scrollable_frame, values=voices, width=50)
        if current_voice and current_voice in voices:
            self.combo_voice.set(current_voice)
        elif voices:
            self.combo_voice.current(0)
            
        self.combo_voice.grid(row=row, column=1, padx=10, sticky="w")
        self.gemini_vars["voice_id"] = self.combo_voice
        
        btn_test_audio = ttk.Button(scrollable_frame, text="Test Audio", command=self.test_audio_only, width=10)
        btn_test_audio.grid(row=row, column=2, padx=5)
        
        row += 1

        # Persona Prompt
        ttk.Label(scrollable_frame, text="Persona Prompt").grid(row=row, column=0, sticky="nw", padx=10, pady=5)
        txt_persona = tk.Text(scrollable_frame, height=5, width=40, font=('Arial', 9))
        txt_persona.insert("1.0", settings.get("gemini", "persona_prompt", ""))
        txt_persona.grid(row=row, column=1, padx=10, pady=5, sticky="w")
        self.gemini_vars["persona_prompt"] = txt_persona # Store widget ref
        row += 1

        # 0. General Filter Settings
        ttk.Label(scrollable_frame, text="Filter Settings", font=('Helvetica', 12, 'bold')).grid(row=row, column=0, sticky="w", pady=10, padx=5)
        row += 1
        
        self.filter_vars = {}
        
        # Duration
        ttk.Label(scrollable_frame, text="Commentary Duration (s)").grid(row=row, column=0, sticky="w", padx=10)
        var = tk.DoubleVar(value=settings.get("filter", "commentary_duration", 5.0))
        entry = ttk.Entry(scrollable_frame, textvariable=var)
        entry.grid(row=row, column=1, padx=10, sticky="w")
        self.filter_vars["commentary_duration"] = var
        row += 1
        
        # Interruption Threshold
        ttk.Label(scrollable_frame, text="Interruption Threshold (Score)").grid(row=row, column=0, sticky="w", padx=10)
        var_thresh = tk.DoubleVar(value=settings.get("filter", "interruption_threshold", 10.0))
        entry_thresh = ttk.Entry(scrollable_frame, textvariable=var_thresh)
        entry_thresh.grid(row=row, column=1, padx=10, sticky="w")
        self.filter_vars["interruption_threshold"] = var_thresh
        row += 1

        # 1. Logger Settings
        ttk.Label(scrollable_frame, text="Logger Settings", font=('Helvetica', 12, 'bold')).grid(row=row, column=0, sticky="w", pady=10, padx=5)
        row += 1
        
        self.logger_vars = {}
        logger_keys = [
            ("Warmup Seconds", "warmup_seconds"),
            ("Leaderboard Interval (s)", "leaderboard_interval"),
            ("Accident Speed Threshold (km/h)", "accident_speed_threshold_kph"),
            ("Accident Time Threshold (s)", "accident_time_threshold")
        ]
        
        for label, key in logger_keys:
            ttk.Label(scrollable_frame, text=label).grid(row=row, column=0, sticky="w", padx=10)
            var = tk.DoubleVar(value=settings.get("logger", key))
            entry = ttk.Entry(scrollable_frame, textvariable=var)
            entry.grid(row=row, column=1, padx=10, sticky="w")
            self.logger_vars[key] = var
            row += 1

        # 2. Priority Settings
        ttk.Label(scrollable_frame, text="Event Priorities (Higher = More Important)", font=('Helvetica', 12, 'bold')).grid(row=row, column=0, sticky="w", pady=10, padx=5)
        row += 1
        
        self.priority_vars = {}
        priorities = settings.get("filter", "priorities")
        for key in sorted(priorities.keys()):
            ttk.Label(scrollable_frame, text=key).grid(row=row, column=0, sticky="w", padx=10)
            var = tk.IntVar(value=priorities[key])
            # Slider
            scale = ttk.Scale(scrollable_frame, from_=0, to=150, variable=var, orient=tk.HORIZONTAL, length=200)
            scale.grid(row=row, column=1, padx=10)
            # Label for value
            lbl = ttk.Label(scrollable_frame, textvariable=var)
            lbl.grid(row=row, column=2, padx=5)
            
            self.priority_vars[key] = var
            row += 1

        # 3. Timeouts
        ttk.Label(scrollable_frame, text="Event Timeouts (Seconds before drop)", font=('Helvetica', 12, 'bold')).grid(row=row, column=0, sticky="w", pady=10, padx=5)
        row += 1
        
        self.timeout_vars = {}
        timeouts = settings.get("filter", "timeouts")
        for key in sorted(timeouts.keys()):
            ttk.Label(scrollable_frame, text=key).grid(row=row, column=0, sticky="w", padx=10)
            var = tk.IntVar(value=timeouts[key])
            entry = ttk.Entry(scrollable_frame, textvariable=var, width=10)
            entry.grid(row=row, column=1, padx=10, sticky="w")
            self.timeout_vars[key] = var
            row += 1
            
        # Save Button
        btn_save = ttk.Button(scrollable_frame, text="Save Settings", command=self.save_settings)
        btn_save.grid(row=row, column=0, columnspan=2, pady=20)

    def test_live_model(self):
        # Save temporary settings first to ensure current keys are used
        self.save_settings()
        
        def run_test():
            self.status_var.set("Testing LLM...")
            sample_event = {
                "category": "OVERTAKE",
                "message": "Verstappen overtakes Hamilton for P1 at Turn 1!"
            }
            text = gemini_service.generate_commentary(sample_event)
            print(f"LLM Output: {text}")
            gemini_service.speak(text)
            self.status_var.set("Test Complete.")

        threading.Thread(target=run_test, daemon=True).start()

    def test_audio_only(self):
        self.save_settings()
        def run_test():
            self.status_var.set("Testing Audio...")
            gemini_service.speak("This is a test of the selected voice engine.")
            self.status_var.set("Audio Test Complete.")
        threading.Thread(target=run_test, daemon=True).start()

    def save_settings(self):
        # Save API Key to API_Key.txt
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        api_key_path = os.path.join(root_dir, "API_Key.txt")
        with open(api_key_path, "w") as f:
            f.write(self.gemini_vars["api_key"].get().strip())
            
        # Update Gemini Settings
        settings.set("gemini", "llm_model", self.gemini_vars["llm_model"].get())
        settings.set("gemini", "thinking_level", self.gemini_vars["thinking_level"].get())
        settings.set("gemini", "tts_enabled", self.gemini_vars["tts_enabled"].get())
        settings.set("gemini", "tts_model", self.gemini_vars["tts_model"].get())
        
        # Save Voice Selection
        selected_voice = self.gemini_vars["voice_id"].get()
        settings.set("gemini", "voice_id", selected_voice)
        # Determine engine based on string
        if "System:" in selected_voice:
            settings.set("gemini", "voice_engine", "system")
        else:
            settings.set("gemini", "voice_engine", "gemini")

        # Get Text from Text Widget
        persona_text = self.gemini_vars["persona_prompt"].get("1.0", "end-1c")
        settings.set("gemini", "persona_prompt", persona_text)
        
        gemini_service.update_settings()

        # Update General Filter
        settings.set("filter", "commentary_duration", self.filter_vars["commentary_duration"].get())
        settings.set("filter", "interruption_threshold", self.filter_vars["interruption_threshold"].get())
        
        # Update Logger
        for key, var in self.logger_vars.items():
            settings.set("logger", key, var.get())
            
        # Update Priorities
        current_priorities = settings.get("filter", "priorities")
        for key, var in self.priority_vars.items():
            current_priorities[key] = var.get()
        settings.set("filter", "priorities", current_priorities)
        
        # Update Timeouts
        current_timeouts = settings.get("filter", "timeouts")
        for key, var in self.timeout_vars.items():
            current_timeouts[key] = var.get()
        settings.set("filter", "timeouts", current_timeouts)
        
        self.status_var.set("Settings Saved.")

    def backend_loop(self):
        while self.running:
            try:
                commentary_queue.update()
                
                # Check for new active event to speak
                if commentary_queue.current_event:
                    self.last_activity_time = time.time() # Reset idle timer
                    event_id = commentary_queue.current_event.get('id')
                    if event_id is not None and event_id != self.last_spoken_event_id:
                        self.last_spoken_event_id = event_id
                        
                        # Only speak if LLM/TTS is enabled
                        if self.var_llm_enabled.get():
                            # Launch async task to generate and speak
                            threading.Thread(
                                target=self.process_commentary, 
                                args=(commentary_queue.current_event['data'],),
                                daemon=True
                            ).start()
                        else:
                            print(f"Skipping LLM for event: {commentary_queue.current_event['data'].get('category')}")

                # Update status text from backend info
                fname = commentary_queue.current_log_file
                if fname:
                    short_name = fname.split('\\')[-1]
                    self.status_var.set(f"Monitoring: {short_name} | Sim Time: {commentary_queue.last_sim_time:.1f}")
                else:
                    self.status_var.set("Waiting for logs...")
                    
                time.sleep(0.5)
            except Exception as e:
                print(f"Backend Error: {e}")
                time.sleep(1)


    def process_commentary(self, event_data):
        """
        Threaded worker to handle LLM generation and TTS.
        """
        try:
            # Clear previous event's pending audio (if any)
            # This ensures we don't speak old news if a new event took over
            gemini_service.clear_queue()
            
            # Use streaming service
            def ui_callback(text):
                # Print to console for now, or could update UI
                pass 

            gemini_service.generate_and_speak_stream(event_data, text_callback=ui_callback)
            
        except Exception as e:
            print(f"Commentary Processing Error: {e}")

    def update_ui(self):
        # Get Snapshot
        active_event, queue_items = commentary_queue.get_snapshot()
        
        # Update Active Event
        if active_event:
            self.lbl_active_msg.configure(text=active_event['message'], bg="#90ee90") # Light green
            self.lbl_active_timer.configure(text=f"Time Remaining: {active_event['time_left']:.1f}s")
        else:
            self.lbl_active_msg.configure(text="Idle (Waiting for events...)", bg="#d9d9d9") # Gray
            self.lbl_active_timer.configure(text="")
            
        # Refresh Treeview
        # Clear existing
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for item in queue_items:
            self.tree.insert("", "end", values=(
                item['category'],
                f"{item['priority']:.1f}",
                f"{item['expires_in']:.1f}",
                item['message']
            ))
            
        # Schedule next update
        self.root.after(500, self.update_ui)

    def on_close(self):
        self.running = False
        print("Stopping Logger...")
        if hasattr(self, 'logger_process'):
            self.logger_process.terminate()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = CommentatorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
