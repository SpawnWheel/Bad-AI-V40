"""
Regression for the 30 Sep Watkins Glen race (MINI, 44 L tank, ~3.9 L/lap, 30-minute timed race):
the car finished with 12.4 L instead of the ~1 L safety net.

Causes reproduced here:
  1. mSessionAdditionalLaps = 0 (no extra lap) was treated as "unknown" and replaced by 1  -> +1 lap of fuel
  2. the leader still had his own pit stop to make when we stopped; the flag fell a lap earlier
     than a no-stop projection of the leader said                                           -> +1 lap of fuel
  3. the safety net was 1 lap (~4 L) instead of 1 L                                         -> +3 L
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from race.state import RaceStateManager
from race.events import EventType
from race.track_model import TrackModel
from race.pit_stop_tracker import PitStopTracker
from race.car_track_stats import CarTrackStats
from race.race_distance import compute_race_distance
from telemetry.collector import ParticipantSnapshot
from telemetry.shared_memory import SessionState, RaceState, FlagColour, PitMode
from tests.test_pit_stop_tracker import make_dummy_snapshot

LEN = 5430.0
LAP = 119.0
V = LEN / LAP
BURN = 3.9
TANK = 44.0
RACE_SEC = 30 * 60
PIT_ENTRY = 5250.0
PIT_EXIT = 350.0
REFUEL_LPS = 0.7
LEADER_AHEAD_SEC = 30.0
LEADER_LANE_SEC = 75.0
LEADER_PIT_LAP = 11


class TestFuelFinishMargin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.tm = TrackModel(storage_path=d / "track.json")
        self.pits = PitStopTracker(storage_path=d / "pits.json", track_model=self.tm)
        self.stats = CarTrackStats(path=d / "stats.json")
        self.base = make_dummy_snapshot()
        self.tm.select_track("Watkins_Glen", "Watkins_Glen_GP", LEN)
        self.tm.info.pit_entry_obs = [PIT_ENTRY]
        self.tm.info.pit_exit_obs = [PIT_EXIT]
        key = self.pits.get_track_key("Watkins_Glen", "Watkins_Glen_GP")
        rec = self.pits._track_rec(key)
        rec["ai_lane_time"] = [LEADER_LANE_SEC] * 5
        rec["drive_loss"] = [15.0] * 3
        self.pits._car_rec("MINI Cooper JCW")["refuel_lps"] = [REFUEL_LPS]

    def tearDown(self):
        self.tmp.cleanup()

    def make_mgr(self, **kw):
        mgr = RaceStateManager(pit_stop_tracker_inst=self.pits, track_model_inst=self.tm, stats_store=self.stats, **kw)
        events = []
        mgr.register_event_listener(lambda e: events.append(e))
        return mgr, events

    # leader: constant pace, LEADER_AHEAD_SEC ahead, one stop on LEADER_PIT_LAP that costs ~63 s
    def leader_state(self, t):
        racing_t = t + LEADER_AHEAD_SEC
        t_in = LEADER_PIT_LAP * LAP - (LEN - PIT_ENTRY) / V            # racing-time at pit entry
        lane_len = (LEN - PIT_ENTRY) + PIT_EXIT
        if racing_t < t_in:
            return racing_t * V, 0
        if racing_t < t_in + LEADER_LANE_SEC:
            frac = (racing_t - t_in) / LEADER_LANE_SEC
            return t_in * V + frac * lane_len, 2
        return t_in * V + lane_len + (racing_t - t_in - LEADER_LANE_SEC) * V, 0

    def snap(self, t, lap, dist, fuel, t_rem, pit, speed, finished, last_lap, best_lap, lap_time,
             leader_d, leader_pm, leader_finished, extra_raw):
        me = ParticipantSnapshot(name="Driver", position=2, lap=lap, sector=min(2, int(3 * dist / LEN)),
                                 lap_distance=dist, is_active=True, pit_mode=int(pit.value),
                                 last_lap_time=last_lap, best_lap_time=best_lap)
        l_lap = int(leader_d // LEN) + 1
        ld = ParticipantSnapshot(name="Dan Zelos", position=1, lap=l_lap, sector=0,
                                 lap_distance=leader_d - (l_lap - 1) * LEN, is_active=True, pit_mode=leader_pm,
                                 last_lap_time=LAP, best_lap_time=LAP,
                                 race_state=int(RaceState.RACESTATE_FINISHED) if leader_finished else 2)
        return dataclasses.replace(
            self.base, timestamp=t, session_state=SessionState.SESSION_RACE, race_state=RaceState.RACESTATE_RACING,
            track_name="Watkins_Glen", track_variation="Watkins_Glen_GP", track_length_m=LEN, total_laps=0,
            car_name="MINI Cooper JCW", position=2, current_lap=lap, laps_completed=lap - 1,
            current_lap_distance=dist, current_lap_time=lap_time, fuel_liters=fuel, fuel_capacity_liters=TANK,
            fuel_level_pct=100 * fuel / TANK, event_time_remaining_sec=t_rem, pit_mode=pit, speed_kmh=speed,
            last_lap_time=last_lap, best_lap_time=best_lap, is_race_finished=finished,
            flag_colour=FlagColour.FLAG_CHEQUERED if finished else FlagColour.FLAG_GREEN,
            participants=(ld, me), player_name="Driver", split_time_ahead=LEADER_AHEAD_SEC, split_time_behind=2.5,
            session_additional_laps=extra_raw,
        )

    def run_race(self, mgr, events, extra_raw=0):
        dt = 0.5
        t = 0.0
        fuel = TANK
        lap, dist, lap_t = 1, 0.0, 0.0
        last = best = 0.0
        pit_state = None
        stop_until = 0.0
        pit_lap = None
        refuel = 0.0
        leader_flag_d = None
        finished_lap = None
        while t < RACE_SEC + 5 * LAP:
            t_rem = RACE_SEC - t if t < RACE_SEC else -1.0
            leader_d, leader_pm = self.leader_state(t)
            if t >= RACE_SEC and leader_flag_d is None:
                # AMS2 with 0 extra laps: leader takes the flag at the end of the lap in progress
                leader_flag_d = (int(leader_d // LEN) + 1 + extra_raw) * LEN
            leader_finished = leader_flag_d is not None and leader_d >= leader_flag_d
            box_call = next((e for e in events if e.event_type == EventType.BOX_THIS_LAP), None)
            if box_call and pit_lap is None:
                pit_lap = box_call.details["pit_lap"]
                refuel = box_call.details["refuel_l"]
            pm, speed = PitMode.PIT_MODE_NONE, 165.0
            if pit_state is None and pit_lap == lap and dist >= PIT_ENTRY:
                pit_state = "in"
            if pit_state == "in":
                pm, speed = PitMode.PIT_MODE_DRIVING_INTO_PITS, 60.0
                if dist >= PIT_ENTRY + 120:
                    pit_state, stop_until = "stopped", t + refuel / REFUEL_LPS
            elif pit_state == "stopped":
                pm, speed = PitMode.PIT_MODE_IN_PIT, 0.0
                fuel = min(TANK, fuel + REFUEL_LPS * dt)
                if t >= stop_until:
                    pit_state = "out"
            elif pit_state == "out":
                pm, speed = PitMode.PIT_MODE_DRIVING_OUT_OF_PITS, 60.0
                if lap == pit_lap + 1 and dist >= PIT_EXIT:
                    pit_state, pm = "done", PitMode.PIT_MODE_NONE
            finished = finished_lap is not None
            mgr.on_telemetry(self.snap(t, lap, dist, fuel, t_rem, pm, speed, finished, last, best, lap_t,
                                       leader_d, leader_pm, leader_finished, extra_raw))
            if finished:
                break
            v = V if speed > 100 else (60 / 3.6 if speed > 0 else 0.0)
            dist += v * dt
            lap_t += dt
            if speed > 0:
                fuel = max(0.0, fuel - BURN * (v * dt) / LEN)
            t += dt
            if dist >= LEN:
                dist -= LEN
                last, lap_t = lap_t, 0.0
                best = min(best, last) if best else last
                lap += 1
                if leader_flag_d is not None and self.leader_state(t)[0] >= leader_flag_d:
                    finished_lap = lap - 1
        return fuel, finished_lap, refuel, pit_lap

    def test_finishes_with_about_one_litre(self):
        mgr, events = self.make_mgr()
        end_fuel, finished_lap, refuel, pit_lap = self.run_race(mgr, events, extra_raw=0)
        self.assertEqual(finished_lap, 15)
        self.assertIsNotNone(pit_lap)
        self.assertGreater(end_fuel, 0.3, f"ran too low: {end_fuel:.2f} L (added {refuel:.1f} L on lap {pit_lap})")
        self.assertLess(end_fuel, 2.0, f"too much left: {end_fuel:.2f} L (added {refuel:.1f} L on lap {pit_lap})")
        self.assertNotIn(EventType.FUEL_EMERGENCY, [e.event_type for e in events])

    def test_extra_lap_setting_is_respected(self):
        """Same race with AMS2's '+1 lap' option (mSessionAdditionalLaps=1): one more lap of fuel, still ~1 L left."""
        mgr, events = self.make_mgr()
        end_fuel, finished_lap, refuel, pit_lap = self.run_race(mgr, events, extra_raw=1)
        self.assertEqual(finished_lap, 16)
        self.assertGreater(end_fuel, 0.3, f"ran too low: {end_fuel:.2f} L")
        self.assertLess(end_fuel, 2.0, f"too much left: {end_fuel:.2f} L")


