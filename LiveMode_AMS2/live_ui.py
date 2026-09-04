import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import threading
import logging
import datetime
from typing import TYPE_CHECKING, Any, Dict, List

from .config import (
    LiveModeConfig, 
    GEMINI_VOICES, 
    GEMINI_ACCENTS, 
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_UPDATE_PROMPT,
    load_config, 
    save_live_mode_config,
    load_live_mode_presets,
    save_live_mode_preset
)

if TYPE_CHECKING:
    from .orchestrator import LiveOrchestrator

logger = logging.getLogger(__name__)

# --- Modern Dark Theme ---
THEME_BG = '#0f1117'
THEME_BG_CARD = '#1a1d27'
THEME_BG_INPUT = '#252836'
THEME_FG = '#e2e4e9'
THEME_FG_DIM = '#6b7280'
THEME_FG_MUTED = '#9ca3af'
THEME_ACCENT = '#6366f1'
THEME_SUCCESS = '#22c55e'
THEME_WARNING = '#f59e0b'
THEME_DANGER = '#ef4444'
THEME_BORDER = '#2d3142'

# Additional category colours
THEME_BATTLE = '#8b5cf6'
THEME_PENALTY = '#f97316'

FONT_FAMILY = 'Segoe UI'
FONT_MONO = 'Consolas'


class ScrollableFrame(tk.Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, bg=THEME_BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=THEME_BG)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
    def _on_mousewheel(self, event):
        if self.scrollable_frame.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")


