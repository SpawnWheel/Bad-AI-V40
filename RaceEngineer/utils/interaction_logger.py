"""
Interaction logging for AI Race Engineer.
Logs all prompts sent to Gemini and responses returned to a dedicated file for development & inspection.
"""

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

LOGS_DIR = Path("logs")
INTERACTIONS_LOG_FILE = LOGS_DIR / "ai_interactions.log"


class InteractionLogger:
    def __init__(self, log_dir: Path = LOGS_DIR):
        self.log_dir = log_dir
        self.latest_log_file = self.log_dir / "ai_interactions.log"
        self._current_session_label: str = "SESSION"
        self._timestamp_str: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"ai_interactions_{self._current_session_label}_{self._timestamp_str}.log"
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def switch_session(self, session_name: str):
        """
        Creates a new dedicated log file for the specified session (e.g. QUALIFY, COUNTDOWN, RACE).
        """
        clean_name = session_name.replace("SESSION_", "").replace("RACESTATE_", "").upper().strip()
        if not clean_name:
            clean_name = "SESSION"

        if clean_name == self._current_session_label:
            return

        self._ensure_dir()
        self._current_session_label = clean_name
        self._timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"ai_interactions_{clean_name}_{self._timestamp_str}.log"

        header = [
            "=" * 80,
            f"NEW SESSION LOG: {clean_name} started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 80,
            "\n"
        ]
        try:
            with open(self.log_file, "a", encoding="utf-8", errors="replace") as f:
                f.write("\n".join(header))
        except Exception:
            pass

    def log_interaction(
        self,
        interaction_type: str,
        driver_input: str,
        session_info: str,
        telemetry_context: str,
        full_prompt_sent: str,
        ai_response_text: str,
        emotion: str,
        latency_sec: Optional[float] = None
    ):
        """
        Appends a fully detailed record of an AI interaction to the current session log
        and the unified latest ai_interactions.log file.
        """
        self._ensure_dir()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        latency_str = f"{latency_sec:.2f}s" if latency_sec is not None else "N/A"

        separator = "=" * 80
        sub_sep = "-" * 80

        entry = [
            separator,
            f"[{now_str}] AI INTERACTION: {interaction_type.upper()}",
            f"LATENCY: {latency_str} | EMOTION: {emotion}",
            sub_sep,
            f"[SESSION INFO]: {session_info}",
            f"[DRIVER INPUT / TRIGGER]: {driver_input}",
            sub_sep,
            "[FULL PROMPT SENT TO GEMINI 3.8 FLASH]:",
            full_prompt_sent.strip(),
            sub_sep,
            "[RESPONSE RETURNED BY GEMINI 3.8 FLASH]:",
            ai_response_text.strip(),
            separator,
            "\n"
        ]
        entry_text = "\n".join(entry)

        # Write to session-specific log file
        try:
            with open(self.log_file, "a", encoding="utf-8", errors="replace") as f:
                f.write(entry_text)
        except Exception:
            pass


interaction_logger = InteractionLogger()
