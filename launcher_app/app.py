"""The main window: header navigation, the screens, the activity panel and everything that runs."""
import json
import os
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox

import racefiles
from . import pipeline
from . import theme as t
from .activity import ALL, ActivityPanel
from .home import HomeScreen
from .library import LibraryScreen
from .processes import ProcessManager, python_exe
from .race import RaceScreen
from .settings import ENGINEER_DIR, SettingsScreen

APP_DIR = racefiles.APP_DIR
CONFIG_FILE = os.path.join(APP_DIR, "launcher_config.json")
ERROR_LOG = os.path.join(APP_DIR, "launcher_errors.log")

LOGGERS = {"AMS2": "Logger_AMS2/ams2_logger.py", "RaceRoom": "Logger_Raceroom/raceroom_logger.py"}
CORNER_MAPPERS = {"AMS2": "CornerMapper_AMS2/corner_mapper.py", "RaceRoom": "CornerMapper_Raceroom/corner_mapper_raceroom.py"}
ENGINEER_SIMS = {"AMS2": "ams2", "RaceRoom": "raceroom"}
NAV = (("home", "Race"), ("library", "My races"), ("settings", "Settings"))


class App:
    def __init__(self, root):
        self.root = root
        root.title("BAD AI - Race Replays")
        root.geometry("1200x840")
        root.minsize(1000, 680)
        root.configure(bg=t.BG)
        root.report_callback_exception = self._report_error
        t.setup_styles(root)

        self.config_data = self._load_config()
        self.settings = self.config_data.setdefault("global_settings", {})
        self.settings.pop("live_mode", None)  # settings of the old live commentator
        self.pm = ProcessManager()
        self.pm.listeners.append(self._on_process_event)
        self.session = None
        self.screen = None
        self.screen_name = None
        self.activity_visible = False

        self._build_header()
        self.status_bar = tk.Label(root, text="", bg=t.BG, fg=t.MUTED, font=(t.FONT, 9), anchor="w", padx=36)
        self.status_bar.pack(side="bottom", fill="x", pady=(0, 6))
        self.paned = tk.PanedWindow(root, orient="vertical", sashwidth=6, bg=t.BORDER, bd=0, sashrelief="flat",
                                    showhandle=False)
        self.paned.pack(fill="both", expand=True)
        self.content = tk.Frame(self.paned, bg=t.BG)
        self.paned.add(self.content, stretch="always", minsize=320)
        self.activity = ActivityPanel(self.paned, self)

        self._toast_job = None
        self.show("home")
        self._pump()
        self._tick()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ settings

    def _load_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=4)
        except OSError as e:
            messagebox.showerror("Settings", f"Could not save {CONFIG_FILE}:\n{e}")

    def videos_folder(self):
        return self.settings.get("videos_folder") or os.path.join(os.path.expanduser("~"), "Videos")

    def ai_env(self):
        s = self.settings
        env = {"GEMINI_MAX_OUTPUT_TOKENS": str(s.get("gemini_max_output_tokens", 65536)),
               "GEMINI_THINKING_LEVEL": str(s.get("gemini_thinking_level", "HIGH"))}
        if s.get("gemini_api_key"):
            env["GEMINI_API_KEY"] = s["gemini_api_key"]
        if s.get("gemini_model"):
            env["GEMINI_MODEL_NAME"] = s["gemini_model"]
        return env

    def prompt_choice(self, sid):
        step = pipeline.STEPS[sid]
        prompts = pipeline.prompts_for(step)
        chosen = (self.settings.get("prompts") or {}).get(sid)
        if chosen in prompts:
            return chosen
        if not prompts:
            return ""
        return max(prompts, key=lambda p: os.path.getmtime(pipeline.prompt_path(step, p)))

    def set_prompt_choice(self, sid, name):
        self.settings.setdefault("prompts", {})[sid] = name
        self.save_settings()

    # ------------------------------------------------------------------ header and navigation

    def _build_header(self):
        bar = tk.Frame(self.root, bg=t.HEADER, height=58, highlightthickness=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=t.BORDER, height=1).pack(fill="x")
        brand = tk.Frame(bar, bg=t.HEADER)
        brand.pack(side="left", padx=(36, 30))
        tk.Label(brand, text="BAD", bg=t.HEADER, fg=t.ACCENT, font=(t.FONT, 17, "bold")).pack(side="left")
        tk.Label(brand, text="AI", bg=t.HEADER, fg=t.TEXT, font=(t.FONT, 17, "bold")).pack(side="left")
        tk.Label(brand, text="   race replays", bg=t.HEADER, fg=t.DIM, font=(t.FONT, 10)).pack(side="left", pady=(5, 0))

        self.nav_buttons = {}
        for name, text in NAV:
            holder = tk.Frame(bar, bg=t.HEADER)
            holder.pack(side="left", fill="y", padx=4)
            b = tk.Button(holder, text=text, command=lambda n=name: self.show(n), bg=t.HEADER, fg=t.MUTED,
                          activebackground=t.HEADER, activeforeground=t.TEXT, relief="flat", bd=0,
                          font=(t.FONT, 10, "bold"), padx=10, cursor="hand2", highlightthickness=0)
            b.pack(fill="y", expand=True)
            line = tk.Frame(holder, bg=t.HEADER, height=3)
            line.pack(fill="x", side="bottom")
            self.nav_buttons[name] = (b, line)

        right = tk.Frame(bar, bg=t.HEADER)
        right.pack(side="right", padx=(0, 30))
        self.activity_btn = tk.Button(right, text="Activity", command=self.toggle_activity, bg=t.NEUTRAL_SOFT,
                                      fg=t.TEXT, activebackground=t.BORDER, relief="flat", bd=0, padx=12, pady=5,
                                      font=(t.FONT, 9, "bold"), cursor="hand2", highlightthickness=0)
        self.activity_btn.pack(side="right", pady=14)
        self.live_label = tk.Label(right, text="", bg=t.HEADER, fg="#ff6a5f", font=(t.FONT, 9, "bold"), cursor="hand2")
        self.live_label.pack(side="right", padx=(0, 14))
        self.live_label.bind("<Button-1>", lambda e: self.show("home"))

    def _style_nav(self):
        for name, (b, line) in self.nav_buttons.items():
            on = name == self.screen_name or (name == "library" and self.screen_name == "race")
            b.config(fg=t.TEXT if on else t.MUTED)
            line.config(bg=t.ACCENT if on else t.HEADER)

    def show(self, name, **kw):
        if self.screen is not None:
            if isinstance(self.screen, SettingsScreen) and name != "settings":
                if not self.screen.save(quiet=True):
                    return
            if isinstance(self.screen, RaceScreen):
                self.screen.flush_notes()
            self.screen.destroy()
        self.screen_name = name
        if name == "home":
            self.screen = HomeScreen(self.content, self)
        elif name == "library":
            self.screen = LibraryScreen(self.content, self)
        elif name == "race":
            self.screen = RaceScreen(self.content, self, kw["folder"])
        else:
            self.screen = SettingsScreen(self.content, self)
        self.screen.pack(fill="both", expand=True)
        self._style_nav()

    def open_race(self, folder):
        self.show("race", folder=folder)

    def toast(self, message, seconds=8):
        self.status_bar.config(text=message)
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(seconds * 1000, lambda: self.status_bar.config(text=""))

    # ------------------------------------------------------------------ activity

    def toggle_activity(self):
        if self.activity_visible:
            self.paned.forget(self.activity)
            self.activity_visible = False
        else:
            self.paned.add(self.activity, stretch="never", minsize=140, height=260)
            self.activity_visible = True

    def show_activity(self, proc=None):
        if not self.activity_visible:
            self.toggle_activity()
        self.activity.show(proc or ALL)

    def _pump(self):
        self.pm.poll()
        self.root.after(100, self._pump)

    def _tick(self):
        running = len(self.pm.running())
        self.activity_btn.config(text=f"Activity  ●  {running} running" if running else "Activity",
                                 fg=t.SUCCESS if running else t.TEXT)
        s = self.session
        if s and s.get("state") == "recording":
            self.live_label.config(text=f"●  RECORDING  {t.format_duration(time.time() - s['rec_started'])}")
        elif s:
            self.live_label.config(text=f"●  LIVE  {s['sim']}")
        else:
            self.live_label.config(text="")
        self.root.after(1000, self._tick)

    def _on_process_event(self, kind, proc, payload):
        if kind in ("exit", "added") and isinstance(self.screen, RaceScreen) and proc.race == self.screen.folder:
            self.screen.refresh()

    # ------------------------------------------------------------------ live session

    def start_session(self, sim):
        if self.session:
            self.show("home")
            return
        # The start screen's switches can leave either one off: recorder only (e.g. to log a race replay)
        # or engineer only (no replays wanted)
        start_engineer = self.settings.get("start_engineer", True)
        start_recorder = self.settings.get("start_recorder", True) or not start_engineer
        engineer_off = None if start_engineer else "Off - recorder only"
        if start_engineer and not self.settings.get("gemini_api_key"):
            if not start_recorder:
                messagebox.showwarning("No Gemini API key", "The race engineer needs a Gemini API key.\n\n"
                                                            "Add it in Settings, then start again.")
                return
            if not messagebox.askyesno("No Gemini API key",
                                       "The race engineer needs a Gemini API key (Settings).\n\n"
                                       "Start only the race recorder for now?"):
                return
            start_engineer = False
            engineer_off = "Off - add your Gemini API key in Settings"

        engineer = logger = None
        if start_engineer:
            env = {"GEMINI_API_KEY": self.settings["gemini_api_key"], "RACE_ENGINEER_SIM": ENGINEER_SIMS[sim]}
            engineer = self.pm.start("Race engineer", [python_exe(), "main.py"], cwd=ENGINEER_DIR, env=env,
                                     kind="engineer", graceful=True)
        if start_recorder:
            script = os.path.join(APP_DIR, LOGGERS[sim])
            logger = self.pm.start(f"{sim} race recorder", [python_exe(), script], cwd=os.path.dirname(script),
                                   kind="logger", graceful=True)
            logger.on_status.append(self._on_logger_status)
        self.session = dict(sim=sim, engineer=engineer, logger=logger, state="waiting_sim", notes=[],
                            started=time.time(), engineer_off=engineer_off,
                            logger_off=None if start_recorder else "Off - engineer only, races are not saved")
        for proc in (engineer, logger):
            if proc:
                proc.on_exit.append(self._on_session_exit)
        self.show("home")

    def _on_logger_status(self, proc, st):
        s = self.session
        if not s or proc is not s["logger"]:
            return
        state = st.get("state")
        if state == "recording":
            s.update(state="recording", track=st.get("track"), folder=st.get("folder"), rec_started=time.time())
        elif state == "saved":
            s["notes"].append(("saved", f"Saved: race at {t.pretty_track(st.get('track'))} ({t.minutes(st.get('seconds'))})",
                               st.get("folder")))
            s["state"] = "waiting"
            self.toast(f"Race saved: {t.pretty_track(st.get('track'))}")
        elif state == "discarded":
            s["notes"].append(("discarded", f"Race at {t.pretty_track(st.get('track'))} was shorter than "
                                            f"{racefiles.MIN_RACE_SECONDS // 60} minutes - not kept", None))
            s["state"] = "waiting"
        elif state in ("waiting", "waiting_sim"):
            s.update(state=state, session=st.get("session"))
        if isinstance(self.screen, HomeScreen):
            self.screen.refresh()

    def _on_session_exit(self, proc):
        s = self.session
        if not s:
            return
        procs = [p for p in (s["engineer"], s["logger"]) if p]
        if any(p.running for p in procs):
            if not proc.stopping:
                self.toast(f"{proc.label} stopped unexpectedly - see Activity.")
                self.show_activity(proc)
            if isinstance(self.screen, HomeScreen):
                self.screen.refresh()
            return
        saved = sum(1 for n in s["notes"] if n[0] == "saved")
        self.session = None
        self.toast(f"Session ended. {saved} race{'s' if saved != 1 else ''} saved." if saved else "Session ended.")
        if isinstance(self.screen, HomeScreen):
            self.show("library" if saved else "home")

    def stop_session(self):
        s = self.session
        if not s:
            return
        s["stopping"] = True
        for proc in (s["engineer"], s["logger"]):
            if proc:
                self.pm.stop(proc)
        if isinstance(self.screen, HomeScreen):
            self.screen.refresh()

    def launch_corner_mapper(self, sim):
        script = os.path.join(APP_DIR, CORNER_MAPPERS[sim])
        if not os.path.exists(script):
            messagebox.showerror("Corner Mapper", f"Not found:\n{script}")
            return
        self.pm.start(f"Corner Mapper ({sim})", [python_exe(), script], cwd=APP_DIR, kind="tool")
        self.toast("Corner Mapper is opening in its own window.")

    # ------------------------------------------------------------------ replay steps

    def run_step(self, folder, sid, on_done=None):
        step = pipeline.STEPS[sid]
        running = self.pm.find_step(folder, sid)
        if running:
            self.show_activity(running)
            return
        if (step.get("prompt_folder") or sid == "camera") and not self.settings.get("gemini_api_key"):
            messagebox.showwarning(step["title"], "This step uses the AI. Add your Gemini API key in Settings first.")
            return
        state, detail = pipeline.step_status(folder, sid)
        if state == "waiting":
            self.toast(f"{step['title']}: {detail}.")
            return
        if step["kind"] == "auto":
            pipeline.keep_history(folder, sid)
        args, cwd, env = pipeline.command(folder, sid)
        env.update(self.ai_env())
        if step.get("prompt_folder"):
            env["GEMINI_PROMPT_FILE"] = pipeline.prompt_path(step, self.prompt_choice(sid))
        started = time.time()
        proc = self.pm.start(step["title"], [python_exe()] + args, cwd=cwd, env=env,
                             kind="step" if step["kind"] == "auto" else "tool", race=folder, step_id=sid)
        proc.on_exit.append(lambda p: self._step_finished(p, folder, sid, started, on_done))
        if step["kind"] == "auto":
            self.show_activity(proc)
            self.toast(f"{step['title']} is running…")
        else:
            self.toast(f"{step['title']} is opening in its own window. Its messages are under Activity.")

    def _step_finished(self, proc, folder, sid, started, on_done):
        step = pipeline.STEPS[sid]
        made = pipeline.output_time(pipeline.output_path(folder, step))
        ok = bool(made and made >= started - 1)
        if proc.stopping:
            self.toast(f"{step['title']} was stopped.")
        elif step["kind"] == "auto":
            if ok:
                self.toast(f"✓ {step['title']} finished.")
            else:
                self.toast(f"{step['title']} did not produce its file - see Activity for what went wrong.")
                self.show_activity(proc)
        elif ok:
            self.toast(f"✓ {step['title']} saved its result.")
        if on_done:
            on_done(ok and not proc.stopping)

    def run_chain(self, folder, sids):
        """Runs automatic steps one after another, stopping at the first that fails."""
        sids = list(sids)
        if not sids:
            return

        def next_step(ok=True):
            if not ok or not sids:
                if ok:
                    self.toast("✓ All steps finished.")
                return
            self.run_step(folder, sids.pop(0), on_done=next_step)

        next_step()

    def copy_prompt(self, folder, sid):
        """Copies the full prompt an AI step would send (prompt + race notes + input) to the clipboard."""
        step = pipeline.STEPS[sid]
        name = self.prompt_choice(sid)
        try:
            with open(pipeline.prompt_path(step, name), "r", encoding="utf-8") as f:
                prompt = f.read()
        except OSError as e:
            messagebox.showerror("Copy prompt", f"Could not read the prompt:\n{e}")
            return
        notes = racefiles.notes_text(racefiles.race_file(folder, "race_notes"))
        extra = f"\n\nProject Specific Instructions:\n{notes}" if notes else ""
        data = ""
        inputs = [k for k in step["args"] if k != "OUTPUT"]
        source = pipeline.input_path(folder, inputs[0]) if inputs else None
        if source and os.path.isfile(source):
            with open(source, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
        text = f"{prompt}{extra}\n\n{data}"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.toast(f"Copied the {step['title']} prompt - {len(text):,} characters.")

    # ------------------------------------------------------------------ closing

    def on_close(self):
        running = self.pm.running()
        if not running:
            self.root.destroy()
            return
        others = [p for p in running if p.kind in ("step", "tool")]
        if others:
            names = "\n".join(f"•  {p.label}" for p in others)
            if not messagebox.askyesno("Close BAD AI", f"These are still running and will be closed too:\n\n{names}\n\n"
                                                       "Close anyway?"):
                return
        if isinstance(self.screen, RaceScreen):
            self.screen.flush_notes()
        cover = tk.Frame(self.root, bg=t.BG)
        cover.place(relx=0, rely=0, relwidth=1, relheight=1)
        t.label(cover, "Stopping the race engineer and saving your race…", size=14, weight="bold",
                anchor="center").place(relx=0.5, rely=0.45, anchor="center")
        self.root.update()

        def work():
            self.pm.stop_all()
            self.root.after(0, self.root.destroy)

        threading.Thread(target=work, daemon=True).start()

    def _report_error(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            with open(ERROR_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}")
        except OSError:
            pass
        print(text)
        messagebox.showerror("Something went wrong", f"{val}\n\nDetails were saved to {ERROR_LOG}")


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays (must come before Tk starts)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
