"""
Unit tests for RaceStateManager pit events:
- Race Leader Pitted event with 15-minute cooldown
- Competitor Pit Exit Proximity event with 5-minute cooldown and lap-down awareness
- Strategy Engine integration with PitStopTracker learned pit loss
"""

import unittest
import tempfile
from pathlib import Path

from race.state import RaceStateManager
from race.events import EventType, EventPriority, RaceEvent
from race.pit_stop_tracker import PitStopTracker
from telemetry.collector import TelemetrySnapshot, ParticipantSnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode


def make_snapshot(
    player_pos: int = 2,
    player_lap: int = 5,
    player_dist: float = 100.0,
    speed_kmh: float = 180.0,
    participants: tuple = (),
    timestamp: float = 1000.0,
    track_name: str = "Interlagos",
    track_variation: str = "Grand Prix",
    track_length_m: float = 4000.0
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        timestamp=timestamp,
        game_state=GameState.GAME_INGAME_PLAYING,
        session_state=SessionState.SESSION_RACE,
        race_state=RaceState.RACESTATE_RACING,
        car_name="Porsche 911 GT3 R",
        car_class_name="GT3",
        speed_kmh=speed_kmh,
        rpm=6500.0,
        max_rpm=8500.0,
        gear=4,
        throttle=0.9,
        brake=0.0,
        steering=0.0,
        clutch=0.0,
        track_name=track_name,
        track_variation=track_variation,
        track_length_m=track_length_m,
        num_sectors=3,
        total_laps=20,
        lap_invalidated=False,
        player_name="PlayerDriver",
        position=player_pos,
        current_lap=player_lap,
        laps_completed=player_lap - 1,
        current_sector=1,
        current_lap_distance=player_dist,
        current_lap_time=30.0,
        last_lap_time=90.0,
        best_lap_time=90.0,
        split_time_ahead=2.0,
        split_time_behind=3.0,
        sector1_time=30.0,
        sector2_time=30.0,
        sector3_time=30.0,
        best_sector1_time=29.0,
        best_sector2_time=29.0,
        best_sector3_time=29.0,
        fuel_level_pct=60.0,
        fuel_liters=50.0,
        fuel_capacity_liters=90.0,
        water_temp_c=90.0,
        oil_temp_c=95.0,
        oil_pressure_kpa=400.0,
        tyre_compound="Dry",
        tyre_tread_temps_c=(85.0, 85.0, 85.0, 85.0),
        tyre_carcass_temps_c=(85.0, 85.0, 85.0, 85.0),
        tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
        tyre_wear_pct=(10.0, 10.0, 10.0, 10.0),
        brake_temps_c=(400.0, 400.0, 400.0, 400.0),
        ambient_temp_c=22.0,
        track_temp_c=32.0,
        rain_density=0.0,
        wind_speed_ms=2.0,
        flag_colour=FlagColour.FLAG_GREEN,
        pit_mode=PitMode.PIT_MODE_NONE,
        aero_damage=0.0,
        engine_damage=0.0,
        collision_flag=False,
        collision_magnitude=0.0,
        drs_state=0,
        ers_mode=0,
        participants=participants
    )