class LiveModeUI:
    def __init__(self, orchestrator: 'LiveOrchestrator'):
        self.orchestrator = orchestrator
        self.root = tk.Tk()
        self.root.title("BAD AI — Live Commentary")
        self.root.geometry("1000x700")
        self.root.configure(bg=THEME_BG)
        
        self.current_config = load_config()
        
        self.queue_collapsed = False
        self.queue_items: List[tuple[tk.Frame, tk.Label, tk.Label, tk.Label]] = []
        self._test_voice_requested = False
        self._history_len = 0
        
        self._setup_styles()
        self._setup_ui()
        self._populate_fields(self.current_config)
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self):
        style = ttk.Style()
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=THEME_BG_INPUT, background=THEME_BG_CARD, foreground=THEME_FG, insertcolor=THEME_FG)
        style.configure('TSpinbox', fieldbackground=THEME_BG_INPUT, background=THEME_BG_CARD, foreground=THEME_FG, insertcolor=THEME_FG)
        style.map('TCombobox', fieldbackground=[('readonly', THEME_BG_INPUT)], selectbackground=[('readonly', THEME_ACCENT)])
        
    def _setup_ui(self):
        self.main_paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=THEME_BORDER, sashwidth=2)
        self.main_paned.pack(fill=tk.BOTH, expand=True)
        
        # --- LEFT PANEL (Settings) ---
        self.left_panel = tk.Frame(self.main_paned, bg=THEME_BG, width=400)
        self.main_paned.add(self.left_panel, minsize=350)
        
        settings_title = tk.Label(self.left_panel, text="Settings", font=(FONT_FAMILY, 14, "bold"), bg=THEME_BG, fg=THEME_FG)
        settings_title.pack(anchor=tk.W, padx=15, pady=(15, 10))
        
        self.settings_scroll = ScrollableFrame(self.left_panel, bg=THEME_BG)
        self.settings_scroll.pack(fill=tk.BOTH, expand=True, padx=10)
        
        content = self.settings_scroll.scrollable_frame
        
        def create_label(parent, text):
            return tk.Label(parent, text=text, font=(FONT_FAMILY, 10), bg=THEME_BG, fg=THEME_FG_DIM)
            
        def create_explainer(parent, text):
            return tk.Label(parent, text=text, font=(FONT_FAMILY, 8), bg=THEME_BG, fg=THEME_FG_MUTED, wraplength=350, justify=tk.LEFT)
            
        def create_entry(parent):
            return tk.Entry(parent, font=(FONT_FAMILY, 10), bg=THEME_BG_INPUT, fg=THEME_FG, insertbackground=THEME_FG, relief=tk.FLAT)
            
        def create_text(parent, height):
            return tk.Text(parent, font=(FONT_FAMILY, 10), bg=THEME_BG_INPUT, fg=THEME_FG, insertbackground=THEME_FG, relief=tk.FLAT, height=height, wrap=tk.WORD)

        # Preset Settings
        preset_frame = tk.Frame(content, bg=THEME_BG)
        preset_frame.pack(fill=tk.X, pady=(0, 15))
        
        tk.Label(preset_frame, text="Preset:", font=(FONT_FAMILY, 10, "bold"), bg=THEME_BG, fg=THEME_FG).pack(side=tk.LEFT)
        
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_var, state='readonly', font=(FONT_FAMILY, 9), width=18)
        self.preset_combo.pack(side=tk.LEFT, padx=(5, 5))
        
        self.load_preset_btn = tk.Button(preset_frame, text="Load", font=(FONT_FAMILY, 8), bg=THEME_BG_INPUT, fg=THEME_FG, relief=tk.FLAT, command=self._load_preset)
        self.load_preset_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.save_preset_btn = tk.Button(preset_frame, text="Save As...", font=(FONT_FAMILY, 8), bg=THEME_BG_INPUT, fg=THEME_FG, relief=tk.FLAT, command=self._save_preset)
        self.save_preset_btn.pack(side=tk.LEFT)
        
        self._refresh_presets()

        # Voice Settings
        tk.Label(content, text="Voice Settings", font=(FONT_FAMILY, 11, "bold"), bg=THEME_BG, fg=THEME_FG).pack(anchor=tk.W, pady=(5, 5))
        
        create_label(content, "Voice:").pack(anchor=tk.W)
        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(content, textvariable=self.voice_var, values=GEMINI_VOICES, state='readonly', font=(FONT_FAMILY, 10))
        self.voice_combo.pack(fill=tk.X)
        create_explainer(content, "Select the AI voice personality.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Accent:").pack(anchor=tk.W)
        self.accent_var = tk.StringVar()
        display_accents = ["None" if a == "" else a for a in GEMINI_ACCENTS]
        self.accent_combo = ttk.Combobox(content, textvariable=self.accent_var, values=display_accents, state='readonly', font=(FONT_FAMILY, 10))
        self.accent_combo.pack(fill=tk.X)
        create_explainer(content, "Choose an accent to stylise the voice output.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Commentator Name:").pack(anchor=tk.W)
        self.name_entry = create_entry(content)
        self.name_entry.pack(fill=tk.X)
        create_explainer(content, "The name the AI uses to identify itself.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Commentator Style:").pack(anchor=tk.W)
        self.style_text = create_text(content, 4)
        self.style_text.pack(fill=tk.X)
        create_explainer(content, "Describe the commentator's personality, energy, and background.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Voice Refinement (Director Notes):").pack(anchor=tk.W)
        self.refine_entry = create_entry(content)
        self.refine_entry.pack(fill=tk.X)
        create_explainer(content, "Small adjustments like 'Speak faster' or 'Whisper'.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Additional Race Context:").pack(anchor=tk.W)
        self.race_context_text = create_text(content, 4)
        self.race_context_text.pack(fill=tk.X)
        create_explainer(content, "Provide custom race info (e.g. 'This is the championship finale').").pack(anchor=tk.W, pady=(0, 10))
        
        self.test_voice_btn = tk.Button(content, text="Test Voice", font=(FONT_FAMILY, 9), bg=THEME_BG_CARD, fg=THEME_FG, command=self._on_test_voice, relief=tk.FLAT)
        self.test_voice_btn.pack(anchor=tk.W, pady=(0, 5))
        
        self.test_voice_status = tk.Label(content, text="", font=(FONT_FAMILY, 9), bg=THEME_BG, fg=THEME_SUCCESS)
        self.test_voice_status.pack(anchor=tk.W, pady=(0, 10))

        # Commentary Settings
        tk.Label(content, text="Commentary Settings", font=(FONT_FAMILY, 11, "bold"), bg=THEME_BG, fg=THEME_FG).pack(anchor=tk.W, pady=(5, 5))
        
        create_label(content, "System Prompt (Initialization):").pack(anchor=tk.W)
        self.system_prompt_text = create_text(content, 10)
        self.system_prompt_text.pack(fill=tk.X, pady=(0, 2))
        
        help_text = "Variables: {commentator_name}, {commentator_style}, {user_race_context}, {target_words}"
        tk.Label(content, text=help_text, font=(FONT_FAMILY, 8), bg=THEME_BG, fg=THEME_FG_MUTED, wraplength=350, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))

        create_label(content, "Update Prompt (Every Interval):").pack(anchor=tk.W)
        self.update_prompt_text = create_text(content, 6)
        self.update_prompt_text.pack(fill=tk.X, pady=(0, 2))

        help_text2 = "Variables: {track_name}, {current_lap}, {total_laps}, {leader_name}, {standings}, {recent_events_summary}"
        tk.Label(content, text=help_text2, font=(FONT_FAMILY, 8), bg=THEME_BG, fg=THEME_FG_MUTED, wraplength=350, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Thinking Level:").pack(anchor=tk.W)
        self.thinking_var = tk.StringVar()
        self.thinking_combo = ttk.Combobox(content, textvariable=self.thinking_var, values=["NONE", "LOW", "MEDIUM", "HIGH"], state='readonly', font=(FONT_FAMILY, 10))
        self.thinking_combo.pack(fill=tk.X)
        create_explainer(content, "Higher levels use more tokens but yield smarter commentary.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Max Output Tokens:").pack(anchor=tk.W)
        self.tokens_spin = ttk.Spinbox(content, from_=1000, to=8192, increment=512, font=(FONT_FAMILY, 10))
        self.tokens_spin.pack(fill=tk.X)
        create_explainer(content, "Controls the maximum length of the AI's response.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Target Words:").pack(anchor=tk.W)
        self.words_spin = ttk.Spinbox(content, from_=50, to=300, increment=10, font=(FONT_FAMILY, 10))
        self.words_spin.pack(fill=tk.X)
        create_explainer(content, "Roughly how many words the AI should aim for per update.").pack(anchor=tk.W, pady=(0, 10))
        
        create_label(content, "Leaderboard Interval (minutes):").pack(anchor=tk.W)
        self.interval_spin = ttk.Spinbox(content, from_=1.0, to=10.0, increment=0.5, format="%.1f", font=(FONT_FAMILY, 10))
        self.interval_spin.pack(fill=tk.X)
        create_explainer(content, "How often to send a telemetry update and generate commentary.").pack(anchor=tk.W, pady=(0, 20))
        
        # Left Panel Controls
        left_controls = tk.Frame(self.left_panel, bg=THEME_BG, pady=10, padx=15)
        left_controls.pack(fill=tk.X, side=tk.BOTTOM)
        
        self.save_btn = tk.Button(left_controls, text="Save Settings", font=(FONT_FAMILY, 9, "bold"), bg=THEME_ACCENT, fg="white", activebackground="#4f46e5", activeforeground="white", relief=tk.FLAT, padx=10, pady=5, command=self._save_settings)
        self.save_btn.pack(side=tk.RIGHT, padx=(5, 0))
        
        self.reset_btn = tk.Button(left_controls, text="Reset to Defaults", font=(FONT_FAMILY, 9), bg=THEME_BG_INPUT, fg=THEME_FG, activebackground=THEME_BG_CARD, activeforeground=THEME_FG, relief=tk.FLAT, padx=10, pady=5, command=self._reset_defaults)
        self.reset_btn.pack(side=tk.RIGHT)

        # --- RIGHT PANEL (Live View) ---
        self.right_panel = tk.Frame(self.main_paned, bg=THEME_BG, padx=15, pady=15)
        self.main_paned.add(self.right_panel, minsize=400)
        
        # Header Bar
        self.header_frame = tk.Frame(self.right_panel, bg=THEME_BG)
        self.header_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.header_dot = tk.Canvas(self.header_frame, width=12, height=12, bg=THEME_BG, highlightthickness=0)
        self.header_dot.pack(side=tk.LEFT, padx=(0, 8))
        self.dot_id = self.header_dot.create_oval(2, 2, 10, 10, fill=THEME_FG_DIM, outline="")
        
        self.header_label = tk.Label(self.header_frame, text="BAD AI — Live", font=(FONT_FAMILY, 14, "bold"), bg=THEME_BG, fg=THEME_FG)
        self.header_label.pack(side=tk.LEFT)
        
        self.token_label = tk.Label(self.header_frame, text="0 tokens", font=(FONT_MONO, 9), bg=THEME_BG, fg=THEME_FG_MUTED)
        self.token_label.pack(side=tk.RIGHT, pady=(4, 0))
        
        # Status Section
        self.status_panel = tk.Frame(self.right_panel, bg=THEME_BG_CARD, padx=12, pady=12)
        self.status_panel.configure(highlightbackground=THEME_BORDER, highlightthickness=1)
        self.status_panel.pack(fill=tk.X, pady=(0, 15))
        
        self.state_label = tk.Label(self.status_panel, text="IDLE", font=(FONT_FAMILY, 10, "bold"), bg=THEME_BG_CARD, fg=THEME_FG_DIM)
        self.state_label.pack(anchor=tk.W, pady=(0, 6))
        
        self.event_label = tk.Label(self.status_panel, text="No events yet", font=(FONT_FAMILY, 9), bg=THEME_BG_CARD, fg=THEME_FG, wraplength=500, justify=tk.LEFT)
        self.event_label.pack(anchor=tk.W)
        
        # Commentary History
        history_frame = tk.Frame(self.right_panel, bg=THEME_BG)
        history_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        tk.Label(history_frame, text="Commentary History", font=(FONT_FAMILY, 11, "bold"), bg=THEME_BG, fg=THEME_FG).pack(anchor=tk.W, pady=(0, 5))
        
        self.history_text = tk.Text(history_frame, font=(FONT_FAMILY, 10), bg=THEME_BG_INPUT, fg=THEME_FG, insertbackground=THEME_FG, relief=tk.FLAT, state=tk.DISABLED, wrap=tk.WORD)
        self.history_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        history_scroll = ttk.Scrollbar(history_frame, orient="vertical", command=self.history_text.yview)
        history_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_text.configure(yscrollcommand=history_scroll.set)
        
        # Queue Panel
        self.queue_container = tk.Frame(self.right_panel, bg=THEME_BG)
        self.queue_container.pack(fill=tk.X, pady=(0, 15))
        
        self.queue_header = tk.Frame(self.queue_container, bg=THEME_BG)
        self.queue_header.pack(fill=tk.X)
        
        self.queue_toggle = tk.Label(self.queue_header, text="▼", font=(FONT_FAMILY, 9), bg=THEME_BG, fg=THEME_FG_MUTED, cursor="hand2")
        self.queue_toggle.pack(side=tk.LEFT, padx=(0, 5))
        self.queue_toggle.bind("<Button-1>", self._toggle_queue)
        
        self.queue_title = tk.Label(self.queue_header, text="Event Queue (0)", font=(FONT_FAMILY, 10, "bold"), bg=THEME_BG, fg=THEME_FG)
        self.queue_title.pack(side=tk.LEFT)
        self.queue_title.bind("<Button-1>", self._toggle_queue)
        
        self.queue_content = tk.Frame(self.queue_container, bg=THEME_BG_CARD, padx=8, pady=8)
        self.queue_content.configure(highlightbackground=THEME_BORDER, highlightthickness=1)
        self.queue_content.pack(fill=tk.X, pady=(5, 0))
        
        for _ in range(5):
            item_frame = tk.Frame(self.queue_content, bg=THEME_BG_CARD)
            
            badge = tk.Label(item_frame, text="CAT", font=(FONT_FAMILY, 8, "bold"), bg=THEME_FG_DIM, fg=THEME_BG, width=12)
            badge.pack(side=tk.LEFT, padx=(0, 8))
            
            msg = tk.Label(item_frame, text="-", font=(FONT_FAMILY, 9), bg=THEME_BG_CARD, fg=THEME_FG, anchor=tk.W)
            msg.pack(side=tk.LEFT, fill=tk.X, expand=True)
            
            score = tk.Label(item_frame, text="0.0", font=(FONT_MONO, 9), bg=THEME_BG_CARD, fg=THEME_FG_MUTED)
            score.pack(side=tk.RIGHT, padx=(5, 0))
            
            self.queue_items.append((item_frame, badge, msg, score))
            
        self.controls_frame = tk.Frame(self.right_panel, bg=THEME_BG)
        self.controls_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        self.start_btn = tk.Button(self.controls_frame, text="Start Commentary", font=(FONT_FAMILY, 10, "bold"), bg=THEME_SUCCESS, fg="white", activebackground="#16a34a", activeforeground="white", relief=tk.FLAT, padx=15, pady=5, command=self._on_start, cursor="hand2")
        self.start_btn.pack(side=tk.LEFT)
        
        self.stop_btn = tk.Button(self.controls_frame, text="Stop Commentary", font=(FONT_FAMILY, 10, "bold"), bg=THEME_DANGER, fg="white", activebackground="#dc2626", activeforeground="white", relief=tk.FLAT, padx=15, pady=5, command=self._on_close, cursor="hand2")
        self.stop_btn.pack(side=tk.RIGHT)
        
        self.pause_btn = tk.Button(self.controls_frame, text="Pause", font=(FONT_FAMILY, 10, "bold"), bg=THEME_WARNING, fg="white", activebackground="#d97706", activeforeground="white", relief=tk.FLAT, padx=15, pady=5, command=self._toggle_pause, cursor="hand2")
        self.pause_btn.pack(side=tk.RIGHT, padx=(0, 10))
        
        self.main_paned.paneconfig(self.left_panel, minsize=350)
        
    def _populate_fields(self, config: LiveModeConfig):
        self.voice_var.set(config.commentator_voice)
        
        accent = config.commentator_accent
        self.accent_var.set("None" if accent == "" else accent)
        
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, config.commentator_name)
        
        self.style_text.delete(1.0, tk.END)
        self.style_text.insert(tk.END, config.commentator_style)
        
        self.refine_entry.delete(0, tk.END)
        self.refine_entry.insert(0, config.voice_refinement_text)
        
        self.race_context_text.delete(1.0, tk.END)
        self.race_context_text.insert(tk.END, config.user_race_context)
        
        sys_prompt = config.system_prompt_template
        if not sys_prompt:
            sys_prompt = DEFAULT_SYSTEM_PROMPT
        self.system_prompt_text.delete(1.0, tk.END)
        self.system_prompt_text.insert(tk.END, sys_prompt)

        upd_prompt = config.update_prompt_template
        if not upd_prompt:
            upd_prompt = DEFAULT_UPDATE_PROMPT
        self.update_prompt_text.delete(1.0, tk.END)
        self.update_prompt_text.insert(tk.END, upd_prompt)
        
        self.thinking_var.set(config.thinking_level)
        self.tokens_spin.set(config.max_output_tokens)
        self.words_spin.set(config.target_words)
        self.interval_spin.set(config.leaderboard_interval_minutes)

    def _save_settings(self):
        new_config = LiveModeConfig()
        
        # Retain API key and model configurations
        new_config.gemini_api_key = self.current_config.gemini_api_key
        new_config.llm_model = self.current_config.llm_model
        new_config.tts_model = self.current_config.tts_model
        
        # Read from UI
        new_config.commentator_voice = self.voice_var.get()
        
        accent = self.accent_var.get()
        new_config.commentator_accent = "" if accent == "None" else accent
        
        new_config.commentator_name = self.name_entry.get().strip()
        new_config.commentator_style = self.style_text.get(1.0, tk.END).strip()
        new_config.voice_refinement_text = self.refine_entry.get().strip()
        new_config.user_race_context = self.race_context_text.get(1.0, tk.END).strip()
        
        new_config.system_prompt_template = self.system_prompt_text.get(1.0, tk.END).strip()
        new_config.update_prompt_template = self.update_prompt_text.get(1.0, tk.END).strip()
        new_config.thinking_level = self.thinking_var.get()
        
        try:
            new_config.max_output_tokens = int(self.tokens_spin.get())
            new_config.target_words = int(self.words_spin.get())
            new_config.leaderboard_interval_minutes = float(self.interval_spin.get())
        except ValueError:
            logger.error("Invalid number format in settings.")
            
        self.current_config = new_config
        save_live_mode_config(self.current_config)
        self.orchestrator.update_config(self.current_config)
        logger.info("Settings saved and config updated.")
        
    def _reset_defaults(self):
        default_config = LiveModeConfig()
        default_config.gemini_api_key = self.current_config.gemini_api_key
        self._populate_fields(default_config)

    def _refresh_presets(self):
        self.presets = load_live_mode_presets()
        preset_names = list(self.presets.keys())
        self.preset_combo['values'] = preset_names
        if preset_names:
            self.preset_combo.set(preset_names[0])
            self.load_preset_btn.config(state=tk.NORMAL)
        else:
            self.preset_combo.set("No presets saved")
            self.load_preset_btn.config(state=tk.DISABLED)

    def _load_preset(self):
        name = self.preset_var.get()
        if name in self.presets:
            preset_data = self.presets[name]
            
            # Start from defaults, overlay preset data
            new_config = LiveModeConfig()
            new_config.gemini_api_key = self.current_config.gemini_api_key
            
            valid_fields = set(LiveModeConfig.__dataclass_fields__.keys())
            for key, val in preset_data.items():
                if key in valid_fields:
                    setattr(new_config, key, val)
                    
            self.current_config = new_config
            self._populate_fields(self.current_config)
            save_live_mode_config(self.current_config)
            self.orchestrator.update_config(self.current_config)
            logger.info(f"Preset '{name}' loaded and saved to config.")
            
    def _save_preset(self):
        # We need to read current fields from UI to save them accurately
        name = simpledialog.askstring("Save Preset", "Enter a name for this commentator preset:", parent=self.root)
        if name and name.strip():
            name = name.strip()
            # Read from UI into a temp config
            temp_config = LiveModeConfig()
            temp_config.commentator_voice = self.voice_var.get()
            accent = self.accent_var.get()
            temp_config.commentator_accent = "" if accent == "None" else accent
            temp_config.commentator_name = self.name_entry.get().strip()
            temp_config.commentator_style = self.style_text.get(1.0, tk.END).strip()
            temp_config.voice_refinement_text = self.refine_entry.get().strip()
            temp_config.user_race_context = self.race_context_text.get(1.0, tk.END).strip()
            temp_config.system_prompt_template = self.system_prompt_text.get(1.0, tk.END).strip()
            temp_config.update_prompt_template = self.update_prompt_text.get(1.0, tk.END).strip()
            temp_config.thinking_level = self.thinking_var.get()
            try:
                temp_config.max_output_tokens = int(self.tokens_spin.get())
                temp_config.target_words = int(self.words_spin.get())
                temp_config.leaderboard_interval_minutes = float(self.interval_spin.get())
            except ValueError:
                pass
                
            save_live_mode_preset(name, temp_config)
            self._refresh_presets()
            self.preset_combo.set(name)

    def _on_test_voice(self):
        self._test_voice_requested = True
        self.test_voice_status.config(text="Requesting voice sample...")
        self.root.after(3000, lambda: self.test_voice_status.config(text=""))

    def _toggle_pause(self):
        if hasattr(self.orchestrator, 'is_paused') and self.orchestrator.is_paused:
            if hasattr(self.orchestrator, 'resume'):
                self.orchestrator.resume()
                self.pause_btn.config(text="Pause")
        else:
            if hasattr(self.orchestrator, 'pause'):
                self.orchestrator.pause()
                self.pause_btn.config(text="Resume")

    def _toggle_queue(self, event=None):
        self.queue_collapsed = not self.queue_collapsed
        if self.queue_collapsed:
            self.queue_toggle.config(text="▶")
            self.queue_content.pack_forget()
        else:
            self.queue_toggle.config(text="▼")
            self.queue_content.pack(fill=tk.X, pady=(5, 0))

    def _get_category_color(self, category: str) -> str:
        category = category.upper()
        colors = {
            'FINISH': THEME_SUCCESS,
            'OVERTAKE': THEME_ACCENT,
            'ACCIDENT': THEME_DANGER,
            'SAFETY_CAR': THEME_WARNING,
            'BATTLE': THEME_BATTLE,
            'PENALTY': THEME_PENALTY,
            'FLAG': THEME_WARNING,
            'PIT': THEME_FG_DIM,
            'LEADERBOARD': THEME_FG_MUTED,
            'SESSION': THEME_FG_MUTED
        }
        return colors.get(category, THEME_FG_DIM)

    def _update(self):
        try:
            status = self.orchestrator.get_status_display()
            queue = self.orchestrator.get_queue_display()
            
            # Update state
            state_text = status.get('state', 'IDLE').upper()
            if hasattr(self.orchestrator, 'is_paused') and self.orchestrator.is_paused:
                state_text = 'PAUSED'
                
            self.state_label.config(text=state_text)
            
            state_color = THEME_FG_DIM
            if state_text == 'PLAYING':
                state_color = THEME_SUCCESS
            elif state_text == 'GENERATING_TTS':
                state_color = THEME_WARNING
            elif state_text == 'GENERATING_COMMENTARY':
                state_color = THEME_ACCENT
            elif state_text in ('ERROR', 'RATE_LIMITED'):
                state_color = THEME_DANGER
            elif state_text == 'PAUSED':
                state_color = THEME_WARNING
            elif state_text == 'CONNECTING':
                state_color = THEME_FG_DIM
                
            self.state_label.config(fg=state_color)
            self.header_dot.itemconfig(self.dot_id, fill=state_color)
            
            # Update event
            event_text = status.get('event', '')
            if not event_text:
                event_text = "No events yet"
            self.event_label.config(text=event_text)
            
            # Update history
            hist = getattr(self.orchestrator, 'commentary_history', [])
            if len(hist) > self._history_len:
                self.history_text.config(state=tk.NORMAL)
                for entry in hist[self._history_len:]:
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    self.history_text.insert(tk.END, f"[{timestamp}] {entry}\n\n")
                self.history_text.see(tk.END)
                self.history_text.config(state=tk.DISABLED)
                self._history_len = len(hist)
            
            # Update queue
            q_count = status.get('queue_size', 0)
            self.queue_title.config(text=f"Event Queue ({q_count})")
            
            # Update token tally
            tokens = status.get('total_tokens', 0)
            self.token_label.config(text=f"{tokens:,} tokens")
            
            if not self.queue_collapsed:
                for i in range(5):
                    frame, badge, msg, score = self.queue_items[i]
                    if i < len(queue):
                        item = queue[i]
                        cat = item.get('category', 'UNKNOWN')
                        
                        badge.config(
                            text=cat[:10], 
                            bg=self._get_category_color(cat),
                            fg='#ffffff' if self._get_category_color(cat) not in (THEME_FG_DIM, THEME_FG_MUTED) else THEME_BG
                        )
                        
                        msg_text = item.get('message', '')
                        if len(msg_text) > 40:
                            msg_text = msg_text[:37] + "..."
                        msg.config(text=msg_text)
                        
                        score_val = item.get('score', 0.0)
                        score.config(text=f"{score_val:.1f}")
                        
                        frame.pack(fill=tk.X, pady=2)
                    else:
                        frame.pack_forget()
                        
        except Exception as e:
            logger.error(f"UI update error: {e}")
            
        # Schedule next update
        self.root.after(200, self._update)

    def _on_start(self):
        if self.orchestrator.state == 'IDLE' and not getattr(self, 'orch_thread', None):
            self.orch_thread = threading.Thread(target=self.orchestrator.start, daemon=True)
            self.orch_thread.start()
            self.start_btn.config(state=tk.DISABLED, bg=THEME_FG_DIM)

    def run(self):
        self.root.after(200, self._update)
        self.root.mainloop()

    def _on_close(self):
        try:
            self.orchestrator.stop()
        except Exception as e:
            logger.error(f"Error stopping orchestrator: {e}")
        finally:
            self.root.destroy()
