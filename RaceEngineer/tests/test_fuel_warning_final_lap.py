"""
Unit tests for final lap fuel warnings, urgency determination, and single-shot alert gating.
"""

import unittest
from race.state import RaceStateManager
from race.events import EventType, EventPriority
from telemetry.collector import (
    TelemetrySnapshot,
    SessionState,
    RaceState,
    GameState,
    FlagColour,
    PitMode,
)


class TestFinalLapFuelWarning(unittest.TestCase):
    def setUp(self):
        self.manager = RaceStateManager()
        self.fired_events = []
        self.manager.register_event_listener(lambda ev: self.fired_events.append(ev))
        # Feed baseline snapshot to initialize session and avoid first-frame baseline return
        base_snap = self.make_snapshot(lap=1, total_laps=10, fuel=50.0)
        self.manager.on_telemetry(base_snap)
        self.fired_events.clear()
        self.manager.fuel_tracker._lap_consumptions = [3.0, 3.0, 3.0]

    def make_snapshot(self, lap: int, total_laps: int, fuel: float, pit_mode: PitMode = PitMode.PIT_MODE_NONE) -> TelemetrySnapshot:
        import time
        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_RACING,
            car_name="Porsche 911 GT3 R",
            car_class_name="GT3",
            speed_kmh=180.0,
            rpm=6000.0,
            max_rpm=8500.0,
            gear=4,
            throttle=0.8,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Interlagos",
            track_variation="Grand Prix",
            track_length_m=4309.0,
            num_sectors=3,
            total_laps=total_laps,
            lap_invalidated=False,
            player_name="Driver",
            position=1,
            current_lap=lap,
            laps_completed=max(0, lap - 1),
            current_sector=2,
            current_lap_time=45.0,
            last_lap_time=90.0,
            best_lap_time=90.0,
            split_time_ahead=0.0,
            split_time_behind=5.0,
            sector1_time=30.0,
            sector2_time=30.0,
            sector3_time=30.0,
            best_sector1_time=30.0,
            best_sector2_time=30.0,
            best_sector3_time=30.0,
            fuel_level_pct=fuel,
            fuel_liters=fuel,
            fuel_capacity_liters=60.0,
            water_temp_c=90.0,
            oil_temp_c=95.0,
            oil_pressure_kpa=450.0,
            tyre_compound="Soft",
            tyre_tread_temps_c=(90.0, 90.0, 90.0, 90.0),
            tyre_carcass_temps_c=(88.0, 88.0, 88.0, 88.0),
            tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
            tyre_wear_pct=(20.0, 20.0, 20.0, 20.0),
            brake_temps_c=(300.0, 300.0, 300.0, 300.0),
            ambient_temp_c=20.0,
            track_temp_c=25.0,
            rain_density=0.0,
            wind_speed_ms=1.0,
            flag_colour=FlagColour.FLAG_NONE,
            pit_mode=pit_mode,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0
        )

    def test_final_lap_single_warning_reassuring_when_safe(self):
        """
        On final lap (lap 10 of 10), fuel drops to 3.3L (1.1 laps at 3.0L/lap).
        The driver WILL finish the race before running out.
        Verify:
        1. Only ONE warning event is emitted across multiple frames.
        2. The event is non-urgent (urgent == False).
        3. The message tells the driver fuel is safe to the finish and they will be fine.
        """
        # Feed frame on final lap where fuel is 3.3L (1.1 laps)
        snap1 = self.make_snapshot(lap=10, total_laps=10, fuel=3.3)
        self.manager.on_telemetry(snap1)

        # Feed multiple subsequent frames on the final lap as fuel slowly drops
        snap2 = self.make_snapshot(lap=10, total_laps=10, fuel=3.2)
        self.manager.on_telemetry(snap2)
        snap3 = self.make_snapshot(lap=10, total_laps=10, fuel=3.1)
        self.manager.on_telemetry(snap3)

        fuel_events = [e for e in self.fired_events if e.event_type in (EventType.FUEL_CRITICAL, EventType.FUEL_EMERGENCY)]
        # Must be exactly ONE warning (no repetition / spam!)
        self.assertEqual(len(fuel_events), 1)

        event = fuel_events[0]
        self.assertEqual(event.event_type, EventType.FUEL_CRITICAL)
        self.assertFalse(event.details.get("urgent"))
        self.assertIn("safe to the finish", event.message)
        self.assertIn("no stop needed", event.message)
        self.assertNotIn("Box immediately", event.message)

    def test_single_warning_urgent_when_cannot_finish(self):
        """
        Driver has 1 lap of fuel left, but 3 laps remaining in race (lap 8 of 10).
        Cannot make the finish without pitting.
        Verify:
        1. Exactly ONE fuel emergency warning is emitted across frames.
        2. The event is urgent (urgent == True).
        3. The message demands the driver box immediately.
        """
        snap1 = self.make_snapshot(lap=8, total_laps=10, fuel=3.0)
        self.manager.on_telemetry(snap1)

        snap2 = self.make_snapshot(lap=8, total_laps=10, fuel=2.9)
        self.manager.on_telemetry(snap2)

        fuel_events = [e for e in self.fired_events if e.event_type in (EventType.FUEL_CRITICAL, EventType.FUEL_EMERGENCY)]
        self.assertEqual(len(fuel_events), 1)

        event = fuel_events[0]
        self.assertEqual(event.event_type, EventType.FUEL_EMERGENCY)
        self.assertTrue(event.details.get("urgent"))
        self.assertIn("laps to the flag", event.message)
        self.assertIn("Box immediately", event.message)

    def test_refuel_resets_fuel_warning_flag(self):
        """
        Verify that pitting and adding fuel (>5L) resets the single-warning gate,
        so a subsequent stint can receive a warning if fuel drops low again.
        """
        # First stint: drops low, triggers warning
        snap1 = self.make_snapshot(lap=8, total_laps=20, fuel=3.0)
        self.manager.on_telemetry(snap1)
        self.assertEqual(len([e for e in self.fired_events if e.event_type == EventType.FUEL_EMERGENCY]), 1)

        # Car pits and refuels to 40.0L (+37L)
        snap_pit = self.make_snapshot(lap=9, total_laps=20, fuel=40.0, pit_mode=PitMode.PIT_MODE_IN_PIT)
        self.manager.on_telemetry(snap_pit)

        # Later in stint 2: drops low again at lap 18
        snap_stint2 = self.make_snapshot(lap=18, total_laps=20, fuel=3.0)
        self.manager.on_telemetry(snap_stint2)

        emergency_events = [e for e in self.fired_events if e.event_type == EventType.FUEL_EMERGENCY]
        self.assertEqual(len(emergency_events), 2)


if __name__ == "__main__":
    unittest.main()
