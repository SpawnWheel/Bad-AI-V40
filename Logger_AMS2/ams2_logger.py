import ctypes
import mmap
import time
import os
import datetime
import sys
import random

# Constants
STRING_LENGTH_MAX = 64
STORED_PARTICIPANTS_MAX = 64
TYRE_MAX = 4
VEC_MAX = 3
TYRE_COMPOUND_NAME_LENGTH_MAX = 40
SHARED_MEMORY_NAME = "$pcars2$"

# Enums
GAME_STATES = {
    0: "Exited",
    1: "Front End",
    2: "Playing",
    3: "Paused",
    4: "In Menu (Time Ticking)",
    5: "Restarting",
    6: "Replay",
    7: "Front End Replay"
}

SESSION_STATES = {
    0: "Invalid",
    1: "Practice",
    2: "Test",
    3: "Qualify",
    4: "Formation Lap",
    5: "Race",
    6: "Time Attack"
}

RACE_STATES = {
    0: "Invalid",
    1: "Not Started",
    2: "Racing",
    3: "Finished",
    4: "Disqualified",
    5: "Retired",
    6: "DNF"
}

FLAG_COLOURS = {
    0: "None",
    1: "Green",
    2: "Blue",
    3: "White (Slow Car)",
    4: "White (Final Lap)",
    5: "Red",
    6: "Yellow",
    7: "Double Yellow",
    8: "Black and White",
    9: "Black Orange Circle",
    10: "Black",
    11: "Chequered"
}

PIT_MODES = {
    0: "None",
    1: "Driving into Pits",
    2: "In Pit",
    3: "Driving out of Pits",
    4: "In Garage",
    5: "Driving out of Garage"
}

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
        ("mBrakeBias", ctypes.c_float),
        ("mTurboBoostPressure", ctypes.c_float),
        ("mTyreCompound", (ctypes.c_char * TYRE_COMPOUND_NAME_LENGTH_MAX) * TYRE_MAX),
        ("mPitSchedules", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mHighestFlagColours", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mHighestFlagReasons", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mNationalities", ctypes.c_uint * STORED_PARTICIPANTS_MAX),
        ("mSnowDensity", ctypes.c_float),
    ]

