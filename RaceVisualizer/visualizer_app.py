"""Race Visualizer window: pick a race, preview the replay, export the video.

    python visualizer_app.py ["<race folder>"]
"""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import racefiles  # noqa: E402
import visualizer as viz  # noqa: E402

BG = "#0b1323"
CARD = "#131e33"
BORDER = "#22314f"
TEXT = "#eef2f8"
MUTED = "#a6b2c8"
ACCENT = "#e0352b"
INPUT = "#0e182b"
FONT = "Segoe UI"

SIZES = [("1280 x 720 (recommended)", 2), ("640 x 360 (smallest file)", 1), ("1920 x 1080", 3)]
PREVIEW_SCALE = 1.5


def race_folders():
    """Race folders with recorded live timing, newest first."""
    out = []
    root = racefiles.RACES_DIR
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        folder = os.path.join(root, name)
        if os.path.isfile(racefiles.race_file(folder, "timing")):
            out.append(folder)
    out.sort(key=lambda f: os.path.basename(f), reverse=True)
    return out


class App:
    def __init__(self, root, folder=None):
        self.root = root
        root.title("Race Visualizer")
        root.configure(bg=BG)
        root.minsize(1000, 720)
        self.replay = None
        self.playing = False
        self.exporting = False
        self.cancel = False
        self.folder = None
        self.photo = None
        self._play_start = None

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=INPUT, background=CARD, foreground=TEXT, arrowcolor=TEXT,
                        bordercolor=BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", TEXT)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=INPUT, bordercolor=BORDER)
        style.configure("Horizontal.TScale", background=BG, troughcolor=INPUT)

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=20, pady=(16, 6))
        tk.Label(top, text="Race Visualizer", bg=BG, fg=TEXT, font=(FONT, 20, "bold")).pack(anchor="w")
        tk.Label(top, text="The whole race in about 90 seconds: every car on its own row, its distance behind the "
                           "leader, pit stops, retirements and the result. Tyre life is estimated from the laps on "
                           "each set.", bg=BG, fg=MUTED, font=(FONT, 9), wraplength=940, justify="left"
                 ).pack(anchor="w")

        form = tk.Frame(root, bg=CARD, padx=16, pady=12, highlightthickness=1, highlightbackground=BORDER)
        form.pack(fill="x", padx=20, pady=6)
        self.folders = race_folders()
        self.race_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.sub_var = tk.StringVar()
        self.secs_var = tk.StringVar(value="90")
        self.size_var = tk.StringVar(value=SIZES[0][0])

        self._label(form, "Race", 0, 0)
        row = tk.Frame(form, bg=CARD)
        row.grid(row=1, column=0, columnspan=3, sticky="w")
        self.race_box = ttk.Combobox(row, textvariable=self.race_var, state="readonly", width=78,
                                     values=[os.path.basename(f) for f in self.folders])
        self.race_box.pack(side="left")
        self.race_box.bind("<<ComboboxSelected>>", lambda e: self.load(self.folders[self.race_box.current()]))
        self._button(row, "Other folder…", self.browse).pack(side="left", padx=(8, 0))

        self._label(form, "Title", 2, 0)
        self._entry(form, self.title_var, 46).grid(row=3, column=0, sticky="w", padx=(0, 16))
        self._label(form, "Subtitle", 2, 1)
        self._entry(form, self.sub_var, 24).grid(row=3, column=1, sticky="w", padx=(0, 16))
        self._label(form, "Length (seconds)", 2, 2)
        self._entry(form, self.secs_var, 8).grid(row=3, column=2, sticky="w")
        for var in (self.title_var, self.sub_var):
            var.trace_add("write", lambda *a: self._apply_titles())
        self.secs_var.trace_add("write", lambda *a: self._length_changed())

        # Preview
        pv = tk.Frame(root, bg=BG)
        pv.pack(fill="both", expand=True, padx=20, pady=6)
        self.canvas = tk.Label(pv, bg="#000000", width=int(viz.W * PREVIEW_SCALE), height=int(viz.H * PREVIEW_SCALE))
        self.canvas.pack()
        controls = tk.Frame(pv, bg=BG)
        controls.pack(fill="x", pady=(6, 0))
        self.play_btn = self._button(controls, "▶  Play", self.toggle_play)
        self.play_btn.pack(side="left")
        self.pos_var = tk.DoubleVar(value=0)
        self.scale = ttk.Scale(controls, from_=0, to=90, variable=self.pos_var, command=lambda v: self._scrub())
        self.scale.pack(side="left", fill="x", expand=True, padx=10)
        self.time_lbl = tk.Label(controls, text="0:00 / 1:30", bg=BG, fg=MUTED, font=(FONT, 9), width=12)
        self.time_lbl.pack(side="left")

        # Export
        ex = tk.Frame(root, bg=CARD, padx=16, pady=12, highlightthickness=1, highlightbackground=BORDER)
        ex.pack(fill="x", padx=20, pady=(6, 16))
        self._label(ex, "Video size", 0, 0)
        ttk.Combobox(ex, textvariable=self.size_var, state="readonly", width=26,
                     values=[s[0] for s in SIZES]).grid(row=1, column=0, sticky="w", padx=(0, 16))
        self.export_btn = self._button(ex, "Export video…", self.export, primary=True)
        self.export_btn.grid(row=1, column=1, sticky="w")
        self.progress = ttk.Progressbar(ex, length=260, mode="determinate")
        self.progress.grid(row=1, column=2, sticky="w", padx=16)
        self.status = tk.Label(ex, text="", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.status.grid(row=1, column=3, sticky="w")
        self.open_row = tk.Frame(ex, bg=CARD)
        self.open_row.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ex.columnconfigure(3, weight=1)

        root.protocol("WM_DELETE_WINDOW", self.close)
        start = folder if folder and os.path.isdir(folder) else (self.folders[0] if self.folders else None)
        if start:
            self.load(start)
        else:
            self.status.config(text="No races with recorded live timing were found.")

    # ------------------------------------------------------------ widgets

    def _label(self, parent, text, row, col):
        tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=row, column=col, sticky="w",
                                                                            pady=(6, 3))

    def _entry(self, parent, var, width):
        return tk.Entry(parent, textvariable=var, width=width, bg=INPUT, fg=TEXT, insertbackground=TEXT,
                        relief="flat", font=(FONT, 10), highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT)

    def _button(self, parent, text, cmd, primary=False):
        bg = ACCENT if primary else CARD
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=TEXT, activebackground=BORDER,
                         activeforeground=TEXT, relief="flat", bd=0, padx=14, pady=5, font=(FONT, 10, "bold"
                         if primary else "normal"), highlightthickness=1, highlightbackground=BORDER, cursor="hand2")

    # ------------------------------------------------------------ loading

    def browse(self):
        path = filedialog.askdirectory(parent=self.root, initialdir=racefiles.RACES_DIR,
                                       title="Choose a race folder")
        if path:
            self.load(os.path.normpath(path))

    def load(self, folder):
        self.stop()
        self.status.config(text="Loading the race…")
        self.root.update_idletasks()
        try:
            race = viz.Race(folder)
        except Exception as e:
            messagebox.showerror("Race Visualizer", f"Could not load this race:\n{e}", parent=self.root)
            self.status.config(text="")
            return
        self.folder = folder
        self.race = race
        if folder not in self.folders:
            self.folders.insert(0, folder)
            self.race_box.config(values=[os.path.basename(f) for f in self.folders])
        self.race_box.current(self.folders.index(folder))
        self._building = True
        self.title_var.set(race.track)
        self.sub_var.set(race.subtitle)
        self._building = False
        self._rebuild()
        cars = len(race.cars)
        laps = self.replay.total_laps
        self.status.config(text=f"{cars} cars" + (f", {laps} laps" if laps else "") + ".")
        for w in self.open_row.winfo_children():
            w.destroy()

    def _seconds(self):
        try:
            return max(30.0, min(600.0, float(self.secs_var.get())))
        except ValueError:
            return None

    def _rebuild(self):
        secs = self._seconds() or 90.0
        self.replay = viz.Replay(self.race, secs, self.title_var.get(), self.sub_var.get())
        self.scale.config(to=secs)
        self.pos_var.set(min(self.pos_var.get(), secs))
        self.show_frame()

    def _apply_titles(self):
        if self.replay and not getattr(self, "_building", False):
            self.replay.title = viz.plain(self.title_var.get())
            self.replay.subtitle = viz.plain(self.sub_var.get())
            self.show_frame()

    def _length_changed(self):
        if self.replay and self._seconds() and abs(self._seconds() - self.replay.seconds) > 0.01:
            self._rebuild()

    # ------------------------------------------------------------ preview

    def show_frame(self):
        if not self.replay:
            return
        v = self.pos_var.get()
        img = self.replay.render(int(v * viz.FPS))
        img = img.resize((int(viz.W * PREVIEW_SCALE), int(viz.H * PREVIEW_SCALE)), Image.NEAREST)
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.config(image=self.photo, width=img.width, height=img.height)
        total = self.replay.seconds
        self.time_lbl.config(text=f"{int(v // 60)}:{int(v % 60):02d} / {int(total // 60)}:{int(total % 60):02d}")

    def _scrub(self):
        if not self.playing:
            self.show_frame()

    def toggle_play(self):
        if self.playing:
            self.stop()
            return
        if not self.replay:
            return
        if self.pos_var.get() >= self.replay.seconds - 0.1:
            self.pos_var.set(0)
        self.playing = True
        self.play_btn.config(text="❚❚  Pause")
        self._play_start = time.perf_counter() - self.pos_var.get()
        self._tick()

    def stop(self):
        self.playing = False
        self.play_btn.config(text="▶  Play")

    def _tick(self):
        if not self.playing:
            return
        v = time.perf_counter() - self._play_start
        if v >= self.replay.seconds:
            self.pos_var.set(self.replay.seconds)
            self.show_frame()
            self.stop()
            return
        self.pos_var.set(v)
        self.show_frame()
        self.root.after(15, self._tick)

    # ------------------------------------------------------------ export

    def export(self):
        if self.exporting:
            self.cancel = True
            return
        if not self.replay:
            return
        if self._seconds() is None:
            messagebox.showerror("Race Visualizer", "The length must be a number of seconds.", parent=self.root)
            return
        out = filedialog.asksaveasfilename(parent=self.root, title="Save the video", defaultextension=".mp4",
                                           initialdir=self.folder, initialfile=viz.OUTPUT_NAME,
                                           filetypes=[("MP4 video", "*.mp4")])
        if not out:
            return
        self.stop()
        scale = dict(SIZES)[self.size_var.get()]
        self.exporting, self.cancel = True, False
        self.export_btn.config(text="Cancel")
        for w in self.open_row.winfo_children():
            w.destroy()
        self.status.config(text="Rendering…")
        replay = self.replay

        def progress(done, total):
            self.root.after(0, lambda: self.progress.config(value=100 * done / total))

        def work():
            err, ok = None, False
            try:
                ok = viz.export(replay, out, scale, progress, cancel=lambda: self.cancel)
            except Exception as e:
                err = str(e)
            self.root.after(0, lambda: self._exported(out, ok, err))

        threading.Thread(target=work, daemon=True).start()

    def _exported(self, out, ok, err):
        self.exporting = False
        self.export_btn.config(text="Export video…")
        if err:
            self.status.config(text="Export failed.")
            messagebox.showerror("Race Visualizer", err, parent=self.root)
            return
        if not ok:
            self.status.config(text="Cancelled.")
            self.progress.config(value=0)
            try:
                os.remove(out)
            except OSError:
                pass
            return
        size = os.path.getsize(out) / 1e6
        self.status.config(text=f"Saved {os.path.basename(out)} ({size:.1f} MB).")
        print(f"Saved {out} ({size:.1f} MB)")
        self._button(self.open_row, "▶  Watch it", lambda: os.startfile(out), primary=True).pack(side="left")
        self._button(self.open_row, "Show in folder",
                     lambda: os.startfile(os.path.dirname(out))).pack(side="left", padx=(8, 0))

    def close(self):
        self.cancel = True
        self.playing = False
        self.root.destroy()


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    App(root, folder)
    root.mainloop()


if __name__ == "__main__":
    main()
