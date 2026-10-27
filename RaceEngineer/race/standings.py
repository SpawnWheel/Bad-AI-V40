"""
Competitor Standings and Pit Wall Timing Board.
Analyzes live competitor laptimes, sector deltas, gaps, pit status,
and generates catch-up lap predictions for the race engineer.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
from telemetry.collector import TelemetrySnapshot, ParticipantSnapshot, SessionState


def fmt_time(sec: float) -> str:
    """Format lap time as M:SS.mmm or --:--."""
    if sec <= 0.001 or sec > 3600.0:
        return "--:--"
    m = int(sec // 60)
    s = sec % 60
    return f"{m}:{s:06.3f}" if m > 0 else f"{s:.3f}s"


def fmt_sec(sec: float) -> str:
    """Format sector time as S.sss or --.-."""
    if sec <= 0.001 or sec > 600.0:
        return "--.-"
    return f"{sec:.3f}"


@dataclass
class CompetitorEntry:
    position: int
    name: str
    is_player: bool
    last_lap: float
    best_lap: float
    s1: float
    s2: float
    s3: float
    gap_to_player_sec: float  # Positive = ahead of player, Negative = behind player, 0.0 = player
    in_pits: bool
    pace_delta_sec: float     # player_last_lap - competitor_last_lap (Negative = player faster)
    laps_to_catch: Optional[float]  # Estimated laps until catch (if catching)
    pit_stops: int = 0
    pace_basis: str = "last"        # "last" | "best" (a last lap had a stop / incident)
    pit_note: str = ""              # projection for a car currently in the pit lane
    car: str = ""                   # RaceRoom: what this rival drives
    rating: float = 0.0             # RaceRoom online driver rating / reputation (0 = not reported, e.g. AI)
    reputation: float = 0.0


class CompetitorTimingBoard:
    """Tracks competitor pace, pit status, pit stop history, and generates the live pit-wall timing table."""

    def __init__(self):
        self.last_table_str: str = "No competitor timing data available."
        self._participant_pit_state: dict[str, bool] = {}  # driver_key -> was_in_pit
        self._participant_pit_stops: dict[str, int] = {}   # driver_key -> total_stops_count
        self._last_session: SessionState = SessionState.SESSION_INVALID
        self._last_lap: int = -1
        self.track_model = None      # set by RaceStateManager (true time gaps from the reference lap)
        self.pit_projector = None    # callable(participant, gap_sec) -> str for cars in the pit lane

    def reset(self):
        """Resets all pit stop and timing tracking."""
        self._participant_pit_state.clear()
        self._participant_pit_stops.clear()
        self.last_table_str = "No competitor timing data available."
        self._last_session = SessionState.SESSION_INVALID
        self._last_lap = -1

    def _update_driver_pit_stops(self, key: str, is_in_pit: bool) -> int:
        """Tracks pit transitions and counts completed pit stops."""
        if key not in self._participant_pit_state:
            # First time seeing this car in the session
            self._participant_pit_state[key] = is_in_pit
            self._participant_pit_stops[key] = 0
            return 0

        was_in_pit = self._participant_pit_state[key]
        if not was_in_pit and is_in_pit:
            # Transitioned from track into pit lane -> new pit stop
            self._participant_pit_stops[key] = self._participant_pit_stops.get(key, 0) + 1

        self._participant_pit_state[key] = is_in_pit
        return self._participant_pit_stops.get(key, 0)

    def stops_for(self, name: str) -> Optional[int]:
        """Completed/started pit stops counted for a driver this race (None = car not seen yet)."""
        key = (name or "").strip().lower()
        if key not in self._participant_pit_stops:
            return None
        return self._participant_pit_stops.get(key, 0)

    def any_competitor_stopped(self, exclude: str = "") -> bool:
        ex = (exclude or "").strip().lower()
        return any(n > 0 for k, n in self._participant_pit_stops.items() if k != ex)

    def update_pit_counts(self, snapshot: TelemetrySnapshot):
        """Called on every telemetry tick so no pit entry is missed between radio calls."""
        if not snapshot or not snapshot.participants:
            return
        curr_is_race = snapshot.session_state in (SessionState.SESSION_FORMATION_LAP, SessionState.SESSION_RACE)
        last_is_race = self._last_session in (SessionState.SESSION_FORMATION_LAP, SessionState.SESSION_RACE)
        if self._last_session != SessionState.SESSION_INVALID and not (curr_is_race and last_is_race) and snapshot.session_state != self._last_session:
            self.reset()
        self._last_session = snapshot.session_state
        pname = snapshot.player_name.strip().lower() if snapshot.player_name else "player"
        for p in snapshot.participants:
            if not p.is_active or p.position <= 0:
                continue
            is_p = (p.position == snapshot.position or p.name.strip().lower() == pname)
            in_p = (snapshot.pit_mode.value > 0) if is_p else (p.pit_mode > 0)
            key = pname if is_p else (p.name.strip().lower() if p.name else f"driver_{p.position}")
            self._update_driver_pit_stops(key, in_p)

    def analyze(self, snapshot: TelemetrySnapshot) -> Tuple[List[CompetitorEntry], str]:
        """
        Extracts key competitors (Leader, Car Ahead, Player, Car Behind, Last Place) and generates
        a compact timing board table string for Gemini context with live pit stop counts.
        """
        if not snapshot or not snapshot.participants:
            return [], "No competitor timing data available."

        # Check for session change or session restart
        # Note: Countdown, Formation Lap, Race, and Cooldown are all part of the RACE group
        # and do not wipe competitor pit stops or timing boards.
        curr_session = snapshot.session_state
        curr_is_race = curr_session in (SessionState.SESSION_FORMATION_LAP, SessionState.SESSION_RACE)
        last_is_race = self._last_session in (SessionState.SESSION_FORMATION_LAP, SessionState.SESSION_RACE)

        if self._last_session != SessionState.SESSION_INVALID:
            if curr_is_race and last_is_race:
                # Both formation lap and active race belong to the same race session
                pass
            elif curr_session != self._last_session:
                self.reset()
        if snapshot.current_lap == 1 and self._last_lap > 2:
            self.reset()

        self._last_session = snapshot.session_state
        self._last_lap = snapshot.current_lap

        active_parts = [p for p in snapshot.participants if p.is_active and p.position > 0]
        if not active_parts:
            return [], "No active competitors found on timing screens."

        # Sort by position
        sorted_parts = sorted(active_parts, key=lambda p: p.position)

        player_pos = snapshot.position
        player_last_lap = snapshot.last_lap_time
        player_best_lap = snapshot.best_lap_time

        # Update pit stops for ALL active cars on the grid
        driver_pit_stops: dict[str, int] = {}
        for p in sorted_parts:
            is_p = (p.position == player_pos or p.name.strip().lower() == snapshot.player_name.strip().lower())
            in_p = (snapshot.pit_mode.value > 0) if is_p else (p.pit_mode > 0)
            d_key = (snapshot.player_name.strip().lower() if snapshot.player_name else "player") if is_p else (p.name.strip().lower() if p.name else f"driver_{p.position}")
            stops = self._update_driver_pit_stops(d_key, in_p)
            driver_pit_stops[d_key] = stops

        total_grid_count = len(sorted_parts)
        last_car = sorted_parts[-1]

        # Target positions:
        # 1. Top 5 cars (P1 through P5)
        # 2. Up to 6 cars ahead of the player
        # 3. The player
        # 4. Up to 6 cars behind the player
        # 5. Last place car
        target_positions = set(range(1, min(6, total_grid_count + 1)))
        target_positions.update(range(max(1, player_pos - 6), player_pos))
        target_positions.add(player_pos)
        target_positions.update(range(player_pos + 1, min(total_grid_count + 1, player_pos + 7)))
        target_positions.add(last_car.position)

        # Pre-calculate player speed estimate for physical gap calculations
        player_p = next(
            (part for part in sorted_parts if part.position == player_pos or (snapshot.player_name and snapshot.player_name.strip().lower() in part.name.strip().lower())),
            None
        )
        pace = player_best_lap if player_best_lap > 20 else (player_last_lap if player_last_lap > 20 else 90.0)
        track_len = snapshot.track_length_m if snapshot.track_length_m > 100 else 4000.0
        avg_speed_mps = track_len / pace

        entries: List[CompetitorEntry] = []

        for p in sorted_parts:
            if p.position not in target_positions:
                continue

            is_player = (p.position == player_pos or p.name.strip().lower() == snapshot.player_name.strip().lower())
            driver_key = (snapshot.player_name.strip().lower() if snapshot.player_name else "player") if is_player else (p.name.strip().lower() if p.name else f"driver_{p.position}")
            pit_stops = driver_pit_stops.get(driver_key, 0)

            if is_player:
                gap_sec = 0.0
                last_lap = player_last_lap
                best_lap = player_best_lap
                s1 = snapshot.sector1_time
                s2 = snapshot.sector2_time
                s3 = snapshot.sector3_time
                in_pits = (snapshot.pit_mode.value > 0)
            else:
                last_lap = p.last_lap_time
                best_lap = p.best_lap_time
                s1 = p.sector1_time
                s2 = p.sector2_time
                s3 = p.sector3_time
                in_pits = (p.pit_mode > 0)

                if p.position == player_pos - 1 and snapshot.split_time_ahead > 0:
                    gap_sec = snapshot.split_time_ahead
                elif p.position == player_pos + 1 and snapshot.split_time_behind > 0:
                    gap_sec = -snapshot.split_time_behind
                elif player_p and (player_p.lap > 0 or player_p.lap_distance > 0) and (p.lap > 0 or p.lap_distance > 0):
                    p_dist = p.lap * track_len + p.lap_distance
                    player_dist = player_p.lap * track_len + player_p.lap_distance
                    dist_diff = p_dist - player_dist
                    tm = self.track_model
                    if tm is not None and tm.has_reference:
                        gap_sec = tm.gap_seconds(p_dist, player_dist, pace) if dist_diff >= 0 else -tm.gap_seconds(player_dist, p_dist, pace)
                    else:
                        gap_sec = dist_diff / avg_speed_mps
                else:
                    gap_sec = (p.position - player_pos) * -2.5

            pace_delta = 0.0
            laps_to_catch = None
            pace_basis = "last"
            pit_note = ""
            # A lap with a pit stop / spin is not "pace": fall back to best laps when either last lap is off
            me_rep = player_last_lap if (player_last_lap > 10 and (player_best_lap <= 10 or player_last_lap <= player_best_lap * 1.05)) else 0.0
            them_rep = last_lap if (last_lap > 10 and (best_lap <= 10 or last_lap <= best_lap * 1.05)) else 0.0
            if not is_player and (me_rep <= 0 or them_rep <= 0) and player_best_lap > 10 and best_lap > 10:
                me_cmp, them_cmp, pace_basis = player_best_lap, best_lap, "best"
            else:
                me_cmp, them_cmp = me_rep, them_rep
            if not is_player and in_pits and self.pit_projector is not None:
                try:
                    pit_note = self.pit_projector(p, gap_sec) or ""
                except Exception:
                    pit_note = ""
            if not is_player and me_cmp > 10.0 and them_cmp > 10.0:
                pace_delta = me_cmp - them_cmp
                if gap_sec > 0 and pace_delta < -0.05:
                    catch_rate = abs(pace_delta)
                    laps_to_catch = max(1.0, gap_sec / catch_rate)
                elif gap_sec < 0 and pace_delta > 0.05:
                    closing_rate = pace_delta
                    laps_to_catch = max(1.0, abs(gap_sec) / closing_rate)

            entries.append(CompetitorEntry(
                position=p.position,
                name=p.name,
                is_player=is_player,
                last_lap=last_lap,
                best_lap=best_lap,
                s1=s1,
                s2=s2,
                s3=s3,
                gap_to_player_sec=gap_sec,
                in_pits=in_pits,
                pace_delta_sec=pace_delta,
                laps_to_catch=laps_to_catch,
                pit_stops=pit_stops,
                pace_basis=pace_basis,
                pit_note=pit_note,
                car=getattr(p, "car_name", "") or "",
                rating=float(getattr(p, "rating", 0.0) or 0.0),
                reputation=float(getattr(p, "reputation", 0.0) or 0.0),
            ))

        table_str = self._format_table(entries, sorted_parts, player_pos)
        self.last_table_str = table_str
        return entries, table_str

    def _format_table(self, entries: List[CompetitorEntry], sorted_parts: List[ParticipantSnapshot], player_pos: int) -> str:
        if not entries:
            return "No competitor data."

        total_grid_count = len(sorted_parts)
        leader_name = sorted_parts[0].name if sorted_parts else "N/A"
        last_car = sorted_parts[-1] if sorted_parts else None
        last_name = last_car.name if last_car else "N/A"
        last_pos = last_car.position if last_car else total_grid_count

        full_grid_snippet = ", ".join([f"P{p.position} {p.name.split()[-1] if p.name else 'Driver'}" for p in sorted_parts])

        # Pitted cars summary across full field
        pitted_cars = []
        for p in sorted_parts:
            d_key = p.name.strip().lower() if p.name else f"driver_{p.position}"
            stops = self._participant_pit_stops.get(d_key, 0)
            if stops > 0:
                surname = p.name.split()[-1] if p.name else "Driver"
                pitted_cars.append(f"P{p.position} {surname} ({stops} stop{'s' if stops > 1 else ''})")

        pitted_str = f"  Cars Pitted: {', '.join(pitted_cars)}" if pitted_cars else "  Cars Pitted: None yet"

        lines = [
            f"[GRID SUMMARY]: {total_grid_count} cars active in session.",
            f"  Leader: P1 {leader_name} | Last Place: P{last_pos} {last_name}",
            pitted_str,
            f"  Full Field Order: {full_grid_snippet}",
            "[COMPETITOR TIMING & PACE BOARD]",
            f"{'Pos':<4} | {'Driver':<16} | {'Last Lap':<9} | {'Best Lap':<9} | {'S1/S2/S3 Splits':<17} | {'Gap to You':<12} | {'Stops':<10} | {'Pace vs You'}"
        ]

        for e in entries:
            star = "*" if e.is_player else " "
            pos_tag = f"P{e.position}{star}"
            if e.position == last_pos and not e.is_player:
                pos_tag = f"P{e.position}L"
            name_str = (e.name[:15] + "..") if len(e.name) > 16 else e.name
            last_str = fmt_time(e.last_lap)
            best_str = fmt_time(e.best_lap)
            splits_str = f"{fmt_sec(e.s1)}/{fmt_sec(e.s2)}/{fmt_sec(e.s3)}"

            if e.in_pits:
                pit_str = f"IN PIT ({e.pit_stops})"
            else:
                pit_str = f"{e.pit_stops} stop" if e.pit_stops == 1 else f"{e.pit_stops} stops"

            if e.is_player:
                gap_str = "--- (YOU)"
                pace_str = "Baseline"
            else:
                gap_sign = "+" if e.gap_to_player_sec > 0 else "-"
                gap_str = f"{gap_sign}{abs(e.gap_to_player_sec):.1f}s" if abs(e.gap_to_player_sec) > 0.05 else "Tied"

                if e.pace_delta_sec < -0.1:
                    catch_info = f" (catch in ~{e.laps_to_catch:.0f} laps)" if e.laps_to_catch and e.gap_to_player_sec > 0 else ""
                    pace_str = f"You are {abs(e.pace_delta_sec):.1f}s/lap faster{catch_info}"
                elif e.pace_delta_sec > 0.1:
                    closing_info = f" (closing by {e.pace_delta_sec:.1f}s/lap)" if e.gap_to_player_sec < 0 else ""
                    pace_str = f"Competitor {e.pace_delta_sec:.1f}s/lap faster{closing_info}"
                else:
                    pace_str = "Equal pace (within 0.1s)"
                if e.pace_basis == "best":
                    pace_str += " [best-lap comparison: a last lap had a stop/incident]"
                if e.pit_note:
                    pace_str += f" | {e.pit_note}"
                if e.car:
                    pace_str += f" | Car: {e.car}"
                if e.rating > 0 or e.reputation > 0:
                    pace_str += f" | Rating {e.rating:.0f}, Reputation {e.reputation:.0f}"
                elif e.car:
                    pace_str += " | no online rating (AI or unranked)"

            lines.append(
                f"{pos_tag:<4} | {name_str:<16} | {last_str:<9} | {best_str:<9} | {splits_str:<17} | {gap_str:<12} | {pit_str:<10} | {pace_str}"
            )

        return "\n".join(lines)


timing_board = CompetitorTimingBoard()