class TestRaceDistanceUnits(unittest.TestCase):
    def _snap(self, lap, dist, t_rem, leader_lap, leader_dist, position=2):
        base = make_dummy_snapshot()
        ld = ParticipantSnapshot(name="L", position=1, lap=leader_lap, sector=0, lap_distance=leader_dist, is_active=True)
        me = ParticipantSnapshot(name="Driver", position=position, lap=lap, sector=0, lap_distance=dist, is_active=True)
        return dataclasses.replace(base, track_length_m=1000.0, total_laps=0, current_lap=lap,
                                   current_lap_distance=dist, event_time_remaining_sec=t_rem,
                                   participants=(ld, me), position=position)

    def test_zero_extra_laps(self):
        s = self._snap(10, 500.0, 350.0, 10, 600.0)   # leader at 9.6 laps + 3.5 laps of clock -> clock out during lap 14
        rd = compute_race_distance(s, 100.0, extra_laps=0)
        self.assertEqual(rd.finish_lap, 14)
        rd1 = compute_race_distance(s, 100.0, extra_laps=1)
        self.assertEqual(rd1.finish_lap, 15)

    def test_leader_pending_stop_shortens_race(self):
        s = self._snap(10, 500.0, 330.0, 10, 600.0)   # leader 9.6 + 3.3 = 12.9 -> flag end of lap 13
        self.assertEqual(compute_race_distance(s, 100.0, extra_laps=0).finish_lap, 13)
        # leader still has to lose 60 s: 9.6 + 2.7 = 12.3 -> still lap 13 for him, but he finishes 60 s later
        # and we (0.1 lap behind, no stop) are past the line by then -> we finish lap 13 too
        rd = compute_race_distance(s, 100.0, extra_laps=0, leader_time_loss_sec=60.0)
        self.assertEqual(rd.finish_lap, 13)
        s2 = self._snap(10, 500.0, 360.0, 10, 600.0)  # 9.6 + 3.6 = 13.2 -> lap 14 without his stop
        self.assertEqual(compute_race_distance(s2, 100.0, extra_laps=0).finish_lap, 14)
        rd2 = compute_race_distance(s2, 100.0, extra_laps=0, leader_time_loss_sec=60.0, player_time_loss_sec=60.0)
        self.assertEqual(rd2.finish_lap, 13)   # 9.6 + 3.0 = 12.6 -> lap 13

    def test_borderline_long(self):
        s = self._snap(10, 500.0, 336.0, 10, 600.0)   # 9.6 + 3.36 = 12.96 -> lap 13 but only 0.04 lap from 14
        rd = compute_race_distance(s, 100.0, extra_laps=0)
        self.assertEqual(rd.finish_lap, 13)
        self.assertTrue(rd.borderline_long)


if __name__ == "__main__":
    unittest.main()
