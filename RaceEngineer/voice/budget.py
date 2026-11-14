"""
Daily voice (TTS) request budget.

The Gemini TTS quota is a number of requests per day (about 200 on this key), shared by practice,
qualifying and every race of the evening. This counts every TTS request the engineer makes and keeps
the count on disk, so a restart or a second session the same day still knows how much is left.

Google resets daily quotas at midnight Pacific time, so the "day" here is the Pacific-time date.
Only the engineer's own requests are counted - anything else using the same key is invisible here.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils.logging import logger


def _quota_day(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        local = now.astimezone(timezone(timedelta(hours=-8)))  # no tz database: PST, at most an hour out
    return local.date().isoformat()


class DailyRequestBudget:
    def __init__(self, daily_limit: int = 200, path: str = "data/tts_usage.json"):
        self.daily_limit = max(1, int(daily_limit))
        self.path = Path(path)
        self._lock = threading.Lock()
        self._day = _quota_day()
        self._count = 0
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("day") == self._day:
                self._count = int(data.get("count", 0))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"TTS budget file unreadable ({e}); starting from 0.")

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"day": self._day, "count": self._count}), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not save TTS budget: {e}")

    def _roll_day(self):
        day = _quota_day()
        if day != self._day:
            self._day = day
            self._count = 0

    # ------------------------------------------------------------------ API
    def record(self, n: int = 1):
        with self._lock:
            self._roll_day()
            self._count += n
            self._save()
            count = self._count
        if count in (int(self.daily_limit * 0.5), int(self.daily_limit * 0.75), int(self.daily_limit * 0.9), self.daily_limit):
            logger.warning(f"[TTS BUDGET] {count}/{self.daily_limit} voice requests used today.")

    @property
    def used(self) -> int:
        with self._lock:
            self._roll_day()
            return self._count

    @property
    def remaining(self) -> int:
        return max(0, self.daily_limit - self.used)

    @property
    def fraction_used(self) -> float:
        return min(1.0, self.used / float(self.daily_limit))

    def summary(self) -> str:
        return f"{self.used}/{self.daily_limit} voice requests used today"
