"""
Fuel tracking, race fuel planning and the fuel ledger for AMS2.

* FuelTracker  - measured burn per lap (lap 1, pit in/out laps and refuel laps excluded) with an
                 optional seed from earlier sessions (practice), and deterministic analysis:
                 range, surplus/deficit to the flag, the pit window, the planned pit lap and how many
                 litres to add at that stop.
* FuelLedger   - accounting: fuel at the start, every refuel, total used, per-lap burn history.
                 This is what answers "how much fuel did I use in total?" after the race.

All distances are fractional laps. A pit stop "on lap k" means the car enters the pits at the END of
lap k (so a call made at the start of lap k is "box this lap").
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class FuelStatus:
    current_liters: float
    capacity_liters: float
    level_pct: float
    avg_per_lap: float
    laps_remaining_fuel: float
    laps_remaining_race: float          # fractional laps from here to the flag
    fuel_deficit_laps: float            # positive = deficit
    target_consumption: Optional[float] # L/lap to make it without pitting
    needs_pit_stop: bool
    is_critical: bool                   # <= 2.2 laps of fuel
    is_empty_risk: bool                 # <= 1.1 laps of fuel
    is_urgent: bool = False             # low fuel AND cannot reach the flag
    fuel_surplus_laps: float = 0.0      # positive = surplus
    fuel_required_liters: float = 0.0   # net litres short of the flag (no margin)
    recommended_refuel_liters: float = 0.0  # litres to add at the PLANNED stop (incl. margin)
    fuel_status_label: str = "FUEL SAFE TO END"
    pit_window_lap: Optional[int] = None      # planned pit lap (enter pits at the END of this lap)
    earliest_safe_pit_lap: Optional[int] = None
    latest_safe_pit_lap: Optional[int] = None
    recommended_pit_window_str: Optional[str] = None
    fuel_needed_liters: float = 0.0     # legacy alias of recommended_refuel_liters
    laps_counted: int = 0
    burn_source: str = "default"        # "measured" | "seeded (practice)" | "default"
    fuel_at_planned_stop: float = 0.0
    stops_needed: int = 0
    margin_laps: float = 0.0
    finish_lap: int = 0
    margin_liters: float = 1.0          # litres planned to be left in the tank at the flag
    distance_note: str = ""             # e.g. "leader's pending stop included" / "borderline: planned +1 lap"


class FuelTracker:
    DEFAULT_BURN = 3.5

    def __init__(self, margin_laps: float = 0.0, pit_lap_preference: str = "latest", arrival_reserve_laps: float = 0.6,
                 margin_liters: float = 1.0):
        self._lap_start_fuel: Optional[float] = None
        self._lap_consumptions: List[float] = []
        self._last_recorded_lap = -1
        self._lap_dirty = False
        self.seed_burn: Optional[float] = None
        # Safety net at the flag = the larger of a fixed litre amount and a number of laps of fuel.
        self.margin_laps = max(0.0, float(margin_laps or 0.0))
        self.margin_liters = max(0.0, float(margin_liters or 0.0))
        self.arrival_reserve_laps = float(arrival_reserve_laps)
        self.pit_lap_preference = (pit_lap_preference or "latest").lower()
        self.declared_pit_lap: Optional[int] = None

    def reset(self):
        self._lap_start_fuel = None
        self._lap_consumptions.clear()
        self._last_recorded_lap = -1
        self._lap_dirty = False
        self.declared_pit_lap = None

    def seed(self, burn_l_per_lap: Optional[float]):
        """Burn rate learned earlier (practice / previous sessions) used until the race provides its own."""
        if burn_l_per_lap and 0.3 < burn_l_per_lap < 30:
            self.seed_burn = float(burn_l_per_lap)

    def mark_lap_dirty(self):
        """Current lap had a pit visit / refuel -> don't use it for the burn average."""
        self._lap_dirty = True

    def on_lap_started(self, lap_number: int, current_fuel_liters: float, was_pit_lap: bool = False):
        """Called when crossing the start/finish line to begin a new lap."""
        if self._lap_start_fuel is not None and lap_number > self._last_recorded_lap:
            completed_lap = self._last_recorded_lap
            consumed = self._lap_start_fuel - current_fuel_liters
            dirty = self._lap_dirty or was_pit_lap
            # Lap 1 ignored (standing start / formation skew); pit laps ignored; sanity bounds.
            if completed_lap >= 2 and not dirty and 0.5 <= consumed <= 15.0:
                self._lap_consumptions.append(consumed)
                if len(self._lap_consumptions) > 3:
                    self._lap_consumptions.pop(0)
        self._lap_start_fuel = current_fuel_liters
        self._last_recorded_lap = lap_number
        self._lap_dirty = False

    def get_avg_consumption(self) -> float:
        """Running 3-lap average; falls back to the seeded burn, then to 3.5 L/lap."""
        if self._lap_consumptions:
            return sum(self._lap_consumptions) / len(self._lap_consumptions)
        if self.seed_burn:
            return self.seed_burn
        return self.DEFAULT_BURN

    def margin_l(self, avg: Optional[float] = None) -> float:
        avg = self.get_avg_consumption() if avg is None else avg
        return max(self.margin_liters, avg * self.margin_laps)

    def burn_source(self) -> str:
        if self._lap_consumptions:
            return "measured"
        if self.seed_burn:
            return "seeded (practice/earlier sessions)"
        return "default"

    # ------------------------------------------------------------------ analysis
    def analyze(
        self,
        current_liters: float,
        capacity_liters: float,
        total_laps: int,
        current_lap: int,
        event_time_remaining_sec: float = -1.0,
        lap_pace_sec: float = 90.0,
        laps_to_go: Optional[float] = None,
        lap_fraction: float = 0.0,
        finish_lap: int = 0,
    ) -> FuelStatus:
        """
        laps_to_go / lap_fraction / finish_lap come from race_distance.compute_race_distance().
        Without them we use the legacy whole-lap estimate (lap race: laps from the start of the current lap;
        timed race: clock / pace + 1).
        """
        avg = self.get_avg_consumption()
        capacity = max(1.0, capacity_liters)
        level_pct = (current_liters / capacity) * 100.0
        laps_remaining_fuel = current_liters / avg if avg > 0 else 999.0
        margin_l = self.margin_l(avg)

        if laps_to_go is not None:
            laps_remaining_race = max(0.0, float(laps_to_go))
            d_now = max(0.0, (current_lap - 1) + max(0.0, min(0.9999, lap_fraction)))
            f_end = finish_lap if finish_lap > 0 else (d_now + laps_remaining_race)
        else:
            if total_laps > 0:
                laps_remaining_race = float(max(0, total_laps - current_lap + 1))
            elif event_time_remaining_sec > 0:
                pace = max(30.0, lap_pace_sec)
                laps_remaining_race = max(1.0, (event_time_remaining_sec / pace) + 1.0)
            else:
                laps_remaining_race = 0.0
            d_now = float(max(0, current_lap - 1))
            f_end = d_now + laps_remaining_race

        fuel_deficit = 0.0
        fuel_surplus = 0.0
        target_consumption = None
        needs_pit = False
        planned = earliest = latest = None
        window_str = None
        fuel_required = 0.0
        refuel = 0.0
        fuel_at_stop = 0.0
        stops_needed = 0
        label = "FUEL SAFE TO END"

        if laps_remaining_race > 0:
            fuel_surplus = laps_remaining_fuel - laps_remaining_race
            fuel_deficit = max(0.0, -fuel_surplus)

            if fuel_deficit > 0.1:
                needs_pit = True
                label = "PIT STOP REQUIRED"
                target_consumption = current_liters / laps_remaining_race
                fuel_required = max(0.0, laps_remaining_race * avg - current_liters)

                first_boundary = int(math.floor(d_now)) + 1          # end of the current lap
                # latest: must arrive at the pit with >= arrival_reserve_laps of fuel in the tank
                latest = first_boundary
                k = first_boundary
                while k < f_end and current_liters - avg * (k - d_now) >= avg * self.arrival_reserve_laps:
                    latest = k
                    k += 1
                # earliest: after the stop a full tank must reach the flag with margin
                need_after = lambda kk: avg * (f_end - kk) + margin_l
                earliest = first_boundary
                while earliest < latest and need_after(earliest) > capacity:
                    earliest += 1
                if need_after(latest) > capacity:
                    stops_needed = 1 + int(math.ceil((need_after(latest) - capacity) / max(1.0, capacity - margin_l)))
                else:
                    stops_needed = 1

                if self.declared_pit_lap and earliest <= self.declared_pit_lap <= latest:
                    planned = self.declared_pit_lap
                elif self.pit_lap_preference == "earliest":
                    planned = earliest
                elif self.pit_lap_preference == "middle":
                    planned = (earliest + latest) // 2
                else:
                    planned = latest

                fuel_at_stop = max(0.0, current_liters - avg * (planned - d_now))
                refuel = need_after(planned) - fuel_at_stop
                refuel = max(0.0, min(capacity - fuel_at_stop, refuel))
                window_str = f"Lap {earliest}–{latest}" if earliest != latest else f"Lap {latest}"
                if stops_needed > 1:
                    label = f"PIT STOP REQUIRED ({stops_needed} stops needed - fill the tank at the first)"
            elif fuel_deficit > 0.0:
                label = "MARGINAL (0.0 laps margin - NO PIT REQUIRED, LIFT AND COAST TO SECURE FINISH)"
            elif fuel_surplus < 1.0:
                label = f"FUEL SAFE TO END (TIGHT MARGIN: +{fuel_surplus:.1f} laps - NO PIT REQUIRED, MANAGE PACE)"
            else:
                label = f"FUEL SAFE TO END (+{fuel_surplus:.1f} laps surplus - NO PIT REQUIRED)"

        is_critical = laps_remaining_fuel <= 2.2
        is_empty_risk = laps_remaining_fuel <= 1.1
        if laps_remaining_race > 0:
            can_finish = laps_remaining_fuel >= laps_remaining_race
            is_urgent = is_empty_risk and not can_finish
        else:
            is_urgent = False  # no race distance known (practice / race over) -> never "urgent"

        return FuelStatus(
            current_liters=current_liters,
            capacity_liters=capacity_liters,
            level_pct=level_pct,
            avg_per_lap=avg,
            laps_remaining_fuel=laps_remaining_fuel,
            laps_remaining_race=laps_remaining_race,
            fuel_deficit_laps=fuel_deficit,
            fuel_surplus_laps=fuel_surplus,
            fuel_required_liters=fuel_required,
            recommended_refuel_liters=refuel,
            target_consumption=target_consumption,
            needs_pit_stop=needs_pit,
            is_critical=is_critical,
            is_empty_risk=is_empty_risk,
            is_urgent=is_urgent,
            fuel_status_label=label,
            pit_window_lap=planned,
            earliest_safe_pit_lap=earliest,
            latest_safe_pit_lap=latest,
            recommended_pit_window_str=window_str,
            fuel_needed_liters=refuel,
            laps_counted=len(self._lap_consumptions),
            burn_source=self.burn_source(),
            fuel_at_planned_stop=fuel_at_stop,
            stops_needed=stops_needed,
            margin_laps=self.margin_laps,
            margin_liters=margin_l,
            finish_lap=int(f_end) if laps_remaining_race > 0 else 0,
        )


