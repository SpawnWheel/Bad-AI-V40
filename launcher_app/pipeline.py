"""The replay pipeline: what each step reads and writes in a race folder, grouped into stages.

Every step writes one fixed file (racefiles.FILES), so a step is done when its file exists, and out of
date when a file it reads has changed since. No scanning or guessing.
"""
import os

import racefiles
from racefiles import race_file

APP_DIR = racefiles.APP_DIR

# input keys a step can take, besides racefiles.FILES keys:
#   video          the replay recording chosen for the race (race.json "video")
#   events_review  the reviewed events if saved, else the AI's events (what Review Events opens)
#   OUTPUT         the step's own output path, as a command-line argument

STEPS = {
    "camera": dict(
        title="Camera Director",
        kind="tool",
        script={"AMS2": "CameraController_AMS2/main_ams2.py", "RaceRoom": "CameraController_Raceroom/main_raceroom.py"},
        args=["race_log"], needs=["race_log"], output="director_notes",
        hint="Plans the camera shots with AI and switches cameras while the replay plays."),
    "combine": dict(
        title="Merge camera notes into the log",
        kind="auto", script="Combiner/combiner.py", workdir="",
        args=["race_log", "director_notes"], needs=["race_log", "director_notes"], output="combined_log",
        hint="Adds who the camera was watching to the race log. Takes a second."),
    "events": dict(
        title="Pick the events for commentary",
        kind="auto", script="AI Steps/gemini_task.py", prompt_folder="Filtering",
        args=["combined_log"], needs=["combined_log"], output="events",
        hint="The AI picks the moments worth talking about from the race log."),
    "review": dict(
        title="Review & sync the events",
        kind="tool", script="3_Refinement/refinement_tool.py",
        args=["video", "events_review"], needs=["video", "events"], output="events_reviewed",
        hint="Watch the replay video, fix or add events, and sync the log to the video. Press Save when done."),
    "spice": dict(
        title="Add in spice",
        kind="auto", script="AI Steps/gemini_task.py", prompt_folder="Refined Filtering",
        args=["events_reviewed"], needs=["events_reviewed"], output="events_spiced",
        hint="The AI adds storylines and colour, using your race notes."),
    "timing": dict(
        title="Work out how long each line can be",
        kind="auto", script="4_FinalFiltering/commentary_calc.py",
        args=["events_spiced"], needs=["events_spiced"], output="events_timed",
        hint="Sets a word count for every event from the time until the next one."),
    "commentary": dict(
        title="Write the commentary",
        kind="auto", script="AI Steps/gemini_task.py", prompt_folder="Commentary",
        args=["events_timed"], needs=["events_timed"], output="commentary",
        hint="The AI writes the commentators' script."),
    "tts": dict(
        title="Record the voices",
        kind="tool", script="TTS/TTSFast/app_gui.py",
        args=["commentary"], needs=["commentary"], output="audio_clips",
        hint="Opens the voice generator in your browser and saves the clips into the race folder. "
             "It keeps running until you press Stop here (or close the app)."),
    "audio": dict(
        title="Stitch the audio together",
        kind="auto", script="AudioCombiner/combine_audio.py",
        args=["audio_clips", "OUTPUT"], needs=["audio_clips"], output="commentary_audio",
        hint="Puts every clip at its time and makes one commentary track."),
    "graphics_setup": dict(
        title="Graphics setup",
        kind="tool", script="GraphicsOverlay/graphics_setup.py",
        args=["video", "race_log"], needs=["video", "race_log"], output="graphics_config",
        hint="Place the timing tower and pop-ups, and set names, logos and car images."),
    "graphics_plan": dict(
        title="Plan the pop-up graphics",
        kind="auto", script="AI Steps/graphic_creator.py", prompt_folder="Graphics",
        args=[], needs=["graphics_config", "commentary"], output="graphics_plan",
        hint="The AI decides when each pop-up graphic appears."),
    "export": dict(
        title="Export the final video",
        kind="auto", script="VideoCombiner/combine_video.py",
        args=["video", "commentary_audio"], needs=["video", "commentary_audio"], output="final_video",
        hint="Cuts the replay to the commentary and burns in the graphics. This can take a while."),
}

STAGES = [
    dict(id="notes", title="Race notes", steps=[],
         about="Tell the AI about this race before you start. The race notes are read by the camera director, "
               "the event picker, the commentary writer and the graphics. Camera notes are only for the camera "
               "director. You can change them at any time."),
    dict(id="film", title="Film the replay", steps=["camera"],
         about="1. Open the Camera Director and press Generate - the AI plans the camera shots from your race log.\n"
               "2. Load the race replay in the sim and start recording your screen (OBS or similar).\n"
               "3. Use the Sync tools at the green flag, then press Play Sequence - the director switches cameras for you.\n"
               "4. When the replay ends, stop recording and pick the video below."),
    dict(id="events", title="Build the event list", steps=["combine", "events", "review"],
         about="The AI picks the moments worth talking about. Then you watch the video in Review & Sync, "
               "fix or add events, and line the log up with the video so the commentary lands on time."),
    dict(id="commentary", title="Write the commentary", steps=["spice", "timing", "commentary"],
         about="Three automatic steps: the AI adds colour from your race notes, each event gets a word count "
               "to fit its gap, and then the commentators' script is written. Press Run all and wait."),
    dict(id="voice", title="Voice it", steps=["tts", "audio"],
         about="Generate the commentators' voices in the voice generator (it opens in your browser), "
               "then stitch the clips into one commentary track."),
    dict(id="graphics", title="Graphics", steps=["graphics_setup", "graphics_plan"],
         about="Set up the broadcast graphics once (most settings carry over to your next race), "
               "then let the AI plan when the pop-ups appear."),
    dict(id="export", title="Export", steps=["export"],
         about="Cuts the replay to the commentary, adds the audio and the graphics, and saves the final MP4."),
]

