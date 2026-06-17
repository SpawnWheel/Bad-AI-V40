import mmap
import time
import ctypes
import sys
import os
import datetime
import json
from r3e_data import *
from settings_manager import settings
from r3e_lookup import r3e_lookup

class RaceRoomLogger:
    def __init__(self):
        self.mm = None
        self.last_update_time = 0
        self.connected = False
        self.data = None
        self.log_file = None
        self.text_log_file = None
        
        # State tracking
        self.prev_session_type = -1
        self.prev_session_phase = -1
        self.prev_session_iteration = -1
        self.prev_track_id = -1
        self.prev_layout_id = -1
        self.prev_flags = R3E_FLAGS()
        self.prev_position = -1
        self.prev_incident_points = -1
        self.prev_completed_laps = -1
        self.prev_in_pitlane = -1
        self.session_start_time = None
        
        # Damage tracking (Player)
        self.prev_damage = {
            'engine': 1.0,
            'transmission': 1.0,
            'aerodynamics': 1.0,
            'suspension': 1.0
        }
        
        # Driver tracking: slot_id -> {place, name, in_pitlane, speed, finish_status}
        self.prev_driver_map = {}
        # Stationary tracking: slot_id -> timestamp when first stopped
        self.stationary_drivers = {} 
        # Pit tracking: slot_id -> {entered_at: timestamp, overtaken_by: list}
        self.drivers_in_pits = {}
        
        # Battle tracking: slot_id -> {active_1s: bool, last_0_2s_time: float, target_slot_id: int}
        self.battle_states = {}
        
        # Advanced Battle tracking (Side-by-side, Drafting): slot_id -> dict
        self.battle_tracking = {}

        # Landmarks data
        self.landmarks_data = {}
        self.load_landmarks()

        # Track cars that have moved/started to prevent false "stopped" alerts on grid
        self.active_cars = set()

        # Leaderboard timing
        self.last_leaderboard_log_time = 0
        self.last_closest_battle_log_time = 0
        self.last_closest_battle_pair = None
        self.last_significant_event_time = 0
        self.pending_grid_log = False
        self.green_phase_start_time = 0

        self.session_names = {
            R3E_SESSION_PRACTICE: "Practice",
            R3E_SESSION_QUALIFY: "Qualifying",
            R3E_SESSION_RACE: "Race",
            R3E_SESSION_WARMUP: "Warmup"
        }
        
        self.phase_names = {
            R3E_SESSION_PHASE_GARAGE: "in the Garage",
            R3E_SESSION_PHASE_GRIDWALK: "on the Grid",
            R3E_SESSION_PHASE_FORMATION: "on the Formation Lap",
            R3E_SESSION_PHASE_COUNTDOWN: "Counting Down",
            R3E_SESSION_PHASE_GREEN: "Racing (Green Flag)",
            R3E_SESSION_PHASE_CHECKERED: "Finished (Checkered Flag)"
        }

    def load_landmarks(self):
        try:
            with open("trackLandmarksData.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                for track in data.get("TrackLandmarksData", []):
                    layout_id = track.get("raceroomLayoutId")
                    if layout_id:
                        self.landmarks_data[layout_id] = track.get("trackLandmarks", [])
            print(f"Loaded landmarks for {len(self.landmarks_data)} tracks.")
        except Exception as e:
            print(f"Error loading landmarks: {e}")

    def get_landmark_at_distance(self, layout_id, distance):
        landmarks = self.landmarks_data.get(layout_id, [])
        for lm in landmarks:
            start = lm.get("distanceRoundLapStart", 0)
            end = lm.get("distanceRoundLapEnd", 0)
            name = lm.get("landmarkName", "Unknown")
            
            if start < end:
                if start <= distance <= end:
                    return name
            else: # Landmark crosses the finish line
                if distance >= start or distance <= end:
                    return name
        return None

    def _close_and_cleanup_logs(self, scan_all=False):
        """Closes current log files and removes them if they are smaller than 5KB.
        If scan_all is True, also scans the entire Logs directory for small files.
        """
        paths_to_check = []
        if self.log_file:
            path = self.log_file.name
            self.log_file.close()
            self.log_file = None
            paths_to_check.append(path)
        if self.text_log_file:
            path = self.text_log_file.name
            self.text_log_file.close()
            self.text_log_file = None
            paths_to_check.append(path)
        
        # Check specific files we just closed
        for path in paths_to_check:
            if os.path.exists(path):
                size = os.path.getsize(path)
                if size < 5120: # 5KB threshold
                    try:
                        os.remove(path)
                        print(f"Removed small/empty log file: {path} ({size} bytes)")
                    except Exception as e:
                        print(f"Error removing small log file {path}: {e}")

        # Optionally scan the whole directory for orphans
        if scan_all:
            project_path = os.environ.get("R3E_PROJECT_PATH")
            log_dir = os.path.join(project_path, "Logs") if project_path else "Logs"
            if os.path.exists(log_dir):
                for filename in os.listdir(log_dir):
                    if filename.endswith(".jsonl") or filename.endswith(".txt"):
                        path = os.path.join(log_dir, filename)
                        try:
                            if os.path.isfile(path) and os.path.getsize(path) < 5120:
                                os.remove(path)
                                print(f"Removed orphaned small log file: {path}")
                        except:
                            pass

    def start_new_log_file(self, session_type=None, scan_orphans=False):
        self._close_and_cleanup_logs(scan_all=scan_orphans)
        
        # Check for Project Context from Launcher
        project_path = os.environ.get("R3E_PROJECT_PATH")
        if project_path:
            log_dir = os.path.join(project_path, "Logs")
        else:
            log_dir = "Logs"
            
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        s_name = self.session_names.get(session_type, "Session")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_json = os.path.join(log_dir, f"{s_name}_{timestamp}.jsonl")
        filename_txt = os.path.join(log_dir, f"{s_name}_{timestamp}.txt")
        
        self.log_file = open(filename_json, "w", encoding="utf-8")
        self.text_log_file = open(filename_txt, "w", encoding="utf-8")
        
        # Reset specific per-log tracking if needed, though session tracking handles most.
        self.log(f"New log file started: {filename_json}", category="SYSTEM")

    def log(self, message, category="INFO", sim_time=None, extra_data=None):
        timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")
        sim_time_str = "00:00:00"

        if sim_time is not None and sim_time >= 0:
            # Format simulation seconds to HH:MM:SS
            hours, remainder = divmod(int(sim_time), 3600)
            minutes, seconds = divmod(remainder, 60)
            sim_time_str = f"{hours:02}:{minutes:02}:{seconds:02}"
            display_timestamp = f"[{sim_time_str}]"
        else:
            display_timestamp = f"[{timestamp_str}]"
            
        # Console output (Human readable)
        print(f"{display_timestamp} [{category}] {message}")
        
        # Text output (Sim Time only)
        if self.text_log_file:
            self.text_log_file.write(f"{sim_time_str} - [{category}] {message}\n")
            self.text_log_file.flush()

        # JSON output (Machine readable)
        if self.log_file:
            log_entry = {
                "timestamp": timestamp_str,
                "sim_time": sim_time_str,
                "sim_time_raw": sim_time if sim_time is not None else 0,
                "category": category,
                "message": message
            }
            if extra_data:
                log_entry.update(extra_data)
                
            self.log_file.write(json.dumps(log_entry) + "\n")
            self.log_file.flush()

    def connect(self):
        try:
            # RaceRoom shared memory name is "$R3E"
            self.mm = mmap.mmap(-1, ctypes.sizeof(R3E_SHARED), R3E_SHARED_MEMORY_NAME)
            self.data = R3E_SHARED.from_buffer(self.mm)
            self.connected = True
            
            # Start a new log file on successful connection, scanning for orphans
            self.start_new_log_file(self.data.session_type, scan_orphans=True)
            self.log("Connected to RaceRoom shared memory.", category="CONNECTION")
            
            # Initialize iteration to avoid immediate double-trigger
            self.prev_session_iteration = self.data.session_iteration
            
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            # Only log error if we have a file (or to console via print fallback if needed, 
            # but log() handles no-file gracefully-ish, though we prefer not to spam if no connection)
            # For connection errors, we might not have a file yet.
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [ERROR] Connection Error: {e}")
            return False

    def decode_string(self, c_string):
        try:
            return bytes(c_string).partition(b'\0')[0].decode('utf-8', errors='replace')
        except:
            return "Unknown"

    def get_drivers(self):
        """
        Parses the current drivers from shared memory into a dictionary.
        """
        drivers = {}
        num_cars = self.data.num_cars
        
        if num_cars < 0 or num_cars > R3E_NUM_DRIVERS_MAX:
            return drivers

        for i in range(num_cars):
            driver_data = self.data.all_drivers_data_1[i]
            info = driver_data.driver_info
            
            slot_id = info.slot_id
            if slot_id == -1: continue 
            
            name = self.decode_string(info.name)
            
            # Lookup Car/Class/Livery info
            car_name = r3e_lookup.get_car_name(info.model_id)
            class_name = r3e_lookup.get_class_name(info.class_id)
            livery_name = r3e_lookup.get_livery_name(info.model_id, info.livery_id)
            
            drivers[slot_id] = {
                'name': name,
                'place': driver_data.place,
                'place_class': driver_data.place_class,
                'class_id': info.class_id,
                'class_name': class_name,
                'car_model': car_name,
                'livery': livery_name,
                'in_pitlane': driver_data.in_pitlane,
                'car_speed': driver_data.car_speed,
                'lap_distance': driver_data.lap_distance,
                'finish_status': driver_data.finish_status,
                'time_delta_front': driver_data.time_delta_front,
                'time_delta_behind': driver_data.time_delta_behind
            }
        return drivers

    def log_leaderboard(self, drivers, title="Leaderboard Update", sim_time=None, show_gaps=True, include_vehicle_info=False):
        """
        Logs the current leaderboard, grouped by class.
        """
        # Group drivers by class
        classes = {}
        for d in drivers.values():
            c_name = d.get('class_name', 'Unknown')
            if c_name not in classes:
                classes[c_name] = []
            classes[c_name].append(d)
        
        leaderboard_data = []
        full_message_parts = []
        
        # Sort classes alphabetically or by some priority (here just name)
        for class_name in sorted(classes.keys()):
            class_drivers = classes[class_name]
            # Sort by class position
            class_drivers.sort(key=lambda x: x['place_class'])
            
            class_entries_str = []
            class_data_entries = []
            
            for d in class_drivers:
                place = d['place_class']
                if place == 0: continue
                
                name = d['name']
                gap = d['time_delta_front'] # This might be overall gap, check if we need class gap.
                # R3E provides 'lap_time_delta_leader_class' but here we are using 'time_delta_front' which is usually car ahead.
                # In multi-class, 'time_delta_front' is gap to physical car ahead, not necessarily same class.
                # However, for a simple leaderboard, we might want gap to leader of class or gap to car ahead in class.
                # The data struct has 'place_class' but gap logic in previous code used 'time_delta_front'.
                # We will stick to 'time_delta_front' for now but be aware it might be mixed class.
                # actually, looking at r3e_driver_data, we have place_class.
                
                # Format gap string
                gap_str = ""
                if show_gaps:
                    if place == 1:
                        gap_str = "Leader"
                    elif gap >= 0:
                        gap_str = f"+{gap:.3f}s"
                
                veh_info = ""
                if include_vehicle_info:
                    veh_info = f" [{d.get('car_model', '')} - {d.get('livery', '')}]"
                
                entry_str = f"P{place}: {name}{veh_info} ({gap_str})" if gap_str else f"P{place}: {name}{veh_info}"
                class_entries_str.append(entry_str)
                
                class_data_entries.append({
                    "place_class": place,
                    "place_overall": d['place'],
                    "name": name,
                    "gap_front": gap,
                    "car_model": d.get('car_model', ''),
                    "livery": d.get('livery', ''),
                    "finish_status": d['finish_status']
                })

            # Add to main structure
            leaderboard_data.append({
                "class_name": class_name,
                "entries": class_data_entries
            })
            
            # Add to text message
            if len(classes) > 1:
                full_message_parts.append(f"[{class_name}] " + " | ".join(class_entries_str))
            else:
                full_message_parts.append(" | ".join(class_entries_str))

        # Join all parts
        full_message = f"{title}: " + " || ".join(full_message_parts)
        
        # Log
        self.log(full_message, category="LEADERBOARD", sim_time=sim_time, extra_data={"leaderboard": leaderboard_data})
        self.last_leaderboard_log_time = time.time()

    def check_connection(self):
        try:
            curr_time = self.data.player.game_simulation_time
            self.last_update_time = curr_time
            return True
        except:
            self.connected = False
            return False

    def update(self):
        if not self.connected:
            if self.connect():
                self.prev_session_type = -1
                self.prev_track_id = -1
                self.prev_driver_map = {}
                self.stationary_drivers = {}
                self.drivers_in_pits = {}
            else:
                return

        if not self.check_connection():
            return

        current_data = self.data
        now = time.time()
        
        # 0. Session Iteration / New Session Detection
        # If the session iteration changes, it means a restart or new session
        if current_data.session_iteration != self.prev_session_iteration:
            if self.prev_session_iteration != -1: # Don't trigger on first loop if connect() set it
                self.start_new_log_file(current_data.session_type)
                self.log("Session Iteration Changed - New Log Started.", category="SYSTEM")
                # Reset state
                self.prev_session_type = -1
                self.prev_track_id = -1
                self.prev_driver_map = {}
                self.stationary_drivers = {}
                self.drivers_in_pits = {}
            self.prev_session_iteration = current_data.session_iteration

        sim_time = current_data.player.game_simulation_time
        
        # 1. Track & Layout
        if current_data.track_id != self.prev_track_id or current_data.layout_id != self.prev_layout_id:
            track = self.decode_string(current_data.track_name)
            layout = self.decode_string(current_data.layout_name)
            self.log(f"Track Loaded: {track} - {layout}", category="TRACK", sim_time=sim_time)
            self.log(f"Track Length: {current_data.layout_length:.1f} meters", category="TRACK", sim_time=sim_time)
            self.prev_track_id = current_data.track_id
            self.prev_layout_id = current_data.layout_id
            self.prev_driver_map = {}
            self.stationary_drivers = {}
            self.drivers_in_pits = {}

        # 2. Session Type
        if current_data.session_type != self.prev_session_type:
            # If we were already logging a different session type, start a new file
            if self.prev_session_type != -1:
                self.start_new_log_file(current_data.session_type)

            s_name = self.session_names.get(current_data.session_type, "Unknown Session")
            self.log(f"Session Changed: We are now in {s_name}.", category="SESSION", sim_time=sim_time)
            
            if current_data.session_length_format in [0, 2]: 
                duration_secs = current_data.session_time_duration
                if duration_secs > 0:
                    mins = int(duration_secs / 60)
                    self.log(f"Session Duration: {mins} minutes.", category="SESSION", sim_time=sim_time)
            elif current_data.session_length_format == 1: 
                if current_data.number_of_laps > 0:
                    self.log(f"Session Duration: {current_data.number_of_laps} laps.", category="SESSION", sim_time=sim_time)

            self.session_start_time = datetime.datetime.now()
            
            # Reset driver map
            self.prev_driver_map = {}
            self.stationary_drivers = {}
            self.drivers_in_pits = {}
            
            # Sync flags to prevent false "Green again" or "Yellow" on session switch
            ctypes.memmove(ctypes.addressof(self.prev_flags), ctypes.addressof(current_data.flags), ctypes.sizeof(R3E_FLAGS))
            
            # Trigger Grid/Leaderboard Log (deferred until drivers are loaded)
            self.pending_grid_log = True
            
            self.prev_session_type = current_data.session_type

        # 2b. Deferred Starting Grid / Initial Leaderboard Log
        if self.pending_grid_log:
            current_driver_map = self.get_drivers()
            if len(current_driver_map) > 0:
                is_race = current_data.session_type == R3E_SESSION_RACE
                title = "Starting Grid Order" if is_race else "Initial Leaderboard"
                
                # Check for Multi-Class
                unique_classes = set(d.get('class_name') for d in current_driver_map.values() if d.get('class_name'))
                if len(unique_classes) > 1:
                    classes_str = ", ".join(sorted(unique_classes))
                    self.log(f"This is a multiclass race involving: {classes_str}.", category="SESSION", sim_time=sim_time)

                # Don't show gaps for starting grid (is_race=True)
                self.log_leaderboard(current_driver_map, title=title, sim_time=sim_time, show_gaps=not is_race, include_vehicle_info=True)
                
                # Mark as done
                self.pending_grid_log = False
                # Prevent immediate periodic update
                self.last_leaderboard_log_time = time.time()

        # 3. Session Phase
        if current_data.session_phase != self.prev_session_phase:
            p_name = self.phase_names.get(current_data.session_phase, "Unknown Phase")
            
            if current_data.session_phase == R3E_SESSION_PHASE_GREEN:
                self.green_phase_start_time = now
                self.active_cars.clear() # Reset active cars list on Green
                if current_data.session_type == R3E_SESSION_RACE:
                    self.log("Green Flag! The race begins!", category="SESSION", sim_time=sim_time)
                else:
                    self.log(f"Phase Update: {p_name}", category="SESSION", sim_time=sim_time)
                
                # Suppress "Green again" flag message that usually accompanies this phase change
                # by syncing the yellow flag state immediately.
                if current_data.flags.yellow == 0 and self.prev_flags.yellow == 1:
                     self.prev_flags.yellow = 0
            else:
                self.log(f"Phase Update: {p_name}", category="SESSION", sim_time=sim_time)
            
            self.prev_session_phase = current_data.session_phase

        # 4. Flags
        if current_data.flags.yellow != self.prev_flags.yellow:
            if current_data.flags.yellow == 1:
                self.log("Flag: YELLOW FLAG detected!", category="FLAG", sim_time=sim_time)
            else:
                self.log("Flag: Green again (Yellow cleared).", category="FLAG", sim_time=sim_time)
        
        if current_data.flags.blue != self.prev_flags.blue:
            if current_data.flags.blue == 1:
                self.log("Flag: BLUE FLAG - Faster car approaching, yield!", category="FLAG", sim_time=sim_time)
        
        if current_data.flags.checkered != self.prev_flags.checkered:
            if current_data.flags.checkered == 1:
                self.log("Flag: CHEQUERED FLAG! Session is ending.", category="FLAG", sim_time=sim_time)

        ctypes.memmove(ctypes.addressof(self.prev_flags), ctypes.addressof(current_data.flags), ctypes.sizeof(R3E_FLAGS))

        # 5. Position & Accidents (Full Field)
        current_driver_map = self.get_drivers()
        
        if self.prev_driver_map:
            drivers_by_place = {d['place']: d for d in current_driver_map.values()}
            
            for slot_id, curr_info in current_driver_map.items():
                if slot_id not in self.prev_driver_map: continue
                
                prev_info = self.prev_driver_map[slot_id]

                # 5x. Pit Stop Tracking (All Drivers)
                # Check for Entry
                if curr_info['in_pitlane'] == 1 and prev_info['in_pitlane'] == 0:
                    self.log(f"{curr_info['name']} entered Pit Lane.", category="PIT", sim_time=sim_time)
                    self.drivers_in_pits[slot_id] = {'entered_at': now, 'overtaken_by': []}
                
                # Check for Exit
                elif curr_info['in_pitlane'] == 0 and prev_info['in_pitlane'] == 1:
                    # Log exit
                    self.log(f"{curr_info['name']} exited Pit Lane.", category="PIT", sim_time=sim_time)
                    
                    # Log overtakes experienced while in pits
                    pit_data = self.drivers_in_pits.get(slot_id)
                    if pit_data and pit_data['overtaken_by']:
                        overtakers = ", ".join(pit_data['overtaken_by'])
                        self.log(f"While in pits, {curr_info['name']} was overtaken by: {overtakers}.", category="PIT_OVERTAKE", sim_time=sim_time)
                    
                    # Log re-joining position
                    c_name = curr_info.get('class_name', '')
                    self.log(f"{curr_info['name']} joins the race in P{curr_info['place_class']} ({c_name}).", category="PIT_EXIT", sim_time=sim_time)
                    
                    # Clean up
                    if slot_id in self.drivers_in_pits:
                        del self.drivers_in_pits[slot_id]
                
                # 5a. Overtakes (Active Racing)
                if current_data.session_phase == R3E_SESSION_PHASE_GREEN:
                    if curr_info['place'] < prev_info['place']:
                        passed_on_track = []
                        
                        # Identify who was passed
                        for victim_id, victim_prev in self.prev_driver_map.items():
                            if victim_id == slot_id or victim_id not in current_driver_map: continue
                            
                            # Logic: Victim was ahead of me, now is behind me
                            # AND Victim is in the same class
                            if victim_prev['place'] < prev_info['place'] and current_driver_map[victim_id]['place'] > curr_info['place']:
                                # Class check
                                if victim_prev.get('class_id') != curr_info.get('class_id'):
                                    continue

                                victim_name = victim_prev['name']
                                
                                # Check if victim is in pits
                                if victim_id in self.drivers_in_pits:
                                    # Add this overtaker (curr_info) to the victim's tracking
                                    if curr_info['name'] not in self.drivers_in_pits[victim_id]['overtaken_by']:
                                        self.drivers_in_pits[victim_id]['overtaken_by'].append(curr_info['name'])
                                else:
                                    passed_on_track.append(victim_name)
                        
                        # Only log standard overtakes for cars NOT in pits
                        if passed_on_track:
                            class_name = curr_info.get('class_name', '')
                            # Get landmark
                            landmark = self.get_landmark_at_distance(current_data.layout_id, curr_info['lap_distance'])
                            location_str = f" at {landmark}" if landmark else ""
                            
                            # Report class position
                            self.log(f"{curr_info['name']} overtook {', '.join(passed_on_track)} for P{curr_info['place_class']} in {class_name}{location_str}.", category="OVERTAKE", sim_time=sim_time)
                            self.last_significant_event_time = now

                # 5b. Accident Detection (Stationary/Slow on Track)
                # Retrieve settings
                accident_speed_kph = settings.get("logger", "accident_speed_threshold_kph", 30.0)
                accident_wait = settings.get("logger", "accident_time_threshold", 0.5)
                accident_speed_ms = accident_speed_kph / 3.6

                # Mark car as active if it moves fast enough (has left grid)
                if curr_info['car_speed'] > accident_speed_ms:
                    self.active_cars.add(slot_id)

                # If speed < threshold, not in pits, and racing is green AND car has been active
                if slot_id in self.active_cars and curr_info['car_speed'] < accident_speed_ms and curr_info['in_pitlane'] == 0 and current_data.session_phase == R3E_SESSION_PHASE_GREEN:
                    if slot_id not in self.stationary_drivers:
                        self.stationary_drivers[slot_id] = now
                    elif now - self.stationary_drivers[slot_id] > accident_wait: # Stopped/Slow for > threshold
                        # Report once and then "mute" until they move
                        c_name = curr_info.get('class_name', '')
                        # Get landmark
                        landmark = self.get_landmark_at_distance(current_data.layout_id, curr_info['lap_distance'])
                        location_str = f" at {landmark}" if landmark else ""
                        
                        self.log(f"Alert: {curr_info['name']} (P{curr_info['place_class']} in {c_name}) is slow/stopped on track{location_str}! Potential accident.", category="ACCIDENT", sim_time=sim_time)
                        self.stationary_drivers[slot_id] = now + 3600 # Mute for an hour or until they move
                else:
                    # They are moving or in pits, reset tracking
                    if slot_id in self.stationary_drivers:
                        if self.stationary_drivers[slot_id] > now + 60: # If they were "muted"
                             self.log(f"Notice: {curr_info['name']} is back on the move.", category="ACCIDENT", sim_time=sim_time)
                        del self.stationary_drivers[slot_id]

                # 5c. Close Battle Logic (Side-by-Side & Drafting)
                battle_delay = settings.get("logger", "battle_activation_delay", 30.0)
                if current_data.session_phase == R3E_SESSION_PHASE_GREEN and (now - self.green_phase_start_time > battle_delay):
                    sbs_gap = settings.get("logger", "battle_side_by_side_gap", 0.05)
                    sbs_cooldown = settings.get("logger", "battle_side_by_side_cooldown", 8.0)
                    draft_gap = settings.get("logger", "battle_draft_gap", 0.1)
                    draft_time = settings.get("logger", "battle_draft_time_threshold", 3.0)
                    draft_cooldown = settings.get("logger", "battle_draft_cooldown", 25.0)

                    if slot_id not in self.battle_tracking:
                        self.battle_tracking[slot_id] = {'sbs_last_time': 0, 'draft_start_time': 0, 'draft_last_time': 0}
                    tracking = self.battle_tracking[slot_id]
                    gap_front = curr_info['time_delta_front']

                    # Only check if gap is valid (>= 0) and not the leader AND not in pits
                    if gap_front >= 0 and curr_info['place'] > 1 and curr_info['in_pitlane'] == 0:
                        # Side-by-Side
                        if gap_front <= sbs_gap:
                             if now - tracking['sbs_last_time'] > sbs_cooldown:
                                 target_driver = drivers_by_place.get(curr_info['place'] - 1)
                                 if target_driver and target_driver['in_pitlane'] == 0:
                                     # Class Check
                                     if target_driver.get('class_id') == curr_info.get('class_id'):
                                         self.log(f"Side by side! {curr_info['name']} (P{curr_info['place_class']}) is fighting {target_driver['name']} (P{target_driver['place_class']})!", category="BATTLE", sim_time=sim_time)
                                         tracking['sbs_last_time'] = now
                                         self.last_significant_event_time = now
                        
                        # Drafting
                        if gap_front <= draft_gap:
                            if tracking['draft_start_time'] == 0:
                                tracking['draft_start_time'] = now
                            elif now - tracking['draft_start_time'] > draft_time:
                                if now - tracking['draft_last_time'] > draft_cooldown:
                                     target_driver = drivers_by_place.get(curr_info['place'] - 1)
                                     if target_driver and target_driver['in_pitlane'] == 0:
                                         # Class Check
                                         if target_driver.get('class_id') == curr_info.get('class_id'):
                                             self.log(f"{curr_info['name']} (P{curr_info['place_class']}) is in the draft of {target_driver['name']} (P{target_driver['place_class']}).", category="BATTLE", sim_time=sim_time)
                                             tracking['draft_last_time'] = now
                                             self.last_significant_event_time = now
                        else:
                            tracking['draft_start_time'] = 0
                    else:
                         tracking['draft_start_time'] = 0

                # 5d. Finish Status Updates
                if curr_info['finish_status'] != prev_info['finish_status']:
                    if current_data.session_type == R3E_SESSION_RACE and curr_info['finish_status'] == 1: # Finished
                        if curr_info['place'] == 1:
                            self.log(f"{curr_info['name']} has taken the win!", category="FINISH", sim_time=sim_time)
                        else:
                            self.log(f"{curr_info['name']} has finished the race in P{curr_info['place']}.", category="FINISH", sim_time=sim_time)

                    if curr_info['finish_status'] == 2: # DNF
                        self.log(f"Out of the race: {curr_info['name']} has DNF'd.", category="DNF", sim_time=sim_time)

        # 5d. Battle Updates (Gaps) - Only during Green Flag Race
        if current_data.session_type == R3E_SESSION_RACE and current_data.session_phase == R3E_SESSION_PHASE_GREEN and (now - self.green_phase_start_time > 30.0):
            # Build place map for target lookup
            drivers_by_place = {d['place']: d for d in current_driver_map.values()}
            
            # 5e. Closest Battle Filler (Every 12s)
            # Throttling: Check self-interval (12s) AND 6s silence from other major events
            if (now - self.last_closest_battle_log_time > 12.0) and (now - self.last_significant_event_time > 6.0):
                min_gap = 999.0
                closest_driver = None
                closest_target = None
                
                for slot_id, curr_info in current_driver_map.items():
                    # Check physical gap
                    gap = curr_info['time_delta_front']
                    
                    if 0 < gap < 2.0:
                         # Find car physically ahead
                         target_place = curr_info['place'] - 1
                         target_driver = drivers_by_place.get(target_place)
                         
                         if target_driver:
                             # CHECK CLASS: Only report same-class battles
                             if target_driver.get('class_id') == curr_info.get('class_id'):
                                 if gap < min_gap:
                                     min_gap = gap
                                     closest_driver = curr_info
                                     closest_target = target_driver
                
                if closest_driver and closest_target:
                    # Check if this specific pair was the last one reported
                    current_pair = frozenset([closest_driver['name'], closest_target['name']])
                    if current_pair == self.last_closest_battle_pair:
                        # Still the same closest battle, don't report again yet
                        closest_driver = None 
                        closest_target = None

                if closest_driver and closest_target:
                    c_name = closest_driver.get('class_name', '')
                    msg = f"Closest battle on track: {closest_driver['name']} is +{min_gap:.3f}s behind {closest_target['name']} for P{closest_driver['place_class']} in {c_name}."
                    # We send minimal data, queue handles priority
                    battle_data = {"gap": min_gap, "place": closest_driver['place_class'], "target_place": closest_target['place_class'], "class_name": c_name}
                    self.log(msg, category="CLOSEST_BATTLE", sim_time=sim_time, extra_data=battle_data)
                    self.last_closest_battle_log_time = now
                    self.last_closest_battle_pair = current_pair

        # 6. Player Specific Damage/Incidents
        # Damage: Assuming 1.0 is healthy
        player_name = self.decode_string(current_data.player_name)
        dmg = current_data.car_damage
        if current_data.session_phase == R3E_SESSION_PHASE_GREEN:
            for part in ['engine', 'aerodynamics', 'suspension']:
                curr_val = getattr(dmg, part)
                if curr_val < self.prev_damage[part] and curr_val != -1.0:
                    self.log(f"Accident! {player_name} has sustained {part} damage.", category="DAMAGE", sim_time=sim_time)
                self.prev_damage[part] = curr_val

        if current_data.incident_points != self.prev_incident_points and self.prev_incident_points != -1:
            diff = current_data.incident_points - self.prev_incident_points
            if diff > 0:
                self.log(f"Incident: {player_name} gained {diff} incident points. (Total: {current_data.incident_points})", category="INCIDENT", sim_time=sim_time)
        self.prev_incident_points = current_data.incident_points

        # Update stored driver map
        self.prev_driver_map = current_driver_map

        # 7. Laps (Player)
        if current_data.completed_laps != self.prev_completed_laps and self.prev_completed_laps != -1:
             self.log(f"Lap Completed! {player_name} is starting Lap {current_data.completed_laps + 1}.", category="LAP", sim_time=sim_time)
             if current_data.lap_time_previous_self > 0:
                 self.log(f"  -> Last Lap Time: {current_data.lap_time_previous_self:.3f}s", category="LAP", sim_time=sim_time)
        self.prev_completed_laps = current_data.completed_laps

        # 8. Pits (Player) - Removed as it is now handled by generic driver tracking
        self.prev_in_pitlane = current_data.in_pitlane

        # 9. Periodic Leaderboard Update (Every 4 minutes during Green Flag Racing)
        if current_data.session_phase == R3E_SESSION_PHASE_GREEN:
             interval = settings.get("logger", "leaderboard_interval", 240)
             if now - self.last_leaderboard_log_time >= interval:
                 self.log_leaderboard(current_driver_map, title="Periodic Leaderboard Update", sim_time=sim_time)


def main():
    logger = RaceRoomLogger()
    print("RaceRoom Commentator Log Started...")
    print("Waiting for RaceRoom to start...")
    
    while True:
        try:
            settings.load() # Reload settings on each loop to allow dynamic updates
            poll_rate = settings.get("logger", "poll_rate", 0.5)
            logger.update()
            time.sleep(poll_rate)
        except KeyboardInterrupt:
            print("\nStopping Logger.")
            logger._close_and_cleanup_logs()
            break
        except Exception as e:
            print(f"Unexpected Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
