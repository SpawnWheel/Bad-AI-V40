"""Gemini generation for the camera controllers (AMS2 and RaceRoom).

Streams the response so the model's thinking appears live in a "Gemini Thinking" window,
and returns the final answer text to the caller on the Tk thread.
"""
import json
import threading
import tkinter as tk
from tkinter import ttk

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "HIGH"
THINKING_LEVELS = ["HIGH", "MEDIUM", "LOW", "MINIMAL", "NONE"]


def extract_json(text):
    """The sequence JSON from a model answer, whether bare, in a ``` block, or with text around it."""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif text.count("```") >= 2:
        text = text.split("```")[1].split("```")[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"No JSON found in Gemini's answer: {text[:200]!r}")
        return json.loads(text[start:end + 1])


def supports_thinking_level(model_name):
    return any(k in model_name.lower() for k in ["gemini-3", "3.8", "3.7", "3.6", "3.5", "3.1"])


def add_model_settings(parent, settings, on_change):
    """Model and thinking-level pickers, saved into the controller's settings dict."""
    row = ttk.Frame(parent); row.pack(fill=tk.X, pady=2)
    ttk.Label(row, text="Model:").pack(side=tk.LEFT, padx=2)
    model_var = tk.StringVar(value=settings.get("gemini_model", DEFAULT_MODEL))
    ttk.Combobox(row, textvariable=model_var, width=18,
                 values=["gemini-3.8-flash", "gemini-3.8-pro", "gemini-3.6-flash"]).pack(side=tk.LEFT, padx=2)
    ttk.Label(row, text="Thinking:").pack(side=tk.LEFT, padx=(6, 2))
    level_var = tk.StringVar(value=settings.get("gemini_thinking_level", DEFAULT_THINKING))
    ttk.Combobox(row, textvariable=level_var, values=THINKING_LEVELS, state="readonly", width=8).pack(side=tk.LEFT, padx=2)

    def changed(*_):
        settings["gemini_model"] = model_var.get().strip() or DEFAULT_MODEL
        settings["gemini_thinking_level"] = level_var.get()
        on_change()
    model_var.trace_add("write", changed)
    level_var.trace_add("write", changed)


class ThinkingWindow:
    def __init__(self, root, title):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.title(title)
        self.win.geometry("720x560")
        self.win.attributes("-topmost", True)
        self.status = tk.StringVar(value="Starting...")
        tk.Label(self.win, textvariable=self.status, anchor="w", bg="#222", fg="#0f0",
                 font=("Consolas", 9, "bold"), padx=6, pady=4).pack(fill=tk.X)
        frame = ttk.Frame(self.win); frame.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(frame, wrap="word", font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        scroll = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text.tag_configure("answer", foreground="#9cdcfe")

    def append(self, chunk, tag=None):
        def do():
            if not self.win.winfo_exists():
                return
            at_end = self.text.yview()[1] > 0.98
            self.text.insert(tk.END, chunk, tag)
            if at_end:
                self.text.see(tk.END)
        self.root.after(0, do)

    def set_status(self, msg):
        self.root.after(0, lambda: self.status.set(msg))


def generate(root, api_key, prompt, model_name, thinking_level, max_output_tokens, on_done, on_error):
    """Runs the request in a background thread; on_done(answer_text) / on_error(exc) are called on the Tk thread."""
    level = thinking_level.upper()
    thinking = supports_thinking_level(model_name) and level != "NONE"
    label = f"{model_name} | Thinking: {level if thinking else 'OFF'}"
    window = ThinkingWindow(root, f"Gemini Thinking - {label}")
    window.set_status(f"Generating with {label} ...")
    print(f"Using model: {label}, Max Output Tokens: {max_output_tokens:,}")

    def work():
        try:
            client = genai.Client(api_key=api_key)
            config = types.GenerateContentConfig(
                max_output_tokens=max_output_tokens,
                thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_level=level) if thinking else None,
            )
            answer = []
            in_answer = False
            for chunk in client.models.generate_content_stream(model=model_name, contents=prompt, config=config):
                if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                    continue
                for part in chunk.candidates[0].content.parts:
                    if not part.text:
                        continue
                    if part.thought:
                        window.append(part.text)
                        print(part.text, end="", flush=True)
                    else:
                        if not in_answer:
                            in_answer = True
                            window.append("\n\n===== ANSWER =====\n", "answer")
                            window.set_status(f"{label} - writing the sequence...")
                        answer.append(part.text)
                        window.append(part.text, "answer")
            window.set_status(f"Done - {label}")
            text = "".join(answer)
            root.after(0, lambda: on_done(text))
        except Exception as e:
            window.set_status(f"Failed - {e}")
            root.after(0, lambda: on_error(e))

    threading.Thread(target=work, daemon=True).start()
