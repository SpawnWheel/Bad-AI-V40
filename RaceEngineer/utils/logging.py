"""
Lightweight structured logging for AMS2 AI Race Engineer.
Safe across Windows consoles with CP1252 / UTF-8.
"""

import sys
from datetime import datetime

# Configure UTF-8 with replace on Windows if supported
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ANSI Color codes for terminal output
COLORS = {
    "DEBUG": "\033[36m",     # Cyan
    "INFO": "\033[32m",      # Green
    "RADIO": "\033[35m",     # Magenta (Driver/Radio)
    "ENGINEER": "\033[33m",  # Yellow/Gold (Engineer speech)
    "WARNING": "\033[93m",   # Bright Yellow
    "ERROR": "\033[91m",     # Red
    "CRITICAL": "\033[41m\033[37m", # Red Background, White text
    "RESET": "\033[0m",
    "BOLD": "\033[1m"
}


class Logger:
    def __init__(self, name: str = "Engineer", verbose: bool = True):
        self.name = name
        self.verbose = verbose

    def _safe_print(self, stream, text: str):
        try:
            print(text, file=stream, flush=True)
        except UnicodeEncodeError:
            clean = text.encode("ascii", errors="replace").decode("ascii")
            print(clean, file=stream, flush=True)

    def _format(self, level: str, msg: str, color_key: str = None) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        col = COLORS.get(color_key or level, "")
        reset = COLORS["RESET"]
        return f"{col}[{timestamp}] [{level.ljust(8)}]{reset} {msg}"

    def debug(self, msg: str):
        if self.verbose:
            self._safe_print(sys.stdout, self._format("DEBUG", msg))

    def info(self, msg: str):
        self._safe_print(sys.stdout, self._format("INFO", msg))

    def radio(self, msg: str):
        """Driver radio input message."""
        self._safe_print(sys.stdout, self._format("DRIVER", f"[RADIO] {msg}", "RADIO"))

    def speech(self, msg: str):
        """Engineer radio spoken message."""
        self._safe_print(sys.stdout, self._format("ENGINEER", f"[RADIO] {msg}", "ENGINEER"))

    def warning(self, msg: str):
        self._safe_print(sys.stdout, self._format("WARN", f"[!] {msg}", "WARNING"))

    def error(self, msg: str):
        self._safe_print(sys.stderr, self._format("ERROR", f"[X] {msg}", "ERROR"))

    def critical(self, msg: str):
        self._safe_print(sys.stderr, self._format("CRITICAL", f"[ALERT] {msg}", "CRITICAL"))


logger = Logger()
