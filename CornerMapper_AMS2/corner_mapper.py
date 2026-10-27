import ctypes
import mmap
import os
import sys
import json
import time
import threading
import bisect
import math
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

# Visual Map Theme Colors
THEME_CAR = "#f1fa8c"
THEME_TRACK_BG = "#14141e"
THEME_TRACK_BASE = "#282a36"
THEME_TRACK_LINE = "#6272a4"
THEME_HANDLE_IN = "#50fa7b"
THEME_HANDLE_OUT = "#ff5555"
THEME_ZONE_NORMAL = "#bd93f9"
THEME_ZONE_SELECTED = "#ffb86c"


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
    SIM_NAME = "AMS2"

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.SIM_NAME} Corner Mapper & Track Manager")
        self.root.geometry("1180x760")
        self.root.minsize(860, 600)
        self.root.configure(bg=THEME_BG)

        self.shm = None
        self.data = None
        self.is_connected = False
        self.running = True

        # Mode State (Normal vs. 500x450 Always-On-Top Overlay)
        self.is_overlay = False
        self.prev_geometry = "1180x760"

        # Telemetry State
        self.current_speed = 0.0
        self.current_lap_dist = 0.0
        self.track_length = 0.0
        self.track_location = ""
        self.track_variation = ""
        self.current_lap = 0
        self.car_world_pos = (0.0, 0.0, 0.0)  # (wx, wz, dist)

        # Track Map Trace Data (from driving a lap in the session)
        self.track_bins = {}           # bin_idx -> (wx, wz, dist)
        self.track_points = []         # Sorted [(wx, wz, dist), ...]
        self._track_distances = []     # Sorted [dist, ...]
        self.screen_points = []        # [(cx, cy, dist), ...]
        self.need_track_rebuild = False

        # Canvas Transform Cache
        self.canvas_w = 400
        self.canvas_h = 400
        self.bounds_min_x = 0.0
        self.bounds_max_x = 0.0
        self.bounds_min_z = 0.0
        self.bounds_max_z = 0.0
        self.map_scale = 1.0
        self.map_offset_x = 0.0
        self.map_offset_y = 0.0
        self.zoom_level = 1.0

        # Interactive Canvas State
        self.drag_info = None          # {"corner_idx": int, "type": "start"|"end"}
        self.selected_corner_idx = -1

        # Recording State (Drive & Mark)
        self.is_recording_corner = False
        self.corner_start_dist = 0.0
        self.last_detected_track_key = ""

        # Track File Data (Exact same JSON structure)
        self.current_track_file = ""
        self.corners = []              # List of {"name": str, "start": float, "end": float}

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
        self.header_frame = tk.Frame(self.root, bg=THEME_BG_CARD, padx=14, pady=10, highlightbackground=THEME_BORDER, highlightthickness=1)
        self.header_frame.pack(fill="x", padx=16, pady=(12, 6))

        # Status & Connection Row
        self.top_row = tk.Frame(self.header_frame, bg=THEME_BG_CARD)
        self.top_row.pack(fill="x")

        self.lbl_status = tk.Label(self.top_row, text=f"🔴 Connecting to {self.SIM_NAME}...", bg=THEME_BG_CARD, fg=THEME_DANGER, font=(FONT_FAMILY, 10, "bold"))
        self.lbl_status.pack(side="left")

        # Overlay Mode Toggle Button (always visible in header)
        self.btn_overlay_toggle = tk.Button(self.top_row, text="📌 Overlay Mode (F7)", bg=THEME_ACCENT, fg="#000000",
                                            font=(FONT_FAMILY, 9, "bold"), command=self.toggle_overlay_mode,
                                            relief="flat", padx=10, pady=2, cursor="hand2")
        self.btn_overlay_toggle.pack(side="right", padx=(8, 0))

        self.lbl_active_corner = tk.Label(self.top_row, text="⚪ ON STRAIGHT", bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 10, "bold"))
        self.lbl_active_corner.pack(side="right", padx=10)

        # Telemetry Metrics Row
        self.metric_row = tk.Frame(self.header_frame, bg=THEME_BG_CARD)
        self.metric_row.pack(fill="x", pady=(6, 2))

        self.lbl_telemetry_track = tk.Label(self.metric_row, text="Sim Track: Waiting for game...", bg=THEME_BG_CARD, fg=THEME_INFO, font=(FONT_FAMILY, 9))
        self.lbl_telemetry_track.pack(side="left")

        self.lbl_telemetry_dist = tk.Label(self.metric_row, text="Lap Dist: 0.0 m / 0.0 m (0.0%)", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9, "bold"))
        self.lbl_telemetry_dist.pack(side="right")

        self.lbl_telemetry_speed = tk.Label(self.metric_row, text="Speed: 0 km/h", bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9))
        self.lbl_telemetry_speed.pack(side="right", padx=(0, 20))

        # Progress bar for lap
        self.lap_progress = ttk.Progressbar(self.header_frame, orient="horizontal", mode="determinate")
        self.lap_progress.pack(fill="x", pady=(4, 0))

        # --- Track Profile Bar (Select, New, Edit, Delete Tracks) ---
        self.track_bar = tk.Frame(self.root, bg=THEME_BG, padx=16, pady=4)
        self.track_bar.pack(fill="x")

        tk.Label(self.track_bar, text="📁 Track File:", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9, "bold")).pack(side="left", padx=(0, 8))

        self.track_combo_var = tk.StringVar()
        self.track_combo = ttk.Combobox(self.track_bar, textvariable=self.track_combo_var, state="readonly", width=34)
        self.track_combo.pack(side="left", padx=(0, 8))
        self.track_combo.bind("<<ComboboxSelected>>", self.on_track_selected)

        btn_new_track = tk.Button(self.track_bar, text="➕ New Track", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                                  command=self.create_new_track, relief="flat", padx=8, pady=2, cursor="hand2")
        btn_new_track.pack(side="left", padx=2)

        btn_edit_track = tk.Button(self.track_bar, text="✏️ Edit Info", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                                   command=self.edit_track_info, relief="flat", padx=8, pady=2, cursor="hand2")
        btn_edit_track.pack(side="left", padx=2)

        btn_delete_track = tk.Button(self.track_bar, text="🗑️ Delete", bg=THEME_DANGER_BG, fg=THEME_DANGER, font=(FONT_FAMILY, 8),
                                     command=self.delete_track_file, relief="flat", padx=8, pady=2, cursor="hand2")
        btn_delete_track.pack(side="left", padx=2)

        btn_refresh_tracks = tk.Button(self.track_bar, text="🔄 Refresh", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                                       command=self.refresh_track_list, relief="flat", padx=8, pady=2, cursor="hand2")
        btn_refresh_tracks.pack(side="left", padx=2)

        # --- Real-Time Corner Recording Panel (Drive & Mark) ---
        self.record_frame = tk.LabelFrame(self.root, text=" 🏎️ Drive & Mark Corners (In-Game or Live) ", bg=THEME_BG_CARD, fg=THEME_ACCENT,
                                          font=(FONT_FAMILY, 9, "bold"), padx=12, pady=6, highlightbackground=THEME_BORDER, highlightthickness=1)
        self.record_frame.pack(fill="x", padx=16, pady=4)

        rec_top = tk.Frame(self.record_frame, bg=THEME_BG_CARD)
        rec_top.pack(fill="x")

        tk.Label(rec_top, text="Next Corner Name:", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(side="left", padx=(0, 6))
        self.corner_name_var = tk.StringVar(value="Turn 1")
        self.entry_corner_name = tk.Entry(rec_top, textvariable=self.corner_name_var, bg=THEME_BG_INPUT, fg=THEME_FG,
                                          insertbackground=THEME_FG, font=(FONT_FAMILY, 9), width=18, relief="flat")
        self.entry_corner_name.pack(side="left", padx=(0, 10))

        self.btn_mark_start = tk.Button(rec_top, text="🚩 Mark Start (F9 / Space)", bg=THEME_INFO, fg="#000000",
                                        font=(FONT_FAMILY, 9, "bold"), command=self.mark_corner_start, relief="flat",
                                        padx=10, pady=4, cursor="hand2")
        self.btn_mark_start.pack(side="left", padx=3)

        self.btn_mark_end = tk.Button(rec_top, text="🏁 Mark End (F10 / Enter)", bg=THEME_SUCCESS, fg="#000000",
                                      font=(FONT_FAMILY, 9, "bold"), command=self.mark_corner_end, relief="flat",
                                      padx=10, pady=4, cursor="hand2", state="disabled")
        self.btn_mark_end.pack(side="left", padx=3)

        self.btn_toggle_rec = tk.Button(rec_top, text="⏺️ Toggle (F8)", bg=THEME_ACCENT, fg="#000000",
                                        font=(FONT_FAMILY, 9, "bold"), command=self.toggle_recording, relief="flat",
                                        padx=10, pady=4, cursor="hand2")
        self.btn_toggle_rec.pack(side="left", padx=3)

        self.lbl_rec_status = tk.Label(self.record_frame, text="💡 Tip: Press F9 when entering a corner, then F10 when exiting. Or use the visual track map below!",
                                       bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 8), anchor="w")
        self.lbl_rec_status.pack(fill="x", pady=(4, 0))

        # --- Main Workspace: Map Canvas (Left) + Corner Table (Right) ---
        self.main_container = tk.Frame(self.root, bg=THEME_BG)
        self.main_container.pack(fill="both", expand=True, padx=16, pady=4)

        # LEFT SIDE: Visual Track Map Frame
        self.map_frame = tk.Frame(self.main_container, bg=THEME_BG)
        self.map_frame.pack(side="left", fill="both", expand=True)

        # Map Toolbar (Normal Mode)
        self.map_header_bar = tk.Frame(self.map_frame, bg=THEME_BG)
        self.map_header_bar.pack(fill="x", pady=(0, 4))

        tk.Label(self.map_header_bar, text="🗺️ Interactive Track Map", bg=THEME_BG, fg=THEME_ACCENT, font=(FONT_FAMILY, 9, "bold")).pack(side="left")

        btn_clear_map = tk.Button(self.map_header_bar, text="🔄 Clear Lap Map", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                                  command=self.clear_recorded_map, relief="flat", padx=6, pady=1, cursor="hand2")
        btn_clear_map.pack(side="right", padx=2)

        btn_fit_zoom = tk.Button(self.map_header_bar, text="🔍 Fit Track", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                                 command=self.reset_map_zoom, relief="flat", padx=6, pady=1, cursor="hand2")
        btn_fit_zoom.pack(side="right", padx=2)

        btn_add_car_corner = tk.Button(self.map_header_bar, text="➕ Add Corner at Car", bg=THEME_INFO, fg="#000000", font=(FONT_FAMILY, 8, "bold"),
                                       command=self.add_corner_at_car, relief="flat", padx=8, pady=1, cursor="hand2")
        btn_add_car_corner.pack(side="right", padx=4)

        # Interactive Canvas
        self.canvas = tk.Canvas(self.map_frame, bg=THEME_TRACK_BG, highlightthickness=1, highlightbackground=THEME_BORDER)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<Double-1>", self.on_canvas_double_click)
        self.canvas.bind("<Button-3>", self.on_canvas_right_click)

        # Overlay Mode Bottom Toolbar (packed only in overlay mode)
        self.overlay_controls = tk.Frame(self.map_frame, bg=THEME_BG_CARD, padx=8, pady=6, highlightthickness=1, highlightbackground=THEME_BORDER)

        ov_row1 = tk.Frame(self.overlay_controls, bg=THEME_BG_CARD)
        ov_row1.pack(fill="x")

        tk.Label(ov_row1, text="Next:", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8)).pack(side="left", padx=(0, 4))
        self.entry_overlay_name = tk.Entry(ov_row1, textvariable=self.corner_name_var, bg=THEME_BG_INPUT, fg=THEME_FG,
                                           insertbackground=THEME_FG, font=(FONT_FAMILY, 8), width=11, relief="flat")
        self.entry_overlay_name.pack(side="left", padx=(0, 6))

        tk.Button(ov_row1, text="➕ Add at Car", bg=THEME_INFO, fg="#000000", font=(FONT_FAMILY, 8, "bold"),
                  command=self.add_corner_at_car, relief="flat", padx=6, pady=2, cursor="hand2").pack(side="left", padx=2)

        tk.Button(ov_row1, text="💾 Save", bg=THEME_SUCCESS, fg="#000000", font=(FONT_FAMILY, 8, "bold"),
                  command=self.save_current_track_file, relief="flat", padx=8, pady=2, cursor="hand2").pack(side="left", padx=4)

        tk.Button(ov_row1, text="🔄 Clear Map", bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 8),
                  command=self.clear_recorded_map, relief="flat", padx=4, pady=2, cursor="hand2").pack(side="right", padx=2)

        self.lbl_overlay_status = tk.Label(self.overlay_controls, text="💡 Drag Green (In) / Red (Out) handles to set corners. Ctrl+S to save.",
                                           bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 8), anchor="w")
        self.lbl_overlay_status.pack(fill="x", pady=(3, 0))

        # RIGHT SIDE: Corner Table & Actions (Normal Mode)
        self.table_container = tk.Frame(self.main_container, bg=THEME_BG)
        self.table_container.pack(side="right", fill="both", expand=True, padx=(10, 0))

        # Left of table_container: Treeview
        tree_frame = tk.Frame(self.table_container, bg=THEME_BG)
        tree_frame.pack(side="left", fill="both", expand=True)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=THEME_BG_CARD, foreground=THEME_FG, fieldbackground=THEME_BG_CARD,
                        rowheight=24, font=(FONT_FAMILY, 8))
        style.configure("Treeview.Heading", background=THEME_BG_INPUT, foreground=THEME_ACCENT, font=(FONT_FAMILY, 8, "bold"))
        style.map("Treeview", background=[("selected", THEME_ACCENT)], foreground=[("selected", "#000000")])

        columns = ("name", "start", "end", "length")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Corner Name")
        self.tree.heading("start", text="Start Dist (m)")
        self.tree.heading("end", text="End Dist (m)")
        self.tree.heading("length", text="Length (m)")

        self.tree.column("name", width=140, anchor="w")
        self.tree.column("start", width=80, anchor="center")
        self.tree.column("end", width=80, anchor="center")
        self.tree.column("length", width=80, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self.edit_selected_corner())
        self.tree.bind("<Delete>", lambda e: self.delete_selected_corner())
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # Right of table_container: Action Toolbar
        action_bar = tk.Frame(self.table_container, bg=THEME_BG, padx=8)
        action_bar.pack(side="right", fill="y")

        tk.Label(action_bar, text="Corner Actions", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w", pady=(0, 4))

        tk.Button(action_bar, text="➕ Add Manually", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=self.add_corner_manually, relief="flat", padx=6, pady=3, cursor="hand2", width=14).pack(pady=1)

        tk.Button(action_bar, text="✏️ Edit Corner", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=self.edit_selected_corner, relief="flat", padx=6, pady=3, cursor="hand2", width=14).pack(pady=1)

        tk.Button(action_bar, text="🗑️ Delete Corner", bg=THEME_DANGER_BG, fg=THEME_DANGER, font=(FONT_FAMILY, 8),
                  command=self.delete_selected_corner, relief="flat", padx=6, pady=3, cursor="hand2", width=14).pack(pady=1)

        tk.Frame(action_bar, bg=THEME_BORDER, height=1).pack(fill="x", pady=6)

        tk.Label(action_bar, text="Nudge Dist", bg=THEME_BG, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 8, "bold")).pack(anchor="w", pady=(0, 2))

        nudge_row1 = tk.Frame(action_bar, bg=THEME_BG)
        nudge_row1.pack(fill="x", pady=1)
        tk.Button(nudge_row1, text="In -5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 7),
                  command=lambda: self.nudge_selected('start', -5), relief="flat", width=6).pack(side="left", padx=1)
        tk.Button(nudge_row1, text="In +5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 7),
                  command=lambda: self.nudge_selected('start', 5), relief="flat", width=6).pack(side="right", padx=1)

        nudge_row2 = tk.Frame(action_bar, bg=THEME_BG)
        nudge_row2.pack(fill="x", pady=1)
        tk.Button(nudge_row2, text="Out -5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 7),
                  command=lambda: self.nudge_selected('end', -5), relief="flat", width=6).pack(side="left", padx=1)
        tk.Button(nudge_row2, text="Out +5m", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 7),
                  command=lambda: self.nudge_selected('end', 5), relief="flat", width=6).pack(side="right", padx=1)

        tk.Frame(action_bar, bg=THEME_BORDER, height=1).pack(fill="x", pady=6)

        tk.Button(action_bar, text="🔼 Move Up", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=self.move_corner_up, relief="flat", padx=6, pady=2, cursor="hand2", width=14).pack(pady=1)

        tk.Button(action_bar, text="🔽 Move Down", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=self.move_corner_down, relief="flat", padx=6, pady=2, cursor="hand2", width=14).pack(pady=1)

        tk.Button(action_bar, text="🔢 Sort by Dist", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                  command=self.sort_corners, relief="flat", padx=6, pady=2, cursor="hand2", width=14).pack(pady=1)

        # --- Bottom Bar: Save & Status (Normal Mode) ---
        self.bottom_bar = tk.Frame(self.root, bg=THEME_BG_CARD, padx=16, pady=8, highlightbackground=THEME_BORDER, highlightthickness=1)
        self.bottom_bar.pack(fill="x", padx=16, pady=(6, 12))

        self.lbl_bottom_status = tk.Label(self.bottom_bar, text=f"Ready. Drive a lap in {self.SIM_NAME} to trace map, or load an existing track profile.",
                                          bg=THEME_BG_CARD, fg=THEME_FG_MUTED, font=(FONT_FAMILY, 9))
        self.lbl_bottom_status.pack(side="left")

        btn_save = tk.Button(self.bottom_bar, text="💾 Save Changes to File", bg=THEME_SUCCESS, fg="#000000",
                             font=(FONT_FAMILY, 9, "bold"), command=self.save_current_track_file, relief="flat",
                             padx=14, pady=3, cursor="hand2")
        btn_save.pack(side="right")

        btn_save_as = tk.Button(self.bottom_bar, text="Save As...", bg=THEME_BG, fg=THEME_FG,
                                font=(FONT_FAMILY, 8), command=self.save_track_as, relief="flat",
                                padx=8, pady=3, cursor="hand2")
        btn_save_as.pack(side="right", padx=6)

    def _setup_keybindings(self):
        self.root.bind("<F9>", lambda e: self.mark_corner_start())
        self.root.bind("<F10>", lambda e: self.mark_corner_end())
        self.root.bind("<F8>", lambda e: self.toggle_recording())
        self.root.bind("<F7>", lambda e: self.toggle_overlay_mode())
        self.root.bind("<space>", self._on_space_pressed)
        self.root.bind("<Return>", self._on_enter_pressed)
        self.root.bind("<Control-s>", lambda e: self.save_current_track_file())
        self.root.bind("<Control-S>", lambda e: self.save_current_track_file())

    def _on_space_pressed(self, event):
        if event.widget not in (self.entry_corner_name, getattr(self, "entry_overlay_name", None)):
            if not self.is_recording_corner:
                self.mark_corner_start()

    def _on_enter_pressed(self, event):
        if self.is_recording_corner:
            self.mark_corner_end()

    # --- Mode Toggle: Normal Editor vs. 500x450 Always-On-Top Overlay ---
    def toggle_overlay_mode(self):
        if not self.is_overlay:
            # Switch to Overlay Mode (500x450, always-on-top)
            self.prev_geometry = self.root.geometry()
            self.is_overlay = True
            self.root.attributes("-topmost", True)

            # Hide normal editor panels
            self.track_bar.pack_forget()
            self.record_frame.pack_forget()
            self.table_container.pack_forget()
            self.bottom_bar.pack_forget()
            self.map_header_bar.pack_forget()

            # Pack overlay controls inside map frame
            self.overlay_controls.pack(fill="x", padx=4, pady=(2, 4), side="bottom")

            # Compact layout padding
            self.main_container.pack_configure(padx=6, pady=2)
            self.header_frame.pack_configure(padx=6, pady=(6, 2))

            self.btn_overlay_toggle.config(text="🪟 Normal (F7)", bg=THEME_INFO)
            self.root.title(f"{self.SIM_NAME} Corner Mapper [OVERLAY]")
            self.root.minsize(400, 320)
            self.root.geometry("500x450")
            self.redraw_map()
        else:
            # Switch back to Normal Editor Mode
            self.is_overlay = False
            self.root.attributes("-topmost", False)

            # Hide overlay controls
            self.overlay_controls.pack_forget()

            # Restore normal layout
            self.main_container.pack_configure(padx=16, pady=4)
            self.header_frame.pack_configure(padx=16, pady=(12, 6))

            self.map_header_bar.pack(fill="x", pady=(0, 4), before=self.canvas)
            self.track_bar.pack(fill="x", padx=16, pady=4, after=self.header_frame)
            self.record_frame.pack(fill="x", padx=16, pady=4, after=self.track_bar)
            self.table_container.pack(side="right", fill="both", expand=True, padx=(10, 0))
            self.bottom_bar.pack(fill="x", padx=16, pady=(6, 12), side="bottom")

            self.btn_overlay_toggle.config(text="📌 Overlay Mode (F7)", bg=THEME_ACCENT)
            self.root.title(f"{self.SIM_NAME} Corner Mapper & Track Manager")
            self.root.minsize(860, 600)
            self.root.geometry(self.prev_geometry if self.prev_geometry else "1180x760")
            self.redraw_map()

    # --- Telemetry Background Worker ---
    def _telemetry_worker(self):
        VK_F7 = 0x76
        VK_F8 = 0x77
        VK_F9 = 0x78
        VK_F10 = 0x79

        f7_down_prev = False
        f8_down_prev = False
        f9_down_prev = False
        f10_down_prev = False

        while self.running:
            try:
                # 1 + 2. Connect and read live data
                if not self._poll_sim():
                    time.sleep(1.0)
                    continue

                # 3. Global Hotkey Checking (allows F7/F8/F9/F10 to trigger even with the game focused)
                try:
                    f7_state = ctypes.windll.user32.GetAsyncKeyState(VK_F7) & 0x8000 != 0
                    if f7_state and not f7_down_prev:
                        self.root.after(0, self.toggle_overlay_mode)
                    f7_down_prev = f7_state

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

            except Exception:
                self.is_connected = False
                self.shm = None

            time.sleep(0.04)  # ~25Hz polling

    def _record_track_point(self, wx, wz):
        """Store the car position and record track outline points as the player drives."""
        self.car_world_pos = (wx, wz, self.current_lap_dist)
        if self.track_length > 100.0 and self.current_speed > 2.0 and 0.0 <= self.current_lap_dist <= self.track_length:
            bin_size = 4.0  # Sample every 4 meters
            bin_idx = int(self.current_lap_dist / bin_size)
            if bin_idx not in self.track_bins:
                self.track_bins[bin_idx] = (wx, wz, self.current_lap_dist)
                self.need_track_rebuild = True

    def _poll_sim(self):
        """Connect to the AMS2 shared memory and read the viewed car. Returns False while disconnected."""
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
                return False

        if self.data:
            view_idx = self.data.mViewedParticipantIndex
            if view_idx < 0 or view_idx >= STORED_PARTICIPANTS_MAX:
                view_idx = 0

            p = self.data.mParticipantInfo[view_idx]
            self.current_lap_dist = float(p.mCurrentLapDistance)
            self.current_lap = int(p.mCurrentLap)
            self.current_speed = float(self.data.mSpeed) * 3.6  # m/s to km/h
            self.track_length = float(self.data.mTrackLength)

            try:
                self.track_location = self.data.mTrackLocation.decode('utf-8', errors='ignore').strip('\x00')
                self.track_variation = self.data.mTrackVariation.decode('utf-8', errors='ignore').strip('\x00')
            except Exception:
                pass

            # Sample World Coordinates (X, Z) for the live track map
            try:
                self._record_track_point(float(p.mWorldPosition[0]), float(p.mWorldPosition[2]))
            except Exception:
                pass
        return True

    # --- UI Periodic Update Loop ---
    def _update_ui_loop(self):
        if not self.running:
            return

        if self.is_connected and self.track_length > 0:
            self.lbl_status.config(text=f"🟢 Connected to {self.SIM_NAME}", fg=THEME_SUCCESS)
            self.lbl_telemetry_track.config(text=f"Track: {self.track_location} - {self.track_variation} ({self.track_length:.1f} m)")

            lap_pct = min(100.0, max(0.0, (self.current_lap_dist / self.track_length) * 100.0))
            self.lbl_telemetry_dist.config(text=f"Lap Dist: {self.current_lap_dist:.1f} m / {self.track_length:.1f} m ({lap_pct:.1f}%)")
            self.lbl_telemetry_speed.config(text=f"Speed: {self.current_speed:.0f} km/h (Lap {self.current_lap})")
            self.lap_progress['value'] = lap_pct

            # Check if game switched tracks
            track_key = f"{self.track_location}_{self.track_variation}".strip("_")
            if track_key and track_key != self.last_detected_track_key and self.track_location:
                self.last_detected_track_key = track_key
                self.clear_recorded_map()
                self._auto_detect_track_from_sim()

            # Active corner feedback
            active_corner = self.get_corner_at_distance(self.current_lap_dist)
            if active_corner:
                self.lbl_active_corner.config(text=f"🟢 IN CORNER: {active_corner}", fg=THEME_SUCCESS)
            elif self.is_recording_corner:
                self.lbl_active_corner.config(text=f"🔴 RECORDING '{self.corner_name_var.get()}' ({self.corner_start_dist:.1f}m)...", fg=THEME_WARNING)
            else:
                self.lbl_active_corner.config(text="⚪ ON STRAIGHT", fg=THEME_FG_MUTED)

            # Rebuild track outline if new points were recorded
            if self.need_track_rebuild:
                self.need_track_rebuild = False
                self.track_points = sorted(self.track_bins.values(), key=lambda pt: pt[2])
                self._track_distances = [pt[2] for pt in self.track_points]
                self.redraw_map()

            # Update live car position dot on canvas
            self.update_car_marker()

        else:
            self.lbl_status.config(text=f"🔴 Waiting for {self.SIM_NAME}...", fg=THEME_DANGER)
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
            else:  # Wrap around start/finish
                if dist >= start or dist <= end:
                    return name
        return None

    # --- Track Map Coordinate Transformations & Geometry ---
    def _on_canvas_configure(self, event):
        if event.width > 50 and event.height > 50:
            self.canvas_w = event.width
            self.canvas_h = event.height
            self.redraw_map()

    def reset_map_zoom(self):
        self.zoom_level = 1.0
        self.redraw_map()

    def clear_recorded_map(self):
        self.track_bins.clear()
        self.track_points.clear()
        self._track_distances.clear()
        self.screen_points.clear()
        self.redraw_map()
        self.lbl_bottom_status.config(text=f"Map cleared. Drive a lap in {self.SIM_NAME} to trace the circuit outline.", fg=THEME_INFO)
        if hasattr(self, 'lbl_overlay_status'):
            self.lbl_overlay_status.config(text="Map cleared. Drive a lap to trace.", fg=THEME_INFO)

    def world_to_canvas(self, wx, wz):
        """Transform world coordinate (wx, wz) to canvas pixel (cx, cy)."""
        cx = self.map_offset_x + (wx - self.bounds_min_x) * self.map_scale
        cy = self.canvas_h - (self.map_offset_y + (wz - self.bounds_min_z) * self.map_scale)
        return (cx, cy)

    def dist_to_world(self, dist):
        """Interpolate (wx, wz) at a given lap distance using binary search."""
        if not self.track_points:
            return None
        if dist <= self.track_points[0][2]:
            return (self.track_points[0][0], self.track_points[0][1])
        if dist >= self.track_points[-1][2]:
            return (self.track_points[-1][0], self.track_points[-1][1])

        idx = bisect.bisect_right(self._track_distances, dist)
        if idx == 0:
            return (self.track_points[0][0], self.track_points[0][1])
        if idx >= len(self.track_points):
            return (self.track_points[-1][0], self.track_points[-1][1])

        p1 = self.track_points[idx - 1]
        p2 = self.track_points[idx]
        d1, d2 = p1[2], p2[2]
        if d2 <= d1:
            return (p1[0], p1[1])
        t = (dist - d1) / (d2 - d1)
        wx = p1[0] + t * (p2[0] - p1[0])
        wz = p1[1] + t * (p2[1] - p1[1])
        return (wx, wz)

    def dist_to_canvas(self, dist):
        """Get canvas pixel (cx, cy) for a lap distance."""
        wpos = self.dist_to_world(dist)
        if wpos is None:
            return None
        return self.world_to_canvas(wpos[0], wpos[1])

    def canvas_to_track_dist(self, mx, my):
        """Project a canvas click/drag point onto the track polyline to find closest lap distance."""
        if not self.screen_points or len(self.screen_points) < 2:
            return 0.0

        best_dist = 0.0
        best_sq = float("inf")

        for i in range(len(self.screen_points) - 1):
            x1, y1, d1 = self.screen_points[i]
            x2, y2, d2 = self.screen_points[i + 1]

            dx = x2 - x1
            dy = y2 - y1
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 0.0001:
                continue

            t = max(0.0, min(1.0, ((mx - x1) * dx + (my - y1) * dy) / seg_len_sq))
            proj_x = x1 + t * dx
            proj_y = y1 + t * dy

            sq = (mx - proj_x) ** 2 + (my - proj_y) ** 2
            if sq < best_sq:
                best_sq = sq
                best_dist = d1 + t * (d2 - d1)

        return max(0.0, min(self.track_length if self.track_length > 0 else 99999.0, best_dist))

    # --- Canvas Drawing & Rendering ---
    def redraw_map(self):
        """Full redraw of the canvas: track outline, corners, handles, and labels."""
        self.canvas.delete("all")

        # 1. Check if we have track points
        if len(self.track_points) < 2:
            # Draw friendly guidance message
            cx = self.canvas_w / 2
            cy = self.canvas_h / 2
            self.canvas.create_text(cx, cy - 25, text=f"🏎️ {self.SIM_NAME} Visual Track Map", fill=THEME_ACCENT,
                                    font=(FONT_FAMILY, 12, "bold"))
            self.canvas.create_text(cx, cy + 5, text=f"Drive a lap in {self.SIM_NAME} to trace the track layout.", fill=THEME_FG,
                                    font=(FONT_FAMILY, 9))
            self.canvas.create_text(cx, cy + 28, text="Corners and In/Out handles will appear here.", fill=THEME_FG_MUTED,
                                    font=(FONT_FAMILY, 9))
            self.canvas.create_text(cx, cy + 52, text="Press F7 to toggle Overlay Mode on top of the game!", fill=THEME_INFO,
                                    font=(FONT_FAMILY, 8, "italic"))
            return

        # 2. Compute Track Bounds and Scaling
        self.bounds_min_x = min(pt[0] for pt in self.track_points)
        self.bounds_max_x = max(pt[0] for pt in self.track_points)
        self.bounds_min_z = min(pt[1] for pt in self.track_points)
        self.bounds_max_z = max(pt[1] for pt in self.track_points)

        margin = 28
        avail_w = max(10, self.canvas_w - 2 * margin)
        avail_h = max(10, self.canvas_h - 2 * margin)

        dx = max(1.0, self.bounds_max_x - self.bounds_min_x)
        dz = max(1.0, self.bounds_max_z - self.bounds_min_z)

        base_scale = min(avail_w / dx, avail_h / dz)
        self.map_scale = base_scale * self.zoom_level

        self.map_offset_x = margin + (avail_w - dx * self.map_scale) / 2.0
        self.map_offset_y = margin + (avail_h - dz * self.map_scale) / 2.0

        # 3. Calculate Screen Points for all track nodes
        self.screen_points = []
        for pt in self.track_points:
            cx, cy = self.world_to_canvas(pt[0], pt[1])
            self.screen_points.append((cx, cy, pt[2]))

        # 4. Draw Base Track Road Polyline
        flat_coords = [coord for pt in self.screen_points for coord in (pt[0], pt[1])]
        if self.track_length > 0 and (self.track_points[-1][2] - self.track_points[0][2]) > (0.85 * self.track_length):
            flat_coords.extend([self.screen_points[0][0], self.screen_points[0][1]])

        # Dark road asphalt
        self.canvas.create_line(flat_coords, fill=THEME_TRACK_BASE, width=12, capstyle="round", joinstyle="round", tags="track_road")
        # Center racing line
        self.canvas.create_line(flat_coords, fill=THEME_TRACK_LINE, width=4, capstyle="round", joinstyle="round", tags="track_center")

        # 5. Start / Finish Line marker
        if self.screen_points:
            s_cx, s_cy, _ = self.screen_points[0]
            self.canvas.create_oval(s_cx - 4, s_cy - 4, s_cx + 4, s_cy + 4, fill="#ffffff", outline="#000000", width=1, tags="sf_marker")
            self.canvas.create_text(s_cx + 12, s_cy, text="S/F", fill="#ffffff", font=(FONT_FAMILY, 7, "bold"), tags="sf_marker")

        # 6. Lap tracing coverage indicator
        if self.track_length > 0:
            coverage = min(100.0, (len(self.track_bins) * 4.0 / self.track_length) * 100.0)
            if coverage < 90.0:
                self.canvas.create_text(margin + 50, 16, text=f"Tracing Lap: {coverage:.0f}%", fill=THEME_WARNING,
                                        font=(FONT_FAMILY, 8, "bold"), anchor="w")

        # 7. Draw Corners, Draggable In/Out Handles, and Name Labels
        self.draw_corners_and_handles()

        # 8. Draw Live Car Marker
        self.update_car_marker()

    def draw_corners_and_handles(self):
        """Draw or refresh the corner highlights, draggable In/Out handles, and labels layer."""
        self.canvas.delete("corner_layer")

        if not self.track_points or len(self.track_points) < 2:
            return

        for idx, corner in enumerate(self.corners):
            start = corner.get("start", 0.0)
            end = corner.get("end", 0.0)
            name = corner.get("name", f"Turn {idx + 1}")
            is_selected = (idx == self.selected_corner_idx)

            zone_color = THEME_ZONE_SELECTED if is_selected else THEME_ZONE_NORMAL
            zone_width = 8 if is_selected else 6

            # Gather points along the track between start and end
            seg_points = []
            start_pix = self.dist_to_canvas(start)
            end_pix = self.dist_to_canvas(end)

            if start_pix:
                seg_points.append(start_pix)

            if start <= end:
                for pt in self.screen_points:
                    if start <= pt[2] <= end:
                        seg_points.append((pt[0], pt[1]))
            else:  # Wrap around start/finish
                for pt in self.screen_points:
                    if pt[2] >= start or pt[2] <= end:
                        seg_points.append((pt[0], pt[1]))

            if end_pix:
                seg_points.append(end_pix)

            # Draw Highlighted Corner Track Segment
            if len(seg_points) >= 2:
                flat_seg = [coord for pt in seg_points for coord in (pt[0], pt[1])]
                self.canvas.create_line(flat_seg, fill=zone_color, width=zone_width, capstyle="round", joinstyle="round",
                                        tags=("corner_layer", f"corner_seg:{idx}"))

            # Draw Draggable Start Handle (Green "In" Point)
            if start_pix:
                sx, sy = start_pix
                r = 9 if is_selected else 7
                self.canvas.create_oval(sx - r, sy - r, sx + r, sy + r, fill=THEME_HANDLE_IN, outline="#ffffff", width=2,
                                        tags=("corner_layer", "handle", f"handle:start:{idx}"))
                self.canvas.create_text(sx, sy, text="In", fill="#000000", font=(FONT_FAMILY, 6, "bold"),
                                        tags=("corner_layer", "handle", f"handle:start:{idx}"))

            # Draw Draggable End Handle (Red "Out" Point)
            if end_pix:
                ex, ey = end_pix
                r = 9 if is_selected else 7
                self.canvas.create_oval(ex - r, ey - r, ex + r, ey + r, fill=THEME_HANDLE_OUT, outline="#ffffff", width=2,
                                        tags=("corner_layer", "handle", f"handle:end:{idx}"))
                self.canvas.create_text(ex, ey, text="Out", fill="#ffffff", font=(FONT_FAMILY, 6, "bold"),
                                        tags=("corner_layer", "handle", f"handle:end:{idx}"))

            # Draw Corner Name Label at Midpoint
            mid_dist = (start + end) / 2.0 if start <= end else (start + (end + self.track_length)) / 2.0
            if self.track_length > 0:
                mid_dist %= self.track_length
            mid_pix = self.dist_to_canvas(mid_dist)
            if mid_pix:
                mx, my = mid_pix
                label_color = THEME_WARNING if is_selected else THEME_FG
                self.canvas.create_text(mx, my - 13, text=name, fill=label_color, font=(FONT_FAMILY, 8, "bold"),
                                        tags=("corner_layer", "corner_label", f"corner_label:{idx}"))

    def update_car_marker(self):
        """Update live car marker position dot on the track map."""
        self.canvas.delete("car_dot")

        if not self.is_connected or len(self.track_points) < 2:
            return

        car_pix = self.dist_to_canvas(self.current_lap_dist)
        if car_pix:
            cx, cy = car_pix
            # Outer yellow glow ring
            self.canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, outline=THEME_CAR, width=2, tags="car_dot")
            # Inner bright dot
            self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=THEME_DANGER, outline="#ffffff", width=1, tags="car_dot")

    # --- Interactive Canvas Mouse Actions (Drag Handles, Click to Select/Add) ---
    def on_canvas_press(self, event):
        items = self.canvas.find_overlapping(event.x - 8, event.y - 8, event.x + 8, event.y + 8)
        for item in items:
            tags = self.canvas.gettags(item)
            for tag in tags:
                if tag.startswith("handle:start:"):
                    idx = int(tag.split(":")[2])
                    self.drag_info = {"corner_idx": idx, "type": "start"}
                    self.select_corner(idx)
                    return
                elif tag.startswith("handle:end:"):
                    idx = int(tag.split(":")[2])
                    self.drag_info = {"corner_idx": idx, "type": "end"}
                    self.select_corner(idx)
                    return
                elif tag.startswith("corner_label:"):
                    idx = int(tag.split(":")[1])
                    self.select_corner(idx)
                    return
                elif tag.startswith("corner_seg:"):
                    idx = int(tag.split(":")[1])
                    self.select_corner(idx)
                    return

    def on_canvas_drag(self, event):
        if not self.drag_info:
            return

        idx = self.drag_info["corner_idx"]
        drag_type = self.drag_info["type"]
        if idx < 0 or idx >= len(self.corners):
            return

        # Snap dragged handle along the track polyline
        new_dist = self.canvas_to_track_dist(event.x, event.y)
        self.corners[idx][drag_type] = round(new_dist, 1)

        # Update Treeview row in real time
        start = self.corners[idx].get("start", 0.0)
        end = self.corners[idx].get("end", 0.0)
        length = (end - start) if end >= start else (self.track_length - start + end)
        if self.tree.exists(str(idx)):
            self.tree.item(str(idx), values=(
                self.corners[idx].get("name", "Unknown"),
                f"{start:.1f}",
                f"{end:.1f}",
                f"{length:.1f}"
            ))

        # Fast redraw corner handles layer
        self.draw_corners_and_handles()

    def on_canvas_release(self, event):
        if self.drag_info:
            idx = self.drag_info["corner_idx"]
            self.drag_info = None
            self.refresh_table()
            self.draw_corners_and_handles()
            if 0 <= idx < len(self.corners):
                c = self.corners[idx]
                msg = f"Updated '{c.get('name')}': In {c.get('start'):.1f}m, Out {c.get('end'):.1f}m"
                self.lbl_bottom_status.config(text=f"✅ {msg}. Click 'Save' to preserve changes!", fg=THEME_SUCCESS)
                if hasattr(self, 'lbl_overlay_status'):
                    self.lbl_overlay_status.config(text=f"✅ {msg}", fg=THEME_SUCCESS)

    def on_canvas_motion(self, event):
        if self.drag_info:
            return
        items = self.canvas.find_overlapping(event.x - 6, event.y - 6, event.x + 6, event.y + 6)
        is_interactive = False
        for item in items:
            for tag in self.canvas.gettags(item):
                if tag.startswith("handle:") or tag.startswith("corner_label:"):
                    is_interactive = True
                    break
            if is_interactive:
                break

        if is_interactive:
            self.canvas.config(cursor="hand2")
        else:
            self.canvas.config(cursor="")

    def on_canvas_double_click(self, event):
        # Double click on a corner label/handle -> Rename corner dialog
        items = self.canvas.find_overlapping(event.x - 8, event.y - 8, event.x + 8, event.y + 8)
        for item in items:
            for tag in self.canvas.gettags(item):
                if tag.startswith("corner_label:") or tag.startswith("handle:"):
                    idx = int(tag.split(":")[-1])
                    self.rename_corner_dialog(idx)
                    return

        # Double click on empty track line -> Add new corner at clicked location
        if self.track_points:
            clicked_dist = self.canvas_to_track_dist(event.x, event.y)
            self.add_corner_at_dist(clicked_dist)

    def on_canvas_right_click(self, event):
        mx, my = event.x, event.y
        menu = tk.Menu(self.root, tearoff=0, bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9))

        # Check if right-clicked near a corner
        items = self.canvas.find_overlapping(mx - 10, my - 10, mx + 10, my + 10)
        found_idx = None
        for item in items:
            for tag in self.canvas.gettags(item):
                if tag.startswith("handle:") or tag.startswith("corner_label:"):
                    found_idx = int(tag.split(":")[-1])
                    break
            if found_idx is not None:
                break

        if found_idx is not None and 0 <= found_idx < len(self.corners):
            c = self.corners[found_idx]
            self.select_corner(found_idx)
            menu.add_command(label=f"✏️ Rename '{c.get('name')}'", command=lambda: self.rename_corner_dialog(found_idx))
            menu.add_command(label=f"🗑️ Delete '{c.get('name')}'", command=lambda: self.delete_corner_by_idx(found_idx))
            menu.add_separator()

        if self.track_points:
            clicked_dist = self.canvas_to_track_dist(mx, my)
            menu.add_command(label=f"➕ Add Corner Here (~{clicked_dist:.0f}m)", command=lambda: self.add_corner_at_dist(clicked_dist))

        menu.add_command(label="🔄 Clear Recorded Lap", command=self.clear_recorded_map)
        menu.post(event.x_root, event.y_root)

    def select_corner(self, idx):
        if 0 <= idx < len(self.corners):
            self.selected_corner_idx = idx
            if self.tree.exists(str(idx)):
                self.tree.selection_set(str(idx))
                self.tree.see(str(idx))
            self.draw_corners_and_handles()

    def on_tree_select(self, event):
        sel = self.tree.selection()
        if sel:
            self.selected_corner_idx = int(sel[0])
            self.draw_corners_and_handles()

    # --- Corner Management Operations ---
    def add_corner_at_car(self):
        """Add a corner at the current car location with draggable In/Out handles."""
        dist = self.current_lap_dist
        name = self.corner_name_var.get().strip() or f"Turn {len(self.corners) + 1}"
        start = round(dist, 1)
        end = round(min(self.track_length, dist + 120.0) if self.track_length > 0 else dist + 120.0, 1)

        self.corners.append({"name": name, "start": start, "end": end})
        self._auto_increment_turn_name(name)
        self.refresh_table()
        self.redraw_map()
        self.select_corner(len(self.corners) - 1)

        msg = f"Added '{name}' ({start:.1f}m - {end:.1f}m). Drag handles on map to adjust!"
        self.lbl_bottom_status.config(text=f"✅ {msg}", fg=THEME_SUCCESS)
        if hasattr(self, 'lbl_overlay_status'):
            self.lbl_overlay_status.config(text=f"✅ {msg}", fg=THEME_SUCCESS)

    def add_corner_at_dist(self, dist):
        """Prompt to add a corner at a specific track distance."""
        default_name = self.corner_name_var.get().strip() or f"Turn {len(self.corners) + 1}"
        name = simpledialog.askstring("Add Corner", "Enter Corner Name:", initialvalue=default_name, parent=self.root)
        if name and name.strip():
            name = name.strip()
            start = round(dist, 1)
            end = round(min(self.track_length, dist + 120.0) if self.track_length > 0 else dist + 120.0, 1)
            self.corners.append({"name": name, "start": start, "end": end})
            self._auto_increment_turn_name(name)
            self.refresh_table()
            self.redraw_map()
            self.select_corner(len(self.corners) - 1)

            msg = f"Added '{name}' ({start:.1f}m - {end:.1f}m). Drag handles on map to adjust!"
            self.lbl_bottom_status.config(text=f"✅ {msg}", fg=THEME_SUCCESS)
            if hasattr(self, 'lbl_overlay_status'):
                self.lbl_overlay_status.config(text=f"✅ {msg}", fg=THEME_SUCCESS)

    def rename_corner_dialog(self, idx):
        if 0 <= idx < len(self.corners):
            c = self.corners[idx]
            new_name = simpledialog.askstring("Rename Corner", f"Enter new name for {c.get('name')}:",
                                              initialvalue=c.get('name'), parent=self.root)
            if new_name and new_name.strip():
                c['name'] = new_name.strip()
                self.refresh_table()
                self.redraw_map()
                self.lbl_bottom_status.config(text=f"Renamed corner to '{c['name']}'.", fg=THEME_SUCCESS)
                if hasattr(self, 'lbl_overlay_status'):
                    self.lbl_overlay_status.config(text=f"Renamed corner to '{c['name']}'.", fg=THEME_SUCCESS)

    def delete_corner_by_idx(self, idx):
        if 0 <= idx < len(self.corners):
            name = self.corners[idx].get("name", "Corner")
            del self.corners[idx]
            self.selected_corner_idx = -1
            self.refresh_table()
            self.redraw_map()
            self.lbl_bottom_status.config(text=f"Deleted '{name}'.", fg=THEME_WARNING)

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

        self.corners.append({
            "name": name,
            "start": start_dist,
            "end": end_dist
        })

        self.is_recording_corner = False
        self.btn_mark_start.config(state="normal", bg=THEME_INFO)
        self.btn_mark_end.config(state="disabled", bg=THEME_BG_CARD)
        self.btn_toggle_rec.config(text="⏺️ Toggle (F8)", bg=THEME_ACCENT)

        self._auto_increment_turn_name(name)
        self.refresh_table()
        self.redraw_map()
        self.select_corner(len(self.corners) - 1)

        self.lbl_rec_status.config(
            text=f"✅ Added [{name}] ({start_dist:.1f}m - {end_dist:.1f}m). Remember to click 'Save'!",
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

    # --- Track File Management (Output JSON Format Remains 100% Unchanged) ---
    def refresh_track_list(self):
        if not os.path.exists(self.corner_names_dir):
            os.makedirs(self.corner_names_dir, exist_ok=True)

        files = [f for f in os.listdir(self.corner_names_dir) if f.endswith(".json") and f not in ("trackLandmarksData.json", "raceroomTrackLandmarksData.json")]
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
            self.redraw_map()

    def on_track_selected(self, event=None):
        filename = self.track_combo_var.get()
        if filename:
            filepath = os.path.join(self.corner_names_dir, filename)
            self.load_track_file(filepath)

    def _auto_detect_track_from_sim(self):
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
            self.redraw_map()
            filename = os.path.basename(filepath)
            self.lbl_bottom_status.config(text=f"Loaded {len(self.corners)} corners from {filename}", fg=THEME_FG)
            self.corner_name_var.set(f"Turn {len(self.corners) + 1}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load track file:\n{e}")

    def create_new_track(self):
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
            try:
                length = float(entry_len.get().strip())
            except ValueError:
                length = 0.0

            if not loc:
                messagebox.showwarning("Warning", "Track location name is required.")
                return

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
                self.redraw_map()
                messagebox.showinfo("Deleted", f"Deleted {filename}.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete track file:\n{e}")

    def save_current_track_file(self):
        """Save corners to current track file. Format is kept 100% identical as requested."""
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
            status_text = f"Saved {len(self.corners)} corners to {filename} at {time.strftime('%H:%M:%S')}"
            self.lbl_bottom_status.config(text=f"✅ {status_text}", fg=THEME_SUCCESS)
            if hasattr(self, 'lbl_overlay_status'):
                self.lbl_overlay_status.config(text=f"✅ Saved {len(self.corners)} corners to {filename}!", fg=THEME_SUCCESS)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save track file:\n{e}")

    def save_track_as(self):
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
            self.redraw_map()
            self.select_corner(len(self.corners) - 1)
            dlg.destroy()

        btn_box = tk.Frame(dlg, bg=THEME_BG)
        btn_box.pack(fill="x", padx=20, pady=20)
        tk.Button(btn_box, text="Add Corner", bg=THEME_SUCCESS, fg="#000000", font=(FONT_FAMILY, 9, "bold"),
                  command=on_confirm, padx=12, pady=4, relief="flat").pack(side="right")
        tk.Button(btn_box, text="Cancel", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=dlg.destroy, padx=12, pady=4, relief="flat").pack(side="right", padx=8)

    def edit_selected_corner(self):
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

            self.corners[idx].update({"name": name, "start": start, "end": end})
            self.refresh_table()
            self.redraw_map()
            self.select_corner(idx)
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
        self.selected_corner_idx = -1
        self.refresh_table()
        self.redraw_map()

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
        self.draw_corners_and_handles()
        self.tree.selection_set(str(idx))

    def move_corner_up(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx > 0:
            self.corners[idx - 1], self.corners[idx] = self.corners[idx], self.corners[idx - 1]
            self.refresh_table()
            self.select_corner(idx - 1)

    def move_corner_down(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < len(self.corners) - 1:
            self.corners[idx + 1], self.corners[idx] = self.corners[idx], self.corners[idx + 1]
            self.refresh_table()
            self.select_corner(idx + 1)

    def sort_corners(self):
        self.corners.sort(key=lambda x: x.get("start", 0.0))
        self.refresh_table()
        self.redraw_map()

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
