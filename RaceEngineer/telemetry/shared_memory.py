"""
AMS2 / Project CARS 2 Shared Memory CTypes Interface.
Reads shared memory mapped at '$pcars2$' (or '$pcars$').
"""

import mmap
import ctypes
from enum import IntEnum
from typing import Optional

# Constants
STRING_LENGTH_MAX = 64
STORED_PARTICIPANTS_MAX = 64
TYRE_COMPOUND_NAME_LENGTH_MAX = 40
TYRE_MAX = 4
VEC_MAX = 3

# Tyre indices
TYRE_FRONT_LEFT = 0
TYRE_FRONT_RIGHT = 1
TYRE_REAR_LEFT = 2
TYRE_REAR_RIGHT = 3

# Vector indices
VEC_X = 0
VEC_Y = 1
VEC_Z = 2


class GameState(IntEnum):
    GAME_EXITED = 0
    GAME_FRONT_END = 1
    GAME_INGAME_PLAYING = 2
    GAME_INGAME_PAUSED = 3
    GAME_INGAME_INMENU_TIME_TICKING = 4
    GAME_INGAME_RESTARTING = 5
    GAME_INGAME_REPLAY = 6
    GAME_FRONT_END_REPLAY = 7


class SessionState(IntEnum):
    SESSION_INVALID = 0
    SESSION_PRACTICE = 1
    SESSION_TEST = 2
    SESSION_QUALIFY = 3
    SESSION_FORMATION_LAP = 4
    SESSION_RACE = 5
    SESSION_TIME_ATTACK = 6


class RaceState(IntEnum):
    RACESTATE_INVALID = 0
    RACESTATE_NOT_STARTED = 1
    RACESTATE_RACING = 2
    RACESTATE_FINISHED = 3
    RACESTATE_DISQUALIFIED = 4
    RACESTATE_RETIRED = 5
    RACESTATE_DNF = 6


class FlagColour(IntEnum):
    FLAG_NONE = 0
    FLAG_GREEN = 1
    FLAG_BLUE = 2
    FLAG_WHITE_SLOW_CAR = 3
    FLAG_WHITE_FINAL_LAP = 4
    FLAG_WHITE = 4  # Alias for backward compatibility
    FLAG_RED = 5
    FLAG_YELLOW = 6
    FLAG_DOUBLE_YELLOW = 7
    FLAG_BLACK_AND_WHITE = 8
    FLAG_BLACK_ORANGE_CIRCLE = 9
    FLAG_BLACK = 10
    FLAG_CHEQUERED = 11


class PitMode(IntEnum):
    PIT_MODE_NONE = 0
    PIT_MODE_DRIVING_INTO_PITS = 1
    PIT_MODE_IN_PIT = 2
    PIT_MODE_DRIVING_OUT_OF_PITS = 3
    PIT_MODE_IN_GARAGE = 4


class ParticipantInfo(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mIsActive", ctypes.c_bool),
        ("mName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mWorldPosition", ctypes.c_float * VEC_MAX),
        ("mCurrentLapDistance", ctypes.c_float),
        ("mRacePosition", ctypes.c_uint32),
        ("mLapsCompleted", ctypes.c_uint32),
        ("mCurrentLap", ctypes.c_uint32),
        ("mCurrentSector", ctypes.c_int32),
    ]


