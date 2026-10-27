"""
Unit tests for Strategy Engine and Intent Classifier.
"""

import unittest
import time
from telemetry.shared_memory import GameState, SessionState, RaceState, FlagColour, PitMode
from telemetry.collector import TelemetrySnapshot
from race.fuel import FuelStatus
from race.tires import TireStatus
from race.strategy import RaceStrategyEngine
from engineer.intent import classify_driver_intent, DriverIntent


class TestStrategyAndIntent(unittest.TestCase):

    def setUp(self):
        self.engine = RaceStrategyEngine()

    def test_intent_classification(self):
        # Joke intent
        intent, telem, guidance = classify_driver_intent("Can you tell me a joke?")
        self.assertEqual(intent, DriverIntent.JOKE_REQUEST)
        self.assertFalse(telem)

        # SitRep query intent
        intent, telem, guidance = classify_driver_intent("Give me a sitrep mate")
        self.assertEqual(intent, DriverIntent.SITREP_QUERY)
        self.assertTrue(telem)

        # Competitor pit stop query intent
        intent, telem, guidance = classify_driver_intent("Has the field done their stops yet?")
        self.assertEqual(intent, DriverIntent.PIT_STRATEGY_QUERY)
        self.assertTrue(telem)

        # Driver declaration to pit
        intent, telem, guidance = classify_driver_intent("So I'm going to pit this lap")
        self.assertEqual(intent, DriverIntent.DRIVER_DECLARATION)
        self.assertTrue(telem)

        # Interrupted speech
        intent, telem, guidance = classify_driver_intent("No, I'm going to pit, but who")
        self.assertEqual(intent, DriverIntent.INTERRUPTED)
        self.assertFalse(telem)

        # Fuel query
        intent, telem, guidance = classify_driver_intent("How much fuel do I need to put in?")
        self.assertEqual(intent, DriverIntent.FUEL_QUERY)
        self.assertTrue(telem)

        # Acknowledgement
        intent, telem, guidance = classify_driver_intent("Copy that")
        self.assertEqual(intent, DriverIntent.ACKNOWLEDGEMENT)
        self.assertFalse(telem)

    def test_mandatory_stop_ignored_when_fuel_and_tires_safe(self):
        # Even if enforced_pit_stop_lap is set or driver_declared_mandatory_stop is called,
        # mandatory stop logic is disabled and strategy relies purely on fuel and tyres.
        self.engine.on_driver_mandatory_stop_declared(True)

        fuel = FuelStatus(
            current_liters=50.0,
            capacity_liters=60.0,
            level_pct=83.3,
            avg_per_lap=3.0,
            laps_remaining_fuel=16.6,
            laps_remaining_race=10.0,
            fuel_deficit_laps=0.0,
            target_consumption=None,
            needs_pit_stop=False,
            is_critical=False,
            is_empty_risk=False,
            fuel_surplus_laps=6.6,
            fuel_status_label="FUEL SAFE TO END"
        )

        tires = TireStatus(
            compound="Soft",
            tread_temps_c=(90.0, 90.0, 90.0, 90.0),
            pressures_bar=(1.8, 1.8, 1.8, 1.8),
            wear_pct=(10.0, 10.0, 10.0, 10.0),
            life_pct=(90.0, 90.0, 90.0, 90.0),
            wear_rate_per_lap=2.0,
            estimated_laps_remaining=20.0,
            is_overheating=False,
            is_cold=False,
            overheated_tires=[],
            cold_tires=[],
            balance_note=None,
            is_gone_off=False,
            is_very_worn=False,
            is_critical_wear=False
        )

        snap = TelemetrySnapshot(
            timestamp=time.time(),
            game_state=GameState.GAME_INGAME_PLAYING,
            session_state=SessionState.SESSION_RACE,
            race_state=RaceState.RACESTATE_RACING,
            car_name="Porsche 911 GT3",
            car_class_name="GT3",
            speed_kmh=180.0,
            rpm=6000.0,
            max_rpm=8500.0,
            gear=4,
            throttle=1.0,
            brake=0.0,
            steering=0.0,
            clutch=0.0,
            track_name="Spa",
            track_variation="GP",
            track_length_m=7004.0,
            num_sectors=3,
            total_laps=15,
            lap_invalidated=False,
            player_name="Driver",
            position=4,
            current_lap=5,
            laps_completed=4,
            current_sector=1,
            current_lap_time=30.0,
            last_lap_time=138.0,
            best_lap_time=137.5,
            split_time_ahead=2.0,
            split_time_behind=1.5,
            sector1_time=38.0,
            sector2_time=52.0,
            sector3_time=47.0,
            best_sector1_time=37.8,
            best_sector2_time=51.8,
            best_sector3_time=46.9,
            fuel_level_pct=83.3,
            fuel_liters=50.0,
            fuel_capacity_liters=60.0,
            water_temp_c=85.0,
            oil_temp_c=95.0,
            oil_pressure_kpa=450.0,
            tyre_compound="Soft",
            tyre_tread_temps_c=(90.0, 90.0, 90.0, 90.0),
            tyre_carcass_temps_c=(88.0, 88.0, 88.0, 88.0),
            tyre_pressures_bar=(1.8, 1.8, 1.8, 1.8),
            tyre_wear_pct=(10.0, 10.0, 10.0, 10.0),
            brake_temps_c=(300.0, 300.0, 300.0, 300.0),
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
            enforced_pit_stop_lap=5
        )

        strat = self.engine.compute_strategy(snap, fuel, tires)

        self.assertFalse(strat.mandatory_stop_required)
        self.assertEqual(strat.mandatory_stop_source, "NONE")
        self.assertFalse(strat.mandatory_stop_pending)
        self.assertEqual(strat.pit_urgency, "NO PIT REQUIRED")

    def test_fuel_intent_phrases(self):
        for phrase in [
            "Will I make it to the end?",
            "Is fuel urgent?",
            "Will I run out before the finish?",
            "Are we good on fuel?",
            "Do we have enough to finish?"
        ]:
            intent, telem, guidance = classify_driver_intent(phrase)
            self.assertEqual(intent, DriverIntent.FUEL_QUERY, f"Failed for phrase: {phrase}")
            self.assertTrue(telem)

    def test_final_lap_fuel_safe_strategy_urgency(self):
        # 1 lap of fuel remaining on final lap, but safe to finish (+0.1 laps surplus)
        fuel = FuelStatus(
            current_liters=3.3,
            capacity_liters=60.0,
            level_pct=5.5,
            avg_per_lap=3.0,
            laps_remaining_fuel=1.1,
            laps_remaining_race=1.0,
            fuel_deficit_laps=0.0,
            target_consumption=None,
            needs_pit_stop=False,
            is_critical=True,
            is_empty_risk=True,
            is_urgent=False,
            fuel_surplus_laps=0.1,
            fuel_status_label="FUEL SAFE TO END"
        )
        tires = TireStatus(
            compound="Soft",
            tread_temps_c=(90.0, 90.0, 90.0, 90.0),
            pressures_bar=(1.8, 1.8, 1.8, 1.8),
            wear_pct=(20.0, 20.0, 20.0, 20.0),
            life_pct=(80.0, 80.0, 80.0, 80.0),
            wear_rate_per_lap=1.0,
            estimated_laps_remaining=15.0,
            is_overheating=False,
            is_cold=False,
            overheated_tires=[],
            cold_tires=[],
            balance_note=None,
            is_gone_off=False,
            is_very_worn=False,
            is_critical_wear=False,
            limiting_tire="FL",
            condition_summary="Nominal",
            corner_wear_rates=[1.0, 1.0, 1.0, 1.0]
        )
        from telemetry.collector import TelemetrySnapshot, SessionState, RaceState, GameState, FlagColour, PitMode
        snap = TelemetrySnapshot(
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
            total_laps=10,
            lap_invalidated=False,
            player_name="Driver",
            position=1,
            current_lap=10,
            laps_completed=9,
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
            fuel_level_pct=5.5,
            fuel_liters=3.3,
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
            pit_mode=PitMode.PIT_MODE_NONE,
            aero_damage=0.0,
            engine_damage=0.0,
            collision_flag=False,
            collision_magnitude=0.0,
            drs_state=0,
            ers_mode=0
        )
        strat = self.engine.compute_strategy(snap, fuel, tires)
        # Even though is_empty_risk is True, driver can finish without pitting:
        self.assertEqual(strat.pit_urgency, "NO PIT REQUIRED")
        self.assertIn("safe to the finish", strat.rationale)


if __name__ == "__main__":
    unittest.main()