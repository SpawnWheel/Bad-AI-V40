"""
Qualifying session tracker for AMS2.
Focuses strictly on timings: leaderboard position, gaps, sector deltas (where fast/slow),
and strictly excludes pit outlaps from qualifying runs.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from telemetry.collector import TelemetrySnapshot, ParticipantSnapshot
from utils.logging import logger


@dataclass
class QualiRunRecord:
    run_number: int
    lap_number: int
    lap_time: float
    is_personal_best: bool
    position_at_completion: int
    gap_to_pole_sec: float
    s1: float
    s2: float
    s3: float
    s1_delta_to_best: float   # negative = faster than PB
    s2_delta_to_best: float
    s3_delta_to_best: float
    sector_analysis: str       # Summary of where driver gained or lost time


class QualiTracker:
    """
    Manages qualifying session timing, detects outlaps, records valid flying runs,
    and isolates sector performance (where fast, where slow).
    """

    def __init__(self):
        self.completed_runs: List[QualiRunRecord] = []
        self.personal_best_lap: float = 0.0
        self.pb_s1: float = 0.0
        self.pb_s2: float = 0.0
        self.pb_s3: float = 0.0

        # Outlap state
        self._was_in_pit: bool = True
        self._is_outlap: bool = True
        self._last_lap: int = 0
        self._current_run_count: int = 0

    def reset(self):
        """Resets qualifying tracking for a fresh session."""
        self.completed_runs.clear()
        self.personal_best_lap = 0.0
        self.pb_s1 = 0.0
        self.pb_s2 = 0.0
        self.pb_s3 = 0.0
        self._was_in_pit = True
        self._is_outlap = True
        self._last_lap = 0
        self._current_run_count = 0
        logger.info("QualiTracker reset for new qualifying session.")

    def on_telemetry(self, snapshot: TelemetrySnapshot) -> Optional[QualiRunRecord]:
        """
        Processes each snapshot. Returns a QualiRunRecord if a valid flying lap
        was just completed (ignores pit outlaps).
        """
        curr_pit = snapshot.pit_mode.value > 0

        # Detect pit exit
        if self._was_in_pit and not curr_pit:
            # Car just left pit lane -> this first lap is an OUTLAP
            self._is_outlap = True
            logger.info(f"[QUALI] Car exited pits on Lap {snapshot.current_lap}. Marked as OUTLAP.")

        elif curr_pit:
            self._is_outlap = True

        self._was_in_pit = curr_pit

        # Detect lap transition
        record_created = None
        if snapshot.current_lap > self._last_lap and self._last_lap > 0:
            lap_time = snapshot.last_lap_time

            if self._is_outlap:
                logger.info(f"[QUALI] Completed outlap {self._last_lap} ({lap_time:.2f}s). Excluded from quali runs. Now on flying lap.")
                self._is_outlap = False  # The lap just starting is the flying lap
            else:
                # Valid hot lap / qualifying run!
                if lap_time > 15.0 and not snapshot.lap_invalidated:
                    self._current_run_count += 1
                    record_created = self._record_flying_lap(snapshot, lap_time)

        self._last_lap = snapshot.current_lap
        return record_created

    def _record_flying_lap(self, snapshot: TelemetrySnapshot, lap_time: float) -> QualiRunRecord:
        """Records a valid flying lap and analyzes sector pace."""
        s1 = snapshot.sector1_time
        s2 = snapshot.sector2_time
        s3 = snapshot.sector3_time

        is_pb = (self.personal_best_lap <= 0.0 or lap_time < self.personal_best_lap)
        if is_pb:
            self.personal_best_lap = lap_time

        # Update PB sectors
        if self.pb_s1 <= 0.0 or (0.0 < s1 < self.pb_s1):
            self.pb_s1 = s1
        if self.pb_s2 <= 0.0 or (0.0 < s2 < self.pb_s2):
            self.pb_s2 = s2
        if self.pb_s3 <= 0.0 or (0.0 < s3 < self.pb_s3):
            self.pb_s3 = s3

        # Compare sectors to PB
        s1_delta = (s1 - self.pb_s1) if self.pb_s1 > 0 else 0.0
        s2_delta = (s2 - self.pb_s2) if self.pb_s2 > 0 else 0.0
        s3_delta = (s3 - self.pb_s3) if self.pb_s3 > 0 else 0.0

        # Find pole position car
        pole_time = 0.0
        for p in snapshot.participants:
            if p.position == 1 and p.best_lap_time > 0:
                pole_time = p.best_lap_time
                break

        gap_to_pole = (lap_time - pole_time) if pole_time > 0 else 0.0

        # Identify where fast and where slow
        sector_comments = []
        if s1_delta <= 0.05:
            sector_comments.append("S1 strong")
        else:
            sector_comments.append(f"lost +{s1_delta:.2f}s in S1")

        if s2_delta <= 0.05:
            sector_comments.append("S2 strong")
        else:
            sector_comments.append(f"lost +{s2_delta:.2f}s in S2")

        if s3_delta <= 0.05:
            sector_comments.append("S3 strong")
        else:
            sector_comments.append(f"lost +{s3_delta:.2f}s in S3")

        analysis_str = ", ".join(sector_comments)

        record = QualiRunRecord(
            run_number=self._current_run_count,
            lap_number=self._last_lap,
            lap_time=lap_time,
            is_personal_best=is_pb,
            position_at_completion=snapshot.position,
            gap_to_pole_sec=gap_to_pole,
            s1=s1,
            s2=s2,
            s3=s3,
            s1_delta_to_best=s1_delta,
            s2_delta_to_best=s2_delta,
            s3_delta_to_best=s3_delta,
            sector_analysis=analysis_str
        )
        self.completed_runs.append(record)
        logger.info(f"[QUALI] Recorded Flying Run #{record.run_number}: {record.lap_time:.3f}s (P{record.position_at_completion}). {analysis_str}.")
        return record

    def get_quali_status_summary(self, snapshot: TelemetrySnapshot) -> str:
        """
        Builds a pure timing summary for qualifying context:
        Position, gap to pole, gap to car ahead, and sector breakdown.
        """
        lines = ["[QUALIFYING TIMINGS & LEADERBOARD]"]

        # 1. Leaderboard position & gaps
        pole_p = next((p for p in snapshot.participants if p.position == 1), None)
        pole_name = pole_p.name if pole_p else "P1"
        pole_best = pole_p.best_lap_time if pole_p else 0.0

        ahead_p = next((p for p in snapshot.participants if p.position == (snapshot.position - 1)), None)

        pos_str = f"You are P{snapshot.position}"
        if snapshot.position == 1:
            pos_str += " (PROVISIONAL POLE)"
        lines.append(f"  Position: {pos_str}")

        if snapshot.best_lap_time > 0:
            def fmt_t(s: float) -> str:
                if s <= 0:
                    return "--:--"
                m = int(s // 60)
                sec = s % 60
                return f"{m}:{sec:06.3f}" if m > 0 else f"{sec:.3f}s"

            lines.append(f"  Your Best: {fmt_t(snapshot.best_lap_time)}")
            if pole_best > 0 and snapshot.position > 1:
                gap_pole = snapshot.best_lap_time - pole_best
                lines.append(f"  Pole Time: {fmt_t(pole_best)} by {pole_name} (+{gap_pole:.3f}s to Pole)")
            elif snapshot.position == 1:
                lines.append("  You hold the fastest lap of the session.")

            if ahead_p and ahead_p.best_lap_time > 0:
                gap_ahead = snapshot.best_lap_time - ahead_p.best_lap_time
                lines.append(f"  Gap to P{ahead_p.position} ({ahead_p.name}): +{gap_ahead:.3f}s")
        else:
            lines.append("  Your Best: No timed flying lap set yet.")

        # 2. Sector Analysis (Where fast vs where slow)
        lines.append("  [SECTOR ANALYSIS — WHERE FAST / WHERE SLOW]:")
        s1_best = snapshot.best_sector1_time
        s2_best = snapshot.best_sector2_time
        s3_best = snapshot.best_sector3_time

        def fmt_s(s: float) -> str:
            return f"{s:.3f}s" if s > 0 else "--.-"

        lines.append(f"    * Your Optimal Sectors: S1 {fmt_s(s1_best)} | S2 {fmt_s(s2_best)} | S3 {fmt_s(s3_best)}")

        if self.completed_runs:
            latest = self.completed_runs[-1]
            lines.append(f"    * Latest Flying Run (#{latest.run_number}): {latest.lap_time:.3f}s | {latest.sector_analysis}")
            if latest.is_personal_best:
                lines.append("    * Pace note: That was your personal best tour.")
        else:
            lines.append("    * Outlap in progress. Prepare for your first flying run.")

        # 3. Session time remaining
        if snapshot.event_time_remaining_sec > 0:
            m = int(snapshot.event_time_remaining_sec // 60)
            s = int(snapshot.event_time_remaining_sec % 60)
            lines.append(f"  Time Remaining on Clock: {m}m {s:02d}s")

        return "\n".join(lines)