class AMS2EventLogger:
    def __init__(self):
        self.shm = None
        self.data = None
        self.log_file = None
        self.start_time = None
        self.race_start_time = None # Track when the green flag drops
        self.race_start_sim_time = None # Track the sim time when the green flag drops to offset log timestamps
        self.recorded_laps_in_event = 0
        self.recorded_finish_sim_time = -1.0
        self.last_leaderboard_time = None # Track periodic leaderboard updates
        self.last_session_state = None
        self.last_race_state = None
        self.last_pit_mode = None
        self.last_flag_colour = None
        self.last_positions = {} # name -> pos
        self.last_lap_distances = {} # name -> distance
        self.last_laps_completed = {} # name -> laps
        self.participant_cars = {} # name -> car_name
        self.participant_classes = {} # name -> class_name
        self.participant_pit_modes = {} # name -> pit_mode
        self.participant_race_states = {} # name -> race_state
        self.finished_participants = set() # set of names who have finished
        self.last_overtake_time = {} # name -> timestamp
        self.accidents = {} # name -> start_time
        self.battles = {} # (name1, name2) -> {"type": str, "time": float, "count": int}
        self.checkered_flag_shown = False
        self.last_sim_time = -1.0

    def connect(self):
        try:
            shm_size = ctypes.sizeof(SharedMemory)
            self.shm = mmap.mmap(-1, shm_size, SHARED_MEMORY_NAME)
            self.data = SharedMemory.from_buffer(self.shm)
            print(f"Connected to AMS2 Shared Memory ({shm_size} bytes)")
            print(f"Shared Memory Version: {self.data.mVersion}, Build: {self.data.mBuildVersionNumber}")
            return True
        except Exception as e:
            print(f"Could not connect to AMS2: {e}")
            return False

    def start_logging(self):
        project_path = os.environ.get("R3E_PROJECT_PATH")
        if project_path:
            log_dir = os.path.join(project_path, "Logs")
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
        else:
            log_dir = "."

        now = datetime.datetime.now()
        filename = f"Race_{now.strftime('%Y%m%d_%H%M%S')}.txt"
        filepath = os.path.join(log_dir, filename)
        
        self.log_file = open(filepath, "w", encoding="utf-8")
        self.start_time = time.time()
        self.log("SYSTEM", f"New log file started: {os.path.abspath(filepath)}")

    def log(self, category, message, sim_time=None):
        if sim_time is not None:
            # Shift the sim_time so the log timestamp aligns with the race start (0:00:00)
            if hasattr(self, 'race_start_sim_time') and self.race_start_sim_time is not None:
                sim_time = max(0, sim_time - self.race_start_sim_time)
                
            # mCurrentTime is in seconds
            ts_val = max(0, int(sim_time))
            timestamp = str(datetime.timedelta(seconds=ts_val))
        else:
            elapsed = 0
            if self.start_time is not None:
                elapsed = max(0, int(time.time() - self.start_time))
            timestamp = str(datetime.timedelta(seconds=elapsed))
        
        log_line = f"{timestamp} - [{category}] {message}"
        print(log_line)
        if self.log_file:
            self.log_file.write(log_line + "\n")
            self.log_file.flush()

    def decode(self, b_arr):
        try:
            return b_arr.value.decode('utf-8', errors='ignore').strip('\x00')
        except AttributeError:
            # If already bytes or something else
            if hasattr(b_arr, 'decode'):
                return b_arr.decode('utf-8', errors='ignore').strip('\x00')
            return str(b_arr)

    def is_safety_car(self, name, car, cls):
        sc_keywords = ["safety car", "pace car"]
        for kw in sc_keywords:
            if kw in name.lower() or kw in car.lower() or kw in cls.lower():
                return True
        return False

    def update(self):
        if not self.data:
            return

        now_time = time.time()

        # Replay / Time Jump detection
        if self.last_sim_time != -1.0 and self.data.mCurrentTime < self.last_sim_time - 5.0:
            self.log("SYSTEM", "Time jump backwards detected. Resetting session tracking for replay.", sim_time=self.data.mCurrentTime)
            self.checkered_flag_shown = False
            self.finished_participants.clear()
            self.last_positions.clear()
            self.last_lap_distances.clear()
            self.last_laps_completed.clear()
            self.battles.clear()
            self.accidents.clear()
            self.last_overtake_time.clear()
            self.participant_race_states.clear()
            self.participant_pit_modes.clear()
            self.race_start_sim_time = None
            self.recorded_laps_in_event = 0
            self.recorded_finish_sim_time = -1.0
            
        self.last_sim_time = self.data.mCurrentTime

        # Session and State
        if self.data.mSessionState != self.last_session_state:
            session_name = SESSION_STATES.get(self.data.mSessionState, "Unknown")
            self.log("SESSION", f"Session Changed: We are now in {session_name}.", sim_time=self.data.mCurrentTime)
            if self.data.mSessionState == 5: # Race
                duration = int(self.data.mEventTimeRemaining / 60) if self.data.mEventTimeRemaining > 0 else 0
                if duration > 0:
                    self.log("SESSION", f"Session Duration: {duration} minutes.", sim_time=self.data.mCurrentTime)
            self.last_session_state = self.data.mSessionState
            self.checkered_flag_shown = False
            self.finished_participants.clear()

        # Phase Updates
        if self.data.mPitMode != self.last_pit_mode:
            if self.data.mPitMode == 4: # In Garage
                self.log("SESSION", "Phase Update: in the Garage", sim_time=self.data.mCurrentTime)
            self.last_pit_mode = self.data.mPitMode

        if self.data.mRaceState != self.last_race_state:
            if self.data.mRaceState == 1: # Not Started
                self.log("SESSION", "Phase Update: Counting Down", sim_time=self.data.mCurrentTime)
                self.log_starting_grid()
            elif self.data.mRaceState == 2: # Racing
                if self.last_race_state == 1:
                    self.race_start_sim_time = self.data.mCurrentTime
                    self.log("SESSION", "Green Flag! The race begins!", sim_time=self.data.mCurrentTime)
                    self.race_start_time = now_time
                    self.last_leaderboard_time = now_time
            self.last_race_state = self.data.mRaceState

        # Record race limits once when racing
        if self.data.mSessionState == 5 and self.data.mRaceState == 2 and self.recorded_finish_sim_time == -1.0:
            self.recorded_laps_in_event = self.data.mLapsInEvent
            if self.data.mEventTimeRemaining > 0.0:
                self.recorded_finish_sim_time = self.data.mCurrentTime + self.data.mEventTimeRemaining
            else:
                self.recorded_finish_sim_time = -2.0

        # Periodic Leaderboard Updates (every 4 minutes)
        if self.data.mRaceState == 2 and self.last_leaderboard_time:
            if now_time - self.last_leaderboard_time > 240: # 4 minutes
                self.log_periodic_leaderboard()
                self.last_leaderboard_time = now_time

        # Flags
        if self.data.mHighestFlagColour != self.last_flag_colour:
            flag_name = FLAG_COLOURS.get(self.data.mHighestFlagColour, "None")
            if self.data.mHighestFlagColour == 1: # Green
                if self.last_flag_colour in [6, 7]: # Only log if clearing a Yellow or Double Yellow
                    self.log("FLAG", "Flag: Green again (Yellow cleared).", sim_time=self.data.mCurrentTime)
            elif self.data.mHighestFlagColour == 2: # Blue
                pass # Suppress blue flag messages
            elif self.data.mHighestFlagColour != 0 and self.data.mHighestFlagColour != 11:
                self.log("FLAG", f"Flag: {flag_name}", sim_time=self.data.mCurrentTime)
            self.last_flag_colour = self.data.mHighestFlagColour

        # Checkered Flag Trigger
        if not self.checkered_flag_shown and self.data.mSessionState == 5: # Race
            if self.data.mRaceState == 3:
                self.checkered_flag_shown = True
            elif self.data.mHighestFlagColour == 11:
                self.checkered_flag_shown = True
            else:
                # 1. Native state transition check
                for i in range(self.data.mNumParticipants):
                    if self.data.mParticipantInfo[i].mIsActive and self.data.mRaceStates[i] == 3:
                        self.checkered_flag_shown = True
                        break
                
                # 2. Replay Fallback: Detect if the leader crossed the line to end the race
                if not self.checkered_flag_shown:
                    leader_idx = -1
                    for i in range(self.data.mNumParticipants):
                        if self.data.mParticipantInfo[i].mIsActive and self.data.mParticipantInfo[i].mRacePosition == 1:
                            leader_idx = i
                            break
                    
                    if leader_idx != -1:
                        p_leader = self.data.mParticipantInfo[leader_idx]
                        leader_name = self.decode(p_leader.mName)
                        leader_laps = p_leader.mLapsCompleted
                        old_leader_laps = self.last_laps_completed.get(leader_name, 0)
                        
                        if leader_laps > old_leader_laps:
                            if self.recorded_laps_in_event > 0 and leader_laps >= self.recorded_laps_in_event:
                                self.checkered_flag_shown = True
                            elif self.recorded_finish_sim_time > 0.0 and self.data.mCurrentTime >= self.recorded_finish_sim_time:
                                self.checkered_flag_shown = True

        # Participants
        active_participants = []
        new_positions = {}
        new_lap_distances = {}
        new_laps_completed = {}
        for i in range(self.data.mNumParticipants):
            p = self.data.mParticipantInfo[i]
            if p.mIsActive:
                name = self.decode(p.mName)
                car_name = self.decode(self.data.mCarNames[i])
                class_name = self.decode(self.data.mCarClassNames[i])
                pos = p.mRacePosition
                dist = p.mCurrentLapDistance
                speed = self.data.mSpeeds[i]
                lap = p.mCurrentLap
                laps_completed = p.mLapsCompleted
                pit_mode = self.data.mPitModes[i]
                race_state = self.data.mRaceStates[i]

                # Finish Detection
                old_race_state = self.participant_race_states.get(name, 0)
                old_laps = self.last_laps_completed.get(name, 0)
                
                is_finished = False
                
                if race_state == 3 and old_race_state != 3:
                    is_finished = True
                elif not is_finished and self.checkered_flag_shown and name not in self.finished_participants:
                    if laps_completed > old_laps:
                        is_finished = True

                if is_finished and name not in self.finished_participants:
                    if pos == 1:
                        self.log("FINISH", f"Checkered Flag! {name} took P1!", sim_time=self.data.mCurrentTime)
                        self.checkered_flag_shown = True
                    else:
                        self.log("FINISH", f"Checkered Flag! {name} comes past the line to take P{pos}.", sim_time=self.data.mCurrentTime)
                    self.finished_participants.add(name)
                
                # Other terminal states
                if race_state != old_race_state and name not in self.finished_participants:
                    if race_state == 4: # Disqualified
                        self.log("SESSION", f"{name} has been disqualified.", sim_time=self.data.mCurrentTime)
                        self.finished_participants.add(name)
                    elif race_state == 5: # Retired
                        self.log("SESSION", f"{name} has retired from the race.", sim_time=self.data.mCurrentTime)
                        self.finished_participants.add(name)
                    elif race_state == 6: # DNF
                        self.log("SESSION", f"{name} is DNF.", sim_time=self.data.mCurrentTime)
                        self.finished_participants.add(name)
                
                self.participant_race_states[name] = race_state
                new_laps_completed[name] = laps_completed

                # Pit Detection (only if not finished)
                if name not in self.finished_participants:
                    old_pit_mode = self.participant_pit_modes.get(name, 0)
                    if pit_mode != old_pit_mode:
                        if pit_mode == 1: # Driving into Pits
                            self.log("PIT", f"{name} has entered the pit lane.", sim_time=self.data.mCurrentTime)
                        elif pit_mode == 2: # In Pit Box
                            self.log("PIT", f"{name} has arrived at their pit box.", sim_time=self.data.mCurrentTime)
                        elif pit_mode == 3: # Driving out of Pits
                            self.log("PIT", f"{name} is leaving the pits.", sim_time=self.data.mCurrentTime)
                
                self.participant_pit_modes[name] = pit_mode
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

        # Check for disconnected players
        if self.last_positions and self.data.mSessionState == 5: # Only in Race
            disconnected = set(self.last_positions.keys()) - set(new_positions.keys())
            for name in disconnected:
                if name not in self.finished_participants:
                    self.log("SESSION", f"{name} has retired and left the session.", sim_time=self.data.mCurrentTime)
                    self.finished_participants.add(name)

        # Overtakes
        if self.last_positions:
            for name, pos in new_positions.items():
                # Ignore events for finished drivers or drivers in pits
                if name in self.finished_participants or self.participant_pit_modes.get(name, 0) != 0:
                    continue
                
                if name in self.last_positions:
                    old_pos = self.last_positions[name]
                    if pos < old_pos and old_pos != 0:
                        # Identify who was overtaken
                        overtaken = []
                        for other_name, other_old_pos in self.last_positions.items():
                            if other_name != name:
                                # Ignore finished drivers or drivers in pits
                                if other_name in self.finished_participants or self.participant_pit_modes.get(other_name, 0) != 0:
                                    continue
                                
                                other_new_pos = new_positions.get(other_name)
                                if other_new_pos and other_old_pos < old_pos and other_new_pos > pos:
                                    overtaken.append(other_name)
                        
                        if overtaken:
                            overtaken_str = ", ".join(overtaken)
                            class_name = self.participant_classes.get(name, "Unknown")
                            self.log("OVERTAKE", f"{name} overtook {overtaken_str} for P{pos} in {class_name}.", sim_time=self.data.mCurrentTime)
                            
                            # Track overtake time for suppression logic
                            self.last_overtake_time[name] = now_time
                            for o_name in overtaken:
                                self.last_overtake_time[o_name] = now_time

        # Accidents
        # COOLDOWN: No accidents for the first 20 seconds of the race
        accident_cooldown = self.race_start_time and (now_time - self.race_start_time < 20)
        
        for p in active_participants:
            name, pos, dist, speed, class_name, lap, pit = p.values()
            # Ignore accidents for finished drivers or drivers in pits
            if name in self.finished_participants or pit != 0:
                if name in self.accidents:
                    del self.accidents[name]
                continue

            if self.data.mRaceState == 2 and speed < 2.0 and dist > 10 and not accident_cooldown: # Stopped on track
                if name not in self.accidents:
                    self.accidents[name] = now_time
                    self.log("ACCIDENT", f"Alert: {name} (P{pos} in {class_name}) is slow/stopped on track! Potential accident.", sim_time=self.data.mCurrentTime)
            elif name in self.accidents:
                if speed > 5.0:
                    del self.accidents[name]
                    self.log("ACCIDENT", f"Notice: {name} is back on the move.", sim_time=self.data.mCurrentTime)

        # Battles
        # Sort by race position for easier battle detection
        sorted_participants = sorted(active_participants, key=lambda x: x["pos"])
        for i in range(len(sorted_participants) - 1):
            p1 = sorted_participants[i]
            p2 = sorted_participants[i+1]
            name1, pos1, dist1, speed1, class1, lap1, pit1 = p1.values()
            name2, pos2, dist2, speed2, class2, lap2, pit2 = p2.values()
            
            # Ignore drivers who have finished
            if name1 in self.finished_participants or name2 in self.finished_participants:
                continue

            # COOLDOWN: No battles for the first lap
            if lap1 <= 1 or lap2 <= 1:
                continue
            
            # Ignore cars in pits
            if pit1 != 0 or pit2 != 0:
                continue

            # Use lap distance to check proximity
            dist_diff = abs(dist1 - dist2)
            battle_key = tuple(sorted([name1, name2]))
            battle_info = self.battles.get(battle_key)
            
            # Check last overtake times for suppression
            last_ot1 = self.last_overtake_time.get(name1, 0)
            last_ot2 = self.last_overtake_time.get(name2, 0)
            since_ot = min(now_time - last_ot1, now_time - last_ot2)

            # Prevent flybys or post-overtake ghost battles
            # 1. Filter out if speed diff is too high (e.g. one has an issue, > 36 km/h diff)
            if abs(speed1 - speed2) > 10.0:
                continue
                
            # 2. Apply cooldown after an overtake to ALL battles (prevent battle message right after overtake)
            if since_ot < 20.0:
                continue

            # Tighten side-by-side threshold to 2.5m (approx one car length/overlap)
            if dist_diff < 2.5:
                # Use 15s cooldown
                sbs_cooldown = 15.0
                # If we were drafting and now side-by-side, we can log immediately (escalation)
                # But if we were already side-by-side, we must respect the cooldown
                if not battle_info or battle_info["type"] != "Side by side" or (now_time - battle_info["time"] > sbs_cooldown):
                    count = battle_info["count"] + 1 if (battle_info and battle_info["type"] == "Side by side") else 1
                    if count > 1:
                        self.log("BATTLE", f"{name2} (P{pos2}) and {name1} (P{pos1}) are still battling side by side!", sim_time=self.data.mCurrentTime)
                    else:
                        self.log("BATTLE", f"Side by side! {name2} (P{pos2}) is fighting {name1} (P{pos1})!", sim_time=self.data.mCurrentTime)
                    self.battles[battle_key] = {"type": "Side by side", "time": now_time, "count": count}
            elif dist_diff < 15.0:
                draft_cooldown = 15.0
                # Check if we are already in a battle of ANY type within the cooldown
                if not battle_info or (now_time - battle_info["time"] > draft_cooldown):
                    count = battle_info["count"] + 1 if (battle_info and battle_info["type"] == "Drafting") else 1
                    # Determine who is drafting whom
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
                        f"{drafter} (P{d_pos}) is refusing to let {leader} (P{l_pos}) escape."
                    ]
                    self.log("BATTLE", random.choice(messages), sim_time=self.data.mCurrentTime)
                    self.battles[battle_key] = {"type": "Drafting", "time": now_time, "count": count}
            else:
                # Do NOT clear the battle immediately if they spread out.
                # This ensures the cooldown persists even if they "flicker" in and out of 15m.
                # The battle will eventually be overwritten or time out via the cooldown checks above.
                pass

        self.last_positions = new_positions
        self.last_lap_distances = new_lap_distances
        self.last_laps_completed = new_laps_completed

    def log_periodic_leaderboard(self):
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
        self.log("LEADERBOARD", f"Current Standings: {leaderboard_str}", sim_time=self.data.mCurrentTime)

    def log_starting_grid(self):
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
                grid.append((pos, name, car))
        
        grid.sort(key=lambda x: x[0])
        grid_str = " | ".join([f"P{pos}: {name} [{car}]" for pos, name, car in grid])
        self.log("LEADERBOARD", f"Starting Grid Order: {grid_str}", sim_time=self.data.mCurrentTime)

    def run(self):
        if not self.connect():
            return
        
        self.start_logging()
        try:
            while True:
                self.update()
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.log("SYSTEM", "Logging stopped by user.")
        finally:
            if self.log_file:
                self.log_file.close()

if __name__ == "__main__":
    logger = AMS2EventLogger()
    logger.run()
