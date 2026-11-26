"""
Practice: the engineer knows what changed between runs without being told, pressures are judged
with a dead band (no more pumping the tyres up on 1-2°C noise), and runs are not split by pit-mode
flicker around the garage.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from practice.handling import HandlingAnalyzer, judge_pressure_profile, cold_equivalent_bar, ATM_BAR, REF_AIR_K
from practice.session_manager import PracticeSessionManager
from practice.setup_journal import SetupJournal
from race.track_model import TrackModel
from telemetry.shared_memory import SessionState, PitMode
from tests.test_pit_stop_tracker import make_dummy_snapshot

LAP_FRAMES = 300      # 30 s laps at 10 Hz


def hot_from_cold(cold_bar: float, air_c: float) -> float:
    return (cold_bar + ATM_BAR) * (air_c + 273.15) / REF_AIR_K - ATM_BAR


class TestPressureProfile(unittest.TestCase):
    def test_oulton_readings_are_not_a_pressure_signal(self):
        # 24 Nov Oulton Park, Caterham: middles ran 1.5-2.5°C cooler than the shoulders all session
        for i, m, o in ((56, 54, 55), (54, 48, 47), (58, 57, 61), (57, 54, 54)):
            self.assertEqual(judge_pressure_profile(i, m, o)[0], "OK", (i, m, o))

    def test_clear_signals_still_count(self):
        self.assertEqual(judge_pressure_profile(85, 90, 80)[0], "HIGH")
        self.assertEqual(judge_pressure_profile(85, 76, 80)[0], "LOW")
        self.assertEqual(judge_pressure_profile(40, 38, 39)[0], "COLD")

    def test_cold_equivalent_is_temperature_independent(self):
        for air in (20.0, 60.0, 95.0):
            self.assertAlmostEqual(cold_equivalent_bar(hot_from_cold(1.70, air), air), 1.70, places=4)


class TestSetupAwareness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name)
        self.journal = SetupJournal(path=p / "j.json")
        self.pm = PracticeSessionManager(journal=self.journal)
        self.pm.handling = HandlingAnalyzer(track_model=TrackModel(storage_path=p / "t.json"))
        self.base = dataclasses.replace(
            make_dummy_snapshot(), session_state=SessionState.SESSION_PRACTICE, track_name="TestRing",
            track_variation="GP", track_length_m=3000.0, car_name="Caterham Academy")
        self.t = 1000.0
        self.lap = 1
        self.last = 0.0

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, pit=PitMode.PIT_MODE_NONE, speed=150.0, cold=1.70, air=60.0, mid=(54, 50, 55, 54),
              throttle=0.6, rps=(30.0, 30.0, 30.0, 30.0), gear=4, i=0):
        self.t += 0.1
        hot = hot_from_cold(cold, air) if isinstance(cold, float) else tuple(hot_from_cold(c, air) for c in cold)
        hot4 = hot if isinstance(hot, tuple) else (hot,) * 4
        s = dataclasses.replace(
            self.base, timestamp=self.t, current_lap=self.lap, current_lap_distance=10.0 * (i % LAP_FRAMES),
            current_lap_time=0.1 * (i % LAP_FRAMES), last_lap_time=self.last, speed_kmh=speed, pit_mode=pit,
            throttle=throttle, brake=0.0, steering=0.0, gear=gear, tyre_rps=rps,
            tyre_slip_speed_ms=(0.2, 0.2, 0.2, 0.2), tyre_pressures_bar=hot4,
            tyre_air_temps_c=(air, air, air, air),
            tyre_temps_inner_c=(56, 54, 58, 57), tyre_temps_center_c=mid, tyre_temps_outer_c=(55, 47, 61, 54),
            fuel_liters=30.0)
        self.pm.on_telemetry(s)

    def garage_visit(self):
        for pm in (PitMode.PIT_MODE_DRIVING_INTO_PITS, PitMode.PIT_MODE_IN_PIT, PitMode.PIT_MODE_IN_GARAGE,
                   PitMode.PIT_MODE_DRIVING_OUT_OF_PITS):
            for _ in range(5):
                self.frame(pit=pm, speed=0.0 if pm != PitMode.PIT_MODE_DRIVING_OUT_OF_PITS else 50.0)

    def do_run(self, laps=3, cold=1.70, mid=(54, 50, 55, 54), air_by_lap=None):
        """Out-lap + flying laps, then back into the pits."""
        for k in range(laps + 1):
            air = air_by_lap[k] if air_by_lap else 40.0 + 6.0 * k     # tyres heat up through the run
            for i in range(LAP_FRAMES):
                self.frame(cold=cold, air=air, mid=mid, i=i)
            self.last = 75.0 + 0.137 * k + 0.011 * self.lap      # distinct lap times
            self.lap += 1
        self.garage_visit()

    def test_measured_pressure_change_is_logged_without_driver_report(self):
        self.do_run(cold=1.70)
        self.do_run(cold=1.74)
        r2 = self.pm.completed_runs[-1]
        self.assertEqual(len(self.pm.completed_runs), 2)
        self.assertTrue(any("all four +0.04 bar" in c for c in r2.changes_before), r2.changes_before)
        self.assertIn("change before this run: Measured: set tyre pressures all four +0.04 bar", self.pm.get_comparative_debrief())

    def test_unchanged_pressures_are_not_reported_as_a_change(self):
        self.do_run(cold=1.70)
        self.do_run(cold=1.70)
        self.assertEqual(self.pm.completed_runs[-1].changes_before, [])
        self.assertIn("unchanged", " ".join(self.pm.completed_runs[-1].detected_changes))

    def test_engineer_call_is_assumed_made_after_a_garage_visit(self):
        self.do_run(cold=1.70)
        self.pm.propose_change("Front ARB -2 clicks")
        self.do_run(cold=1.70)
        ch = self.pm.completed_runs[-1].changes_before
        self.assertTrue(any("Front ARB -2 clicks" in c and "assumed made" in c for c in ch), ch)

    def test_pressure_call_not_seen_in_measurement_is_flagged(self):
        self.do_run(cold=1.70)
        self.pm.propose_change("All four cold pressures -0.10 bar")
        self.do_run(cold=1.70)
        r = self.pm.completed_runs[-1]
        self.assertEqual(r.changes_before, [])
        self.assertTrue(any("does NOT show" in c for c in r.detected_changes), r.detected_changes)

    def test_pit_flicker_does_not_create_empty_runs_or_steal_the_change(self):
        self.do_run(cold=1.70)
        # driver confirms a change in the box; then pit mode flickers to 0 and back before he really leaves
        self.journal.add_change(self.pm.journal_key, "Rear ARB +1", self.pm.next_run_number)
        for _ in range(10):
            self.frame(pit=PitMode.PIT_MODE_NONE, speed=40.0)
        self.garage_visit()
        self.do_run(cold=1.70)
        self.assertEqual([r.run_number for r in self.pm.completed_runs], [1, 2])
        self.assertIn("Rear ARB +1", self.pm.completed_runs[-1].changes_before)

    def test_pressure_chasing_warning(self):
        # middle stays ~2°C cool whatever the pressure
        self.do_run(cold=1.70, mid=(53, 49, 57, 54))
        self.do_run(cold=1.74, mid=(53, 49, 57, 54))
        sheet = self.pm.get_comparative_debrief()
        self.assertIn("PRESSURE NOT MOVING THE PROFILE", sheet)
        self.assertIn("flat - no pressure signal", sheet)


class TestDrivetrain(unittest.TestCase):
    def _feed(self, ha, rear_factor_under_power):
        base = dataclasses.replace(make_dummy_snapshot(), pit_mode=PitMode.PIT_MODE_NONE)
        t = 0.0
        for i in range(400):
            t += 0.1
            power = (i // 20) % 2 == 0
            rps = (30.0, 30.0, 30.0 * (rear_factor_under_power if power else 1.0) * 1.02,
                   30.0 * (rear_factor_under_power if power else 1.0) * 1.02)   # rears 2% bigger/smaller-radius offset
            s = dataclasses.replace(base, current_lap=1, current_lap_time=t, speed_kmh=90.0, steering=0.0,
                                    throttle=1.0 if power else 0.1, brake=0.0, gear=3 if power else 4,
                                    tyre_rps=rps, tyre_slip_speed_ms=(0.1, 0.1, 0.15, 0.15),
                                    local_accel_ms2=(0.0, 0.0, 2.0), angular_velocity_rads=(0.0, 0.0, 0.0))
            ha.on_telemetry(s)

    def test_low_power_rwd_is_detected(self):
        ha = HandlingAnalyzer(track_model=None)
        ha._track_model = False  # no track model needed
        self._feed(ha, 1.03)
        self.assertEqual(ha.drivetrain, "RWD")

    def test_fwd_is_detected(self):
        ha = HandlingAnalyzer(track_model=None)
        ha._track_model = False
        self._feed(ha, 0.97)
        self.assertEqual(ha.drivetrain, "FWD")


if __name__ == "__main__":
    unittest.main()
