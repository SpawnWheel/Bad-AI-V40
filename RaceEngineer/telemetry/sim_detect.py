"""
Which supported sim is running? Looks at the Windows process list (no extra dependencies).

Shared memory alone can't answer this: a mapping stays alive (with stale data) while any other app
- CrewChief, SimHub, a dash - still has it open after the game has closed.
"""

import ctypes
import os
import sys
from typing import Dict, Optional, Set

SIM_AMS2 = "AMS2"
SIM_R3E = "R3E"

SIM_DISPLAY_NAMES = {
    SIM_AMS2: "Automobilista 2",
    SIM_R3E: "RaceRoom",
}

SIM_PROCESSES = {
    SIM_AMS2: ("ams2avx.exe", "ams2.exe"),
    SIM_R3E: ("rrre64.exe", "rrre.exe"),
}

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


def normalize_sim(name: Optional[str]) -> str:
    """Config value -> SIM_* constant, or "auto"."""
    n = (name or "auto").strip().lower().replace(" ", "").replace("-", "")
    if n in ("ams2", "automobilista", "automobilista2", "pcars2", "pcars"):
        return SIM_AMS2
    if n in ("r3e", "raceroom", "rrre", "raceroomracingexperience"):
        return SIM_R3E
    return "auto"


def list_processes() -> Optional[Dict[str, int]]:
    """Lower-case exe name -> pid of running processes. None if the process list can't be read."""
    if sys.platform != "win32":
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return None
        procs: Dict[str, int] = {}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                procs[entry.szExeFile.lower()] = int(entry.th32ProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return procs
    except Exception:
        return None


def running_sims(procs: Optional[Dict[str, int]] = None) -> Optional[Set[str]]:
    """Set of SIM_* constants whose game process is running. None if unknown."""
    procs = list_processes() if procs is None else procs
    if procs is None:
        return None
    return {sim for sim, exes in SIM_PROCESSES.items() if any(e in procs for e in exes)}


def process_path(pid: int) -> Optional[str]:
    """Full path of a running process' executable (used to find RaceRoom's install folder)."""
    if sys.platform != "win32" or not pid:
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_uint32(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return None


def sim_exe_path(sim: str) -> Optional[str]:
    procs = list_processes() or {}
    for exe in SIM_PROCESSES.get(sim, ()):
        if exe in procs:
            path = process_path(procs[exe])
            if path and os.path.exists(path):
                return path
    return None
