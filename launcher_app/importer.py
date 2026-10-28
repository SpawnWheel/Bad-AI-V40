"""Brings projects from the old launcher (Projects/<name>/) into the race library.

Each old race log that ran for at least five minutes becomes a race folder. The log the project was
working on also brings its pipeline files, renamed to the fixed names. Files are hard-linked where the
drive allows (no extra space), else copied. The old Projects folder is never changed.
"""
import datetime
import glob
import json
import os
import re
import shutil

import racefiles
from racefiles import race_file

PROJECTS_DIR = os.path.join(racefiles.APP_DIR, "Projects")


def link_or_copy(src, dst):
    # Small files are copied, since the pipeline edits some in place; big media is hard-linked (never edited)
    if os.path.getsize(src) > 20 * 1024 * 1024:
        try:
            os.link(src, dst)
            return dst
        except OSError:
            pass
    shutil.copy2(src, dst)
    return dst


def imported_names():
    names = set()
    for race in racefiles.list_races(include_archived=True):
        if race["meta"].get("imported_from"):
            names.add(race["meta"]["imported_from"])
    return names


def pending_projects():
    """Old project folders that hold a race log and have not been imported yet."""
    try:
        names = sorted(os.listdir(PROJECTS_DIR))
    except OSError:
        return []
    done = imported_names()
    return [n for n in names if n not in done and _race_logs(os.path.join(PROJECTS_DIR, n))]


def _race_logs(project):
    """[(path, summary)] of the project's logs that hold a race of at least five minutes."""
    logs = []
    for path in glob.glob(os.path.join(project, "Logs", "Race_*.txt")):
        summary = racefiles.summarize_log(path)
        if summary["green_at"] is not None and summary["duration_sec"] >= racefiles.MIN_RACE_SECONDS:
            logs.append((path, summary))
    return logs


def _latest(paths):
    paths = [p for p in paths if os.path.exists(p)]
    return max(paths, key=os.path.getmtime) if paths else None


def _old_pipeline_files(project, state):
    """Old file for each new key, found by the old names (not by the old scan's guesses)."""
    def pick(pattern, exclude=()):
        files = [f for f in glob.glob(os.path.join(project, pattern))
                 if not any(x in os.path.basename(f) for x in exclude)]
        return _latest(files)

    found = {
        "director_notes": pick("director_notes_*.txt"),
        "camera_sequence": pick("camera_sequence_*.json"),
        "combined_log": pick("combined_race_log.txt"),
        "events": _latest([f for f in glob.glob(os.path.join(project, "filtered_*.txt"))]),
        "events_spiced": pick("post_filtered_*.txt", exclude=("_forCommentary",)),
        "events_timed": pick("*_forCommentary.txt"),
        "commentary": pick("*_forCommentary_commentary.txt"),
        "commentary_audio": pick("combined_timeline.wav"),
        "graphics_config": pick("graphics_config.json"),
        "graphics_plan": pick("graphics_plan.json"),
        "final_video": pick("*_final*.mp4"),
        "race_notes": pick("project_notes.txt"),
        "camera_notes": pick("camera_instructions.txt"),
    }
    clips = os.path.join(project, "AudioClipsRAW")
    if os.path.isdir(clips) and os.listdir(clips):
        found["audio_clips"] = clips

    # The reviewed events were saved under any name: the old state knew it, or "Add in spice" named its output after it
    reviewed = state.get("refined_log")
    known = {os.path.normcase(os.path.abspath(p)) for p in found.values() if p}
    if not (reviewed and os.path.exists(reviewed) and os.path.normcase(os.path.abspath(reviewed)) not in known
            and not os.path.basename(reviewed).startswith(("post_filtered_", "filtered_", "graphics_"))):
        reviewed = None
        if found["events_spiced"]:
            base = os.path.basename(found["events_spiced"])[len("post_filtered_"):]
            candidate = os.path.join(project, base)
            reviewed = candidate if os.path.exists(candidate) else None
    found["events_reviewed"] = reviewed
    return {k: v for k, v in found.items() if v}


def _log_time(path):
    m = re.search(r"Race_(\d{8}_\d{6})", os.path.basename(path))
    if m:
        return datetime.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
    return datetime.datetime.fromtimestamp(os.path.getmtime(path))


def import_project(name):
    """Imports one old project. Returns the race folders made."""
    project = os.path.join(PROJECTS_DIR, name)
    try:
        with open(os.path.join(project, "project_state.json"), "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    sim = "AMS2" if state.get("sim_type") == "AMS2" else "RaceRoom"

    logs = _race_logs(project)
    if not logs:
        return []

    raw = state.get("raw_log")
    main = next((p for p, _ in logs if raw and os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(raw))), None)
    if not main:
        main = max(logs, key=lambda item: item[1]["duration_sec"])[0]

    made = []
    os.makedirs(racefiles.RACES_DIR, exist_ok=True)
    for path, summary in sorted(logs, key=lambda item: _log_time(item[0])):
        started = _log_time(path)
        track = summary["track"] or name
        folder = racefiles.unique_folder(os.path.join(
            racefiles.RACES_DIR, racefiles.folder_name(started, track, racefiles.race_label(summary))))
        os.makedirs(folder)
        link_or_copy(path, race_file(folder, "race_log"))
        timing = os.path.splitext(path)[0] + "_timing.jsonl"
        if os.path.exists(timing):
            link_or_copy(timing, race_file(folder, "timing"))
        meta = dict(sim=racefiles.guess_sim(path) or sim, track=track, started_at=started.isoformat(timespec="seconds"), status="recorded",
                    summary=summary, imported_from=name)

        if path == main:
            for key, src in _old_pipeline_files(project, state).items():
                dst = race_file(folder, key)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, copy_function=link_or_copy)
                else:
                    link_or_copy(src, dst)
            video = state.get("video_file")
            if video and os.path.exists(video):
                meta["video"] = video
        with open(os.path.join(folder, racefiles.RACE_JSON), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        made.append(folder)
    return made
