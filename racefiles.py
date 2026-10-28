"""Races and their files: one folder per recorded race, with the same file names in every folder.

The loggers record each race into Races/<date time track>/ and every pipeline step reads and writes
the fixed names below, so the launcher always knows which file is which without scanning or guessing.
"""
import datetime
import json
import os
import re
import shutil

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RACES_DIR = os.environ.get("BADAI_RACES_DIR") or os.path.join(APP_DIR, "Races")
ARCHIVE_DIR_NAME = "_Archive"

# Races shorter than this (green flag to the last event) are not kept
MIN_RACE_SECONDS = 300

RACE_JSON = "race.json"
HISTORY_DIR = "history"
DEBUG_DIR = "debug"

# Every file a race folder can hold, in pipeline order
FILES = {
    "race_notes": "race_notes.txt",
    "camera_notes": "camera_notes.txt",
    "race_log": "01_race_log.txt",
    "timing": "01_race_log_timing.jsonl",
    "camera_sequence": "02_camera_sequence.json",
    "director_notes": "02_director_notes.txt",
    "combined_log": "03_combined_log.txt",
    "events": "04_events.txt",
    "events_reviewed": "05_events_reviewed.txt",
    "events_spiced": "06_events_spiced.txt",
    "events_timed": "07_events_timed.txt",
    "commentary": "08_commentary.txt",
    "audio_clips": "09_audio_clips",
    "commentary_audio": "10_commentary_audio.wav",
    "graphics_config": "11_graphics_setup.json",
    "graphics_plan": "11_graphics_plan.json",
    "final_video": "12_final_video.mp4",
}

RACE_NOTES_TEMPLATE = (
    "# Race notes - read by the AI when it writes the events, commentary, camera plan and graphics.\n"
    "# Lines starting with # are ignored. Examples:\n"
    "# This is round 3 of the RCV Caterham championship. Daniel Born leads the points.\n"
    "# [RCV]thommo is the defending champion. Pronounce 'Neworder' as 'New Order'.\n"
)
CAMERA_NOTES_TEMPLATE = (
    "# Camera notes - read only by the camera director AI.\n"
    "# Lines starting with # are ignored. Example:\n"
    "# Focus on car #24 whenever it is battling for position.\n"
)

STATUS_PREFIX = "##BADAI "


def race_file(folder, key):
    return os.path.join(folder, FILES[key])


