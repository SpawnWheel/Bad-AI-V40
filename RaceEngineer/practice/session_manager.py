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

    @property
    def is_in_pits(self) -> bool:
        return not self.is_car_on_track

    def mark_debrief_delivered(self):
        """Called when debrief has been delivered to driver over radio."""
        self.stint_completed_since_last_debrief = False

    def record_setup_change(self, change_desc: str):
        """Records a setup change recommended to the driver for follow-up on next stint."""
        self.pending_setup_changes.append(change_desc)

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
        logger.info("Practice Session Manager reset for new session.")

    @property
    def journal_key(self) -> str:
        return self.journal.session_key(self.car, self.track, self.variation)

    def on_telemetry(self, snapshot: TelemetrySnapshot):
        """Processes live snapshot to track pit exits and pit entries."""
        curr_pit_mode = snapshot.pit_mode.value
        in_pit = curr_pit_mode > 0

        if snapshot.car_name:
            self.car, self.track, self.variation = snapshot.car_name, snapshot.track_name, snapshot.track_variation

        # Feed the 10Hz monitors
        self.monitor.on_telemetry(snapshot)
        try:
            self.handling.on_telemetry(snapshot)
        except Exception as e:
            logger.debug(f"handling analyzer error: {e}")
        # Pressures as the car leaves the box = the set (cold-ish) pressures for this run
        if self.is_car_on_track and not self._pressures_captured and snapshot.speed_kmh > 5:
            if any(p > 0.3 for p in snapshot.tyre_pressures_bar):
                self._run_start_pressures = tuple(snapshot.tyre_pressures_bar)
                self._pressures_captured = True

        # Detect transition: Pit -> Track (Run starts)
        if self._last_pit_mode > 0 and curr_pit_mode == 0:
            self.is_car_on_track = True
            self._stint_start_lap = snapshot.current_lap
            self._stint_start_fuel = snapshot.fuel_liters
            self._stint_lap_times.clear()
            self._stint_lap_consumptions.clear()
            self._fuel_lap_start = snapshot.fuel_liters
            self.monitor.reset_run()
            self._pressures_captured = False
            self._run_start_pressures = tuple(snapshot.tyre_pressures_bar) if any(p > 0.3 for p in snapshot.tyre_pressures_bar) else ()
            self._pressures_captured = bool(self._run_start_pressures)
            logger.info(f"[PRACTICE] Pit exit detected. Starting Run #{self.current_run_number} (Fuel: {snapshot.fuel_liters:.1f}L).")

        # Detect transition: Track -> Pit (Run finishes)
        elif self._last_pit_mode == 0 and curr_pit_mode > 0 and self.is_car_on_track:
            self._finalize_current_run(snapshot)
            self.is_car_on_track = False

        # Lap completion tracking during active stint
        if self.is_car_on_track and snapshot.current_lap > self._last_completed_lap and self._last_completed_lap > 0:
            if snapshot.last_lap_time > 15.0:
                self._stint_lap_times.append(snapshot.last_lap_time)

            # Record lap fuel consumption
            if self._fuel_lap_start is not None:
                consumed = self._fuel_lap_start - snapshot.fuel_liters
                if 0.5 <= consumed <= 15.0:
                    self._stint_lap_consumptions.append(consumed)
            self._fuel_lap_start = snapshot.fuel_liters

        self._last_completed_lap = snapshot.current_lap
        self._last_pit_mode = curr_pit_mode

    def _finalize_current_run(self, snapshot: TelemetrySnapshot):
        """Archives the completed stint into completed_runs."""
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
        run_laps = self.handling.laps_since(self._stint_start_lap + 1)
        handling_agg = aggregate(run_laps) if run_laps else {}
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
            ch = f" | change before this run: {'; '.join(r.changes_before)}" if r.changes_before else (" | baseline" if r.run_number == 1 else " | no change recorded")
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
                lines.append("  Pressures leaving the box (set pressures) this run: " +
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
                lines.append(f"  [LAST SETUP RECOMMENDATION]: {self.pending_setup_changes[-1]}")
                lines.append("  [FEEDBACK CHECK]: Proactively ask the driver how the car felt vs last run to evaluate if this change worked.")

        live = self._live_run_block()
        if live:
            lines.extend(live)
        return "\n".join(lines)

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

    def _live_run_block(self) -> List[str]:
        if not self.is_car_on_track:
            return []
        laps = self.handling.laps_since(self._stint_start_lap + 1)
        if not laps:
            return []
        agg = aggregate(laps)
        return [f"[CURRENT RUN #{self.current_run_number} SO FAR — {len(laps)} flying lap(s)]"] + format_run(agg, self.handling.drivetrain)
