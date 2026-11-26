"""
Practice Session & Stint Manager for AMS2.
Coordinates mini-sessions / stints, stint-to-stint comparisons,
calculates race setup fuel burn, and manages setup advice.
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from telemetry.collector import TelemetrySnapshot
from .diagnostics import PracticeTelemetryMonitor, PracticeDiagnosticsSummary
from .handling import HandlingAnalyzer, aggregate, format_run, format_corner_table
from .setup_journal import setup_journal as _global_journal
from utils.logging import logger


def _fmt_lap(sec: float) -> str:
    if sec <= 0:
        return "--:--"
    m = int(sec // 60)
    return f"{m}:{sec % 60:06.3f}" if m else f"{sec:.3f}s"


@dataclass
class PracticeRunRecord:
    run_number: int
    laps_completed: int
    lap_times: List[float]
    best_lap_time: float
    avg_fuel_burn_l_per_lap: float
    tyre_compound: str
    tyre_wear_pct: Tuple[float, ...]
    tyre_pressures_bar: Tuple[float, ...]
    tyre_inner_temps_c: Tuple[float, ...]
    tyre_center_temps_c: Tuple[float, ...]
    tyre_outer_temps_c: Tuple[float, ...]
    diagnostics: PracticeDiagnosticsSummary
    timestamp: float = field(default_factory=time.time)
    handling: Dict = field(default_factory=dict)          # aggregate() over the run's flying laps
    changes_before: List[str] = field(default_factory=list)
    start_pressures_bar: Tuple[float, ...] = ()
    start_fuel: float = 0.0
    flying_laps: List[float] = field(default_factory=list)
    cold_eq_bar: Dict[str, float] = field(default_factory=dict)   # set pressures measured on track (20°C)
    garage_before: bool = False                                   # car went to the garage before this run
    detected_changes: List[str] = field(default_factory=list)     # what the app saw change before this run

    @property
    def median_lap(self) -> float:
        laps = sorted(self.flying_laps)
        if not laps:
            return 0.0
        best = laps[0]
        clean = [x for x in laps if x <= best * 1.03]
        return clean[len(clean) // 2]

    @property
    def lap_spread(self) -> float:
        laps = sorted(self.flying_laps)
        if len(laps) < 2:
            return 0.0
        best = laps[0]
        clean = [x for x in laps if x <= best * 1.03]
        return (clean[-1] - clean[0]) if len(clean) >= 2 else 0.0


class PracticeSessionManager:
    """
    Manages the lifecycle of practice runs (stints) and provides
    comparative debriefs and setup diagnostics.
    """

    def __init__(self, monitor: Optional[PracticeTelemetryMonitor] = None, journal=None, stats_store=None):
        self.monitor = monitor or PracticeTelemetryMonitor()
        self.handling = HandlingAnalyzer()
        self.journal = journal or _global_journal
        self.stats_store = stats_store
        self.session_started_at = time.strftime("%Y-%m-%d %H:%M")
        self.car = ""
        self.track = ""
        self.variation = ""
        self._run_start_pressures: Tuple[float, ...] = ()
        self._pressures_captured = False
        self.completed_runs: List[PracticeRunRecord] = []

        # Current stint state
        self.current_run_number: int = 1
        self.is_car_on_track: bool = False
        self._stint_start_lap: int = 0
        self._stint_lap_times: List[float] = []
        self._stint_start_fuel: float = 0.0
        self._last_pit_mode: int = 0
        self._initial_briefing_given: bool = False

        # Fuel burn tracking specifically for practice stints
        self._fuel_lap_start: Optional[float] = None
        self._stint_lap_consumptions: List[float] = []
        self._last_completed_lap: int = -1

        # Debrief and mini-session conversation tracking
        self.stint_completed_since_last_debrief: bool = False
        self.pending_setup_changes: List[str] = []
        self.last_stint_end_temps: Optional[Dict[str, Tuple[float, float, float]]] = None
        self._init_run_tracking()

    def _init_run_tracking(self):
        # Debounced pit exit / entry (pit mode flickers around the garage and pit box in AMS2, which used
        # to split one run into several empty ones and attach setup changes to the wrong run)
        self._exit_cand: Optional[Dict] = None
        self._entry_cand_t: Optional[float] = None
        self._run_started_t: float = 0.0
        self._last_raw_pit_mode: int = -1
        self._last_lap_time_seen: float = 0.0
        self._run_start_seq: int = 0
        # Setup-change awareness
        self._garage_since_last_run: bool = False
        self._run_garage_visit: bool = False
        self._run_cold_eq: Dict[str, float] = {}
        self._change_check_done: bool = True
        self.run_changes_detected: List[str] = []
        self.proposed_change: str = ""
        self._proposal_run: int = 0

    @property
    def is_in_pits(self) -> bool:
        return not self.is_car_on_track

    def mark_debrief_delivered(self):
        """Called when debrief has been delivered to driver over radio."""
        self.stint_completed_since_last_debrief = False

    def record_setup_change(self, change_desc: str):
        """Records a setup change the driver confirmed, for follow-up on the next stint."""
        self.pending_setup_changes.append(change_desc)
        if self.proposed_change and self._proposal_run == self.next_run_number:
            self.proposed_change = ""          # confirmed by the driver: no need to assume it

    @property
    def next_run_number(self) -> int:
        """The run a change made now applies to (the current run is already under way when on track)."""
        return self.current_run_number + (1 if self.is_car_on_track else 0)

    def propose_change(self, change_desc: str):
        """The engineer asked for a change. If the car then goes to the garage before the next run, the
        change is assumed made (and logged as such) without the driver having to report it."""
        change_desc = (change_desc or "").strip()
        if len(change_desc) >= 3:
            self.proposed_change = change_desc
            self._proposal_run = self.next_run_number

    def reset_session(self):
        """Resets all practice runs for a brand new session."""
        self.completed_runs.clear()
        self.current_run_number = 1
        self.is_car_on_track = False
        self._stint_start_lap = 0
        self._stint_lap_times.clear()
        self._stint_start_fuel = 0.0
        self._stint_lap_consumptions.clear()
        self._fuel_lap_start = None
        self._last_completed_lap = -1
        self._initial_briefing_given = False
        self.stint_completed_since_last_debrief = False
        self.pending_setup_changes.clear()
        self.last_stint_end_temps = None
        self.monitor.reset_run()
        self.handling.reset()
        self.session_started_at = time.strftime("%Y-%m-%d %H:%M")
        self._run_start_pressures = ()
        self._pressures_captured = False
        self._init_run_tracking()
        logger.info("Practice Session Manager reset for new session.")

    @property
    def journal_key(self) -> str:
        return self.journal.session_key(self.car, self.track, self.variation)

    def on_telemetry(self, snapshot: TelemetrySnapshot):
        """Processes live snapshot to track pit exits and pit entries."""
        curr_pit_mode = snapshot.pit_mode.value
        in_pit = curr_pit_mode > 0
        now = float(getattr(snapshot, "timestamp", 0.0) or time.time())

        if snapshot.car_name:
            self.car, self.track, self.variation = snapshot.car_name, snapshot.track_name, snapshot.track_variation

        if curr_pit_mode != self._last_raw_pit_mode:
            logger.info(f"[PRACTICE] pit mode {self._last_raw_pit_mode} -> {curr_pit_mode} | lap {snapshot.current_lap} | "
                        f"{snapshot.speed_kmh:.0f} km/h | run active: {self.is_car_on_track}")
            self._last_raw_pit_mode = curr_pit_mode
        if curr_pit_mode == 4:      # PIT_MODE_IN_GARAGE: the setup screen can be opened here
            self._garage_since_last_run = True

        # Feed the 10Hz monitors
        self.monitor.on_telemetry(snapshot)
        try:
            self.handling.on_telemetry(snapshot)
        except Exception as e:
            logger.debug(f"handling analyzer error: {e}")

        if not self.is_car_on_track:
            # Run starts once the car has been out of the pits for 4 s and is at speed (debounced)
            if not in_pit:
                if self._exit_cand is None:
                    press = tuple(snapshot.tyre_pressures_bar) if any(p > 0.3 for p in snapshot.tyre_pressures_bar) else ()
                    self._exit_cand = {"t": now, "lap": snapshot.current_lap, "fuel": snapshot.fuel_liters,
                                       "pressures": press, "seq": self.handling.seq}
                elif now - self._exit_cand["t"] >= 4.0 and snapshot.speed_kmh > 30.0:
                    self._start_run(snapshot, now)
            else:
                self._exit_cand = None
        else:
            # Run ends on a clear pit entry (driving in / in the box / garage); "driving out" flickers need 2 s
            if in_pit:
                if self._entry_cand_t is None:
                    self._entry_cand_t = now
                if now - self._entry_cand_t >= 2.0 or snapshot.speed_kmh < 3.0 or curr_pit_mode in (1, 2, 4):
                    self._finalize_current_run(snapshot)
                    self.is_car_on_track = False
                    self._entry_cand_t = None
                    self._exit_cand = None
            else:
                self._entry_cand_t = None
                if not self._change_check_done:
                    self._check_setup_changes(now)

        # Lap completion tracking during active stint
        if self.is_car_on_track and snapshot.current_lap > self._last_completed_lap and self._last_completed_lap > 0:
            lt = snapshot.last_lap_time
            # AMS2 bumps the lap counter on leaving the garage without a new lap time: skip stale repeats
            if lt > 15.0 and abs(lt - self._last_lap_time_seen) > 1e-3:
                self._stint_lap_times.append(lt)
            if lt > 0:
                self._last_lap_time_seen = lt

            # Record lap fuel consumption
            if self._fuel_lap_start is not None:
                consumed = self._fuel_lap_start - snapshot.fuel_liters
                if 0.5 <= consumed <= 15.0:
                    self._stint_lap_consumptions.append(consumed)
            self._fuel_lap_start = snapshot.fuel_liters
        elif snapshot.current_lap != self._last_completed_lap and snapshot.last_lap_time > 0:
            self._last_lap_time_seen = snapshot.last_lap_time      # in-lap / pit-lane crossings

        self._last_completed_lap = snapshot.current_lap
        self._last_pit_mode = curr_pit_mode

    # ------------------------------------------------------------------ run start & setup-change awareness
    def _start_run(self, snapshot: TelemetrySnapshot, now: float):
        c = self._exit_cand or {}
        self.is_car_on_track = True
        self._run_started_t = now
        self._stint_start_lap = c.get("lap", snapshot.current_lap)
        self._stint_start_fuel = c.get("fuel", snapshot.fuel_liters)
        self._stint_lap_times.clear()
        self._stint_lap_consumptions.clear()
        self._fuel_lap_start = self._stint_start_fuel
        self._run_start_seq = c.get("seq", self.handling.seq)
        self.monitor.reset_run()
        self._run_start_pressures = c.get("pressures", ())
        self._pressures_captured = bool(self._run_start_pressures)
        self._run_garage_visit = self._garage_since_last_run
        self._garage_since_last_run = False
        self._run_cold_eq = {}
        self.run_changes_detected = []
        self._change_check_done = False
        self._exit_cand = None
        logger.info(f"[PRACTICE] Pit exit confirmed. Starting Run #{self.current_run_number} (Fuel: {self._stint_start_fuel:.1f}L"
                    f"{', after a garage visit' if self._run_garage_visit else ''}).")

    def _prev_run_with(self, attr: str) -> Optional[PracticeRunRecord]:
        for r in reversed(self.completed_runs):
            if getattr(r, attr):
                return r
        return None

    @staticmethod
    def _describe_pressure_delta(deltas: Dict[str, float]) -> str:
        vals = [deltas[c] for c in ("FL", "FR", "RL", "RR") if c in deltas]
        if len(vals) == 4 and max(vals) - min(vals) <= 0.012:
            return f"all four {sum(vals) / 4:+.2f} bar"
        if len(vals) == 4 and abs(vals[0] - vals[1]) <= 0.012 and abs(vals[2] - vals[3]) <= 0.012:
            return f"fronts {(vals[0] + vals[1]) / 2:+.2f} bar, rears {(vals[2] + vals[3]) / 2:+.2f} bar"
        return ", ".join(f"{c} {deltas[c]:+.2f}" for c in ("FL", "FR", "RL", "RR") if c in deltas) + " bar"

    def _check_setup_changes(self, now: float):
        """Early in each run: did the set pressures change since the last run, and was the car in the garage?

        Set pressures are measured from the hot pressure and the tyre's internal air temperature (AMS2), so
        they can be compared whatever the tyre temperatures. Without air temps (RaceRoom) the pit-exit
        pressures are compared when both runs started from the garage on cold tyres."""
        live = self.handling.live_cold_eq()
        ready = len(live) == 4 and all(n >= 30 for _, _, n in live.values())
        consistent = ready and all(spread <= 0.03 for _, spread, _ in live.values())
        timed_out = now - self._run_started_t > 180.0
        has_air = bool(live)
        if not ready and not timed_out and (has_air or now - self._run_started_t < 20.0):
            return
        self._change_check_done = True

        detected: List[str] = []
        pressure_measured_change = False
        pressure_measured = False
        if ready and consistent:
            self._run_cold_eq = {c: round(v[0], 3) for c, v in live.items()}
            pressure_measured = True
            prev = self._prev_run_with("cold_eq_bar")
            if prev:
                deltas = {c: self._run_cold_eq[c] - prev.cold_eq_bar[c] for c in self._run_cold_eq if c in prev.cold_eq_bar}
                if any(abs(d) >= 0.025 for d in deltas.values()):
                    pressure_measured_change = True
                    detected.append(f"Measured: set tyre pressures {self._describe_pressure_delta(deltas)} vs run #{prev.run_number}")
                else:
                    detected.append(f"Measured: set tyre pressures unchanged vs run #{prev.run_number} (within 0.02 bar)")
        elif ready:
            logger.info("[PRACTICE] Cold-equivalent pressures inconsistent over the lap - not using them to detect changes.")
        elif self._run_garage_visit and self._run_start_pressures:
            prev = self._prev_run_with("start_pressures_bar")
            if prev and prev.garage_before and len(prev.start_pressures_bar) == 4 and len(self._run_start_pressures) == 4:
                deltas = {c: self._run_start_pressures[i] - prev.start_pressures_bar[i] for i, c in enumerate(("FL", "FR", "RL", "RR"))}
                pressure_measured = True
                if any(abs(d) >= 0.02 for d in deltas.values()):
                    pressure_measured_change = True
                    detected.append(f"Measured at pit exit (cold tyres): tyre pressures {self._describe_pressure_delta(deltas)} vs run #{prev.run_number}")

        run_no = self.current_run_number
        confirmed = self.journal.changes_for_run(self.journal_key, run_no, self.session_started_at) if self.car else []
        proposal = self.proposed_change if self.proposed_change and self._proposal_run == run_no else ""
        is_pressure_proposal = bool(proposal) and any(k in proposal.lower() for k in ("pressure", " bar", "psi"))

        if self._run_garage_visit:
            if proposal and not confirmed:
                if is_pressure_proposal and pressure_measured and not pressure_measured_change:
                    detected.append(f"Your call before this run ('{proposal}') does NOT show in the measured pressures - check with the driver")
                elif is_pressure_proposal and pressure_measured_change:
                    detected.append(f"Your call '{proposal}' - compare with the measured change above")
                else:
                    detected.append(f"Car went to the garage after your call '{proposal}' - assume it was made")
                    if self.car:
                        self.journal.add_change(self.journal_key, f"{proposal} (engineer's call, car went to the garage - assumed made)", run_no)
                self.proposed_change = ""
            elif not confirmed and not pressure_measured_change:
                detected.append("Car went to the garage before this run - other setup items may have changed; ask only if the handling data moved")
        if pressure_measured_change and self.car:
            self.journal.add_change(self.journal_key, detected[0], run_no)

        self.run_changes_detected = detected
        if detected:
            logger.info(f"[PRACTICE] Run #{run_no} setup check: " + " | ".join(detected))

    def _finalize_current_run(self, snapshot: TelemetrySnapshot):
        """Archives the completed stint into completed_runs."""
        if not self._stint_lap_times:
            # No lap completed: a trip round the pit lane / garage, not a run. Keep the run number (so a
            # change logged for it still applies to the next real run) and carry the garage visit over.
            self._garage_since_last_run = self._garage_since_last_run or self._run_garage_visit
            self._change_check_done = True
            logger.info(f"[PRACTICE] Run #{self.current_run_number} had no completed lap - not counted as a run.")
            return
        diag_summary = self.monitor.get_summary(snapshot)
        best_lap = min(self._stint_lap_times) if self._stint_lap_times else (snapshot.best_lap_time or 0.0)

        # Calculate average fuel consumption for this run
        if self._stint_lap_consumptions:
            avg_fuel = sum(self._stint_lap_consumptions) / len(self._stint_lap_consumptions)
        elif self._stint_lap_times and self._stint_start_fuel > snapshot.fuel_liters:
            avg_fuel = (self._stint_start_fuel - snapshot.fuel_liters) / max(1, len(self._stint_lap_times))
        else:
            avg_fuel = 0.0

        # Flying laps = every lap of the run except the out-lap
        flying = [t for t in self._stint_lap_times[1:] if t > 15.0]
        run_laps = self._run_handling_laps()
        handling_agg = aggregate(run_laps) if run_laps else {}
        if not self._change_check_done:
            self._check_setup_changes(float(getattr(snapshot, "timestamp", 0.0) or time.time()) + 999.0)
        cold_eq = handling_agg.get("cold_eq_bar") or self._run_cold_eq
        changes = self.journal.changes_for_run(self.journal_key, self.current_run_number, self.session_started_at) if self.car else []

        run_record = PracticeRunRecord(
            run_number=self.current_run_number,
            laps_completed=len(self._stint_lap_times),
            lap_times=list(self._stint_lap_times),
            best_lap_time=best_lap,
            avg_fuel_burn_l_per_lap=avg_fuel,
            tyre_compound=snapshot.tyre_compound,
            tyre_wear_pct=snapshot.tyre_wear_pct,
            tyre_pressures_bar=snapshot.tyre_pressures_bar,
            tyre_inner_temps_c=snapshot.tyre_temps_inner_c,
            tyre_center_temps_c=snapshot.tyre_temps_center_c,
            tyre_outer_temps_c=snapshot.tyre_temps_outer_c,
            diagnostics=diag_summary,
            handling=handling_agg,
            changes_before=changes,
            start_pressures_bar=self._run_start_pressures,
            start_fuel=self._stint_start_fuel,
            flying_laps=flying,
            cold_eq_bar=dict(cold_eq) if len(cold_eq) == 4 else {},
            garage_before=self._run_garage_visit,
            detected_changes=list(self.run_changes_detected),
        )
        self.completed_runs.append(run_record)
        try:
            if self.car:
                self.journal.set_drivetrain(self.car, self.handling.drivetrain)
                self.journal.add_run(self.journal_key, {
                    "run": self.current_run_number, "when": time.strftime("%Y-%m-%d %H:%M"),
                    "laps": len(flying), "best": round(min(flying), 3) if flying else 0,
                    "median": round(run_record.median_lap, 3), "changes_before": changes,
                    "fuel_burn": round(avg_fuel, 3),
                })
            if self.stats_store is not None and self.car:
                self.stats_store.add_fuel_burns(self.car, self.track, self.variation, self._stint_lap_consumptions[1:])
                self.stats_store.add_lap_times(self.car, self.track, self.variation, flying)
        except Exception as e:
            logger.debug(f"journal/stats update failed: {e}")

        # Mark stint completed for on-demand PTT debrief
        self.stint_completed_since_last_debrief = True

        # Save end-of-stint tyre temperatures
        if len(snapshot.tyre_temps_inner_c) >= 4:
            self.last_stint_end_temps = {
                "FL": (snapshot.tyre_temps_inner_c[0], snapshot.tyre_temps_center_c[0], snapshot.tyre_temps_outer_c[0]),
                "FR": (snapshot.tyre_temps_inner_c[1], snapshot.tyre_temps_center_c[1], snapshot.tyre_temps_outer_c[1]),
                "RL": (snapshot.tyre_temps_inner_c[2], snapshot.tyre_temps_center_c[2], snapshot.tyre_temps_outer_c[2]),
                "RR": (snapshot.tyre_temps_inner_c[3], snapshot.tyre_temps_center_c[3], snapshot.tyre_temps_outer_c[3]),
            }

        logger.info(f"[PRACTICE] Completed Run #{self.current_run_number}: {run_record.laps_completed} laps, Best: {run_record.best_lap_time:.2f}s, Burn: {avg_fuel:.2f}L/lap.")
        self.current_run_number += 1

    def get_overall_avg_fuel_burn(self) -> float:
        """Returns the average fuel burn per lap across all completed practice laps."""
        all_consumptions = []
        for run in self.completed_runs:
            if run.avg_fuel_burn_l_per_lap > 0:
                all_consumptions.append(run.avg_fuel_burn_l_per_lap)
        if not all_consumptions:
            return 3.5  # default fallback
        return sum(all_consumptions) / len(all_consumptions)

    def get_comparative_debrief(self, current_snapshot: Optional[TelemetrySnapshot] = None) -> str:
        """
        The engineer's setup lab sheet: what we know about the car, every run this session with the
        change made before it, the latest run in detail, and a measured comparison with the run before.
        """
        car = self.car or (current_snapshot.car_name if current_snapshot else "")
        lines: List[str] = []

        # ---- car knowledge
        facts = self.journal.car_facts(car) if car else []
        dt = self.handling.drivetrain if self.handling.drivetrain != "unknown" else (self.journal.drivetrain(car) if car else "unknown")
        lines.append("[CAR KNOWLEDGE — what we know about this car]")
        lines.append(f"  Drivetrain (measured): {dt}")
        if facts:
            for f in facts[-10:]:
                lines.append(f"  * {f}")
        else:
            lines.append("  * NOTHING KNOWN YET about which setup parameters this car allows. Before prescribing a mechanical "
                         "change, ask the driver what the setup screen lets him adjust (pressures, camber, toe, ARBs, springs, "
                         "dampers, diff, bias, wings...) and record it as a car_fact.")

        history = self.journal.history_summary(self.journal_key, self.session_started_at) if car else []
        if history:
            lines.append("[EARLIER SESSIONS WITH THIS CAR HERE]")
            lines.extend(history)

        if not self.completed_runs and not self._stint_lap_times:
            lines.append("[PRACTICE RUN DEBRIEF]")
            lines.append("  No full practice stint completed yet. Head out for a 4-5 lap run so we can gather baseline telemetry.")
            live = self._live_run_block()
            if live:
                lines.extend(live)
            return "\n".join(lines)

        # ---- run log
        lines.append("[PRACTICE RUN DEBRIEF — SETUP LAB RUN LOG] (flying laps only; out-laps excluded)")
        for r in self.completed_runs[-6:]:
            if r.changes_before:
                ch = f" | change before this run: {'; '.join(r.changes_before)}"
            elif r is self.completed_runs[0]:
                ch = " | baseline"
            elif r.garage_before:
                ch = " | car went to the garage before it, no change recorded or measured"
            else:
                ch = " | no change (straight back out)"
            fb = f" | fuel {r.avg_fuel_burn_l_per_lap:.2f} L/lap" if r.avg_fuel_burn_l_per_lap > 0 else ""
            best = min(r.flying_laps) if r.flying_laps else r.best_lap_time
            lines.append(
                f"  Run #{r.run_number}: {len(r.flying_laps)} flying laps | best {_fmt_lap(best)} | median {_fmt_lap(r.median_lap)}"
                f" | spread {r.lap_spread:.2f}s{fb}{ch}"
            )

        latest = self.completed_runs[-1] if self.completed_runs else None
        prev = self.completed_runs[-2] if len(self.completed_runs) >= 2 else None

        if latest:
            lines.append(f"  Run #{latest.run_number} Summary: {latest.laps_completed} laps incl. out-lap | "
                         f"Best Lap: {latest.best_lap_time:.3f}s" + (f" | Fuel Burn: {latest.avg_fuel_burn_l_per_lap:.2f} L/lap" if latest.avg_fuel_burn_l_per_lap > 0 else ""))
            if latest.start_pressures_bar:
                lines.append(("  Pressures at pit exit, cold tyres from the garage: " if latest.garage_before else
                              "  Pressures at pit exit (tyres still warm from the last run - NOT the set pressures): ") +
                             " | ".join(f"{c} {v:.2f} bar" for c, v in zip(("FL", "FR", "RL", "RR"), latest.start_pressures_bar)))
            lines.append(f"[LATEST RUN #{latest.run_number} — MEASURED HANDLING & TYRES]")
            lines.extend(format_run(latest.handling, dt))
            lines.extend(format_corner_table(latest.handling, prev.handling if prev else None))

            if prev:
                lines.append(f"  [COMPARISON VS RUN #{prev.run_number}]:")
                if latest.best_lap_time > 0 and prev.best_lap_time > 0:
                    delta = latest.best_lap_time - prev.best_lap_time
                    if delta < -0.05:
                        lines.append(f"    * Pace: Found {abs(delta):.2f}s improvement on best lap ({prev.best_lap_time:.2f}s -> {latest.best_lap_time:.2f}s).")
                    elif delta > 0.05:
                        lines.append(f"    * Pace: {delta:.2f}s slower on best lap than Run #{prev.run_number}.")
                    else:
                        lines.append("    * Pace: Identical best lap (within 0.05s).")
                if latest.median_lap and prev.median_lap:
                    noise = max(latest.lap_spread, prev.lap_spread) / 2.0
                    dm = latest.median_lap - prev.median_lap
                    sig = "SIGNIFICANT" if abs(dm) > max(0.1, noise) else "within lap-to-lap noise"
                    lines.append(f"    * Median flying lap {dm:+.2f}s ({sig}; noise ~±{noise:.2f}s)")
                lines.extend(self._handling_deltas(latest.handling, prev.handling))
                lines.append("    * Judge the change on: median lap + the handling metric it was meant to fix + the driver's feel. "
                             "If it didn't help, revert it before trying the next idea.")

            if self.pending_setup_changes:
                lines.append(f"  [LAST CHANGE THE DRIVER CONFIRMED]: {self.pending_setup_changes[-1]}")
                lines.append("  [FEEDBACK CHECK]: Proactively ask the driver how the car felt vs last run to evaluate if this change worked.")

        lines.extend(self._pressure_work_block())
        if self.proposed_change and self._proposal_run == self.next_run_number:
            lines.append(f"[YOUR CALL FOR RUN #{self._proposal_run}]: {self.proposed_change} - if the car goes to the garage before "
                         "the run it is logged as made; measured pressure changes are logged automatically. Don't ask him to report it.")

        live = self._live_run_block()
        if live:
            lines.extend(live)
        return "\n".join(lines)

    def _pressure_work_block(self) -> List[str]:
        """Pressure history over the session and whether the tyres actually responded to the changes."""
        runs = [r for r in self.completed_runs if r.handling.get("pressures_bar")]
        if not runs:
            return []

        def axle(d: Dict, cs) -> Optional[float]:
            v = [d[c] for c in cs if c in d]
            return sum(v) / len(v) if v else None

        def mid_offset(r) -> Optional[float]:
            t = r.handling.get("tyre_imo", {})
            v = [m - (i + o) / 2.0 for (i, m, o) in t.values()]
            return sum(v) / len(v) if v else None

        def avg_temp(r) -> Optional[float]:
            t = r.handling.get("tyre_imo", {})
            v = [(i + m + o) / 3.0 for (i, m, o) in t.values()]
            return sum(v) / len(v) if v else None

        out = ["[TYRE PRESSURES THIS SESSION] (set = measured cold-equivalent at 20°C; mid = middle vs shoulders, avg of 4)"]
        for r in runs[-6:]:
            hot = r.handling["pressures_bar"]
            bits = [f"hot F {axle(hot, ('FL', 'FR')):.2f} / R {axle(hot, ('RL', 'RR')):.2f} bar"]
            if r.cold_eq_bar:
                bits.insert(0, f"set F {axle(r.cold_eq_bar, ('FL', 'FR')):.2f} / R {axle(r.cold_eq_bar, ('RL', 'RR')):.2f} bar")
            mo, at = mid_offset(r), avg_temp(r)
            if mo is not None:
                bits.append(f"mid {mo:+.1f}°C")
            if at is not None:
                bits.append(f"tread avg {at:.0f}°C")
            out.append(f"  Run #{r.run_number}: " + " | ".join(bits))

        # Did the profile respond to the last pressure change?
        for a, b in reversed(list(zip(runs, runs[1:]))):
            if a.cold_eq_bar and b.cold_eq_bar:
                dp = axle(b.cold_eq_bar, ("FL", "FR", "RL", "RR")) - axle(a.cold_eq_bar, ("FL", "FR", "RL", "RR"))
            else:
                dp = axle(b.handling["pressures_bar"], ("FL", "FR", "RL", "RR")) - axle(a.handling["pressures_bar"], ("FL", "FR", "RL", "RR"))
            if abs(dp) < 0.02:
                continue
            ma, mb = mid_offset(a), mid_offset(b)
            if ma is None or mb is None:
                break
            dm = mb - ma
            if abs(dm) < 1.0:
                out.append(f"  ! PRESSURE NOT MOVING THE PROFILE: {dp:+.2f} bar between run #{a.run_number} and #{b.run_number} moved the "
                           f"middle only {dm:+.1f}°C ({ma:+.1f} -> {mb:+.1f}). The middle temp is not a pressure signal on this car "
                           "right now - stop changing pressure to fix it. Judge pressure on lap time, feel and the hot-pressure trend.")
            else:
                out.append(f"  Last pressure change {dp:+.2f} bar (run #{a.run_number} -> #{b.run_number}) moved the middle {dm:+.1f}°C.")
            break

        first, last = runs[0].handling["pressures_bar"], runs[-1].handling["pressures_bar"]
        rise = axle(last, ("FL", "FR", "RL", "RR")) - axle(first, ("FL", "FR", "RL", "RR"))
        if len(runs) >= 2 and abs(rise) >= 0.06:
            out.append(f"  Hot pressures have moved {rise:+.2f} bar since run #{runs[0].run_number}.")
        return out

    @staticmethod
    def _handling_deltas(cur: Dict, prev: Dict) -> List[str]:
        out = []
        if not cur or not prev:
            return out
        ui_c, ui_p = cur.get("ui", {}), prev.get("ui", {})
        bits = [f"{p} {ui_c[p] - ui_p[p]:+.2f}" for p in ("entry", "mid", "exit") if p in ui_c and p in ui_p]
        if bits:
            out.append(f"    * Understeer index change (+ = more understeer now): {', '.join(bits)}")
        cs = cur.get("countersteer_per_lap", 0) - prev.get("countersteer_per_lap", 0)
        ws = cur.get("wheelspin_per_lap", 0) - prev.get("wheelspin_per_lap", 0)
        lk = sum(cur.get("lockups_per_lap", {}).values()) - sum(prev.get("lockups_per_lap", {}).values())
        out.append(f"    * Per lap: counter-steer {cs:+.1f}, wheelspin {ws:+.1f}, lock-ups {lk:+.1f}")
        tc, tp = cur.get("tyre_imo", {}), prev.get("tyre_imo", {})
        tb = []
        for c in ("FL", "FR", "RL", "RR"):
            if c in tc and c in tp:
                i1, m1, o1 = tc[c]
                i0, m0, o0 = tp[c]
                tb.append(f"{c} avg {((i1 + m1 + o1) - (i0 + m0 + o0)) / 3:+.1f}°C, I-O {(i1 - o1) - (i0 - o0):+.1f}")
        if tb:
            out.append("    * Tyre temps change: " + " | ".join(tb))
        pc, pp = cur.get("pressures_bar", {}), prev.get("pressures_bar", {})
        pb = [f"{c} {pc[c] - pp[c]:+.3f}" for c in ("FL", "FR", "RL", "RR") if c in pc and c in pp]
        if pb:
            out.append("    * Hot pressure change (bar): " + " | ".join(pb))
        return out

    def _run_handling_laps(self):
        """Handling laps of the current run, out-lap excluded (the first lap finished after pit exit)."""
        return self.handling.laps_after_seq(self._run_start_seq)[1:]

    def _live_run_block(self) -> List[str]:
        if not self.is_car_on_track:
            return []
        out = []
        if self.run_changes_detected:
            out.append(f"[SETUP CHANGES SEEN FOR RUN #{self.current_run_number}] " + " | ".join(self.run_changes_detected))
        laps = self._run_handling_laps()
        if not laps:
            return out
        agg = aggregate(laps)
        return out + [f"[CURRENT RUN #{self.current_run_number} SO FAR — {len(laps)} flying lap(s)]"] + format_run(agg, self.handling.drivetrain)
