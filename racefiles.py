"""Races and their files: one folder per recorded race, with the same file names in every folder.

The loggers record each race into Races/<date time track>/ and every pipeline step reads and writes
the fixed names below, so the launcher always knows which file is which without scanning or guessing.
"""
import datetime
import json
import os
import re
import shutil
import time

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
    "graphics_notes": "graphics_notes.txt",
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
    "qualifying": "00_qualifying.json",
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


def graphics_notes_block(folder):
    """The graphics notes as a prompt section for the pop-up graphics AI, or "" when there are none."""
    notes = notes_text(race_file(folder, "graphics_notes"))
    if not notes:
        return ""
    return ("\n\nGRAPHICS NOTES (background facts from the producer for the pop-ups):\n"
            "Pick the ones that fit a moment in the race and use them in the words of a pop-up, e.g. a driver_card, "
            "headline or insight, shown when that driver is on camera or the commentators touch on that story. "
            "You don't have to use them all; use each one at most once, and keep its facts as written.\n"
            f"{notes}")


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


# ---------------------------------------------------------------- championship
# Every race says which championship it belongs to (chosen on the race page before any step runs), so the graphics
# and anything learned about the drivers (their flags) go to that championship and no other. An invitational or
# special event belongs to none: it starts with a blank slate and keeps what it learns to itself.
INVITATIONAL = "__invitational__"
INVITATIONAL_LABEL = "Invitational / special event"


def race_championship(folder):
    """The championship name chosen for the race, INVITATIONAL, or '' when none has been chosen yet."""
    return str(read_race(folder).get("championship") or "") if folder else ""


def set_race_championship(folder, name):
    update_race(folder, championship=name or "")


# Notes that belong to the championship rather than one race (the drivers, rivalries, standings, running jokes),
# written once and carried over to every race in it. An invitational has none.
CHAMPIONSHIP_NOTES_DIR = os.path.join(APP_DIR, "Graphics", "Championships", "Notes")


def championship_notes_path(name):
    """The notes file of a championship, or '' for an invitational or no championship."""
    if not name or name == INVITATIONAL:
        return ""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "championship"
    return os.path.join(CHAMPIONSHIP_NOTES_DIR, f"{slug}.txt")


def notes_for_ai(folder):
    """The race's championship notes and race notes, as one block for the AI steps, or "" when there are none."""
    champ = race_championship(folder)
    parts = []
    champ_notes = notes_text(championship_notes_path(champ)) if championship_notes_path(champ) else ""
    if champ_notes:
        parts.append(f"Championship notes ({champ}, carried over from race to race):\n{champ_notes}")
    race_notes = notes_text(race_file(folder, "race_notes"))
    if race_notes:
        parts.append(f"Race notes (this race):\n{race_notes}" if champ_notes else race_notes)
    return "\n\n".join(parts)


# ---------------------------------------------------------------- grid intro
# Optional pre-race intro: the timing step adds a [GRID] event GRID seconds before the green flag, so the
# commentators talk over a full-screen starting grid. The export holds one frame of the replay under it
# (the recording before the start is the sim's menus), and an optional music track plays underneath.
# Everything stays in video time, so no other event, clip or graphic moves.

GRID_TAG = "[GRID]"
MUSIC_DIR = os.path.join(APP_DIR, "Music")
MUSIC_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")
GRID_INTRO_DEFAULTS = {
    "enabled": False,
    "seconds": 10,        # commentary over the grid, before the green flag
    "hold_lead": 2.0,     # the replay starts playing this long before the green flag (the held frame)
    "music": "",          # file name in Music/
    "music_level": "Normal",
}
GRID_SECONDS_RANGE = (5, 30)
STAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\s*-\s*")


def grid_intro_settings(folder):
    """The race's grid intro settings (race.json "grid_intro") over the defaults."""
    data = read_race(folder).get("grid_intro") or {}
    s = {**GRID_INTRO_DEFAULTS, **{k: v for k, v in data.items() if k in GRID_INTRO_DEFAULTS}}
    try:
        s["seconds"] = max(GRID_SECONDS_RANGE[0], min(GRID_SECONDS_RANGE[1], int(s["seconds"])))
        s["hold_lead"] = max(0.0, min(10.0, float(s["hold_lead"])))
    except (TypeError, ValueError):
        s["seconds"], s["hold_lead"] = GRID_INTRO_DEFAULTS["seconds"], GRID_INTRO_DEFAULTS["hold_lead"]
    return s


def grid_music_path(settings):
    name = (settings.get("music") or "").strip()
    path = os.path.join(MUSIC_DIR, name) if name else ""
    return path if path and os.path.isfile(path) else ""


