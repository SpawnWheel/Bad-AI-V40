"""RaceRoom Corner Mapper.

Same editor as the AMS2 Corner Mapper (drive & mark, draggable In/Out handles, overlay mode),
but reads RaceRoom's shared memory and edits Corner Names/raceroomTrackLandmarksData.json,
the file the RaceRoom logger loads its corner names from (keyed by layout id).
Works in replays too: it follows whichever car the game is showing.
"""
import json
import os
import shutil
import sys
import time
import tkinter as tk
from tkinter import messagebox

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "CornerMapper_AMS2"))
sys.path.insert(0, os.path.join(APP_DIR, "Logger_Raceroom"))

from corner_mapper import (  # noqa: E402
    CornerMapperApp, get_corner_names_dir,
    THEME_BG, THEME_BG_CARD, THEME_BG_INPUT, THEME_FG, THEME_INFO, THEME_SUCCESS, THEME_WARNING, FONT_FAMILY,
)
from r3e_shared import R3ESharedMemory, decode  # noqa: E402

LANDMARKS_FILE = "raceroomTrackLandmarksData.json"
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mapper_settings.json")
# Older name fields in the landmarks file, used to label layouts that have no raceroomTrackName yet
FALLBACK_NAME_KEYS = ("rf2TrackNames", "rf1TrackNames", "acTrackNames", "pcars2TrackName", "pcarsTrackName", "irTrackName")


def landmark_to_display(name):
    """'hell_corner' -> 'Hell Corner' (same as the logger shows it)."""
    return name.replace("_", " ").title()


def display_to_landmark(name):
    """'Hell Corner' -> 'hell_corner'."""
    return "_".join(name.strip().lower().split())


def tidy_number(value):
    value = round(float(value), 1)
    return int(value) if value == int(value) else value


