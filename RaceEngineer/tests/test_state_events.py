"""
Unit test for RaceStateManager event transitions including track arrival.
"""

import unittest
import time
from race.state import RaceStateManager
from race.events import EventType, RaceEvent
from telemetry.collector import TelemetrySnapshot, ParticipantSnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode


class TestStateEvents(unittest.TestCase):
    def make_snapshot(self, lap=1, pos=3, flag=FlagColour.FLAG_GREEN, track="Interlagos", variation="GP"):
        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_RACING,
            car_name="Porsche 911 GT3",
            car_class_name="GT3",
            speed_kmh=210.0,
            rpm=7500.0,
            max_rpm=8500.0,
            gear=4,
            throttle=1.0,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name=track,
            track_variation=variation,
            track_length_m=4309.0,
            num_sectors=3,
            total_laps=20,
            lap_invalidated=False,
            player_name="Driver",
            position=pos,
            current_lap=lap,
            laps_completed=lap - 1,
            current_sector=1,
            current_lap_time=15.0,
            last_lap_time=91.4,
            best_lap_time=91.2,
            split_time_ahead=1.5,
            split_time_behind=3.0,
            sector1_time=30.0,
            sector2_time=30.0,
            sector3_time=31.4,
            best_sector1_time=30.0,
            best_sector2_time=30.0,
            best_sector3_time=31.2,
            fuel_level_pct=70.0,
            fuel_liters=45.0,
            fuel_capacity_liters=65.0,
            water_temp_c=90.0,
            oil_temp_c=100.0,
            oil_pressure_kpa=400.0,
            tyre_compound="Soft",
            tyre_tread_temps_c=(95.0, 95.0, 92.0, 92.0),
            tyre_carcass_temps_c=(92.0, 92.0, 90.0, 90.0),
            tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
            tyre_wear_pct=(15.0, 15.0, 12.0, 12.0),
            brake_temps_c=(400.0, 400.0, 380.0, 380.0),
            ambient_temp_c=24.0,
            track_temp_c=32.0,
            rain_density=0.0,
            wind_speed_ms=2.0,
            flag_colour=flag,
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            participants=()
        )

    def test_track_loaded_event(self):
        manager = RaceStateManager(verbosity="balanced")
        events = []
        manager.register_event_listener(lambda e: events.append(e))

        # First snapshot on track loading
        manager.on_telemetry(self.make_snapshot(lap=1, pos=3, track="Silverstone", variation="Grand Prix"))

        track_events = [e for e in events if e.event_type == EventType.TRACK_LOADED]
        self.assertEqual(len(track_events), 1)
        self.assertIn("Silverstone", track_events[0].message)
        self.assertIn("rundown", track_events[0].message.lower())

    def test_lap_completion_and_position_events(self):
        manager = RaceStateManager(verbosity="balanced")
        events_received = []
        manager.register_event_listener(lambda e: events_received.append(e))

        # First snapshot sets baseline (and emits TRACK_LOADED)
        manager.on_telemetry(self.make_snapshot(lap=1, pos=3))
        self.assertEqual(events_received[0].event_type, EventType.TRACK_LOADED)

        # Lap completion -> Lap 2
        manager.on_telemetry(self.make_snapshot(lap=2, pos=3))
        lap_events = [e for e in events_received if e.event_type == EventType.LAP_COMPLETED]
        self.assertEqual(len(lap_events), 1)

        # Position gain -> P2
        manager.on_telemetry(self.make_snapshot(lap=2, pos=2))
        pos_events = [e for e in events_received if e.event_type == EventType.POSITION_GAINED]
        self.assertEqual(len(pos_events), 1)
        self.assertEqual(pos_events[0].details["new_pos"], 2)


if __name__ == "__main__":
    unittest.main()
