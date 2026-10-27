"""
Message prioritization, cooldowns, and quiet-zone timing scheduler for AI Race Engineer.
"""

import time
import heapq
from typing import Optional, Tuple, Callable
from dataclasses import dataclass, field

from race.events import EventPriority, RaceEvent
from telemetry.collector import TelemetrySnapshot
from utils.logging import logger


@dataclass(order=True)
class QueuedMessage:
    priority_order: int  # Negated priority so higher priority pops first
    timestamp: float
    text: str = field(compare=False)
    event_priority: EventPriority = field(compare=False)
    event_type: str = field(compare=False)
    emotion: str = field(default="calm and composed", compare=False)
    delivery: str = field(default="", compare=False)


class MessageScheduler:
    def __init__(self, quiet_zone_braking: bool = True, min_interval_sec: float = 3.5):
        self.quiet_zone_braking = quiet_zone_braking
        self.min_interval_sec = min_interval_sec

        self._queue: list[QueuedMessage] = []
        self._last_speech_time = 0.0
        self._quiet_hold_since: Optional[float] = None

    def push(
        self,
        text: str,
        priority: EventPriority,
        event_type: str = "general",
        emotion: str = "calm and composed",
        delivery: str = ""
    ):
        """Queues a message with priority, intended vocal emotion, and delivery notes."""
        ev_str = event_type.value if hasattr(event_type, "value") else str(event_type)

        # Purge older pending yellow flags or rival incidents when a new caution is queued
        track_caution_events = {"flag_yellow", "flag_double_yellow", "rival_incident"}
        if ev_str in track_caution_events:
            self._queue = [
                m for m in self._queue
                if (m.event_type.value if hasattr(m.event_type, "value") else str(m.event_type)) not in track_caution_events
            ]
            heapq.heapify(self._queue)

        # Cap queue depth to maximum 2 pending messages to prevent pileups
        if len(self._queue) >= 2:
            self._queue.sort(key=lambda m: (m.priority_order, -m.timestamp))
            self._queue.pop()
            heapq.heapify(self._queue)

        order = -int(priority)
        msg = QueuedMessage(
            priority_order=order,
            timestamp=time.time(),
            text=text,
            event_priority=priority,
            event_type=event_type,
            emotion=emotion,
            delivery=delivery
        )
        heapq.heappush(self._queue, msg)

    MAX_QUIET_HOLD_SEC = 5.0

    def is_in_quiet_zone(self, snapshot: Optional[TelemetrySnapshot]) -> bool:
        """True in heavy braking or while loaded up in a corner (don't talk to him mid-corner)."""
        if not self.quiet_zone_braking or not snapshot:
            return False
        # Heavy braking zone: brake pedal pressed hard (> 70%) while at speed (> 70 km/h)
        if snapshot.brake > 0.70 and snapshot.speed_kmh > 70.0:
            return True
        # Committed cornering: > 1.2 g lateral (fall back to big steering input if no accel data)
        acc = getattr(snapshot, "local_accel_ms2", None)
        if acc and any(acc):
            return abs(acc[0]) > 1.2 * 9.81 and snapshot.speed_kmh > 60.0
        return abs(snapshot.steering) > 0.35 and snapshot.speed_kmh > 60.0

    def poll_next_message(self, snapshot: Optional[TelemetrySnapshot]) -> Optional[QueuedMessage]:
        """
        Pulls the highest priority message if conditions allow (timing & quiet zone).
        Returns None if no message is ready or driver is busy.
        """
        if not self._queue:
            return None

        now = time.time()
        top = self._queue[0]
        ev_str = top.event_type.value if hasattr(top.event_type, "value") else str(top.event_type)

        # Discard stale messages before dispatching (even for CRITICAL)
        track_caution_events = {"flag_yellow", "flag_double_yellow", "rival_incident"}
        if ev_str in track_caution_events:
            max_age = 6.0
        elif ev_str in {"battle_entered", "position_lost", "position_gained"}:
            max_age = 8.0
        else:
            max_age = 45.0

        if (now - top.timestamp) > max_age:
            logger.info(f"Dropping stale radio message [{ev_str}] ({now - top.timestamp:.1f}s old)")
            heapq.heappop(self._queue)
            return None

        # CRITICAL messages bypass timing and quiet zones immediately once staleness check passes
        if top.event_priority == EventPriority.CRITICAL:
            heapq.heappop(self._queue)
            self._last_speech_time = now
            return top

        # Non-critical messages wait for a calmer moment (braking zone / committed corner), max a few seconds
        if self.is_in_quiet_zone(snapshot):
            if self._quiet_hold_since is None:
                self._quiet_hold_since = now
            if now - self._quiet_hold_since < self.MAX_QUIET_HOLD_SEC:
                return None
        self._quiet_hold_since = None

        # Non-critical messages must respect minimum spacing
        if (now - self._last_speech_time) < self.min_interval_sec:
            return None

        heapq.heappop(self._queue)
        self._last_speech_time = now
        return top

    def clear(self):
        self._queue.clear()
