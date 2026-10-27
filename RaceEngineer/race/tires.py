"""
Tire and brake monitoring for AMS2.
Tracks temperatures, wear degradation, and vehicle balance.
"""

from dataclasses import dataclass
from typing import Tuple, List, Optional


@dataclass
class TireStatus:
    compound: str
    tread_temps_c: Tuple[float, float, float, float]  # FL, FR, RL, RR
    pressures_bar: Tuple[float, float, float, float]
    wear_pct: Tuple[float, float, float, float]
    life_pct: Tuple[float, float, float, float]       # 100 - wear_pct
    wear_rate_per_lap: float
    estimated_laps_remaining: float                    # Laps until very worn (50% life cliff)

    # Conditions & Diagnosis
    is_overheating: bool
    is_cold: bool
    overheated_tires: List[str]
    cold_tires: List[str]
    balance_note: Optional[str]                        # e.g., "Front-limited / understeer tendency"
    is_gone_off: bool                                  # True if any tire <= 75% life (lost peak grip)
    is_very_worn: bool                                 # True if any tire <= 50% life (critical cliff)
    is_critical_wear: bool                             # True if any tire <= 25% life (puncture risk)
    limiting_tire: Optional[str] = None                # e.g. "FL (Front-Left)"
    corner_wear_rates: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    condition_summary: str = "Fresh"


