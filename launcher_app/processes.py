"""Runs every tool in the background, without console windows, and collects what it prints.

Output is read on worker threads and handed to the Tk thread through a queue; poll() drains it.
Tools that support it (the loggers and the race engineer) are stopped cleanly by writing "stop"
to their stdin; everything else, and anything that does not stop in time, has its process tree ended.
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

import racefiles

ANSI_RE = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")
MAX_LINES = 4000

ANSI_COLOURS = {
    30: "#7d8594", 31: "#ff6b6b", 32: "#5fd38d", 33: "#e9c46a", 34: "#6ea8fe", 35: "#d291ff", 36: "#5ccfe6", 37: "#d5d9e0",
    90: "#7d8594", 91: "#ff7b7b", 92: "#7be3a4", 93: "#ffd866", 94: "#8ab8ff", 95: "#e0a8ff", 96: "#7fe0f0", 97: "#ffffff",
}


_PYTHON = None


def _console_python(exe):
    """python.exe next to a pythonw.exe (children need real stdout)."""
    if os.path.basename(exe).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


def _has_packages(exe):
    """True if this interpreter can import the AI library the steps need."""
    try:
        return subprocess.run([exe, "-c", "from google import genai"], capture_output=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _default_python():
    """The Python that Windows' py launcher picks by default (marked * in `py -0p`), or None."""
    try:
        out = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "*" and os.path.exists(parts[-1]):
            return parts[-1]
    return None


def python_exe():
    """The interpreter the tools run under: the launcher's own, unless it lacks the packages the steps need
    (e.g. BAD AI was started with a second Python install) and the default one has them."""
    global _PYTHON
    if _PYTHON is None:
        _PYTHON = _console_python(sys.executable)
        if not _has_packages(_PYTHON):
            other = _default_python()
            if other and os.path.normcase(other) != os.path.normcase(_PYTHON) and _has_packages(other):
                print(f"{_PYTHON} has no google-genai; running the tools with {other} instead.")
                _PYTHON = other
    return _PYTHON


def parse_ansi(text):
    """[(segment, colour or None, bold)] for a line that may hold ANSI colour codes."""
    out, colour, bold, pos = [], None, False, 0
    for m in ANSI_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], colour, bold))
        pos = m.end()
        if m.group(2) != "m":
            continue
        for code in [int(c) for c in m.group(1).split(";") if c.isdigit()] or [0]:
            if code == 0:
                colour, bold = None, False
            elif code == 1:
                bold = True
            elif code in ANSI_COLOURS:
                colour = ANSI_COLOURS[code]
            elif code == 41:
                colour = "#ff6b6b"
    if pos < len(text):
        out.append((text[pos:], colour, bold))
    return out


class Process:
    def __init__(self, pid_seq, label, kind, race=None, step_id=None, graceful=False):
        self.id = pid_seq
        self.label = label
        self.kind = kind            # engineer | logger | step | tool
        self.race = race            # race folder, for pipeline steps
        self.step_id = step_id
        self.graceful = graceful
        self.popen = None
        self.lines = []             # [(text, transient)]
        self.state = "starting"     # running | finished | failed | stopped
        self.returncode = None
        self.started = time.time()
        self.ended = None
        self.stopping = False
        self.on_exit = []
        self.on_status = []

    @property
    def running(self):
        return self.state in ("starting", "running")

    def elapsed(self):
        return (self.ended or time.time()) - self.started


