import gradio as gr
from fastapi import Request
import os
import json
import shutil
import subprocess
import threading
import time
import re
import sys

# Tags process_tts.py prints for each finished or failed clip (kept in step with process_tts.py)
CLIP_TAG = "@@CLIP "
FAILED_TAG = "@@FAILED "
IDLE_TAG = "@@IDLE"

default_input_file = os.environ.get("R3E_INPUT_FILE")
if not default_input_file and len(sys.argv) > 1:
    if os.path.exists(sys.argv[1]):
        default_input_file = sys.argv[1]

default_output_folder = "outputs test"
r3e_project_path = os.environ.get("R3E_PROJECT_PATH")
if os.environ.get("BADAI_OUTPUT"):
    default_output_folder = os.environ["BADAI_OUTPUT"]
elif r3e_project_path:
    default_output_folder = os.path.join(r3e_project_path, "AudioClipsRAW")

VOICES_DIR = "CommentatorsAndDrivers"
IDLE_UNLOAD_SECONDS = 60  # a finished run frees the GPU after this long without a redo


def file_path_of(f):
    """Gradio 5 hands files over as path strings; older versions as objects with .name."""
    if f is None:
        return None
    return f if isinstance(f, str) else getattr(f, "name", None)


# Get initial list of speakers to show in the UI if possible
def get_speakers_from_file(file_path):
    path = file_path_of(file_path)
    if path is None or not os.path.exists(path):
        return []

    speakers = set()
    pattern = r'^(\d{2}:\d{2}:\d{2}) - \[(.*?)\] (.*)$'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                match = re.match(pattern, line.strip())
                if match:
                    speakers.add(match.group(2))
    except Exception:
        pass
    return sorted(list(speakers))