class TireTracker:
    # Operating temperature window: GT / Touring optimal 80°C - 105°C
    TEMP_COLD_THRESHOLD = 70.0
    TEMP_OVERHEAT_THRESHOLD = 105.0

    # User-defined tyre life thresholds:
    # 100% - 76%: Fresh
    # <= 75%: Tyres have gone "off" (peak grip lost)
    # <= 50%: Very worn (severe grip cliff, box now)
    # <= 25%: Critical puncture risk
    LIFE_GONE_OFF_THRESHOLD = 75.0
    LIFE_VERY_WORN_THRESHOLD = 50.0
    LIFE_PUNCTURE_RISK_THRESHOLD = 25.0

    TIRE_NAMES = ["FL", "FR", "RL", "RR"]

    def __init__(self):
        self._lap_start_wear: Optional[Tuple[float, ...]] = None
        self._wear_rates: List[float] = []
        self._corner_wear_rates: List[List[float]] = [[], [], [], []]
        self._last_lap = -1

    def reset(self):
        self._lap_start_wear = None
        self._wear_rates.clear()
        self._corner_wear_rates = [[], [], [], []]
        self._last_lap = -1

    def on_lap_started(self, lap_number: int, current_wear: Tuple[float, ...]):
        """Called at start of each lap to measure wear delta."""
        if self._lap_start_wear is not None and lap_number > self._last_lap:
            deltas = [max(0.0, current_wear[i] - self._lap_start_wear[i]) for i in range(4)]
            avg_delta = sum(deltas) / 4.0
            if 0.05 <= avg_delta <= 15.0:
                self._wear_rates.append(avg_delta)
                if len(self._wear_rates) > 10:
                    self._wear_rates.pop(0)

                for i in range(4):
                    self._corner_wear_rates[i].append(deltas[i])
                    if len(self._corner_wear_rates[i]) > 10:
                        self._corner_wear_rates[i].pop(0)

        self._lap_start_wear = current_wear
        self._last_lap = lap_number

    def get_wear_rate(self) -> float:
        """Returns average % wear per lap across all 4 tires."""
        if not self._wear_rates:
            return 2.0  # default estimate ~2.0% wear per lap
        recent = self._wear_rates[-3:]
        return sum(recent) / len(recent)

    def get_corner_wear_rates(self) -> Tuple[float, float, float, float]:
        """Returns per-corner average % wear per lap."""
        rates = []
        for i in range(4):
            hist = self._corner_wear_rates[i]
            if hist:
                recent = hist[-3:]
                rates.append(sum(recent) / len(recent))
            else:
                rates.append(2.0)
        return (rates[0], rates[1], rates[2], rates[3])

    def analyze(
        self,
        compound: str,
        tread_temps: Tuple[float, float, float, float],
        pressures: Tuple[float, float, float, float],
        wear: Tuple[float, float, float, float]
    ) -> TireStatus:
        overheated = []
        cold = []

        for i, name in enumerate(self.TIRE_NAMES):
            temp = tread_temps[i]
            if temp > self.TEMP_OVERHEAT_THRESHOLD:
                overheated.append(name)
            elif temp < self.TEMP_COLD_THRESHOLD and temp > 15.0:  # ignore 0 if uninitialized
                cold.append(name)

        # Balance diagnosis
        front_avg_temp = (tread_temps[0] + tread_temps[1]) / 2.0
        rear_avg_temp = (tread_temps[2] + tread_temps[3]) / 2.0
        left_avg_temp = (tread_temps[0] + tread_temps[2]) / 2.0
        right_avg_temp = (tread_temps[1] + tread_temps[3]) / 2.0

        balance_note = None
        if front_avg_temp - rear_avg_temp > 12.0 and front_avg_temp > 20.0:
            balance_note = "Front tires running hot (understeer / front-axle heavy)"
        elif rear_avg_temp - front_avg_temp > 12.0 and rear_avg_temp > 20.0:
            balance_note = "Rear tires running hot (traction spin / oversteer)"
        elif left_avg_temp - right_avg_temp > 12.0 and left_avg_temp > 20.0:
            balance_note = "Left side tires heavily loaded"
        elif right_avg_temp - left_avg_temp > 12.0 and right_avg_temp > 20.0:
            balance_note = "Right side tires heavily loaded"

        wear_rate = self.get_wear_rate()
        corner_rates = self.get_corner_wear_rates()

        # Compute tyre life % (100% = new, 0% = dead)
        life_pct = tuple(max(0.0, 100.0 - wear[i]) for i in range(4))

        # Identify limiting tire (tire with lowest life or highest wear rate)
        min_life_idx = 0
        min_life = life_pct[0]
        for i in range(1, 4):
            if life_pct[i] < min_life:
                min_life = life_pct[i]
                min_life_idx = i

        limiting_name = f"{self.TIRE_NAMES[min_life_idx]} ({'Front-Left' if min_life_idx==0 else 'Front-Right' if min_life_idx==1 else 'Rear-Left' if min_life_idx==2 else 'Rear-Right'})"

        # Calculate remaining laps until 50% life (the very worn cliff)
        limiting_rate = max(0.1, corner_rates[min_life_idx])
        laps_until_very_worn = max(0.0, (min_life - self.LIFE_VERY_WORN_THRESHOLD) / limiting_rate)

        is_gone_off = min_life <= self.LIFE_GONE_OFF_THRESHOLD
        is_very_worn = min_life <= self.LIFE_VERY_WORN_THRESHOLD
        is_critical_wear = min_life <= self.LIFE_PUNCTURE_RISK_THRESHOLD

        if is_critical_wear:
            condition_summary = f"CRITICAL PUNCTURE RISK! ({min_life:.0f}% life on {limiting_name})"
        elif is_very_worn:
            condition_summary = f"VERY WORN / ON THE CLIFF! ({min_life:.0f}% life on {limiting_name} - severe grip loss, box now)"
        elif is_gone_off:
            condition_summary = f"TYRES GONE OFF ({min_life:.0f}% life on {limiting_name} - past peak grip, car will slide)"
        else:
            condition_summary = f"Good ({min_life:.0f}% life on {limiting_name})"

        return TireStatus(
            compound=compound,
            tread_temps_c=tread_temps,
            pressures_bar=pressures,
            wear_pct=wear,
            life_pct=life_pct,
            wear_rate_per_lap=wear_rate,
            estimated_laps_remaining=laps_until_very_worn,
            is_overheating=len(overheated) > 0,
            is_cold=len(cold) > 0,
            overheated_tires=overheated,
            cold_tires=cold,
            balance_note=balance_note,
            is_gone_off=is_gone_off,
            is_very_worn=is_very_worn,
            is_critical_wear=is_critical_wear,
            limiting_tire=limiting_name,
            corner_wear_rates=corner_rates,
            condition_summary=condition_summary
        )