class ProcessManager:
    def __init__(self):
        self.processes = []
        self.events = queue.Queue()
        self.listeners = []        # fn(kind, process, payload) on the Tk thread
        self._seq = 0

    # ------------------------------------------------------------------ starting

    def start(self, label, args, cwd, env=None, kind="step", race=None, step_id=None, graceful=False):
        self._seq += 1
        proc = Process(self._seq, label, kind, race, step_id, graceful)
        full_env = os.environ.copy()
        full_env.update(env or {})
        full_env["PYTHONUNBUFFERED"] = "1"
        full_env["PYTHONIOENCODING"] = "utf-8"
        full_env["BADAI_MANAGED"] = "1"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            proc.popen = subprocess.Popen(args, cwd=cwd, env=full_env, creationflags=flags,
                                          stdin=subprocess.PIPE if graceful else subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except Exception as e:
            proc.state = "failed"
            proc.ended = time.time()
            proc.lines.append((f"Could not start {label}: {e}", False))
            self.processes.append(proc)
            self._emit("added", proc)
            self._emit("exit", proc)
            return proc
        proc.state = "running"
        self.processes.append(proc)
        threading.Thread(target=self._reader, args=(proc,), daemon=True, name=f"read-{label}").start()
        self._emit("added", proc)
        return proc

    def _reader(self, proc):
        stream = proc.popen.stdout
        buf = b""
        while True:
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(1)
            if not chunk:
                break
            buf += chunk
            # \r ends a progress line (ffmpeg, tqdm) that the next one replaces
            while True:
                idx = min([i for i in (buf.find(b"\n"), buf.find(b"\r")) if i >= 0], default=-1)
                if idx < 0:
                    break
                raw, sep = buf[:idx], buf[idx:idx + 1]
                buf = buf[idx + 1:]
                if sep == b"\r" and buf.startswith(b"\n"):
                    buf, sep = buf[1:], b"\n"
                self._line(proc, raw.decode("utf-8", errors="replace"), transient=(sep == b"\r"))
        if buf:
            self._line(proc, buf.decode("utf-8", errors="replace"), False)
        code = proc.popen.wait()
        self.events.put(("exit", proc, code))

    def _line(self, proc, text, transient):
        if text.startswith(racefiles.STATUS_PREFIX):
            try:
                self.events.put(("status", proc, json.loads(text[len(racefiles.STATUS_PREFIX):])))
            except ValueError:
                pass
            return
        if not text.strip() and transient:
            return
        self.events.put(("line", proc, (text, transient)))

    # ------------------------------------------------------------------ Tk thread

    def poll(self):
        """Drains worker events into the processes and tells the listeners. Call from the Tk thread."""
        changed = set()
        for _ in range(2000):
            try:
                kind, proc, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                text, transient = payload
                if proc.lines and proc.lines[-1][1]:
                    proc.lines[-1] = (text, transient)  # replace the previous progress line
                    self._emit("replace", proc, text)
                else:
                    proc.lines.append((text, transient))
                    self._emit("line", proc, text)
                if len(proc.lines) > MAX_LINES:
                    del proc.lines[:len(proc.lines) - MAX_LINES]
                changed.add(proc)
            elif kind == "status":
                for fn in list(proc.on_status):
                    fn(proc, payload)
                self._emit("status", proc, payload)
            elif kind == "exit":
                proc.returncode = payload
                proc.ended = time.time()
                if proc.stopping:
                    proc.state = "stopped"
                else:
                    proc.state = "finished" if payload == 0 else "failed"
                proc.lines.append((f"── {proc.label} {proc.state}"
                                   f"{'' if payload == 0 or proc.stopping else f' (exit code {payload})'} ──", False))
                self._emit("line", proc, proc.lines[-1][0])
                for fn in list(proc.on_exit):
                    fn(proc)
                self._emit("exit", proc)
        return changed

    def _emit(self, kind, proc, payload=None):
        for fn in list(self.listeners):
            try:
                fn(kind, proc, payload)
            except Exception as e:
                print(f"Activity listener failed: {e}")

    # ------------------------------------------------------------------ stopping

    def stop(self, proc, timeout=6.0):
        """Asks a process to stop (cleanly if it can), ending its process tree if it does not."""
        if not proc.running or not proc.popen:
            return
        proc.stopping = True

        def work():
            if proc.graceful:
                try:
                    proc.popen.stdin.write(b"stop\n")
                    proc.popen.stdin.flush()
                    proc.popen.stdin.close()
                except OSError:
                    pass
                try:
                    proc.popen.wait(timeout)
                    return
                except subprocess.TimeoutExpired:
                    pass
            kill_tree(proc.popen)

        threading.Thread(target=work, daemon=True).start()

    def running(self, kind=None):
        return [p for p in self.processes if p.running and (kind is None or p.kind == kind)]

    def find_step(self, race, step_id):
        for p in reversed(self.processes):
            if p.race == race and p.step_id == step_id and p.running:
                return p
        return None

    def clear_finished(self):
        self.processes = [p for p in self.processes if p.running]

    def stop_all(self, timeout=6.0):
        """Stops everything and waits (used when the app closes)."""
        procs = self.running()
        for p in procs:
            self.stop(p, timeout)
        deadline = time.time() + timeout + 2
        for p in procs:
            try:
                p.popen.wait(max(0.1, deadline - time.time()))
            except Exception:
                kill_tree(p.popen)


def kill_tree(popen):
    if popen.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(popen.pid), "/T", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        popen.kill()