class TTSRunner:
    """Runs process_tts.py in the background and keeps its log, the clips it has made and the redo queue.

    A redo is sent to the running process, which does it straight after its current line. After the
    last line the process keeps the model loaded and waits for more redos, until Unload Model or the
    next Start. Redos it didn't get to (or asked for once it was unloaded) go to a follow-up run.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.proc = None
        self.base_args = None
        self.log = ""
        self.progress = ""
        self.clips = {}      # line index -> clip info, in the order the lines were first made
        self.pending = []    # line indexes waiting for a redo
        self.version = 0     # bumped whenever the clip list changes
        self.idle = False    # all lines done, model loaded and waiting for redos
        self.made = 0        # takes made this run; each take's number puts it in the review queue

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def unload(self):
        """Lets an idle process finish (it exits once stdin closes), freeing the GPU. Returns the thread to wait on."""
        with self.lock:
            proc, thread = self.proc, self.thread
            if not (self.idle and proc):
                return None
        try:
            proc.stdin.close()
        except OSError:
            pass
        return thread

    def start(self, base_args, line_args):
        # A finished run that is only waiting for redos makes way for the new one
        waiting = self.unload()
        if waiting:
            waiting.join(timeout=60)
        with self.lock:
            if self.busy():
                return False
            self.base_args = base_args
            self.log, self.progress = "", "Loading model..."
            self.clips, self.pending, self.made = {}, [], 0
            self.version += 1
            self.thread = threading.Thread(target=self._work, args=(base_args + line_args,), daemon=True)
            self.thread.start()
        return True

    def keep(self, line):
        with self.lock:
            clip = self.clips.get(line)
            if clip and clip["review"] == "new":
                clip["review"] = "kept"
                self.version += 1

    def redo(self, line):
        with self.lock:
            clip = self.clips.get(line)
            if not clip or line in self.pending:
                return
            self.pending.append(line)
            clip["status"] = "queued"
            clip["review"] = "redoing"
            self.version += 1
            self.log += f"--- Redo of line {line} ({clip['timecode']} {clip['speaker']}) queued ---\n"
            if not self.busy():
                self.log += f"\n--- Redoing line {line} (loading model) ---\n"
                self.progress = "Loading model for redo..."
                self.thread = threading.Thread(target=self._work, args=(self._redo_args(),), daemon=True)
                self.thread.start()
                return
            proc = self.proc
            if proc and proc.poll() is None:
                try:
                    proc.stdin.write(json.dumps({"redo": line, "path": clip["path"]}) + "\n")
                    proc.stdin.flush()
                except OSError:
                    pass  # the process is finishing; the follow-up run picks this one up

    def _redo_args(self):
        args = self.base_args + ["--redo_only"]
        for line in self.pending:
            args += ["--redo", f"{line}={self.clips[line]['path']}"]
        return args

    def _work(self, args):
        while args:
            with self.lock:
                before = list(self.pending)
            self._run(args)
            with self.lock:
                if self.pending and self.pending == before and "--redo_only" in args:
                    # A redo-only run that did none of its redos would just fail again
                    for line in self.pending:
                        self.clips[line]["status"] = "failed"
                    self.pending = []
                    self.version += 1
                if self.pending:
                    self.log += f"\n--- Redoing {len(self.pending)} clip(s) queued at the end of the run ---\n"
                    args = self._redo_args()
                else:
                    args = None
                    self.progress = "Done · model unloaded."

    def _run(self, args):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [sys.executable, "process_tts.py"] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env
        )
        with self.lock:
            self.proc = proc

        for line in proc.stdout:
            # Progress bars can leave a line open, so look for the tags anywhere in the line
            if CLIP_TAG in line:
                self._clip_done(json.loads(line.split(CLIP_TAG, 1)[1]))
                continue
            if FAILED_TAG in line:
                self._clip_failed(json.loads(line.split(FAILED_TAG, 1)[1])["line"])
                continue
            if IDLE_TAG in line:
                with self.lock:
                    self.idle = True
                    self.progress = ("✅ All lines done · model still loaded, so redos start straight away "
                                     f"(unloads after {IDLE_UNLOAD_SECONDS} s without a redo)")
                    self.log += (f"--- All lines done. The model stays loaded for redos for {IDLE_UNLOAD_SECONDS} s "
                                 "(Unload Model frees the GPU now) ---\n")
                continue
            with self.lock:
                self.idle = False
                self.log += line
                progress_match = re.search(r'\[(\d+)/(\d+)\]', line)
                if progress_match:
                    self.progress = f"Processing line {progress_match.group(1)} of {progress_match.group(2)}"
                elif line.startswith("[redo]"):
                    self.progress = "Redoing " + line[len("[redo]"):].strip()

        proc.wait()
        try:
            proc.stdin.close()
        except OSError:
            pass
        with self.lock:
            self.proc = None
            self.idle = False
            if proc.returncode == 0:
                self.log += "\n--- Model unloaded ---\n"
            else:
                self.log += f"\n--- Process exited with code {proc.returncode} ---\n"

    def _clip_done(self, info):
        allow_path(os.path.dirname(info["path"]))
        with self.lock:
            line = info["line"]
            old = self.clips.get(line)
            info["takes"] = old["takes"] + 1 if old else 1
            info["status"] = "redone" if info["redo"] else "done"
            # Every new take goes to the back of the review queue
            self.made += 1
            info["review"], info["seq"] = "new", self.made
            if info["redo"] and line in self.pending:
                self.pending.remove(line)
            self.clips[line] = info
            self.version += 1

    def _clip_failed(self, line):
        with self.lock:
            if line in self.pending:
                self.pending.remove(line)
                # The old take is still there, so it goes back for review
                self.made += 1
                self.clips[line].update(status="failed", review="new", seq=self.made)
                self.version += 1


runner = TTSRunner()


def allow_path(folder):
    # Gradio only plays files from folders it has been told about; the clips live in the race folder
    folder = os.path.abspath(folder)
    if folder not in demo.allowed_paths:
        demo.allowed_paths.append(folder)


def run_tts_process(input_file, output_folder, model_size, xvec_only, temperature, start_line, limit, lock_seed, seed):
    path = file_path_of(input_file)
    if path is None:
        return "Error: Please select an input file."

    # Prepare arguments
    base_args = [
        "--input", path,
        "--output", output_folder,
        "--model", f"Qwen/Qwen3-TTS-12Hz-{model_size}",
        "--temperature", str(temperature),
        "--listen",
        "--idle_timeout", str(IDLE_UNLOAD_SECONDS),
    ]

    if xvec_only:
        base_args.append("--xvec_only")

    if lock_seed and seed is not None:
        base_args.extend(["--seed", str(max(0, int(seed)))])

    line_args = ["--start_line", str(int(start_line))]
    if limit and int(limit) > 0:
        line_args.extend(["--limit", str(int(limit))])

    if not runner.start(base_args, line_args):
        gr.Warning("A run is already going. Use Redo on a clip to add it to the queue.")
    return runner.log


def poll_runner(seen):
    """Timer tick: pushes the log, the progress line and (only when it changed) the clip list."""
    with runner.lock:
        log, progress, version = runner.log, runner.progress, runner.version
        clips = [dict(c) for c in runner.clips.values()] if version != seen.get("version") else None
        queued = len(runner.pending)
    if queued:
        progress += f" · {queued} redo(s) queued"
    log_out = log if len(log) != seen.get("log_len") else gr.skip()
    new_seen = {"version": version, "log_len": len(log)}
    return log_out, progress, clips if clips is not None else gr.skip(), new_seen


def clip_action(action):
    """'keep:<line>' or 'redo:<line>' from the page's review card and clip list."""
    kind, _, line = (action or "").partition(":")
    if not line.isdigit():
        return
    if kind == "keep":
        runner.keep(int(line))
    elif kind == "redo":
        runner.redo(int(line))


