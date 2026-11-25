"""
Deterministic Race Strategy Engine for AMS2.
Pre-calculates all fuel, pit stop, mandatory regulation, and tactical targets
so the LLM race engineer never invents or calculates numbers.
"""

import re
from dataclasses import dataclass
from typing import Optional, List, Tuple
from telemetry.collector import TelemetrySnapshot
from race.fuel import FuelStatus
from race.tires import TireStatus
from race.gaps import GapAnalysis


@dataclass
class RaceStrategy:
    # Fuel Strategy
    fuel_status_label: str             # "PIT STOP REQUIRED" | "MARGINAL — PIT RECOMMENDED" | "FUEL SAFE TO END"
    fuel_deficit_laps: float           # laps short of finish
    fuel_surplus_laps: float           # laps beyond finish
    fuel_required_liters: float        # raw litres needed to finish
    recommended_refuel_liters: float   # litres needed with 1.5-lap safety margin
    fuel_pit_window_str: str           # e.g. "Lap 9–14"
    latest_safe_fuel_lap: int

    # Mandatory Regulations
    mandatory_stop_required: bool
    mandatory_stop_source: str         # "SIM TELEMETRY" | "DRIVER RADIO" | "NONE"
    enforced_pit_stop_lap: int         # from sim (-1 if none)
    mandatory_stops_completed: int
    mandatory_stop_pending: bool

    # Tactical & Tires
    tire_cliff_laps: float             # laps to 50% cliff
    limiting_tire: Optional[str]
    tire_condition_summary: str

    # Overall Combined Pit Recommendation
    pit_urgency: str                   # "NO PIT REQUIRED" | "PIT WINDOW OPEN" | "BOX THIS LAP" | "OVERDUE"
    combined_pit_window: str           # e.g. "Lap 9–12" or "Lap 5–15 (Mandatory)"
    confidence: str                    # "HIGH" | "MEDIUM" | "LOW"
    rationale: str                     # Human-readable reason for the engineer

    # Pit Exit Rejoin Projection
    projected_rejoin_pos: Optional[int] = None
    rejoin_summary: str = "N/A"

    # Planned stop
    planned_pit_lap: Optional[int] = None
    planned_refuel_liters: float = 0.0
    fuel_at_planned_stop: float = 0.0
    pit_loss_breakdown: str = ""

    # One line for the engineer: is there a mandatory stop, and is it done?
    mandatory_line: str = ""


_MANDATORY_RE = re.compile(r"\b(mandatory|compulsory|enforced|required|obligatory)\b[^.\n]{0,30}\b(pit|stop|tyre|tire)", re.I)
_MANDATORY_RE2 = re.compile(r"\b(pit|tyre|tire)[- ]?stop\b[^.\n]{0,20}\b(mandatory|compulsory|required)\b", re.I)
_NO_MANDATORY_RE = re.compile(r"\b(no|not a|without( a)?)\s+(mandatory|compulsory|required)\b[^.\n]{0,20}\b(pit|stop)", re.I)


def text_declares_mandatory_stop(text: str) -> Optional[bool]:
    """True if the text says there is a mandatory stop, False if it says there isn't, None if it doesn't say."""
    t = text or ""
    if _NO_MANDATORY_RE.search(t):
        return False
    if _MANDATORY_RE.search(t) or _MANDATORY_RE2.search(t):
        return True
    return None