class AMS2SharedMemory(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        # Version Number
        ("mVersion", ctypes.c_uint32),
        ("mBuildVersionNumber", ctypes.c_uint32),

        # Game States
        ("mGameState", ctypes.c_uint32),
        ("mSessionState", ctypes.c_uint32),
        ("mRaceState", ctypes.c_uint32),

        # Participant Info
        ("mViewedParticipantIndex", ctypes.c_int32),
        ("mNumParticipants", ctypes.c_int32),
        ("mParticipantInfo", ParticipantInfo * STORED_PARTICIPANTS_MAX),

        # Unfiltered Input
        ("mUnfilteredThrottle", ctypes.c_float),
        ("mUnfilteredBrake", ctypes.c_float),
        ("mUnfilteredSteering", ctypes.c_float),
        ("mUnfilteredClutch", ctypes.c_float),

        # Vehicle information
        ("mCarName", ctypes.c_char * STRING_LENGTH_MAX),
        ("mCarClassName", ctypes.c_char * STRING_LENGTH_MAX),

        # Event information
        ("mLapsInEvent", ctypes.c_uint32),
        ("mTrackLocation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTrackVariation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTrackLength", ctypes.c_float),

        # Timings
        ("mNumSectors", ctypes.c_int32),
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

        # Flags
        ("mHighestFlagColour", ctypes.c_uint32),
        ("mHighestFlagReason", ctypes.c_uint32),

        # Pit Info
        ("mPitMode", ctypes.c_uint32),
        ("mPitSchedule", ctypes.c_uint32),

        # Car State
        ("mCarFlags", ctypes.c_uint32),
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
        ("mGear", ctypes.c_int32),
        ("mNumGears", ctypes.c_int32),
        ("mOdometerKM", ctypes.c_float),
        ("mAntiLockActive", ctypes.c_bool),
        ("mLastOpponentCollisionIndex", ctypes.c_int32),
        ("mLastOpponentCollisionMagnitude", ctypes.c_float),
        ("mBoostActive", ctypes.c_bool),
        ("mBoostAmount", ctypes.c_float),

        # Motion & Device Related (VECTORS COME BEFORE TYRES IN C++)
        ("mOrientation", ctypes.c_float * VEC_MAX),
        ("mLocalVelocity", ctypes.c_float * VEC_MAX),
        ("mWorldVelocity", ctypes.c_float * VEC_MAX),
        ("mAngularVelocity", ctypes.c_float * VEC_MAX),
        ("mLocalAcceleration", ctypes.c_float * VEC_MAX),
        ("mWorldAcceleration", ctypes.c_float * VEC_MAX),
        ("mExtentsCentre", ctypes.c_float * VEC_MAX),

        # Wheels / Tyres
        ("mTyreFlags", ctypes.c_uint32 * TYRE_MAX),
        ("mTerrain", ctypes.c_uint32 * TYRE_MAX),
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

        # Car Damage
        ("mCrashState", ctypes.c_uint32),
        ("mAeroDamage", ctypes.c_float),
        ("mEngineDamage", ctypes.c_float),

        # Weather
        ("mAmbientTemperature", ctypes.c_float),
        ("mTrackTemperature", ctypes.c_float),
        ("mRainDensity", ctypes.c_float),
        ("mWindSpeed", ctypes.c_float),
        ("mWindDirectionX", ctypes.c_float),
        ("mWindDirectionY", ctypes.c_float),
        ("mCloudBrightness", ctypes.c_float),

        # PCars2 additions start, version 8
        ("mSequenceNumber", ctypes.c_uint32),
        ("mWheelLocalPositionY", ctypes.c_float * TYRE_MAX),
        ("mSuspensionTravel", ctypes.c_float * TYRE_MAX),
        ("mSuspensionVelocity", ctypes.c_float * TYRE_MAX),
        ("mAirPressure", ctypes.c_float * TYRE_MAX),
        ("mEngineSpeed", ctypes.c_float),
        ("mEngineTorque", ctypes.c_float),
        ("mWings", ctypes.c_float * 2),
        ("mHandBrake", ctypes.c_float),

        # Additional race variables
        ("mCurrentSector1Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCurrentSector2Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCurrentSector3Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector1Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector2Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestSector3Times", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mFastestLapTimes", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mLastLapTimes", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mLapsInvalidated", ctypes.c_bool * STORED_PARTICIPANTS_MAX),
        ("mRaceStates", ctypes.c_uint32 * STORED_PARTICIPANTS_MAX),
        ("mPitModes", ctypes.c_uint32 * STORED_PARTICIPANTS_MAX),
        ("mOrientations", (ctypes.c_float * VEC_MAX) * STORED_PARTICIPANTS_MAX),
        ("mSpeeds", ctypes.c_float * STORED_PARTICIPANTS_MAX),
        ("mCarNames", (ctypes.c_char * STRING_LENGTH_MAX) * STORED_PARTICIPANTS_MAX),
        ("mCarClassNames", (ctypes.c_char * STRING_LENGTH_MAX) * STORED_PARTICIPANTS_MAX),
        ("mEnforcedPitStopLap", ctypes.c_int32),
        ("mTranslatedTrackLocation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mTranslatedTrackVariation", ctypes.c_char * STRING_LENGTH_MAX),
        ("mBrakeBias", ctypes.c_float),
        ("mTurboBoostPressure", ctypes.c_float),
        ("mTyreCompound", (ctypes.c_char * TYRE_COMPOUND_NAME_LENGTH_MAX) * TYRE_MAX),
        ("mPitSchedules", ctypes.c_uint32 * STORED_PARTICIPANTS_MAX),
        ("mHighestFlagColours", ctypes.c_uint32 * STORED_PARTICIPANTS_MAX),
        ("mHighestFlagReasons", ctypes.c_uint32 * STORED_PARTICIPANTS_MAX),
        ("mNationalities", ctypes.c_uint32 * STORED_PARTICIPANTS_MAX),
        ("mSnowDensity", ctypes.c_float),

        # AMS2 Additions (v10...)
        ("mSessionDuration", ctypes.c_float),
        ("mSessionAdditionalLaps", ctypes.c_int32),
        ("mTyreTempLeft", ctypes.c_float * TYRE_MAX),
        ("mTyreTempCenter", ctypes.c_float * TYRE_MAX),
        ("mTyreTempRight", ctypes.c_float * TYRE_MAX),
        ("mDrsState", ctypes.c_uint32),
        ("mRideHeight", ctypes.c_float * TYRE_MAX),
        ("mJoyPad0", ctypes.c_uint32),
        ("mDPad", ctypes.c_uint32),
        ("mAntiLockSetting", ctypes.c_int32),
        ("mTractionControlSetting", ctypes.c_int32),
        ("mErsDeploymentMode", ctypes.c_int32),
        ("mErsAutoModeEnabled", ctypes.c_bool),
        ("mClutchTemp", ctypes.c_float),
        ("mClutchWear", ctypes.c_float),
        ("mClutchOverheated", ctypes.c_bool),
        ("mClutchSlipping", ctypes.c_bool),
        ("mYellowFlagState", ctypes.c_int32),
        ("mSessionIsPrivate", ctypes.c_bool),
        ("mLaunchStage", ctypes.c_int32),
    ]


class AMS2TelemetryReader:
    """Safely opens and reads AMS2 shared memory via Windows mmap."""

    def __init__(self, tag_name: str = "$pcars2$"):
        self.tag_name = tag_name
        self.shm: Optional[mmap.mmap] = None
        self.data_copy = AMS2SharedMemory()
        self._struct_size = ctypes.sizeof(AMS2SharedMemory)

    def is_connected(self) -> bool:
        return self.shm is not None

    def connect(self) -> bool:
        """Attempts to open the named shared memory block."""
        if self.shm is not None:
            return True

        for tag in [self.tag_name, "$pcars$", "Local\\$pcars2$", "Local\\$pcars$"]:
            try:
                self.shm = mmap.mmap(
                    -1,
                    self._struct_size,
                    tagname=tag,
                    access=mmap.ACCESS_READ
                )
                self.tag_name = tag
                return True
            except (FileNotFoundError, OSError, PermissionError):
                continue

        return False

    def disconnect(self):
        """Closes the shared memory mapping."""
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None

    def read_snapshot(self) -> Optional[AMS2SharedMemory]:
        """
        Reads a consistent frame from shared memory into a local struct copy.
        Returns None if shared memory is not mapped or data is invalid.
        """
        if not self.is_connected():
            if not self.connect():
                return None

        try:
            self.shm.seek(0)
            raw_bytes = self.shm.read(self._struct_size)
            if len(raw_bytes) < self._struct_size:
                return None

            ctypes.memmove(
                ctypes.byref(self.data_copy),
                raw_bytes,
                self._struct_size
            )

            # Check if game wrote a valid header version
            if self.data_copy.mVersion > 0:
                # If game explicitly exited, disconnect to release mmap handle and await restart
                if self.data_copy.mGameState == GameState.GAME_EXITED:
                    self.disconnect()
                    return None
                return self.data_copy
            return None

        except (ValueError, OSError):
            self.disconnect()
            return None

    @staticmethod
    def decode_str(byte_arr) -> str:
        """Safely decodes ctypes char array to clean Python string."""
        if isinstance(byte_arr, bytes):
            raw = byte_arr
        elif hasattr(byte_arr, "value"):
            raw = byte_arr.value
        else:
            raw = bytes(byte_arr)
        return raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()
