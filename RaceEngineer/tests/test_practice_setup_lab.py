"""
Practice setup lab: synthetic laps with a known handling signature must come out of the analyzer
with the right sign, and the debrief must present the run log, the measured handling, the car
knowledge and a run-vs-run comparison.
"""

import dataclasses
import math
import tempfile
import unittest
from pathlib import Path

from practice.session_manager import PracticeSessionManager
from practice.setup_journal import SetupJournal
from practice.handling import HandlingAnalyzer
from race.track_model import TrackModel
from telemetry.shared_memory import SessionState, PitMode
from tests.test_pit_stop_tracker import make_dummy_snapshot

LEN = 3000.0
# (start, end, radius m, apex speed km/h)
CORNERS = [(400, 600, 60, 80), (1200, 1450, 150, 130), (2000, 2200, 90, 100)]


def lap_profile(mid_understeer: float):
    """Yields (dist, speed_kmh, steer, yaw, lat_ms2, brake, throttle) at 10 Hz for one lap."""
    d = 0.0
    out = []
    while d < LEN:
        speed = 180.0
        steer = yaw = lat = 0.0
        brake, thr = 0.0, 1.0
        for (a, b, r, vk) in CORNERS:
            if a - 80 <= d < a:          # braking zone
                speed, brake, thr = vk + 30, 0.8, 0.0
            elif a <= d < b:
                frac = (d - a) / (b - a)
                speed = vk
                v = vk / 3.6
                yaw = v / r
                lat = v * v / r
                base = 0.25 * (60.0 / r)  # steering for this radius if neutral
                if frac < 0.3:            # entry, trail-braking
                    brake, thr = 0.3, 0.0
                    steer = base * 1.05
                elif frac < 0.7:          # mid
                    brake, thr = 0.0, 0.2
                    steer = base * (1.0 + mid_understeer)
                else:                     # exit
                    brake, thr = 0.0, 0.8
                    steer = base * 1.1
        v = max(5.0, speed / 3.6)
        out.append((d, speed, steer, yaw, lat, brake, thr))
        d += v * 0.1
    # some gentle low-g driving to learn the neutral response R0 (straight with slight curvature)
    return out


class TestPracticeSetupLab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name)
        self.tm = TrackModel(storage_path=p / "t.json")
        self.journal = SetupJournal(path=p / "j.json")
        self.pm = PracticeSessionManager(journal=self.journal)
        self.pm.handling = HandlingAnalyzer(track_model=self.tm)
        self.base = dataclasses.replace(
            make_dummy_snapshot(), session_state=SessionState.SESSION_PRACTICE, track_name="TestRing",
            track_variation="GP", track_length_m=LEN, car_name="MINI Cooper JCW")
        self.t = 1000.0

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, lap, dist, speed, steer, yaw, lat, brake, thr, lap_time, last_lap, pit=PitMode.PIT_MODE_NONE, fuel=30.0):
        self.t += 0.1
        s = dataclasses.replace(
            self.base, timestamp=self.t, current_lap=lap, current_lap_distance=dist, speed_kmh=speed,
            steering=steer, angular_velocity_rads=(0.0, yaw, 0.0), local_accel_ms2=(lat, 0.0, 0.0),
            brake=brake, throttle=thr, current_lap_time=lap_time, last_lap_time=last_lap, pit_mode=pit,
            fuel_liters=fuel, tyre_slip_speed_ms=(0.5, 0.5, 0.3, 0.3), tyre_rps=(30, 30, 30, 30),
            tyre_temps_inner_c=(92, 90, 80, 80), tyre_temps_center_c=(88, 86, 78, 78),
            tyre_temps_outer_c=(84, 82, 76, 76), tyre_pressures_bar=(1.85, 1.85, 1.80, 1.80),
            ride_height_cm=(4.0, 4.0, 5.0, 5.0),
        )
        self.tm.on_telemetry(s)
        self.pm.on_telemetry(s)

    def drive_run(self, start_lap, laps, mid_us, lap_time=90.0):
        # leave the box
        self.feed(start_lap, 10.0, 0.0, 0, 0, 0, 0, 0, 0.0, 0.0, pit=PitMode.PIT_MODE_IN_PIT)
        last = 0.0
        fuel = 30.0
        for k in range(laps + 1):  # out-lap + flying laps
            lap = start_lap + k
            prof = lap_profile(mid_us)
            n = len(prof)
            for i, (d, sp, st, yw, lt, br, th) in enumerate(prof):
                # gentle low-g weave on the straight (teaches the neutral response)
                if 2600 < d < 2900:
                    sp, r = 110.0, 300.0
                    v = sp / 3.6
                    yw, lt, st, br, th = v / r, v * v / r, 0.25 * (60.0 / r), 0.0, 0.5
                fuel -= 3.0 / n
                self.feed(lap, d, sp, st, yw, lt, br, th, lap_time * i / n, last, fuel=fuel)
            last = lap_time - 0.1 * k
        # back in
        self.feed(start_lap + laps + 1, 5.0, 40.0, 0, 0, 0, 0, 0, 1.0, last, pit=PitMode.PIT_MODE_DRIVING_INTO_PITS)

    def test_run_log_and_understeer_signature(self):
        self.journal.add_car_fact("MINI Cooper JCW", "Camber and anti-roll bars are fixed on this car")
        self.drive_run(1, 4, mid_us=0.45)
        self.assertEqual(len(self.pm.completed_runs), 1)
        agg = self.pm.completed_runs[0].handling
        self.assertIn("ui", agg)
        ui = agg["ui"]
        self.assertIn("mid", ui)
        self.assertGreater(ui["mid"], ui["entry"] + 0.1, f"mid-corner push should stand out: {ui}")
        self.assertTrue(self.tm.info.corners, "corners should be learned from the first clean lap")

        # driver makes a change, second run with less push
        self.journal.add_change(self.pm.journal_key, "Front cold pressures -0.05 bar", self.pm.current_run_number)
        self.drive_run(7, 4, mid_us=0.15)
        text = self.pm.get_comparative_debrief()
        self.assertIn("[CAR KNOWLEDGE", text)
        self.assertIn("Camber and anti-roll bars are fixed", text)
        self.assertIn("SETUP LAB RUN LOG", text)
        self.assertIn("change before this run: Front cold pressures -0.05 bar", text)
        self.assertIn("Understeer index by phase", text)
        self.assertIn("COMPARISON VS RUN #1", text)
        self.assertIn("Understeer index change", text)
        agg2 = self.pm.completed_runs[1].handling
        self.assertLess(agg2["ui"]["mid"], ui["mid"])


if __name__ == "__main__":
    unittest.main()
