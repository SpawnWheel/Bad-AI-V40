"""
Competitor Incident and Crash Tracker for AMS2.
Monitors competitor on-track speeds to detect spins, crashes, and stopped cars.
Maintains a rolling memory of incidents in the last few minutes for engineer context.
"""

from dataclasses import dataclass
from race.track_model import sector_label as _sl
from typing import List, Dict, Tuple, Optional
import time
from telemetry.collector import ParticipantSnapshot


@dataclass
class IncidentRecord:
    driver_name: str
    position: int
    sector: int
    lap: int
    timestamp: float
    speed_kmh: float
    description: str
    is_ahead: bool = True
    relative_delta_m: float = 0.0
    projected_delta_m: float = 0.0
    likely_to_see: bool = True


class IncidentTracker:
    def __init__(
        self,
        speed_threshold_kmh: float = 20.0,
        duration_sec: float = 2.0,
        cooldown_per_car_sec: float = 60.0,
        memory_retention_sec: float = 120.0
    ):
        self.speed_threshold_kmh = speed_threshold_kmh
        self.duration_sec = duration_sec
        self.cooldown_per_car_sec = cooldown_per_car_sec
        self.memory_retention_sec = memory_retention_sec

        # driver_key -> timestamp when speed first dropped below threshold
        self._slow_start_times: Dict[str, float] = {}
        # driver_key -> timestamp when incident was last alerted
        self._last_alert_times: Dict[str, float] = {}
        # Rolling log of incidents (pruned after memory_retention_sec for live hazard warnings)
        self.incident_history: List[IncidentRecord] = []
        # Full-session log of incidents (retained for the entire race for SitRep storylines)
        self.session_incidents: List[IncidentRecord] = []

    def reset(self):
        self._slow_start_times.clear()
        self._last_alert_times.clear()
        self.incident_history.clear()
        self.session_incidents.clear()

    def update(
        self,
        participants: Tuple[ParticipantSnapshot, ...],
        player_name: str,
        current_time: float,
        player_lap_dist: float = 0.0,
        track_length_m: float = 0.0,
        player_speed_kmh: float = 0.0
    ) -> List[IncidentRecord]:
        """
        Analyzes live participant speeds.
        Calculates relative position to player, compensating for ~4s AI audio pipeline latency.
        Returns newly detected incident records.
        """
        if not participants:
            return []

        # Prune old incidents outside memory retention window
        cutoff = current_time - self.memory_retention_sec
        self.incident_history = [inc for inc in self.incident_history if inc.timestamp >= cutoff]

        newly_detected: List[IncidentRecord] = []
        player_key = player_name.strip().lower() if player_name else "driver"

        for p in participants:
            if not p.is_active or p.position <= 0:
                continue

            driver_key = p.name.strip().lower() if p.name else f"driver_{p.position}"

            # Ignore player
            if driver_key == player_key or (player_name and player_name.lower() in p.name.lower()):
                continue

            # In the pits or pit lane, cars naturally drive slow / stop
            if p.pit_mode > 0:
                self._slow_start_times.pop(driver_key, None)
                continue

            # Check if car is stopped or crawling on track
            if p.speed_kmh < self.speed_threshold_kmh:
                if driver_key not in self._slow_start_times:
                    self._slow_start_times[driver_key] = current_time
                else:
                    elapsed = current_time - self._slow_start_times[driver_key]
                    if elapsed >= self.duration_sec:
                        # One record per continuous stop (a parked / retired car is not a new spin every minute)
                        last_alert = self._last_alert_times.get(driver_key, 0.0)
                        already_reported_this_stop = last_alert >= self._slow_start_times[driver_key]
                        if not already_reported_this_stop and current_time - last_alert >= self.cooldown_per_car_sec:
                            self._last_alert_times[driver_key] = current_time

                            # Compute relative track position with 4.0s AI pipeline latency compensation
                            is_ahead = True
                            likely_to_see = True
                            relative_delta_m = 0.0
                            projected_delta_m = 0.0

                            if track_length_m > 100.0 and p.lap_distance > 0.0:
                                dist_ahead = (p.lap_distance - player_lap_dist) % track_length_m
                                if dist_ahead < (track_length_m / 2.0):
                                    is_ahead = True
                                    relative_delta_m = dist_ahead
                                    player_speed_mps = max(0.0, player_speed_kmh / 3.6)
                                    projected_delta_m = max(0.0, dist_ahead - (player_speed_mps * 4.0))

                                    if projected_delta_m <= 50.0:
                                        location_desc = f"right ahead of you now in Sector {_sl(p.sector)}—watch your line"
                                        likely_to_see = True
                                    elif projected_delta_m < 350.0:
                                        location_desc = f"ahead in Sector {_sl(p.sector)}, coming up fast (~{projected_delta_m:.0f}m ahead)—you will encounter him"
                                        likely_to_see = True
                                    else:
                                        location_desc = f"ahead in Sector {_sl(p.sector)} (~{projected_delta_m:.0f}m ahead)"
                                        likely_to_see = True
                                else:
                                    is_ahead = False
                                    relative_delta_m = track_length_m - dist_ahead
                                    projected_delta_m = relative_delta_m
                                    likely_to_see = False
                                    location_desc = f"behind you in Sector {_sl(p.sector)} (~{relative_delta_m:.0f}m back), track ahead is clear"
                            else:
                                location_desc = f"in Sector {_sl(p.sector)} ({p.speed_kmh:.0f} km/h)"

                            desc = f"P{p.position} {p.name} stopped or spun {location_desc}."
                            record = IncidentRecord(
                                driver_name=p.name,
                                position=p.position,
                                sector=p.sector,
                                lap=p.lap,
                                timestamp=current_time,
                                speed_kmh=p.speed_kmh,
                                description=desc,
                                is_ahead=is_ahead,
                                relative_delta_m=relative_delta_m,
                                projected_delta_m=projected_delta_m,
                                likely_to_see=likely_to_see
                            )
                            self.incident_history.append(record)
                            self.session_incidents.append(record)
                            newly_detected.append(record)
            else:
                # Car is moving normally
                self._slow_start_times.pop(driver_key, None)

        return newly_detected

    def get_recent_incidents_summary(self, current_time: float) -> str:
        """Formats recent track incidents into a concise summary block for telemetry context."""
        cutoff = current_time - self.memory_retention_sec
        recent = [inc for inc in self.incident_history if inc.timestamp >= cutoff]
        if not recent:
            return "Track clear. No recent on-track incidents reported."

        lines = ["[RECENT TRACK INCIDENTS (LAST 2 MIN)]:"]
        for inc in recent[-5:]:  # show up to 5 most recent
            age_sec = int(current_time - inc.timestamp)
            time_str = f"{age_sec}s ago" if age_sec < 60 else f"{age_sec // 60}m {age_sec % 60}s ago"
            lines.append(f"- P{inc.position} {inc.driver_name} in Sector {_sl(inc.sector)} (stopped/spun, {time_str})")
        return "\n".join(lines)

    def get_race_storylines_summary(
        self,
        participants: Tuple[ParticipantSnapshot, ...] = (),
        current_time: Optional[float] = None
    ) -> str:
        """
        Formats notable spins/incidents from earlier in the race along with how
        each affected driver is currently doing (current position, positions gained/lost, and pace).
        """
        if not self.session_incidents:
            return ""

        def fmt_t(sec: float) -> str:
            if sec <= 0.001 or sec > 3600.0:
                return "--:--"
            m = int(sec // 60)
            s = sec % 60
            return f"{m}:{s:06.3f}" if m > 0 else f"{s:.3f}s"

        part_map: Dict[str, ParticipantSnapshot] = {}
        for p in participants:
            if p.name:
                part_map[p.name.strip().lower()] = p

        lines = ["[RACE STORYLINES & INCIDENT RECOVERY LOG (FULL RACE)]:"]
        # collapse repeats of the same driver on the same lap
        merged = []
        for inc in self.session_incidents:
            if merged and merged[-1].driver_name == inc.driver_name and merged[-1].lap == inc.lap:
                continue
            merged.append(inc)
        for inc in merged[-8:]:
            d_key = inc.driver_name.strip().lower()
            curr_p = part_map.get(d_key)
            lap_label = f"Lap {inc.lap}" if inc.lap > 0 else "earlier"
            if curr_p:
                pos_diff = inc.position - curr_p.position  # positive = gained positions since spin, negative = lost positions
                if pos_diff < 0:
                    diff_str = f"down {abs(pos_diff)} place{'s' if abs(pos_diff) > 1 else ''} since incident"
                elif pos_diff > 0:
                    diff_str = f"recovered +{pos_diff} place{'s' if pos_diff > 1 else ''} since incident"
                else:
                    diff_str = "back in same position"
                pace_info = f", Last Lap: {fmt_t(curr_p.last_lap_time)}" if curr_p.last_lap_time > 0 else ""
                pit_info = " [currently IN PITS]" if curr_p.pit_mode > 0 else ""
                lines.append(
                    f"- {inc.driver_name}: Spun/stopped on {lap_label} (Sector {_sl(inc.sector)}) while running P{inc.position} "
                    f"-> Now P{curr_p.position} ({diff_str}{pace_info}){pit_info}"
                )
            else:
                lines.append(
                    f"- {inc.driver_name}: Spun/stopped on {lap_label} (Sector {_sl(inc.sector)}) while running P{inc.position}"
                )
        return "\n".join(lines)


incident_tracker = IncidentTracker()