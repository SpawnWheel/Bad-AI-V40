"""
AMS2 Shared Memory Structures and Constants

This file contains a copy of the ctypes structures and constants needed to read
AMS2 shared memory. It is a shared copy for the live mode to avoid modifying
the original logger module.
"""
import ctypes

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

YELLOW_FLAG_STATES = {
    -1: "Invalid",
    0: "None",
    1: "Pending",
    2: "Pits Closed",
    3: "Pit Lead Lap",
    4: "Pits Open",
    5: "Pits Open",
    6: "Last Lap",
    7: "Resume",
    8: "Race Halt"
}

PIT_SCHEDULE_PENALTIES = {
    5: "Drive-Through",
    6: "Stop-Go",
    7: "Drive-Through (Pit Spot Occupied)"
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
        # AMS2 Additions (v10+)
        ("mSessionDuration", ctypes.c_float),
        ("mSessionAdditionalLaps", ctypes.c_int),
        ("mTyreTempLeft", ctypes.c_float * TYRE_MAX),
        ("mTyreTempCenter", ctypes.c_float * TYRE_MAX),
        ("mTyreTempRight", ctypes.c_float * TYRE_MAX),
        ("mDrsState", ctypes.c_uint),
        ("mRideHeight", ctypes.c_float * TYRE_MAX),
        ("mJoyPad0", ctypes.c_uint),
        ("mDPad", ctypes.c_uint),
        ("mAntiLockSetting", ctypes.c_int),
        ("mTractionControlSetting", ctypes.c_int),
        ("mErsDeploymentMode", ctypes.c_int),
        ("mErsAutoModeEnabled", ctypes.c_bool),
        ("mClutchTemp", ctypes.c_float),
        ("mClutchWear", ctypes.c_float),
        ("mClutchOverheated", ctypes.c_bool),
        ("mClutchSlipping", ctypes.c_bool),
        ("mYellowFlagState", ctypes.c_int),
    ]

# Windows Virtual Key Code for F12
VK_F12 = 0x7B

def format_sim_time(seconds: float) -> str:
    """Convert sim time in seconds to HH:MM:SS format."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
