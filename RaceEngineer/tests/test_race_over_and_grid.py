"""
Unit tests verifying Race Over awareness, Full Grid awareness (last place),
and Tyre Life degradation thresholds.
"""

import unittest
from telemetry.collector import TelemetrySnapshot, SessionState, FlagColour, PitMode, GameState, RaceState
from race.standings import CompetitorTimingBoard, ParticipantSnapshot
from race.tires import TireTracker
from race.fuel import FuelStatus
from race.gaps import GapAnalysis
from engineer.context import build_telemetry_context


class TestRaceOverAndGrid(unittest.TestCase):

    def test_race_finished_context(self):
        """Checks that when the race is finished, context explicitly states 0 laps remaining."""
        snap = TelemetrySnapshot(
            timestamp=1000.0,
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_FINISHED,
            car_name="Porsche 911 GT3 R",
            car_class_name="GT3",
            speed_kmh=80.0,
            rpm=4000.0,
            max_rpm=9000.0,
            gear=3,
            throttle=0.2,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Silverstone",
            track_variation="Grand Prix",
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=15,
            lap_invalidated=False,
            player_name="Daniel",
            position=1,
            current_lap=15,
            laps_completed=15,
            current_sector=3,
            current_lap_time=118.5,
            last_lap_time=118.5,
            best_lap_time=117.9,
            split_time_ahead=0.0,
            split_time_behind=4.2,
            sector1_time=32.1,
            sector2_time=42.3,
            sector3_time=43.5,
            best_sector1_time=31.9,
            best_sector2_time=42.0,
            best_sector3_time=43.2,
            fuel_level_pct=12.0,
            fuel_liters=12.0,
            fuel_capacity_liters=100.0,
            water_temp_c=88.0,
            oil_temp_c=98.0,
            oil_pressure_kpa=450.0,
            tyre_compound="Soft",
            tyre_tread_temps_c=(92.0, 93.0, 90.0, 91.0),
            tyre_carcass_temps_c=(90.0, 91.0, 88.0, 89.0),
            tyre_pressures_bar=(1.82, 1.83, 1.78, 1.79),
            tyre_wear_pct=(24.0, 26.0, 20.0, 22.0),
            brake_temps_c=(450.0, 455.0, 410.0, 415.0),
            ambient_temp_c=22.0,
            track_temp_c=28.0,
            rain_density=0.0,
            wind_speed_ms=2.0,
            flag_colour=FlagColour.FLAG_CHEQUERED,
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            event_time_remaining_sec=0.0,
            session_duration_min=30,
            is_race_finished=True,
            participants=()
        )

        fuel = FuelStatus(12.0, 100.0, 12.0, 3.2, 3.75, 0, 0.0, None, False, False, False)
        tracker = TireTracker()
        tires = tracker.analyze("Soft", (92.0, 93.0, 90.0, 91.0), (1.82, 1.83, 1.78, 1.79), (24.0, 26.0, 20.0, 22.0))
        gaps = GapAnalysis(None, None, None, None, False, None)

        context = build_telemetry_context(snap, fuel, tires, gaps)

        self.assertIn("RACE FINISHED / CHEQUERED FLAG TAKEN (THE RACE IS OVER)", context)
        self.assertIn("0 laps remaining", context)
        self.assertIn("You finished in P1!", context)
        self.assertIn("Cool down on the in-lap", context)

    def test_full_grid_and_last_place(self):
        """Checks that all cars on the grid are accounted for and last place driver is exposed."""
        participants = []
        for i in range(1, 13):
            participants.append(ParticipantSnapshot(
                name=f"Driver_{i}",
                position=i,
                lap=5,
                sector=1,
                lap_distance=500.0 * i,
                is_active=True,
                last_lap_time=120.0 + i * 0.5,
                best_lap_time=119.0 + i * 0.5,
                sector1_time=32.0,
                sector2_time=42.0,
                sector3_time=44.0,
                pit_mode=0
            ))

        snap = TelemetrySnapshot(
            timestamp=1000.0,
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_RACING,
            car_name="Porsche 911 GT3 R",
            car_class_name="GT3",
            speed_kmh=220.0,
            rpm=7500.0,
            max_rpm=9000.0,
            gear=5,
            throttle=1.0,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Spa",
            track_variation="Grand Prix",
            track_length_m=7004.0,
            num_sectors=3,
            total_laps=15,
            lap_invalidated=False,
            player_name="Driver_2",
            position=2,
            current_lap=5,
            laps_completed=4,
            current_sector=1,
            current_lap_time=32.0,
            last_lap_time=121.0,
            best_lap_time=120.0,
            split_time_ahead=1.5,
            split_time_behind=2.0,
            sector1_time=32.0,
            sector2_time=42.0,
            sector3_time=44.0,
            best_sector1_time=31.8,
            best_sector2_time=41.9,
            best_sector3_time=43.5,
            fuel_level_pct=45.0,
            fuel_liters=45.0,
            fuel_capacity_liters=100.0,
            water_temp_c=85.0,
            oil_temp_c=95.0,
            oil_pressure_kpa=450.0,
            tyre_compound="Medium",
            tyre_tread_temps_c=(90.0, 90.0, 90.0, 90.0),
            tyre_carcass_temps_c=(88.0, 88.0, 88.0, 88.0),
            tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
            tyre_wear_pct=(10.0, 10.0, 10.0, 10.0),
            brake_temps_c=(400.0, 400.0, 400.0, 400.0),
            ambient_temp_c=20.0,
            track_temp_c=25.0,
            rain_density=0.0,
            wind_speed_ms=1.0,
            flag_colour=FlagColour.FLAG_NONE,
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            event_time_remaining_sec=1200.0,
            session_duration_min=30,
            is_race_finished=False,
            participants=tuple(participants)
        )

        board = CompetitorTimingBoard()
        entries, table = board.analyze(snap)

        self.assertIn("12 cars active in session", table)
        self.assertIn("Leader: P1 Driver_1", table)
        self.assertIn("Last Place: P12 Driver_12", table)
        self.assertIn("P12L", table)

    def test_tire_life_thresholds(self):
        """Checks tyre life calculation, 75% 'gone off' threshold, and 50% 'very worn' cliff."""
        tracker = TireTracker()

        # Fresh tyres (10% wear -> 90% life)
        status_fresh = tracker.analyze("Hard", (90, 90, 90, 90), (1.8, 1.8, 1.8, 1.8), (10, 10, 10, 10))
        self.assertFalse(status_fresh.is_gone_off)
        self.assertFalse(status_fresh.is_very_worn)
        self.assertEqual(status_fresh.life_pct, (90.0, 90.0, 90.0, 90.0))

        # Tyres gone off (26% wear -> 74% life <= 75%)
        status_off = tracker.analyze("Hard", (90, 90, 90, 90), (1.8, 1.8, 1.8, 1.8), (26, 20, 20, 20))
        self.assertTrue(status_off.is_gone_off)
        self.assertFalse(status_off.is_very_worn)
        self.assertIn("TYRES GONE OFF", status_off.condition_summary)

        # Tyres very worn / cliff (52% wear -> 48% life <= 50%)
        status_worn = tracker.analyze("Hard", (90, 90, 90, 90), (1.8, 1.8, 1.8, 1.8), (52, 30, 30, 30))
        self.assertTrue(status_worn.is_gone_off)
        self.assertTrue(status_worn.is_very_worn)
        self.assertIn("VERY WORN / ON THE CLIFF", status_worn.condition_summary)

    def test_incident_storylines_across_full_race(self):
        """Verifies IncidentTracker retains full-session spins and compares against live recovery position."""
        from race.incidents import IncidentTracker
        tracker = IncidentTracker(speed_threshold_kmh=20.0, duration_sec=1.0, memory_retention_sec=60.0)

        p_lap2 = (
            ParticipantSnapshot(name="Lando Norris", position=2, lap=2, sector=2, lap_distance=1500.0, is_active=True, speed_kmh=5.0),
        )
        tracker.update(p_lap2, "Daniel", 100.0, player_lap_dist=1200.0, track_length_m=5000.0, player_speed_kmh=180.0)
        tracker.update(p_lap2, "Daniel", 101.5, player_lap_dist=1300.0, track_length_m=5000.0, player_speed_kmh=180.0)

        # Advance 5 minutes later (past the 60s hazard window) on Lap 6, where Norris is now P5 running 91.2s
        p_lap6 = (
            ParticipantSnapshot(name="Lando Norris", position=5, lap=6, sector=1, lap_distance=800.0, is_active=True, speed_kmh=210.0, last_lap_time=91.25),
        )
        tracker.update(p_lap6, "Daniel", 400.0, player_lap_dist=900.0, track_length_m=5000.0, player_speed_kmh=180.0)

        # 2-minute hazard summary is now clear, but full-race storyline retains the spin and recovery status
        self.assertIn("Track clear", tracker.get_recent_incidents_summary(400.0))
        story = tracker.get_race_storylines_summary(p_lap6, 400.0)
        self.assertIn("Lando Norris", story)
        self.assertIn("Lap 2", story)
        self.assertIn("while running P2 -> Now P5 (down 3 places since incident", story)
        self.assertIn("1:31.250", story)

    def test_leader_finished_event_and_proximity_suppression(self):
        """Verifies LEADER_FINISHED fires when player is far from line (>15s) and is suppressed when close (<=15s)."""
        from race.state import RaceStateManager
        from race.events import EventType

        emitted = []
        mgr = RaceStateManager()
        mgr.register_event_listener(lambda ev: emitted.append(ev))

        def make_snap(player_dist: float, leader_race_state: int, split_ahead: float = 25.0):
            parts = (
                ParticipantSnapshot(
                    name="Max Verstappen",
                    position=1,
                    lap=10,
                    laps_completed=10 if leader_race_state == 3 else 9,
                    sector=3,
                    lap_distance=100.0,
                    is_active=True,
                    race_state=leader_race_state,
                    flag_colour=11 if leader_race_state == 3 else 1
                ),
                ParticipantSnapshot(
                    name="Daniel",
                    position=2,
                    lap=10,
                    laps_completed=9,
                    sector=2,
                    lap_distance=player_dist,
                    is_active=True,
                    race_state=2,
                    flag_colour=1
                ),
            )
            return TelemetrySnapshot(
                timestamp=1000.0,
                game_state=GameState.GAME_INGAME_PLAYING,
                session_state=SessionState.SESSION_RACE,
                race_state=RaceState.RACESTATE_RACING,
                car_name="Porsche 911 GT3 R",
                car_class_name="GT3",
                speed_kmh=180.0,  # 50 m/s
                rpm=7000.0,
                max_rpm=9000.0,
                gear=4,
                throttle=1.0,
                brake=0.0,
                steering=0.0,
                clutch=0.0,
                track_name="Silverstone",
                track_variation="GP",
                track_length_m=5000.0,
                num_sectors=3,
                total_laps=10,
                lap_invalidated=False,
                player_name="Daniel",
                position=2,
                current_lap=10,
                laps_completed=9,
                current_sector=2,
                current_lap_distance=player_dist,
                current_lap_time=50.0,
                last_lap_time=100.0,
                best_lap_time=100.0,
                split_time_ahead=split_ahead,
                split_time_behind=4.0,
                sector1_time=30.0,
                sector2_time=35.0,
                sector3_time=35.0,
                best_sector1_time=30.0,
                best_sector2_time=35.0,
                best_sector3_time=35.0,
                fuel_level_pct=20.0,
                fuel_liters=20.0,
                fuel_capacity_liters=100.0,
                water_temp_c=85.0,
                oil_temp_c=95.0,
                oil_pressure_kpa=450.0,
                tyre_compound="Soft",
                tyre_tread_temps_c=(90.0, 90.0, 90.0, 90.0),
                tyre_carcass_temps_c=(88.0, 88.0, 88.0, 88.0),
                tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
                tyre_wear_pct=(20.0, 20.0, 20.0, 20.0),
                brake_temps_c=(400.0, 400.0, 400.0, 400.0),
                ambient_temp_c=20.0,
                track_temp_c=25.0,
                rain_density=0.0,
                wind_speed_ms=1.0,
                flag_colour=FlagColour.FLAG_GREEN,
                pit_mode=PitMode.PIT_MODE_NONE,
                aero_damage=0.0,
                engine_damage=0.0,
                collision_flag=False,
                collision_magnitude=0.0,
                drs_state=0,
                ers_mode=0,
                is_race_finished=False,
                participants=parts
            )

        # Case 1: Player is 2500m from the finish line (~50s away at 50m/s) -> should emit LEADER_FINISHED
        mgr.on_telemetry(make_snap(2400.0, leader_race_state=2))
        mgr.on_telemetry(make_snap(2500.0, leader_race_state=3, split_ahead=50.0))
        leader_events = [e for e in emitted if e.event_type == EventType.LEADER_FINISHED]
        self.assertEqual(len(leader_events), 1)
        self.assertIn("Verstappen", leader_events[0].message)

        # Case 2: Player is 4600m into a 5000m lap (400m from line = 8s away <= 15s) -> should suppress LEADER_FINISHED
        emitted.clear()
        mgr.reset_session(SessionState.SESSION_RACE)
        mgr.on_telemetry(make_snap(4500.0, leader_race_state=2, split_ahead=8.0))
        mgr.on_telemetry(make_snap(4600.0, leader_race_state=3, split_ahead=8.0))
        leader_events_close = [e for e in emitted if e.event_type == EventType.LEADER_FINISHED]
        self.assertEqual(len(leader_events_close), 0)


if __name__ == "__main__":
    unittest.main()
