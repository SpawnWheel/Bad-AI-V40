"""OBS Studio through its WebSocket server (built into OBS 28 and later: Tools > WebSocket Server Settings).

The Camera Directors use it to switch OBS to the sim's scene, start the recording, and note how far into the
video the race log's clock was at that moment. That offset is saved in the race folder (02_video_sync.json),
and Review & Sync shifts the events by it when it opens, so nobody has to scrub the video to the green flag.

    video time = log time + offset

Pausing the sequence (Space) pauses the recording too, so the offset holds for the whole video.
"""
import base64
import hashlib
import json
import os
import threading
import time
import tkinter as tk
import uuid
from datetime import datetime
from tkinter import messagebox, ttk

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect as ws_connect

import racefiles

CONFIG_FILE = os.path.join(racefiles.APP_DIR, "launcher_config.json")
# Each PC's OBS has its own scenes, so the Camera Directors' choices live here (not in git, see .gitignore):
# {"AMS2": {"record": true, "scene": "..."}, "RaceRoom": {...}}
CHOICES_FILE = os.path.join(racefiles.APP_DIR, "obs_scenes.json")
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 4455
CURRENT_SCENE = "(current scene)" # Scene choice that leaves OBS on whatever scene it shows
START_TIMEOUT = 5.0 # Seconds to wait for OBS to report the recording running
# OBS's outputDuration counts frames once they're encoded, so it runs behind the video: a steady 0.56 s on
# OBS 33 (measured; the finished file starts the moment OBS reports the recording running, to 0.01 s). So the
# video is timed from that moment, and outputDuration + this lag is only used for a recording OBS was already making.
OUTPUT_DURATION_LAG = 0.56
AUTH_FAILED = 4009 # OBS closes the connection with this code on a wrong password


# OBS keeps its WebSocket server settings here (OBS 28+), so on this PC nobody has to copy the password over
OBS_WEBSOCKET_CONFIG = os.path.join(os.environ.get("APPDATA", ""), "obs-studio", "plugin_config", "obs-websocket",
                                    "config.json")


class OBSError(Exception):
    pass


