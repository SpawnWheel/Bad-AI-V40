"""
Timed-race finish from the leader's position, the fuel safety margin for "one more lap", and the
AMS2 lap-counter glitch that made fuel burn look lower than it was.
"""

import dataclasses
import unittest

from race.car_track_stats import CarTrackStats
from race.fuel import FuelTracker
from race.race_distance import compute_race_distance
from telemetry.collector import ParticipantSnapshot
from tests.test_fuel_finish_margin import make_dummy_snapshot

PACE = 100.0   # s/lap, track 1000 m -> 10 m per second


def snap(lap, dist, t_rem, leader_lap, leader_dist, position=4):
    base = make_dummy_snapshot()
    ld = ParticipantSnapshot(name="Leader", position=1, lap=leader_lap, sector=0, lap_distance=leader_dist, is_active=True)
    me = ParticipantSnapshot(name="Driver", position=position, lap=lap, sector=0, lap_distance=dist, is_active=True)
    return dataclasses.replace(base, track_length_m=1000.0, total_laps=0, current_lap=lap,
                               current_lap_distance=dist, event_time_remaining_sec=t_rem,
                               participants=(ld, me), position=position)


class TestFinishFromTheLeader(unittest.TestCase):
    def test_leader_just_past_the_line_when_the_clock_runs_out(self):
        # Clock has 3 s left. Leader just crossed into lap 12 (20 m in); we're 950 m into lap 11.
        # He has to finish lap 12, so we finish our lap 11 AND lap 12.
        s = snap(11, 950.0, 3.0, 12, 20.0)
        rd = compute_race_distance(s, PACE, extra_laps=0)
        self.assertEqual(rd.finish_lap, 12)
        self.assertEqual(rd.laps_incl_current, 2)
        # "+1 lap" race: one more on top
        self.assertEqual(compute_race_distance(s, PACE, extra_laps=1).finish_lap, 13)

    def test_leader_just_short_of_the_line_when_the_clock_runs_out(self):
        # Leader 20 m before the line on lap 11 with 1 s left: the clock runs out first, he finishes lap 11.
        # We're half a lap behind on the same lap -> lap 11 is our last too.
        s = snap(11, 480.0, 1.0, 11, 980.0)
        rd = compute_race_distance(s, PACE, extra_laps=0)
        self.assertEqual(rd.finish_lap, 11)
        self.assertEqual(rd.laps_incl_current, 1)
        self.assertEqual(compute_race_distance(s, PACE, extra_laps=1).finish_lap, 12)

    def test_we_are_just_past_the_line_and_the_leader_is_almost_a_lap_up(self):
        # We crossed into lap 11 a moment ago; leader is about to finish lap 11 (nearly lapping us).
        # Clock out now -> he finishes lap 11 within seconds, we still have to complete our lap 11.
        s = snap(11, 30.0, 1.0, 11, 990.0)
        self.assertEqual(compute_race_distance(s, PACE, extra_laps=0).finish_lap, 11)

    def test_extra_lap_fuel_margin_grows_with_the_time_left(self):
        # Leader projected to reach the line 0.1 lap AFTER the clock runs out.
        # 12 laps of clock left: a 1% faster leader gets there in time -> plan fuel for one more lap.
        far = snap(2, 0.0, 1190.0, 2, 0.0, position=1)        # x = 1 + 11.9 = 12.9
        self.assertTrue(compute_race_distance(far, PACE, extra_laps=0).borderline_long)
        # 1 lap of clock left: the projection is good to a few metres -> no extra lap
        near = snap(12, 0.0, 90.0, 12, 0.0, position=1)        # x = 11 + 0.9 = 11.9
        self.assertFalse(compute_race_distance(near, PACE, extra_laps=0).borderline_long)


class TestLapCounterGlitch(unittest.TestCase):
    def test_fuel_tracker_keeps_the_lap_start_reading(self):
        ft = FuelTracker()
        ft.on_lap_started(2, 40.0)
        ft.on_lap_started(3, 37.6)           # lap 2: 2.4 L
        ft.on_lap_started(3, 37.5)           # glitch: same lap reported again a second later
        ft.on_lap_started(4, 35.2)           # lap 3 must be 37.6 -> 35.2 = 2.4 L, not 2.3
        self.assertAlmostEqual(ft.get_avg_consumption(), 2.4, places=3)

    def test_state_counts_each_lap_once_when_the_lap_number_flickers(self):
        from race.state import RaceStateManager
        from tests.test_incidents_and_alerts import TestIncidentsAndTacticalAlerts
        tc = TestIncidentsAndTacticalAlerts()
        sm = RaceStateManager()
        mk = lambda lap, fuel, last: tc.make_snapshot(current_lap=lap, fuel_liters=fuel, last_lap_time=last,
                                                     total_laps=20, position=3)
        for lap, fuel in ((3, 40.0), (4, 37.6)):
            sm.on_telemetry(mk(lap, fuel, 91.0))
        # line crossed into lap 5, then the sim reports lap 4 again twice, then 5 again
        for lap, fuel in ((5, 35.2), (4, 35.15), (4, 35.1), (5, 35.05), (5, 35.0)):
            sm.on_telemetry(mk(lap, fuel, 90.5))
        self.assertEqual(sm.current_lap, 5)
        self.assertEqual(sm.lap_times, [91.0, 90.5])
        self.assertEqual([b[0] for b in sm.fuel_ledger.lap_burns].count(4), 1)
        self.assertAlmostEqual(sm.fuel_tracker.get_avg_consumption(), 2.4, places=2)


class TestSavedBurns(unittest.TestCase):
    def test_short_measured_laps_dont_drag_the_seed_down(self):
        # Donington Mini as stored before the fix: half the entries were glitch-shortened laps
        stored = [2.51, 2.38, 2.53, 2.52, 2.49, 1.9, 0.51, 0.74, 1.11, 0.3, 2.44, 0.38]
        self.assertAlmostEqual(CarTrackStats.robust_burn(stored), 2.51, places=2)
        self.assertIsNone(CarTrackStats.robust_burn([]))


if __name__ == "__main__":
    unittest.main()
