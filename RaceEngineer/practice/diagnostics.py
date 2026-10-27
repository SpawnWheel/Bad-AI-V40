"""
High-frequency (10Hz) Practice Telemetry Diagnostics Engine.
Monitors bottoming out, redlining, wheelspin, 3-point tyre temperatures,
brake temperature windows, and in-car settings during practice runs.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from telemetry.collector import TelemetrySnapshot


@dataclass
class TyreThermalDiagnosis:
    corner: str                  # FL, FR, RL, RR
    inner_c: float
    center_c: float
    outer_c: float
    inner_outer_spread_c: float  # Inner - Outer
    camber_status: str           # "OPTIMAL" | "TOO_MUCH_CAMBER" | "INSUFFICIENT_CAMBER" | "COLD"
    pressure_status: str         # "OPTIMAL" | "OVER_INFLATED" | "UNDER_INFLATED" | "COLD"
    camber_advice: str
    pressure_advice: str
    is_averaged: bool = False


@dataclass
class PracticeDiagnosticsSummary:
    # Mechanical & Floor
    bottoming_count_total: int = 0
    bottoming_count_front: int = 0
    bottoming_count_rear: int = 0
    bottoming_locations: List[str] = field(default_factory=list)
    has_bottoming_issue: bool = False

    # Gearing & Powertrain
    redline_count: int = 0
    max_rpm_seen: float = 0.0
    top_speed_kmh: float = 0.0
    top_gear_seen: int = 0
    has_redline_issue: bool = False

    # Traction & Wheelspin
    wheelspin_count: int = 0
    wheelspin_rear_left: int = 0
    wheelspin_rear_right: int = 0
    has_wheelspin_issue: bool = False

    # Tyres (3-Point Temps)
    tyre_diagnoses: Dict[str, TyreThermalDiagnosis] = field(default_factory=dict)
    camber_summary: str = "Tyres within normal camber window."
    pressure_summary: str = "Tire pressures within normal inflation window."
    averaged_inner_temps_c: Tuple[float, ...] = ()
    averaged_center_temps_c: Tuple[float, ...] = ()
    averaged_outer_temps_c: Tuple[float, ...] = ()
    is_temp_averaged: bool = False

    # Brakes
    cold_brakes: List[str] = field(default_factory=list)
    overheated_brakes: List[str] = field(default_factory=list)
    brake_status_summary: str = "Brake temperatures in optimal operating window."

    # In-Car Settings
    brake_bias_pct: float = 0.0
    abs_setting: int = 0
    tc_setting: int = 0


class PracticeTelemetryMonitor:
    """
    Ingests 10Hz telemetry frames in Practice mode and tallies mechanical
    symptoms (bottoming, redline, wheelspin, tyre thermals).
    """

    def __init__(self):
        self.reset_run()

    def reset_run(self):
        """Resets tallies for a fresh stint/run."""
        self._bottoming_front = 0
        self._bottoming_rear = 0
        self._bottoming_events = []
        self._last_bottoming_time = 0.0

        self._redline_events = 0
        self._last_redline_time = 0.0
        self._max_rpm = 0.0
        self._top_speed_kmh = 0.0
        self._top_gear = 0

        self._wheelspin_fl = 0
        self._wheelspin_fr = 0
        self._wheelspin_rl = 0
        self._wheelspin_rr = 0
        self._last_wheelspin_time = 0.0

        self._last_snapshot: Optional[TelemetrySnapshot] = None
        self._clutch_slip_count = 0

        # Running tyre temperature accumulators (flying laps only, excluding outlap)
        self._stint_start_lap: int = -1
        self._outlap_completed: bool = False
        self._temp_samples_count: int = 0
        self._sum_inner: List[float] = [0.0, 0.0, 0.0, 0.0]
        self._sum_center: List[float] = [0.0, 0.0, 0.0, 0.0]
        self._sum_outer: List[float] = [0.0, 0.0, 0.0, 0.0]

    def on_telemetry(self, snap: TelemetrySnapshot):
        """Called on each telemetry snapshot (10Hz)."""
        # Skip analysis if in pits, front-end, or paused
        if snap.pit_mode.value > 0 or snap.speed_kmh < 15.0:
            if self._last_snapshot is None:
                self._last_snapshot = snap
            return

        now = snap.timestamp

        # Outlap detection: Stint starts on snap.current_lap. Once lap increments, outlap is complete.
        if self._stint_start_lap == -1 and snap.current_lap > 0:
            self._stint_start_lap = snap.current_lap

        if snap.current_lap > self._stint_start_lap and self._stint_start_lap > 0:
            self._outlap_completed = True

        # Accumulate tyre temps only on flying laps (outlap excluded) and at racing speeds (> 40 km/h)
        if (
            self._outlap_completed
            and snap.pit_mode.value == 0
            and snap.speed_kmh > 40.0
            and len(snap.tyre_temps_inner_c) >= 4
            and len(snap.tyre_temps_center_c) >= 4
            and len(snap.tyre_temps_outer_c) >= 4
        ):
            if all(t > 10.0 for t in snap.tyre_temps_inner_c) and all(t > 10.0 for t in snap.tyre_temps_outer_c):
                for i in range(4):
                    self._sum_inner[i] += snap.tyre_temps_inner_c[i]
                    self._sum_center[i] += snap.tyre_temps_center_c[i]
                    self._sum_outer[i] += snap.tyre_temps_outer_c[i]
                self._temp_samples_count += 1

        # 1. Bottoming Out Detection
        # Check suspension travel (mm) and suspension velocity (m/s)
        # Low travel (< 5mm) or floor ride height near 0 with bump spike
        st = snap.suspension_travel_mm
        sv = snap.suspension_velocity_ms
        rh = snap.ride_height_mm

        front_bottomed = False
        rear_bottomed = False

        if len(st) >= 4 and len(sv) >= 4:
            # Front wheels
            if (st[0] < 5.0 or st[1] < 5.0) and (abs(sv[0]) > 0.4 or abs(sv[1]) > 0.4):
                front_bottomed = True
            elif len(rh) >= 4 and (rh[0] < 2.0 or rh[1] < 2.0) and (abs(sv[0]) > 0.3 or abs(sv[1]) > 0.3):
                front_bottomed = True

            # Rear wheels
            if (st[2] < 5.0 or st[3] < 5.0) and (abs(sv[2]) > 0.4 or abs(sv[3]) > 0.4):
                rear_bottomed = True
            elif len(rh) >= 4 and (rh[2] < 2.0 or rh[3] < 2.0) and (abs(sv[2]) > 0.3 or abs(sv[3]) > 0.3):
                rear_bottomed = True

        if (front_bottomed or rear_bottomed) and (now - self._last_bottoming_time) > 0.8:
            self._last_bottoming_time = now
            loc_label = f"Sector {snap.current_sector}" if snap.current_sector > 0 else "on track"
            if front_bottomed:
                self._bottoming_front += 1
                self._bottoming_events.append(f"Front floor strike in {loc_label} (travel < 5mm)")
            if rear_bottomed:
                self._bottoming_rear += 1
                self._bottoming_events.append(f"Rear floor strike in {loc_label} (travel < 5mm)")

        # 2. Straightaway Redline Detection
        if snap.rpm > self._max_rpm:
            self._max_rpm = snap.rpm
        if snap.speed_kmh > self._top_speed_kmh:
            self._top_speed_kmh = snap.speed_kmh
        if snap.gear > self._top_gear:
            self._top_gear = snap.gear

        # Redlining in top gear at high speed under full throttle
        if (
            snap.throttle > 0.9
            and snap.speed_kmh > 150.0
            and snap.rpm >= (0.985 * snap.max_rpm)
            and (now - self._last_redline_time) > 2.0
        ):
            self._last_redline_time = now
            self._redline_events += 1

        # 3. Corner Exit Wheelspin Detection
        # Check driven tyre slip speed (> 4.5 m/s under acceleration)
        slip = snap.tyre_slip_speed_ms
        if len(slip) >= 4 and snap.throttle > 0.65 and snap.speed_kmh > 25.0:
            if slip[2] > 4.5 or slip[3] > 4.5:
                if (now - self._last_wheelspin_time) > 1.2:
                    self._last_wheelspin_time = now
                    if slip[2] > 4.5:
                        self._wheelspin_rl += 1
                    if slip[3] > 4.5:
                        self._wheelspin_rr += 1

        # 4. Clutch slipping
        if snap.clutch_slipping and snap.clutch < 0.1 and snap.throttle > 0.5:
            self._clutch_slip_count += 1

        self._last_snapshot = snap

    def get_summary(self, current_snap: Optional[TelemetrySnapshot] = None) -> PracticeDiagnosticsSummary:
        """Generates a complete diagnostic report for the current stint."""
        snap = current_snap or self._last_snapshot
        total_bottoming = self._bottoming_front + self._bottoming_rear
        has_bottoming = total_bottoming >= 2
        has_redline = self._redline_events >= 2
        total_wheelspin = self._wheelspin_rl + self._wheelspin_rr
        has_wheelspin = total_wheelspin >= 3

        tyre_diag = {}
        camber_notes = []
        pressure_notes = []

        # Determine whether to use flying lap averages (excluding outlap) or current snapshot
        is_averaged = self._temp_samples_count >= 10
        if is_averaged:
            inner_vals = [s / self._temp_samples_count for s in self._sum_inner]
            center_vals = [s / self._temp_samples_count for s in self._sum_center]
            outer_vals = [s / self._temp_samples_count for s in self._sum_outer]
        elif snap and len(snap.tyre_temps_inner_c) >= 4:
            inner_vals = list(snap.tyre_temps_inner_c)
            center_vals = list(snap.tyre_temps_center_c)
            outer_vals = list(snap.tyre_temps_outer_c)
        else:
            inner_vals = []
            center_vals = []
            outer_vals = []

        if len(inner_vals) >= 4:
            corners = ["FL", "FR", "RL", "RR"]
            for i, c_name in enumerate(corners):
                inn = inner_vals[i]
                cen = center_vals[i]
                out = outer_vals[i]

                spread = inn - out
                avg_io = (inn + out) / 2.0
                center_diff = cen - avg_io
                min_io = min(inn, out)
                max_io = max(inn, out)

                # Camber evaluation:
                # - Target: Inner is ~7°C hotter than outer.
                # - Less than 5.0°C: insufficient negative camber.
                # - 5.0°C to 9.0°C: optimal window.
                # - Greater than 9.0°C: excessive negative camber.
                # Fronts: ~7°C inner-outer (5-9 window). Rears run less: ~4°C (2-6 window).
                is_rear = i >= 2
                lo, hi, tgt = (2.0, 6.0, 4.0) if is_rear else (5.0, 9.0, 7.0)
                if inn < 45.0 or out < 45.0:
                    c_status = "COLD"
                    c_advice = "Tyres cold, complete more flying laps before tuning camber."
                elif spread > hi:
                    c_status = "TOO_MUCH_CAMBER"
                    c_advice = f"{c_name} inner shoulder overheating (+{spread:.1f}°C hotter vs outer, target {tgt:.0f}°C). Reduce negative camber."
                    camber_notes.append(f"{c_name} excessive camber (+{spread:.1f}°C spread, target {tgt:.0f}°C)")
                elif spread < lo:
                    c_status = "INSUFFICIENT_CAMBER"
                    c_advice = f"{c_name} outer shoulder overworked ({spread:.1f}°C spread vs outer, target {tgt:.0f}°C). Add negative camber."
                    camber_notes.append(f"{c_name} insufficient camber ({spread:.1f}°C spread, target {tgt:.0f}°C)")
                else:
                    c_status = "OPTIMAL"
                    c_advice = f"{c_name} camber window optimal ({spread:.1f}°C spread, target {tgt:.0f}°C)."

                # Pressure evaluation (Tyre bulging):
                # - Optimal: Contact patch is flat; middle temp sits between inner and outer temps.
                # - Bulging out (Over-inflated): Middle temp runs hotter than gradient / above both (cen > max_io or center_diff > 2.0°C).
                # - Bulging in (Under-inflated): Middle temp runs cooler than gradient / below both (cen < min_io or center_diff < -2.0°C).
                if cen < 45.0:
                    p_status = "COLD"
                    p_advice = "Cold pressures."
                elif cen > max_io or center_diff > 2.0:
                    p_status = "OVER_INFLATED"
                    p_advice = f"{c_name} tyre bulging out / over-inflated (middle {cen:.1f}°C, +{center_diff:.1f}°C above profile). Lower tyre pressure."
                    pressure_notes.append(f"{c_name} bulging out / over-inflated (+{center_diff:.1f}°C mid)")
                elif cen < min_io or center_diff < -2.0:
                    p_status = "UNDER_INFLATED"
                    p_advice = f"{c_name} tyre bulging in / under-inflated (middle {cen:.1f}°C, {center_diff:.1f}°C below profile). Raise tyre pressure."
                    pressure_notes.append(f"{c_name} bulging in / under-inflated ({center_diff:.1f}°C mid)")
                else:
                    p_status = "OPTIMAL"
                    p_advice = f"{c_name} tyre pressure optimal (middle temp between inner and outer, no bulging)."

                tyre_diag[c_name] = TyreThermalDiagnosis(
                    corner=c_name,
                    inner_c=inn,
                    center_c=cen,
                    outer_c=out,
                    inner_outer_spread_c=spread,
                    camber_status=c_status,
                    pressure_status=p_status,
                    camber_advice=c_advice,
                    pressure_advice=p_advice,
                    is_averaged=is_averaged
                )

        avg_tag = " (flying lap averages)" if is_averaged else ""
        camber_summary_str = ("; ".join(camber_notes) if camber_notes else "Camber alignment well-balanced across all corners (~7°C inner spread).") + avg_tag
        pressure_summary_str = ("; ".join(pressure_notes) if pressure_notes else "Tyre pressures uniform (no bulging in or out, middle sits between inner and outer).") + avg_tag

        # Brake diagnostics
        cold_b = []
        hot_b = []
        brake_summary = "Brake temperatures optimal (300–600°C)."
        if snap and len(snap.brake_temps_c) >= 4:
            corners = ["FL", "FR", "RL", "RR"]
            for i, c_name in enumerate(corners):
                bt = snap.brake_temps_c[i]
                if bt > 20.0 and bt < 200.0:
                    cold_b.append(f"{c_name} ({bt:.0f}°C)")
                elif bt > 750.0:
                    hot_b.append(f"{c_name} ({bt:.0f}°C)")

            if hot_b:
                brake_summary = f"Brakes running hot on {', '.join(hot_b)}. Consider opening brake ducts."
            elif cold_b:
                brake_summary = f"Brakes below optimal temp on {', '.join(cold_b)}. Consider closing brake ducts."

        return PracticeDiagnosticsSummary(
            bottoming_count_total=total_bottoming,
            bottoming_count_front=self._bottoming_front,
            bottoming_count_rear=self._bottoming_rear,
            bottoming_locations=list(set(self._bottoming_events)),
            has_bottoming_issue=has_bottoming,

            redline_count=self._redline_events,
            max_rpm_seen=self._max_rpm,
            top_speed_kmh=self._top_speed_kmh,
            top_gear_seen=self._top_gear,
            has_redline_issue=has_redline,

            wheelspin_count=total_wheelspin,
            wheelspin_rear_left=self._wheelspin_rl,
            wheelspin_rear_right=self._wheelspin_rr,
            has_wheelspin_issue=has_wheelspin,

            tyre_diagnoses=tyre_diag,
            camber_summary=camber_summary_str,
            pressure_summary=pressure_summary_str,
            averaged_inner_temps_c=tuple(inner_vals),
            averaged_center_temps_c=tuple(center_vals),
            averaged_outer_temps_c=tuple(outer_vals),
            is_temp_averaged=is_averaged,

            cold_brakes=cold_b,
            overheated_brakes=hot_b,
            brake_status_summary=brake_summary,

            brake_bias_pct=snap.brake_bias_pct if snap else 0.0,
            abs_setting=snap.abs_setting if snap else 0,
            tc_setting=snap.tc_setting if snap else 0
        )
