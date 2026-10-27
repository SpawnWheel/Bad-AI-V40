"""
Unit tests for Unified Session Lifecycle Management:
- Start-at-any-time standby behavior
- Session group classifications
- Countdown -> Formation Lap -> Green Flag Race -> Cooldown Lap continuity (no resets, unified session)
- Inter-session resets (Practice -> Quali -> Race)
- In-game session restart detection
- Front-end menu exit and re-entry
- Brain and scheduler cleanup on session change
"""

import unittest
import time
from race.state import RaceStateManager, get_session_group
from race.events import EventType, RaceEvent
from telemetry.collector import TelemetrySnapshot
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from engineer.context import build_telemetry_context
from engineer.brain import RaceEngineerBrain
from engineer.scheduler import MessageScheduler
from race.fuel import FuelStatus
from race.tires import TireStatus
from race.gaps import GapAnalysis


class TestSessionLifecycle(unittest.TestCase):

    def make_snapshot(
        self,
        session_state: SessionState = SessionState.SESSION_RACE,
        race_state: RaceState = RaceState.RACESTATE_RACING,
        game_state: GameState = GameState.GAME_INGAME_PLAYING,
        lap: int = 1,
        laps_completed: int = 0,
        fuel_liters: float = 40.0,
        track: str = "Silverstone",
        variation: str = "GP",
        car: str = "Porsche 911 GT3"
    ) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            timestamp=time.time(),
            game_state=game_state,
            session_state=session_state,
            race_state=race_state,
            car_name=car,
            car_class_name="GT3",
            speed_kmh=180.0,
            rpm=7000.0,
            max_rpm=8500.0,
            gear=3,
            throttle=0.8,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name=track,
            track_variation=variation,
            track_length_m=5891.0,
            num_sectors=3,
            total_laps=15,
            lap_invalidated=False,
            player_name="Driver",
            position=3,
            current_lap=lap,
            laps_completed=laps_completed,
            current_sector=1,
            current_lap_time=20.0,
            last_lap_time=98.0,
            best_lap_time=97.5,
            split_time_ahead=1.5,
            split_time_behind=2.0,
            sector1_time=32.0,
            sector2_time=33.0,
            sector3_time=33.0,
            best_sector1_time=31.8,
            best_sector2_time=32.8,
            best_sector3_time=32.9,
            fuel_level_pct=60.0,
            fuel_liters=fuel_liters,
            fuel_capacity_liters=65.0,
            water_temp_c=90.0,
            oil_temp_c=100.0,
            oil_pressure_kpa=400.0,
            tyre_compound="Soft",
            tyre_tread_temps_c=(95.0, 95.0, 92.0, 92.0),
            tyre_carcass_temps_c=(92.0, 92.0, 90.0, 90.0),
            tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
            tyre_wear_pct=(10.0, 10.0, 8.0, 8.0),
            brake_temps_c=(400.0, 400.0, 380.0, 380.0),
            ambient_temp_c=22.0,
            track_temp_c=28.0,
            rain_density=0.0,
            wind_speed_ms=1.5,
            flag_colour=FlagColour.FLAG_GREEN,
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0,
            participants=()
        )

    def test_session_group_classification(self):
        """Verify that raw AMS2 session states map to expected logical session groups."""
        self.assertEqual(get_session_group(SessionState.SESSION_PRACTICE), "PRACTICE")
        self.assertEqual(get_session_group(SessionState.SESSION_TEST), "PRACTICE")
        self.assertEqual(get_session_group(SessionState.SESSION_QUALIFY), "QUALIFY")
        self.assertEqual(get_session_group(SessionState.SESSION_TIME_ATTACK), "TIME_ATTACK")
        self.assertEqual(get_session_group(SessionState.SESSION_FORMATION_LAP), "RACE")
        self.assertEqual(get_session_group(SessionState.SESSION_RACE), "RACE")
        self.assertEqual(get_session_group(SessionState.SESSION_INVALID), "UNKNOWN")

    def test_race_continuity_countdown_formation_race_cooldown(self):
        """
        Critical test: Verify that Countdown, Formation Lap, Active Race, and Cooldown Lap
        are seen as the SAME session and NEVER trigger session resets.
        """
        state_mgr = RaceStateManager()
        resets_detected = []
        state_mgr.register_session_reset_listener(lambda grp: resets_detected.append(grp))

        events_fired = []
        state_mgr.register_event_listener(lambda ev: events_fired.append(ev))

        # 1. Starting grid countdown (initial entry into session)
        grid_snap = self.make_snapshot(
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_NOT_STARTED,
            lap=0,
            laps_completed=0,
            fuel_liters=50.0
        )
        state_mgr.on_telemetry(grid_snap)
        self.assertEqual(state_mgr.current_session_group, "RACE")
        # Initial session setup performed exactly 1 reset
        self.assertEqual(len(resets_detected), 1)

        # 2. Formation lap starts
        formation_snap = self.make_snapshot(
            session_state=SessionState.SESSION_FORMATION_LAP,
            race_state=RaceState.RACESTATE_NOT_STARTED,
            lap=0,
            laps_completed=0,
            fuel_liters=49.5
        )
        state_mgr.on_telemetry(formation_snap)
        # MUST NOT trigger an additional session reset!
        self.assertEqual(len(resets_detected), 1)
        self.assertEqual(state_mgr.current_session_group, "RACE")

        # 3. Lights out -> Green flag race lap 1
        race_snap1 = self.make_snapshot(
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_RACING,
            lap=1,
            laps_completed=0,
            fuel_liters=48.5
        )
        state_mgr.on_telemetry(race_snap1)
        # MUST NOT trigger an additional session reset!
        self.assertEqual(len(resets_detected), 1)
        self.assertEqual(state_mgr.current_session_group, "RACE")

        # 4. Lap 2 completed
        race_snap2 = self.make_snapshot(
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_RACING,
            lap=2,
            laps_completed=1,
            fuel_liters=45.5
        )
        state_mgr.on_telemetry(race_snap2)
        self.assertEqual(len(resets_detected), 1)

        # 5. Cooldown lap after chequered flag
        cooldown_snap = self.make_snapshot(
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_FINISHED,
            lap=15,
            laps_completed=15,
            fuel_liters=10.0
        )
        state_mgr.on_telemetry(cooldown_snap)
        # Still the SAME race session!
        self.assertEqual(len(resets_detected), 1)
        self.assertEqual(state_mgr.current_session_group, "RACE")

        # Confirm no SESSION_CHANGED event was emitted during any of these race phases
        session_changed_events = [e for e in events_fired if e.event_type == EventType.SESSION_CHANGED]
        self.assertEqual(len(session_changed_events), 0)

    def test_inter_session_transitions(self):
        """
        Verify that going Practice -> Quali -> Race triggers clean session resets
        and notifies registered listeners.
        """
        state_mgr = RaceStateManager()
        resets_detected = []
        state_mgr.register_session_reset_listener(lambda grp: resets_detected.append(grp))

        events_fired = []
        state_mgr.register_event_listener(lambda ev: events_fired.append(ev))

        # 1. Start in Practice
        prac_snap1 = self.make_snapshot(session_state=SessionState.SESSION_PRACTICE, lap=1)
        state_mgr.on_telemetry(prac_snap1)
        self.assertEqual(state_mgr.current_session_group, "PRACTICE")

        prac_snap2 = self.make_snapshot(session_state=SessionState.SESSION_PRACTICE, lap=2)
        state_mgr.on_telemetry(prac_snap2)

        # 2. Transition to Qualifying
        quali_snap1 = self.make_snapshot(session_state=SessionState.SESSION_QUALIFY, lap=1)
        state_mgr.on_telemetry(quali_snap1)

        self.assertIn("QUALIFY", resets_detected)
        self.assertEqual(state_mgr.current_session_group, "QUALIFY")

        # 3. Transition to Race (Countdown)
        race_snap1 = self.make_snapshot(
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_NOT_STARTED,
            lap=0
        )
        state_mgr.on_telemetry(race_snap1)

        self.assertIn("RACE", resets_detected)
        self.assertEqual(state_mgr.current_session_group, "RACE")

        # Verify SESSION_CHANGED events were emitted for both transitions
        session_changed_events = [e for e in events_fired if e.event_type == EventType.SESSION_CHANGED]
        self.assertEqual(len(session_changed_events), 2)
        self.assertEqual(session_changed_events[0].details["session_group"], "QUALIFY")
        self.assertEqual(session_changed_events[1].details["session_group"], "RACE")

    def test_in_game_session_restart(self):
        """Verify that clicking Restart Session in AMS2 pause menu triggers a session reset."""
        state_mgr = RaceStateManager()
        resets_detected = []
        state_mgr.register_session_reset_listener(lambda grp: resets_detected.append(grp))

        # Driving in Race on Lap 5
        state_mgr.on_telemetry(self.make_snapshot(lap=1))
        state_mgr.on_telemetry(self.make_snapshot(lap=5, laps_completed=4))

        # Driver clicks restart: session restarting game state
        restart_snap = self.make_snapshot(
            game_state=GameState.GAME_INGAME_RESTARTING,
            lap=1,
            laps_completed=0
        )
        state_mgr.on_telemetry(restart_snap)
        self.assertIn("RACE", resets_detected)

    def test_front_end_exit_and_reentry(self):
        """Verify that exiting to main menu and returning to in-game resets the session."""
        state_mgr = RaceStateManager()
        resets_detected = []
        state_mgr.register_session_reset_listener(lambda grp: resets_detected.append(grp))

        # In a race
        state_mgr.on_telemetry(self.make_snapshot(lap=1))
        state_mgr.on_telemetry(self.make_snapshot(lap=5))

        # Exits to Main Menu
        menu_snap = self.make_snapshot(game_state=GameState.GAME_FRONT_END)
        state_mgr.on_telemetry(menu_snap)
        self.assertTrue(state_mgr._session_needs_reset)

        # Loads a new race
        new_race_snap = self.make_snapshot(lap=1)
        state_mgr.on_telemetry(new_race_snap)
        self.assertIn("RACE", resets_detected)

    def test_brain_reset_clears_history(self):
        """Verify that brain.reset_session() cleanly wipes radio history."""
        brain = RaceEngineerBrain(api_key=None)
        brain.history.append({"speaker": "Driver", "text": "How are the tires?"})
        brain.history.append({"speaker": "Engineer", "text": "Tires look good mate."})
        self.assertEqual(len(brain.history), 2)

        brain.reset_session()
        self.assertEqual(len(brain.history), 0)

    def test_context_standby_and_formation_descriptions(self):
        """Verify build_telemetry_context produces descriptive strings for standby, countdown, and formation lap."""
        fuel = FuelStatus(40.0, 65.0, 60.0, 3.0, 13.0, 10.0, 0.0, None, False, False, False)
        tires = TireStatus("Soft", (95.0, 95.0, 92.0, 92.0), (1.8, 1.8, 1.8, 1.8), (10.0, 10.0, 8.0, 8.0), (90.0, 90.0, 92.0, 92.0), 2.0, 30.0, False, False, [], [], None, False, False, False)
        gaps = GapAnalysis(None, None, None, None, False, None)

        # 1. No snapshot (game not loaded)
        ctx_none = build_telemetry_context(None, fuel, tires, gaps)
        self.assertIn("NO TELEMETRY AVAILABLE", ctx_none)

        # 2. Game in front-end menu
        menu_snap = self.make_snapshot(game_state=GameState.GAME_FRONT_END)
        ctx_menu = build_telemetry_context(menu_snap, fuel, tires, gaps)
        self.assertIn("FRONT-END / PADDOCK MENU", ctx_menu)

        # 3. Starting grid countdown
        grid_snap = self.make_snapshot(race_state=RaceState.RACESTATE_NOT_STARTED)
        ctx_grid = build_telemetry_context(grid_snap, fuel, tires, gaps)
        self.assertIn("STARTING GRID COUNTDOWN", ctx_grid)

        # 4. Formation lap
        form_snap = self.make_snapshot(session_state=SessionState.SESSION_FORMATION_LAP)
        ctx_form = build_telemetry_context(form_snap, fuel, tires, gaps)
        self.assertIn("FORMATION LAP", ctx_form)


if __name__ == "__main__":
    unittest.main()
