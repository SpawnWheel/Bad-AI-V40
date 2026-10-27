from typing import Optional, List
from telemetry.collector import TelemetrySnapshot
from telemetry.shared_memory import SessionState, GameState, RaceState
from telemetry.sim_detect import SIM_DISPLAY_NAMES, SIM_R3E
from race.fuel import FuelStatus
from race.tires import TireStatus
from race.gaps import GapAnalysis
from race.standings import timing_board
from race.strategy import RaceStrategy, strategy_engine
from race.pit_stop_tracker import pit_stop_tracker


def _tyre_window_line(snapshot) -> str:
    """Tyre / brake operating windows when the sim publishes them (RaceRoom does, per car)."""
    tw = getattr(snapshot, "tyre_temp_window_c", ()) or ()
    if len(tw) < 4 or not any(w[1] > 0 for w in tw):
        return ""
    front, rear = tw[0], tw[2]
    line = (f"  SIM TYRE WINDOW (from {SIM_DISPLAY_NAMES.get(getattr(snapshot, 'sim', ''), 'the sim')}): "
            f"front cold <{front[0]:.0f} / optimal {front[1]:.0f} / hot >{front[2]:.0f}°C")
    if abs(rear[1] - front[1]) > 0.5:
        line += f" | rear cold <{rear[0]:.0f} / optimal {rear[1]:.0f} / hot >{rear[2]:.0f}°C"
    bw = getattr(snapshot, "brake_temp_window_c", ()) or ()
    if len(bw) >= 4 and bw[0][1] > 0:
        line += f" | brakes optimal ~{bw[0][1]:.0f}°C (hot >{bw[0][2]:.0f}°C)"
    return line + " - judge tread temps against THIS window, not generic numbers"


