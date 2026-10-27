"""
End-to-end replay of a timed race modelled on the 29 Sep Watkins Glen log (MINI, 44 L tank,
~3.9 L/lap, ~119 s laps, 30 min + 1 lap). Verifies:
  * pit calls come early: heads-up at the start of lap k-1, "box this lap" at the start of lap k,
    short reminder before pit entry
  * no false fuel emergency on the final lap
  * the fuel ledger knows start fuel, fuel added and total used (the post-race questions)
  * race distance: flag at the end of the lap after the clock expires (+1)
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
from telemetry.collector import TelemetrySnapshot, ParticipantSnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from tests.test_pit_stop_tracker import make_dummy_snapshot

LEN = 5430.0
LAP = 119.0
BURN = 3.9
TANK = 44.0
RACE_SEC = 30 * 60
PIT_ENTRY = 5250.0
PIT_EXIT = 350.0


class TestTimedRaceReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.tm = TrackModel(storage_path=d / "track.json")
        self.pits = PitStopTracker(storage_path=d / "pits.json", track_model=self.tm)
        self.stats = CarTrackStats(path=d / "stats.json")
        self.mgr = RaceStateManager(pit_stop_tracker_inst=self.pits, track_model_inst=self.tm, stats_store=self.stats,
                                    timed_race_extra_laps=1)
        self.events = []
        self.mgr.register_event_listener(lambda e: self.events.append(e))
        self.base = make_dummy_snapshot()
        # pit entry learned in practice
        self.tm.select_track("Watkins_Glen", "Watkins_Glen_GP", LEN)
        self.tm.info.pit_entry_obs = [PIT_ENTRY]

    def tearDown(self):
        self.tmp.cleanup()

    def snap(self, t, lap, dist, fuel, t_rem, pit=PitMode.PIT_MODE_NONE, speed=165.0, finished=False,
             last_lap=0.0, best_lap=0.0, lap_time=0.0):
        me = ParticipantSnapshot(name="Driver", position=1, lap=lap, sector=min(2, int(3 * dist / LEN)),
                                 lap_distance=dist, is_active=True, pit_mode=int(pit.value),
                                 last_lap_time=last_lap, best_lap_time=best_lap)
        ai = ParticipantSnapshot(name="Mike Epps", position=2, lap=lap, sector=0,
                                 lap_distance=max(0.0, dist - 300.0), is_active=True, pit_mode=0,
                                 last_lap_time=last_lap, best_lap_time=best_lap)
        return dataclasses.replace(
            self.base, timestamp=t, session_state=SessionState.SESSION_RACE, race_state=RaceState.RACESTATE_RACING,
            track_name="Watkins_Glen", track_variation="Watkins_Glen_GP", track_length_m=LEN, total_laps=0,
            car_name="MINI Cooper JCW", position=1, current_lap=lap, laps_completed=lap - 1,
            current_lap_distance=dist, current_lap_time=lap_time, fuel_liters=fuel, fuel_capacity_liters=TANK,
            fuel_level_pct=100 * fuel / TANK, event_time_remaining_sec=t_rem, pit_mode=pit, speed_kmh=speed,
            last_lap_time=last_lap, best_lap_time=best_lap, is_race_finished=finished,
            flag_colour=FlagColour.FLAG_CHEQUERED if finished else FlagColour.FLAG_GREEN,
            participants=(me, ai), player_name="Driver", split_time_ahead=0.0, split_time_behind=2.5,
            session_additional_laps=1,
        )

    def run_race(self):
        dt = 0.5
        t = 0.0
        fuel = TANK
        lap, dist, lap_t = 1, 0.0, 0.0
        last = best = 0.0
        pit_state = None
        stop_until = 0.0
        added_target = 0.0
        self.pit_lap = None
        self.lap_events = []
        finished_lap = None
        while t < RACE_SEC + 4 * LAP:
            t_rem = max(0.0, RACE_SEC - t)
            # pit decision: driver obeys "box this lap"
            box_call = next((e for e in self.events if e.event_type == EventType.BOX_THIS_LAP), None)
            if box_call and self.pit_lap is None:
                self.pit_lap = box_call.details["pit_lap"]
                added_target = box_call.details["refuel_l"]
            pm = PitMode.PIT_MODE_NONE
            speed = 165.0
            if pit_state is None and self.pit_lap == lap and dist >= PIT_ENTRY:
                pit_state = "in"
            if pit_state == "in":
                pm = PitMode.PIT_MODE_DRIVING_INTO_PITS
                speed = 60.0
                if dist >= PIT_ENTRY + 120:
                    pit_state, stop_until = "stopped", t + added_target / 2.0
                    self.fuel_before_stop = fuel
            elif pit_state == "stopped":
                pm, speed = PitMode.PIT_MODE_IN_PIT, 0.0
                fuel = min(TANK, fuel + 2.0 * dt)
                if t >= stop_until:
                    pit_state = "out"
            elif pit_state == "out":
                pm, speed = PitMode.PIT_MODE_DRIVING_OUT_OF_PITS, 60.0
                if lap == self.pit_lap + 1 and dist >= PIT_EXIT:
                    pit_state = "done"
                    pm = PitMode.PIT_MODE_NONE
            finished = finished_lap is not None
            self.mgr.on_telemetry(self.snap(t, lap, dist, fuel, t_rem, pm, speed, finished, last, best, lap_t))
            if finished:
                break
            # advance
            v = LEN / LAP if speed > 100 else (60 / 3.6 if speed > 0 else 0.0)
            dist += v * dt
            lap_t += dt
            if speed > 0:
                fuel = max(0.0, fuel - BURN * (v * dt) / LEN)
            t += dt
            if dist >= LEN:
                dist -= LEN
                last = lap_t
                best = min(best, lap_t) if best else lap_t
                lap_t = 0.0
                lap += 1
                rd = self.mgr._last_race_distance
                if rd and rd.clock_expired and lap > rd.finish_lap:
                    finished_lap = lap - 1
        self.finished_lap = finished_lap
        return fuel

    def test_replay(self):
        end_fuel = self.run_race()
        types = [e.event_type for e in self.events]

        # --- pit call sequence
        heads = [e for e in self.events if e.event_type == EventType.BOX_NEXT_LAP]
        this_lap = [e for e in self.events if e.event_type == EventType.BOX_THIS_LAP]
        remind = [e for e in self.events if e.event_type == EventType.BOX_REMINDER]
        self.assertEqual(len(heads), 1, types)
        self.assertEqual(len(this_lap), 1, types)
        self.assertEqual(len(remind), 1, types)
        k = this_lap[0].details["pit_lap"]
        self.assertEqual(heads[0].details["pit_lap"], k)
        self.assertIn("You're", this_lap[0].message)
        pct = int(this_lap[0].message.split("You're ")[1].split("%")[0])
        self.assertLess(pct, 10, "box-this-lap must come at the START of the pit lap")
        self.assertIsNotNone(remind[0].details["time_to_entry"])
        self.assertLessEqual(remind[0].details["time_to_entry"], 21)
        self.assertGreater(remind[0].details["time_to_entry"], 5)

        # --- no fuel panic at the end, finished with fuel
        self.assertNotIn(EventType.FUEL_EMERGENCY, types)
        self.assertGreater(end_fuel, 0.5)

        # --- race distance: clock expires during lap N (15 laps + a ~25 s stop > 1800 s -> lap 15)
        #     -> flag at the end of lap N + 1
        self.assertEqual(self.mgr._leader_lap_at_time_expiry, 15)
        self.assertEqual(self.finished_lap, 16)

        # --- ledger answers the post-race questions
        led = self.mgr.fuel_ledger
        self.assertAlmostEqual(led.start_fuel, TANK, places=1)
        self.assertGreater(led.total_added, 10)
        self.assertAlmostEqual(led.total_used, led.start_fuel + led.total_added - led.current_fuel, places=3)
        self.assertAlmostEqual(led.total_used, BURN * 16, delta=2.5)
        summary = led.summary(16)
        self.assertIn("Started with 44.0 L", summary)
        self.assertIn("Total used", summary)

        # --- pit loss learned as drive-through + service, not raw lane time
        stop = self.pits.last_stop
        self.assertIsNotNone(stop)
        self.assertGreater(stop["stationary"], 3)
        self.assertIsNotNone(stop["loss"])
        self.assertLess(stop["drive_loss"], stop["loss"])

        # --- learned stats for next time
        self.assertIsNotNone(self.stats.fuel_burn("MINI Cooper JCW", "Watkins_Glen", "Watkins_Glen_GP"))
        self.assertIsNotNone(self.tm.info.pit_entry_m)


if __name__ == "__main__":
    unittest.main()
