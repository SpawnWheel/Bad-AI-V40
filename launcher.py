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

# --- Modern Dark Theme ---
THEME_BG = "#0f1117"
THEME_BG_CARD = "#1a1d27"
THEME_BG_INPUT = "#252836"
THEME_FG = "#e2e4e9"
THEME_FG_DIM = "#6b7280"
THEME_FG_MUTED = "#9ca3af"
THEME_ACCENT = "#6366f1"       # Indigo
THEME_ACCENT_LIGHT = "#818cf8"
THEME_ACCENT_PURPLE = "#8b5cf6"
THEME_SUCCESS = "#22c55e"
THEME_SUCCESS_BG = "#0f2918"
THEME_WARNING = "#f59e0b"
THEME_DANGER = "#ef4444"
THEME_BUTTON_BG = "#252836"
THEME_BUTTON_HOVER = "#363a4d"
THEME_BORDER = "#2d3142"
THEME_READY_BG = "#111827"
THEME_READY_BORDER = "#22c55e"

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"


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
        """Scans the project folders for the latest files based on config definitions.
        
        Respects pipeline order: a step's output is only scanned if all of
        its input slots are already populated in the project state.
        """
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

            # Pipeline ordering: only scan for this step's output if all
            # its input slots are already satisfied in the project state
            input_slots = step.get('input_slots', [])
            if input_slots:
                inputs_ready = all(self.project_state.get(slot) for slot in input_slots)
                if not inputs_ready:
                    continue

            output_slot = step.get('output_slot')
            output_folder = step.get('output_folder')
            output_ext = step.get('output_extension')
            output_prefix = step.get('output_prefix', '')

            if output_slot:
                target_dir = os.path.join(proj_path, output_folder) if output_folder else proj_path
                if not os.path.exists(target_dir):
                    continue

                if output_ext == "FOLDER":
                    # For folder outputs, point to the folder itself if it has contents
                    if os.listdir(target_dir):
                         if self.project_state.get(output_slot) != target_dir:
                            self.project_state[output_slot] = target_dir
                            changes = True
                else:
                    # File scanning with extension and optional prefix matching
                    files = []
                    for f in os.listdir(target_dir):
                        if output_ext and not f.endswith(output_ext):
                            continue
                        if output_prefix and output_prefix not in f:
                            continue
                        files.append(os.path.join(target_dir, f))
                    
                    if files:
                        latest_file = max(files, key=os.path.getmtime)
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
        self.root.title("BAD AI — Commentary Suite")
        self.root.geometry("1050x800")
        self.root.configure(bg=THEME_BG)
        self.root.minsize(800, 600)

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
        self.style.configure("TLabel", background=THEME_BG, foreground=THEME_FG, font=(FONT_FAMILY, 10))
        self.style.configure("TButton", background=THEME_BUTTON_BG, foreground=THEME_FG, font=(FONT_FAMILY, 10), borderwidth=0, padding=(12, 6))
        self.style.map("TButton", background=[('active', THEME_BUTTON_HOVER)])
        self.style.configure("TNotebook", background=THEME_BG, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=THEME_BG_CARD, foreground=THEME_FG_DIM, padding=[16, 8], font=(FONT_FAMILY, 10, "bold"))
        self.style.map("TNotebook.Tab", background=[('selected', THEME_ACCENT)], foreground=[('selected', '#ffffff')])
        self.style.configure("TCombobox", fieldbackground=THEME_BG_INPUT, background=THEME_BG_INPUT, foreground=THEME_FG, borderwidth=0)
        self.style.map("TCombobox", fieldbackground=[('readonly', THEME_BG_INPUT)])

        # --- Header Bar ---
        self.header_bar = tk.Frame(root, bg=THEME_BG_CARD, height=56)
        self.header_bar.pack(fill='x')
        self.header_bar.pack_propagate(False)

        title_frame = tk.Frame(self.header_bar, bg=THEME_BG_CARD)
        title_frame.pack(side="left", padx=20, pady=8)
        tk.Label(title_frame, text="BAD AI", bg=THEME_BG_CARD, fg=THEME_ACCENT, font=(FONT_FAMILY, 18, "bold")).pack(side="left")
        tk.Label(title_frame, text="  Commentary Suite", bg=THEME_BG_CARD, fg=THEME_FG_DIM, font=(FONT_FAMILY, 12)).pack(side="left", pady=(4, 0))

        # --- Project Selection Bar ---
        self.project_frame = tk.Frame(root, bg=THEME_BG, pady=12)
        self.project_frame.pack(fill='x', padx=20)

        # Project selector
        proj_left = tk.Frame(self.project_frame, bg=THEME_BG)
        proj_left.pack(side="left")
        
        tk.Label(proj_left, text="Project", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9)).pack(side="left", padx=(0, 8))
        
        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(proj_left, textvariable=self.project_var, state="readonly", width=35)
        self.project_combo.pack(side="left", padx=(0, 12))
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_change)

        tk.Label(proj_left, text="Sim", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9)).pack(side="left", padx=(8, 8))
        self.sim_var = tk.StringVar(value="Raceroom")
        self.sim_combo = ttk.Combobox(proj_left, textvariable=self.sim_var, values=["Raceroom", "AMS2"], state="readonly", width=10)
        self.sim_combo.pack(side="left")
        self.sim_combo.bind("<<ComboboxSelected>>", self.on_sim_change)

        # Action buttons
        proj_right = tk.Frame(self.project_frame, bg=THEME_BG)
        proj_right.pack(side="right")

        for text, cmd in [("New Project", self.create_project), 
                          ("Open Folder", self.open_project_folder),
                          ("Project Notes", self.edit_project_notes),
                          ("Scan Files", self.scan_files)]:
            btn = tk.Button(proj_right, text=text, bg=THEME_BUTTON_BG, fg=THEME_FG,
                           font=(FONT_FAMILY, 9), command=cmd, relief="flat",
                           padx=12, pady=4, cursor="hand2",
                           activebackground=THEME_BUTTON_HOVER, activeforeground=THEME_FG,
                           bd=0, highlightthickness=0)
            btn.pack(side="left", padx=3)
            self._bind_hover(btn, THEME_BUTTON_BG, THEME_BUTTON_HOVER)

        # Separator
        tk.Frame(root, bg=THEME_BORDER, height=1).pack(fill='x', padx=20)

        # Layout - Notebook
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True, padx=20, pady=(12, 8))

        self.tab_run = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_run, text="  Pipeline  ")
        self.notebook.add(self.tab_settings, text="  Settings  ")

        self.build_run_tab()
        self.build_settings_tab()
        
        # Footer
        self.footer_frame = tk.Frame(root, bg=THEME_BG_CARD, height=32)
        self.footer_frame.pack(side="bottom", fill="x")
        self.footer_frame.pack_propagate(False)
        self.footer = tk.Label(self.footer_frame, text="Ready", bg=THEME_BG_CARD, fg=THEME_FG_DIM, font=(FONT_FAMILY, 9), anchor="w", padx=20)
        self.footer.pack(fill="both", expand=True)

        self.update_project_list()

        # Auto-refresh project state every 5 seconds to pick up changes
        # made by subprocess tools (e.g. camera controller writing director notes)
        self._last_project_state_mtime = 0
        self._start_auto_refresh()

    def _bind_hover(self, widget, normal_bg, hover_bg):
        """Add hover color effect to a widget."""
        widget.bind("<Enter>", lambda e: widget.config(bg=hover_bg))
        widget.bind("<Leave>", lambda e: widget.config(bg=normal_bg))

    def _start_auto_refresh(self):
        """Periodically check if project_state.json was modified externally and refresh."""
        try:
            proj_path = self.pm.get_project_path()
            if proj_path:
                state_path = os.path.join(proj_path, "project_state.json")
                if os.path.exists(state_path):
                    mtime = os.path.getmtime(state_path)
                    if mtime > self._last_project_state_mtime:
                        self._last_project_state_mtime = mtime
                        self.pm.load_project_state()
                        self.refresh_state_display()
        except Exception:
            pass
        self.root.after(5000, self._start_auto_refresh)

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
        # Scrollable pipeline — full width, no side panel
        canvas = tk.Canvas(self.tab_run, bg=THEME_BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(self.tab_run, orient="vertical", command=canvas.yview)
        
        self.run_scroll_frame = tk.Frame(canvas, bg=THEME_BG)
        self.run_scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=self.run_scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Bind mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        canvas.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        scrollbar.pack(side="right", fill="y", pady=10)
        
        self.refresh_run_tab()

    def refresh_state_display(self):
        """Update input vars in run tab from project state."""
        if not self.pm.project_state:
            return

        for key, value in self.pm.project_state.items():
            # Update input vars and labels in run tab
            for step_idx, vars_list in self.step_input_vars.items():
                for var, slot in vars_list:
                    if slot == key:
                        if var.get() != value:
                            var.set(value)
                
                # Always re-check readiness for this step if it uses this slot
                if key in self.steps[step_idx].get('input_slots', []):
                    self._update_step_readiness(step_idx, self.steps[step_idx])

    def _slot_display_name(self, slot):
        """Convert a slot key like 'raw_log' to a readable label 'Raw Log'."""
        return slot.replace('_', ' ').title()

    def _create_tooltip(self, widget, text_func):
        """Attach a hover tooltip to a widget. text_func() returns the tooltip string."""
        tip_window = [None]
        def show(event):
            text = text_func()
            if not text:
                return
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 2
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            label = tk.Label(tw, text=text, bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_MONO, 8),
                             relief="solid", borderwidth=1, padx=6, pady=3)
            label.pack()
            tip_window[0] = tw
        def hide(event):
            if tip_window[0]:
                tip_window[0].destroy()
                tip_window[0] = None
        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def refresh_run_tab(self):
        # Clear existing buttons and vars
        for widget in self.run_scroll_frame.winfo_children():
            widget.destroy()
        self.step_input_vars = {}
        self.step_labels = {}
        self.step_prompt_vars = {}
        self.step_frames = {}

        current_sim = self.sim_var.get()

        display_idx = 1
        for idx, step in enumerate(self.steps):
            # Simulation Filter
            step_sims = step.get('sims', [])
            if step_sims and current_sim not in step_sims:
                continue

            has_prompt = bool(step.get('prompt_folder'))

            # --- Card Frame ---
            card = tk.Frame(self.run_scroll_frame, bg=THEME_BG_CARD, padx=16, pady=12,
                           highlightbackground=THEME_BORDER, highlightthickness=1)
            card.pack(fill="x", pady=4, padx=4)
            self.step_frames[idx] = card

            # Top row: step number + name + buttons
            top_row = tk.Frame(card, bg=THEME_BG_CARD)
            top_row.pack(fill="x")

            # Step number badge
            badge = tk.Label(top_row, text=f" {display_idx} ", bg=THEME_ACCENT, fg="#ffffff",
                           font=(FONT_FAMILY, 9, "bold"), padx=6, pady=1)
            badge.pack(side="left", padx=(0, 10))

            # Step name
            lbl = tk.Label(top_row, text=step.get('name', 'Unknown'), bg=THEME_BG_CARD, fg=THEME_FG,
                          font=(FONT_FAMILY, 11, "bold"), anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            self.step_labels[idx] = lbl

            # Buttons on the right
            btn_frame = tk.Frame(top_row, bg=THEME_BG_CARD)
            btn_frame.pack(side="right")

            # Copy to Clipboard button (only for AI steps with prompt_folder)
            if has_prompt:
                copy_btn = tk.Button(btn_frame, text="\U0001F4CB Copy Prompt", bg=THEME_BG_INPUT, fg=THEME_ACCENT_LIGHT,
                                    font=(FONT_FAMILY, 9), relief="flat", padx=10, pady=4, cursor="hand2",
                                    activebackground=THEME_BUTTON_HOVER, activeforeground=THEME_ACCENT_LIGHT,
                                    bd=0, highlightthickness=0,
                                    command=lambda s=step, i=idx: self.copy_ai_prompt_to_clipboard(s, i))
                copy_btn.pack(side="left", padx=(0, 6))
                self._bind_hover(copy_btn, THEME_BG_INPUT, THEME_BUTTON_HOVER)

            # Run button
            run_btn = tk.Button(btn_frame, text="\u25B6  Run", bg=THEME_ACCENT, fg="#ffffff",
                               font=(FONT_FAMILY, 10, "bold"), relief="flat", padx=16, pady=4,
                               cursor="hand2", activebackground=THEME_ACCENT_LIGHT, activeforeground="#ffffff",
                               bd=0, highlightthickness=0,
                               command=lambda s=step, i=idx: self.run_script(s, i))
            run_btn.pack(side="left")
            self._bind_hover(run_btn, THEME_ACCENT, THEME_ACCENT_LIGHT)

            display_idx += 1

            # Bottom row: inputs
            input_slots = step.get('input_slots', [])
            input_labels = step.get('input_labels', [])
            self.step_input_vars[idx] = []
            
            if input_slots:
                inputs_frame = tk.Frame(card, bg=THEME_BG_CARD)
                inputs_frame.pack(fill="x", pady=(8, 0))
                
                for slot_idx, slot in enumerate(input_slots):
                    slot_frame = tk.Frame(inputs_frame, bg=THEME_BG_CARD)
                    slot_frame.pack(fill="x", pady=2)
                    
                    # Descriptive label for the slot
                    label_text = input_labels[slot_idx] if slot_idx < len(input_labels) else self._slot_display_name(slot)
                    slot_lbl = tk.Label(slot_frame, text=f"{label_text}:", bg=THEME_BG_CARD, fg=THEME_FG_DIM,
                                       font=(FONT_FAMILY, 9), anchor="w", width=22)
                    slot_lbl.pack(side="left")

                    # Hidden var stores the full path
                    input_var = tk.StringVar()
                    val = self.pm.project_state.get(slot, "")
                    input_var.set(val)
                    self.step_input_vars[idx].append((input_var, slot))
                    
                    # Trace changes to update project state
                    input_var.trace_add("write", lambda *a, v=input_var, s=slot: self.update_project_slot(s, v.get()))

                    # Display var shows filename only
                    display_var = tk.StringVar()
                    if val:
                        display_var.set(os.path.basename(val))
                    else:
                        display_var.set("(select file...)" if slot == "video_file" else "(waiting...)")

                    entry = tk.Entry(slot_frame, textvariable=display_var, bg=THEME_BG_INPUT,
                                     fg=THEME_FG if val else THEME_FG_DIM,
                                     font=(FONT_MONO, 9), width=40, state="readonly",
                                     readonlybackground=THEME_BG_INPUT, cursor="arrow",
                                     relief="flat", bd=0, highlightthickness=0)
                    entry.pack(side="left", padx=(0, 4))
                    
                    # Tooltip shows full path on hover
                    self._create_tooltip(entry, lambda v=input_var: v.get() if v.get() else None)

                    # Keep references for updating display when var changes
                    input_var.trace_add("write", lambda *a, dv=display_var, iv=input_var, e=entry, sl=slot: self._update_display_var(dv, iv, e, sl))
                    
                    btn_browse = tk.Button(slot_frame, text="...", bg=THEME_BG_INPUT, fg=THEME_FG_MUTED,
                                          font=(FONT_MONO, 9), relief="flat", padx=6, cursor="hand2",
                                          activebackground=THEME_BUTTON_HOVER,
                                          bd=0, highlightthickness=0,
                                          command=lambda i=idx, si=slot_idx: self.browse_for_step_input(i, si))
                    btn_browse.pack(side="left")
                    self._bind_hover(btn_browse, THEME_BG_INPUT, THEME_BUTTON_HOVER)

            # Prompt Selection (if configured)
            prompt_folder = step.get('prompt_folder')
            if prompt_folder:
                prompt_frame = tk.Frame(card, bg=THEME_BG_CARD)
                prompt_frame.pack(fill="x", pady=(6, 0))

                abs_prompt_folder = os.path.join(os.path.abspath(step.get('working_dir', '.')), prompt_folder)
                prompts = []
                if os.path.exists(abs_prompt_folder):
                    prompts = [f for f in os.listdir(abs_prompt_folder) if f.endswith('.txt')]
                
                if not prompts:
                    prompts = ["default.txt"]
                
                prompt_var = tk.StringVar(value=prompts[0])
                self.step_prompt_vars[idx] = (prompt_var, abs_prompt_folder)

                tk.Label(prompt_frame, text="Prompt:", bg=THEME_BG_CARD, fg=THEME_FG_DIM,
                        font=(FONT_FAMILY, 9), anchor="w", width=22).pack(side="left")
                
                prompt_cb = ttk.Combobox(prompt_frame, textvariable=prompt_var, values=prompts, state="readonly", width=30)
                prompt_cb.pack(side="left")

            # Readiness Visual
            self._update_step_readiness(idx, step)

    def _update_display_var(self, display_var, input_var, entry, slot):
        """Sync the display entry to show basename when the hidden input_var changes."""
        val = input_var.get()
        if val:
            display_var.set(os.path.basename(val))
            entry.config(fg=THEME_FG)
        else:
            display_var.set("(select file...)" if slot == "video_file" else "(waiting...)")
            entry.config(fg=THEME_FG_DIM)

    def _update_step_readiness(self, idx, step):
        """Update the visual readiness state of a step row."""
        input_slots = step.get('input_slots', [])
        ready = True
        if input_slots:
            for req in input_slots:
                if not self.pm.project_state.get(req):
                    ready = False
                    break

        if idx in self.step_frames:
            if ready:
                self.step_frames[idx].config(bg=THEME_READY_BG, highlightbackground=THEME_SUCCESS)
                if idx in self.step_labels:
                    self.step_labels[idx].config(bg=THEME_READY_BG, fg=THEME_SUCCESS)
                # Update child widget backgrounds
                for child in self.step_frames[idx].winfo_children():
                    try:
                        child.config(bg=THEME_READY_BG)
                        # Recurse into child frames
                        for subchild in child.winfo_children():
                            try:
                                if isinstance(subchild, (tk.Label, tk.Frame)):
                                    subchild.config(bg=THEME_READY_BG)
                            except:
                                pass
                    except:
                        pass
            else:
                self.step_frames[idx].config(bg=THEME_BG_CARD, highlightbackground=THEME_BORDER)
                if idx in self.step_labels:
                    self.step_labels[idx].config(bg=THEME_BG_CARD, fg=THEME_FG)

    def update_project_slot(self, slot, value):
        if self.pm.project_state.get(slot) != value:
            self.pm.project_state[slot] = value
            self.pm.save_project_state()
            self.refresh_state_display()

    def browse_for_step_input(self, step_idx, slot_idx):
        initial_dir = self.pm.get_project_path() or "."
        var, slot_name = self.step_input_vars[step_idx][slot_idx]
        
        if slot_name.lower().endswith("_folder") or slot_name.lower().endswith("_dir"):
            selected_path = filedialog.askdirectory(initialdir=initial_dir)
        else:
            selected_path = filedialog.askopenfilename(initialdir=initial_dir, filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
            
        if selected_path:
            var.set(os.path.abspath(selected_path))

    def copy_ai_prompt_to_clipboard(self, step, step_idx):
        """Assemble the full prompt that would be sent to the AI and copy it to clipboard.
        
        This mirrors the logic in gemini_task.py: prompt_text + extra_context + data_content
        """
        # 1. Resolve the prompt file
        if step_idx not in self.step_prompt_vars:
            self.status("Error: No prompt configured for this step.")
            return
        
        prompt_var, abs_prompt_folder = self.step_prompt_vars[step_idx]
        selected_prompt = prompt_var.get()
        prompt_file = os.path.join(abs_prompt_folder, selected_prompt)
        
        if not os.path.exists(prompt_file):
            messagebox.showerror("Error", f"Prompt file not found:\n{prompt_file}")
            return
        
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_text = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read prompt file:\n{e}")
            return
        
        # 2. Resolve the input data file
        input_slots = step.get('input_slots', [])
        data_content = ""
        
        if input_slots:
            data_filename = self.pm.project_state.get(input_slots[0])
            if data_filename and os.path.exists(data_filename):
                try:
                    with open(data_filename, 'r', encoding='utf-8') as f:
                        data_content = f.read()
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to read input file:\n{e}")
                    return
            else:
                messagebox.showwarning("Warning", f"Input file for slot '{input_slots[0]}' not found.\nCopying prompt only (without data).")
        
        # 3. Read project notes (extra context)
        extra_context = ""
        project_path = self.pm.get_project_path()
        
        if project_path:
            notes_path = os.path.join(project_path, "project_notes.txt")
            if os.path.exists(notes_path):
                try:
                    with open(notes_path, 'r', encoding='utf-8') as f:
                        notes_content = f.read().strip()
                        if notes_content:
                            extra_context = f"\n\nProject Specific Instructions:\n{notes_content}"
                except Exception:
                    pass
        
        # 4. Assemble (same as gemini_task.py line 70)
        full_prompt = f"{prompt_text}{extra_context}\n\n{data_content}"
        
        # 5. Copy to clipboard
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(full_prompt)
            self.root.update()  # Required to keep clipboard after potential focus loss
            
            char_count = len(full_prompt)
            self.status(f"Copied to clipboard — {char_count:,} characters ({step.get('name', 'AI Step')})")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy to clipboard:\n{e}")

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
            self.status("Process launched. Click 'Scan Files' after completion to update state.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch:\n{e}")

    def build_settings_tab(self):
        self.settings_frame = ttk.Frame(self.tab_settings)
        self.settings_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # --- API Settings Card ---
        api_card = tk.Frame(self.settings_frame, bg=THEME_BG_CARD, padx=20, pady=20,
                           highlightbackground=THEME_BORDER, highlightthickness=1)
        api_card.pack(fill="x", pady=(0, 16))
        
        tk.Label(api_card, text="API Configuration", bg=THEME_BG_CARD, fg=THEME_FG,
                font=(FONT_FAMILY, 13, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        
        # API Key
        tk.Label(api_card, text="Gemini API Key", bg=THEME_BG_CARD, fg=THEME_FG_MUTED,
                font=(FONT_FAMILY, 10)).grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.api_key_var = tk.StringVar(value=self.global_settings.get("gemini_api_key", ""))
        self.api_key_var.trace_add("write", lambda *a: self.update_global_setting("gemini_api_key", self.api_key_var.get()))
        api_entry = tk.Entry(api_card, textvariable=self.api_key_var, bg=THEME_BG_INPUT, fg=THEME_FG,
                            width=60, show="\u2022", font=(FONT_MONO, 10), relief="flat", bd=0,
                            insertbackground=THEME_FG, highlightthickness=0)
        api_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 12), ipady=4)
        
        # Model
        tk.Label(api_card, text="Gemini Model", bg=THEME_BG_CARD, fg=THEME_FG_MUTED,
                font=(FONT_FAMILY, 10)).grid(row=3, column=0, sticky="w", pady=(0, 4))
        self.model_var = tk.StringVar(value=self.global_settings.get("gemini_model", "gemini-3.1-pro-preview"))
        self.model_var.trace_add("write", lambda *a: self.update_global_setting("gemini_model", self.model_var.get()))
        model_entry = tk.Entry(api_card, textvariable=self.model_var, bg=THEME_BG_INPUT, fg=THEME_FG,
                              width=60, font=(FONT_MONO, 10), relief="flat", bd=0,
                              insertbackground=THEME_FG, highlightthickness=0)
        model_entry.grid(row=4, column=0, columnspan=2, sticky="ew", ipady=4)
        
        api_card.columnconfigure(0, weight=1)

        # --- Action Buttons ---
        btn_frame = tk.Frame(self.settings_frame, bg=THEME_BG)
        btn_frame.pack(fill="x", pady=(8, 0))

        save_btn = tk.Button(btn_frame, text="Save Configuration", bg=THEME_ACCENT, fg="#ffffff",
                            font=(FONT_FAMILY, 10, "bold"), relief="flat", padx=20, pady=6,
                            cursor="hand2", activebackground=THEME_ACCENT_LIGHT, activeforeground="#ffffff",
                            bd=0, highlightthickness=0, command=self.save_config)
        save_btn.pack(side="right", padx=(8, 0))
        self._bind_hover(save_btn, THEME_ACCENT, THEME_ACCENT_LIGHT)

        reload_btn = tk.Button(btn_frame, text="Reload Configuration", bg=THEME_BUTTON_BG, fg=THEME_FG,
                              font=(FONT_FAMILY, 10), relief="flat", padx=20, pady=6,
                              cursor="hand2", activebackground=THEME_BUTTON_HOVER, activeforeground=THEME_FG,
                              bd=0, highlightthickness=0,
                              command=lambda: [self.load_config(), self.refresh_run_tab()])
        reload_btn.pack(side="right")
        self._bind_hover(reload_btn, THEME_BUTTON_BG, THEME_BUTTON_HOVER)

    def status(self, msg):
        self.footer.config(text=f"  {msg}")
        print(msg)

if __name__ == "__main__":
    root = tk.Tk()
    app = BadAILauncher(root)
    root.mainloop()