def unload_model():
    if runner.unload() is None:
        gr.Info("No model is waiting - it unloads by itself when nothing is loaded or running.")
    else:
        gr.Info("Unloading the model.")


REVIEW_HTML = """
<div id="tts-review">
  <div id="tts-counts">Clips show up here as they are made.</div>
  <div id="tts-card" class="tts-waiting">
    <div class="tts-card-head"><b id="tts-card-title">Waiting for the first clip...</b><span id="tts-card-meta"></span></div>
    <div id="tts-card-text"></div>
    <audio id="tts-card-audio" controls preload="auto"></audio>
    <div class="tts-card-btns">
      <button id="tts-keep" disabled>👍 Keep <kbd>K</kbd></button>
      <button id="tts-redo-card" disabled>🔁 Redo <kbd>R</kbd></button>
    </div>
    <div class="tts-card-foot">
      <label><input type="checkbox" id="tts-autoplay" checked> Auto-play each clip</label>
      <span><kbd>Space</kbd> play / pause</span>
    </div>
  </div>
  <details id="tts-all"><summary>All clips</summary><div id="tts-clips"></div></details>
</div>
"""

# The review card plays one clip at a time, oldest take first; Keep or Redo moves on to the next.
# New clips and new takes queue up behind it, so nothing moves under the mouse while listening.
# "All clips" lists every line in script order to go back to. Both update in place from the clip
# data, so a playing clip keeps playing while others arrive.
CLIPS_JS = """
(clips) => {
    const T = window.__tts || (window.__tts = {decided: new Map(), current: null, clips: []});
    if (Array.isArray(clips)) T.clips = clips;
    const key = c => c.line + ':' + c.takes;
    // What the page shows for a clip: a decision made here counts before the server has caught up
    const stateOf = c => c.review === 'new' && T.decided.has(key(c))
        ? (T.decided.get(key(c)) === 'keep' ? 'kept' : 'redoing') : c.review;
    const src = c => 'gradio_api/file=' + encodeURIComponent(c.path) + '?take=' + c.takes;
    const send = (action) => {
        fetch('tts-action', {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify({action})});
    };

    if (!T.wired) {
        T.wired = true;
        T.decide = (kind) => {
            const c = T.current;
            if (!c) return;
            T.decided.set(key(c), kind);
            send(kind + ':' + c.line);
            T.current = null;
            T.render();
        };
        T.redoFromList = (line) => {
            const c = T.clips.find(x => x.line === line);
            if (c) T.decided.set(key(c), 'redo');
            send('redo:' + line);
            T.render();
        };
        // Auto-play is on unless this browser turned it off before
        const autoplay = document.getElementById('tts-autoplay');
        try { autoplay.checked = localStorage.getItem('tts-autoplay') !== 'off'; } catch (e) { autoplay.checked = true; }
        autoplay.onchange = () => { try { localStorage.setItem('tts-autoplay', autoplay.checked ? 'on' : 'off'); } catch (e) {} };
        document.getElementById('tts-keep').onclick = () => T.decide('keep');
        document.getElementById('tts-redo-card').onclick = () => T.decide('redo');
        document.getElementById('tts-card-audio').addEventListener('ended', () =>
            document.getElementById('tts-card').classList.add('tts-heard'));
        document.addEventListener('keydown', (e) => {
            const tag = (e.target.tagName || '').toLowerCase();
            if (['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable) return;
            if (e.ctrlKey || e.altKey || e.metaKey || !document.getElementById('tts-card')?.offsetParent) return;
            const k = e.key.toLowerCase();
            if (k === 'k') T.decide('keep');
            else if (k === 'r') T.decide('redo');
            else if (k === ' ') {
                e.preventDefault();
                const a = document.getElementById('tts-card-audio');
                if (a.src) a.paused ? a.play() : a.pause();
            }
        });
    }

    T.render = () => {
        const all = T.clips;
        const waiting = all.filter(c => stateOf(c) === 'new').sort((a, b) => a.seq - b.seq);
        const redoing = all.filter(c => stateOf(c) === 'redoing').length;
        const kept = all.filter(c => stateOf(c) === 'kept').length;

        // Card: stay on the clip being reviewed; otherwise take the oldest waiting take
        const card = document.getElementById('tts-card');
        const audio = document.getElementById('tts-card-audio');
        const stillThere = T.current && waiting.some(c => key(c) === key(T.current));
        if (!stillThere) {
            const next = waiting[0] || null;
            T.current = next;
            card.classList.remove('tts-heard');
            document.getElementById('tts-keep').disabled = !next;
            document.getElementById('tts-redo-card').disabled = !next;
            if (next) {
                card.classList.remove('tts-waiting');
                document.getElementById('tts-card-title').textContent = next.timecode + ' · ' + next.speaker;
                document.getElementById('tts-card-meta').textContent = ' · line ' + next.line
                    + (next.takes > 1 ? ' · 🔁 take ' + next.takes : '') + (next.status === 'failed' ? ' · ❌ redo failed, old take' : '');
                document.getElementById('tts-card-text').textContent = next.text;
                audio.src = src(next);
                if (document.getElementById('tts-autoplay').checked) audio.play().catch(() => {});
            } else {
                card.classList.add('tts-waiting');
                audio.removeAttribute('src');
                audio.load();
                document.getElementById('tts-card-title').textContent = all.length ? 'All caught up' : 'Waiting for the first clip...';
                document.getElementById('tts-card-meta').textContent = all.length ? ' · the next clip plays here when it is made' : '';
                document.getElementById('tts-card-text').textContent = '';
            }
        }
        const left = waiting.length - (T.current ? 1 : 0);
        document.getElementById('tts-counts').innerHTML = all.length
            ? `<b>${left}</b> more to review · <b>${redoing}</b> redoing · <b>${kept}</b> kept · ${all.length} lines made`
            : 'Clips show up here as they are made.';

        // All clips, in script order
        const box = document.getElementById('tts-clips');
        const label = {new: '🆕 to review', kept: '✅ kept', redoing: '⏳ redoing'};
        document.querySelector('#tts-all summary').textContent = `All clips (${all.length})`;
        for (const c of all) {
            let row = box.querySelector(`.tts-clip[data-line="${c.line}"]`);
            if (!row) {
                row = document.createElement('div');
                row.className = 'tts-clip';
                row.dataset.line = c.line;
                row.innerHTML = '<div class="tts-head"><span class="tts-chip"></span><b></b><span class="tts-meta"></span></div>'
                    + '<div class="tts-text"></div>'
                    + '<div class="tts-ctrl"><audio controls preload="none"></audio><button class="tts-redo">🔁 Redo</button></div>';
                row.querySelector('.tts-redo').onclick = () => T.redoFromList(c.line);
                const next = [...box.querySelectorAll('.tts-clip')].find(r => +r.dataset.line > c.line);
                box.insertBefore(row, next || null);
            }
            const state = stateOf(c);
            row.querySelector('.tts-chip').textContent = label[state] || '';
            row.querySelector('.tts-chip').dataset.state = state;
            row.querySelector('b').textContent = c.timecode + ' · ' + c.speaker;
            row.querySelector('.tts-meta').textContent = ' · line ' + c.line + (c.takes > 1 ? ' · take ' + c.takes : '');
            row.querySelector('.tts-text').textContent = c.text;
            const a = row.querySelector('audio');
            if (a.dataset.take !== String(c.takes)) {
                a.src = src(c);
                a.dataset.take = String(c.takes);
            }
            const btn = row.querySelector('.tts-redo');
            btn.disabled = state === 'redoing';
            btn.textContent = state === 'redoing' ? '⏳ Queued' : '🔁 Redo';
        }
        if (!all.length) box.innerHTML = '';
    };
    T.render();
}
"""

