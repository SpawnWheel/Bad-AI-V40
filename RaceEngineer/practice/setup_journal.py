"""
Setup journal: what the engineer knows about a car (what can be adjusted, drivetrain, driver
preferences) and the history of setup work at a car + track (runs, changes, conclusions).

AMS2 does not expose the setup through shared memory and its setup files are encrypted, so the
engineer learns the car the way a real one would: from the driver ("camber is fixed on this car",
"I dropped the fronts by 0.05") and from the data. Persisted in data/setup_journal.json so the next
session at this track starts where the last one ended.
"""

import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from utils.logging import logger

DEFAULT_PATH = Path("data/setup_journal.json")


class SetupJournal:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = threading.Lock()
        self._db: Dict = {"cars": {}, "sessions": {}}
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    d.setdefault("cars", {})
                    d.setdefault("sessions", {})
                    self._db = d
        except Exception as e:
            logger.warning(f"Could not load setup journal: {e}")

    # ------------------------------------------------------------------ persistence
    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._db, f, indent=1)
        except Exception as e:
            logger.error(f"Could not save setup journal: {e}")

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def session_key(car: str, track: str, variation: str) -> str:
        return f"{(car or '').strip()}@{(track or '').strip()}|{(variation or '').strip()}"

    def _car(self, car: str) -> Dict:
        return self._db["cars"].setdefault((car or "unknown").strip(), {"facts": [], "drivetrain": "", "driver_prefs": []})

    def _sess(self, key: str) -> Dict:
        return self._db["sessions"].setdefault(key, {"runs": [], "changes": [], "conclusions": []})

    # ------------------------------------------------------------------ car knowledge
    def add_car_fact(self, car: str, fact: str):
        fact = (fact or "").strip().strip('"')
        if len(fact) < 4:
            return
        with self._lock:
            rec = self._car(car)
            low = fact.lower()
            if any(low == f["text"].lower() for f in rec["facts"]):
                return
            rec["facts"].append({"text": fact[:200], "when": self._now()})
            del rec["facts"][:-25]
            self._save()
        logger.info(f"[SETUP JOURNAL] Car fact for {car}: {fact}")

    def set_drivetrain(self, car: str, dt: str):
        if dt and dt != "unknown":
            with self._lock:
                rec = self._car(car)
                if rec.get("drivetrain") != dt:
                    rec["drivetrain"] = dt
                    self._save()

    def car_facts(self, car: str) -> List[str]:
        rec = self._db["cars"].get((car or "unknown").strip()) or {}
        return [f["text"] for f in rec.get("facts", [])]

    def drivetrain(self, car: str) -> str:
        rec = self._db["cars"].get((car or "unknown").strip()) or {}
        return rec.get("drivetrain") or "unknown"

    # ------------------------------------------------------------------ session history
    def add_change(self, key: str, text: str, run_number: int):
        text = (text or "").strip().strip('"')
        if len(text) < 3:
            return
        with self._lock:
            s = self._sess(key)
            if s["changes"] and s["changes"][-1]["text"].lower() == text.lower():
                return
            s["changes"].append({"text": text[:200], "before_run": run_number, "when": self._now()})
            del s["changes"][:-40]
            self._save()
        logger.info(f"[SETUP JOURNAL] Change before run #{run_number}: {text}")

    def add_conclusion(self, key: str, text: str):
        text = (text or "").strip().strip('"')
        if len(text) < 4:
            return
        with self._lock:
            s = self._sess(key)
            if s["conclusions"] and s["conclusions"][-1]["text"].lower() == text.lower():
                return
            s["conclusions"].append({"text": text[:240], "when": self._now()})
            del s["conclusions"][:-20]
            self._save()
        logger.info(f"[SETUP JOURNAL] Conclusion: {text}")

    def add_run(self, key: str, run: Dict):
        with self._lock:
            s = self._sess(key)
            s["runs"].append(run)
            del s["runs"][:-20]
            self._save()

    def changes_for_run(self, key: str, run_number: int, session_start: str = "") -> List[str]:
        s = self._db["sessions"].get(key) or {}
        return [c["text"] for c in s.get("changes", [])
                if c.get("before_run") == run_number and (not session_start or c.get("when", "") >= session_start)]

    def history_summary(self, key: str, session_start: str = "") -> List[str]:
        """Lines describing earlier sessions at this car+track (before session_start)."""
        s = self._db["sessions"].get(key) or {}
        out = []
        old_changes = [c for c in s.get("changes", []) if not session_start or c.get("when", "") < session_start]
        old_concl = [c for c in s.get("conclusions", []) if not session_start or c.get("when", "") < session_start]
        old_runs = [r for r in s.get("runs", []) if not session_start or r.get("when", "") < session_start]
        if old_runs:
            best = min((r.get("best", 0) for r in old_runs if r.get("best")), default=0)
            if best:
                out.append(f"  Best lap here in earlier sessions: {best:.3f}s")
        for c in old_changes[-6:]:
            out.append(f"  Earlier change ({c['when']}): {c['text']}")
        for c in old_concl[-5:]:
            out.append(f"  Earlier conclusion ({c['when']}): {c['text']}")
        return out


setup_journal = SetupJournal()