INPUT_LABELS = {
    "video": "the replay video", "race_log": "the race log", "director_notes": "the camera notes from the Camera Director",
    "combined_log": "the merged log", "events": "the event list", "events_reviewed": "the reviewed events",
    "events_spiced": "the spiced events", "events_timed": "the timed events", "commentary": "the commentary",
    "audio_clips": "the voice clips", "commentary_audio": "the commentary audio", "graphics_config": "the graphics setup",
}


def sim_of(race_folder):
    return racefiles.read_race(race_folder).get("sim") or "AMS2"


def script_for(step, race_folder):
    script = step["script"]
    if isinstance(script, dict):
        script = script.get(sim_of(race_folder)) or next(iter(script.values()))
    return os.path.join(APP_DIR, script)


def workdir_for(step, race_folder):
    if "workdir" in step:
        return os.path.join(APP_DIR, step["workdir"]) if step["workdir"] else APP_DIR
    return os.path.dirname(script_for(step, race_folder))


def prompts_for(step):
    """The prompt files a step can use (newest name last), or [] for steps without a prompt."""
    folder = step.get("prompt_folder")
    if not folder:
        return []
    path = os.path.join(APP_DIR, "AI Steps", folder)
    try:
        return sorted(f for f in os.listdir(path) if f.lower().endswith(".txt"))
    except OSError:
        return []


def prompt_path(step, name):
    return os.path.join(APP_DIR, "AI Steps", step["prompt_folder"], name)


def input_path(race_folder, key):
    if key == "video":
        video = racefiles.read_race(race_folder).get("video")
        return video if video and os.path.exists(video) else None
    if key == "events_review":
        reviewed = race_file(race_folder, "events_reviewed")
        return reviewed if os.path.exists(reviewed) else input_path(race_folder, "events")
    path = race_file(race_folder, key)
    if os.path.isdir(path):
        return path if os.listdir(path) else None
    return path if os.path.exists(path) else None


def output_path(race_folder, step):
    return race_file(race_folder, step["output"])


def output_time(path):
    """When an output was last written (newest file inside a folder output), or None."""
    if not os.path.exists(path):
        return None
    if os.path.isdir(path):
        times = [os.path.getmtime(os.path.join(path, f)) for f in os.listdir(path)]
        return max(times) if times else None
    return os.path.getmtime(path)


def step_status(race_folder, step_id):
    """(state, detail): done | outdated | ready | waiting, with a short explanation."""
    step = STEPS[step_id]
    missing = [k for k in step["needs"] if not input_path(race_folder, k)]
    made = output_time(output_path(race_folder, step))
    if made:
        newer = [k for k in step["needs"] if k != "video" and (output_time(race_file(race_folder, k)) or 0) > made + 1]
        if newer and not missing:
            return "outdated", f"{INPUT_LABELS.get(newer[0], newer[0]).capitalize()} changed since this ran"
        return "done", ""
    if missing:
        return "waiting", "Needs " + " and ".join(INPUT_LABELS.get(k, k) for k in missing)
    return "ready", ""


def stage_status(race_folder, stage):
    if stage["id"] == "notes":
        has = os.path.exists(race_file(race_folder, "race_notes"))
        return ("done" if has else "optional"), (1 if has else 0), 1
    states = [step_status(race_folder, s)[0] for s in stage["steps"]]
    if stage["id"] == "film":
        # Choosing the replay video is part of filming it
        states.append("done" if input_path(race_folder, "video") else "ready")
    done = sum(1 for s in states if s == "done")
    total = len(states)
    if done == total:
        return "done", done, total
    if any(s in ("ready", "outdated") for s in states) or done:
        return "ready", done, total
    return "waiting", done, total


def progress(race_folder):
    """(stages done, stage count) for the library - race notes are optional and not counted."""
    stages = [s for s in STAGES if s["id"] != "notes"]
    done = sum(1 for s in stages if stage_status(race_folder, s)[0] == "done")
    return done, len(stages)


def next_stage(race_folder):
    for stage in STAGES[1:]:
        if stage_status(race_folder, stage)[0] != "done":
            return stage
    return STAGES[-1]


def command(race_folder, step_id):
    """(args, cwd, env additions) to run a step on a race."""
    step = STEPS[step_id]
    out = output_path(race_folder, step)
    args = []
    for key in step["args"]:
        if key == "OUTPUT":
            args.append(out)
        else:
            path = input_path(race_folder, key)
            if path:
                args.append(path)
    env = {"R3E_PROJECT_PATH": race_folder, "BADAI_OUTPUT": out}
    if args:
        env["R3E_INPUT_FILE"] = args[0]
    return [script_for(step, race_folder)] + args, workdir_for(step, race_folder), env


def keep_history(race_folder, step_id):
    """Copies the step's previous output into history/ before an automatic step replaces it."""
    path = output_path(race_folder, STEPS[step_id])
    if os.path.isfile(path) and os.path.getsize(path) < 50 * 1024 * 1024:
        try:
            racefiles.save_to_history(path)
        except OSError as e:
            print(f"Could not keep a copy of {os.path.basename(path)}: {e}")