CLIPS_CSS = """
.tts-hidden {display: none !important;}
#tts-counts {font-size: 0.9em; margin-bottom: 6px; opacity: 0.85;}
#tts-card {border: 2px solid var(--color-accent); border-radius: 10px; padding: 12px 14px; transition: border-color 0.3s;}
#tts-card.tts-waiting {border-color: var(--border-color-primary); opacity: 0.75;}
#tts-card.tts-heard {border-color: #2e9d55;}
.tts-card-head {font-size: 1.05em;}
#tts-card-meta, .tts-meta {opacity: 0.6;}
#tts-card-text {margin: 6px 0 10px; font-size: 0.95em; min-height: 1.2em;}
#tts-card-audio {width: 100%; height: 36px;}
.tts-card-btns {display: flex; gap: 10px; margin-top: 10px;}
.tts-card-btns button {flex: 1; padding: 10px; font-size: 1.05em; border-radius: 8px; cursor: pointer;
                       border: 1px solid var(--border-color-primary); background: var(--button-secondary-background-fill);}
#tts-keep:not(:disabled) {background: #2e9d55; color: white; border-color: #2e9d55;}
.tts-card-btns button:disabled {opacity: 0.4; cursor: default;}
.tts-card-foot {display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.85em; opacity: 0.75;}
#tts-autoplay {appearance: auto; -webkit-appearance: checkbox; accent-color: var(--color-accent);
               width: 14px; height: 14px; vertical-align: middle; margin-right: 4px;}
kbd {border: 1px solid var(--border-color-primary); border-radius: 4px; padding: 0 4px; font-size: 0.8em;}
#tts-all {margin-top: 12px;}
#tts-all summary {cursor: pointer; font-weight: 600;}
#tts-clips {max-height: 420px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; margin-top: 6px;}
.tts-clip {border: 1px solid var(--border-color-primary); border-radius: 8px; padding: 6px 10px;}
.tts-head {font-size: 0.9em;}
.tts-chip {font-size: 0.8em; margin-right: 6px; padding: 1px 6px; border-radius: 10px; background: var(--background-fill-secondary);}
.tts-chip[data-state="new"] {background: rgba(255, 170, 0, 0.25);}
.tts-chip[data-state="kept"] {background: rgba(46, 157, 85, 0.25);}
.tts-text {font-size: 0.85em; opacity: 0.85; margin: 2px 0 4px;}
.tts-ctrl {display: flex; align-items: center; gap: 8px;}
.tts-ctrl audio {height: 32px; flex: 1; min-width: 0;}
.tts-redo {padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-color-primary);
           background: var(--button-secondary-background-fill); cursor: pointer; white-space: nowrap;}
.tts-redo:disabled {opacity: 0.5; cursor: default;}
"""