def local_obs_config():
    """OBS's own WebSocket settings on this PC: {"server_enabled", "server_port", "server_password", ...} or {}."""
    try:
        with open(OBS_WEBSOCKET_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def is_local(host):
    return (host or DEFAULT_HOST).strip().lower() in ("localhost", "127.0.0.1", "::1")


def fill_from_obs(host, port, password):
    """A blank port or password is read from OBS's own settings when OBS runs on this PC."""
    if is_local(host) and (not port or not password):
        local = local_obs_config()
        port = port or local.get("server_port")
        password = password or local.get("server_password") or ""
    return port or DEFAULT_PORT, password


def settings():
    """(host, port, password) from the launcher's Settings, blanks filled in from OBS on this PC."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            s = json.load(f).get("global_settings", {})
    except (OSError, ValueError):
        s = {}
    try:
        port = int(s.get("obs_port") or 0)
    except (TypeError, ValueError):
        port = 0
    host = (s.get("obs_host") or DEFAULT_HOST).strip()
    port, password = fill_from_obs(host, port, s.get("obs_password") or "")
    return host, port, password


def _sha256_b64(text):
    return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


class OBS:
    """One connection to OBS. Requests may come from any thread."""

    def __init__(self, host=None, port=None, password=None, timeout=3.0):
        if password is None:
            host, port, password = settings()
        else:
            host = host or DEFAULT_HOST
            port, password = fill_from_obs(host, port, password)
        self.timeout = timeout
        self.lock = threading.Lock()
        try:
            self.ws = ws_connect(f"ws://{host}:{port}", open_timeout=timeout, max_size=None,
                                 subprotocols=["obswebsocket.json"])
        except (OSError, TimeoutError, WebSocketException) as e:
            if is_local(host) and local_obs_config().get("server_enabled") is False:
                raise OBSError("OBS's WebSocket server is switched off. In OBS: Tools > WebSocket Server Settings > "
                               "tick Enable WebSocket server > OK.") from None
            raise OBSError(f"OBS isn't answering at {host}:{port}. Is OBS open, with the WebSocket server "
                           f"switched on (Tools > WebSocket Server Settings)? ({e})") from None
        try:
            hello = self._receive(0)
            identify = {"rpcVersion": 1, "eventSubscriptions": 0} # No events: only answers to requests
            auth = hello.get("authentication")
            if auth:
                if not password:
                    raise OBSError("OBS asks for a password. Copy it from OBS (Tools > WebSocket Server Settings > "
                                   "Show Connect Info) into the app's Settings.")
                identify["authentication"] = _sha256_b64(_sha256_b64(password + auth["salt"]) + auth["challenge"])
            self._send(1, identify)
            self._receive(2)
        except ConnectionClosed as e:
            self.ws.close()
            if e.rcvd and e.rcvd.code == AUTH_FAILED:
                raise OBSError("OBS turned down the password. Copy it again from OBS (Tools > WebSocket Server "
                               "Settings > Show Connect Info) into the app's Settings.") from None
            raise OBSError(f"OBS closed the connection: {e}") from None
        except (OSError, TimeoutError, ValueError) as e:
            self.ws.close()
            raise OBSError(f"OBS didn't answer properly: {e}") from None

    def _send(self, op, data):
        self.ws.send(json.dumps({"op": op, "d": data}))

    def _receive(self, op, request_id=None):
        """The next message with this op code (and request id), skipping anything else."""
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("no answer in time")
            msg = json.loads(self.ws.recv(timeout=left))
            if msg.get("op") == op and (request_id is None or msg["d"].get("requestId") == request_id):
                return msg["d"]

    def request(self, request_type, data=None):
        """Sends one request and returns its responseData; OBSError if OBS refuses it or the line drops."""
        rid = uuid.uuid4().hex
        payload = {"requestType": request_type, "requestId": rid}
        if data:
            payload["requestData"] = data
        try:
            with self.lock:
                self._send(6, payload)
                answer = self._receive(7, rid)
        except (ConnectionClosed, OSError, TimeoutError, ValueError) as e:
            raise OBSError(f"Lost the connection to OBS: {e}") from None
        status = answer.get("requestStatus", {})
        if not status.get("result"):
            raise OBSError(f"OBS refused {request_type}: {status.get('comment') or status.get('code')}")
        return answer.get("responseData") or {}

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ what the directors need

    def version(self):
        v = self.request("GetVersion")
        return v.get("obsVersion", "?"), v.get("obsWebSocketVersion", "?")

    def scenes(self):
        """Scene names as OBS lists them, top first."""
        scenes = self.request("GetSceneList").get("scenes", [])
        return [s["sceneName"] for s in sorted(scenes, key=lambda s: -s.get("sceneIndex", 0))]

    def record_status(self):
        return self.request("GetRecordStatus")


def test_connection(host, port, password):
    """A one-line answer for the Settings page."""
    try:
        obs = OBS(host, port, password)
    except OBSError as e:
        return False, str(e)
    try:
        version, ws_version = obs.version()
        scenes = obs.scenes()
    except OBSError as e:
        return False, str(e)
    finally:
        obs.close()
    return True, f"Connected to OBS {version} (WebSocket {ws_version}). Scenes: {', '.join(scenes) or 'none'}."


# ---------------------------------------------------------------- the race's video sync file

def sync_path(folder):
    return racefiles.race_file(folder, "video_sync")


def read_video_sync(folder):
    """{"offset": seconds, "video": recording or None, ...} or None."""
    try:
        with open(sync_path(folder), "r", encoding="utf-8") as f:
            data = json.load(f)
        float(data["offset"])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_video_sync(folder, offset, video=None, sim=""):
    data = {"offset": round(offset, 3), "video": video, "sim": sim,
            "written": datetime.now().isoformat(timespec="seconds"),
            "about": "video time = race log time + offset, measured by the Camera Director through OBS"}
    with open(sync_path(folder), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def same_video(a, b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ---------------------------------------------------------------- the directors' Recording box

def read_choices(sim):
    """This PC's Record with OBS tick and scene for a sim's Camera Director."""
    try:
        with open(CHOICES_FILE, "r", encoding="utf-8") as f:
            choice = json.load(f).get(sim, {})
    except (OSError, ValueError, AttributeError):
        choice = {}
    return {"record": bool(choice.get("record", True)), "scene": choice.get("scene") or ""}


def save_choices(sim, record, scene):
    try:
        with open(CHOICES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[sim] = {"record": record, "scene": scene}
    with open(CHOICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class Recorder:
    """The Camera Director's "Recording (OBS)" box, and the recording it runs.

    The tick and scene are this PC's own (obs_scenes.json, one entry per sim), since every OBS has its own scenes.
    log writes to the director's Activity Log. Every OBS call is made off the Tk thread.
    """

    def __init__(self, parent, root, log, sim):
        self.root = root
        self.settings = read_choices(sim)
        self.log = log
        self.sim = sim
        self.obs = None
        self.recording = False # Started or found running by this director
        self.offset = None
        self.started_at = None # time.monotonic() when OBS reported the recording running (None: started elsewhere)
        self.paused_since = None
        self.paused_total = 0.0
        self.pause_lock = threading.Lock()
        self.folder = os.environ.get("R3E_PROJECT_PATH") or None

        box = ttk.LabelFrame(parent, text="Recording (OBS)")
        box.pack(fill=tk.X, pady=5)
        row = ttk.Frame(box); row.pack(fill=tk.X, pady=2)
        self.record_var = tk.BooleanVar(value=self.settings["record"])
        ttk.Checkbutton(row, text="Record with OBS", variable=self.record_var,
                        command=self._save_choice).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="R", width=2, command=self.refresh_scenes).pack(side=tk.RIGHT, padx=2)
        self.scene_var = tk.StringVar(value=self.settings["scene"] or CURRENT_SCENE)
        self.scene_combo = ttk.Combobox(row, textvariable=self.scene_var, state="readonly", width=18,
                                        values=[CURRENT_SCENE] + ([self.settings["scene"]] if self.settings["scene"] else []))
        self.scene_combo.pack(side=tk.RIGHT, padx=2)
        self.scene_combo.bind("<<ComboboxSelected>>", lambda e: self._save_choice())
        ttk.Label(row, text="Scene:").pack(side=tk.RIGHT, padx=2)

        row2 = ttk.Frame(box); row2.pack(fill=tk.X, pady=2)
        self.state_var = tk.StringVar(value="Not recording")
        ttk.Label(row2, textvariable=self.state_var).pack(side=tk.LEFT, padx=2)
        self.stop_btn = ttk.Button(row2, text="Stop Recording", command=self.stop_prompt, state="disabled")
        self.stop_btn.pack(side=tk.RIGHT, padx=2)

    def _ui(self, fn, *args):
        self.root.after(0, fn, *args)

    def _set_state(self, text, recording=None):
        def apply():
            self.state_var.set(text)
            if recording is not None:
                self.stop_btn.config(state="normal" if recording else "disabled")
        self._ui(apply)

    def _save_choice(self):
        self.settings["record"] = self.record_var.get()
        scene = self.scene_var.get()
        self.settings["scene"] = "" if scene == CURRENT_SCENE else scene
        try:
            save_choices(self.sim, self.settings["record"], self.settings["scene"])
        except OSError as e:
            self.log(f"Couldn't save the OBS choice: {e}")

    @property
    def enabled(self):
        return self.record_var.get()

    def refresh_scenes(self):
        def work():
            try:
                obs = self.obs or OBS()
                scenes = obs.scenes()
                if obs is not self.obs:
                    obs.close()
            except OBSError as e:
                self.log(f"OBS: {e}")
                return
            def apply():
                self.scene_combo.config(values=[CURRENT_SCENE] + scenes)
                if self.settings["scene"] and self.settings["scene"] not in scenes:
                    self.log(f"OBS has no scene called '{self.settings['scene']}' - pick one again")
            self._ui(apply)
            self.log(f"OBS scenes: {', '.join(scenes)}")
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ called from the director's play thread

    def start(self):
        """Switches to the chosen scene and starts recording (or picks up a recording already running).
        Returns True to go on, False when OBS failed and the user would rather not play without it."""
        if not self.enabled:
            return True
        try:
            if self.obs is None:
                self.obs = OBS()
            scene = self.settings.get("scene")
            if scene and scene not in self.obs.scenes():
                self.log(f"OBS has no scene called '{scene}' - recording the scene OBS is showing. "
                         "Press R in the Recording box and pick one.")
                scene = ""
            if scene:
                self.obs.request("SetCurrentProgramScene", {"sceneName": scene})
            status = self.obs.record_status()
            self.started_at, self.paused_since, self.paused_total = None, None, 0.0
            if status.get("outputActive"):
                if status.get("outputPaused"):
                    self.obs.request("ResumeRecord")
                self.log("OBS was already recording - using that recording (timed to about 0.1 s)")
            else:
                self.obs.request("StartRecord")
                deadline = time.monotonic() + START_TIMEOUT
                while True:
                    asked = time.monotonic()
                    if self.obs.record_status().get("outputActive"):
                        self.started_at = asked # The video's first frame, to about 0.01 s
                        break
                    if asked > deadline:
                        raise OBSError("OBS didn't start recording in time")
                    time.sleep(0.01)
                self.log("OBS recording started" + (f" on scene '{scene}'" if scene else ""))
            self.recording = True
            self._set_state("Recording" + (f" ({scene})" if scene else ""), True)
            return True
        except OBSError as e:
            self.log(f"OBS: {e}")
            self._drop()
            answer = {}
            done = threading.Event()
            def ask():
                answer["go"] = messagebox.askyesno(
                    "OBS", f"Couldn't start the OBS recording:\n\n{e}\n\nPlay the sequence anyway? You'd have to "
                           "record and line the video up by hand.")
                done.set()
            self._ui(ask)
            done.wait()
            return answer["go"]

    def mark(self, log_now):
        """Measures the video time against the director's log clock right now and saves the offset.
        log_now() returns the director's current log time in seconds. Call it whenever the director's
        clock is (re)synced; each call replaces the saved offset."""
        if not (self.recording and self.obs):
            return
        try:
            before = log_now()
            asked = time.monotonic()
            status = self.obs.record_status()
            answered = time.monotonic()
            after = log_now()
        except OBSError as e:
            self.log(f"OBS: {e}")
            return
        if not status.get("outputActive"):
            self.log("OBS isn't recording any more - the video offset wasn't saved")
            self._recording_gone()
            return
        if self.started_at is not None:
            video = self.video_time((asked + answered) / 2)
        else:
            video = status.get("outputDuration", 0) / 1000.0 + OUTPUT_DURATION_LAG
        self.offset = video - (before + after) / 2
        self.log(f"Video offset {self.offset:+.2f}s (video time = log time {'+' if self.offset >= 0 else '-'} "
                 f"{abs(self.offset):.2f}s)")
        if self.folder:
            try:
                write_video_sync(self.folder, self.offset, sim=self.sim) # The video is named when it stops
            except OSError as e:
                self.log(f"Couldn't save the video offset: {e}")

    def video_time(self, at):
        """Seconds into the video at time.monotonic() `at`: since the recording started, less any pauses."""
        with self.pause_lock:
            paused = self.paused_total + (at - self.paused_since if self.paused_since is not None else 0.0)
        return at - self.started_at - paused

    def pause(self):
        self._quiet_request("PauseRecord", True)

    def resume(self):
        self._quiet_request("ResumeRecord", False)

    def _quiet_request(self, request_type, pausing):
        if self.recording and self.obs:
            threading.Thread(target=self._try, args=(request_type, pausing), daemon=True).start()

    def _try(self, request_type, pausing):
        try:
            asked = time.monotonic()
            self.obs.request(request_type)
            at = (asked + time.monotonic()) / 2 # When OBS acted on it
        except OBSError as e:
            self.log(f"OBS: {e}")
            return
        with self.pause_lock:
            if pausing and self.paused_since is None:
                self.paused_since = at
            elif not pausing and self.paused_since is not None:
                self.paused_total += at - self.paused_since
                self.paused_since = None

    # ------------------------------------------------------------------ stopping

    def stop_prompt(self):
        if messagebox.askyesno("Stop Recording", "Stop the OBS recording now?"):
            threading.Thread(target=self.stop, daemon=True).start()

    def stop(self):
        """Stops the recording and makes it the race's replay video."""
        if not (self.recording and self.obs):
            return
        try:
            path = self.obs.request("StopRecord").get("outputPath")
        except OBSError as e:
            self.log(f"OBS: {e}")
            self._recording_gone()
            return
        self.log(f"OBS recording saved: {path}" if path else "OBS recording stopped")
        if path and self.folder:
            try:
                racefiles.update_race(self.folder, video=path)
                if self.offset is not None:
                    write_video_sync(self.folder, self.offset, video=path, sim=self.sim)
                self.log("Set as this race's replay video")
            except OSError as e:
                self.log(f"Couldn't save the video to the race: {e}")
        self._recording_gone()

    def _recording_gone(self):
        self.recording = False
        self._set_state("Not recording", False)
        self._drop()

    def _drop(self):
        if self.obs:
            self.obs.close()
        self.obs = None

    def close(self):
        """The director is closing: the recording keeps going in OBS."""
        self._drop()
