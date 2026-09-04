import ctypes
import mmap
import os
import sys
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

# Constants for AMS2 Shared Memory
STRING_LENGTH_MAX = 64
STORED_PARTICIPANTS_MAX = 64
TYRE_MAX = 4
VEC_MAX = 3
SHARED_MEMORY_NAME = "$pcars2$"

# Theme Colors (Dark motorsport theme matching the main launcher)
THEME_BG = "#1e1e2e"
THEME_BG_CARD = "#282a36"
THEME_BG_INPUT = "#181825"
THEME_FG = "#f8f8f2"
THEME_FG_MUTED = "#6272a4"
THEME_ACCENT = "#bd93f9"
THEME_ACCENT_HOVER = "#caa6fb"
THEME_SUCCESS = "#50fa7b"
THEME_SUCCESS_BG = "#1d3b2b"
THEME_DANGER = "#ff5555"
THEME_DANGER_BG = "#3b1d1d"
THEME_WARNING = "#ffb86c"
THEME_INFO = "#8be9fd"
THEME_BORDER = "#44475a"
FONT_FAMILY = "Segoe UI"

class ParticipantInfo(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mIsActive", ctypes.c_bool),
        ("mName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mWorldPosition", ctypes.c_float * VEC_MAX),
        ("mCurrentLapDistance", ctypes.c_float),
        ("mRacePosition", ctypes.c_uint),
        ("mLapsCompleted", ctypes.c_uint),
        ("mCurrentLap", ctypes.c_uint),
        ("mCurrentSector", ctypes.c_int),
    ]