def get_mappings():
    if os.path.exists("speaker_mappings.json"):
        with open("speaker_mappings.json", "r", encoding="utf-8") as f:
            return json.dumps(json.load(f), indent=4)
    return "{}"

def save_mappings(text):
    try:
        data = json.loads(text)
        with open("speaker_mappings.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return "Mappings saved successfully!"
    except Exception as e:
        return f"Error saving mappings: {e}"

def refresh_speakers(file_path):
    if file_path is None:
        return "Please upload a commentary file to see detected speakers."

    speakers = get_speakers_from_file(file_path)
    if not speakers:
        return "No speakers detected in file."

    # Load mappings to find where the voices are
    mappings = {}
    if os.path.exists("speaker_mappings.json"):
        with open("speaker_mappings.json", "r", encoding="utf-8") as f:
            mappings = json.load(f)

    status_lines = [f"### Detected Speakers ({len(speakers)}):"]

    for s in speakers:
        found_msg = "❌ No voice file mapped"
        voice_file = mappings.get(s)

        if voice_file:
            path = os.path.join(VOICES_DIR, voice_file)
            if os.path.exists(path):
                ref_text_path = path.rsplit('.', 1)[0] + '.txt'
                if os.path.exists(ref_text_path):
                    found_msg = "✅ Found Voice + Transcript (High Quality)"
                else:
                    found_msg = "⚠️ Found Voice (Standard Quality)"
                if os.path.exists(path.rsplit('.', 1)[0] + '.style.txt'):
                    found_msg += " · 🎭 style note"

        status_lines.append(f"- **{s}**: {found_msg}")

    return "\n".join(status_lines)

def write_style(wav_name, style):
    """Saves (or clears) a voice's delivery note, read by process_tts.py as the model's instruct."""
    style_path = os.path.join(VOICES_DIR, wav_name.rsplit('.', 1)[0] + ".style.txt")
    if style.strip():
        with open(style_path, 'w', encoding='utf-8') as f:
            f.write(style.strip())
    elif os.path.exists(style_path):
        os.remove(style_path)

def upload_voice(wav_file, txt_file=None, direct_text="", style=""):
    wav_path = file_path_of(wav_file)
    if wav_path is None:
        return "Please select a WAV file.", gr.skip()

    if not os.path.exists(VOICES_DIR):
        os.makedirs(VOICES_DIR)

    # Save WAV into the library
    wav_name = os.path.basename(wav_path)
    wav_dest = os.path.join(VOICES_DIR, wav_name)
    shutil.copy(wav_path, wav_dest)

    msg = f"Successfully added {wav_name} to {VOICES_DIR}."

    # Save TXT (priority: direct text > file upload)
    transcript_content = ""
    txt_path = file_path_of(txt_file)
    if direct_text.strip():
        transcript_content = direct_text.strip()
    elif txt_path is not None:
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                transcript_content = f.read().strip()
        except Exception as e:
            return f"Error reading transcript file: {e}", gr.update(choices=list_voices_dropdown())

    if transcript_content:
        txt_name = wav_name.rsplit('.', 1)[0] + ".txt"
        txt_dest = os.path.join(VOICES_DIR, txt_name)
        with open(txt_dest, 'w', encoding='utf-8') as f:
            f.write(transcript_content)
        msg += f" Added transcript {txt_name} for High Quality mode."

    if style.strip():
        write_style(wav_name, style)
        msg += " Saved its style note."

    return msg, gr.update(choices=list_voices_dropdown())

def load_voice_transcript(voice_filename):
    if not voice_filename:
        return "", ""
    base = os.path.join(VOICES_DIR, voice_filename.rsplit('.', 1)[0])
    texts = []
    for path in (base + ".txt", base + ".style.txt"):
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                texts.append(f.read())
        else:
            texts.append("")
    return texts[0], texts[1]

def save_voice_transcript(voice_filename, text, style):
    if not voice_filename:
        return "No voice selected."
    txt_path = os.path.join(VOICES_DIR, voice_filename.rsplit('.', 1)[0] + ".txt")
    try:
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(text.strip())
        write_style(voice_filename, style)
        return f"✅ Transcript and style saved for {voice_filename}. Voice is now High Quality!"
    except Exception as e:
        return f"❌ Error: {e}"

def list_voices_dropdown():
    if os.path.exists(VOICES_DIR):
        return sorted([f for f in os.listdir(VOICES_DIR) if f.endswith(".wav")])
    return []

STYLE_HINT = ("Optional. Tells the model how to deliver the line, e.g. \"Speak English with a strong Spanish accent.\" "
              "Works best with Fast Mode off; leave blank to rely on the reference audio alone.")

# Build the UI
with gr.Blocks(title="Faster Qwen3 TTS - Race Commentary", css=CLIPS_CSS) as demo:
    gr.Markdown("# 🏎️ Faster Qwen3 TTS Commentary Processor")
    gr.Markdown("Transform your commentary text files into individual MP3 files optimized for the 3060 Ti.")
    
    with gr.Tab("Generation"):
        with gr.Row():
            with gr.Column():
                input_file = gr.File(label="Commentary Text File", file_types=[".txt"], value=default_input_file)
                output_folder = gr.Textbox(label="Output Folder", value=default_output_folder, lines=1, max_lines=2)
                
                with gr.Row():
                    model_size = gr.Dropdown(
                        label="Model Size", 
                        choices=["0.6B-Base", "1.7B-Base"], 
                        value="1.7B-Base"
                    )
                    cloning_mode = gr.Checkbox(label="Fast Mode (xvec_only)", value=False)
                
                with gr.Row():
                    temp = gr.Slider(minimum=0.1, maximum=1.5, value=0.9, step=0.1, label="Temperature")
                    start_line = gr.Number(label="Start Line Index", value=0, precision=0)
                    limit = gr.Number(label="Limit (0 for all)", value=5, precision=0)
                
                with gr.Row():
                    lock_seed = gr.Checkbox(label="Lock Seed", value=False,
                                            info="Off: each line gets a new random seed (shown in the log). On: every line uses the seed below.")
                    seed = gr.Number(label="Seed", value=42, precision=0, minimum=0)
                
                with gr.Row():
                    generate_btn = gr.Button("🚀 Start Processing", variant="primary", scale=3)
                    unload_btn = gr.Button("⏏️ Unload Model", scale=1)
                with gr.Row():
                    speaker_info = gr.Markdown("Select a file to see detected speakers...")
                    refresh_spk_btn = gr.Button("🔄 Refresh Status", size="sm")
            
            with gr.Column():
                run_status = gr.Markdown("")
                gr.HTML(REVIEW_HTML)
                with gr.Accordion("Processing Log", open=False):
                    status_log = gr.Textbox(show_label=False, interactive=False, lines=12, max_lines=12)
                clips_json = gr.JSON(value=[], elem_classes=["tts-hidden"])
                seen_state = gr.State({})
        
        input_file.change(refresh_speakers, inputs=[input_file], outputs=[speaker_info])
        refresh_spk_btn.click(refresh_speakers, inputs=[input_file], outputs=[speaker_info])
        generate_btn.click(run_tts_process, 
                          inputs=[input_file, output_folder, model_size, cloning_mode, temp, start_line, limit, lock_seed, seed], 
                          outputs=[status_log])
        unload_btn.click(unload_model, queue=False)
        gr.Timer(1.0).tick(poll_runner, inputs=[seen_state], outputs=[status_log, run_status, clips_json, seen_state],
                           show_progress="hidden", queue=False)  # off the queue: a queued tick takes 2-3 s
        clips_json.change(None, inputs=[clips_json], js=CLIPS_JS)

    with gr.Tab("Manage Library"):
        gr.Markdown("### 📚 Manage Voices & Transcripts")
        gr.Markdown("Select an existing voice to add or edit its transcript for **High Quality** mode.")
        with gr.Row():
            with gr.Column():
                voice_select = gr.Dropdown(label="Select Voice from Library", choices=list_voices_dropdown())
                refresh_library_btn = gr.Button("🔄 Refresh Library List", size="sm")
                transcript_editor = gr.Textbox(label="Voice Transcript", placeholder="Write what the speaker says in their reference WAV...", lines=8)
                style_editor = gr.Textbox(label="Style / Accent (optional)", info=STYLE_HINT, lines=2)
                save_transcript_btn = gr.Button("💾 Save Transcript", variant="primary")
            with gr.Column():
                library_status = gr.Markdown("")

        voice_select.change(load_voice_transcript, inputs=[voice_select], outputs=[transcript_editor, style_editor])
        save_transcript_btn.click(save_voice_transcript, inputs=[voice_select, transcript_editor, style_editor], outputs=[library_status])
        refresh_library_btn.click(lambda: gr.update(choices=list_voices_dropdown()), outputs=[voice_select])

    with gr.Tab("Load New Voice"):
        gr.Markdown("### 🎙️ Upload Reference Audio")
        gr.Markdown(f"Adds a new voice to your library (copied into `{VOICES_DIR}`). Providing a transcript enables High-Quality ICL mode.")
        with gr.Row():
            with gr.Column():
                new_wav = gr.File(label="Select WAV File", file_types=[".wav"])
                gr.Markdown("---")
                gr.Markdown("**Transcript (Optional)**")
                new_txt_input = gr.Textbox(label="Type Transcript Here", placeholder="Paste the text from the audio clip...", lines=5)
                new_txt_file = gr.File(label="OR Upload Transcript File", file_types=[".txt"])
                new_style = gr.Textbox(label="Style / Accent (optional)", info=STYLE_HINT, lines=2)
                upload_btn = gr.Button("📤 Upload to Library", variant="primary")
            with gr.Column():
                upload_status = gr.Textbox(label="Upload Status", interactive=False)

        upload_btn.click(upload_voice, inputs=[new_wav, new_txt_file, new_txt_input, new_style], outputs=[upload_status, voice_select])

    with gr.Tab("Speaker Mappings"):
        gr.Markdown("Edit the manual mappings between name tags and WAV files.")
        mapping_text = gr.Code(value=get_mappings(), language="json", label="speaker_mappings.json")
        with gr.Row():
            refresh_map_btn = gr.Button("🔄 Refresh from File")
            save_map_btn = gr.Button("💾 Save Mappings", variant="primary")

        save_status = gr.Markdown("")

        refresh_map_btn.click(get_mappings, outputs=[mapping_text])
        save_map_btn.click(save_mappings, inputs=[mapping_text], outputs=[save_status])

    with gr.Tab("Voices Preview"):
        gr.Markdown(f"Current files in `{VOICES_DIR}` folder.")

        def list_voices():
            if os.path.exists(VOICES_DIR):
                files = [f for f in os.listdir(VOICES_DIR) if f.endswith(".wav")]
                return "\n".join([f"- {f}" for f in files])
            return "Folder not found."

        voices_list = gr.Markdown(list_voices())
        refresh_voices = gr.Button("Refresh List")
        refresh_voices.click(list_voices, outputs=[voices_list])

    # Check the speakers of the file the launcher handed over straight away
    demo.load(refresh_speakers, inputs=[input_file], outputs=[speaker_info])
    # Set up the review card (buttons, keys, auto-play) before the first clip arrives
    demo.load(None, js=CLIPS_JS)

async def page_action(request: Request):
    clip_action((await request.json()).get("action"))
    return {"ok": True}


def launch(**kwargs):
    demo.queue().launch(prevent_thread_lock=True, **kwargs)
    # Keep and Redo post straight here: sent as Gradio events they wait for a later timer tick
    demo.app.add_api_route("/tts-action", page_action, methods=["POST"])
    demo.block_thread()


if __name__ == "__main__":
    launch(inbrowser=True, allowed_paths=[os.path.abspath(default_output_folder)])
