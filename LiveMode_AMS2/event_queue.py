import threading
import time
import logging
from dataclasses import dataclass
from typing import List, Tuple, Dict

logger = logging.getLogger(__name__)

@dataclass
class LiveEvent:
    category: str
    message: str
    timestamp: float
    position: int = 0
    sub_type: str = ""
    is_filler: bool = False

CATEGORY_WEIGHTS: Dict[str, float] = {
    "FINISH": 10.0,
    "ACCIDENT": 15.0,
    "SAFETY_CAR": 15.0,
    "OVERTAKE": 20.0,
    "PENALTY": 25.0,
    "BATTLE_side_by_side": 30.0,
    "FLAG": 35.0,
    "SESSION": 40.0,
    "BATTLE_drafting": 45.0,
    "PIT": 50.0,
    "LEADERBOARD": 60.0
}

CATEGORY_TTLS: Dict[str, float] = {
    "FINISH": 30.0,
    "SAFETY_CAR": 25.0,
    "ACCIDENT": 20.0,
    "PENALTY": 20.0,
    "LEADERBOARD": 3600.0,  # Never expire while audio is playing
    "SESSION": 20.0,
    "OVERTAKE": 15.0,
    "FLAG": 15.0,
    "BATTLE_side_by_side": 12.0,
    "PIT": 12.0,
    "BATTLE_drafting": 10.0
}

def get_weight(category: str, sub_type: str) -> float:
    key = f"{category}_{sub_type}" if category == "BATTLE" and sub_type else category
    return CATEGORY_WEIGHTS.get(key, 50.0)

def get_ttl(category: str, sub_type: str) -> float:
    key = f"{category}_{sub_type}" if category == "BATTLE" and sub_type else category
    return CATEGORY_TTLS.get(key, 15.0)

class EventQueue:
    def __init__(self, ttl_multiplier: float = 1.0):
        self._lock = threading.Lock()
        self._events: List[LiveEvent] = []  # Commentary queue (LEADERBOARD only)
        self._context_events: List[LiveEvent] = []  # Context buffer
        self._dropped_events: List[LiveEvent] = []
        self.ttl_multiplier = ttl_multiplier
        self._scores: Dict[int, float] = {}

    def add_event(self, event: LiveEvent) -> None:
        with self._lock:
            self._add_to_queue(event)
            self._add_to_context(event)

    def _add_to_context(self, event: LiveEvent) -> None:
        self._context_events.append(event)
        if len(self._context_events) > 30:
            self._context_events.pop(0)

    def get_context_events(self, max_count: int = 20) -> List[LiveEvent]:
        with self._lock:
            events = self._context_events[-max_count:]
            self._context_events.clear()
            return events

    def _add_to_queue(self, event: LiveEvent) -> None:
        now = time.time()
        merged = False
        for i, existing in enumerate(self._events):
            if existing.category == event.category and (now - existing.timestamp) <= 3.0:
                words1 = set(w.lower().strip(".,:;!?'\"()") for w in existing.message.split() if len(w) > 2)
                words2 = set(w.lower().strip(".,:;!?'\"()") for w in event.message.split() if len(w) > 2)
                
                if words1.intersection(words2):
                    # Merge them: keep the newer one, append context from old one's message
                    event.message = f"{event.message} | Prev: {existing.message}"
                    if event.position == 0 and existing.position > 0:
                        event.position = existing.position
                    
                    self._events[i] = event
                    merged = True
                    break
        
        if not merged:
            self._events.append(event)
            
        self._re_rank_unlocked(now)

    def _calculate_score(self, event: LiveEvent, now: float) -> float:
        base = get_weight(event.category, event.sub_type)
        position_bonus = event.position * 2 if event.position > 0 else 10.0
        age_penalty = (now - event.timestamp) * 5.0
        return base + position_bonus + age_penalty

    def _re_rank_unlocked(self, now: float) -> None:
        scored_events = []
        for event in self._events:
            ttl = get_ttl(event.category, event.sub_type) * self.ttl_multiplier
            if now - event.timestamp > ttl:
                self._dropped_events.append(event)
            else:
                score = self._calculate_score(event, now)
                scored_events.append((score, event))
                
        if len(self._dropped_events) > 20:
            self._dropped_events = self._dropped_events[-20:]
            
        scored_events.sort(key=lambda x: x[0])
        self._events = [e for _, e in scored_events]
        self._scores = {id(e): s for s, e in scored_events}

    def re_rank(self) -> None:
        with self._lock:
            self._re_rank_unlocked(time.time())

    def pop_top(self, batch_range: float = 5.0) -> List[LiveEvent]:
        with self._lock:
            self._re_rank_unlocked(time.time())
            if not self._events:
                return []
                
            top_event = self._events[0]
            top_score = self._scores[id(top_event)]
            
            batch = []
            remaining = []
            
            for event in self._events:
                if self._scores[id(event)] <= top_score + batch_range:
                    batch.append(event)
                else:
                    remaining.append(event)
            
            self._events = remaining
            return batch

    def get_top_n(self, n: int = 5) -> List[Tuple[LiveEvent, float]]:
        with self._lock:
            self._re_rank_unlocked(time.time())
            return [(event, self._scores[id(event)]) for event in self._events[:n]]
            
    def get_dropped_events(self, max_count: int = 10) -> List[LiveEvent]:
        with self._lock:
            return self._dropped_events[-max_count:]

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._events) == 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._context_events.clear()
            self._dropped_events.clear()
            self._scores.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._events)