def list_music():
    try:
        return sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(MUSIC_EXTS))
    except OSError:
        return []


def stamp_seconds(line):
    m = STAMP_RE.match(line)
    return parse_seconds(*m.groups()) if m else None


def is_green_flag_line(line):
    return GRID_TAG not in line and "green flag" in line.lower()


def green_flag_time(folder):
    """Video time of the green flag in the latest event list (reviewed or later), or None."""
    for key in ("events_timed", "events_spiced", "events_reviewed", "events"):
        try:
            with open(race_file(folder, key), "r", encoding="utf-8") as f:
                for line in f:
                    t = stamp_seconds(line)
                    if t is not None and is_green_flag_line(line):
                        return t
        except OSError:
            continue
    return None


def grid_intro_in_events(folder):
    """(grid event time, green flag time) as written in the timed events, or None without a grid intro."""
    grid = green = None
    try:
        with open(race_file(folder, "events_timed"), "r", encoding="utf-8") as f:
            for line in f:
                t = stamp_seconds(line)
                if t is None:
                    continue
                if grid is None and GRID_TAG in line:
                    grid = t
                elif green is None and is_green_flag_line(line):
                    green = t
    except OSError:
        return None
    return (grid, green) if grid is not None and green is not None else None


# ---------------------------------------------------------------- qualifying times
# Every driver's best lap from the qualifying session before the race, shown on the starting grid and given to the
# grid commentary. The recorders save them as qualifying runs (QualifyingWatch); a race recorded afterwards on the
# same track takes them into its folder. Results can also be pasted in (parse_pasted_qualifying). Without any,
# the grid simply shows no times.

QUALI_PENDING = os.path.join(RACES_DIR, "_qualifying_pending.json")
QUALI_MAX_AGE_SEC = 6 * 3600   # a race this long after the qualifying session still takes its times
LAP_TIME_RANGE = (10.0, 3600.0)


def qualifying_rows(best):
    """[{'name', 'time', 'pos'}] from {name: best lap seconds}, fastest first."""
    rows = sorted(((n, t) for n, t in best.items() if t), key=lambda r: r[1])
    return [{"name": n, "time": round(t, 3), "pos": i + 1} for i, (n, t) in enumerate(rows)]


