"""
Unit tests for Rival Incident Tracking, Pit Exit Rejoin Projection,
and Automatic Tactical Alerts (5min, Final Lap, Box Next Lap).
"""

import unittest
import time
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from telemetry.collector import TelemetrySnapshot, ParticipantSnapshot
from race.incidents import IncidentTracker, IncidentRecord
from race.strategy import RaceStrategyEngine
from race.fuel import FuelStatus, FuelTracker
from race.tires import TireStatus, TireTracker
from race.state import RaceStateManager
from race.events import EventType, RaceEvent, EventPriority


class TestIncidentsAndTacticalAlerts(unittest.TestCase):

    def make_snapshot(self, **kwargs):
        defaults = dict(
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
            track_name="Interlagos",
            track_variation="GP",
            track_length_m=4309.0,
            num_sectors=3,
            total_laps=20,
            event_time_remaining_sec=-1.0,
            lap_invalidated=False,
            player_name="Driver Player",
            position=1,
            current_lap=1,
            laps_completed=0,
            current_sector=1,
            current_lap_distance=0.0,
            current_lap_time=15.0,
            last_lap_time=91.4,
            best_lap_time=91.2,
            split_time_ahead=0.0,
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
        defaults.update(kwargs)
        return TelemetrySnapshot(**defaults)

    def test_incident_tracker_detection_and_cooldown(self):
        tracker = IncidentTracker(
            speed_threshold_kmh=20.0,
            duration_sec=2.0,
            cooldown_per_car_sec=60.0,
            memory_retention_sec=300.0
        )

        p1 = ParticipantSnapshot(
            name="Rival Hamilton",
            position=2,
            lap=5,
            sector=2,
            lap_distance=2000.0,
            is_active=True,
            speed_kmh=15.0,  # Below 20 km/h
            pit_mode=PitMode.PIT_MODE_NONE
        )
        p_player = ParticipantSnapshot(
            name="Driver Player",
            position=1,
            lap=5,
            sector=2,
            lap_distance=2100.0,
            is_active=True,
            speed_kmh=5.0,   # Player is slow/stopped
            pit_mode=PitMode.PIT_MODE_NONE
        )

        # t = 100.0 (first detection, stopped_since recorded)
        incidents = tracker.update([p1, p_player], player_name="Driver Player", current_time=100.0)
        self.assertEqual(len(incidents), 0, "Should not trigger immediately at t=100")

        # t = 101.5 (only 1.5s elapsed, threshold is 2.0s)
        incidents = tracker.update([p1, p_player], player_name="Driver Player", current_time=101.5)
        self.assertEqual(len(incidents), 0, "Should not trigger before 2.0s duration")

        # t = 102.2 (2.2s elapsed -> threshold reached!)
        incidents = tracker.update([p1, p_player], player_name="Driver Player", current_time=102.2)
        self.assertEqual(len(incidents), 1, "Should trigger incident after 2.0s")
        self.assertEqual(incidents[0].driver_name, "Rival Hamilton")
        self.assertEqual(incidents[0].sector, 2)
        self.assertEqual(incidents[0].position, 2)

        # t = 105.0 (still stopped, but within 60s cooldown)
        incidents = tracker.update([p1, p_player], player_name="Driver Player", current_time=105.0)
        self.assertEqual(len(incidents), 0, "Should not re-trigger while on cooldown")

        # Summary check
        summary = tracker.get_recent_incidents_summary(current_time=105.0)
        self.assertIn("Rival Hamilton", summary)
        # AMS2 mCurrentSector is 0-based (the live log showed "Sector 0"): raw 2 = Sector 3
        self.assertIn("Sector 3", summary)

    def test_incident_tracker_ignores_pit_lane_cars(self):
        tracker = IncidentTracker(
            speed_threshold_kmh=20.0,
            duration_sec=2.0,
            cooldown_per_car_sec=60.0
        )

        p_pit = ParticipantSnapshot(
            name="Pit Car Leclerc",
            position=4,
            lap=5,
            sector=3,
            lap_distance=500.0,
            is_active=True,
            speed_kmh=0.0,
            pit_mode=PitMode.PIT_MODE_IN_PIT  # Car is legitimately in pit lane
        )

        tracker.update([p_pit], player_name="Driver Player", current_time=10.0)
        incidents = tracker.update([p_pit], player_name="Driver Player", current_time=15.0)
        self.assertEqual(len(incidents), 0, "Cars in pit lane should never trigger rival incident alerts")

    def test_pit_exit_rejoin_projection(self):
        engine = RaceStrategyEngine()

        # Build grid:
        # P1: Player
        # P2: Rival Verstappen, gap = 10.0s behind player
        # P3: Rival Norris, gap = 35.0s behind player
        participants = [
            ParticipantSnapshot(
                name="Driver Player", position=1, lap=10, sector=1,
                lap_distance=500.0, is_active=True, speed_kmh=220.0, pit_mode=PitMode.PIT_MODE_NONE
            ),
            ParticipantSnapshot(
                name="Max Verstappen", position=2, lap=10, sector=1,
                lap_distance=400.0, is_active=True, speed_kmh=220.0, pit_mode=PitMode.PIT_MODE_NONE
            ),
            ParticipantSnapshot(
                name="Lando Norris", position=3, lap=9, sector=3,
                lap_distance=3155.0, is_active=True, speed_kmh=220.0, pit_mode=PitMode.PIT_MODE_NONE
            ),
        ]

        snapshot = self.make_snapshot(
            current_lap=10,
            total_laps=20,
            position=1,
            split_time_behind=10.0,  # Verstappen is 10s behind
            participants=participants,
            fuel_liters=20.0,
            fuel_capacity_liters=60.0
        )

        fuel = FuelStatus(
            current_liters=20.0,
            capacity_liters=60.0,
            level_pct=33.3,
            avg_per_lap=3.0,
            laps_remaining_fuel=6.6,
            laps_remaining_race=10.0,
            fuel_deficit_laps=3.4,
            target_consumption=None,
            fuel_required_liters=10.2,
            recommended_refuel_liters=16.2,
            is_critical=False,
            is_empty_risk=False,
            needs_pit_stop=True,
            earliest_safe_pit_lap=1,
            latest_safe_pit_lap=15
        )

        tire_tracker = TireTracker()
        tires = tire_tracker.analyze(
            compound="Soft",
            tread_temps=(90.0, 90.0, 90.0, 90.0),
            pressures=(1.9, 1.9, 1.9, 1.9),
            wear=(15.0, 15.0, 15.0, 15.0)
        )

        # Estimated pit loss is 26.0s
        # Verstappen is 10s behind (< 26s), so Verstappen will jump us (1 car passes).
        # Rejoin position should be P2!
        strategy = engine.compute_strategy(snapshot, fuel, tires, estimated_pit_loss_sec=26.0)

        self.assertEqual(strategy.projected_rejoin_pos, 2)
        self.assertIn("rejoin ~P2", strategy.rejoin_summary)
        self.assertIn("Verstappen", strategy.rejoin_summary)

    def test_5min_time_warning_and_final_lap_events(self):
        state_mgr = RaceStateManager()
        fired_events = []

        def on_event(ev: RaceEvent):
            fired_events.append(ev)

        state_mgr.register_event_listener(on_event)

        # Initial baseline snapshot (t_remaining = 600s)
        snap1 = self.make_snapshot(
            current_lap=1,
            total_laps=0,
            event_time_remaining_sec=600.0,
            position=1,
            fuel_liters=40.0
        )
        state_mgr.on_telemetry(snap1)

        # Snapshot 2: Time drops to 295s (<= 300s) -> 5-minute warning must fire
        snap2 = self.make_snapshot(
            current_lap=8,
            total_laps=0,
            event_time_remaining_sec=295.0,
            position=1,
            fuel_liters=20.0
        )
        state_mgr.on_telemetry(snap2)

        event_types = [e.event_type for e in fired_events]
        self.assertIn(EventType.TIME_WARNING_5MIN, event_types)

        # Snapshot 3: Session clock expires during lap 15 (time = 0.0). Final lap should NOT fire yet.
        snap3 = self.make_snapshot(
            current_lap=15,
            total_laps=0,
            event_time_remaining_sec=0.0,
            position=1,
            fuel_liters=10.0
        )
        state_mgr.on_telemetry(snap3)
        self.assertNotIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])

        # Snapshot 4: Driver crosses start/finish line after time expiry -> Lap 16 begins (Final Lap)
        snap4 = self.make_snapshot(
            current_lap=16,
            total_laps=0,
            event_time_remaining_sec=0.0,
            position=1,
            fuel_liters=9.0
        )
        state_mgr.on_telemetry(snap4)
        self.assertIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])

    def test_box_next_lap_event(self):
        state_mgr = RaceStateManager()
        fired_events = []

        def on_event(ev: RaceEvent):
            fired_events.append(ev)

        state_mgr.register_event_listener(on_event)

        snap1 = self.make_snapshot(
            current_lap=1,
            total_laps=20,
            position=1,
            fuel_liters=50.0
        )
        state_mgr.on_telemetry(snap1)

        # Set burn rate so 6.0L left with 3.0L/lap gives 2.0 laps remaining
        state_mgr.fuel_tracker.lap_fuel_burns = [3.0, 3.0]
        state_mgr.fuel_tracker.avg_consumption_per_lap = 3.0

        snap2 = self.make_snapshot(
            current_lap=10,
            total_laps=20,
            position=1,
            fuel_liters=6.0,  # 6.0L / 3.0L/lap = 2.0 laps remaining
            fuel_capacity_liters=60.0
        )
        state_mgr.on_telemetry(snap2)

        event_types = [e.event_type for e in fired_events]
        # Only ~2 laps of fuel on lap 10 -> the plan is to pit at the end of THIS lap, so the
        # "box this lap" call is made at the start of the lap (heads-up would be a lap earlier).
        self.assertIn(EventType.BOX_THIS_LAP, event_types)

    def test_tactical_alerts_bypass_silent_mode(self):
        from main import RaceEngineerApp
        app = RaceEngineerApp("config.yaml")

        # Verify default loaded config
        self.assertTrue(app.speak_only_when_spoken_to)
        self.assertTrue(app.allow_tactical_alerts)

        dispatched_events = []
        app.brain.generate_event_advice = lambda ev, ctx: (f"Alert: {ev.message}", "calm")
        app.scheduler.push = lambda text, priority, event_type, emotion: dispatched_events.append(event_type)

        # 1. Non-tactical event (LAP_COMPLETED) -> Should be suppressed
        lap_event = RaceEvent(
            event_type=EventType.LAP_COMPLETED,
            priority=EventPriority.MEDIUM,
            timestamp=time.time(),
            message="Completed Lap 5"
        )
        app._handle_race_event(lap_event)
        time.sleep(0.05)
        self.assertNotIn(EventType.LAP_COMPLETED, dispatched_events)

        # 2. Tactical alert (TIME_WARNING_5MIN) -> Should be allowed through!
        warn_event = RaceEvent(
            event_type=EventType.TIME_WARNING_5MIN,
            priority=EventPriority.HIGH,
            timestamp=time.time(),
            message="5 minutes remaining"
        )
        app._handle_race_event(warn_event)
        time.sleep(0.05)
        self.assertIn(EventType.TIME_WARNING_5MIN, dispatched_events)

        # 3. Tactical alert (BOX_NEXT_LAP) -> Should be allowed through!
        box_event = RaceEvent(
            event_type=EventType.BOX_NEXT_LAP,
            priority=EventPriority.HIGH,
            timestamp=time.time(),
            message="Box next lap"
        )
        app._handle_race_event(box_event)
        time.sleep(0.05)
        self.assertIn(EventType.BOX_NEXT_LAP, dispatched_events)

        # 4. Weather alert (WEATHER_RAIN_STARTED) -> Should be allowed through!
        rain_event = RaceEvent(
            event_type=EventType.WEATHER_RAIN_STARTED,
            priority=EventPriority.HIGH,
            timestamp=time.time(),
            message="Rain started"
        )
        app._handle_race_event(rain_event)
        time.sleep(0.05)
        self.assertIn(EventType.WEATHER_RAIN_STARTED, dispatched_events)

    def test_ams2_flag_mappings_and_no_false_black_flags(self):
        # 1. Verify enum values match AMS2 Madness Engine spec
        self.assertEqual(FlagColour.FLAG_YELLOW.value, 6)
        self.assertEqual(FlagColour.FLAG_DOUBLE_YELLOW.value, 7)
        self.assertEqual(FlagColour.FLAG_BLACK.value, 10)
        self.assertEqual(FlagColour.FLAG_CHEQUERED.value, 11)
        self.assertEqual(FlagColour.FLAG_WHITE_FINAL_LAP.value, 4)

        state_mgr = RaceStateManager()
        fired_events = []
        state_mgr.register_event_listener(lambda ev: fired_events.append(ev))

        # Baseline: Green flag
        snap_base = self.make_snapshot(
            flag_colour=FlagColour.FLAG_GREEN,
            current_lap=1
        )
        state_mgr.on_telemetry(snap_base)
        fired_events.clear()

        # 2. Yellow flag (6) must fire FLAG_YELLOW, NOT FLAG_BLACK
        snap_yellow = self.make_snapshot(
            flag_colour=FlagColour.FLAG_YELLOW,
            current_lap=2
        )
        state_mgr.on_telemetry(snap_yellow)
        event_types = [e.event_type for e in fired_events]
        self.assertIn(EventType.FLAG_YELLOW, event_types)
        self.assertNotIn(EventType.FLAG_BLACK, event_types)

        fired_events.clear()

        # 3. Black flag (10) must fire FLAG_BLACK
        snap_black = self.make_snapshot(
            flag_colour=FlagColour.FLAG_BLACK,
            current_lap=3
        )
        state_mgr.on_telemetry(snap_black)
        event_types = [e.event_type for e in fired_events]
        self.assertIn(EventType.FLAG_BLACK, event_types)

        fired_events.clear()

        # 4. Chequered flag (11) must fire FLAG_CHEQUERED
        snap_chequered = self.make_snapshot(
            flag_colour=FlagColour.FLAG_CHEQUERED,
            current_lap=20
        )
        state_mgr.on_telemetry(snap_chequered)
        event_types = [e.event_type for e in fired_events]
        self.assertIn(EventType.FLAG_CHEQUERED, event_types)

    def test_weather_lifecycle_events(self):
        state_mgr = RaceStateManager()
        fired_events = []
        state_mgr.register_event_listener(lambda ev: fired_events.append(ev))

        # Baseline: Dry (0%)
        snap0 = self.make_snapshot(rain_density=0.0, current_lap=1)
        state_mgr.on_telemetry(snap0)

        # 1. Rain starts (30%) -> WEATHER_RAIN_STARTED
        snap1 = self.make_snapshot(rain_density=0.30, current_lap=2)
        state_mgr.on_telemetry(snap1)
        event_types = [e.event_type for e in fired_events]
        self.assertIn(EventType.WEATHER_RAIN_STARTED, event_types)

        fired_events.clear()

        # 2. Rain intensifies to 74% -> WEATHER_RAIN_INTENSIFIED
        snap2 = self.make_snapshot(rain_density=0.74, current_lap=3)
        state_mgr.on_telemetry(snap2)
        event_types = [e.event_type for e in fired_events]
        self.assertIn(EventType.WEATHER_RAIN_INTENSIFIED, event_types)

        fired_events.clear()

        # 3. Rain stops (0%) -> WEATHER_RAIN_STOPPED
        snap3 = self.make_snapshot(rain_density=0.0, current_lap=4)
        state_mgr.on_telemetry(snap3)
        event_types = [e.event_type for e in fired_events]
        self.assertIn(EventType.WEATHER_RAIN_STOPPED, event_types)

    def test_timed_race_countdown_and_negative_time_remaining(self):
        state_mgr = RaceStateManager()
        fired_events = []
        state_mgr.register_event_listener(lambda ev: fired_events.append(ev))

        # Baseline: 650s left
        snap1 = self.make_snapshot(total_laps=0, event_time_remaining_sec=650.0, current_lap=1)
        state_mgr.on_telemetry(snap1)

        # 10 minute warning (<= 600s)
        snap2 = self.make_snapshot(total_laps=0, event_time_remaining_sec=590.0, current_lap=2)
        state_mgr.on_telemetry(snap2)
        self.assertIn(EventType.TIME_WARNING_10MIN, [e.event_type for e in fired_events])

        # 2 minute warning (<= 120s)
        snap3 = self.make_snapshot(total_laps=0, event_time_remaining_sec=115.0, current_lap=6)
        state_mgr.on_telemetry(snap3)
        self.assertIn(EventType.TIME_WARNING_2MIN, [e.event_type for e in fired_events])

        # 1 minute warning (<= 60s)
        snap4 = self.make_snapshot(total_laps=0, event_time_remaining_sec=55.0, current_lap=7)
        state_mgr.on_telemetry(snap4)
        self.assertIn(EventType.TIME_WARNING_1MIN, [e.event_type for e in fired_events])

        # Clock expires during lap 8 (time drops to -1.0). Final lap should NOT fire yet.
        snap5 = self.make_snapshot(total_laps=0, event_time_remaining_sec=-1.0, current_lap=8)
        state_mgr.on_telemetry(snap5)
        self.assertNotIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])

        # Driver crosses line into Lap 9 -> Starts Final Lap
        snap5b = self.make_snapshot(total_laps=0, event_time_remaining_sec=-1.0, current_lap=9)
        state_mgr.on_telemetry(snap5b)
        self.assertIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])

        # Chequered flag when race finishes
        snap6 = self.make_snapshot(total_laps=0, event_time_remaining_sec=-1.0, current_lap=9, race_state=RaceState.RACESTATE_FINISHED)
        state_mgr.on_telemetry(snap6)
        self.assertIn(EventType.FLAG_CHEQUERED, [e.event_type for e in fired_events])

    def test_lap_countdown_warnings(self):
        state_mgr = RaceStateManager()
        fired_events = []
        state_mgr.register_event_listener(lambda ev: fired_events.append(ev))

        # 20 laps total, lap 1
        snap1 = self.make_snapshot(total_laps=20, current_lap=1)
        state_mgr.on_telemetry(snap1)

        # Lap 16 -> 5 laps remaining (20 - 16 + 1 = 5)
        snap2 = self.make_snapshot(total_laps=20, current_lap=16)
        state_mgr.on_telemetry(snap2)
        self.assertIn(EventType.LAPS_WARNING_5LAPS, [e.event_type for e in fired_events])

        # Lap 19 -> 2 laps remaining (20 - 19 + 1 = 2)
        snap3 = self.make_snapshot(total_laps=20, current_lap=19)
        state_mgr.on_telemetry(snap3)
        self.assertIn(EventType.LAPS_WARNING_2LAPS, [e.event_type for e in fired_events])

        # Lap 20 -> Final lap (20 - 20 + 1 = 1)
        snap4 = self.make_snapshot(total_laps=20, current_lap=20)
        state_mgr.on_telemetry(snap4)
        self.assertIn(EventType.FINAL_LAP, [e.event_type for e in fired_events])

    def test_brain_scrubs_chuckles_and_stage_directions(self):
        from engineer.brain import RaceEngineerBrain
        sample = (
            "TRANSCRIPT: What is the weather doing?\n"
            "EMOTION: witty chuckle\n"
            "DELIVERY: with a dry chuckle\n"
            "RESPONSE: [chuckles] Rain is starting to fall, mate. Watch your braking into turn one."
        )
        transcript, emotion, delivery, response = RaceEngineerBrain._parse_llm_response(sample)
        self.assertNotIn("chuckle", response.lower())
        self.assertNotIn("[chuckles]", response)
        self.assertTrue(response.startswith("Rain is starting to fall"))
        self.assertNotIn("chuckle", delivery.lower())
        self.assertNotIn("witty", emotion.lower())

    def test_collector_convert_real_ctypes_ams2_shared_memory(self):
        from telemetry.collector import TelemetryCollector
        from telemetry.shared_memory import AMS2SharedMemory
        collector = TelemetryCollector()
        raw = AMS2SharedMemory()
        raw.mVersion = 13
        raw.mGameState = 2  # GAME_INGAME_PLAYING
        raw.mSessionState = 5  # SESSION_RACE
        raw.mRaceState = 2  # RACESTATE_RACING
        raw.mViewedParticipantIndex = 0
        raw.mNumParticipants = 1
        raw.mParticipantInfo[0].mIsActive = True
        raw.mParticipantInfo[0].mRacePosition = 1
        raw.mParticipantInfo[0].mCurrentLap = 3
        raw.mParticipantInfo[0].mCurrentSector = 2
        raw.mParticipantInfo[0].mName = b"Test Driver\x00"
        raw.mTrackLocation = b"Interlagos\x00"
        raw.mTrackVariation = b"GP\x00"
        raw.mCarName = b"Porsche 911 GT3\x00"
        raw.mCarClassName = b"GT3\x00"
        raw.mBestLapTime = 92.5
        raw.mLastLapTime = 93.1
        raw.mCurrentTime = 34.2
        raw.mFuelLevel = 0.5
        raw.mFuelCapacity = 100.0

        snapshot = collector._convert_snapshot(raw)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.player_name, "Test Driver")
        self.assertEqual(snapshot.track_name, "Interlagos")
        self.assertAlmostEqual(snapshot.best_lap_time, 92.5, places=2)
        self.assertAlmostEqual(snapshot.last_lap_time, 93.1, places=2)

    def test_grid_incident_suppression(self):
        state_mgr = RaceStateManager()
        fired_events = []
        state_mgr.register_event_listener(lambda ev: fired_events.append(ev))

        # 1. Car stopped on starting grid (RACESTATE_NOT_STARTED) -> Must NOT fire incident
        p_stopped = ParticipantSnapshot(
            name="Andre Costa",
            position=12,
            lap=1,
            sector=2,
            lap_distance=500.0,
            is_active=True,
            speed_kmh=0.0
        )
        snap_base = self.make_snapshot(current_lap=1, race_state=RaceState.RACESTATE_NOT_STARTED, participants=(p_stopped,))
        state_mgr.on_telemetry(snap_base)

        snap_grid = self.make_snapshot(current_lap=1, race_state=RaceState.RACESTATE_NOT_STARTED, participants=(p_stopped,))
        # advance time for incident duration
        state_mgr.incident_tracker._slow_start_times["andre costa"] = time.time() - 5.0
        state_mgr.on_telemetry(snap_grid)

        event_types = [e.event_type for e in fired_events]
        self.assertNotIn(EventType.RIVAL_INCIDENT, event_types)

        # 2. Car stopped during Lap 1 launch phase (current_lap_time < 15.0) -> Must NOT fire
        snap_launch = self.make_snapshot(
            current_lap=1,
            current_lap_time=8.0,
            race_state=RaceState.RACESTATE_RACING,
            participants=(p_stopped,)
        )
        state_mgr.on_telemetry(snap_launch)
        self.assertNotIn(EventType.RIVAL_INCIDENT, [e.event_type for e in fired_events])

        # 3. Car stopped on Lap 2 -> MUST fire
        snap_lap2 = self.make_snapshot(
            current_lap=2,
            current_lap_time=30.0,
            race_state=RaceState.RACESTATE_RACING,
            track_length_m=3000.0,
            participants=(p_stopped,)
        )
        state_mgr.on_telemetry(snap_lap2)
        self.assertIn(EventType.RIVAL_INCIDENT, [e.event_type for e in fired_events])

    def test_relative_incident_with_latency_compensation(self):
        tracker = IncidentTracker()
        now = time.time()

        # Rival 1: 300m ahead of player on a 4000m track.
        # Player speed = 180 km/h (50 m/s).
        # In 4.0s of AI latency, player travels 200m.
        # Projected delta = 100m ahead -> coming up fast!
        p_ahead = ParticipantSnapshot(
            name="Cléber Barros",
            position=15,
            lap=4,
            sector=1,
            lap_distance=1300.0,
            is_active=True,
            speed_kmh=0.0
        )
        # Pre-seed slow duration
        tracker._slow_start_times["cléber barros"] = now - 5.0

        incidents = tracker.update(
            participants=(p_ahead,),
            player_name="Driver Player",
            current_time=now,
            player_lap_dist=1000.0,
            track_length_m=4000.0,
            player_speed_kmh=180.0
        )

        self.assertEqual(len(incidents), 1)
        inc = incidents[0]
        self.assertTrue(inc.is_ahead)
        self.assertTrue(inc.likely_to_see)
        self.assertAlmostEqual(inc.relative_delta_m, 300.0, places=1)
        self.assertAlmostEqual(inc.projected_delta_m, 100.0, places=1)
        self.assertIn("coming up fast (~100m ahead)", inc.description)

        # Rival 2: 200m behind player (player at 1000m, rival at 800m)
        tracker.reset()
        p_behind = ParticipantSnapshot(
            name="Leandro Paiva",
            position=20,
            lap=4,
            sector=1,
            lap_distance=800.0,
            is_active=True,
            speed_kmh=0.0
        )
        tracker._slow_start_times["leandro paiva"] = now - 5.0

        incidents_behind = tracker.update(
            participants=(p_behind,),
            player_name="Driver Player",
            current_time=now,
            player_lap_dist=1000.0,
            track_length_m=4000.0,
            player_speed_kmh=180.0
        )
        self.assertEqual(len(incidents_behind), 1)
        inc_b = incidents_behind[0]
        self.assertFalse(inc_b.is_ahead)
        self.assertFalse(inc_b.likely_to_see)
        self.assertAlmostEqual(inc_b.relative_delta_m, 200.0, places=1)
        self.assertIn("behind you", inc_b.description)

    def test_scheduler_stale_yellow_flag_drop_and_caution_purging(self):
        from engineer.scheduler import MessageScheduler
        scheduler = MessageScheduler()

        # 1. Pushing yellow flag, then pushing rival incident purges the older generic yellow
        scheduler.push(
            text="Yellow flag on track, maintain delta",
            priority=EventPriority.CRITICAL,
            event_type=EventType.FLAG_YELLOW
        )
        self.assertEqual(len(scheduler._queue), 1)
        self.assertEqual(scheduler._queue[0].event_type, EventType.FLAG_YELLOW)

        # Push more specific rival incident -> generic yellow flag must be purged
        scheduler.push(
            text="Barros has spun in Sector 1 ahead of you",
            priority=EventPriority.HIGH,
            event_type=EventType.RIVAL_INCIDENT
        )
        self.assertEqual(len(scheduler._queue), 1)
        self.assertEqual(scheduler._queue[0].event_type, EventType.RIVAL_INCIDENT)

        # 2. Stale yellow flag (>6.0s old) must be dropped
        scheduler._queue[0].timestamp = time.time() - 7.0
        polled = scheduler.poll_next_message(snapshot=None)
        self.assertIsNone(polled)
        self.assertEqual(len(scheduler._queue), 0)

    def test_incident_retention_2_minute_window(self):
        tracker = IncidentTracker(
            speed_threshold_kmh=20.0,
            duration_sec=2.0,
            cooldown_per_car_sec=60.0,
            memory_retention_sec=120.0  # 2 minutes retention
        )
        p1 = ParticipantSnapshot(
            name="Rival Alonso",
            position=3,
            lap=5,
            sector=1,
            lap_distance=1000.0,
            is_active=True,
            speed_kmh=10.0,
            pit_mode=PitMode.PIT_MODE_NONE
        )
        tracker.update([p1], player_name="Driver Player", current_time=100.0)
        tracker.update([p1], player_name="Driver Player", current_time=102.5)  # incident triggered at 102.5

        # At t = 220.0 (117.5s elapsed, within 120s): incident retained
        summary = tracker.get_recent_incidents_summary(current_time=220.0)
        self.assertIn("Rival Alonso", summary)
        self.assertIn("LAST 2 MIN", summary)

        # At t = 225.0 (122.5s elapsed, > 120s): incident pruned
        summary_expired = tracker.get_recent_incidents_summary(current_time=225.0)
        self.assertIn("Track clear", summary_expired)
        self.assertNotIn("Rival Alonso", summary_expired)

    def test_yellow_flag_and_rival_incident_suppressed_from_proactive_speech(self):
        from main import RaceEngineerApp
        from unittest.mock import MagicMock

        app = RaceEngineerApp.__new__(RaceEngineerApp)
        app.player = MagicMock()
        app.scheduler = MagicMock()
        app.brain = MagicMock()
        app.brain.generate_event_advice.return_value = ("Box this lap", "focused", "sharp")
        app.speak_only_when_spoken_to = False
        app.allow_tactical_alerts = True
        app.emotion_mode = "dynamic"
        app._get_current_context_string = MagicMock(return_value="context")
        app._resolve_emotion = lambda em: em

        # Yellow flag event -> suppressed
        yellow_event = RaceEvent(
            event_type=EventType.FLAG_YELLOW,
            priority=EventPriority.CRITICAL,
            timestamp=time.time(),
            message="Yellow flag caution",
            details={}
        )
        app._handle_race_event(yellow_event)
        app.brain.generate_event_advice.assert_not_called()
        app.scheduler.push.assert_not_called()

        # Rival incident event -> suppressed
        rival_event = RaceEvent(
            event_type=EventType.RIVAL_INCIDENT,
            priority=EventPriority.HIGH,
            timestamp=time.time(),
            message="Incident on track",
            details={}
        )
        app._handle_race_event(rival_event)
        app.brain.generate_event_advice.assert_not_called()
        app.scheduler.push.assert_not_called()

        # Box next lap event -> allowed and processed
        box_event = RaceEvent(
            event_type=EventType.BOX_NEXT_LAP,
            priority=EventPriority.HIGH,
            timestamp=time.time(),
            message="Box next lap",
            details={}
        )
        app._handle_race_event(box_event)
        time.sleep(0.05)
        app.brain.generate_event_advice.assert_called_once()


if __name__ == "__main__":
    unittest.main()
