"""The activity panel: everything the background tools print, in one place instead of console windows."""
import time
import tkinter as tk
from tkinter import ttk

from . import theme as t
from .processes import parse_ansi

ALL = None
STATE_COLOURS = {"starting": t.WARNING, "running": t.SUCCESS, "finished": t.CONSOLE_DIM,
                 "failed": "#ff6b6b", "stopped": t.CONSOLE_DIM}
SHOW_LINES = 1500


class ActivityPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.CONSOLE_BG)
        self.app = app
        self.pm = app.pm
        self.selected = ALL
        self.all_lines = []        # [(process, text)] for the "Everything" view
        self.rows = []             # listbox index -> process (index 0 = everything)

        bar = tk.Frame(self, bg="#1d2027", height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="Activity", bg="#1d2027", fg="#ffffff", font=(t.FONT, 10, "bold")).pack(side="left", padx=(14, 8))
        self.summary = tk.Label(bar, text="", bg="#1d2027", fg=t.CONSOLE_DIM, font=(t.FONT, 9))
        self.summary.pack(side="left")
        for text, cmd in (("Hide", app.toggle_activity), ("Clear finished", self.clear_finished),
                          ("Stop selected", self.stop_selected)):
            b = tk.Button(bar, text=text, command=cmd, bg="#1d2027", fg=t.CONSOLE_FG, activebackground="#2b2f38",
                          activeforeground="#ffffff", relief="flat", bd=0, padx=10, font=(t.FONT, 9), cursor="hand2")
            b.pack(side="right", padx=2, pady=4)
            if text == "Stop selected":
                self.stop_btn = b

        body = tk.Frame(self, bg=t.CONSOLE_BG)
        body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(body, width=34, bg="#1a1d23", fg=t.CONSOLE_FG, selectbackground=t.CONSOLE_SEL,
                                  selectforeground="#ffffff", activestyle="none", relief="flat", bd=0,
                                  highlightthickness=0, font=(t.FONT, 9))
        self.listbox.pack(side="left", fill="y", padx=(0, 1))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        self.text = tk.Text(body, bg=t.CONSOLE_BG, fg=t.CONSOLE_FG, insertbackground=t.CONSOLE_FG, relief="flat",
                            bd=0, highlightthickness=0, font=(t.FONT_MONO, 9), wrap="word", padx=10, pady=6,
                            state="disabled", selectbackground="#334")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview, style="Console.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("dim", foreground=t.CONSOLE_DIM)
        self.text.tag_configure("bold", font=(t.FONT_MONO, 9, "bold"))
        self._colour_tags = set()

        self.pm.listeners.append(self._on_event)
        self._rebuild_list()
        self._tick()

    # ------------------------------------------------------------------ process list

    def _row_text(self, proc):
        if proc is ALL:
            running = len(self.pm.running())
            return f"  ◆  Everything{f'  ({running} running)' if running else ''}"
        state = {"running": "", "starting": "starting", "finished": "done", "failed": "failed",
                 "stopped": "stopped"}[proc.state]
        mins, secs = divmod(int(proc.elapsed()), 60)
        when = f"{mins}:{secs:02d}"
        return f"  ●  {proc.label}   {state + ' · ' if state else ''}{when}"

    def _rebuild_list(self):
        keep = self.selected
        self.rows = [ALL] + list(reversed(self.pm.processes))
        self.listbox.delete(0, "end")
        for i, proc in enumerate(self.rows):
            self.listbox.insert("end", self._row_text(proc))
            colour = t.CONSOLE_FG if proc is ALL else STATE_COLOURS[proc.state]
            self.listbox.itemconfigure(i, fg=colour)
        index = self.rows.index(keep) if keep in self.rows else 0
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self._update_summary()

    def _refresh_rows(self):
        for i, proc in enumerate(self.rows):
            if proc is ALL or proc.running:
                self.listbox.delete(i)
                self.listbox.insert(i, self._row_text(proc))
                self.listbox.itemconfigure(i, fg=t.CONSOLE_FG if proc is ALL else STATE_COLOURS[proc.state])
        if self.selected in self.rows:
            self.listbox.selection_set(self.rows.index(self.selected))

    def _update_summary(self):
        running = self.pm.running()
        self.summary.config(text=("  ·  ".join(p.label for p in running) if running else "Nothing running"))
        sel = self.selected
        self.stop_btn.config(state="normal" if sel is not ALL and sel.running else "disabled")

    def _tick(self):
        if self.winfo_exists():
            self._refresh_rows()
            self.after(1000, self._tick)

    def _on_select(self, _):
        sel = self.listbox.curselection()
        if not sel:
            return
        proc = self.rows[sel[0]]
        if proc is not self.selected:
            self.selected = proc
            self._render()
        self._update_summary()

    def show(self, proc):
        """Selects a process (or ALL) and shows its output."""
        self.selected = proc
        self._rebuild_list()
        self._render()

    # ------------------------------------------------------------------ output

    def _render(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if self.selected is ALL:
            for proc, text in self.all_lines[-SHOW_LINES:]:
                self._insert(text, proc)
        else:
            for text, _ in self.selected.lines[-SHOW_LINES:]:
                self._insert(text)
        self.text.configure(state="disabled")
        self.text.see("end")

    def _tag(self, colour, bold):
        name = f"c{colour}{'b' if bold else ''}"
        if name not in self._colour_tags:
            self._colour_tags.add(name)
            self.text.tag_configure(name, foreground=colour or t.CONSOLE_FG,
                                    font=(t.FONT_MONO, 9, "bold") if bold else (t.FONT_MONO, 9))
        return name

    def _insert(self, text, proc=None):
        if proc is not None:
            self.text.insert("end", f"{proc.label[:18]:<18} │ ", "dim")
        for segment, colour, bold in parse_ansi(text):
            self.text.insert("end", segment, self._tag(colour, bold) if (colour or bold) else ())
        self.text.insert("end", "\n")

    def _append(self, text, proc, replace=False):
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        if replace:
            self.text.delete("end-2l linestart", "end-1c")
        self._insert(text, proc)
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > SHOW_LINES + 300:
            self.text.delete("1.0", f"{lines - SHOW_LINES}.0")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _on_event(self, kind, proc, payload):
        if kind in ("line", "replace"):
            replaced_in_all = kind == "replace" and bool(self.all_lines) and self.all_lines[-1][0] is proc
            if replaced_in_all:
                self.all_lines[-1] = (proc, payload)
            else:
                self.all_lines.append((proc, payload))
                if len(self.all_lines) > 6000:
                    del self.all_lines[:1000]
            if self.selected is ALL:
                self._append(payload, proc, replace=replaced_in_all)
            elif self.selected is proc:
                self._append(payload, None, replace=(kind == "replace"))
        elif kind in ("added", "exit"):
            self._rebuild_list()

    def clear_finished(self):
        self.pm.clear_finished()
        live = set(self.pm.processes)
        self.all_lines = [(p, x) for p, x in self.all_lines if p in live]
        if self.selected is not ALL and self.selected not in live:
            self.selected = ALL
        self._rebuild_list()
        self._render()

    def stop_selected(self):
        if self.selected is not ALL and self.selected.running:
            self.pm.stop(self.selected)
