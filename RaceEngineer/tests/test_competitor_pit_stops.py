"""
Unit tests for competitor pit stop tracking and pit-wall timing board display.
"""

import unittest
from telemetry.collector import (
    TelemetrySnapshot,
    ParticipantSnapshot,
    SessionState,
    GameState,
    RaceState,
    FlagColour,
    PitMode
)
from race.standings import CompetitorTimingBoard


class TestCompetitorPitStops(unittest.TestCase):

    def _make_snapshot(self, player_pit_mode=PitMode.PIT_MODE_NONE, competitor_pit_mode=0, lap=5, session=SessionState.SESSION_RACE, player_pos=2, participants=None):
        if participants is None:
            participants = (
                ParticipantSnapshot(
                    name="Max Verstappen",
                    position=1,
                    lap=lap,
                    sector=2,
                    lap_distance=2500.0,
                    is_active=True,
                    last_lap_time=91.0,
                    best_lap_time=90.5,
                    sector1_time=28.0,
                    sector2_time=34.0,
                    sector3_time=29.0,
                    pit_mode=competitor_pit_mode
                ),
                ParticipantSnapshot(
                    name="Daniel (Player)",
                    position=player_pos,
                    lap=lap,
                    sector=2,
                    lap_distance=2400.0,
                    is_active=True,
                    last_lap_time=91.5,
                    best_lap_time=90.8,
                    sector1_time=28.2,
                    sector2_time=34.1,
                    sector3_time=29.2,
                    pit_mode=0
                ),
                ParticipantSnapshot(
                    name="Charles Leclerc",
                    position=3,
                    lap=lap,
                    sector=2,
                    lap_distance=2200.0,
                    is_active=True,
                    last_lap_time=92.0,
                    best_lap_time=91.2,
                    sector1_time=28.5,
                    sector2_time=34.3,
                    sector3_time=29.2,
                    pit_mode=0
                )
            )

        return TelemetrySnapshot(
            timestamp=100.0,
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=session,
            race_state=RaceState.RACESTATE_RACING,
            car_name="Porsche 911 GT3",
            car_class_name="GT3",
            speed_kmh=200.0,
            rpm=7000.0,
            max_rpm=9000.0,
            gear=4,
            throttle=1.0,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Silverstone",
            track_variation="Grand Prix",
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=25,
            lap_invalidated=False,
            player_name="Daniel (Player)",
            position=player_pos,
            current_lap=lap,
            laps_completed=lap - 1,
            current_sector=2,
            current_lap_time=45.0,
            last_lap_time=91.5,
            best_lap_time=90.8,
            split_time_ahead=2.0,
            split_time_behind=3.0,
            sector1_time=28.2,
            sector2_time=34.1,
            sector3_time=29.2,
            best_sector1_time=28.0,
            best_sector2_time=34.0,
            best_sector3_time=29.0,
            fuel_level_pct=50.0,
            fuel_liters=35.0,
            fuel_capacity_liters=70.0,
            water_temp_c=85.0,
            oil_temp_c=95.0,
            oil_pressure_kpa=400.0,
            tyre_compound="Medium",
            tyre_tread_temps_c=(90.0, 91.0, 89.0, 90.0),
            tyre_carcass_temps_c=(88.0, 89.0, 87.0, 88.0),
            tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
            tyre_wear_pct=(15.0, 16.0, 14.0, 14.0),
            brake_temps_c=(400.0, 400.0, 350.0, 350.0),
            ambient_temp_c=22.0,
            track_temp_c=30.0,
            rain_density=0.0,
            wind_speed_ms=2.0,
            flag_colour=FlagColour.FLAG_NONE,
            pit_mode=player_pit_mode,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            event_time_remaining_sec=1500.0,
            session_duration_min=30,
            is_race_finished=False,
            participants=participants
        )

    def test_competitor_pit_stop_lifecycle(self):
        """Verifies competitor transitions: 0 stops -> IN PIT (1) -> 1 stop -> IN PIT (2) -> 2 stops."""
        board = CompetitorTimingBoard()

        # 1. First tick: Verstappen on track
        snap1 = self._make_snapshot(competitor_pit_mode=0, lap=5)
        entries1, table1 = board.analyze(snap1)
        v_entry1 = next(e for e in entries1 if e.name == "Max Verstappen")
        self.assertEqual(v_entry1.pit_stops, 0)
        self.assertFalse(v_entry1.in_pits)
        self.assertIn("0 stops", table1)
        self.assertIn("Cars Pitted: None yet", table1)

        # 2. Verstappen enters pits (Stop 1)
        snap2 = self._make_snapshot(competitor_pit_mode=2, lap=6)  # PIT_MODE_IN_PIT
        entries2, table2 = board.analyze(snap2)
        v_entry2 = next(e for e in entries2 if e.name == "Max Verstappen")
        self.assertEqual(v_entry2.pit_stops, 1)
        self.assertTrue(v_entry2.in_pits)
        self.assertIn("IN PIT (1)", table2)
        self.assertIn("P1 Verstappen (1 stop)", table2)

        # 3. Multiple ticks while in pit - should NOT double count
        snap3 = self._make_snapshot(competitor_pit_mode=2, lap=6)
        entries3, table3 = board.analyze(snap3)
        v_entry3 = next(e for e in entries3 if e.name == "Max Verstappen")
        self.assertEqual(v_entry3.pit_stops, 1)
        self.assertTrue(v_entry3.in_pits)

        # 4. Verstappen rejoins track after Stop 1
        snap4 = self._make_snapshot(competitor_pit_mode=0, lap=7)
        entries4, table4 = board.analyze(snap4)
        v_entry4 = next(e for e in entries4 if e.name == "Max Verstappen")
        self.assertEqual(v_entry4.pit_stops, 1)
        self.assertFalse(v_entry4.in_pits)
        self.assertIn("1 stop", table4)
        self.assertIn("Cars Pitted: P1 Verstappen (1 stop)", table4)

        # 5. Verstappen pits again on lap 18 (Stop 2)
        snap5 = self._make_snapshot(competitor_pit_mode=1, lap=18)  # PIT_MODE_DRIVING_INTO_PITS
        entries5, table5 = board.analyze(snap5)
        v_entry5 = next(e for e in entries5 if e.name == "Max Verstappen")
        self.assertEqual(v_entry5.pit_stops, 2)
        self.assertTrue(v_entry5.in_pits)
        self.assertIn("IN PIT (2)", table5)
        self.assertIn("P1 Verstappen (2 stops)", table5)

        # 6. Verstappen exits pits on lap 19
        snap6 = self._make_snapshot(competitor_pit_mode=0, lap=19)
        entries6, table6 = board.analyze(snap6)
        v_entry6 = next(e for e in entries6 if e.name == "Max Verstappen")
        self.assertEqual(v_entry6.pit_stops, 2)
        self.assertFalse(v_entry6.in_pits)
        self.assertIn("2 stops", table6)

    def test_player_pit_stop_tracking(self):
        """Verifies that the player's own pit stops are tracked as well."""
        board = CompetitorTimingBoard()

        # Player on track
        snap1 = self._make_snapshot(player_pit_mode=PitMode.PIT_MODE_NONE, lap=5)
        entries1, table1 = board.analyze(snap1)
        p_entry1 = next(e for e in entries1 if e.is_player)
        self.assertEqual(p_entry1.pit_stops, 0)
        self.assertFalse(p_entry1.in_pits)

        # Player enters pits
        snap2 = self._make_snapshot(player_pit_mode=PitMode.PIT_MODE_IN_PIT, lap=8)
        entries2, table2 = board.analyze(snap2)
        p_entry2 = next(e for e in entries2 if e.is_player)
        self.assertEqual(p_entry2.pit_stops, 1)
        self.assertTrue(p_entry2.in_pits)

        # Player leaves pits
        snap3 = self._make_snapshot(player_pit_mode=PitMode.PIT_MODE_NONE, lap=9)
        entries3, table3 = board.analyze(snap3)
        p_entry3 = next(e for e in entries3 if e.is_player)
        self.assertEqual(p_entry3.pit_stops, 1)
        self.assertFalse(p_entry3.in_pits)

    def test_board_reset(self):
        """Verifies that calling reset clears all pit stop counts."""
        board = CompetitorTimingBoard()

        # Simulate Verstappen pitting
        snap1 = self._make_snapshot(competitor_pit_mode=0, lap=5)
        board.analyze(snap1)
        snap2 = self._make_snapshot(competitor_pit_mode=2, lap=6)
        entries2, _ = board.analyze(snap2)
        self.assertEqual(entries2[0].pit_stops, 1)

        # Reset board
        board.reset()
        snap3 = self._make_snapshot(competitor_pit_mode=0, lap=1)
        entries3, table3 = board.analyze(snap3)
        self.assertEqual(entries3[0].pit_stops, 0)
        self.assertIn("Cars Pitted: None yet", table3)

    def test_expanded_grid_coverage_top5_and_surroundings(self):
        """Verifies that Top 5, 6 ahead, player, 6 behind, and last car are included in the board."""
        board = CompetitorTimingBoard()
        # Create a 24-car field with Player at P10
        participants = []
        for pos in range(1, 25):
            is_player = (pos == 10)
            participants.append(
                ParticipantSnapshot(
                    name="Daniel (Player)" if is_player else f"Driver {pos}",
                    position=pos,
                    lap=5,
                    sector=1,
                    lap_distance=1000.0 - (pos * 30),
                    is_active=True,
                    last_lap_time=90.0 + pos * 0.1,
                    best_lap_time=89.5 + pos * 0.1,
                    pit_mode=0
                )
            )

        snap = self._make_snapshot(player_pos=10, participants=tuple(participants))

        entries, table = board.analyze(snap)
        positions_in_board = [e.position for e in entries]

        # Top 5: 1, 2, 3, 4, 5
        for p in [1, 2, 3, 4, 5]:
            self.assertIn(p, positions_in_board)

        # 6 ahead of P10: 4, 5, 6, 7, 8, 9
        for p in [6, 7, 8, 9]:
            self.assertIn(p, positions_in_board)

        # Player: 10
        self.assertIn(10, positions_in_board)

        # 6 behind P10: 11, 12, 13, 14, 15, 16
        for p in [11, 12, 13, 14, 15, 16]:
            self.assertIn(p, positions_in_board)

        # Last car: 24
        self.assertIn(24, positions_in_board)


if __name__ == "__main__":
    unittest.main()
