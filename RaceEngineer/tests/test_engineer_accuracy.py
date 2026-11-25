"""
Race engineer accuracy fixes (Oulton Park, Nov 2026 review):
 - our car is locked by name, not by the camera's car (the engineer called thommo's P8 as our finish)
 - gaps come from every car's distance history, not AMS2's split times (75 s to a car 1.2 s ahead after a stop)
 - a faster car on its way is spotted from clean-lap pace, and our own spin doesn't fake a closing rate
 - fuel planning numbers exist outside the race (quali / between sessions)
 - online names are made speakable ("[RCV]RayFin" -> "Ray Fin")
"""

import types
import unittest

from race.gaps import GapClock
from race.radar import RaceRadar, robust_rate, quick_pace
from utils.names import spoken_name, build_name_map, apply_name_map


def _car(name, pos, laps, dist, last=0.0, best=0.0):
    return types.SimpleNamespace(name=name, position=pos, laps_completed=laps, lap_distance=dist,
                                 is_active=True, pit_mode=0, last_lap_time=last, best_lap_time=best)


class TestGapClock(unittest.TestCase):
    L = 2000.0

    def _feed(self, clock, t, cars):
        clock.update(types.SimpleNamespace(track_length_m=self.L, participants=cars), now=t)

    def test_gap_is_time_since_car_ahead_was_here(self):
        c = GapClock()
        v = 25.0   # m/s, both cars; B runs 50 m (= 2.0 s) behind A
        for i in range(60):
            t = i * 0.5
            da, db = 400 + v * t, 350 + v * t
            self._feed(c, t, [_car("A", 1, int(da // self.L), da % self.L), _car("B", 2, int(db // self.L), db % self.L)])
        self.assertAlmostEqual(c.gap("A", "B"), 2.0, delta=0.05)
        self.assertIsNone(c.gap("B", "A"))          # B is not ahead of A

    def test_line_crossing_glitch_is_ignored(self):
        c = GapClock()
        v = 25.0
        for i in range(40):
            t = i * 0.5
            d = 1900 + v * t
            laps, dist = int(d // self.L), d % self.L
            if i == 10:
                laps = 0           # lap counter flips one tick late at the line: total jumps back a whole lap
            self._feed(c, t, [_car("A", 1, laps, dist), _car("B", 2, int((d - 25) // self.L), (d - 25) % self.L)])
        self.assertAlmostEqual(c.gap("A", "B"), 1.0, delta=0.05)


class TestRadarPace(unittest.TestCase):
    def test_robust_rate_ignores_one_jump(self):
        # gap steady at 3 s, then our spin adds 10 s in one segment
        samples = [(0.0, 3.0), (0.33, 3.0), (0.67, 3.0), (1.0, 3.0), (1.33, 13.0), (1.67, 13.0)]
        self.assertAlmostEqual(robust_rate(samples), 0.0, delta=0.01)

    def test_quick_pace_ignores_out_lap(self):
        self.assertAlmostEqual(quick_pace([73.99, 74.12, 75.67, 108.5, 74.92]), 74.055, delta=0.01)

    def test_faster_car_flagged_from_clean_laps(self):
        r = RaceRadar()
        r.set_my_pace(74.6, [74.5, 74.7, 74.6])
        bill = _car("Bill", 12, 13, 0.0, last=74.9)
        for lt in (73.99, 74.12, 75.67, 108.5, 74.92):
            bill.last_lap_time = lt
            r.note_laps([bill])
        r.sample_field(13.0, [("Bill", 12, 1.9, 74.92)])
        inc = r.incoming(laps_to_go=11)
        self.assertEqual(len(inc), 1)
        name, pos, gap, rate, meet = inc[0]
        self.assertEqual(name, "Bill")
        self.assertGreater(rate, 0.4)
        reasons = r.candidates(13, 11)
        self.assertTrue(any(x.key == "incoming:Bill" for x in reasons))

    def test_slower_car_not_flagged_even_if_gap_shrank(self):
        r = RaceRadar()
        r.set_my_pace(74.0, [74.0, 74.1])
        slow = _car("Slow", 9, 5, 0.0)
        for lt in (75.0, 75.2):
            slow.last_lap_time = lt
            r.note_laps([slow])
        for i, g in enumerate((8.0, 6.0, 4.0, 3.0, 2.5)):     # gap shrank because WE had a moment
            r.sample_field(5.0 + i / 3, [("Slow", 9, g, 75.2)])
        self.assertEqual(r.incoming(laps_to_go=10), [])

    def test_no_call_when_he_cannot_catch_before_the_flag(self):
        r = RaceRadar()
        r.set_my_pace(74.6, [74.6, 74.6])
        far = _car("Far", 14, 11, 0.0)
        for lt in (73.8, 73.8):
            far.last_lap_time = lt
            r.note_laps([far])
        r.sample_field(11.0, [("Far", 14, 14.4, 73.8)])
        self.assertFalse(any(x.key.startswith("incoming:") for x in r.candidates(11, 8)))


class TestSpokenNames(unittest.TestCase):
    def test_names(self):
        self.assertEqual(spoken_name("[RCV]RayFin"), "Ray Fin")
        self.assertEqual(spoken_name("Bill(AuchAye)_[SR-50]"), "Bill")
        self.assertEqual(spoken_name("[RCV]ATIBoy69"), "ATI Boy")
        self.assertEqual(spoken_name("kf.MTG"), "MTG")
        self.assertEqual(spoken_name("clinton.nortje"), "Clinton Nortje")
        self.assertEqual(spoken_name("hOnDa cAmInO"), "hOnDa cAmInO")
        self.assertEqual(spoken_name("Daniel Born"), "Daniel Born")

    def test_reply_text_is_fixed(self):
        m = build_name_map(["[RCV]RayFin", "[RCV]ATIBoy69", "Googlebear"])
        self.assertEqual(apply_name_map("RayFin's nine-two behind, ATIBoy69 ahead, Googlebear too.", m),
                         "Ray Fin's nine-two behind, ATI Boy ahead, Googlebear too.")


class TestPlayerLock(unittest.TestCase):
    def _raw(self, viewed, own_speed, speeds, names=("Daniel Born", "thommo", "Bill")):
        parts = [types.SimpleNamespace(mName=n, mIsActive=True) for n in names]
        return types.SimpleNamespace(mNumParticipants=len(names), mParticipantInfo=parts, mTrackLocation="Oulton",
                                     mTrackVariation="Fosters", mSpeed=own_speed, mSpeeds=list(speeds),
                                     mViewedParticipantIndex=viewed)

    def test_camera_on_another_car_keeps_us(self):
        from telemetry.collector import TelemetryCollector
        col = object.__new__(TelemetryCollector)
        col._player_lock_name, col._player_lock_track = "", ""
        col._player_candidate, col._player_candidate_hits = "", 0
        dec = lambda s: s
        for _ in range(4):   # we drive at 40 m/s, the camera is on us
            self.assertEqual(col._resolve_player_index(self._raw(0, 40.0, (40.0, 38.0, 41.5)), 0, dec), 0)
        # the camera jumps to thommo (index 1) at the end of the race: we stay index 0
        self.assertEqual(col._resolve_player_index(self._raw(1, 30.0, (30.0, 35.0, 33.0)), 1, dec), 0)


class TestFuelPlanning(unittest.TestCase):
    def test_planning_block_from_earlier_sessions(self):
        from race.state import RaceStateManager
        st = RaceStateManager()
        st.current_snapshot = None
        st._last_event = ("Caterham Academy", "OultonPark", "Fosters", 36.0)
        st.stats_store = types.SimpleNamespace(fuel_burn=lambda *a: 1.05, race_pace=lambda *a: 74.5)
        st.fuel_tracker = types.SimpleNamespace(burn_source=lambda: "default", get_avg_consumption=lambda: 3.5)
        block = st.fuel_planning_block()
        self.assertIn("1.05 L/lap", block)
        self.assertIn("30 min ~25 laps -> 27.3 L", block)    # 25 laps + one spare at 1.05


if __name__ == "__main__":
    unittest.main()


class TestPositionAfterStops(unittest.TestCase):
    def test_cars_still_to_stop_drop_back(self):
        from race.virtual_order import order_after_stops, describe_for_driver
        cars = [
            {"name": "A", "position": 1, "gap_to_leader": 0.0, "still_to_stop": True},
            {"name": "B", "position": 2, "gap_to_leader": 5.0, "still_to_stop": True},
            {"name": "Me", "position": 3, "gap_to_leader": 20.0, "still_to_stop": False},
            {"name": "C", "position": 4, "gap_to_leader": 30.0, "still_to_stop": False},
            {"name": "D", "position": 5, "gap_to_leader": 9.0, "still_to_stop": False, "pit_elapsed": 20.0},
        ]
        order = order_after_stops(cars, 38.0)
        self.assertEqual([x.name for x in order], ["Me", "D", "C", "A", "B"])
        text = describe_for_driver(order, "Me", 38.0)
        self.assertIn("On the road P3 -> once everyone has made their stop ~P1", text)


class TestPitLossLearning(unittest.TestCase):
    def test_partial_lane_passes_dont_shrink_the_loss(self):
        from race.pit_stop_tracker import PitStopTracker
        self.assertAlmostEqual(PitStopTracker._robust_loss([12.4, 3.66, 3.19, 3.71, 12.73]), 12.565, delta=0.01)


class TestLappedTraffic(unittest.TestCase):
    def test_lapper_behind_and_backmarker_ahead_on_the_road(self):
        L = 2000.0
        c = GapClock()
        for i in range(10):
            t = i * 0.5
            me = 5 * L + 1000 + 25 * t
            cars = [_car("Me", 8, int(me // L), me % L),
                    _car("Leader", 1, int((me + L - 150) // L), (me + L - 150) % L),     # a lap up, 150 m behind on the road
                    _car("Slow", 15, int((me - L + 200) // L), (me - L + 200) % L),      # a lap down, 200 m ahead on the road
                    _car("Rival", 7, int((me + 100) // L), (me + 100) % L)]              # same lap, just ahead
            c.update(types.SimpleNamespace(track_length_m=L, participants=cars), now=t)
        lappers, backs = c.road_traffic("Me", 20.0, 80.0)
        self.assertEqual([x[0] for x in lappers], ["leader"])
        self.assertEqual([x[0] for x in backs], ["slow"])
        self.assertAlmostEqual(lappers[0][1], 150 / 25.0, delta=0.1)
        self.assertEqual(lappers[0][2], 1)
