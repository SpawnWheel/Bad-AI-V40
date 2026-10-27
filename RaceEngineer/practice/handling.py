"""
Handling & driving-style analysis from AMS2 telemetry (fed at 10 Hz in practice, 5 Hz otherwise).

Everything here is measured, not guessed, so the engineer can reason like a data engineer:

Balance (per corner phase and speed band)
  * Understeer index  UI = 1 - R / R0, where R = path curvature per unit of steering (yaw rate / speed /
    |steering|) while cornering, and R0 = the same ratio measured in gentle, low-g driving (the car's
    linear, "neutral" response). UI > 0: the car needs more lock than neutral for the same path
    (understeer). UI < 0: less lock than neutral (oversteer). Every car rises towards the limit, so the
    useful signals are the DIFFERENCES between phases (entry / mid / exit), speed bands, corners and runs.
  * Slip balance  SB = (front slip - rear slip) / (front + rear) using tyre slip speed. > 0 fronts sliding
    more (understeer), < 0 rears sliding more (oversteer). On exit the driven axle's wheelspin adds to it.
  * Counter-steer events: steering opposite to the rotation = the rear stepped out (oversteer moments).
Grip events: lock-ups per wheel (brakes), wheelspin on the driven axle (traction), with corner locations.
Platform: min ride height per axle (cm) and max suspension travel, possible floor contact.
Temperatures: lap-averaged inner/middle/outer tread temps at speed, carcass, hot pressures, brake peaks.
Driving style: trail-braking share, braking pressure, coasting, full / partial throttle share, time from
apex to full throttle, steering corrections.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Optional, Tuple

G = 9.81
CORNERS = ("FL", "FR", "RL", "RR")


def _med(v):
    return median(v) if v else None


def _speed_band(kmh: float) -> str:
    if kmh < 100:
        return "slow"
    if kmh < 150:
        return "medium"
    return "fast"


@dataclass
class LapHandling:
    lap: int
    lap_time: float = 0.0
    valid: bool = True
    ui: Dict[str, float] = field(default_factory=dict)        # "entry|slow" -> median UI
    sb: Dict[str, float] = field(default_factory=dict)        # "entry" -> median slip balance
    ui_by_corner: Dict[int, Dict[str, float]] = field(default_factory=dict)
    countersteer: List[str] = field(default_factory=list)
    lockups: Dict[str, int] = field(default_factory=dict)
    lockup_where: List[str] = field(default_factory=list)
    wheelspin: int = 0
    wheelspin_where: List[str] = field(default_factory=list)
    tyre_imo: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)
    carcass: Dict[str, float] = field(default_factory=dict)
    pressures_bar: Dict[str, float] = field(default_factory=dict)
    brake_peak: Dict[str, float] = field(default_factory=dict)
    ride_min_cm: Dict[str, float] = field(default_factory=dict)
    ride_min_where: Dict[str, str] = field(default_factory=dict)
    travel_max_mm: Dict[str, float] = field(default_factory=dict)
    style: Dict[str, float] = field(default_factory=dict)
    corner_min_speed: Dict[int, float] = field(default_factory=dict)
    corner_time: Dict[int, float] = field(default_factory=dict)
    top_speed: float = 0.0
    limiter_sec: float = 0.0


class HandlingAnalyzer:
    def __init__(self, track_model=None):
        self._track_model = track_model
        self.drivetrain: str = "unknown"
        self._dt_front = 0.0
        self._dt_rear = 0.0
        self._dt_n = 0
        self._steer_yaw_sign_sum = 0.0
        self._r0_samples: List[float] = []
        self.laps: List[LapHandling] = []
        self._reset_lap(0)

    @property
    def track_model(self):
        if self._track_model is None:
            from race.track_model import track_model as tm
            self._track_model = tm
        return self._track_model

    # ------------------------------------------------------------------ lifecycle
    def reset(self):
        self.laps = []
        self._r0_samples = []
        self._reset_lap(0)

    def _reset_lap(self, lap: int):
        self._lap = lap
        self._valid = True
        self._r = defaultdict(list)          # key -> list of R
        self._sb = defaultdict(list)
        self._corner_r = defaultdict(lambda: defaultdict(list))
        self._cs_events: List[str] = []
        self._last_cs_t = -9.0
        self._lock = defaultdict(int)
        self._lock_where: List[str] = []
        self._last_lock_t = -9.0
        self._spin = 0
        self._spin_where: List[str] = []
        self._last_spin_t = -9.0
        self._temp = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
        self._carc = defaultdict(list)
        self._press = defaultdict(list)
        self._brake_peak = defaultdict(float)
        self._ride_min = {}
        self._ride_where = {}
        self._travel_max = defaultdict(float)
        self._n = 0
        self._n_brake = 0
        self._n_trail = 0
        self._brake_peaks: List[float] = []
        self._cur_brake_peak = 0.0
        self._n_coast = 0
        self._n_full = 0
        self._n_partial = 0
        self._apex_t: Optional[float] = None
        self._apex_to_full: List[float] = []
        self._steer_rev = 0
        self._last_steer_rate_sign = 0
        self._last_steer: Optional[float] = None
        self._last_t: Optional[float] = None
        self._corner_min = {}
        self._corner_entry_t = {}
        self._corner_time = {}
        self._top = 0.0
        self._limiter = 0.0
        self._prev_speed = 0.0

    # ------------------------------------------------------------------ helpers
    def _where(self, d: float) -> str:
        tm = self.track_model
        c = tm.current_corner(d) if tm else None
        if c:
            return f"C{c.cid}"
        return f"{d / 1000.0:.2f}km"

    def _phase(self, snap) -> Optional[str]:
        if snap.brake > 0.1:
            return "entry"
        if snap.throttle >= 0.4:
            return "exit"
        return "mid"

    # ------------------------------------------------------------------ feed
    def on_telemetry(self, snap):
        if snap is None or getattr(snap, "game_state", 2) != 2:
            return
        lap = int(snap.current_lap)
        if lap != self._lap:
            if self._lap > 0 and lap == self._lap + 1:
                self._finish_lap(float(snap.last_lap_time))
            self._reset_lap(lap)
        if snap.pit_mode.value > 0:
            self._valid = False
            return
        t = float(snap.current_lap_time)
        dt = 0.0 if self._last_t is None else max(0.0, min(0.5, t - self._last_t))
        self._last_t = t
        v = snap.speed_kmh / 3.6
        if v < 8:
            return
        self._n += 1
        acc = snap.local_accel_ms2 or (0.0, 0.0, 0.0)
        lat_g = abs(acc[0]) / G
        yaw = (snap.angular_velocity_rads or (0.0, 0.0, 0.0))[1]
        steer = float(snap.steering)
        d = float(snap.current_lap_distance)
        band = _speed_band(snap.speed_kmh)
        slip = snap.tyre_slip_speed_ms or (0, 0, 0, 0)
        rps = snap.tyre_rps or (0, 0, 0, 0)
        tm = self.track_model
        corner = tm.current_corner(d) if tm else None

        self._top = max(self._top, snap.speed_kmh)
        if snap.throttle > 0.9 and snap.rpm >= 0.985 * snap.max_rpm:
            self._limiter += dt

        # --- steer / yaw sign relation (learned; conventions differ between sims)
        if lat_g > 0.4 and abs(steer) > 0.08 and abs(yaw) > 0.05:
            self._steer_yaw_sign_sum += 1.0 if steer * yaw > 0 else -1.0
        rel = 1.0 if self._steer_yaw_sign_sum >= 0 else -1.0

        # --- balance: curvature per steering
        if abs(steer) > 0.03 and abs(yaw) > 0.02 and v > 10 and steer * yaw * rel > 0:
            r = (abs(yaw) / v) / abs(steer)
            if lat_g < 0.35 and snap.brake < 0.05 and 0.1 < snap.throttle < 0.9:
                self._r0_samples.append(r)
                if len(self._r0_samples) > 4000:
                    self._r0_samples = self._r0_samples[-3000:]
            elif lat_g >= 0.6:
                ph = self._phase(snap)
                self._r[f"{ph}|{band}"].append(r)
                self._r[ph].append(r)
                if corner:
                    self._corner_r[corner.cid][ph].append(r)
                fs = (abs(slip[0]) + abs(slip[1])) / 2.0
                rs = (abs(slip[2]) + abs(slip[3])) / 2.0
                if fs + rs > 0.3:
                    self._sb[ph].append((fs - rs) / (fs + rs))

        # --- counter-steer (rear stepping out)
        if abs(steer) > 0.04 and abs(yaw) > 0.15 and v > 11 and steer * yaw * rel < 0:
            if t - self._last_cs_t > 1.0:
                self._last_cs_t = t
                self._cs_events.append(f"{self._where(d)} ({self._phase(snap)}, {snap.speed_kmh:.0f} km/h)")

        # --- drivetrain detection (straight-line hard acceleration)
        if snap.throttle > 0.85 and snap.brake < 0.02 and abs(steer) < 0.05 and 30 < snap.speed_kmh < 160 and snap.gear in (1, 2, 3):
            self._dt_front += abs(slip[0]) + abs(slip[1])
            self._dt_rear += abs(slip[2]) + abs(slip[3])
            self._dt_n += 1
            if self._dt_n >= 30:
                f, rr = self._dt_front, self._dt_rear
                if f > 1.6 * rr:
                    self.drivetrain = "FWD"
                elif rr > 1.6 * f:
                    self.drivetrain = "RWD"
                else:
                    self.drivetrain = "AWD/unclear"

        # --- lock-ups
        if snap.brake > 0.3 and snap.speed_kmh > 40 and all(x != 0 for x in rps):
            m = median([abs(x) for x in rps])
            for i, x in enumerate(rps):
                if abs(x) < 0.7 * m and t - self._last_lock_t > 0.8:
                    self._last_lock_t = t
                    self._lock[CORNERS[i]] += 1
                    self._lock_where.append(f"{CORNERS[i]} at {self._where(d)}")

        # --- wheelspin (driven axle)
        if snap.throttle > 0.5 and snap.brake < 0.05 and 20 < snap.speed_kmh < 200:
            if self.drivetrain == "FWD":
                ws = max(abs(slip[0]), abs(slip[1]))
            elif self.drivetrain == "RWD":
                ws = max(abs(slip[2]), abs(slip[3]))
            else:
                ws = max(abs(x) for x in slip)
            if ws > 4.0 and t - self._last_spin_t > 1.2:
                self._last_spin_t = t
                self._spin += 1
                self._spin_where.append(self._where(d))

        # --- tyres / brakes / platform
        if snap.speed_kmh > 60:
            for i, c in enumerate(CORNERS):
                inn, mid, out = snap.tyre_temps_inner_c[i], snap.tyre_temps_center_c[i], snap.tyre_temps_outer_c[i]
                if inn > 10 and out > 10:
                    acc4 = self._temp[c]
                    acc4[0] += inn
                    acc4[1] += mid
                    acc4[2] += out
                    acc4[3] += 1
                if snap.tyre_carcass_temps_c[i] > 10:
                    self._carc[c].append(snap.tyre_carcass_temps_c[i])
                if snap.tyre_pressures_bar[i] > 0.3:
                    self._press[c].append(snap.tyre_pressures_bar[i])
        for i, c in enumerate(CORNERS):
            self._brake_peak[c] = max(self._brake_peak[c], snap.brake_temps_c[i])
            self._travel_max[c] = max(self._travel_max[c], snap.suspension_travel_mm[i])
        rh = snap.ride_height_cm or (0, 0, 0, 0)
        if any(x != 0 for x in rh):
            for axle, (a, b) in (("front", (rh[0], rh[1])), ("rear", (rh[2], rh[3]))):
                val = min(a, b)
                if axle not in self._ride_min or val < self._ride_min[axle]:
                    self._ride_min[axle] = val
                    self._ride_where[axle] = f"{self._where(d)} at {snap.speed_kmh:.0f} km/h"

        # --- driving style
        if snap.brake > 0.05:
            self._n_brake += 1
            self._cur_brake_peak = max(self._cur_brake_peak, snap.brake)
            if abs(steer) > 0.08:
                self._n_trail += 1
        elif self._cur_brake_peak > 0:
            if self._cur_brake_peak > 0.2:
                self._brake_peaks.append(self._cur_brake_peak)
            self._cur_brake_peak = 0.0
        if snap.brake < 0.05 and snap.throttle < 0.05 and snap.speed_kmh > 50:
            self._n_coast += 1
        if snap.throttle > 0.95:
            self._n_full += 1
        elif snap.throttle > 0.1 and snap.brake < 0.05:
            self._n_partial += 1
        # apex = local speed minimum while cornering
        if lat_g > 0.5 and snap.speed_kmh < self._prev_speed and snap.throttle < 0.5:
            self._apex_t = t
        if self._apex_t is not None and snap.throttle > 0.95:
            gap = t - self._apex_t
            if 0 < gap < 6:
                self._apex_to_full.append(gap)
            self._apex_t = None
        if self._last_steer is not None and lat_g > 0.3:
            ds = steer - self._last_steer
            sgn = 1 if ds > 0.01 else (-1 if ds < -0.01 else 0)
            if sgn and self._last_steer_rate_sign and sgn != self._last_steer_rate_sign:
                self._steer_rev += 1
            if sgn:
                self._last_steer_rate_sign = sgn
        self._last_steer = steer
        self._prev_speed = snap.speed_kmh

        # --- per-corner min speed & time
        if corner:
            cid = corner.cid
            self._corner_min[cid] = min(self._corner_min.get(cid, 999.0), snap.speed_kmh)
            if cid not in self._corner_entry_t:
                self._corner_entry_t[cid] = t
            else:
                self._corner_time[cid] = t - self._corner_entry_t[cid]

    # ------------------------------------------------------------------ lap / run reports
    def _r0(self) -> Optional[float]:
        return _med(self._r0_samples[-2000:]) if len(self._r0_samples) >= 30 else None

    def _finish_lap(self, lap_time: float):
        if self._n < 20:
            return
        r0 = self._r0()
        lh = LapHandling(lap=self._lap, lap_time=lap_time, valid=self._valid and lap_time > 0)
        if r0:
            for k, lst in self._r.items():
                if len(lst) >= 5:
                    lh.ui[k] = round(1.0 - median(lst) / r0, 3)
            for cid, phases in self._corner_r.items():
                lh.ui_by_corner[cid] = {ph: round(1.0 - median(v) / r0, 3) for ph, v in phases.items() if len(v) >= 3}
        for k, lst in self._sb.items():
            if len(lst) >= 5:
                lh.sb[k] = round(median(lst), 3)
        lh.countersteer = list(self._cs_events)
        lh.lockups = dict(self._lock)
        lh.lockup_where = list(self._lock_where)
        lh.wheelspin = self._spin
        lh.wheelspin_where = list(self._spin_where)
        for c, (a, b, cc, n) in self._temp.items():
            if n:
                lh.tyre_imo[c] = (a / n, b / n, cc / n)
        lh.carcass = {c: sum(v) / len(v) for c, v in self._carc.items() if v}
        lh.pressures_bar = {c: sum(v) / len(v) for c, v in self._press.items() if v}
        lh.brake_peak = dict(self._brake_peak)
        lh.ride_min_cm = dict(self._ride_min)
        lh.ride_min_where = dict(self._ride_where)
        lh.travel_max_mm = dict(self._travel_max)
        n = max(1, self._n)
        lh.style = {
            "trail_brake_pct": round(100.0 * self._n_trail / max(1, self._n_brake), 1),
            "brake_time_pct": round(100.0 * self._n_brake / n, 1),
            "avg_brake_peak_pct": round(100.0 * (sum(self._brake_peaks) / len(self._brake_peaks)), 1) if self._brake_peaks else 0.0,
            "coast_pct": round(100.0 * self._n_coast / n, 1),
            "full_throttle_pct": round(100.0 * self._n_full / n, 1),
            "partial_throttle_pct": round(100.0 * self._n_partial / n, 1),
            "apex_to_full_throttle_s": round(median(self._apex_to_full), 2) if self._apex_to_full else 0.0,
            "steering_corrections_per_min": round(60.0 * self._steer_rev / max(1.0, lap_time), 1) if lap_time > 0 else 0.0,
        }
        lh.corner_min_speed = {k: round(v, 1) for k, v in self._corner_min.items()}
        lh.corner_time = {k: round(v, 2) for k, v in self._corner_time.items()}
        lh.top_speed = self._top
        lh.limiter_sec = round(self._limiter, 1)
        self.laps.append(lh)
        if len(self.laps) > 60:
            self.laps = self.laps[-60:]

    def laps_since(self, lap_from: int) -> List[LapHandling]:
        return [l for l in self.laps if l.lap >= lap_from]


def aggregate(laps: List[LapHandling]) -> Dict:
    """Run-level medians over valid laps (the out-lap should be excluded by the caller)."""
    laps = [l for l in laps if l.valid] or laps
    if not laps:
        return {}
    out: Dict = {"laps": len(laps)}
    keys = set(k for l in laps for k in l.ui)
    out["ui"] = {k: round(median([l.ui[k] for l in laps if k in l.ui]), 3) for k in keys}
    keys = set(k for l in laps for k in l.sb)
    out["sb"] = {k: round(median([l.sb[k] for l in laps if k in l.sb]), 3) for k in keys}
    out["countersteer_per_lap"] = round(sum(len(l.countersteer) for l in laps) / len(laps), 2)
    cs_loc = defaultdict(int)
    for l in laps:
        for e in l.countersteer:
            cs_loc[e.split(" ")[0]] += 1
    out["countersteer_where"] = sorted(cs_loc.items(), key=lambda x: -x[1])[:4]
    locks = defaultdict(int)
    for l in laps:
        for k, v in l.lockups.items():
            locks[k] += v
    out["lockups_per_lap"] = {k: round(v / len(laps), 2) for k, v in locks.items()}
    lw = defaultdict(int)
    for l in laps:
        for e in l.lockup_where:
            lw[e] += 1
    out["lockup_where"] = sorted(lw.items(), key=lambda x: -x[1])[:4]
    out["wheelspin_per_lap"] = round(sum(l.wheelspin for l in laps) / len(laps), 2)
    sw = defaultdict(int)
    for l in laps:
        for e in l.wheelspin_where:
            sw[e] += 1
    out["wheelspin_where"] = sorted(sw.items(), key=lambda x: -x[1])[:4]
    temps = {}
    for c in CORNERS:
        vals = [l.tyre_imo[c] for l in laps if c in l.tyre_imo]
        if vals:
            temps[c] = tuple(round(median([v[i] for v in vals]), 1) for i in range(3))
    out["tyre_imo"] = temps
    out["carcass"] = {c: round(median([l.carcass[c] for l in laps if c in l.carcass]), 1) for c in CORNERS if any(c in l.carcass for l in laps)}
    out["pressures_bar"] = {c: round(median([l.pressures_bar[c] for l in laps if c in l.pressures_bar]), 3) for c in CORNERS if any(c in l.pressures_bar for l in laps)}
    out["brake_peak"] = {c: round(median([l.brake_peak[c] for l in laps if c in l.brake_peak]), 0) for c in CORNERS if any(c in l.brake_peak for l in laps)}
    rm = {}
    for axle in ("front", "rear"):
        vals = [(l.ride_min_cm[axle], l.ride_min_where.get(axle, "")) for l in laps if axle in l.ride_min_cm]
        if vals:
            v, w = min(vals, key=lambda x: x[0])
            rm[axle] = (round(v, 2), w)
    out["ride_min_cm"] = rm
    out["travel_max_mm"] = {c: round(max(l.travel_max_mm.get(c, 0) for l in laps), 1) for c in CORNERS}
    skeys = set(k for l in laps for k in l.style)
    out["style"] = {k: round(median([l.style[k] for l in laps if k in l.style]), 2) for k in skeys}
    cids = set(k for l in laps for k in l.corner_min_speed)
    out["corner_min_speed"] = {c: round(median([l.corner_min_speed[c] for l in laps if c in l.corner_min_speed]), 1) for c in cids}
    cids = set(k for l in laps for k in l.corner_time)
    out["corner_time"] = {c: round(median([l.corner_time[c] for l in laps if c in l.corner_time]), 2) for c in cids}
    cui = defaultdict(lambda: defaultdict(list))
    for l in laps:
        for cid, ph in l.ui_by_corner.items():
            for p, v in ph.items():
                cui[cid][p].append(v)
    out["ui_by_corner"] = {cid: {p: round(median(v), 3) for p, v in ph.items()} for cid, ph in cui.items()}
    out["top_speed"] = round(max(l.top_speed for l in laps), 1)
    out["limiter_sec_per_lap"] = round(sum(l.limiter_sec for l in laps) / len(laps), 1)
    return out


def _ui_word(v: float) -> str:
    return "understeer" if v > 0 else "oversteer"


def format_run(agg: Dict, drivetrain: str = "unknown") -> List[str]:
    """Human/LLM-readable lines for one run's aggregate."""
    if not agg:
        return ["  (not enough clean running yet)"]
    L = []
    ui = agg.get("ui", {})
    if ui:
        phases = [f"{p} {ui[p]:+.2f}" for p in ("entry", "mid", "exit") if p in ui]
        bands = []
        for b in ("slow", "medium", "fast"):
            seg = [f"{ {'entry': 'E', 'mid': 'M', 'exit': 'X'}[p] }{ui[f'{p}|{b}']:+.2f}" for p in ("entry", "mid", "exit") if f"{p}|{b}" in ui]
            if seg:
                bands.append(f"{b}: {' '.join(seg)}")
        L.append(f"  Understeer index by phase (+ = understeer, - = oversteer, 0 = linear/neutral): {', '.join(phases)}")
        if bands:
            L.append(f"    by speed (E/M/X = entry/mid/exit): {' | '.join(bands)}")
    else:
        L.append("  Understeer index: not enough cornering data yet (need a few clean laps).")
    sb = agg.get("sb", {})
    if sb:
        L.append("  Slip balance (+ fronts sliding more, - rears): " + ", ".join(f"{p} {sb[p]:+.2f}" for p in ("entry", "mid", "exit") if p in sb)
                 + (f"  [{drivetrain}: exit value includes driven-wheel wheelspin]" if drivetrain in ("FWD", "RWD") else ""))
    L.append(f"  Rear stepping out (counter-steer moments): {agg.get('countersteer_per_lap', 0):.1f}/lap"
             + (f" - mostly {', '.join(f'{w} x{n}' for w, n in agg.get('countersteer_where', []))}" if agg.get("countersteer_where") else ""))
    lk = agg.get("lockups_per_lap", {})
    L.append("  Lock-ups per lap: " + (", ".join(f"{c} {v:.1f}" for c, v in lk.items()) if lk else "none")
             + (f" (where: {', '.join(f'{w} x{n}' for w, n in agg.get('lockup_where', []))})" if agg.get("lockup_where") else ""))
    L.append(f"  Wheelspin on the driven axle ({drivetrain}): {agg.get('wheelspin_per_lap', 0):.1f}/lap"
             + (f" (where: {', '.join(f'{w} x{n}' for w, n in agg.get('wheelspin_where', []))})" if agg.get("wheelspin_where") else ""))
    t = agg.get("tyre_imo", {})
    if t:
        segs = []
        for c in CORNERS:
            if c in t:
                i, m, o = t[c]
                segs.append(f"{c} {i:.0f}/{m:.0f}/{o:.0f} (I-O {i - o:+.1f}, mid vs avg {m - (i + o) / 2:+.1f})")
        L.append("  Tyre tread temps at speed, lap-averaged, Inner/Mid/Outer °C: " + " | ".join(segs))
    if agg.get("carcass"):
        L.append("  Carcass temps: " + " | ".join(f"{c} {v:.0f}°C" for c, v in agg["carcass"].items()))
    if agg.get("pressures_bar"):
        L.append("  HOT pressures (on track): " + " | ".join(f"{c} {v:.2f} bar ({v * 14.5038:.1f} psi)" for c, v in agg["pressures_bar"].items()))
    if agg.get("brake_peak"):
        L.append("  Brake peak temps per lap: " + " | ".join(f"{c} {v:.0f}°C" for c, v in agg["brake_peak"].items()))
    rm = agg.get("ride_min_cm", {})
    if rm:
        L.append("  Minimum ride height: " + " | ".join(f"{a} {v:.2f} cm ({w})" for a, (v, w) in rm.items())
                 + ("  <- floor very close to the ground, possible bottoming" if any(v <= 0.5 for v, _ in rm.values()) else ""))
    if agg.get("travel_max_mm"):
        L.append("  Max suspension travel used (mm): " + " | ".join(f"{c} {v:.0f}" for c, v in agg["travel_max_mm"].items()))
    st = agg.get("style", {})
    if st:
        L.append(
            "  Driving style: trail-braking {tb:.0f}% of braking time, avg peak brake {bp:.0f}%, braking {bt:.0f}% of lap, "
            "coasting {co:.1f}%, full throttle {ft:.0f}%, part throttle {pt:.0f}%, apex->full throttle {af:.2f}s, "
            "steering corrections {sc:.0f}/min".format(
                tb=st.get("trail_brake_pct", 0), bp=st.get("avg_brake_peak_pct", 0), bt=st.get("brake_time_pct", 0),
                co=st.get("coast_pct", 0), ft=st.get("full_throttle_pct", 0), pt=st.get("partial_throttle_pct", 0),
                af=st.get("apex_to_full_throttle_s", 0), sc=st.get("steering_corrections_per_min", 0)))
    L.append(f"  Top speed {agg.get('top_speed', 0):.0f} km/h | on the rev limiter {agg.get('limiter_sec_per_lap', 0):.1f}s per lap")
    return L


def format_corner_table(agg: Dict, prev: Optional[Dict] = None, limit: int = 12) -> List[str]:
    cms = agg.get("corner_min_speed", {})
    if not cms:
        return []
    out = ["  Per corner (our C-numbering): min speed | time through | understeer index E/M/X" + (" | vs previous run" if prev else "")]
    cui = agg.get("ui_by_corner", {})
    ct = agg.get("corner_time", {})
    for cid in sorted(cms)[:limit]:
        ui = cui.get(cid, {})
        uis = "/".join(f"{ui[p]:+.2f}" if p in ui else "--" for p in ("entry", "mid", "exit"))
        line = f"    C{cid}: {cms[cid]:.0f} km/h | {ct.get(cid, 0):.2f}s | {uis}"
        if prev:
            pm = prev.get("corner_min_speed", {}).get(cid)
            pt = prev.get("corner_time", {}).get(cid)
            bits = []
            if pm is not None:
                bits.append(f"{cms[cid] - pm:+.1f} km/h")
            if pt is not None and cid in ct:
                bits.append(f"{ct[cid] - pt:+.2f}s")
            if bits:
                line += " | " + ", ".join(bits)
        out.append(line)
    return out
