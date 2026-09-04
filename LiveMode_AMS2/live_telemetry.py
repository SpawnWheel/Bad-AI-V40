import ctypes
import mmap
import time
import os
import datetime
import threading
import random
import json
from typing import Dict, Any, Optional

from .shared_structs import (
    SharedMemory, ParticipantInfo, GAME_STATES, SESSION_STATES, RACE_STATES, 
    FLAG_COLOURS, PIT_MODES, PIT_SCHEDULE_PENALTIES, format_sim_time,
    SHARED_MEMORY_NAME, STRING_LENGTH_MAX, VK_F12
)
from .event_queue import EventQueue, LiveEvent

def sanitize_filename(name):
    name = name.replace(":", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")
    return "".join(c for c in name if c.isalnum() or c in ("_", "-"))

class LiveTelemetryReader:
    def __init__(self, queue: EventQueue, ttl_multiplier: float = 1.0, leaderboard_interval_seconds: float = 240.0):
        self.queue = queue
        self.ttl_multiplier = ttl_multiplier
        self.leaderboard_interval_seconds = leaderboard_interval_seconds
        self.shm = None
        self.data = None
        self._stop_flag = False
        self._thread = None
        
        # State tracking mirroring ams2_logger.py
        self.last_leaderboard_time = None
        self.last_session_state = None
        self.last_race_state = None
        self.last_pit_mode = None
        self.last_flag_colour = None
        self.last_positions = {}
        self.last_lap_distances = {}
        self.last_laps_completed = {}
        self.last_laps = {}
        self.participant_cars = {}
        self.participant_classes = {}
        self.participant_pit_modes = {}
        self.participant_race_states = {}
        self.finished_participants = set()
        self.assumed_retired_participants = set()
        self.last_overtake_time = {}
        self.accidents = {}
        self.last_movement_time = {}
        self.battles = {}
        self.checkered_flag_shown = False
        self.starting_grid_pending = False
        self.starting_grid_pending_sim_time = None
        self.green_flag_fired = False
        self.green_flag_logged = False
        self.manual_timer_expired = False
        self.f12_was_pressed = False
        self.safety_car_active = False
        self.sc_vehicle_on_track = False
        self.participant_pit_schedules = {}
        self.distance_history = {}
        self.last_track_key = ""
        self.current_track_corners = []
        self.corner_names_dir = self._find_corner_names_dir()

    def _find_corner_names_dir(self):
        """Locate Corner Names folder."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target = os.path.join(base_dir, "Corner Names")
        if os.path.exists(target):
            return target
        if os.path.exists("Corner Names"):
            return os.path.abspath("Corner Names")
        return target

    def load_track_corners(self, track_location, track_variation=""):
        """Load corner definitions for active track from Corner Names/."""
        self.current_track_corners = []
        if not track_location or not os.path.exists(self.corner_names_dir):
            return

        loc_clean = sanitize_filename(track_location).lower()
        var_clean = sanitize_filename(track_variation).lower() if track_variation else ""

        matched_file = None
        exact_candidate = f"{sanitize_filename(track_location)}_{sanitize_filename(track_variation)}.json".strip("_")
        loc_candidate = f"{sanitize_filename(track_location)}.json"

        exact_path = os.path.join(self.corner_names_dir, exact_candidate)
        loc_path = os.path.join(self.corner_names_dir, loc_candidate)

        if os.path.exists(exact_path):
            matched_file = exact_path
        elif os.path.exists(loc_path):
            matched_file = loc_path
        else:
            for f in os.listdir(self.corner_names_dir):
                if f.endswith(".json") and f not in ("trackLandmarksData.json", "raceroomTrackLandmarksData.json"):
                    f_lower = f.lower()
                    if loc_clean and loc_clean in f_lower:
                        if var_clean and var_clean in f_lower:
                            matched_file = os.path.join(self.corner_names_dir, f)
                            break
                        elif not matched_file:
                            matched_file = os.path.join(self.corner_names_dir, f)

        if matched_file and os.path.exists(matched_file):
            try:
                with open(matched_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.current_track_corners = data.get("corners", [])
                print(f"[LIVE] Loaded {len(self.current_track_corners)} corners for {track_location}")
            except Exception as e:
                print(f"[LIVE] Error reading corner file {matched_file}: {e}")

    def get_corner_at_distance(self, distance):
        if not self.current_track_corners or distance is None:
            return None
        for c in self.current_track_corners:
            start = c.get("start", 0.0)
            end = c.get("end", 0.0)
            name = c.get("name", "")
            if start <= end:
                if start <= distance <= end:
                    return name
            else:
                if distance >= start or distance <= end:
                    return name
        return None

    def connect(self) -> bool:
        try:
            shm_size = ctypes.sizeof(SharedMemory)
            self.shm = mmap.mmap(-1, shm_size, SHARED_MEMORY_NAME)
            self.data = SharedMemory.from_buffer(self.shm)
            return True
        except Exception as e:
            print(f"Could not connect to AMS2: {e}")
            return False

    def start(self):
        self._stop_flag = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_flag = True
        if self._thread:
            self._thread.join(timeout=2.0)
            
    def _poll_loop(self):
        while not self._stop_flag:
            if self.data is None and not self.connect():
                time.sleep(1.0)
                continue
            
            try:
                self._update()
            except Exception as e:
                print(f"Error in telemetry update: {e}")
                
            time.sleep(0.5)

    def decode(self, b_arr):
        try:
            return b_arr.value.decode('utf-8', errors='ignore').strip('\x00')
        except AttributeError:
            if hasattr(b_arr, 'decode'):
                return b_arr.decode('utf-8', errors='ignore').strip('\x00')
            return str(b_arr)

    def is_safety_car(self, name, car, cls):
        sc_keywords = ["safety car", "pace car"]
        for kw in sc_keywords:
            if kw in name.lower() or kw in car.lower() or kw in cls.lower():
                return True
        return False

    def get_session_time(self):
        if self.data is not None and self.data.mCurrentTime >= 0:
            return self.data.mCurrentTime
        return 0.0

    def check_f12_key(self):
        try:
            state = ctypes.windll.user32.GetAsyncKeyState(VK_F12)
            is_pressed = (state & 0x8000) != 0
            if is_pressed and not self.f12_was_pressed:
                self.f12_was_pressed = True
                return True
            elif not is_pressed:
                self.f12_was_pressed = False
            return False
        except Exception:
            return False

    def _push_event(self, category: str, message: str, position: int = 0, sub_type: str = ""):
        event = LiveEvent(
            category=category,
            message=message,
            timestamp=time.time(),
            position=position,
            sub_type=sub_type
        )
        self.queue.add_event(event)

    def _update(self):
        if not self.data:
            return

        sim_now = self.get_session_time()

        # Track Identification & Reporting
        track_loc = self.decode(self.data.mTrackLocation)
        track_var = self.decode(self.data.mTrackVariation)
        track_key = f"{track_loc}_{track_var}".strip("_")
        if track_loc and track_key != self.last_track_key:
            self.last_track_key = track_key
            self.load_track_corners(track_loc, track_var)

        # Session and State
        if self.data.mSessionState != self.last_session_state:
            session_name = SESSION_STATES.get(self.data.mSessionState, "Unknown")
            self._push_event("SESSION", f"Session Changed: We are now in {session_name}.")

            self.last_session_state = self.data.mSessionState
            self.checkered_flag_shown = False
            self.finished_participants.clear()
            self.starting_grid_pending = False
            self.starting_grid_pending_sim_time = None
            self.green_flag_fired = False
            self.green_flag_logged = False
            self.manual_timer_expired = False
            self.safety_car_active = False
            self.sc_vehicle_on_track = False
            self.participant_pit_schedules.clear()
            self.distance_history.clear()

        # Phase Updates
        if self.data.mPitMode != self.last_pit_mode:
            if self.data.mPitMode == 4: # In Garage
                self._push_event("SESSION", "Phase Update: in the Garage")
            self.last_pit_mode = self.data.mPitMode

        if self.data.mRaceState != self.last_race_state:
            if self.data.mRaceState == 1: # Not Started / Countdown
                self._push_event("SESSION", "Phase Update: Counting Down")
                if self.data.mSessionState == 5: # Race
                    if self.data.mLapsInEvent > 0:
                        self._push_event("SESSION", f"Race Distance: {self.data.mLapsInEvent} laps.")
                    if self.data.mEventTimeRemaining > 0.0:
                        duration = int(self.data.mEventTimeRemaining / 60)
                        if duration > 0:
                            self._push_event("SESSION", f"Race Duration: {duration} minutes.")
            elif self.data.mRaceState == 2: # Racing
                if not self.green_flag_fired and self.data.mSessionState == 5:
                    self.last_leaderboard_time = sim_now
                    self.green_flag_fired = True
                    self.starting_grid_pending = True
                    self.starting_grid_pending_sim_time = sim_now
            self.last_race_state = self.data.mRaceState

        # Deferred Starting Grid
        if self.starting_grid_pending:
            positions_ready = False
            for i in range(self.data.mNumParticipants):
                p = self.data.mParticipantInfo[i]
                if p.mIsActive and p.mRacePosition > 0:
                    positions_ready = True
                    break
            
            if positions_ready or (sim_now - self.starting_grid_pending_sim_time >= 10.0):
                self._log_starting_grid()
                if not self.green_flag_logged:
                    self._push_event("SESSION", "Green Flag! The race begins!")
                    self.green_flag_logged = True
                self.starting_grid_pending = False

        # Periodic Leaderboard Updates
        if self.data.mRaceState == 2 and self.last_leaderboard_time is not None:
            if sim_now - self.last_leaderboard_time > self.leaderboard_interval_seconds:
                self._log_periodic_leaderboard()
                self.last_leaderboard_time = sim_now

        # Pre-scan for safety car
        sc_on_track_now = False
        for i in range(self.data.mNumParticipants):
            p = self.data.mParticipantInfo[i]
            if p.mIsActive:
                sc_name = self.decode(p.mName)
                sc_car = self.decode(self.data.mCarNames[i])
                sc_cls = self.decode(self.data.mCarClassNames[i])
                if self.is_safety_car(sc_name, sc_car, sc_cls):
                    sc_pit = self.data.mPitModes[i]
                    if sc_pit == 0:
                        sc_on_track_now = True
                    break

        # Safety Car Detection
        if sc_on_track_now and self.data.mHighestFlagColour == 7 and not self.safety_car_active:
            self.safety_car_active = True
            self._push_event("SAFETY_CAR", "Safety Car has been deployed!")
            self._log_safety_car_leaderboard("Safety Car Deployed")
        elif self.safety_car_active and self.sc_vehicle_on_track and not sc_on_track_now:
            self.safety_car_active = False
            self._push_event("SAFETY_CAR", "Safety Car period is over. Racing resumes!")
            self._log_safety_car_leaderboard("Safety Car Ending")

        self.sc_vehicle_on_track = sc_on_track_now

        # Flags
        if self.data.mHighestFlagColour != self.last_flag_colour:
            flag_name = FLAG_COLOURS.get(self.data.mHighestFlagColour, "None")
            if self.data.mHighestFlagColour == 1: # Green
                if self.last_flag_colour in [6, 7]:
                    if not self.safety_car_active:
                        self._push_event("FLAG", "Flag: Green again (Yellow cleared).")
            elif self.data.mHighestFlagColour == 2: # Blue
                pass
            elif self.data.mHighestFlagColour == 7: # Double Yellow
                if not self.safety_car_active:
                    self._push_event("FLAG", f"Flag: {flag_name}")
            elif self.data.mHighestFlagColour != 0 and self.data.mHighestFlagColour != 11:
                self._push_event("FLAG", f"Flag: {flag_name}")
            self.last_flag_colour = self.data.mHighestFlagColour

        # F12 manual timer trigger
        if self.check_f12_key() and not self.manual_timer_expired:
            self.manual_timer_expired = True
            self._push_event("SESSION", "Manual race end triggered (F12). Waiting for P1 to cross the line.")

        # Checkered Flag Trigger
        if not self.checkered_flag_shown and self.data.mSessionState == 5: # Race
            if self.data.mRaceState == 3:
                self.checkered_flag_shown = True
            elif self.data.mHighestFlagColour == 11:
                self.checkered_flag_shown = True
            else:
                for i in range(self.data.mNumParticipants):
                    if self.data.mParticipantInfo[i].mIsActive and self.data.mRaceStates[i] == 3:
                        self.checkered_flag_shown = True
                        break
                
                if not self.checkered_flag_shown and self.data.mRaceState == 2:
                    leader_idx = -1
                    for i in range(self.data.mNumParticipants):
                        if self.data.mParticipantInfo[i].mIsActive and self.data.mParticipantInfo[i].mRacePosition == 1:
                            leader_idx = i
                            break
                    
                    if leader_idx != -1:
                        p_leader = self.data.mParticipantInfo[leader_idx]
                        leader_name = self.decode(p_leader.mName)
                        leader_laps = p_leader.mLapsCompleted
                        old_leader_laps = self.last_laps_completed.get(leader_name, leader_laps)
                        leader_lap = p_leader.mCurrentLap
                        old_leader_lap = getattr(self, 'last_laps', {}).get(leader_name, leader_lap)
                        
                        is_time_up = (self.data.mEventTimeRemaining != -1.0 and self.data.mEventTimeRemaining <= 0.0)
                        is_laps_up = (self.data.mLapsInEvent > 0 and leader_laps >= self.data.mLapsInEvent)
                        
                        leader_crossed_line = (leader_laps > old_leader_laps) or (leader_lap > old_leader_lap)
                        
                        if leader_crossed_line:
                            if is_time_up or is_laps_up or self.manual_timer_expired:
                                self.checkered_flag_shown = True

        # Participants
        active_participants = []
        new_positions = {}
        new_lap_distances = {}
        new_laps_completed = {}
        new_laps = {}
        for i in range(self.data.mNumParticipants):
            p = self.data.mParticipantInfo[i]
            if p.mIsActive:
                name = self.decode(p.mName)
                car_name = self.decode(self.data.mCarNames[i])
                class_name = self.decode(self.data.mCarClassNames[i])
                
                if self.is_safety_car(name, car_name, class_name):
                    continue
                    
                pos = p.mRacePosition
                dist = p.mCurrentLapDistance
                speed = self.data.mSpeeds[i]
                lap = p.mCurrentLap
                laps_completed = p.mLapsCompleted
                pit_mode = self.data.mPitModes[i]
                race_state = self.data.mRaceStates[i]

                # Record distance history for trajectory interpolation
                track_length = self.data.mTrackLength if (self.data and self.data.mTrackLength > 0) else 4000.0
                total_dist = laps_completed * track_length + dist
                self._record_distance_history(name, sim_now, total_dist)

                if speed < 2.0:
                    if name not in getattr(self, 'last_movement_time', {}):
                        if not hasattr(self, 'last_movement_time'): self.last_movement_time = {}
                        self.last_movement_time[name] = sim_now
                    elif sim_now - self.last_movement_time[name] >= 120.0 and name not in self.finished_participants:
                        self._push_event("SESSION", f"{name} has not moved in 2 minutes and is assumed retired.", position=pos)
                        self.finished_participants.add(name)
                        if not hasattr(self, 'assumed_retired_participants'): self.assumed_retired_participants = set()
                        self.assumed_retired_participants.add(name)
                        if name in self.accidents:
                            del self.accidents[name]
                else:
                    if name in getattr(self, 'last_movement_time', {}):
                        del self.last_movement_time[name]
                        if name in getattr(self, 'assumed_retired_participants', set()):
                            self.assumed_retired_participants.remove(name)
                            if name in self.finished_participants:
                                self.finished_participants.remove(name)
                            self._push_event("SESSION", f"Notice: {name} was assumed retired but has started moving again!", position=pos)

                old_race_state = self.participant_race_states.get(name, race_state)
                old_laps = self.last_laps_completed.get(name, laps_completed)
                old_lap = getattr(self, 'last_laps', {}).get(name, lap)
                
                is_finished = False
                crossed_line = (laps_completed > old_laps) or (lap > old_lap)
                
                if not is_finished and self.checkered_flag_shown and name not in self.finished_participants:
                    if crossed_line:
                        is_finished = True

                if is_finished and name not in self.finished_participants:
                    if pos == 1:
                        self._push_event("FINISH", f"Checkered Flag! {name} took P1!", position=pos)
                        self.checkered_flag_shown = True
                    else:
                        self._push_event("FINISH", f"Checkered Flag! {name} comes past the line to take P{pos}.", position=pos)
                    self.finished_participants.add(name)
                
                if race_state != old_race_state and name not in self.finished_participants:
                    if race_state == 4:
                        self._push_event("SESSION", f"{name} has been disqualified.", position=pos)
                        self.finished_participants.add(name)
                    elif race_state == 5:
                        self._push_event("SESSION", f"{name} has retired from the race.", position=pos)
                        self.finished_participants.add(name)
                    elif race_state == 6:
                        self._push_event("SESSION", f"{name} is DNF.", position=pos)
                        self.finished_participants.add(name)
                
                self.participant_race_states[name] = race_state
                new_laps_completed[name] = laps_completed
                new_laps[name] = lap

                if name not in self.finished_participants:
                    old_pit_mode = self.participant_pit_modes.get(name, 0)
                    if pit_mode != old_pit_mode:
                        if pit_mode == 1:
                            self._push_event("PIT", f"{name} has entered the pit lane.", position=pos)
                        elif pit_mode == 2:
                            self._push_event("PIT", f"{name} has arrived at their pit box.", position=pos)
                        elif pit_mode == 3:
                            self._push_event("PIT", f"{name} is leaving the pits.", position=pos)
                
                self.participant_pit_modes[name] = pit_mode

                if name not in self.finished_participants:
                    pit_schedule = self.data.mPitSchedules[i]
                    old_pit_schedule = self.participant_pit_schedules.get(name, 0)
                    if pit_schedule != old_pit_schedule:
                        if pit_schedule in PIT_SCHEDULE_PENALTIES and old_pit_schedule not in PIT_SCHEDULE_PENALTIES:
                            penalty_name = PIT_SCHEDULE_PENALTIES[pit_schedule]
                            self._push_event("PENALTY", f"{name} (P{pos}) has been given a {penalty_name} penalty!", position=pos)
                        elif old_pit_schedule in PIT_SCHEDULE_PENALTIES and pit_schedule not in PIT_SCHEDULE_PENALTIES:
                            self._push_event("PENALTY", f"{name} has served their penalty.", position=pos)
                    self.participant_pit_schedules[name] = pit_schedule
                    
                self.participant_cars[name] = car_name
                self.participant_classes[name] = class_name
                new_positions[name] = pos
                new_lap_distances[name] = dist
                active_participants.append({
                    "name": name, 
                    "pos": pos, 
                    "dist": dist, 
                    "speed": speed, 
                    "class": class_name, 
                    "lap": lap, 
                    "pit": pit_mode
                })

        if self.last_positions and self.data.mSessionState == 5:
            disconnected = set(self.last_positions.keys()) - set(new_positions.keys())
            for name in disconnected:
                if name not in self.finished_participants:
                    old_pos = self.last_positions.get(name, 0)
                    self._push_event("SESSION", f"{name} has retired and left the session.", position=old_pos)
                    self.finished_participants.add(name)

        # Overtakes
        if self.last_positions:
            for name, pos in new_positions.items():
                if name in self.finished_participants or self.participant_pit_modes.get(name, 0) != 0:
                    continue
                
                if name in self.last_positions:
                    old_pos = self.last_positions[name]
                    if pos < old_pos and old_pos != 0:
                        overtaken = []
                        for other_name, other_old_pos in self.last_positions.items():
                            if other_name != name:
                                if other_name in self.finished_participants or self.participant_pit_modes.get(other_name, 0) != 0:
                                    continue
                                
                                other_new_pos = new_positions.get(other_name)
                                if other_new_pos and other_old_pos < old_pos and other_new_pos > pos:
                                    overtaken.append(other_name)
                        
                        if overtaken:
                            overtaken_str = ", ".join(overtaken)
                            class_name = self.participant_classes.get(name, "Unknown")
                            dist = new_lap_distances.get(name, 0.0)
                            corner = self.get_corner_at_distance(dist)
                            location_str = f" at {corner}" if corner else ""
                            self._push_event("OVERTAKE", f"{name} overtook {overtaken_str} for P{pos} in {class_name}{location_str}.", position=pos)
                            
                            self.last_overtake_time[name] = sim_now
                            for o_name in overtaken:
                                self.last_overtake_time[o_name] = sim_now

        # Accidents
        green_flag_sim_time = self.starting_grid_pending_sim_time if self.starting_grid_pending_sim_time is not None else 0
        accident_cooldown = self.green_flag_fired and (sim_now - green_flag_sim_time < 20)
        
        for p in active_participants:
            name, pos, dist, speed, class_name, lap, pit = p.values()
            if name in self.finished_participants or pit != 0:
                if name in self.accidents:
                    del self.accidents[name]
                continue

            if self.data.mRaceState == 2 and speed < 2.0 and dist > 10 and not accident_cooldown:
                if name not in self.accidents:
                    self.accidents[name] = sim_now
                    corner = self.get_corner_at_distance(dist)
                    location_str = f" at {corner}" if corner else ""
                    self._push_event("ACCIDENT", f"Alert: {name} (P{pos} in {class_name}) is slow/stopped on track{location_str}! Potential accident.", position=pos)
            elif name in self.accidents:
                if speed > 5.0:
                    del self.accidents[name]
                    self._push_event("ACCIDENT", f"Notice: {name} is back on the move.", position=pos)

        # Battles
        if self.safety_car_active:
            self.battles.clear()
        else:
            sorted_participants = sorted(active_participants, key=lambda x: x["pos"])
            for i in range(len(sorted_participants) - 1):
                p1 = sorted_participants[i]
                p2 = sorted_participants[i+1]
                name1, pos1, dist1, speed1, class1, lap1, pit1 = p1.values()
                name2, pos2, dist2, speed2, class2, lap2, pit2 = p2.values()
                
                if name1 in self.finished_participants or name2 in self.finished_participants:
                    continue

                if lap1 <= 1 or lap2 <= 1:
                    continue
                
                if pit1 != 0 or pit2 != 0:
                    continue

                dist_diff = abs(dist1 - dist2)
                battle_key = tuple(sorted([name1, name2]))
                battle_info = self.battles.get(battle_key)
                
                last_ot1 = self.last_overtake_time.get(name1, 0)
                last_ot2 = self.last_overtake_time.get(name2, 0)
                since_ot = min(sim_now - last_ot1, sim_now - last_ot2)

                if abs(speed1 - speed2) > 10.0:
                    continue
                    
                if since_ot < 20.0:
                    continue

                min_pos = min(pos1, pos2)

                if dist_diff < 2.5:
                    sbs_cooldown = 15.0
                    if not battle_info or battle_info["type"] != "Side by side" or (sim_now - battle_info["time"] > sbs_cooldown):
                        count = battle_info["count"] + 1 if (battle_info and battle_info["type"] == "Side by side") else 1
                        if count > 1:
                            self._push_event("BATTLE", f"{name2} (P{pos2}) and {name1} (P{pos1}) are still battling side by side!", position=min_pos, sub_type="side_by_side")
                        else:
                            self._push_event("BATTLE", f"Side by side! {name2} (P{pos2}) is fighting {name1} (P{pos1})!", position=min_pos, sub_type="side_by_side")
                        self.battles[battle_key] = {"type": "Side by side", "time": sim_now, "count": count}
                elif dist_diff < 15.0:
                    draft_cooldown = 15.0
                    if not battle_info or (sim_now - battle_info["time"] > draft_cooldown):
                        count = battle_info["count"] + 1 if (battle_info and battle_info["type"] == "Drafting") else 1
                        drafter, leader = (name2, name1) if dist1 > dist2 else (name1, name2)
                        d_pos, l_pos = (pos2, pos1) if dist1 > dist2 else (pos1, pos2)
                        
                        messages = [
                            f"{drafter} (P{d_pos}) is in the draft of {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is still pressuring {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is looking for a way to make the pass on {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is still battling with {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is glued to the back of {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is stalking {leader} (P{l_pos}) through the corners.",
                            f"{drafter} (P{d_pos}) is trying to force a mistake from {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is right in the wheel tracks of {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is refusing to let {leader} (P{l_pos}) escape.",
                            f"{drafter} (P{d_pos}) is all over the back of {leader} (P{l_pos}), looking for an opening.",
                            f"{drafter} (P{d_pos}) is closing in on {leader} (P{l_pos}) under braking.",
                            f"{drafter} (P{d_pos}) is keeping the pressure on {leader} (P{l_pos}) lap after lap.",
                            f"{drafter} (P{d_pos}) has the pace to challenge {leader} (P{l_pos}) here.",
                            f"{drafter} (P{d_pos}) is filling the mirrors of {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is using the slipstream to stay right with {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is biding their time behind {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is probing for a gap on {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is turning up the heat on {leader} (P{l_pos}).",
                            f"{drafter} (P{d_pos}) is nose to tail with {leader} (P{l_pos}), waiting for the moment to strike."
                        ]
                        self._push_event("BATTLE", random.choice(messages), position=min_pos, sub_type="drafting")
                        self.battles[battle_key] = {"type": "Drafting", "time": sim_now, "count": count}

        self.last_positions = new_positions
        self.last_lap_distances = new_lap_distances
        self.last_laps_completed = new_laps_completed
        self.last_laps = new_laps

    def _record_distance_history(self, name, sim_time, total_distance):
        if name not in self.distance_history:
            self.distance_history[name] = []
        hist = self.distance_history[name]
        hist.append((sim_time, total_distance))
        cutoff = sim_time - 90.0
        while hist and hist[0][0] < cutoff:
            hist.pop(0)

    def _calculate_time_gap(self, ahead_info, behind_info, sim_now):
        """
        Calculate the time gap between a driver and the driver directly ahead.
        Uses trajectory/distance history interpolation when available,
        falling back to pace-based calculation.
        """
        if behind_info.get("race_state") in [4, 5, 6] or behind_info["name"] in getattr(self, "assumed_retired_participants", set()):
            if behind_info.get("race_state") == 4:
                return "DQ"
            elif behind_info.get("race_state") == 6:
                return "DNF"
            else:
                return "Retired"

        track_length = self.data.mTrackLength if (self.data and self.data.mTrackLength > 0) else 4000.0
        
        dist_ahead = ahead_info["laps"] * track_length + ahead_info["dist"]
        dist_behind = behind_info["laps"] * track_length + behind_info["dist"]
        
        dist_diff = dist_ahead - dist_behind

        # Check for lapped status
        if dist_diff >= track_length:
            laps_behind = int(dist_diff // track_length)
            if laps_behind == 1:
                return "+1 Lap"
            elif laps_behind > 1:
                return f"+{laps_behind} Laps"

        # Try to find exact time gap using distance history of the car ahead
        hist_ahead = self.distance_history.get(ahead_info["name"], [])
        if hist_ahead and len(hist_ahead) >= 2:
            for j in range(len(hist_ahead) - 1):
                t1, d1 = hist_ahead[j]
                t2, d2 = hist_ahead[j + 1]
                if d1 <= dist_behind <= d2 and d2 > d1:
                    fraction = (dist_behind - d1) / (d2 - d1)
                    t_ahead_at_dist = t1 + fraction * (t2 - t1)
                    gap = sim_now - t_ahead_at_dist
                    if gap >= 0.0:
                        return f"+{gap:.3f}s"

        # Fallback: estimate time gap from distance difference and pace
        dist_gap = max(0.0, dist_diff)
        
        speed = 0.0
        if behind_info.get("last_lap_time", 0) > 10.0:
            speed = track_length / behind_info["last_lap_time"]
        elif ahead_info.get("last_lap_time", 0) > 10.0:
            speed = track_length / ahead_info["last_lap_time"]
        elif behind_info.get("fastest_lap_time", 0) > 10.0:
            speed = track_length / behind_info["fastest_lap_time"]
        elif ahead_info.get("fastest_lap_time", 0) > 10.0:
            speed = track_length / ahead_info["fastest_lap_time"]
        elif behind_info.get("speed", 0) > 5.0:
            speed = behind_info["speed"]
        elif ahead_info.get("speed", 0) > 5.0:
            speed = ahead_info["speed"]
        else:
            speed = 40.0

        gap = dist_gap / speed if speed > 0 else 0.0
        return f"+{gap:.3f}s"

    def _log_periodic_leaderboard(self):
        sim_now = self.get_session_time()
        grid = []
        for i in range(self.data.mNumParticipants):
            p = self.data.mParticipantInfo[i]
            if p.mIsActive:
                name = self.decode(p.mName)
                car = self.decode(self.data.mCarNames[i])
                cls = self.decode(self.data.mCarClassNames[i])
                
                if self.is_safety_car(name, car, cls):
                    continue
                    
                pos = p.mRacePosition
                grid.append({
                    "pos": pos,
                    "name": name,
                    "car": car,
                    "class": cls,
                    "dist": p.mCurrentLapDistance,
                    "laps": p.mLapsCompleted,
                    "lap": p.mCurrentLap,
                    "speed": self.data.mSpeeds[i],
                    "last_lap_time": self.data.mLastLapTimes[i],
                    "fastest_lap_time": self.data.mFastestLapTimes[i],
                    "race_state": self.data.mRaceStates[i],
                    "idx": i
                })
        
        grid.sort(key=lambda x: x["pos"])
        
        entries = []
        for idx, item in enumerate(grid):
            pos = item["pos"]
            name = item["name"]
            if idx == 0 or pos == 1:
                gap_str = "Leader"
            else:
                ahead_item = grid[idx - 1]
                gap_str = self._calculate_time_gap(ahead_item, item, sim_now)
            
            entries.append(f"P{pos}: {name} ({gap_str})")
        
        leaderboard_str = " | ".join(entries)
        self._push_event("LEADERBOARD", f"Current Standings: {leaderboard_str}")

    def _log_starting_grid(self):
        grid = []
        for i in range(self.data.mNumParticipants):
            p = self.data.mParticipantInfo[i]
            if p.mIsActive:
                name = self.decode(p.mName)
                car = self.decode(self.data.mCarNames[i])
                cls = self.decode(self.data.mCarClassNames[i])
                
                if self.is_safety_car(name, car, cls):
                    continue
                    
                pos = p.mRacePosition
                
                grid.append({
                    "pos": pos,
                    "name": name,
                    "car": car
                })
        
        grid.sort(key=lambda x: x["pos"])
            
        grid_str = " | ".join([f"P{item['pos']}: {item['name']} [{item['car']}]" for item in grid])
        self._push_event("LEADERBOARD", f"Starting Grid Order: {grid_str}")

    def _log_safety_car_leaderboard(self, label):
        grid = []
        for i in range(self.data.mNumParticipants):
            p = self.data.mParticipantInfo[i]
            if p.mIsActive:
                name = self.decode(p.mName)
                car = self.decode(self.data.mCarNames[i])
                cls = self.decode(self.data.mCarClassNames[i])
                
                if self.is_safety_car(name, car, cls):
                    continue
                    
                pos = p.mRacePosition
                grid.append((pos, name))
        
        grid.sort(key=lambda x: x[0])
        leaderboard_str = " | ".join([f"P{pos}: {name}" for pos, name in grid])
        self._push_event("LEADERBOARD", f"{label} Standings: {leaderboard_str}")

    def get_race_context(self) -> Dict[str, Any]:
        if not self.data:
            return {
                'track_name': 'Unknown',
                'current_lap': 1,
                'total_laps': 'Unknown',
                'leader_name': 'Unknown',
                'standings': '',
                'session_state': 'Unknown',
                'race_active': False
            }

        track_name = f"{self.decode(self.data.mTranslatedTrackLocation)} {self.decode(self.data.mTranslatedTrackVariation)}".strip()
        if not track_name:
            track_name = f"{self.decode(self.data.mTrackLocation)} {self.decode(self.data.mTrackVariation)}".strip()
            
        total_laps = self.data.mLapsInEvent if self.data.mLapsInEvent > 0 else 'Unknown'
        session_state = SESSION_STATES.get(self.data.mSessionState, "Unknown")
        race_active = self.data.mRaceState == 2

        leader_name = "Unknown"
        current_lap = 1
        grid = []
        sim_now = self.get_session_time()
        
        for i in range(self.data.mNumParticipants):
            p = self.data.mParticipantInfo[i]
            if p.mIsActive:
                name = self.decode(p.mName)
                car = self.decode(self.data.mCarNames[i])
                cls = self.decode(self.data.mCarClassNames[i])
                
                if self.is_safety_car(name, car, cls):
                    continue
                    
                pos = p.mRacePosition
                grid.append({
                    "pos": pos,
                    "name": name,
                    "car": car,
                    "class": cls,
                    "dist": p.mCurrentLapDistance,
                    "laps": p.mLapsCompleted,
                    "lap": p.mCurrentLap,
                    "speed": self.data.mSpeeds[i],
                    "last_lap_time": self.data.mLastLapTimes[i],
                    "fastest_lap_time": self.data.mFastestLapTimes[i],
                    "race_state": self.data.mRaceStates[i],
                    "idx": i
                })
                
                if pos == 1:
                    leader_name = name
                    current_lap = p.mCurrentLap
                    
        grid.sort(key=lambda x: x["pos"])
        entries = []
        for idx, item in enumerate(grid):
            pos = item["pos"]
            name = item["name"]
            if idx == 0 or pos == 1:
                gap_str = "Leader"
            else:
                ahead_item = grid[idx - 1]
                gap_str = self._calculate_time_gap(ahead_item, item, sim_now)
            entries.append(f"P{pos}: {name} ({gap_str})")
        standings = " | ".join(entries)

        return {
            'track_name': track_name,
            'current_lap': current_lap,
            'total_laps': total_laps,
            'leader_name': leader_name,
            'standings': standings,
            'session_state': session_state,
            'race_active': race_active
        }