class SharedMemory(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mVersion", ctypes.c_uint),
        ("mBuildVersionNumber", ctypes.c_uint),
        ("mGameState", ctypes.c_uint),
        ("mSessionState", ctypes.c_uint),
        ("mRaceState", ctypes.c_uint),
        ("mViewedParticipantIndex", ctypes.c_int),
        ("mNumParticipants", ctypes.c_int),
        ("mParticipantInfo", ParticipantInfo * STORED_PARTICIPANTS_MAX),
        ("mUnfilteredThrottle", ctypes.c_float),
        ("mUnfilteredBrake", ctypes.c_float),
        ("mUnfilteredSteering", ctypes.c_float),
        ("mUnfilteredClutch", ctypes.c_float),
        ("mCarName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mCarClassName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mLapsInEvent", ctypes.c_uint),
        ("mTrackLocation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTrackVariation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTrackLength", ctypes.c_float),
        ("mNumSectors", ctypes.c_int),
        ("mLapInvalidated", ctypes.c_bool),
        ("mBestLapTime", ctypes.c_float),
        ("mLastLapTime", ctypes.c_float),
        ("mCurrentTime", ctypes.c_float),
        ("mSplitTimeAhead", ctypes.c_float),
        ("mSplitTimeBehind", ctypes.c_float),
        ("mSplitTime", ctypes.c_float),
        ("mEventTimeRemaining", ctypes.c_float),
        ("mPersonalFastestLapTime", ctypes.c_float),
        ("mWorldFastestLapTime", ctypes.c_float),
        ("mCurrentSector1Time", ctypes.c_float),
        ("mCurrentSector2Time", ctypes.c_float),
        ("mCurrentSector3Time", ctypes.c_float),
        ("mFastestSector1Time", ctypes.c_float),
        ("mFastestSector2Time", ctypes.c_float),
        ("mFastestSector3Time", ctypes.c_float),
        ("mPersonalFastestSector1Time", ctypes.c_float),
        ("mPersonalFastestSector2Time", ctypes.c_float),
        ("mPersonalFastestSector3Time", ctypes.c_float),
        ("mWorldFastestSector1Time", ctypes.c_float),
        ("mWorldFastestSector2Time", ctypes.c_float),
        ("mWorldFastestSector3Time", ctypes.c_float),
        ("mHighestFlagColour", ctypes.c_uint),
        ("mHighestFlagReason", ctypes.c_uint),
        ("mPitMode", ctypes.c_uint),
        ("mPitSchedule", ctypes.c_uint),
        ("mCarFlags", ctypes.c_uint),
        ("mOilTempCelsius", ctypes.c_float),
        ("mOilPressureKPa", ctypes.c_float),
        ("mWaterTempCelsius", ctypes.c_float),
        ("mWaterPressureKPa", ctypes.c_float),
        ("mFuelPressureKPa", ctypes.c_float),
        ("mFuelLevel", ctypes.c_float),
        ("mFuelCapacity", ctypes.c_float),
        ("mSpeed", ctypes.c_float),
        ("mRpm", ctypes.c_float),
        ("mMaxRPM", ctypes.c_float),
        ("mBrake", ctypes.c_float),
        ("mThrottle", ctypes.c_float),
        ("mClutch", ctypes.c_float),
        ("mSteering", ctypes.c_float),
        ("mGear", ctypes.c_int),
        ("mNumGears", ctypes.c_int),
        ("mOdometerKM", ctypes.c_float),
        ("mAntiLockActive", ctypes.c_bool),
        ("mLastOpponentCollisionIndex", ctypes.c_int),
        ("mLastOpponentCollisionMagnitude", ctypes.c_float),
        ("mBoostActive", ctypes.c_bool),
        ("mBoostAmount", ctypes.c_float),
        ("mOrientation", ctypes.c_float * VEC_MAX),
        ("mLocalVelocity", ctypes.c_float * VEC_MAX),
        ("mWorldVelocity", ctypes.c_float * VEC_MAX),
        ("mAngularVelocity", ctypes.c_float * VEC_MAX),
        ("mLocalAcceleration", ctypes.c_float * VEC_MAX),
        ("mWorldAcceleration", ctypes.c_float * VEC_MAX),
        ("mExtentsCentre", ctypes.c_float * VEC_MAX),
        ("mTyreFlags", ctypes.c_uint * TYRE_MAX),
        ("mTerrain", ctypes.c_uint * TYRE_MAX),
        ("mTyreY", ctypes.c_float * TYRE_MAX),
        ("mTyreRPS", ctypes.c_float * TYRE_MAX),
        ("mTyreSlipSpeed", ctypes.c_float * TYRE_MAX),
        ("mTyreTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreGrip", ctypes.c_float * TYRE_MAX),
        ("mTyreHeightAboveGround", ctypes.c_float * TYRE_MAX),
        ("mTyreLateralStiffness", ctypes.c_float * TYRE_MAX),
        ("mTyreWear", ctypes.c_float * TYRE_MAX),
        ("mBrakeDamage", ctypes.c_float * TYRE_MAX),
        ("mSuspensionDamage", ctypes.c_float * TYRE_MAX),
        ("mBrakeTempCelsius", ctypes.c_float * TYRE_MAX),
        ("mTyreTreadTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreLayerTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreCarcassTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreRimTemp", ctypes.c_float * TYRE_MAX),
        ("mTyreInternalAirTemp", ctypes.c_float * TYRE_MAX),
        ("mCrashState", ctypes.c_uint),
        ("mAeroDamage", ctypes.c_float),
        ("mEngineDamage", ctypes.c_float),
        ("mAmbientTemperature", ctypes.c_float),
        ("mTrackTemperature", ctypes.c_float),
        ("mRainDensity", ctypes.c_float),
        ("mWindSpeed", ctypes.c_float),
        ("mWindDirectionX", ctypes.c_float),
        ("mWindDirectionY", ctypes.c_float),
        ("mCloudBrightness", ctypes.c_float),
        ("mSequenceNumber", ctypes.c_uint),
        ("mWheelLocalPositionY", ctypes.c_float * TYRE_MAX),
        ("mSuspensionTravel", ctypes.c_float * TYRE_MAX),
        ("mSuspensionVelocity", ctypes.c_float * TYRE_MAX),
        ("mAirPressure", ctypes.c_float * TYRE_MAX),
        ("mEngineSpeed", ctypes.c_float),
        ("mEngineTorque", ctypes.c_float),
        ("mWings", ctypes.c_float * 2),
        ("mHandBrake", ctypes.c_float),
        ("mCurrentSector1Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCurrentSector2Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCurrentSector3Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector1Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector2Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector3Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestLapTimes", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mLastLapTimes", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mLapsInvalidated_PC2", ctypes.c_bool * STORED_PARTICIPANTS_MAX),
        ("mRaceStates", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mPitModes", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mOrientations", (ctypes.c_float * VEC_MAX) * STORED_PARTICIPANTS_MAX),
        ("mSpeeds", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCarNames", (ctypes.c_char * STRING_LENGTH_MAX) * STORED_PARTICIPANTS_MAX),
        ("mCarClassNames", (ctypes.c_char * STRING_LENGTH_MAX) * STORED_PARTICIPANTS_MAX),
        ("mEnforcedPitStopLap", ctypes.c_int),
        ("mTranslatedTrackLocation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTranslatedTrackVariation", ctypes.c_char * STRING_LENGTH_MAX),
    ]

def get_corner_names_dir():
    """Find or create the Corner Names directory."""
    # Look relative to project root or current working dir
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dir = os.path.join(base_dir, "Corner Names")
    if not os.path.exists(target_dir):
        target_dir = os.path.abspath("Corner Names")
        os.makedirs(target_dir, exist_ok=True)
    return target_dir

def sanitize_filename(name):
    """Sanitize string for filename."""
    name = name.replace(":", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")
    return "".join(c for c in name if c.isalnum() or c in ("_", "-"))

class CornerMapperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AMS2 Corner Mapper & Track Manager")
        self.root.geometry("1040x740")
        self.root.minsize(860, 600)
        self.root.configure(bg=THEME_BG)

        self.shm = None
        self.data = None
        self.is_connected = False
        self.running = True

        # Telemetry State
        self.current_speed = 0.0
        self.current_lap_dist = 0.0
        self.track_length = 0.0
        self.track_location = ""
        self.track_variation = ""
        self.current_lap = 0

        # Recording State
        self.is_recording_corner = False
        self.corner_start_dist = 0.0
        self.last_detected_track_key = ""

        # Track Data
        self.current_track_file = ""
        self.corners = [] # List of {"name": str, "start": float, "end": float}

        self.corner_names_dir = get_corner_names_dir()

        self._setup_ui()
        self._setup_keybindings()

        # Start Telemetry Thread
        self.telemetry_thread = threading.Thread(target=self._telemetry_worker, daemon=True)
        self.telemetry_thread.start()

        # Periodic UI update loop
        self.root.after(50, self._update_ui_loop)

        # Refresh available tracks list
        self.refresh_track_list()

    def _setup_ui(self):
        # --- Top Header: Connection & Telemetry HUD ---
        header_frame = tk.Frame(self.root, bg=THEME_BG_CARD, padx=16, pady=12, highlightbackground=THEME_BORDER, highlightthickness=1)
        header_frame.pack(fill="x", padx=16, pady=(16, 8))

        # Status & Connection
        top_row = tk.Frame(header_frame, bg=THEME_BG_CARD)
        top_row.pack(fill="x")

        self.lbl_status = tk.Label(top_row, text="🔴 Connecting to AMS2...", bg=THEME_BG_CARD, fg=THEME_DANGER, font=(FONT_FAMILY, 11, "bold"))
        self.lbl_status.pack(side="left")

        self.lbl_active_corner = tk.Label(top_row, text="⚪ ON STRAIGHT", bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 11, "bold"))
        self.lbl_active_corner.pack(side="right")

        # Telemetry Metrics
        metric_row = tk.Frame(header_frame, bg=THEME_BG_CARD)
        metric_row.pack(fill="x", pady=(10, 4))

        self.lbl_telemetry_track = tk.Label(metric_row, text="Sim Track: Waiting for game...", bg=THEME_BG_CARD, fg=THEME_INFO, font=(FONT_FAMILY, 10))
        self.lbl_telemetry_track.pack(side="left")

        self.lbl_telemetry_dist = tk.Label(metric_row, text="Lap Dist: 0.0 m / 0.0 m (0.0%)", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 10, "bold"))
        self.lbl_telemetry_dist.pack(side="right")

        self.lbl_telemetry_speed = tk.Label(metric_row, text="Speed: 0 km/h", bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 10))
        self.lbl_telemetry_speed.pack(side="right", padx=(0, 24))

        # Progress bar for lap
        self.lap_progress = ttk.Progressbar(header_frame, orient="horizontal", mode="determinate")
        self.lap_progress.pack(fill="x", pady=(6, 0))

        # --- Track Profile Bar (Select, New, Edit, Delete Tracks) ---
        track_bar = tk.Frame(self.root, bg=THEME_BG, padx=16, pady=4)
        track_bar.pack(fill="x")

        tk.Label(track_bar, text="📁 Track File:", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 10, "bold")).pack(side="left", padx=(0, 8))

        self.track_combo_var = tk.StringVar()
        self.track_combo = ttk.Combobox(track_bar, textvariable=self.track_combo_var, state="readonly", width=38)
        self.track_combo.pack(side="left", padx=(0, 8))
        self.track_combo.bind("<<ComboboxSelected>>", self.on_track_selected)

        # Track management buttons
        btn_new_track = tk.Button(track_bar, text="➕ New Track", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                                  command=self.create_new_track, relief="flat", padx=10, pady=3, cursor="hand2")
        btn_new_track.pack(side="left", padx=3)

        btn_edit_track = tk.Button(track_bar, text="✏️ Edit Track Info", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                                   command=self.edit_track_info, relief="flat", padx=10, pady=3, cursor="hand2")
        btn_edit_track.pack(side="left", padx=3)

        btn_delete_track = tk.Button(track_bar, text="🗑️ Delete Track", bg=THEME_DANGER_BG, fg=THEME_DANGER, font=(FONT_FAMILY, 9),
                                     command=self.delete_track_file, relief="flat", padx=10, pady=3, cursor="hand2")
        btn_delete_track.pack(side="left", padx=3)

        btn_refresh_tracks = tk.Button(track_bar, text="🔄 Refresh", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                                       command=self.refresh_track_list, relief="flat", padx=8, pady=3, cursor="hand2")
        btn_refresh_tracks.pack(side="left", padx=3)

        # --- Real-Time Corner Recording Panel ---
        record_frame = tk.LabelFrame(self.root, text=" 🏎️ Drive & Mark Corners (In-Game or Live) ", bg=THEME_BG_CARD, fg=THEME_ACCENT,
                                     font=(FONT_FAMILY, 10, "bold"), padx=14, pady=10, highlightbackground=THEME_BORDER, highlightthickness=1)
        record_frame.pack(fill="x", padx=16, pady=8)

        rec_top = tk.Frame(record_frame, bg=THEME_BG_CARD)
        rec_top.pack(fill="x")

        tk.Label(rec_top, text="Next Corner Name:", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 10)).pack(side="left", padx=(0, 6))
        self.corner_name_var = tk.StringVar(value="Turn 1")
        self.entry_corner_name = tk.Entry(rec_top, textvariable=self.corner_name_var, bg=THEME_BG_INPUT, fg=THEME_FG,
                                          insertbackground=THEME_FG, font=(FONT_FAMILY, 10), width=22, relief="flat")
        self.entry_corner_name.pack(side="left", padx=(0, 14))

        # Big Action Buttons
        self.btn_mark_start = tk.Button(rec_top, text="🚩 Mark Start (F9 / Space)", bg=THEME_INFO, fg="#000000",
                                        font=(FONT_FAMILY, 10, "bold"), command=self.mark_corner_start, relief="flat",
                                        padx=12, pady=6, cursor="hand2")
        self.btn_mark_start.pack(side="left", padx=4)

        self.btn_mark_end = tk.Button(rec_top, text="🏁 Mark End (F10 / Enter)", bg=THEME_SUCCESS, fg="#000000",
                                      font=(FONT_FAMILY, 10, "bold"), command=self.mark_corner_end, relief="flat",
                                      padx=12, pady=6, cursor="hand2", state="disabled")
        self.btn_mark_end.pack(side="left", padx=4)

        self.btn_toggle_rec = tk.Button(rec_top, text="⏺️ Start/Stop Toggle (F8)", bg=THEME_ACCENT, fg="#000000",
                                        font=(FONT_FAMILY, 10, "bold"), command=self.toggle_recording, relief="flat",
                                        padx=12, pady=6, cursor="hand2")
        self.btn_toggle_rec.pack(side="left", padx=4)

        self.lbl_rec_status = tk.Label(record_frame, text="💡 Tip: Press F9 (or Space) when entering a corner, then F10 (or Enter) when exiting. Hotkeys work while driving in AMS2!",
                                       bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9), anchor="w")
        self.lbl_rec_status.pack(fill="x", pady=(8, 0))

        # --- Table & Corner Editing Panel ---
        table_container = tk.Frame(self.root, bg=THEME_BG, padx=16, pady=4)
        table_container.pack(fill="both", expand=True)

        # Left: Corner List (Treeview)
        tree_frame = tk.Frame(table_container, bg=THEME_BG)
        tree_frame.pack(side="left", fill="both", expand=True)

        # Style Treeview
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=THEME_BG_CARD, foreground=THEME_FG, fieldbackground=THEME_BG_CARD,
                        rowheight=26, font=(FONT_FAMILY, 9))
        style.configure("Treeview.Heading", background=THEME_BG_INPUT, foreground=THEME_ACCENT, font=(FONT_FAMILY, 9, "bold"))
        style.map("Treeview", background=[("selected", THEME_ACCENT)], foreground=[("selected", "#000000")])

        columns = ("name", "start", "end", "length")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Corner Name")
        self.tree.heading("start", text="Start Dist (m)")
        self.tree.heading("end", text="End Dist (m)")
        self.tree.heading("length", text="Length (m)")

        self.tree.column("name", width=220, anchor="w")
        self.tree.column("start", width=110, anchor="center")
        self.tree.column("end", width=110, anchor="center")
        self.tree.column("length", width=110, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self.edit_selected_corner())
        self.tree.bind("<Delete>", lambda e: self.delete_selected_corner())

        # Right: Action Toolbar for Table
        action_bar = tk.Frame(table_container, bg=THEME_BG, padx=10)
        action_bar.pack(side="right", fill="y")

        tk.Label(action_bar, text="Manage Corners", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 6))

        tk.Button(action_bar, text="➕ Add Manually", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=self.add_corner_manually, relief="flat", padx=10, pady=4, cursor="hand2", width=16).pack(pady=2)

        tk.Button(action_bar, text="✏️ Edit Selected", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=self.edit_selected_corner, relief="flat", padx=10, pady=4, cursor="hand2", width=16).pack(pady=2)

        tk.Button(action_bar, text="🗑️ Delete Selected", bg=THEME_DANGER_BG, fg=THEME_DANGER, font=(FONT_FAMILY, 9),
                  command=self.delete_selected_corner, relief="flat", padx=10, pady=4, cursor="hand2", width=16).pack(pady=2)

        tk.Frame(action_bar, bg=THEME_BORDER, height=1).pack(fill="x", pady=8)

        tk.Label(action_bar, text="Fine-Tune Nudge", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 4))

        nudge_row1 = tk.Frame(action_bar, bg=THEME_BG)
        nudge_row1.pack(fill="x", pady=1)
        tk.Button(nudge_row1, text="Start -5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=lambda: self.nudge_selected('start', -5), relief="flat", width=7).pack(side="left", padx=1)
        tk.Button(nudge_row1, text="Start +5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=lambda: self.nudge_selected('start', 5), relief="flat", width=7).pack(side="right", padx=1)

        nudge_row2 = tk.Frame(action_bar, bg=THEME_BG)
        nudge_row2.pack(fill="x", pady=1)
        tk.Button(nudge_row2, text="End -5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=lambda: self.nudge_selected('end', -5), relief="flat", width=7).pack(side="left", padx=1)
        tk.Button(nudge_row2, text="End +5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=lambda: self.nudge_selected('end', 5), relief="flat", width=7).pack(side="right", padx=1)

        tk.Frame(action_bar, bg=THEME_BORDER, height=1).pack(fill="x", pady=8)

        tk.Button(action_bar, text="🔼 Move Up", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=self.move_corner_up, relief="flat", padx=10, pady=3, cursor="hand2", width=16).pack(pady=2)

        tk.Button(action_bar, text="🔽 Move Down", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=self.move_corner_down, relief="flat", padx=10, pady=3, cursor="hand2", width=16).pack(pady=2)

        tk.Button(action_bar, text="🔢 Sort by Dist", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=self.sort_corners, relief="flat", padx=10, pady=3, cursor="hand2", width=16).pack(pady=2)

        # --- Bottom Bar: Save & Status ---
        bottom_bar = tk.Frame(self.root, bg=THEME_BG_CARD, padx=16, pady=10, highlightbackground=THEME_BORDER, highlightthickness=1)
        bottom_bar.pack(fill="x", padx=16, pady=(8, 16))

        self.lbl_bottom_status = tk.Label(bottom_bar, text="Ready. Select or create a track to start mapping.", bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9))
        self.lbl_bottom_status.pack(side="left")

        btn_save = tk.Button(bottom_bar, text="💾 Save Changes to File", bg=THEME_SUCCESS, fg="#000000",
                             font=(FONT_FAMILY, 10, "bold"), command=self.save_current_track_file, relief="flat",
                             padx=16, pady=4, cursor="hand2")
        btn_save.pack(side="right")

        btn_save_as = tk.Button(bottom_bar, text="Save As...", bg=THEME_BG, fg=THEME_FG,
                                font=(FONT_FAMILY, 9), command=self.save_track_as, relief="flat",
                                padx=10, pady=4, cursor="hand2")
        btn_save_as.pack(side="right", padx=6)

    def _setup_keybindings(self):
        # Window-level keybindings
        self.root.bind("<F9>", lambda e: self.mark_corner_start())
        self.root.bind("<F10>", lambda e: self.mark_corner_end())
        self.root.bind("<F8>", lambda e: self.toggle_recording())
        self.root.bind("<space>", self._on_space_pressed)
        self.root.bind("<Return>", self._on_enter_pressed)

    def _on_space_pressed(self, event):
        # If user is not actively typing in an entry box, space triggers mark start
        if event.widget != self.entry_corner_name:
            if not self.is_recording_corner:
                self.mark_corner_start()

    def _on_enter_pressed(self, event):
        # If user is not actively typing in entry box or if recording is active
        if self.is_recording_corner:
            self.mark_corner_end()

    # --- Telemetry Background Worker ---
    def _telemetry_worker(self):
        VK_F9 = 0x78
        VK_F10 = 0x79
        VK_F8 = 0x77

        f9_down_prev = False
        f10_down_prev = False
        f8_down_prev = False

        while self.running:
            try:
                # 1. Connect or maintain connection
                if not self.shm:
                    shm_size = ctypes.sizeof(SharedMemory)
                    try:
                        self.shm = mmap.mmap(-1, shm_size, SHARED_MEMORY_NAME)
                        self.data = SharedMemory.from_buffer(self.shm)
                        self.is_connected = True
                    except Exception:
                        self.is_connected = False
                        self.shm = None
                        self.data = None
                        time.sleep(1.0)
                        continue

                # 2. Read live data
                if self.data:
                    view_idx = self.data.mViewedParticipantIndex
                    if view_idx < 0 or view_idx >= STORED_PARTICIPANTS_MAX:
                        view_idx = 0
                    
                    p = self.data.mParticipantInfo[view_idx]
                    self.current_lap_dist = float(p.mCurrentLapDistance)
                    self.current_lap = int(p.mCurrentLap)
                    self.current_speed = float(self.data.mSpeed) * 3.6 # m/s to km/h
                    self.track_length = float(self.data.mTrackLength)

                    try:
                        self.track_location = self.data.mTrackLocation.decode('utf-8', errors='ignore').strip('\x00')
                        self.track_variation = self.data.mTrackVariation.decode('utf-8', errors='ignore').strip('\x00')
                    except Exception:
                        pass

                # 3. Global Hotkey Checking (allows F8/F9/F10 to trigger even when driving in-game with AMS2 focused)
                try:
                    f9_state = ctypes.windll.user32.GetAsyncKeyState(VK_F9) & 0x8000 != 0
                    if f9_state and not f9_down_prev:
                        self.root.after(0, self.mark_corner_start)
                    f9_down_prev = f9_state

                    f10_state = ctypes.windll.user32.GetAsyncKeyState(VK_F10) & 0x8000 != 0
                    if f10_state and not f10_down_prev:
                        self.root.after(0, self.mark_corner_end)
                    f10_down_prev = f10_state

                    f8_state = ctypes.windll.user32.GetAsyncKeyState(VK_F8) & 0x8000 != 0
                    if f8_state and not f8_down_prev:
                        self.root.after(0, self.toggle_recording)
                    f8_down_prev = f8_state
                except Exception:
                    pass

            except Exception as e:
                self.is_connected = False
                self.shm = None

            time.sleep(0.04) # ~25Hz polling

    # --- UI Periodic Update Loop ---
    def _update_ui_loop(self):
        if not self.running:
            return

        if self.is_connected and self.track_length > 0:
            self.lbl_status.config(text="🟢 Connected to AMS2", fg=THEME_SUCCESS)
            self.lbl_telemetry_track.config(text=f"Track: {self.track_location} - {self.track_variation} ({self.track_length:.1f} m)")
            
            lap_pct = min(100.0, max(0.0, (self.current_lap_dist / self.track_length) * 100.0))
            self.lbl_telemetry_dist.config(text=f"Lap Dist: {self.current_lap_dist:.1f} m / {self.track_length:.1f} m ({lap_pct:.1f}%)")
            self.lbl_telemetry_speed.config(text=f"Speed: {self.current_speed:.0f} km/h (Lap {self.current_lap})")
            self.lap_progress['value'] = lap_pct

            # Check if game switched tracks
            track_key = f"{self.track_location}_{self.track_variation}".strip("_")
            if track_key and track_key != self.last_detected_track_key and self.track_location:
                self.last_detected_track_key = track_key
                self._auto_detect_track_from_sim()

            # Active corner feedback
            active_corner = self.get_corner_at_distance(self.current_lap_dist)
            if active_corner:
                self.lbl_active_corner.config(text=f"🟢 IN CORNER: {active_corner}", fg=THEME_SUCCESS)
            elif self.is_recording_corner:
                self.lbl_active_corner.config(text=f"🔴 RECORDING '{self.corner_name_var.get()}' (from {self.corner_start_dist:.1f}m)...", fg=THEME_WARNING)
            else:
                self.lbl_active_corner.config(text="⚪ ON STRAIGHT", fg=THEME_FG_MUTED)

        else:
            self.lbl_status.config(text="🔴 Waiting for AMS2 to run...", fg=THEME_DANGER)
            self.lbl_telemetry_track.config(text="Sim Track: Waiting for game...")
            self.lbl_telemetry_dist.config(text="Lap Dist: -- m")
            self.lbl_telemetry_speed.config(text="Speed: -- km/h")
            self.lap_progress['value'] = 0
            self.lbl_active_corner.config(text="⚪ Disconnected", fg=THEME_FG_MUTED)

        self.root.after(50, self._update_ui_loop)

    def get_corner_at_distance(self, dist):
        for c in self.corners:
            start = c.get("start", 0.0)
            end = c.get("end", 0.0)
            name = c.get("name", "")
            if start <= end:
                if start <= dist <= end:
                    return name
            else: # Wrap around start/finish
                if dist >= start or dist <= end:
                    return name
        return None

    # --- Recording Actions ---
    def mark_corner_start(self):
        dist = self.current_lap_dist
        self.is_recording_corner = True
        self.corner_start_dist = round(dist, 1)

        self.btn_mark_start.config(state="disabled", bg=THEME_BG_CARD)
        self.btn_mark_end.config(state="normal", bg=THEME_SUCCESS)
        self.btn_toggle_rec.config(text="⏹️ Stop (F8)", bg=THEME_DANGER)

        corner_name = self.corner_name_var.get().strip() or "Corner"
        self.lbl_rec_status.config(
            text=f"🔴 Recording [{corner_name}] from {self.corner_start_dist:.1f}m. Press F10 / Enter when leaving corner.",
            fg=THEME_WARNING
        )

    def mark_corner_end(self):
        if not self.is_recording_corner:
            return

        end_dist = round(self.current_lap_dist, 1)
        start_dist = self.corner_start_dist
        name = self.corner_name_var.get().strip() or f"Turn {len(self.corners) + 1}"

        # Add corner
        self.corners.append({
            "name": name,
            "start": start_dist,
            "end": end_dist
        })

        self.is_recording_corner = False
        self.btn_mark_start.config(state="normal", bg=THEME_INFO)
        self.btn_mark_end.config(state="disabled", bg=THEME_BG_CARD)
        self.btn_toggle_rec.config(text="⏺️ Start/Stop Toggle (F8)", bg=THEME_ACCENT)

        # Auto-increment turn name if Turn X
        self._auto_increment_turn_name(name)

        # Refresh table
        self.refresh_table()
        self.lbl_rec_status.config(
            text=f"✅ Added [{name}] ({start_dist:.1f}m - {end_dist:.1f}m). Remember to click 'Save Changes' when done!",
            fg=THEME_SUCCESS
        )

    def toggle_recording(self):
        if self.is_recording_corner:
            self.mark_corner_end()
        else:
            self.mark_corner_start()

    def _auto_increment_turn_name(self, prev_name):
        if prev_name.lower().startswith("turn "):
            try:
                num = int(prev_name[5:].strip())
                self.corner_name_var.set(f"Turn {num + 1}")
                return
            except ValueError:
                pass
        elif prev_name.lower().startswith("t"):
            try:
                num = int(prev_name[1:].strip())
                self.corner_name_var.set(f"T{num + 1}")
                return
            except ValueError:
                pass
        self.corner_name_var.set(f"Turn {len(self.corners) + 1}")

    # --- Track File Management ---
    def refresh_track_list(self):
        """Scan Corner Names folder for JSON track files."""
        if not os.path.exists(self.corner_names_dir):
            os.makedirs(self.corner_names_dir, exist_ok=True)

        files = [f for f in os.listdir(self.corner_names_dir) if f.endswith(".json") and f != "trackLandmarksData.json" and f != "raceroomTrackLandmarksData.json"]
        files.sort()

        self.track_combo['values'] = files
        if files:
            if not self.track_combo_var.get() or self.track_combo_var.get() not in files:
                self.track_combo.set(files[0])
                self.load_track_file(os.path.join(self.corner_names_dir, files[0]))
        else:
            self.track_combo.set("")
            self.corners = []
            self.refresh_table()

    def on_track_selected(self, event=None):
        filename = self.track_combo_var.get()
        if filename:
            filepath = os.path.join(self.corner_names_dir, filename)
            self.load_track_file(filepath)

    def _auto_detect_track_from_sim(self):
        """Auto-detect track from game telemetry and load or suggest it."""
        if not self.track_location:
            return

        expected_filename = f"{sanitize_filename(self.track_location)}_{sanitize_filename(self.track_variation)}.json".strip("_") + ".json"
        full_path = os.path.join(self.corner_names_dir, expected_filename)

        if os.path.exists(full_path):
            self.track_combo.set(expected_filename)
            self.load_track_file(full_path)
            self.lbl_bottom_status.config(text=f"Auto-loaded profile for {self.track_location} ({expected_filename})", fg=THEME_SUCCESS)
        else:
            files = self.track_combo['values']
            match = None
            loc_clean = sanitize_filename(self.track_location).lower()
            for f in files:
                if loc_clean in f.lower():
                    match = f
                    break
            if match:
                self.track_combo.set(match)
                self.load_track_file(os.path.join(self.corner_names_dir, match))
                self.lbl_bottom_status.config(text=f"Auto-matched profile for {self.track_location} ({match})", fg=THEME_SUCCESS)

    def load_track_file(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.current_track_file = filepath
            self.track_location = data.get("track_location", self.track_location)
            self.track_variation = data.get("track_variation", self.track_variation)
            self.track_length = data.get("track_length", self.track_length)
            self.corners = data.get("corners", [])

            self.refresh_table()
            filename = os.path.basename(filepath)
            self.lbl_bottom_status.config(text=f"Loaded {len(self.corners)} corners from {filename}", fg=THEME_FG)
            self.corner_name_var.set(f"Turn {len(self.corners) + 1}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load track file:\n{e}")

    def create_new_track(self):
        """Dialog to create a new track profile."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Create New Track Profile")
        dlg.geometry("440x280")
        dlg.configure(bg=THEME_BG)
        dlg.transient(self.root)
        dlg.grab_set()

        def_loc = self.track_location if self.track_location else "Silverstone"
        def_var = self.track_variation if self.track_variation else "Grand Prix"
        def_len = f"{self.track_length:.1f}" if self.track_length > 0 else "5891.0"

        tk.Label(dlg, text="Track Location Name:", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(15, 2))
        entry_loc = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_loc.insert(0, def_loc)
        entry_loc.pack(fill="x", padx=20)

        tk.Label(dlg, text="Track Variation / Layout:", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_var = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_var.insert(0, def_var)
        entry_var.pack(fill="x", padx=20)

        tk.Label(dlg, text="Track Length (meters):", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_len = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_len.insert(0, def_len)
        entry_len.pack(fill="x", padx=20)

        def on_confirm():
            loc = entry_loc.get().strip()
            var = entry_var.get().strip()
            length_str = entry_len.get().strip()

            if not loc:
                messagebox.showwarning("Warning", "Track location name is required.")
                return

            try:
                length = float(length_str)
            except ValueError:
                length = 0.0

            filename = f"{sanitize_filename(loc)}_{sanitize_filename(var)}.json".strip("_") + ".json"
            filepath = os.path.join(self.corner_names_dir, filename)

            track_data = {
                "track_location": loc,
                "track_variation": var,
                "track_length": length,
                "corners": []
            }

            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(track_data, f, indent=2)

                dlg.destroy()
                self.refresh_track_list()
                self.track_combo.set(filename)
                self.load_track_file(filepath)
                messagebox.showinfo("Track Created", f"Created new track profile:\n{filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save new track file:\n{e}")

        btn_box = tk.Frame(dlg, bg=THEME_BG)
        btn_box.pack(fill="x", padx=20, pady=20)
        tk.Button(btn_box, text="Create Track", bg=THEME_SUCCESS, fg="#000000", font=(FONT_FAMILY, 9, "bold"),
                  command=on_confirm, padx=12, pady=4, relief="flat").pack(side="right")
        tk.Button(btn_box, text="Cancel", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=dlg.destroy, padx=12, pady=4, relief="flat").pack(side="right", padx=8)

    def edit_track_info(self):
        """Edit current track name, variation, and length."""
        if not self.current_track_file or not os.path.exists(self.current_track_file):
            messagebox.showwarning("Warning", "No track file currently loaded.")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Track Information")
        dlg.geometry("440x280")
        dlg.configure(bg=THEME_BG)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Track Location Name:", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(15, 2))
        entry_loc = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_loc.insert(0, self.track_location)
        entry_loc.pack(fill="x", padx=20)

        tk.Label(dlg, text="Track Variation / Layout:", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_var = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_var.insert(0, self.track_variation)
        entry_var.pack(fill="x", padx=20)

        tk.Label(dlg, text="Track Length (meters):", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_len = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_len.insert(0, f"{self.track_length:.1f}")
        entry_len.pack(fill="x", padx=20)

        def on_confirm():
            loc = entry_loc.get().strip()
            var = entry_var.get().strip()
            try:
                length = float(entry_len.get().strip())
            except ValueError:
                length = self.track_length

            self.track_location = loc
            self.track_variation = var
            self.track_length = length

            self.save_current_track_file()
            dlg.destroy()

        btn_box = tk.Frame(dlg, bg=THEME_BG)
        btn_box.pack(fill="x", padx=20, pady=20)
        tk.Button(btn_box, text="Save", bg=THEME_SUCCESS, fg="#000000", font=(FONT_FAMILY, 9, "bold"),
                  command=on_confirm, padx=12, pady=4, relief="flat").pack(side="right")
        tk.Button(btn_box, text="Cancel", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=dlg.destroy, padx=12, pady=4, relief="flat").pack(side="right", padx=8)

    def delete_track_file(self):
        """Delete current track JSON file."""
        if not self.current_track_file or not os.path.exists(self.current_track_file):
            messagebox.showwarning("Warning", "No track file selected.")
            return

        filename = os.path.basename(self.current_track_file)
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to permanently delete track profile:\n{filename}?"):
            try:
                os.remove(self.current_track_file)
                self.current_track_file = ""
                self.corners = []
                self.refresh_track_list()
                messagebox.showinfo("Deleted", f"Deleted {filename}.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete track file:\n{e}")

    def save_current_track_file(self):
        """Save corners to current track file."""
        if not self.current_track_file:
            self.save_track_as()
            return

        track_data = {
            "track_location": self.track_location,
            "track_variation": self.track_variation,
            "track_length": round(self.track_length, 1),
            "corners": self.corners
        }

        try:
            with open(self.current_track_file, "w", encoding="utf-8") as f:
                json.dump(track_data, f, indent=2)

            filename = os.path.basename(self.current_track_file)
            self.lbl_bottom_status.config(text=f"Saved {len(self.corners)} corners to {filename} at {time.strftime('%H:%M:%S')}", fg=THEME_SUCCESS)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save track file:\n{e}")

    def save_track_as(self):
        """Save track with a new name."""
        filename = filedialog.asksaveasfilename(
            initialdir=self.corner_names_dir,
            title="Save Track Profile As",
            filetypes=[("JSON Files", "*.json")],
            defaultextension=".json"
        )
        if filename:
            self.current_track_file = filename
            self.save_current_track_file()
            self.refresh_track_list()
            self.track_combo.set(os.path.basename(filename))

    # --- Corner Table Operations ---
    def refresh_table(self):
        """Refresh Treeview with current corners list."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        for idx, c in enumerate(self.corners):
            start = c.get("start", 0.0)
            end = c.get("end", 0.0)
            length = (end - start) if end >= start else (self.track_length - start + end)
            self.tree.insert("", "end", iid=str(idx), values=(
                c.get("name", "Unknown"),
                f"{start:.1f}",
                f"{end:.1f}",
                f"{length:.1f}"
            ))

    def add_corner_manually(self):
        """Open dialog to add a corner manually with specific distances."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Add Corner Manually")
        dlg.geometry("380x260")
        dlg.configure(bg=THEME_BG)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Corner Name:", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(15, 2))
        entry_name = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_name.insert(0, f"Turn {len(self.corners) + 1}")
        entry_name.pack(fill="x", padx=20)

        tk.Label(dlg, text="Start Distance (meters):", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_start = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_start.insert(0, f"{self.current_lap_dist:.1f}")
        entry_start.pack(fill="x", padx=20)

        tk.Label(dlg, text="End Distance (meters):", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_end = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_end.insert(0, f"{self.current_lap_dist + 150.0:.1f}")
        entry_end.pack(fill="x", padx=20)

        def on_confirm():
            name = entry_name.get().strip()
            try:
                start = float(entry_start.get().strip())
                end = float(entry_end.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Distances must be valid numbers.")
                return

            self.corners.append({"name": name, "start": start, "end": end})
            self.refresh_table()
            dlg.destroy()

        btn_box = tk.Frame(dlg, bg=THEME_BG)
        btn_box.pack(fill="x", padx=20, pady=20)
        tk.Button(btn_box, text="Add Corner", bg=THEME_SUCCESS, fg="#000000", font=(FONT_FAMILY, 9, "bold"),
                  command=on_confirm, padx=12, pady=4, relief="flat").pack(side="right")
        tk.Button(btn_box, text="Cancel", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=dlg.destroy, padx=12, pady=4, relief="flat").pack(side="right", padx=8)

    def edit_selected_corner(self):
        """Edit selected corner values."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Select a corner in the list to edit.")
            return

        idx = int(sel[0])
        corner = self.corners[idx]

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Edit {corner.get('name')}")
        dlg.geometry("380x260")
        dlg.configure(bg=THEME_BG)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Corner Name:", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(15, 2))
        entry_name = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_name.insert(0, corner.get("name", ""))
        entry_name.pack(fill="x", padx=20)

        tk.Label(dlg, text="Start Distance (meters):", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_start = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_start.insert(0, str(corner.get("start", 0.0)))
        entry_start.pack(fill="x", padx=20)

        tk.Label(dlg, text="End Distance (meters):", bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_end = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
        entry_end.insert(0, str(corner.get("end", 0.0)))
        entry_end.pack(fill="x", padx=20)

        def on_confirm():
            name = entry_name.get().strip()
            try:
                start = float(entry_start.get().strip())
                end = float(entry_end.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Distances must be valid numbers.")
                return

            self.corners[idx] = {"name": name, "start": start, "end": end}
            self.refresh_table()
            self.tree.selection_set(str(idx))
            dlg.destroy()

        btn_box = tk.Frame(dlg, bg=THEME_BG)
        btn_box.pack(fill="x", padx=20, pady=20)
        tk.Button(btn_box, text="Update", bg=THEME_SUCCESS, fg="#000000", font=(FONT_FAMILY, 9, "bold"),
                  command=on_confirm, padx=12, pady=4, relief="flat").pack(side="right")
        tk.Button(btn_box, text="Cancel", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=dlg.destroy, padx=12, pady=4, relief="flat").pack(side="right", padx=8)

    def delete_selected_corner(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        del self.corners[idx]
        self.refresh_table()

    def nudge_selected(self, field, amount):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        val = self.corners[idx].get(field, 0.0) + amount
        if self.track_length > 0:
            val = val % self.track_length
        self.corners[idx][field] = round(val, 1)
        self.refresh_table()
        self.tree.selection_set(str(idx))

    def move_corner_up(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx > 0:
            self.corners[idx - 1], self.corners[idx] = self.corners[idx], self.corners[idx - 1]
            self.refresh_table()
            self.tree.selection_set(str(idx - 1))

    def move_corner_down(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < len(self.corners) - 1:
            self.corners[idx + 1], self.corners[idx] = self.corners[idx], self.corners[idx + 1]
            self.refresh_table()
            self.tree.selection_set(str(idx + 1))

    def sort_corners(self):
        """Sort corners ascending by start distance."""
        self.corners.sort(key=lambda x: x.get("start", 0.0))
        self.refresh_table()

    def close(self):
        self.running = False
        self.root.destroy()

def main():
    root = tk.Tk()
    app = CornerMapperApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()

if __name__ == "__main__":
    main()