class TestPitEvents(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "test_pit_times.json"
        self.tracker = PitStopTracker(storage_path=self.storage_path, default_pit_loss_sec=45.0)

        self.mgr = RaceStateManager(
            verbosity="balanced",
            estimated_pit_loss_sec=45.0,
            pit_stop_tracker_inst=self.tracker
        )
        self.captured_events = []
        self.mgr.register_event_listener(lambda ev: self.captured_events.append(ev))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_leader_pitted_event_detection(self):
        """Verifies LEADER_PITTED event is emitted when P1 enters the pits."""
        # Baseline snapshot on track
        p1 = ParticipantSnapshot(name="Max Verstappen", position=1, lap=5, sector=1, lap_distance=300.0, is_active=True, pit_mode=0)
        p2 = ParticipantSnapshot(name="PlayerDriver", position=2, lap=5, sector=1, lap_distance=200.0, is_active=True, pit_mode=0)

        snap1 = make_snapshot(player_pos=2, player_lap=5, participants=(p1, p2), timestamp=1000.0)
        self.mgr.on_telemetry(snap1)
        self.assertEqual(len([e for e in self.captured_events if e.event_type == EventType.LEADER_PITTED]), 0)

        # Leader P1 enters pit lane (pit_mode = 1)
        p1_in_pit = ParticipantSnapshot(name="Max Verstappen", position=1, lap=5, sector=1, lap_distance=350.0, is_active=True, pit_mode=1)
        snap2 = make_snapshot(player_pos=2, player_lap=5, participants=(p1_in_pit, p2), timestamp=1001.0)
        self.mgr.on_telemetry(snap2)

        leader_events = [e for e in self.captured_events if e.event_type == EventType.LEADER_PITTED]
        self.assertEqual(len(leader_events), 1)
        self.assertEqual(leader_events[0].details["driver"], "Max Verstappen")
        self.assertIn("Verstappen", leader_events[0].message)

    def test_leader_pitted_not_fired_for_player(self):
        """Verifies LEADER_PITTED does not fire if the player themselves is the leader."""
        p1 = ParticipantSnapshot(name="PlayerDriver", position=1, lap=5, sector=1, lap_distance=300.0, is_active=True, pit_mode=0)
        p2 = ParticipantSnapshot(name="Lewis Hamilton", position=2, lap=5, sector=1, lap_distance=200.0, is_active=True, pit_mode=0)

        snap1 = make_snapshot(player_pos=1, player_lap=5, participants=(p1, p2), timestamp=1000.0)
        self.mgr.on_telemetry(snap1)

        # Player pits (pit_mode = 1)
        snap2 = make_snapshot(player_pos=1, player_lap=5, participants=(p1, p2), timestamp=1001.0)
        self.mgr.on_telemetry(snap2)

        leader_events = [e for e in self.captured_events if e.event_type == EventType.LEADER_PITTED]
        self.assertEqual(len(leader_events), 0)

    def test_leader_pitted_cooldown(self):
        """Verifies LEADER_PITTED respects 15-minute (900s) cooldown."""
        p1_track = ParticipantSnapshot(name="Max Verstappen", position=1, lap=5, sector=1, lap_distance=300.0, is_active=True, pit_mode=0)
        p2 = ParticipantSnapshot(name="PlayerDriver", position=2, lap=5, sector=1, lap_distance=200.0, is_active=True, pit_mode=0)
        self.mgr.on_telemetry(make_snapshot(player_pos=2, participants=(p1_track, p2), timestamp=1000.0))

        # First pit stop
        p1_pit = ParticipantSnapshot(name="Max Verstappen", position=1, lap=5, sector=1, lap_distance=350.0, is_active=True, pit_mode=1)
        self.mgr.on_telemetry(make_snapshot(player_pos=2, participants=(p1_pit, p2), timestamp=1001.0))
        self.assertEqual(len([e for e in self.captured_events if e.event_type == EventType.LEADER_PITTED]), 1)

        # Leaves pits
        self.mgr.on_telemetry(make_snapshot(player_pos=2, participants=(p1_track, p2), timestamp=1050.0))

        # Pits again immediately -> blocked by 900s cooldown
        self.mgr.on_telemetry(make_snapshot(player_pos=2, participants=(p1_pit, p2), timestamp=1100.0))
        self.assertEqual(len([e for e in self.captured_events if e.event_type == EventType.LEADER_PITTED]), 1)

        # Fast-forward cooldown by setting last emitted time 901s in the past
        import time
        self.mgr._event_cooldowns[EventType.LEADER_PITTED] = time.time() - 901.0
        self.mgr._leader_pit_state["max verstappen"] = False

        self.mgr.on_telemetry(make_snapshot(player_pos=2, participants=(p1_pit, p2), timestamp=2000.0))
        self.assertEqual(len([e for e in self.captured_events if e.event_type == EventType.LEADER_PITTED]), 2)

    def test_competitor_pit_exit_proximity_same_lap(self):
        """Verifies PIT_EXIT_PROXIMITY fires when a competitor exits within 3.0s on the same lap."""
        # Baseline snapshot on track
        p_track = ParticipantSnapshot(name="Lando Norris", position=4, lap=5, sector=1, lap_distance=150.0, is_active=True, pit_mode=2)
        p_player = ParticipantSnapshot(name="PlayerDriver", position=2, lap=5, sector=1, lap_distance=90.0, is_active=True, pit_mode=0)
        self.mgr.on_telemetry(make_snapshot(player_pos=2, player_lap=5, player_dist=90.0, participants=(p_track, p_player), timestamp=1000.0))

        # Track length = 4000m, lap pace = 90.0s -> avg speed = 44.44 m/s.
        # 3 seconds = ~133.3m.
        # Competitor is at lap_distance = 150m driving out (pit_mode = 3).
        # Player is at lap_distance = 100m approaching (50m behind -> ~1.12s gap).
        p_exit = ParticipantSnapshot(name="Lando Norris", position=4, lap=5, sector=1, lap_distance=150.0, is_active=True, pit_mode=3)
        p_player2 = ParticipantSnapshot(name="PlayerDriver", position=2, lap=5, sector=1, lap_distance=100.0, is_active=True, pit_mode=0)

        snap = make_snapshot(player_pos=2, player_lap=5, player_dist=100.0, participants=(p_exit, p_player2), timestamp=1001.0)
        self.mgr.on_telemetry(snap)

        proximity_events = [e for e in self.captured_events if e.event_type == EventType.PIT_EXIT_PROXIMITY]
        self.assertEqual(len(proximity_events), 1)
        ev = proximity_events[0]
        self.assertEqual(ev.details["driver"], "Lando Norris")
        self.assertFalse(ev.details["is_lap_down"])
        self.assertIn("Norris", ev.message)
        self.assertIn("same lap", ev.message)
        self.assertLessEqual(ev.details["time_gap_sec"], 3.0)

    def test_competitor_pit_exit_proximity_lap_down(self):
        """Verifies PIT_EXIT_PROXIMITY identifies competitor as a lap down."""
        # Baseline snapshot
        p_track = ParticipantSnapshot(name="Lance Stroll", position=18, lap=7, sector=1, lap_distance=120.0, is_active=True, pit_mode=2)
        p_player = ParticipantSnapshot(name="PlayerDriver", position=2, lap=8, sector=1, lap_distance=90.0, is_active=True, pit_mode=0)
        self.mgr.on_telemetry(make_snapshot(player_pos=2, player_lap=8, player_dist=90.0, participants=(p_track, p_player), timestamp=1000.0))

        # Player is on lap 8, competitor is on lap 7 (1 lap down)
        p_exit = ParticipantSnapshot(name="Lance Stroll", position=18, lap=7, sector=1, lap_distance=120.0, is_active=True, pit_mode=3)
        p_player2 = ParticipantSnapshot(name="PlayerDriver", position=2, lap=8, sector=1, lap_distance=100.0, is_active=True, pit_mode=0)

        snap = make_snapshot(player_pos=2, player_lap=8, player_dist=100.0, participants=(p_exit, p_player2), timestamp=1001.0)
        self.mgr.on_telemetry(snap)

        proximity_events = [e for e in self.captured_events if e.event_type == EventType.PIT_EXIT_PROXIMITY]
        self.assertEqual(len(proximity_events), 1)
        ev = proximity_events[0]
        self.assertEqual(ev.details["driver"], "Lance Stroll")
        self.assertTrue(ev.details["is_lap_down"])
        self.assertEqual(ev.details["laps_behind"], 1)
        self.assertIn("lap down", ev.message)

    def test_competitor_pit_exit_far_away_not_triggered(self):
        """Verifies PIT_EXIT_PROXIMITY does not fire if the gap is greater than 3.0s."""
        # Baseline snapshot
        p_track = ParticipantSnapshot(name="Lando Norris", position=4, lap=5, sector=1, lap_distance=600.0, is_active=True, pit_mode=2)
        p_player = ParticipantSnapshot(name="PlayerDriver", position=2, lap=5, sector=1, lap_distance=90.0, is_active=True, pit_mode=0)
        self.mgr.on_telemetry(make_snapshot(player_pos=2, player_lap=5, player_dist=90.0, participants=(p_track, p_player), timestamp=1000.0))

        # 4000m track / 90s = 44.44 m/s. 3s = 133m.
        # Competitor is at lap_distance = 600m. Player is at 100m. Delta = 500m (~11.25s).
        p_exit = ParticipantSnapshot(name="Lando Norris", position=4, lap=5, sector=1, lap_distance=600.0, is_active=True, pit_mode=3)
        p_player2 = ParticipantSnapshot(name="PlayerDriver", position=2, lap=5, sector=1, lap_distance=100.0, is_active=True, pit_mode=0)

        snap = make_snapshot(player_pos=2, player_lap=5, player_dist=100.0, participants=(p_exit, p_player2), timestamp=1001.0)
        self.mgr.on_telemetry(snap)

        proximity_events = [e for e in self.captured_events if e.event_type == EventType.PIT_EXIT_PROXIMITY]
        self.assertEqual(len(proximity_events), 0)

    def test_strategy_uses_learned_pit_loss(self):
        """Verifies get_current_strategy() uses the learned pit stop time from PitStopTracker."""
        # Provide active competitors behind player so rejoin projection calculates
        p_player = ParticipantSnapshot(name="PlayerDriver", position=1, lap=5, sector=1, lap_distance=500.0, is_active=True, pit_mode=0)
        p_behind = ParticipantSnapshot(name="Charles Leclerc", position=2, lap=5, sector=1, lap_distance=100.0, is_active=True, pit_mode=0)

        self.mgr.current_snapshot = make_snapshot(player_pos=1, track_name="Interlagos", track_variation="Grand Prix", participants=(p_player, p_behind))
        strat_default = self.mgr.get_current_strategy()
        self.assertIn("pit loss 45s", strat_default.rejoin_summary)

        # Record learned pit stop: 29.2s
        self.tracker.record_measurement("Interlagos", "Grand Prix", 29.2)

        strat_learned = self.mgr.get_current_strategy()
        self.assertIn("pit loss 29s", strat_learned.rejoin_summary)


if __name__ == "__main__":
    unittest.main()
