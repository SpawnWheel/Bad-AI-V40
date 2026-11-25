"""
Position after everyone's stops.

In a race with a mandatory stop the order on the road lies until everybody has stopped: a car that hasn't
stopped yet is effectively a pit stop further back than it looks. Each car's time behind the leader (from
the gap clock) plus the stop it still has to make gives the order the race will settle into.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CarAfterStops:
    name: str
    road_pos: int
    gap_to_leader: float        # seconds on the road now
    still_to_stop: bool
    owed: float                 # seconds this car still has to lose in the pits
    effective: float            # gap_to_leader + owed
    virtual_pos: int = 0


def order_after_stops(cars: List[dict], stop_loss: float) -> List[CarAfterStops]:
    """
    cars: dicts with name, position, gap_to_leader (s), still_to_stop (bool) and optionally
    pit_elapsed (s already spent in the pit lane on a stop happening right now).
    """
    out: List[CarAfterStops] = []
    for c in cars:
        owed = 0.0
        if c.get("pit_elapsed") is not None:
            owed = max(0.0, stop_loss - float(c["pit_elapsed"]))      # mid-stop: part of the loss is already in the gap
        elif c.get("still_to_stop"):
            owed = stop_loss
        gap = float(c["gap_to_leader"])
        out.append(CarAfterStops(c["name"], int(c["position"]), gap, bool(c.get("still_to_stop")), owed, gap + owed))
    out.sort(key=lambda x: (x.effective, x.road_pos))
    for i, x in enumerate(out, start=1):
        x.virtual_pos = i
    return out


def describe_for_driver(order: List[CarAfterStops], me: str, stop_loss: float, window: int = 2) -> str:
    """Context block: our road vs settled position and the cars around us once the stops are done."""
    mine: Optional[CarAfterStops] = next((x for x in order if x.name == me), None)
    if mine is None:
        return ""
    pending = [x for x in order if x.still_to_stop and x.name != me]
    lines = ["[POSITION AFTER STOPS — COMPUTED, mandatory stops; an estimate - AI/online stop times vary]",
             f"  On the road P{mine.road_pos} -> once everyone has made their stop ~P{mine.virtual_pos}"
             f" (stop worth ~{stop_loss:.0f}s; {len(pending)} other car{'s' if len(pending) != 1 else ''} still to stop"
             + (", you still have to stop too" if mine.still_to_stop else "") + ")"]
    i = mine.virtual_pos - 1
    near = order[max(0, i - window): i + window + 1]
    for x in near:
        if x.name == me:
            continue
        rel = x.effective - mine.effective
        where = f"{abs(rel):.1f}s {'ahead of' if rel < 0 else 'behind'}"
        tag = " (still to stop)" if x.still_to_stop else ""
        lines.append(f"  After stops P{x.virtual_pos}: {x.name}{tag}, ~{where} you (on the road now P{x.road_pos})")
    return "\n".join(lines)