def notes_text(path):
    """A notes file without its # comment lines, or "" when there is none."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#")).strip()
    except OSError:
        return ""


def emit_status(**fields):
    """A status line for the launcher (shown on its live screen, hidden from the activity log)."""
    print(STATUS_PREFIX + json.dumps(fields, ensure_ascii=False), flush=True)


def safe_name(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text or "")
    return re.sub(r"\s+", " ", text).strip(" .") or "Race"


def unique_folder(path):
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists(f"{path} ({n})"):
        n += 1
    return f"{path} ({n})"


def folder_name(started, track, car_class=""):
    name = f"{started:%Y-%m-%d %H.%M} {safe_name(track.replace('_', ' '))}"
    return f"{name} - {safe_name(car_class)}" if car_class else name


# ---------------------------------------------------------------- race.json

def read_race(folder):
    try:
        with open(os.path.join(folder, RACE_JSON), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def update_race(folder, **fields):
    data = read_race(folder)
    data.update(fields)
    with open(os.path.join(folder, RACE_JSON), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


# ---------------------------------------------------------------- reading a race log

LINE_RE = re.compile(r"^\s*(\d+):(\d{2}):(\d{2})\s*-\s*\[(\w+)\]\s*(.*)$")


def parse_seconds(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


def summarize_log(path):
    """Track, length, grid and timing of a race log, for the race library."""
    info = {"track": "", "track_length_m": None, "race_length": "", "drivers": 0, "cars": [],
            "classes": [], "winner": "", "green_at": None, "last_at": 0, "duration_sec": 0, "events": 0}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return info
    for line in lines:
        m = LINE_RE.match(line)
        if not m:
            continue
        t = parse_seconds(*m.group(1, 2, 3))
        cat, msg = m.group(4), m.group(5).strip()
        info["events"] += 1
        info["last_at"] = max(info["last_at"], t)
        if cat == "TRACK" and msg.startswith("Track Loaded:"):
            info["track"] = msg.split(":", 1)[1].strip()
        elif cat == "TRACK" and msg.startswith("Track Length:"):
            num = re.search(r"([\d.]+)", msg)
            info["track_length_m"] = float(num.group(1)) if num else None
        elif cat == "SESSION" and msg.startswith(("Race Distance:", "Race Duration:")):
            info["race_length"] = msg.split(":", 1)[1].strip().rstrip(".")
        elif cat == "SESSION" and msg.startswith("Green Flag") and info["green_at"] is None:
            info["green_at"] = t
        elif cat == "LEADERBOARD" and msg.startswith("Starting Grid Order:"):
            entries = [e.split(":", 1)[1].strip() for e in msg.split(":", 1)[1].split(" | ") if ":" in e]
            info["drivers"] = len(entries)
            cars = [e.rsplit(" [", 1)[1].rstrip("]") for e in entries if e.endswith("]") and " [" in e]
            cars = [c for c in cars if c and c != "Unknown Car"]
            info["cars"] = sorted(set(cars), key=cars.index)
        elif cat == "FINISH" and not info["winner"]:
            won = re.search(r"Checkered Flag! (.+?) took P1", msg)
            if won:
                info["winner"] = won.group(1)
        elif cat == "OVERTAKE" and not info["classes"]:
            cls = re.search(r" for P\d+ in (.+?)(?: at |\.$)", msg)
            if cls:
                info["classes"] = [cls.group(1)]
    info["duration_sec"] = info["last_at"] - (info["green_at"] or 0)
    return info


def guess_sim(path):
    """Which sim wrote a log, from the way each logger words its lines (None when it can't tell)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(20000)
    except OSError:
        return None
    if "RaceRoom" in head or "No corner landmarks found for layout" in head:
        return "RaceRoom"
    if re.search(r"corner landmarks for .+\(.+\.json", head) or "No corner landmark file found" in head:
        return "AMS2"
    track = re.search(r"\[TRACK\] Track Loaded: .+ - (\S+)$", head, re.M)
    if track and "_" in track.group(1):
        return "AMS2"  # AMS2 layout names look like Interlagos_SCB
    return None


def race_label(summary):
    """What was raced: the car when everyone drove the same one, else the class."""
    cars = summary.get("cars") or []
    if len(cars) == 1:
        return cars[0]
    return (summary.get("classes") or [""])[0].replace("_", " ")


# ---------------------------------------------------------------- recording a race (used by the loggers)

class RaceRecording:
    """One race being logged live. finish() keeps it only when it ran for MIN_RACE_SECONDS."""

    def __init__(self, sim, track):
        self.sim = sim
        self.track = track
        self.started = datetime.datetime.now()
        os.makedirs(RACES_DIR, exist_ok=True)
        self.folder = unique_folder(os.path.join(RACES_DIR, folder_name(self.started, track)))
        os.makedirs(self.folder)
        self.log_path = race_file(self.folder, "race_log")
        self.file = open(self.log_path, "w", encoding="utf-8")
        self.green_at = None
        self.last_at = 0
        update_race(self.folder, sim=sim, track=track, started_at=self.started.isoformat(timespec="seconds"),
                    status="recording")
        emit_status(state="recording", sim=sim, track=track, folder=self.folder)

    def write(self, line, seconds):
        if not self.file:
            return
        self.file.write(line + "\n")
        self.file.flush()
        self.last_at = max(self.last_at, seconds)
        if self.green_at is None and "Green Flag!" in line:
            self.green_at = seconds

    @property
    def duration(self):
        return self.last_at - (self.green_at or 0)

    def finish(self):
        """Closes the race. Returns the folder it was kept in, or None when it was too short to keep."""
        if not self.file:
            return None
        self.file.close()
        self.file = None
        if self.green_at is None or self.duration < MIN_RACE_SECONDS:
            shutil.rmtree(self.folder, ignore_errors=True)
            emit_status(state="discarded", track=self.track, seconds=self.duration if self.green_at is not None else 0)
            why = (f"ran {self.duration // 60} min - shorter than {MIN_RACE_SECONDS // 60} min"
                   if self.green_at is not None else "never reached the green flag")
            print(f"Race at {self.track} {why}, so it was not kept.", flush=True)
            return None

        summary = summarize_log(self.log_path)
        folder = self.folder
        car_class = race_label(summary)
        if car_class:
            target = unique_folder(os.path.join(RACES_DIR, folder_name(self.started, self.track, car_class)))
            try:
                os.rename(folder, target)
                folder = target
            except OSError:
                pass
        update_race(folder, status="recorded", ended_at=datetime.datetime.now().isoformat(timespec="seconds"),
                    summary=summary)
        emit_status(state="saved", track=self.track, folder=folder, seconds=self.duration)
        print(f"Race saved: {folder}", flush=True)
        return folder


def watch_for_stop(stop_event):
    """When the launcher runs a logger, it asks it to stop by writing 'stop' (or closing stdin)."""
    if not os.environ.get("BADAI_MANAGED"):
        return
    import sys
    import threading

    def wait():
        try:
            for line in sys.stdin:
                if line.strip().lower() == "stop":
                    break
        except (OSError, ValueError):
            pass
        stop_event.set()

    threading.Thread(target=wait, name="StopWatcher", daemon=True).start()


# ---------------------------------------------------------------- the race library

def list_races(include_archived=False):
    """Every race folder, newest first: {'folder', 'name', 'meta', 'summary'}."""
    roots = [RACES_DIR] + ([os.path.join(RACES_DIR, ARCHIVE_DIR_NAME)] if include_archived else [])
    races = []
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            folder = os.path.join(root, name)
            if name == ARCHIVE_DIR_NAME or not os.path.isfile(race_file(folder, "race_log")):
                continue
            meta = read_race(folder)
            summary = meta.get("summary")
            if not summary:
                summary = summarize_log(race_file(folder, "race_log"))
                try:
                    meta = update_race(folder, summary=summary)
                except OSError:
                    pass
            races.append({"folder": folder, "name": name, "meta": meta, "summary": summary,
                          "archived": root != RACES_DIR})
    races.sort(key=lambda r: r["meta"].get("started_at") or r["name"], reverse=True)
    return races


def save_to_history(path, keep=5):
    """Copies a file (or folder) into the race's history/ folder before a step overwrites it."""
    if not os.path.exists(path):
        return
    folder = os.path.dirname(path)
    history = os.path.join(folder, HISTORY_DIR)
    os.makedirs(history, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(path))
    stamp = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H.%M.%S")
    target = os.path.join(history, f"{base} ({stamp}){ext}")
    if os.path.exists(target):
        return
    if os.path.isdir(path):
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target)
    old = sorted(f for f in os.listdir(history) if f.startswith(base + " ("))
    for f in old[:-keep]:
        p = os.path.join(history, f)
        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