def format_lap_history(
    lap_times: Optional[List[float]],
    last_lap: float,
    best_lap: float,
    header_title: str = "[YOUR TIMING & LAP HISTORY]",
    pit_laps: Optional[set] = None,
) -> str:
    """Formats recent lap times with lap-to-lap deltas and pace trend summary."""
    def fmt_t(sec: float) -> str:
        if sec <= 0:
            return "--:--"
        m = int(sec // 60)
        s = sec % 60
        return f"{m}:{s:06.3f}" if m > 0 else f"{s:.3f}s"

    history_laps = list(lap_times) if lap_times else []
    if last_lap > 10.0 and (not history_laps or abs(history_laps[-1] - last_lap) > 0.05):
        history_laps.append(last_lap)

    lines = [f"{header_title} Last Lap: {fmt_t(last_lap)} | Best Lap: {fmt_t(best_lap)}"]

    if not history_laps:
        lines.append("  Lap History: No full laps timed yet.")
        return "\n".join(lines)

    recent_laps = history_laps[-6:]
    start_lap_idx = len(history_laps) - len(recent_laps) + 1

    seq_parts = []
    prev_time = None
    best_time_in_hist = min(history_laps)

    pit_laps = pit_laps or set()
    for i, t in enumerate(recent_laps):
        lap_num = start_lap_idx + i
        is_pb = abs(t - best_time_in_hist) < 0.01 and t > 10.0
        pb_tag = " (PB)" if is_pb else ""
        if lap_num in pit_laps:
            pb_tag += " [PIT LAP]"

        if prev_time is not None:
            delta = t - prev_time
            sign = "+" if delta > 0 else "-"
            delta_str = f" ({sign}{abs(delta):.2f}s{pb_tag})"
        else:
            delta_str = f" (baseline{pb_tag})"

        seq_parts.append(f"Lap {lap_num}: {fmt_t(t)}{delta_str}")
        prev_time = t

    lines.append(f"  Recent Laps: {' | '.join(seq_parts)}")

    # Trend from clean laps only (pit laps, lap 1 and spins/incident laps >7% off the best are not pace)
    clean_hist = [t for n, t in enumerate(history_laps, start=1)
                  if n not in pit_laps and n > 1 and t <= best_time_in_hist * 1.07]
    if len(clean_hist) >= 2:
        history_laps = clean_hist
    if len(history_laps) >= 2:
        recent_3 = history_laps[-3:] if len(history_laps) >= 3 else history_laps[-2:]
        avg_recent = sum(recent_3) / len(recent_3)
        spread = max(recent_3) - min(recent_3)
        if spread <= 0.35:
            trend_desc = f"Very consistent pace (within {spread:.2f}s over last {len(recent_3)} laps, avg {fmt_t(avg_recent)})"
        elif recent_3[-1] < recent_3[0]:
            trend_desc = f"Improving pace (gaining ~{recent_3[0] - recent_3[-1]:.2f}s over last {len(recent_3)} laps)"
        else:
            trend_desc = f"Pace dropping (+{recent_3[-1] - recent_3[0]:.2f}s over last {len(recent_3)} laps, avg {fmt_t(avg_recent)})"
        lines.append(f"  Pace Trend: {trend_desc}")

    return "\n".join(lines)


def _race_lap_stats(lap_times: Optional[List[float]], pit_laps: Optional[set]) -> str:
    laps = list(lap_times or [])
    if not laps:
        return ""
    pit_laps = pit_laps or set()
    best = min(laps)
    clean = [t for n, t in enumerate(laps, start=1) if n > 1 and n not in pit_laps and t <= best * 1.07]
    if not clean:
        return ""
    s = sorted(clean)
    med = s[len(s) // 2]
    mean = sum(clean) / len(clean)
    sd = (sum((x - mean) ** 2 for x in clean) / len(clean)) ** 0.5

    def f(x):
        return f"{int(x // 60)}:{x % 60:06.3f}"
    return (f"[RACE LAP STATS] {len(laps)} laps | best {f(best)} | clean-lap median {f(med)} | "
            f"consistency (std dev) {sd:.2f}s over {len(clean)} clean laps | pit laps: {sorted(pit_laps) or 'none'}")


def build_telemetry_context(
    snapshot: Optional[TelemetrySnapshot],
    fuel: FuelStatus,
    tires: TireStatus,
    gaps: GapAnalysis,
    timing_board_table: Optional[str] = None,
    strategy: Optional[RaceStrategy] = None,
    incidents_summary: Optional[str] = None,
    lap_times: Optional[List[float]] = None,
    practice_debrief: Optional[str] = None,
    quali_summary: Optional[str] = None,
    race_storylines_summary: Optional[str] = None,
    extras: Optional[dict] = None,
) -> str:
    """
    extras (all optional, supplied by RaceStateManager.get_context_extras()):
      track_position, race_distance (RaceDistance), fuel_ledger (str), pit_laps (set), last_stop (str),
      start_position (int), pressure_unit ("bar"|"psi"), corner_map (str), brake_bias_note (str)
    """
    extras = extras or {}
    if not snapshot or snapshot.game_state == GameState.GAME_EXITED:
        return "NO TELEMETRY AVAILABLE (no sim running - Automobilista 2 / RaceRoom - or no active session)."

    if snapshot.game_state == GameState.GAME_FRONT_END:
        return "SESSION MODE: FRONT-END / PADDOCK MENU (Standing by to load into track session)."

    if strategy is None:
        strategy = strategy_engine.compute_strategy(snapshot, fuel, tires)

    # Format lap time helper
    def fmt_time(sec: float) -> str:
        if sec <= 0:
            return "--:--"
        m = int(sec // 60)
        s = sec % 60
        return f"{m}:{s:06.3f}" if m > 0 else f"{s:.3f}s"

    # Identify session classification
    session = snapshot.session_state
    is_practice = session in (SessionState.SESSION_PRACTICE, SessionState.SESSION_TEST)
    is_qualifying = session in (SessionState.SESSION_QUALIFY, SessionState.SESSION_TIME_ATTACK)
    is_race = session in (SessionState.SESSION_RACE, SessionState.SESSION_FORMATION_LAP)

    # Competitor timing board
    if timing_board_table is None:
        _, timing_board_table = timing_board.analyze(snapshot)

    # Tyre details
    tire_temps_str = f"FL {tires.tread_temps_c[0]:.0f}°C | FR {tires.tread_temps_c[1]:.0f}°C | RL {tires.tread_temps_c[2]:.0f}°C | RR {tires.tread_temps_c[3]:.0f}°C"
    tire_pressures_str = " | ".join(
        f"{c} {tires.pressures_bar[i]:.2f} bar ({tires.pressures_bar[i] * 14.5038:.1f} psi)" for i, c in enumerate(("FL", "FR", "RL", "RR"))
    ) + " (HOT, live)"
    pit_laps = extras.get("pit_laps") or set()
    track_pos = extras.get("track_position")
    on_track = snapshot.pit_mode.value == 0 and snapshot.speed_kmh > 5
    tire_wear_str = (
        f"FL {tires.wear_pct[0]:.0f}% ({tires.corner_wear_rates[0]:.1f}%/lap) | "
        f"FR {tires.wear_pct[1]:.0f}% ({tires.corner_wear_rates[1]:.1f}%/lap) | "
        f"RL {tires.wear_pct[2]:.0f}% ({tires.corner_wear_rates[2]:.1f}%/lap) | "
        f"RR {tires.wear_pct[3]:.0f}% ({tires.corner_wear_rates[3]:.1f}%/lap)"
    )

    limiting_info = f"Limiting Tyre: {tires.limiting_tire} (~{tires.estimated_laps_remaining:.1f} laps life remaining)" if tires.limiting_tire else f"Est. Life: {tires.estimated_laps_remaining:.1f} laps"
    balance_note = f" [{tires.balance_note}]" if tires.balance_note else ""

    # Flag
    flag_str = snapshot.flag_colour.name.replace("FLAG_", "")

    # Weather condition description
    rain_pct = snapshot.rain_density * 100
    if rain_pct == 0:
        weather_desc = "Dry (0% rain)"
    elif rain_pct < 20:
        weather_desc = f"Light Drizzle ({rain_pct:.0f}% rain)"
    elif rain_pct < 60:
        weather_desc = f"Moderate Rain ({rain_pct:.0f}% rain) - Wetting"
    else:
        weather_desc = f"Heavy Rain ({rain_pct:.0f}% rain) - Wet Conditions"

    sim = getattr(snapshot, "sim", "AMS2")
    sim_name = SIM_DISPLAY_NAMES.get(sim, sim)
    if getattr(snapshot, "weather_available", True):
        weather_str = f"Weather: {weather_desc}, Ambient {snapshot.ambient_temp_c:.0f}°C, Track {snapshot.track_temp_c:.0f}°C"
    else:
        weather_str = f"Weather: not reported by {sim_name} (no air/track temps or rain data - ask the driver if it matters)"

    # Common Header
    header = (
        f"[SIM] {sim_name}\n"
        f"[SESSION] {snapshot.session_state.name.replace('SESSION_', '')} | "
        f"Track: {snapshot.track_name} ({snapshot.track_variation}) | "
        f"{weather_str}\n"
        f"[CAR] {snapshot.car_name} ({snapshot.car_class_name}) | Flag: {flag_str}"
    )
    tyre_window = _tyre_window_line(snapshot)

    clock_str = f" | Time Remaining: {int(snapshot.event_time_remaining_sec // 60)}m {int(snapshot.event_time_remaining_sec % 60):02d}s" if snapshot.event_time_remaining_sec > 0 else ""

    # 1. PRACTICE / TEST SESSION FOCUS (Deep Setup Diagnostics & Fuel Burn)
    if is_practice:
        # Build 3-point tyre temperatures string
        inn = snapshot.tyre_temps_inner_c
        cen = snapshot.tyre_temps_center_c
        out = snapshot.tyre_temps_outer_c

        three_point_lines = []
        corners = ["FL", "FR", "RL", "RR"]
        for i, c in enumerate(corners):
            spread = inn[i] - out[i] if len(inn) > i and len(out) > i else 0.0
            mid_diff = cen[i] - ((inn[i] + out[i]) / 2.0) if len(cen) > i else 0.0
            min_io = min(inn[i], out[i]) if len(inn) > i and len(out) > i else 0.0
            max_io = max(inn[i], out[i]) if len(inn) > i and len(out) > i else 0.0

            if i < 2:
                spread_eval = "camber optimal (target ~7°C)" if 5.0 <= spread <= 9.0 else ("too much neg camber (>9°C)" if spread > 9.0 else "insufficient camber (<5°C)")
            else:
                spread_eval = "camber optimal (rear target ~4°C)" if 2.0 <= spread <= 6.0 else ("too much neg camber (>6°C)" if spread > 6.0 else "insufficient camber (<2°C)")
            if cen[i] > max_io or mid_diff > 2.0:
                press_eval = "over-inflated / bulging out"
            elif cen[i] < min_io or mid_diff < -2.0:
                press_eval = "under-inflated / bulging in"
            else:
                press_eval = "pressure optimal (no bulging)"

            three_point_lines.append(
                f"  {c}: Inner {inn[i]:.0f}°C | Mid {cen[i]:.0f}°C | Outer {out[i]:.0f}°C (Spread: {spread:+.1f}°C [{spread_eval}], Mid: {mid_diff:+.1f}°C [{press_eval}])"
            )

        bias_str = f"{snapshot.brake_bias_pct:.1f}% front" if snapshot.brake_bias_pct > 0 else "not reported"
        abs_str = f"Level {snapshot.abs_setting}" if snapshot.abs_setting >= 0 else "not fitted / not reported"
        tc_str = f"Level {snapshot.tc_setting}" if snapshot.tc_setting >= 0 else "not fitted / not reported"
        in_car_controls = f"[IN-CAR CONTROLS] Brake Bias: {bias_str} | ABS: {abs_str} | TC: {tc_str}" + (f" ({extras['brake_bias_note']})" if extras.get("brake_bias_note") else "")
        tire_reading_note = " (last on-track reading retained in pits)" if snapshot.pit_mode.value > 0 else ""

        where = f"ON TRACK - {track_pos}" if (on_track and track_pos) else ("IN THE PIT BOX / PIT LANE (car stopped - setup discussion possible)" if snapshot.pit_mode.value > 0 else "")
        lines = [
            header,
            f"[SESSION MODE]: PRACTICE / SETUP & DATA GATHERING{clock_str}",
            f"[WHERE THE CAR IS]: {where}" if where else "",
            format_lap_history(lap_times, snapshot.last_lap_time, snapshot.best_lap_time, pit_laps=pit_laps),
            f"  Sectors: S1 {fmt_time(snapshot.sector1_time)} (best: {fmt_time(snapshot.best_sector1_time)}) | "
            f"S2 {fmt_time(snapshot.sector2_time)} (best: {fmt_time(snapshot.best_sector2_time)}) | "
            f"S3 {fmt_time(snapshot.sector3_time)} (best: {fmt_time(snapshot.best_sector3_time)})",
            f"[RACE SETUP FUEL BURN] Current Tank: {fuel.current_liters:.1f}L | "
            + (f"Average Burn Rate: {fuel.avg_per_lap:.2f}L/lap (measured for race calculations)" if fuel.laps_counted > 0 else "Average Burn Rate: calculating over stint..."),
            in_car_controls,
            f"[TIRE WINDOW & BALANCE - 3-POINT THERMAL PROFILE (Inner / Middle / Outer)]{tire_reading_note} {tires.compound}:",
            "\n".join(three_point_lines),
            tyre_window,
            f"  Pressures: {tire_pressures_str}",
            f"  Wear: {tire_wear_str}",
            (f"[BRAKES live] FL {snapshot.brake_temps_c[0]:.0f}°C | FR {snapshot.brake_temps_c[1]:.0f}°C | RL {snapshot.brake_temps_c[2]:.0f}°C | RR {snapshot.brake_temps_c[3]:.0f}°C "
             "(use the per-lap brake PEAKS in the run data below to judge ducts - live values drop on straights and in the pits)")
            if on_track else "[BRAKES] car in pits - live brake temps are meaningless now; use the per-lap brake peaks from the run data below",
        ]
        if extras.get("corner_map"):
            lines.append(f"[CORNER MAP (auto-detected, our numbering)]: {extras['corner_map']}")
        lines = [l for l in lines if l]

        if practice_debrief:
            lines.append(practice_debrief)

        lines.append(
            f"[ENGINE & AERO] Water: {snapshot.water_temp_c:.0f}°C | Oil: {snapshot.oil_temp_c:.0f}°C | Damage: Aero {snapshot.aero_damage:.1f}, Eng {snapshot.engine_damage:.1f}"
        )
        return "\n".join(lines)

    # 2. QUALIFYING / TIME ATTACK FOCUS (Pure Timings Only - No Fuel Strategy)
    elif is_qualifying:
        lines = [
            header,
            f"[SESSION MODE]: QUALIFYING / TIME ATTACK{clock_str}",
        ]
        if quali_summary:
            lines.append(quali_summary)
        else:
            lines.extend([
                f"  Position: P{snapshot.position}",
                format_lap_history(lap_times, snapshot.last_lap_time, snapshot.best_lap_time, header_title="[FLYING LAP TIMING & LAP HISTORY]"),
                f"  Sectors: S1 {fmt_time(snapshot.sector1_time)} (best: {fmt_time(snapshot.best_sector1_time)}) | "
                f"S2 {fmt_time(snapshot.sector2_time)} (best: {fmt_time(snapshot.best_sector2_time)}) | "
                f"S3 {fmt_time(snapshot.sector3_time)} (best: {fmt_time(snapshot.best_sector3_time)})",
            ])

        lines.extend([
            f"[TIRES FOR FLYING LAP] {tires.compound} | Tread: {tire_temps_str}"
            + (f"\n{tyre_window}" if tyre_window else " (optimal grip window ~80–100°C)"),
            f"[DAMAGE] Aero {snapshot.aero_damage:.1f} | Engine {snapshot.engine_damage:.1f}"
        ])
        return "\n".join(lines)

    # 3. RACE / FORMATION FOCUS (Default)
    else:
        # Check if race has finished
        if snapshot.is_race_finished or flag_str == "CHEQUERED":
            start_pos = extras.get("start_position") or 0
            gained = f" (started P{start_pos}, {start_pos - snapshot.position:+d} places)" if start_pos else ""
            lines = [
                header,
                f"[SESSION STATUS]: RACE FINISHED / CHEQUERED FLAG TAKEN (THE RACE IS OVER)",
                f"  RESULT: You finished in P{snapshot.position}!{gained} The race is over. 0 laps remaining.",
                f"  INSTRUCTION: Cool down on the in-lap and bring the car into the pit lane / parc fermé. Debrief questions are welcome - answer them with the facts below.",
                timing_board_table,
                format_lap_history(lap_times, snapshot.last_lap_time, snapshot.best_lap_time, pit_laps=pit_laps),
                _race_lap_stats(lap_times, pit_laps),
            ]
            if extras.get("fuel_ledger"):
                lines.append(extras["fuel_ledger"])
            if extras.get("last_stop"):
                lines.append(f"[PIT STOPS THIS RACE] {extras['last_stop']}")
            lines += [
                f"[TIRE LIFE & HEALTH] {tires.compound} | Condition: {tires.condition_summary}",
                f"[ENGINE HEALTH] Water: {snapshot.water_temp_c:.0f}°C | Oil: {snapshot.oil_temp_c:.0f}°C"
            ]
            return "\n".join(l for l in lines if l)

        # Check if on starting grid or formation lap
        if snapshot.session_state == SessionState.SESSION_FORMATION_LAP:
            race_progress = f"FORMATION LAP | Warming tires and brakes before grid lineup | Position: P{snapshot.position}"
        elif snapshot.race_state == RaceState.RACESTATE_NOT_STARTED:
            race_progress = f"STARTING GRID COUNTDOWN | Starting P{snapshot.position} | Waiting for lights out"
        elif snapshot.total_laps > 0:
            laps_left = max(0, snapshot.total_laps - snapshot.current_lap + 1)
            race_progress = f"Lap {snapshot.current_lap}/{snapshot.total_laps} ({laps_left} laps remaining to the flag)"
        elif extras.get("race_distance") is not None and extras["race_distance"].mode == "TIMED":
            rd = extras["race_distance"]
            clock = (f"Clock {int(snapshot.event_time_remaining_sec // 60)}m {int(snapshot.event_time_remaining_sec % 60):02d}s"
                     if snapshot.event_time_remaining_sec > 0 else "CLOCK EXPIRED")
            unsure = " (BORDERLINE: clock runs out close to the line - could be one lap more or less, say so if asked)" if rd.borderline else ""
            final = " | THIS IS THE FINAL LAP (white flag)" if rd.is_final_lap else ""
            if "leader's pending stop" in (rd.note or ""):
                unsure += " (includes the time the leader will lose at his pit stop - he hasn't stopped yet)"
            race_progress = (
                f"TIMED RACE | {clock} | Current Lap: {snapshot.current_lap} | "
                f"Laps to go INCLUDING this one: {rd.laps_incl_current} -> chequered flag at the end of Lap {rd.finish_lap}"
                f" ({'RaceRoom' if sim == SIM_R3E else 'AMS2'}: when the clock hits zero the leader finishes that lap + {rd.extra_laps} more){unsure}{final}"
            )
        else:
            # Timed race
            if snapshot.event_time_remaining_sec > 0:
                mins = int(snapshot.event_time_remaining_sec // 60)
                secs = int(snapshot.event_time_remaining_sec % 60)
                race_progress = f"TIMED RACE | Time Remaining: {mins}m {secs:02d}s (~{fuel.laps_remaining_race:.1f} laps to flag) | Current Lap: {snapshot.current_lap}"
            elif flag_str == "WHITE_FINAL_LAP":
                race_progress = f"TIMED RACE | FINAL LAP (White Flag)! This is your final lap to the chequered flag | Current Lap: {snapshot.current_lap}"
            else:
                race_progress = f"TIMED RACE | Time Expired! Finish this lap, then crossing the line starts the final lap | Current Lap: {snapshot.current_lap}"

        tire_life_str = f"FL {tires.life_pct[0]:.0f}% | FR {tires.life_pct[1]:.0f}% | RL {tires.life_pct[2]:.0f}% | RR {tires.life_pct[3]:.0f}%"

        loss_sec, is_learned, count = pit_stop_tracker.get_pit_loss_info(snapshot.track_name, snapshot.track_variation)
        if is_learned:
            pit_loss_desc = f"Estimated Pit Loss: {loss_sec:.1f}s (learned from {count} stop{'s' if count > 1 else ''} at this track)"
        else:
            pit_loss_desc = f"Estimated Pit Loss: {loss_sec:.1f}s (default baseline — no pit data recorded for this track yet)"

        lines = [
            header,
            f"[SESSION MODE]: RACE | {race_progress}",
        ]
        if track_pos and on_track:
            lines.append(f"[TRACK POSITION]: {track_pos}")
        elif snapshot.pit_mode.value > 0:
            lines.append("[TRACK POSITION]: IN THE PIT LANE")
        lines.append(timing_board_table)
        if incidents_summary and "Track clear" not in incidents_summary:
            lines.append(incidents_summary)
        if race_storylines_summary:
            lines.append(race_storylines_summary)

        lines.extend([
            format_lap_history(lap_times, snapshot.last_lap_time, snapshot.best_lap_time, pit_laps=pit_laps),
            "[FUEL STATUS — COMPUTED]" + (" (PRE-START READING: the sim can still show the previous session's fuel until the start - confirm the load with the driver)"
                                           if snapshot.race_state == RaceState.RACESTATE_NOT_STARTED else ""),
            f"  Current Fuel: {fuel.current_liters:.1f}L ({fuel.level_pct:.0f}%) of {fuel.capacity_liters:.0f}L tank | " + (f"Burn Rate: {fuel.avg_per_lap:.2f}L/lap ({fuel.burn_source} - race laps not measured yet)" if fuel.laps_counted == 0 else f"Burn Rate: {fuel.avg_per_lap:.2f}L/lap avg (measured over last {fuel.laps_counted} clean {'lap' if fuel.laps_counted == 1 else 'laps'})"),
            f"  Fuel Range: {fuel.laps_remaining_fuel:.1f} laps | Race Remaining: {fuel.laps_remaining_race:.1f} laps" + (f" ({fuel.distance_note})" if getattr(fuel, "distance_note", "") else ""),
            f"  Fuel Balance: {'DEFICIT: ' + str(round(fuel.fuel_deficit_laps, 1)) + ' laps short' if fuel.fuel_deficit_laps > 0 else 'SURPLUS: +' + str(round(fuel.fuel_surplus_laps, 1)) + ' laps'}",
            f"  Fuel Urgency: {'URGENT (Will run out before finish - pit stop required)' if fuel.is_urgent else 'NOT URGENT (Fuel-safe to the chequered flag - you will be fine)' if (fuel.laps_remaining_race > 0 and fuel.laps_remaining_fuel >= fuel.laps_remaining_race) else 'NOMINAL'}",
            (f"  Planned stop: box at the END of Lap {fuel.pit_window_lap} (window Lap {fuel.earliest_safe_pit_lap}-{fuel.latest_safe_pit_lap}) | "
             f"arrive with ~{fuel.fuel_at_planned_stop:.1f}L | ADD ~{fuel.recommended_refuel_liters:.1f}L (enough to the flag + {getattr(fuel, 'margin_liters', 1.0):.1f} L spare)"
             if fuel.needs_pit_stop and fuel.pit_window_lap else f"  Short to the flag by: {fuel.fuel_required_liters:.1f}L net"),
            f"  AUTHORITATIVE FUEL STATUS: {strategy.fuel_status_label}",
            f"[PIT STRATEGY — AUTHORITATIVE]",
            f"  Recommended Pit Window: {strategy.combined_pit_window}",
            (f"  Pit calls: heads-up at the start of Lap {fuel.pit_window_lap - 1}, 'box this lap' at the start of Lap {fuel.pit_window_lap}, reminder before pit entry"
             if fuel.needs_pit_stop and fuel.pit_window_lap else "  Pit calls: none planned"),
            f"  Latest Safe Pit Lap: Lap {strategy.latest_safe_fuel_lap} (before fuel empty risk)",
            f"  {strategy.rejoin_summary}",
            f"  Pit loss: {strategy.pit_loss_breakdown}" if strategy.pit_loss_breakdown else f"  {pit_loss_desc}",
            f"  Pit Urgency: {strategy.pit_urgency}",
            f"  Strategy Rationale: {strategy.rationale}",
            f"[TIRE LIFE & HEALTH] {tires.compound} | Temps: {tire_temps_str}{balance_note}",
            f"  Life: {tire_life_str}",
            f"  Condition: {tires.condition_summary}",
            f"  Laps Until 50% Cliff: ~{tires.estimated_laps_remaining:.1f} laps on {tires.limiting_tire}",
            f"[ENGINE HEALTH] Water: {snapshot.water_temp_c:.0f}°C | Oil: {snapshot.oil_temp_c:.0f}°C | Damage: Aero {snapshot.aero_damage:.1f}, Eng {snapshot.engine_damage:.1f}"
        ])
        if extras.get("fuel_ledger"):
            lines.append(extras["fuel_ledger"])
        if extras.get("last_stop"):
            lines.append(f"[OUR PIT STOPS THIS RACE] {extras['last_stop']}")
        return "\n".join(l for l in lines if l)