class QualifyingWatch:
    """Each driver's best lap during one qualifying session, saved (every few seconds while it changes) to
    QUALI_PENDING, so the race that follows takes them even if the recorder restarts in between."""

    SAVE_EVERY = 3.0

    def __init__(self, sim, track):
        self.sim, self.track = sim, track
        self.best = {}
        self.dirty = False
        self.saved_at = 0.0

    def update(self, laps):
        """laps: (driver name, best lap seconds so far); a time of 0 or less means no lap yet."""
        for name, t in laps:
            if name and t and LAP_TIME_RANGE[0] < t < LAP_TIME_RANGE[1] and t < self.best.get(name, 1e9) - 1e-4:
                self.best[name] = t
                self.dirty = True
        if self.dirty and time.time() - self.saved_at >= self.SAVE_EVERY:
            self.save()

    def save(self):
        if not self.best:
            return
        data = {"source": "recorded", "sim": self.sim, "track": self.track,
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "drivers": qualifying_rows(self.best)}
        try:
            os.makedirs(RACES_DIR, exist_ok=True)
            with open(QUALI_PENDING, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.dirty, self.saved_at = False, time.time()
        except OSError as e:
            print(f"Qualifying times not saved: {e}", flush=True)

    def close(self):
        if self.dirty:
            self.save()
        if self.best:
            print(f"Qualifying: {len(self.best)} drivers set a time; the next race at {self.track} will use them.",
                  flush=True)


def attach_pending_qualifying(folder, sim, track):
    """Copies the latest qualifying times into a race that just started recording, when they are from the same
    sim and track and recent. They stay pending, so a restarted race takes them too. True when attached."""
    try:
        with open(QUALI_PENDING, "r", encoding="utf-8") as f:
            data = json.load(f)
        age = (datetime.datetime.now() - datetime.datetime.fromisoformat(data["updated_at"])).total_seconds()
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if data.get("sim") != sim or data.get("track") != track or not 0 <= age <= QUALI_MAX_AGE_SEC:
        return False
    try:
        with open(race_file(folder, "qualifying"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        return False
    print(f"Qualifying times from {data['updated_at'][11:16]} attached ({len(data.get('drivers', []))} drivers).",
          flush=True)
    return True


def read_qualifying(folder):
    """The race's qualifying data ({'source', 'drivers': [{'name', 'time', 'pos'}]}), or None without any."""
    try:
        with open(race_file(folder, "qualifying"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("drivers") else None
    except (OSError, ValueError):
        return None


def qualifying_times(folder):
    """{driver log name: best lap seconds} for the race, or {}."""
    data = read_qualifying(folder) or {}
    return {d["name"]: float(d["time"]) for d in data.get("drivers", []) if d.get("name") and d.get("time")}


def save_qualifying(folder, rows, source):
    path = race_file(folder, "qualifying")
    if not rows:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                   "drivers": rows}, f, indent=2, ensure_ascii=False)


def format_lap_time(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


def grid_names(folder):
    """Driver names in grid order from the race log's starting grid line (the names the whole pipeline uses)."""
    try:
        with open(race_file(folder, "race_log"), "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Starting Grid Order:" in line:
                    body = line.split("Starting Grid Order:", 1)[1]
                    names = []
                    for part in body.split("|"):
                        m = re.match(r"\s*(?:\[[^\]]*\]\s+)?P\d+:\s*(.+?)(?:\s\[[^\]]*\])?\s*$", part)
                        if m:
                            names.append(m.group(1).strip())
                    return names
    except OSError:
        pass
    return []


PASTE_TIME_RE = re.compile(r"(?<![\d:.])(\+\s*)?(?:(\d{1,2}):)?(\d{1,2})[.,](\d{3})(?!\d)")


def _norm_words(text):
    """Lower case words without team tags or punctuation: '[RCV]Dom.Payne' -> 'dom payne'."""
    text = re.sub(r"\[[^\]]*\]", " ", text or "")
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def parse_pasted_qualifying(text, aliases):
    """Reads pasted qualifying results (a results screen, a league site or a spreadsheet: one driver a line,
    with a lap time like 1:42.315 or a gap like +0.214). aliases: {log name: [names they may be written as]}.
    Returns (rows as qualifying_rows gives them, lines with a time but no driver matched)."""
    keys = []
    for name, names in aliases.items():
        for alias in set([name] + [a for a in names if a]):
            words = _norm_words(alias)
            compact = words.replace(" ", "")
            if len(compact) >= 3:
                keys.append((name, words, compact))
    found, unmatched = {}, []
    for line in (text or "").splitlines():
        times = PASTE_TIME_RE.findall(line)
        if not times:
            continue
        words = _norm_words(PASTE_TIME_RE.sub(" ", line))
        compact, padded = words.replace(" ", ""), f" {words} "
        best = None
        for name, kw, kc in keys:
            # Long names may be written without spaces or dots; short ones have to stand as words
            hit = (len(kc) >= 5 and kc in compact) or f" {kw} " in padded
            if hit and (best is None or len(kc) > best[1]):
                best = (name, len(kc))
        if not best:
            unmatched.append(line.strip())
            continue
        lap = gap = None
        for plus, mins, secs, ms in times:
            t = int(mins or 0) * 60 + int(secs) + int(ms) / 1000
            if plus or t < LAP_TIME_RANGE[0]:
                gap = t if gap is None else gap
            elif lap is None:
                lap = t
        found.setdefault(best[0], (lap, gap))
    laps = [lap for lap, _ in found.values() if lap]
    pole = min(laps) if laps else None
    best_times = {}
    for name, (lap, gap) in found.items():
        if lap:
            best_times[name] = lap
        elif gap is not None and pole is not None:
            best_times[name] = pole + gap
    return qualifying_rows(best_times), unmatched


def grid_hold_frame_path(folder, hold):
    """Where the frame held under the grid is saved (named by its time, so a new replay start grabs a new one)."""
    return os.path.join(folder, DEBUG_DIR, f"grid_hold_frame_{hold:.2f}s.png")


def grid_intro_window(folder):
    """Video times of the grid intro that the commentary was written for: {'start' (the grid event), 'green',
    'hold' (where the replay starts playing; the frame there is held before it)}, or None."""
    found = grid_intro_in_events(folder)
    if not found:
        return None
    grid, green = found
    lead = grid_intro_settings(folder)["hold_lead"]
    return {"start": float(grid), "green": float(green), "hold": max(float(grid) + 1.0, green - lead)}


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
        attach_pending_qualifying(self.folder, sim, track)
        emit_status(state="recording", sim=sim, track=track, folder=self.folder)

    def write(self, line, seconds):
        if not self.file:
            return
        self.file.write(line + "\n")
        self.file.flush()
        self.last_at = max(self.last_at, seconds)
        if self.green_at is None and "Green Flag!" in line:
            self.green_at = seconds

    def rewrite(self, lines):
        """Replaces everything logged so far (a logger correcting its log before the race is closed)."""
        if not self.file:
            return
        text = "".join(line + "\n" for line in lines)
        self.file.seek(0)
        self.file.truncate()
        self.file.write(text)
        self.file.flush()

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
