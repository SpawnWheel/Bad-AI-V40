"""
Race distance model: exactly how much racing is left for the player, in fractional laps.

Lap races:   the leader finishes at the end of lap N; everybody else takes the flag the next time they
             cross the line.
Timed races (AMS2 rule): when the clock hits zero the leader completes the lap in progress, then
             `extra_laps` more complete laps (mSessionAdditionalLaps: 0 = no extra lap, 1 = "+1 lap").
             Everybody else takes the flag the first time they cross the line after the leader.

Pit stops still to come matter in timed races:
  * leader_time_loss_sec - a stop the leader has not made yet: the leader covers less distance before the
                           clock runs out (that's what decides the final lap) and takes longer to finish it.
  * player_time_loss_sec - our own upcoming stop: we drop back relative to the leader, which can only
                           make our race shorter (the leader's clock is not affected by our stop).

Distances are in "laps completed" units: D = (current_lap - 1) + lap_distance / track_length.
"""

import math
from dataclasses import dataclass
from typing import Optional

EPS = 1e-4
LEADER_FINISHED_STATE = 3  # RaceState.RACESTATE_FINISHED (participant race_state)


@dataclass
class RaceDistance:
    mode: str                     # "LAPS" | "TIMED" | "NONE"
    laps_to_go: float             # fractional laps from where the player is now to the flag
    finish_lap: int               # the player's lap number at whose END the chequered flag falls (0 = unknown)
    laps_incl_current: int        # line crossings left including the current lap (= finish_lap - current_lap + 1)
    is_final_lap: bool
    clock_expired: bool = False
    extra_laps: int = 0
    leader_pace_sec: float = 0.0
    note: str = ""
    borderline: bool = False      # timed race: clock runs out within ~8% of a lap of the line -> +/-1 lap uncertain
    borderline_long: bool = False # timed race: clock projected to run out just BEFORE the line -> could be one lap MORE
    leader_x: float = 0.0         # projected leader distance (laps) when the clock hits zero


def _dist(lap: int, lap_distance: float, length: float) -> float:
    frac = (lap_distance / length) if length > 100 else 0.0
    frac = max(0.0, min(0.9999, frac))
    return max(0.0, (lap - 1) + frac)


