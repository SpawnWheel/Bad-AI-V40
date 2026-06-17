import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json
import os
import subprocess
import sys
import shutil
from datetime import datetime

CONFIG_FILE = "launcher_config.json"
STATE_FILE = "launcher_state.json"
PROJECT_ROOT_DIR = "Projects"

THEME_BG = "#1e1e1e"
THEME_FG = "#00ff41" # Matrix Green
THEME_ACCENT = "#d32f2f" # Red
THEME_BUTTON_BG = "#333333"
THEME_BUTTON_ACTIVE = "#555555"

class ProjectManager:
    def __init__(self, config_steps):
        self.current_project = None
        self.config_steps = config_steps
        self.project_state = {}
        self.ensure_project_root()
        self.load_state()

    def ensure_project_root(self):
        if not os.path.exists(PROJECT_ROOT_DIR):
            os.makedirs(PROJECT_ROOT_DIR)

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    last_project = state.get("last_project")
                    if last_project and os.path.exists(os.path.join(PROJECT_ROOT_DIR, last_project)):
                        self.current_project = last_project
                        self.load_project_state()
            except:
                pass

    def save_state(self):
        state = {"last_project": self.current_project}
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
        except:
            pass

    def load_project_state(self):
        if not self.current_project:
            self.project_state = {}
            return

        proj_path = self.get_project_path()
        state_path = os.path.join(proj_path, "project_state.json")
        
        if os.path.exists(state_path):
            try:
                with open(state_path, 'r') as f:
                    self.project_state = json.load(f)
            except:
                self.project_state = {}
        else:
            self.project_state = {}
            # Auto-scan on first load if state doesn't exist
            self.scan_project_files()

    def save_project_state(self):
        if not self.current_project:
            return

        proj_path = self.get_project_path()
        state_path = os.path.join(proj_path, "project_state.json")
        
        try:
            with open(state_path, 'w') as f:
                json.dump(self.project_state, f, indent=4)
        except Exception as e:
            print(f"Failed to save project state: {e}")

    def scan_project_files(self):
        """Scans the project folders for the latest files based on config definitions."""
        if not self.current_project:
            return

        changes = False
        proj_path = self.get_project_path()
        current_sim = self.project_state.get("sim_type", "Raceroom")

        for step in self.config_steps:
            # Filter by sim
            step_sims = step.get("sims", [])
            if step_sims and current_sim not in step_sims:
                continue

            output_slot = step.get('output_slot')
            output_folder = step.get('output_folder')
            output_ext = step.get('output_extension')

            if output_slot and output_folder:
                # Special handling for "FOLDER" extension type (Timeline/Audio)
                target_dir = os.path.join(proj_path, output_folder)
                if not os.path.exists(target_dir):
                    continue

                if output_ext == "FOLDER":
                    # For folder outputs, we just point to the folder itself
                    # Or check if the folder is not empty?
                    # For now, let's just set the path to the folder if it exists
                    if os.listdir(target_dir):
                         if self.project_state.get(output_slot) != target_dir:
                            self.project_state[output_slot] = target_dir
                            changes = True
                else:
                    # File scanning
                    files = [os.path.join(target_dir, f) for f in os.listdir(target_dir) if f.endswith(output_ext or "")]
                    if files:
                        latest_file = max(files, key=os.path.getmtime)
                        # Only update if different or missing (to avoid overwriting manual overrides if we had them, 
                        # but for now we assume 'latest is greatest' is the desired auto-behavior)
                        if self.project_state.get(output_slot) != latest_file:
                             self.project_state[output_slot] = latest_file
                             changes = True
        
        if changes:
            self.save_project_state()

    def create_project(self, name):
        # Sanitize name
        safe_name = "".join([c for c in name if c.isalpha() or c.isdigit() or c in (' ', '_', '-')]).rstrip()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{safe_name}_{timestamp}"
        path = os.path.join(PROJECT_ROOT_DIR, folder_name)
        
        try:
            os.makedirs(path)
            # Only create Logs subfolder, everything else goes in root
            os.makedirs(os.path.join(path, "Logs"), exist_ok=True)
            
            self.current_project = folder_name
            self.project_state = {}
            self.save_state()
            self.save_project_state()
            return True, folder_name
        except Exception as e:
            return False, str(e)

    def get_projects(self):
        if not os.path.exists(PROJECT_ROOT_DIR):
            return []
        return [d for d in os.listdir(PROJECT_ROOT_DIR) if os.path.isdir(os.path.join(PROJECT_ROOT_DIR, d))]

    def get_project_path(self):
        if self.current_project:
            return os.path.abspath(os.path.join(PROJECT_ROOT_DIR, self.current_project))
        return None

class BadAILauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("BAD AI: Raceroom Edition")
        self.root.geometry("1000x800")
        self.root.configure(bg=THEME_BG)

        # Load Config First
        self.steps = []
        self.load_config()

        self.pm = ProjectManager(self.steps)
        self.step_input_vars = {} # {step_idx: (StringVar, slot)}
        self.step_labels = {} # {step_idx: Label}

        # Style configuration
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.style.configure("TFrame", background=THEME_BG)
        self.style.configure("TLabel", background=THEME_BG, foreground=THEME_FG, font=("Consolas", 10))
        self.style.configure("TButton", background=THEME_BUTTON_BG, foreground=THEME_FG, font=("Consolas", 10, "bold"), borderwidth=1)
        self.style.map("TButton", background=[('active', THEME_BUTTON_ACTIVE)])
        self.style.configure("TNotebook", background=THEME_BG, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=THEME_BUTTON_BG, foreground=THEME_FG, padding=[10, 5])
        self.style.map("TNotebook.Tab", background=[('selected', THEME_ACCENT)])

        # --- Project Selection Header ---
        self.project_frame = tk.Frame(root, bg=THEME_BG, pady=10)
        self.project_frame.pack(fill='x', padx=10)
        
        tk.Label(self.project_frame, text="ACTIVE PROJECT:", bg=THEME_BG, fg=THEME_ACCENT, font=("Consolas", 10, "bold")).pack(side="left")
        
        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(self.project_frame, textvariable=self.project_var, state="readonly", width=40)
        self.project_combo.pack(side="left", padx=10)
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_change)

        tk.Label(self.project_frame, text="SIM:", bg=THEME_BG, fg=THEME_ACCENT, font=("Consolas", 10, "bold")).pack(side="left", padx=(10, 0))
        self.sim_var = tk.StringVar(value="Raceroom")
        self.sim_combo = ttk.Combobox(self.project_frame, textvariable=self.sim_var, values=["Raceroom", "AMS2"], state="readonly", width=10)
        self.sim_combo.pack(side="left", padx=5)
        self.sim_combo.bind("<<ComboboxSelected>>", self.on_sim_change)

        tk.Button(self.project_frame, text="NEW PROJECT", bg=THEME_BUTTON_BG, fg=THEME_FG, font=("Consolas", 9), command=self.create_project).pack(side="left", padx=5)
        tk.Button(self.project_frame, text="OPEN FOLDER", bg=THEME_BUTTON_BG, fg=THEME_FG, font=("Consolas", 9), command=self.open_project_folder).pack(side="left", padx=5)
        tk.Button(self.project_frame, text="PROJECT NOTES", bg=THEME_BUTTON_BG, fg=THEME_FG, font=("Consolas", 9), command=self.edit_project_notes).pack(side="left", padx=5)
        tk.Button(self.project_frame, text="SCAN FILES", bg=THEME_BUTTON_BG, fg=THEME_FG, font=("Consolas", 9), command=self.scan_files).pack(side="left", padx=5)

        # Layout
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        self.tab_run = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_run, text=">> EXECUTE <<")
        self.notebook.add(self.tab_settings, text=":: CONFIGURE ::")

        self.build_run_tab()
        self.build_settings_tab()
        
        # Footer
        self.footer = tk.Label(root, text="SYSTEM READY...", bg=THEME_BG, fg=THEME_ACCENT, font=("Consolas", 8), anchor="w")
        self.footer.pack(side="bottom", fill="x", padx=10, pady=2)

        self.update_project_list()

    def update_project_list(self):
        projects = self.pm.get_projects()
        self.project_combo['values'] = projects
        if self.pm.current_project and self.pm.current_project in projects:
            self.project_combo.set(self.pm.current_project)
            # Ensure state is loaded for the auto-selected project
            self.pm.load_project_state()
            self.refresh_state_display()
        elif projects:
            self.project_combo.current(0)
            self.pm.current_project = projects[0]
            self.pm.save_state()
            self.pm.load_project_state()
            self.refresh_state_display()
        else:
            self.project_combo.set("")

    def on_project_change(self, event):
        self.pm.current_project = self.project_var.get()
        self.pm.save_state()
        self.pm.load_project_state()
        
        # Update sim type from project state
        sim_type = self.pm.project_state.get("sim_type", "Raceroom")
        self.sim_var.set(sim_type)
        
        self.status(f"Active Project: {self.pm.current_project}")
        self.refresh_state_display()
        self.refresh_run_tab()

    def on_sim_change(self, event):
        if self.pm.current_project:
            self.pm.project_state["sim_type"] = self.sim_var.get()
            self.pm.save_project_state()
            self.refresh_run_tab()
            self.refresh_state_display()

    def scan_files(self):
        self.pm.scan_project_files()
        self.refresh_state_display()
        self.status("Project files scanned and state updated.")

    def create_project(self):
        name = simpledialog.askstring("New Project", "Enter Project Name:")
        if name:
            success, result = self.pm.create_project(name)
            if success:
                self.update_project_list()
                self.project_combo.set(result) # Select the new one
                self.on_project_change(None)
                self.status(f"Project created: {result}")
            else:
                messagebox.showerror("Error", f"Failed to create project: {result}")

    def open_project_folder(self):
        path = self.pm.get_project_path()
        if path and os.path.exists(path):
            os.startfile(path)

    def edit_project_notes(self):
        path = self.pm.get_project_path()
        if path and os.path.exists(path):
            notes_file = os.path.join(path, "project_notes.txt")
            if not os.path.exists(notes_file):
                try:
                    with open(notes_file, 'w', encoding='utf-8') as f:
                        f.write("# Project Specific Notes\n# Add any specific instructions for this project here.\n")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to create notes file: {e}")
                    return
            
            os.startfile(notes_file)
        else:
            messagebox.showwarning("Warning", "No active project selected.")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.steps = data
                        self.global_settings = {"gemini_api_key": ""}
                    else:
                        self.steps = data.get("steps", [])
                        self.global_settings = data.get("global_settings", {"gemini_api_key": ""})
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load config: {e}")
                self.steps = []
                self.global_settings = {"gemini_api_key": ""}
        else:
            self.steps = []
            self.global_settings = {"gemini_api_key": ""}

    def save_config(self):
        try:
            data = {
                "global_settings": self.global_settings,
                "steps": self.steps
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=4)
            self.status(f"Configuration saved to {CONFIG_FILE}")
            # Update PM config ref
            self.pm.config_steps = self.steps
            self.refresh_run_tab()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {e}")

    def update_global_setting(self, key, value):
        self.global_settings[key] = value

    def build_run_tab(self):
        # Top: Pipeline Flow
        self.run_scroll_frame = tk.Frame(self.tab_run, bg=THEME_BG)
        self.run_scroll_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        # Right: State Monitor
        self.state_frame = tk.LabelFrame(self.tab_run, text="Project Context", bg=THEME_BG, fg=THEME_ACCENT, padx=10, pady=10)
        self.state_frame.pack(side="right", fill="y", padx=10, pady=10, ipadx=5)
        
        self.state_text = tk.Text(self.state_frame, height=30, width=40, bg="#222", fg="#ddd", font=("Consolas", 8))
        self.state_text.pack(fill="both", expand=True)
        
        self.refresh_run_tab()

    def refresh_state_display(self):
        self.state_text.config(state="normal")
        self.state_text.delete(1.0, tk.END)
        
        if not self.pm.project_state:
            self.state_text.insert(tk.END, "(No State Loaded)")
        else:
            for key, value in self.pm.project_state.items():
                short_val = os.path.basename(value) if value else "None"
                self.state_text.insert(tk.END, f"[{key}]\n -> {short_val}\n\n")
                
                # Update input vars and labels in run tab
                for step_idx, vars_list in self.step_input_vars.items():
                    for var, slot in vars_list:
                        if slot == key:
                            if var.get() != value:
                                var.set(value)
                    
                    # Always re-check readiness for this step if it uses this slot
                    if key in self.steps[step_idx].get('input_slots', []):
                        ready = True
                        for req in self.steps[step_idx].get('input_slots', []):
                            if not self.pm.project_state.get(req):
                                ready = False
                                break
                        if step_idx in self.step_labels:
                            self.step_labels[step_idx].config(fg=THEME_FG if ready else THEME_ACCENT)
        
        self.state_text.config(state="disabled")

    def refresh_run_tab(self):
        # Clear existing buttons and vars
        for widget in self.run_scroll_frame.winfo_children():
            widget.destroy()
        self.step_input_vars = {}
        self.step_labels = {}
        self.step_prompt_vars = {}

        header = tk.Label(self.run_scroll_frame, text="/// SEQUENCE INITIATION ///", bg=THEME_BG, fg=THEME_ACCENT, font=("Consolas", 16, "bold"))
        header.pack(pady=10)

        current_sim = self.sim_var.get()

        display_idx = 1
        for idx, step in enumerate(self.steps):
            # Simulation Filter
            step_sims = step.get('sims', [])
            if step_sims and current_sim not in step_sims:
                continue

            frame = tk.Frame(self.run_scroll_frame, bg=THEME_BG, pady=5)
            frame.pack(fill="x", pady=5)

            # Label
            lbl = tk.Label(frame, text=f"[{display_idx}] {step.get('name', 'Unknown')}", bg=THEME_BG, fg=THEME_FG, width=25, anchor="w", font=("Consolas", 10, "bold"))
            lbl.pack(side="left")
            self.step_labels[idx] = lbl
            display_idx += 1

            # Input Selection
            input_slots = step.get('input_slots', [])
            self.step_input_vars[idx] = []
            
            if input_slots:
                inputs_frame = tk.Frame(frame, bg=THEME_BG)
                inputs_frame.pack(side="left", padx=5)
                
                for slot_idx, slot in enumerate(input_slots):
                    slot_frame = tk.Frame(inputs_frame, bg=THEME_BG)
                    slot_frame.pack(fill="x", pady=1)
                    
                    input_var = tk.StringVar()
                    val = self.pm.project_state.get(slot, "")
                    input_var.set(val)
                    self.step_input_vars[idx].append((input_var, slot))
                    
                    # Trace changes to update project state
                    input_var.trace_add("write", lambda *a, v=input_var, s=slot: self.update_project_slot(s, v.get()))

                    entry = tk.Entry(slot_frame, textvariable=input_var, bg="#333", fg="white", font=("Consolas", 8), width=35)
                    entry.pack(side="left")
                    
                    btn_browse = tk.Button(slot_frame, text="...", bg=THEME_BUTTON_BG, fg=THEME_FG, font=("Consolas", 8),
                                           command=lambda i=idx, si=slot_idx: self.browse_for_step_input(i, si))
                    btn_browse.pack(side="left", padx=2)
            else:
                # Placeholder for steps without input
                tk.Label(frame, text="(No input required)", bg=THEME_BG, fg="#555", font=("Consolas", 8), width=40).pack(side="left", padx=5)

            # Prompt Selection (if configured)
            prompt_folder = step.get('prompt_folder')
            if prompt_folder:
                abs_prompt_folder = os.path.join(os.path.abspath(step.get('working_dir', '.')), prompt_folder)
                prompts = []
                if os.path.exists(abs_prompt_folder):
                    prompts = [f for f in os.listdir(abs_prompt_folder) if f.endswith('.txt')]
                
                if not prompts:
                    prompts = ["default.txt"]
                
                prompt_var = tk.StringVar(value=prompts[0])
                self.step_prompt_vars[idx] = (prompt_var, abs_prompt_folder)
                
                prompt_cb = ttk.Combobox(frame, textvariable=prompt_var, values=prompts, state="readonly", width=25)
                prompt_cb.pack(side="left", padx=5)

            # Run Button
            btn = tk.Button(frame, text="INITIALIZE", bg=THEME_BUTTON_BG, fg=THEME_FG, font=("Consolas", 10, "bold"),
                            command=lambda s=step, i=idx: self.run_script(s, i), relief="flat", padx=15)
            btn.pack(side="right")

            # Readiness Visual
            ready = True
            if input_slots:
                for req in input_slots:
                    if not self.pm.project_state.get(req):
                        ready = False
                        break
            if not ready:
                lbl.config(fg=THEME_ACCENT)
            else:
                lbl.config(fg=THEME_FG)

    def update_project_slot(self, slot, value):
        if self.pm.project_state.get(slot) != value:
            self.pm.project_state[slot] = value
            self.pm.save_project_state()
            self.refresh_state_display()

    def browse_for_step_input(self, step_idx, slot_idx):
        initial_dir = self.pm.get_project_path() or "."
        filename = filedialog.askopenfilename(initialdir=initial_dir)
        if filename:
            var, _ = self.step_input_vars[step_idx][slot_idx]
            var.set(os.path.abspath(filename))

    def run_script(self, step, step_idx=None):
        script_path = step.get('script_path')
        work_dir = step.get('working_dir')
        input_slots = step.get('input_slots', [])
        
        if not script_path:
            self.status("Error: No script path defined.")
            return

        # Resolve paths
        abs_script_path = os.path.abspath(script_path)
        abs_work_dir = os.path.abspath(work_dir) if work_dir else os.path.dirname(abs_script_path)
        
        project_path = self.pm.get_project_path()
        if not project_path:
            messagebox.showwarning("Warning", "No active project selected.")
            # We can still run, but environment might be limited
        
        # Resolve Input Files
        input_files = []
        if input_slots:
            for slot in input_slots:
                val = self.pm.project_state.get(slot)
                if val and os.path.exists(val):
                    input_files.append(val)
            
            if not input_files:
                # If mandatory, warn user? Or ask them to pick?
                # For now, warn but allow run (script might handle it or fail)
                resp = messagebox.askyesno("Missing Input", f"Could not find valid input for slots: {input_slots}\nRun anyway?")
                if not resp:
                    return

        if not os.path.exists(abs_script_path):
            messagebox.showerror("Error", f"Script not found:\n{abs_script_path}")
            return

        self.status(f"Launching {step.get('name')}...")
        
        # Prepare Environment
        env = os.environ.copy()
        if project_path:
            env["R3E_PROJECT_PATH"] = project_path
        if input_files:
            env["R3E_INPUT_FILE"] = input_files[0]
            print(f"Passed inputs: {input_files}")
            
        # Add Prompt File if selected
        if step_idx is not None and step_idx in getattr(self, 'step_prompt_vars', {}):
            prompt_var, abs_prompt_folder = self.step_prompt_vars[step_idx]
            selected_prompt = prompt_var.get()
            if selected_prompt:
                env["GEMINI_PROMPT_FILE"] = os.path.join(abs_prompt_folder, selected_prompt)
                
        # Add output settings if they exist
        if "output_prefix" in step:
            env["GEMINI_OUTPUT_PREFIX"] = step["output_prefix"]
        if "output_extension" in step:
            env["GEMINI_OUTPUT_SUFFIX"] = step["output_extension"]
            
        # Add Global API Key
        api_key = self.global_settings.get("gemini_api_key")
        if api_key:
            env["GEMINI_API_KEY"] = api_key
            
        # Add Global Gemini Model
        gemini_model = self.global_settings.get("gemini_model")
        if gemini_model:
            env["GEMINI_MODEL_NAME"] = gemini_model

        # CLI Args
        args = [sys.executable, abs_script_path]
        if input_files:
             args.extend(input_files)

        try:
            # Launch
            if sys.platform == "win32":
                cmd = ["cmd.exe", "/K"] + args
                subprocess.Popen(cmd, cwd=abs_work_dir, creationflags=subprocess.CREATE_NEW_CONSOLE, env=env)
            else:
                subprocess.Popen(args, cwd=abs_work_dir, env=env)
            
            # Post-Launch: Suggest scanning
            self.status("Process launched. Click 'SCAN FILES' after completion to update state.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch:\n{e}")

    def build_settings_tab(self):
        # List of steps to edit
        self.settings_frame = ttk.Frame(self.tab_settings)
        self.settings_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # --- GLOBAL SETTINGS ---
        global_frame = tk.LabelFrame(self.settings_frame, text="GLOBAL SETTINGS", bg=THEME_BG, fg=THEME_ACCENT, padx=10, pady=10)
        global_frame.pack(fill="x", pady=(0, 20))
        
        tk.Label(global_frame, text="Gemini API Key:", bg=THEME_BG, fg=THEME_FG).grid(row=0, column=0, sticky="w")
        self.api_key_var = tk.StringVar(value=self.global_settings.get("gemini_api_key", ""))
        self.api_key_var.trace_add("write", lambda *a: self.update_global_setting("gemini_api_key", self.api_key_var.get()))
        tk.Entry(global_frame, textvariable=self.api_key_var, bg="#333", fg="white", width=60, show="*").grid(row=0, column=1, padx=10, pady=2, sticky="w")

        tk.Label(global_frame, text="Gemini Model:", bg=THEME_BG, fg=THEME_FG).grid(row=1, column=0, sticky="w")
        self.model_var = tk.StringVar(value=self.global_settings.get("gemini_model", "gemini-3.1-pro-preview"))
        self.model_var.trace_add("write", lambda *a: self.update_global_setting("gemini_model", self.model_var.get()))
        tk.Entry(global_frame, textvariable=self.model_var, bg="#333", fg="white", width=60).grid(row=1, column=1, padx=10, pady=2, sticky="w")

        # Control Buttons
        ctrl_frame = ttk.Frame(self.settings_frame)
        ctrl_frame.pack(fill="x", pady=10)
        
        ttk.Button(ctrl_frame, text="Add Step", command=self.add_step).pack(side="left", padx=5)
        ttk.Button(ctrl_frame, text="Save Config", command=self.save_config).pack(side="right", padx=5)
        ttk.Button(ctrl_frame, text="Reload Config", command=lambda: [self.load_config(), self.refresh_settings_list(), self.refresh_run_tab()]).pack(side="right", padx=5)

        # List Area
        self.list_canvas = tk.Canvas(self.settings_frame, bg=THEME_BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.settings_frame, orient="vertical", command=self.list_canvas.yview)
        self.edit_frame = ttk.Frame(self.list_canvas)

        self.edit_frame.bind(
            "<Configure>",
            lambda e: self.list_canvas.configure(
                scrollregion=self.list_canvas.bbox("all")
            )
        )

        self.list_canvas.create_window((0, 0), window=self.edit_frame, anchor="nw")
        self.list_canvas.configure(yscrollcommand=self.scrollbar.set)

        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        self.refresh_settings_list()

    def refresh_settings_list(self):
        for widget in self.edit_frame.winfo_children():
            widget.destroy()
            
        for idx, step in enumerate(self.steps):
            self.create_step_editor(idx, step)

    def create_step_editor(self, idx, step):
        frame = tk.LabelFrame(self.edit_frame, text=f"Step {idx+1}", bg=THEME_BG, fg=THEME_ACCENT, padx=10, pady=5)
        frame.pack(fill="x", pady=5, padx=5)
        
        # Helper to create label/entry pair
        def add_field(row, label, key):
            tk.Label(frame, text=label, bg=THEME_BG, fg="#888").grid(row=row, column=0, sticky="e")
            val = step.get(key, "")
            if isinstance(val, list): val = ",".join(val)
            var = tk.StringVar(value=str(val))
            entry = tk.Entry(frame, textvariable=var, bg="#333", fg="white", insertbackground="white")
            entry.grid(row=row, column=1, sticky="ew", padx=5)
            
            # Special handler for lists
            if key in ("input_slots", "sims"):
                 var.trace_add("write", lambda *a: self.update_step_list(idx, key, var.get()))
            else:
                 var.trace_add("write", lambda *a: self.update_step(idx, key, var.get()))

        add_field(0, "Name:", "name")
        add_field(1, "Sims (comma sep):", "sims")
        add_field(2, "Script:", "script_path")
        add_field(3, "Work Dir:", "working_dir")
        add_field(4, "Inputs (comma sep):", "input_slots")
        add_field(5, "Output Slot:", "output_slot")
        add_field(6, "Out Folder:", "output_folder")
        add_field(7, "Out Ext:", "output_extension")

        # Buttons
        btn_frame = tk.Frame(frame, bg=THEME_BG)
        btn_frame.grid(row=0, column=2, rowspan=8, padx=5)
        
        tk.Button(btn_frame, text="Del", bg="#500", fg="white", command=lambda: self.delete_step(idx)).pack(fill="x", pady=2)
        tk.Button(btn_frame, text="Up", bg="#333", fg="white", command=lambda: self.move_step(idx, -1)).pack(fill="x", pady=2)
        tk.Button(btn_frame, text="Dn", bg="#333", fg="white", command=lambda: self.move_step(idx, 1)).pack(fill="x", pady=2)

        frame.columnconfigure(1, weight=1)

    def update_step(self, idx, key, value):
        if 0 <= idx < len(self.steps):
            self.steps[idx][key] = value

    def update_step_list(self, idx, key, value):
        if 0 <= idx < len(self.steps):
            self.steps[idx][key] = [x.strip() for x in value.split(",") if x.strip()]

    def add_step(self):
        self.steps.append({
            "name": "New Step",
            "script_path": "",
            "working_dir": "",
            "input_slots": [],
            "output_slot": "",
            "output_folder": "",
            "output_extension": ""
        })
        self.refresh_settings_list()

    def delete_step(self, idx):
        if 0 <= idx < len(self.steps):
            del self.steps[idx]
            self.refresh_settings_list()

    def move_step(self, idx, direction):
        new_idx = idx + direction
        if 0 <= new_idx < len(self.steps):
            self.steps[idx], self.steps[new_idx] = self.steps[new_idx], self.steps[idx]
            self.refresh_settings_list()

    def status(self, msg):
        self.footer.config(text=f">> {msg}")
        print(msg)

if __name__ == "__main__":
    root = tk.Tk()
    app = BadAILauncher(root)
    root.mainloop()