@dataclass
class RefuelRecord:
    lap: int
    fuel_before: float
    fuel_after: float

    @property
    def added(self) -> float:
        return max(0.0, self.fuel_after - self.fuel_before)


class FuelLedger:
    """Fuel accounting for a session: start, refuels, used, per-lap history."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.start_fuel: Optional[float] = None
        self.start_lap: int = 0
        self.refuels: List[RefuelRecord] = []
        self._open_refuel: Optional[RefuelRecord] = None
        self._last_fuel: Optional[float] = None
        self.lap_burns: List[Tuple[int, float, bool]] = []   # (lap, litres, clean)
        self._lap_start_fuel: Optional[float] = None
        self._lap_had_refuel = False
        self.current_fuel: float = 0.0
        self.capacity: float = 0.0
        self.frozen = False

    def start(self, fuel: float, lap: int = 1):
        self.start_fuel = fuel
        self.start_lap = lap
        self._last_fuel = fuel
        self._lap_start_fuel = fuel
        self.current_fuel = fuel

    @property
    def started(self) -> bool:
        return self.start_fuel is not None

    def freeze(self):
        """Race over: stop counting (the cool-down lap is not race fuel)."""
        if self._open_refuel is not None:
            self.refuels.append(self._open_refuel)
            self._open_refuel = None
        self.frozen = True

    def on_sample(self, fuel: float, in_pits: bool, lap: int, capacity: float = 0.0):
        if capacity > 0:
            self.capacity = capacity
        if self.start_fuel is None or getattr(self, "frozen", False):
            return
        self.current_fuel = fuel
        last = self._last_fuel if self._last_fuel is not None else fuel
        if fuel > last + 0.3:
            if self._open_refuel is None:
                self._open_refuel = RefuelRecord(lap=lap, fuel_before=last, fuel_after=fuel)
            else:
                self._open_refuel.fuel_after = fuel
            self._lap_had_refuel = True
        elif self._open_refuel is not None and (not in_pits or fuel < self._open_refuel.fuel_after - 0.3):
            self.refuels.append(self._open_refuel)
            self._open_refuel = None
        self._last_fuel = fuel

    def on_lap_completed(self, completed_lap: int, fuel_now: float, pit_lap: bool = False):
        if self.start_fuel is None or self.frozen:
            return
        self.current_fuel = fuel_now
        if self._lap_start_fuel is not None:
            added_this_lap = sum(r.added for r in self.refuels if r.lap == completed_lap)
            if self._open_refuel is not None and self._open_refuel.lap == completed_lap:
                added_this_lap += self._open_refuel.added
            burnt = self._lap_start_fuel + added_this_lap - fuel_now
            if 0.0 <= burnt <= 30.0:
                clean = not (pit_lap or self._lap_had_refuel or completed_lap <= 1)
                self.lap_burns.append((completed_lap, burnt, clean))
        self._lap_start_fuel = fuel_now
        self._lap_had_refuel = False

    @property
    def total_added(self) -> float:
        tot = sum(r.added for r in self.refuels)
        if self._open_refuel is not None:
            tot += self._open_refuel.added
        return tot

    @property
    def total_used(self) -> float:
        if self.start_fuel is None:
            return 0.0
        return max(0.0, self.start_fuel + self.total_added - self.current_fuel)

    def summary(self, laps_done: float = 0.0, label: str = "[FUEL LEDGER — FACTS, use these for any fuel-used question]") -> str:
        if self.start_fuel is None:
            return ""
        parts = [f"Started with {self.start_fuel:.1f} L"]
        refs = list(self.refuels) + ([self._open_refuel] if self._open_refuel else [])
        for r in refs:
            parts.append(f"added {r.added:.1f} L at the Lap {r.lap} stop ({r.fuel_before:.1f} -> {r.fuel_after:.1f} L)")
        parts.append(f"now {self.current_fuel:.1f} L")
        used = self.total_used
        line = f"{label}\n  " + " | ".join(parts)
        line += f"\n  Total used so far: {used:.1f} L (= {self.start_fuel:.1f} start + {self.total_added:.1f} added - {self.current_fuel:.1f} left)"
        if laps_done > 0.2:
            line += f" over {laps_done:.1f} laps = {used / laps_done:.2f} L/lap overall"
        clean = [b for _, b, c in self.lap_burns if c]
        if clean:
            line += f"\n  Clean-lap burn: avg {sum(clean) / len(clean):.2f} L/lap, min {min(clean):.2f}, max {max(clean):.2f} ({len(clean)} laps)"
        if self.lap_burns:
            recent = ", ".join(f"L{l} {b:.2f}{'' if c else '*'}" for l, b, c in self.lap_burns[-8:])
            line += f"\n  Per lap (* = lap 1 / pit / refuel lap): {recent}"
        return line