class RaceroomCornerMapperApp(CornerMapperApp):
    SIM_NAME = "RaceRoom"

    def __init__(self, root):
        self.r3e = R3ESharedMemory()
        self.live_layout_id = None
        self.current_layout_id = None   # Layout being edited (None = new, not in the file yet)
        self.landmarks_path = os.path.join(get_corner_names_dir(), LANDMARKS_FILE)
        self.landmarks_data = {"TrackLandmarksData": []}
        self.combo_layout_ids = {}      # combo label -> layout id
        self.backup_done = False
        self.flip_x = self._load_settings().get("flip_x", False)
        super().__init__(root)

    # --- Settings ---
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({"flip_x": self.flip_x}, f, indent=2)
        except Exception:
            pass

    # --- UI additions ---
    def _setup_ui(self):
        super()._setup_ui()
        self.btn_flip = tk.Button(self.map_header_bar, text="↔️ Flip Map", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 8),
                                  command=self.toggle_flip, relief="flat", padx=6, pady=1, cursor="hand2")
        self.btn_flip.pack(side="right", padx=2)
        tk.Button(self.map_header_bar, text="⭐ Overtake Spot", bg=THEME_BG_CARD, fg=THEME_WARNING, font=(FONT_FAMILY, 8, "bold"),
                  command=self.toggle_overtake_selected, relief="flat", padx=6, pady=1, cursor="hand2").pack(side="right", padx=2)
        self.lbl_rec_status.config(text="💡 Tip: Press F9 when entering a corner, then F10 when exiting. Works in replays too - it follows the car on screen.")

    def toggle_flip(self):
        self.flip_x = not self.flip_x
        self._save_settings()
        self.redraw_map()

    def toggle_overtake_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Select a corner in the list to mark it as a common overtaking spot.")
            return
        idx = int(sel[0])
        corner = self.corners[idx]
        corner["overtake"] = not corner.get("overtake", False)
        self.refresh_table()
        self.select_corner(idx)
        state = "now" if corner["overtake"] else "no longer"
        self.lbl_bottom_status.config(text=f"'{corner['name']}' is {state} a common overtaking spot. Click 'Save' to preserve changes!", fg=THEME_SUCCESS)

    def refresh_table(self):
        super().refresh_table()
        for idx, c in enumerate(self.corners):
            if c.get("overtake") and self.tree.exists(str(idx)):
                values = list(self.tree.item(str(idx), "values"))
                values[0] = f"⭐ {values[0]}"
                self.tree.item(str(idx), values=values)

    def world_to_canvas(self, wx, wz):
        if self.flip_x:
            wx = self.bounds_min_x + self.bounds_max_x - wx
        return super().world_to_canvas(wx, wz)

    # --- Telemetry ---
    def _poll_sim(self):
        header, drivers = self.r3e.read()
        if header is None:
            self.is_connected = False
            return False

        self.is_connected = True
        self.track_location = decode(header.track_name)
        self.track_variation = decode(header.layout_name)
        self.track_length = float(header.layout_length)
        self.live_layout_id = int(header.layout_id) if header.layout_id > 0 else None

        # Follow the car the game is showing (player, or the watched car in replays)
        slot = header.vehicle_info.slot_id
        car = next((d for d in drivers if d.driver_info.slot_id == slot), drivers[0] if drivers else None)
        if car is not None:
            self.current_lap_dist = float(car.lap_distance)
            self.current_lap = int(car.completed_laps) + 1
            self.current_speed = float(car.car_speed) * 3.6  # m/s to km/h
            self._record_track_point(float(car.position.x), float(car.position.z))
        return True

    # --- Landmarks file ---
    def _read_landmarks_file(self):
        try:
            with open(self.landmarks_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("TrackLandmarksData", [])
            return data
        except FileNotFoundError:
            return {"TrackLandmarksData": []}

    def _write_landmarks_file(self):
        """Back up the file once per session, then write atomically so the logger never reads half a file."""
        if not self.backup_done and os.path.exists(self.landmarks_path):
            shutil.copy2(self.landmarks_path, self.landmarks_path + ".bak")
            self.backup_done = True
        tmp_path = self.landmarks_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\r\n") as f:
            json.dump(self.landmarks_data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, self.landmarks_path)

    def _find_entry(self, layout_id):
        for entry in self.landmarks_data["TrackLandmarksData"]:
            if entry.get("raceroomLayoutId") == layout_id:
                return entry
        return None

    def _entry_name(self, entry):
        name = entry.get("raceroomTrackName")
        if name:
            return name
        for key in FALLBACK_NAME_KEYS:
            value = entry.get(key)
            if isinstance(value, list) and value:
                value = value[0]
            if value:
                return str(value).replace("_", " ").strip(" :")
        return "Unnamed layout"

    def _combo_label(self, entry):
        return f"{self._entry_name(entry)} [{entry.get('raceroomLayoutId')}] ({len(entry.get('trackLandmarks', []))})"

    def _select_layout_in_combo(self, layout_id):
        for label, lid in self.combo_layout_ids.items():
            if lid == layout_id:
                self.track_combo.set(label)
                return
        self.track_combo.set("")

    # --- Track profile management (overrides the AMS2 per-file versions) ---
    def refresh_track_list(self):
        try:
            self.landmarks_data = self._read_landmarks_file()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read {LANDMARKS_FILE}:\n{e}")
            return

        entries = sorted(self.landmarks_data["TrackLandmarksData"], key=lambda e: self._entry_name(e).lower())
        self.combo_layout_ids = {self._combo_label(e): e.get("raceroomLayoutId") for e in entries}
        self.track_combo["values"] = list(self.combo_layout_ids)

        if self.current_layout_id is not None and self._find_entry(self.current_layout_id):
            self._select_layout_in_combo(self.current_layout_id)
        elif self.live_layout_id is not None:
            self._auto_detect_track_from_sim()
        elif entries:
            self.load_layout(entries[0].get("raceroomLayoutId"))
        else:
            self.track_combo.set("")
            self.corners = []
            self.refresh_table()
            self.redraw_map()

    def on_track_selected(self, event=None):
        layout_id = self.combo_layout_ids.get(self.track_combo_var.get())
        if layout_id is not None:
            self.load_layout(layout_id)

    def load_track_file(self, filepath):
        # Only used by the AMS2 base class; RaceRoom profiles live in one file keyed by layout id
        self.refresh_track_list()

    def load_layout(self, layout_id):
        entry = self._find_entry(layout_id)
        if entry is None:
            return
        self.current_layout_id = layout_id
        self.corners = []
        for lm in entry.get("trackLandmarks", []):
            corner = {
                "name": landmark_to_display(lm.get("landmarkName", "")),
                "start": float(lm.get("distanceRoundLapStart", 0)),
                "end": float(lm.get("distanceRoundLapEnd", 0)),
                "overtake": bool(lm.get("isCommonOvertakingSpot", False)),
            }
            if "midPoint" in lm:
                corner["midPoint"] = lm["midPoint"]
            self.corners.append(corner)

        self.selected_corner_idx = -1
        self._select_layout_in_combo(layout_id)
        self.refresh_table()
        self.redraw_map()
        self.lbl_bottom_status.config(text=f"Loaded {len(self.corners)} corners for {self._entry_name(entry)} (layout {layout_id})", fg=THEME_FG)
        self.corner_name_var.set(f"Turn {len(self.corners) + 1}")

    def _auto_detect_track_from_sim(self):
        if self.live_layout_id is None:
            return
        live_name = f"{self.track_location} - {self.track_variation}".strip(" -")
        if self._find_entry(self.live_layout_id):
            self.load_layout(self.live_layout_id)
            self.lbl_bottom_status.config(text=f"Auto-loaded corners for {live_name} (layout {self.live_layout_id})", fg=THEME_SUCCESS)
        else:
            # Start a fresh profile for this layout; Save adds it to the landmarks file
            self.current_layout_id = None
            self.corners = []
            self.selected_corner_idx = -1
            self.track_combo.set("")
            self.refresh_table()
            self.redraw_map()
            self.corner_name_var.set("Turn 1")
            self.lbl_bottom_status.config(
                text=f"No corners yet for {live_name} (layout {self.live_layout_id}). Mark corners, then Save to add it.",
                fg=THEME_WARNING)

    def _track_dialog(self, title, name, layout_id, length, on_ok, confirm_text):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("440x280")
        dlg.configure(bg=THEME_BG)
        dlg.transient(self.root)
        dlg.grab_set()

        entries = []
        for label, value in (("Track Name (e.g. Brands Hatch - Grand Prix):", name),
                             ("RaceRoom Layout ID:", "" if layout_id is None else str(layout_id)),
                             ("Track Length (meters):", f"{length:.1f}" if length else "")):
            tk.Label(dlg, text=label, bg=THEME_BG, fg=THEME_FG, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=20, pady=(12, 2))
            entry = tk.Entry(dlg, bg=THEME_BG_INPUT, fg=THEME_FG, font=(FONT_FAMILY, 10), insertbackground=THEME_FG)
            entry.insert(0, value)
            entry.pack(fill="x", padx=20)
            entries.append(entry)

        def confirm():
            try:
                new_layout_id = int(entries[1].get().strip())
            except ValueError:
                messagebox.showwarning("Warning", "Layout ID must be a whole number (shown at the top while RaceRoom is running).", parent=dlg)
                return
            try:
                new_length = float(entries[2].get().strip() or 0)
            except ValueError:
                new_length = 0.0
            if on_ok(entries[0].get().strip(), new_layout_id, new_length) is not False:
                dlg.destroy()

        btn_box = tk.Frame(dlg, bg=THEME_BG)
        btn_box.pack(fill="x", padx=20, pady=18)
        tk.Button(btn_box, text=confirm_text, bg=THEME_SUCCESS, fg="#000000", font=(FONT_FAMILY, 9, "bold"),
                  command=confirm, padx=12, pady=4, relief="flat").pack(side="right")
        tk.Button(btn_box, text="Cancel", bg=THEME_BG_CARD, fg=THEME_FG, font=(FONT_FAMILY, 9),
                  command=dlg.destroy, padx=12, pady=4, relief="flat").pack(side="right", padx=8)

    def _live_track_name(self):
        return f"{self.track_location} - {self.track_variation}".strip(" -")

    def _new_entry(self, name, layout_id, length, corners):
        if self._find_entry(layout_id):
            messagebox.showwarning("Warning", f"Layout {layout_id} already has a corner profile. Select it from the list instead.")
            return False
        entry = {"raceroomTrackName": name, "raceroomLayoutId": layout_id, "trackLandmarks": []}
        if length > 0:
            entry["approximateTrackLength"] = round(length)
        self.landmarks_data["TrackLandmarksData"].append(entry)
        self.current_layout_id = layout_id
        self.corners = corners
        self.save_current_track_file()
        self.refresh_track_list()
        self.load_layout(layout_id)
        return True

    def create_new_track(self):
        self._track_dialog("Create New Layout Profile", self._live_track_name(), self.live_layout_id, self.track_length,
                           lambda n, lid, ln: self._new_entry(n, lid, ln, []), "Create")

    def save_track_as(self):
        corners = [dict(c) for c in self.corners]
        self._track_dialog("Save Corners As Layout", self._live_track_name(), self.live_layout_id, self.track_length,
                           lambda n, lid, ln: self._new_entry(n, lid, ln, corners), "Save")

    def edit_track_info(self):
        entry = self._find_entry(self.current_layout_id) if self.current_layout_id is not None else None
        if entry is None:
            messagebox.showwarning("Warning", "No saved layout profile currently loaded.")
            return

        def apply(name, layout_id, length):
            if layout_id != self.current_layout_id and self._find_entry(layout_id):
                messagebox.showwarning("Warning", f"Layout {layout_id} already has a corner profile.")
                return False
            entry["raceroomTrackName"] = name
            entry["raceroomLayoutId"] = layout_id
            if length > 0:
                entry["approximateTrackLength"] = round(length)
            self.current_layout_id = layout_id
            self.save_current_track_file()
            self.refresh_track_list()

        self._track_dialog("Edit Layout Information", self._entry_name(entry), self.current_layout_id,
                           entry.get("approximateTrackLength", self.track_length), apply, "Save")

    def delete_track_file(self):
        entry = self._find_entry(self.current_layout_id) if self.current_layout_id is not None else None
        if entry is None:
            messagebox.showwarning("Warning", "No saved layout profile selected.")
            return
        name = self._entry_name(entry)
        if messagebox.askyesno("Confirm Delete", f"Remove all corners for {name} (layout {self.current_layout_id}) from {LANDMARKS_FILE}?\n\n"
                                                 f"A backup is kept as {LANDMARKS_FILE}.bak."):
            try:
                self.landmarks_data["TrackLandmarksData"].remove(entry)
                self._write_landmarks_file()
                self.current_layout_id = None
                self.corners = []
                self.refresh_track_list()
                self.redraw_map()
                self.lbl_bottom_status.config(text=f"Deleted corner profile for {name}.", fg=THEME_WARNING)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete layout profile:\n{e}")

    def save_current_track_file(self):
        """Write the corners into the layout's entry, keeping every other field of the landmarks file as it was."""
        if self.current_layout_id is None:
            if self.live_layout_id is None:
                self.save_track_as()
                return
            # First save for the layout being driven: add it to the file
            self.landmarks_data["TrackLandmarksData"].append({
                "raceroomTrackName": self._live_track_name(),
                "raceroomLayoutId": self.live_layout_id,
                "approximateTrackLength": round(self.track_length),
                "trackLandmarks": [],
            })
            self.current_layout_id = self.live_layout_id

        entry = self._find_entry(self.current_layout_id)
        if not entry.get("raceroomTrackName") and self.current_layout_id == self.live_layout_id and self.track_location:
            entry["raceroomTrackName"] = self._live_track_name()

        landmarks = []
        for c in self.corners:
            lm = {
                "landmarkName": display_to_landmark(c.get("name", "")),
                "distanceRoundLapStart": tidy_number(c.get("start", 0.0)),
                "distanceRoundLapEnd": tidy_number(c.get("end", 0.0)),
                "isCommonOvertakingSpot": bool(c.get("overtake", False)),
            }
            if "midPoint" in c:
                lm["midPoint"] = c["midPoint"]
            landmarks.append(lm)
        entry["trackLandmarks"] = landmarks

        try:
            self._write_landmarks_file()
            self.refresh_track_list()
            status_text = f"Saved {len(self.corners)} corners for {self._entry_name(entry)} (layout {self.current_layout_id}) at {time.strftime('%H:%M:%S')}"
            self.lbl_bottom_status.config(text=f"✅ {status_text}", fg=THEME_SUCCESS)
            self.lbl_overlay_status.config(text=f"✅ Saved {len(self.corners)} corners!", fg=THEME_SUCCESS)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save {LANDMARKS_FILE}:\n{e}")

    def _update_ui_loop(self):
        super()._update_ui_loop()
        if self.is_connected and self.track_length > 0 and self.live_layout_id is not None:
            self.lbl_telemetry_track.config(text=f"Track: {self._live_track_name()} [layout {self.live_layout_id}] ({self.track_length:.1f} m)")

    def close(self):
        self.running = False
        self.r3e.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = RaceroomCornerMapperApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