def compute_race_distance(
    snap,
    pace_sec: float,
    extra_laps: int = 1,
    leader_lap_at_expiry: int = 0,
    time_remaining_override: Optional[float] = None,
    leader_time_loss_sec: float = 0.0,
    player_time_loss_sec: float = 0.0,
    borderline_long_laps: float = 0.06,
) -> RaceDistance:
    """
    snap: TelemetrySnapshot (uses total_laps, current_lap, current_lap_distance, track_length_m,
          event_time_remaining_sec, participants, position).
    pace_sec: representative lap time of the leader (or the player if unknown).
    leader_lap_at_expiry: leader's current_lap when the clock ran out (0 if not expired / unknown).
    time_remaining_override: legacy - replaces the clock reading (kept for callers/tests).
    """
    length = float(getattr(snap, "track_length_m", 0.0) or 0.0)
    lap = int(getattr(snap, "current_lap", 0) or 0)
    if lap <= 0:
        lap = 1
    d_p = _dist(lap, float(getattr(snap, "current_lap_distance", 0.0) or 0.0), length)

    leader = None
    for p in getattr(snap, "participants", ()) or ():
        if getattr(p, "is_active", True) and getattr(p, "position", 0) == 1:
            leader = p
            break
    if leader is not None and getattr(snap, "position", 0) != 1:
        d_l = _dist(int(leader.lap or 1), float(leader.lap_distance or 0.0), length)
        if d_l + EPS < d_p:  # data glitch (e.g. leader just crossed, distance not yet reset)
            d_l = d_p
    else:
        d_l = d_p

    pace = max(30.0, float(pace_sec or 90.0))
    total_laps = int(getattr(snap, "total_laps", 0) or 0)

    # Leader already has the flag: we finish the next time we cross the line, whatever the clock says
    # (RaceRoom even starts a fresh countdown for the rest of the field at that point).
    if (leader is not None and getattr(snap, "position", 0) != 1
            and int(getattr(leader, "race_state", 2) or 2) == LEADER_FINISHED_STATE):
        return RaceDistance(
            mode="LAPS" if total_laps > 0 else "TIMED",
            laps_to_go=max(0.0, lap - d_p),
            finish_lap=lap,
            laps_incl_current=1,
            is_final_lap=True,
            clock_expired=total_laps <= 0,
            extra_laps=int(extra_laps) if total_laps <= 0 else 0,
            leader_pace_sec=pace,
            note="leader has taken the chequered flag - we finish when we next cross the line",
        )

    t_rem = float(getattr(snap, "event_time_remaining_sec", -1.0))
    if time_remaining_override is not None and t_rem > 0:
        t_rem = max(1.0, float(time_remaining_override))

    borderline = False
    borderline_long = False
    leader_x = 0.0
    is_leader = (leader is None) or getattr(snap, "position", 0) == 1
    l_loss = max(0.0, float(player_time_loss_sec if is_leader else leader_time_loss_sec))
    p_loss = max(0.0, float(player_time_loss_sec))
    shift = 0.0  # laps the player gains (+) / loses (-) on the leader before the leader finishes
    if total_laps > 0:
        f_l = float(total_laps)
        mode = "LAPS"
        expired = False
        note = f"{total_laps}-lap race"
        shift = 0.0 if is_leader else (l_loss - p_loss) / pace
    elif t_rem > 0 or leader_lap_at_expiry > 0:
        mode = "TIMED"
        extra = max(0, int(extra_laps))
        if leader_lap_at_expiry > 0 and t_rem <= 0:
            expired = True
            f_l = float(leader_lap_at_expiry + extra)
            note = f"clock expired during leader's lap {leader_lap_at_expiry}; +{extra} lap(s) after that"
        else:
            expired = False
            x_l = d_l + max(0.0, t_rem - l_loss) / pace
            x_p = d_p + max(0.0, t_rem - p_loss) / pace
            if not is_leader and x_p > x_l:
                # our stop is projected to be so much shorter than the leader's that we'd be leading on the road
                is_leader, d_l, x_l = True, d_p, x_p
            x = x_l
            leader_x = x
            borderline = abs(x - round(x)) < 0.08
            boundary = math.ceil(x - EPS)
            borderline_long = (boundary - x) < borderline_long_laps
            f_l = float(boundary + extra)
            note = f"clock {t_rem:.0f}s at ~{pace:.1f}s/lap, then finish that lap + {extra} more"
            if l_loss > 0 and not is_leader:
                note += f"; leader's pending stop (~{l_loss:.0f}s) included"
            shift = 0.0 if is_leader else (l_loss - p_loss) / pace
    else:
        return RaceDistance("NONE", 0.0, 0, 0, False)

    if is_leader:
        f_p = math.ceil(f_l - EPS)
    else:
        # where we are when the leader takes the flag -> we finish at the next line crossing
        y = min(f_l, d_p + (f_l - d_l) + shift)
        f_p = math.ceil(y - EPS)
    f_p = max(f_p, lap)  # at minimum the lap in progress has to be finished
    laps_to_go = max(0.0, f_p - d_p)
    finish_lap = int(f_p)
    laps_incl = max(0, finish_lap - lap + 1)
    return RaceDistance(
        mode=mode,
        laps_to_go=laps_to_go,
        finish_lap=finish_lap,
        laps_incl_current=laps_incl,
        is_final_lap=(laps_incl == 1),
        clock_expired=(mode == "TIMED" and expired),
        extra_laps=(extra_laps if mode == "TIMED" else 0),
        leader_pace_sec=pace,
        note=note,
        borderline=borderline,
        borderline_long=borderline_long,
        leader_x=leader_x,
    )