class RaceStrategyEngine:
    def __init__(self):
        # None = the driver hasn't said; True / False = he told us over the radio
        self.driver_declared_mandatory_stop: Optional[bool] = None
        self.driver_declared_pit_lap: Optional[int] = None
        self.player_stops_completed: int = 0
        self._last_pit_mode: int = 0
        self._last_announced_strategy: Optional[str] = None
        # config strategy.mandatory_pit_stop: "auto" (sim data / brief / radio), "yes" or "no"
        self.config_mandatory: str = "auto"
        self._declared_track: str = ""
        self.brief_mandatory: Optional[bool] = None   # from the driver's pre-race brief (persists across sessions)

    def reset(self):
        # The driver's word on a mandatory stop is kept across practice -> quali -> race at the same track
        # (he often says it before the race); mandatory_source() drops it when the track changes.
        self.driver_declared_pit_lap = None
        self.player_stops_completed = 0
        self._last_pit_mode = 0
        self._last_announced_strategy = None

    def on_driver_mandatory_stop_declared(self, enabled: bool = True, track: str = ""):
        """Called when the driver tells us over the radio whether there is a mandatory pit stop."""
        self.driver_declared_mandatory_stop = enabled
        self._declared_track = track or self._declared_track

    def set_driver_brief(self, brief: str):
        self.brief_mandatory = text_declares_mandatory_stop(brief)

    def mandatory_source(self, snapshot: Optional[TelemetrySnapshot]) -> Tuple[Optional[bool], str]:
        """(is there a mandatory stop? True / False / None = unknown, where that comes from)."""
        cfg = str(self.config_mandatory or "auto").strip().lower()
        if cfg in ("no", "false", "off", "0"):
            return False, "config"
        if cfg in ("yes", "true", "on", "1"):
            return True, "config"
        if self.driver_declared_mandatory_stop is not None and snapshot is not None and self._declared_track                 and snapshot.track_name and snapshot.track_name != self._declared_track:
            self.driver_declared_mandatory_stop = None      # that was another event
        if self.driver_declared_mandatory_stop is not None:
            return self.driver_declared_mandatory_stop, "driver on the radio"
        if self.brief_mandatory is not None:
            return self.brief_mandatory, "driver's pre-race brief"
        lap = int(getattr(snapshot, "enforced_pit_stop_lap", -1) or -1) if snapshot is not None else -1
        if lap > 0:
            return True, f"sim rules (enforced pit stop lap {lap})"
        return None, ""

    def update_pit_tracking(self, pit_mode_value: int):
        """Tracks when player enters and completes pit stops."""
        # pit_mode > 0 indicates driving into pits, in pit, driving out, etc.
        if self._last_pit_mode == 0 and pit_mode_value > 0:
            self.player_stops_completed += 1
        self._last_pit_mode = pit_mode_value

    def compute_strategy(
        self,
        snapshot: Optional[TelemetrySnapshot],
        fuel: FuelStatus,
        tires: TireStatus,
        estimated_pit_loss_sec: float = 45.0,
        pit_loss_breakdown: str = "",
        track_model=None,
        mandatory_served_lap: Optional[int] = None,
    ) -> RaceStrategy:
        if not snapshot:
            return RaceStrategy(
                fuel_status_label="NO DATA",
                fuel_deficit_laps=0.0,
                fuel_surplus_laps=0.0,
                fuel_required_liters=0.0,
                recommended_refuel_liters=0.0,
                fuel_pit_window_str="N/A",
                latest_safe_fuel_lap=0,
                mandatory_stop_required=False,
                mandatory_stop_source="NONE",
                enforced_pit_stop_lap=-1,
                mandatory_stops_completed=0,
                mandatory_stop_pending=False,
                tire_cliff_laps=99.0,
                limiting_tire=None,
                tire_condition_summary="Nominal",
                pit_urgency="NO PIT REQUIRED",
                combined_pit_window="N/A",
                confidence="LOW",
                rationale="No live telemetry available.",
                projected_rejoin_pos=None,
                rejoin_summary="No live telemetry available."
            )

        self.update_pit_tracking(snapshot.pit_mode.value)

        current_lap = snapshot.current_lap
        sim_enforced_lap = -1

        # 1. Mandatory pit stop: only when the sim rules, the driver's brief, the driver on the radio or the
        #    config say so. Unknown is said as unknown, so the engineer never tells him "no stop needed" in a
        #    race that has one (Oulton, Nov 2026: "fuel's safe, no stop needed" in a mandatory-stop race).
        known, src = self.mandatory_source(snapshot)
        mandatory_required = bool(known)
        mandatory_source = src.upper() if src else "NONE"
        sim_enforced_lap = int(getattr(snapshot, "enforced_pit_stop_lap", -1) or -1)
        served = mandatory_served_lap is not None
        mandatory_pending = mandatory_required and not served
        if known is True:
            if served:
                mandatory_line = f"Mandatory stop: REQUIRED ({src}) - SERVED on Lap {mandatory_served_lap}. Nothing more to do for it."
            else:
                mandatory_line = (f"Mandatory stop: REQUIRED ({src}) and NOT SERVED YET. Fuel and tyres may be fine, "
                                  f"but we still have to stop - never say 'no stop needed'.")
        elif known is False:
            mandatory_line = f"Mandatory stop: none in this race ({src})."
        else:
            mandatory_line = ("Mandatory stop: NOT KNOWN for this race (nothing in the sim data, the driver's brief or "
                              "on the radio). Don't claim there is or isn't one: say fuel and tyres are good to the flag, "
                              "not 'no stop needed'.")

        # 2. Fuel Metrics
        fuel_deficit = fuel.fuel_deficit_laps
        fuel_surplus = fuel.fuel_surplus_laps
        fuel_needed = fuel.fuel_required_liters
        refuel_rec = fuel.recommended_refuel_liters
        latest_safe_lap = fuel.latest_safe_pit_lap or (current_lap + int(fuel.laps_remaining_fuel))

        fuel_window_str = fuel.recommended_pit_window_str or f"Lap {current_lap}–{latest_safe_lap}"

        # 3. Tire Limits
        tire_cliff_laps = tires.estimated_laps_remaining
        tire_critical = tires.is_critical_wear or tires.is_very_worn

        # 4. Synthesize Combined Strategy & Urgency
        must_pit_now = False
        pit_urgency = "NO PIT REQUIRED"
        rationale_parts = []

        can_finish_fuel = (
            fuel.laps_remaining_race > 0
            and fuel.fuel_deficit_laps <= 0.0
            and fuel.laps_remaining_fuel >= fuel.laps_remaining_race
        )

        if fuel.is_empty_risk and not can_finish_fuel:
            pit_urgency = "BOX THIS LAP (FUEL EMERGENCY)"
            must_pit_now = True
            rationale_parts.append(f"Fuel critically low ({snapshot.fuel_liters:.1f}L remaining, <1 lap) - deficit to flag")
        elif tire_critical:
            pit_urgency = "BOX THIS LAP (TIRES CRITICAL)"
            must_pit_now = True
            rationale_parts.append(f"Tires critical on {tires.limiting_tire} ({tires.condition_summary})")
        elif fuel.needs_pit_stop:
            # Pit stop is needed for fuel
            pit_urgency = "PIT STOP REQUIRED"
            rationale_parts.append(
                f"Fuel deficit of {fuel_deficit:.1f} laps (need ~{refuel_rec:.1f}L added to finish)"
            )

            # Check if we are currently inside the recommended window
            if current_lap >= (fuel.earliest_safe_pit_lap or current_lap):
                pit_urgency = "PIT WINDOW OPEN"
        elif mandatory_pending:
            laps_left = fuel.laps_remaining_race
            if 0 < laps_left <= 2.2:
                pit_urgency = "BOX THIS LAP (MANDATORY STOP NOT SERVED)"
            else:
                pit_urgency = "MANDATORY STOP STILL TO SERVE"
            rationale_parts.append(f"Mandatory stop not served yet ({src}); fuel safe to the flag (+{fuel_surplus:.1f} laps), so the lap is ours to choose")
        else:
            pit_urgency = "NO PIT REQUIRED"
            if fuel.is_empty_risk and can_finish_fuel:
                rationale_parts.append(f"Fuel is low ({snapshot.fuel_liters:.1f}L remaining) but safe to the finish (+{fuel_surplus:.1f} laps margin), tires nominal")
            else:
                rationale_parts.append(f"Fuel safe to end (+{fuel_surplus:.1f} laps margin), tires nominal")

        # Determine Combined Pit Window String
        if fuel.needs_pit_stop:
            combined_window = fuel_window_str
            if fuel.pit_window_lap:
                combined_window += f" | PLANNED: box at the end of Lap {fuel.pit_window_lap}, add ~{fuel.recommended_refuel_liters:.0f} L"
        elif mandatory_pending:
            combined_window = "Mandatory stop to serve - any lap before the flag (fuel doesn't force a lap; pick it for traffic / clean air)"
        else:
            combined_window = "No stop scheduled"

        # 5. Deterministic Pit Exit Rejoin Projection
        projected_rejoin_pos = None
        rejoin_summary = "No competitor timing data to project pit exit."

        if snapshot.participants and snapshot.position > 0:
            active_parts = [p for p in snapshot.participants if p.is_active and p.position > 0]
            player_pos = snapshot.position
            total_field = len(active_parts)

            # Sort active cars by position
            sorted_cars = sorted(active_parts, key=lambda p: p.position)

            player_p = next(
                (p for p in active_parts if p.position == player_pos or (snapshot.player_name and snapshot.player_name.lower() in p.name.lower())),
                None
            )

            # Calculate average track speed in m/s if track length and pace available
            lap_pace = max(30.0, snapshot.best_lap_time or snapshot.last_lap_time or 90.0)
            track_length = snapshot.track_length_m if snapshot.track_length_m > 100.0 else 4000.0
            avg_speed_mps = track_length / lap_pace

            cars_that_overtake = []
            cars_remaining_behind = []

            for p in sorted_cars:
                if p.position <= player_pos:
                    continue  # Already ahead of player

                # Determine current gap behind player (in seconds)
                if p.position == player_pos + 1 and snapshot.split_time_behind > 0:
                    gap_behind_sec = snapshot.split_time_behind
                elif track_model is not None and player_p and (p.lap > 0 or p.lap_distance > 0):
                    tp = player_p.lap * track_length + player_p.lap_distance
                    tc = p.lap * track_length + p.lap_distance
                    gap_behind_sec = track_model.gap_seconds(tp, tc, lap_pace) if tp > tc else (p.position - player_pos) * 2.5
                elif player_p and (player_p.lap > 0 or player_p.lap_distance > 0) and (p.lap > 0 or p.lap_distance > 0):
                    total_player_dist = player_p.lap * track_length + player_p.lap_distance
                    total_p_dist = p.lap * track_length + p.lap_distance
                    dist_behind = total_player_dist - total_p_dist
                    if dist_behind > 0:
                        gap_behind_sec = dist_behind / avg_speed_mps
                    else:
                        gap_behind_sec = (p.position - player_pos) * 2.5
                else:
                    gap_behind_sec = (p.position - player_pos) * 2.5

                if gap_behind_sec < estimated_pit_loss_sec:
                    cars_that_overtake.append((p, gap_behind_sec))
                else:
                    cars_remaining_behind.append((p, gap_behind_sec))

            # Projected position on exit
            projected_rejoin_pos = min(total_field, player_pos + len(cars_that_overtake))

            # Find car immediately ahead and behind on exit
            rejoin_details = []
            if cars_that_overtake:
                car_ahead_exit, gap_orig = cars_that_overtake[-1]
                gap_ahead_exit = estimated_pit_loss_sec - gap_orig
                surname = car_ahead_exit.name.split()[-1] if car_ahead_exit.name else "Car"
                rejoin_details.append(f"behind P{projected_rejoin_pos - 1} {surname} (~{gap_ahead_exit:.1f}s)")

            if cars_remaining_behind:
                car_behind_exit, gap_orig = cars_remaining_behind[0]
                gap_behind_exit = gap_orig - estimated_pit_loss_sec
                surname = car_behind_exit.name.split()[-1] if car_behind_exit.name else "Car"
                rejoin_details.append(f"ahead of P{projected_rejoin_pos + 1} {surname} (~{gap_behind_exit:.1f}s)")

            detail_str = f" ({', '.join(rejoin_details)})" if rejoin_details else ""
            rejoin_summary = f"If you pit: rejoin ~P{projected_rejoin_pos}{detail_str} [pit loss {estimated_pit_loss_sec:.0f}s]"

        # Determine Confidence
        if fuel.laps_counted >= 3:
            confidence = "HIGH"
        elif fuel.laps_counted >= 1:
            confidence = "MEDIUM"
        else:
            confidence = "LOW (Awaiting lap fuel burn data)"

        rationale = "; ".join(rationale_parts)

        return RaceStrategy(
            fuel_status_label=fuel.fuel_status_label,
            fuel_deficit_laps=fuel_deficit,
            fuel_surplus_laps=fuel_surplus,
            fuel_required_liters=fuel_needed,
            recommended_refuel_liters=refuel_rec,
            fuel_pit_window_str=fuel_window_str,
            latest_safe_fuel_lap=latest_safe_lap,
            mandatory_stop_required=mandatory_required,
            mandatory_stop_source=mandatory_source,
            enforced_pit_stop_lap=sim_enforced_lap,
            mandatory_stops_completed=self.player_stops_completed,
            mandatory_stop_pending=mandatory_pending,
            tire_cliff_laps=tire_cliff_laps,
            limiting_tire=tires.limiting_tire,
            tire_condition_summary=tires.condition_summary,
            pit_urgency=pit_urgency,
            combined_pit_window=combined_window,
            confidence=confidence,
            rationale=rationale,
            projected_rejoin_pos=projected_rejoin_pos,
            rejoin_summary=rejoin_summary,
            planned_pit_lap=fuel.pit_window_lap if fuel.needs_pit_stop else None,
            planned_refuel_liters=fuel.recommended_refuel_liters,
            fuel_at_planned_stop=fuel.fuel_at_planned_stop,
            pit_loss_breakdown=pit_loss_breakdown,
            mandatory_line=mandatory_line,
        )


strategy_engine = RaceStrategyEngine()