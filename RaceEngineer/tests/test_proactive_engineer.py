"""
Proactive engineer: daily voice budget, gap radar, pace plan and the speak / stay-quiet gate.
"""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from race.events import EventPriority, EventType, RaceEvent
from race.radar import RaceRadar, reference_pace
from voice import budget as budget_mod
from voice.budget import DailyRequestBudget


def car(name, pos, pit_mode=0):
    return SimpleNamespace(name=name, position=pos, is_active=True, pit_mode=pit_mode, lap=5)


def snap(position, ahead_gap, behind_gap, names=("Ahead Driver", "Behind Driver")):
    parts = [car("Me Myself", position)]
    if position > 1:
        parts.append(car(names[0], position - 1))
    parts.append(car(names[1], position + 1))
    return SimpleNamespace(position=position, player_name="Me Myself", participants=tuple(parts),
                           split_time_ahead=ahead_gap, split_time_behind=behind_gap)


class TestDailyBudget(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "tts_usage.json")

    def test_counts_and_persists_within_the_day(self):
        b = DailyRequestBudget(daily_limit=10, path=self.path)
        b.record(); b.record(); b.record()
        self.assertEqual(b.used, 3)
        self.assertEqual(b.remaining, 7)
        again = DailyRequestBudget(daily_limit=10, path=self.path)
        self.assertEqual(again.used, 3)

    def test_new_quota_day_starts_from_zero(self):
        with mock.patch.object(budget_mod, "_quota_day", return_value="2026-11-13"):
            b = DailyRequestBudget(daily_limit=10, path=self.path)
            b.record(4)
        with mock.patch.object(budget_mod, "_quota_day", return_value="2026-11-14"):
            self.assertEqual(b.used, 0)
            self.assertEqual(DailyRequestBudget(daily_limit=10, path=self.path).used, 0)

    def test_tts_requests_are_counted(self):
        from voice.tts import GeminiTTS
        tts = GeminiTTS(api_key=None)
        tts.client = mock.MagicMock()
        tts.budget = DailyRequestBudget(daily_limit=10, path=self.path)
        tts._create_interaction({"model": "x"})
        tts._create_interaction({"model": "x"})
        self.assertEqual(tts.budget.used, 2)


class TestTtsApiVersion(unittest.TestCase):
    def test_voice_requests_are_pinned_to_v1beta(self):
        from voice.tts import GeminiTTS
        tts = GeminiTTS(api_key=None)
        tts.client = mock.MagicMock()
        tts._create_interaction({"model": "gemini-3.8-flash-tts"})
        self.assertEqual(tts.client.interactions.create.call_args.kwargs["api_version"], "v1beta")

    def test_older_sdk_without_the_argument_still_works(self):
        from voice.tts import GeminiTTS
        calls = []

        def create(**kw):
            if "api_version" in kw:
                raise TypeError("create() got an unexpected keyword argument 'api_version'")
            calls.append(kw)
            return "ok"

        tts = GeminiTTS(api_key=None)
        tts.client = SimpleNamespace(interactions=SimpleNamespace(create=create))
        self.assertEqual(tts._create_interaction({"model": "m"}), "ok")
        self.assertEqual(tts._create_interaction({"model": "m"}), "ok")   # remembered: no second TypeError
        self.assertEqual(len(calls), 2)
        self.assertFalse(tts._api_version_supported)


class TestRaceRadar(unittest.TestCase):
    def run_laps(self, radar, gaps, position=5, names=("Ahead Driver", "Behind Driver")):
        for i, (a, b) in enumerate(gaps, start=10):
            radar.sample(i, snap(position, a, b, names), pace=90.0)
        return 10 + len(gaps) - 1

    def test_car_closing_from_behind_is_a_threat_with_a_prediction(self):
        r = RaceRadar()
        lap = self.run_laps(r, [(5.0, 2.4), (5.0, 2.0), (5.0, 1.6)])
        self.assertAlmostEqual(r.behind.rate, -0.4, places=2)
        reasons = r.candidates(lap, laps_to_go=10)
        self.assertEqual(reasons[0].key, "threat:Behind Driver")
        self.assertIn("closing 0.4s/lap", reasons[0].text)
        self.assertIn("on your gearbox in ~3 laps", reasons[0].text)
        # announced -> not repeated next lap
        r.mark_announced([x.key for x in reasons], lap)
        r.sample(lap + 1, snap(5, 5.0, 1.3), pace=90.0)
        self.assertFalse(any(x.key.startswith("threat:") for x in r.candidates(lap + 1, 9)))

    def test_closing_car_that_runs_out_of_laps(self):
        r = RaceRadar()
        lap = self.run_laps(r, [(9.0, 2.4), (9.0, 2.2), (9.0, 2.0)])
        reasons = r.candidates(lap, laps_to_go=3)
        self.assertIn("runs out of laps", reasons[0].text)

    def test_catching_the_car_ahead(self):
        r = RaceRadar()
        lap = self.run_laps(r, [(3.0, 8.0), (2.5, 8.0), (2.0, 8.0)])
        reasons = r.candidates(lap, laps_to_go=8)
        self.assertTrue(any(x.key == "catch:Ahead Driver" for x in reasons))

    def test_lapped_cars_and_new_neighbours_are_not_fights(self):
        r = RaceRadar()
        lap = self.run_laps(r, [(5.0, 95.0), (5.0, 93.0), (5.0, 91.0)])
        self.assertTrue(r.behind.lapped)
        self.assertEqual(r.candidates(lap, 10), [])
        # someone else is behind now: no trend yet
        r.sample(lap + 1, snap(5, 5.0, 1.0, names=("Ahead Driver", "Someone New")), pace=90.0)
        self.assertIsNone(r.behind.rate)

    def test_brief_names_ahead_and_behind_unambiguously(self):
        r = RaceRadar()
        self.run_laps(r, [(0.9, 4.0), (0.8, 4.0), (0.6, 4.0)])
        text = r.brief(snap(5, 0.6, 4.0))
        self.assertIn("Car AHEAD: P4 Ahead Driver +0.6s", text)
        self.assertIn("Car BEHIND: P6 Behind Driver -4.0s", text)
        self.assertIn("YOU are closing", text)

    def test_our_pit_lap_restarts_the_trends(self):
        r = RaceRadar()
        self.run_laps(r, [(5.0, 2.4), (5.0, 2.0), (5.0, 1.6)])
        r.sample(13, snap(5, 20.0, 1.0), pace=90.0, dirty=True)
        self.assertIsNone(r.behind)


class TestPacePlan(unittest.TestCase):
    LAPS = [91.0, 90.4, 90.6, 90.5]

    def radar_with(self, gaps):
        r = RaceRadar()
        for i, (a, b) in enumerate(gaps, start=10):
            r.sample(i, snap(5, a, b), pace=90.0)
        return r

    def test_reference_pace_ignores_slow_laps(self):
        self.assertAlmostEqual(reference_pace([90.5, 120.0, 90.3, 90.7]), 90.5)

    def test_push_to_defend_gives_a_target_lap(self):
        r = self.radar_with([(9.0, 2.0), (9.0, 1.7), (9.0, 1.4)])
        plan = r.update_pace_plan(self.LAPS, laps_to_go=10)
        self.assertEqual(plan.mode, "PUSH_DEFEND")
        self.assertAlmostEqual(plan.target_lap, 90.5 - 0.3, places=2)
        self.assertIn("1:30.200", plan.text)
        self.assertIn("vs target", plan.text)

    def test_hold_when_already_catching_fast_enough(self):
        r = self.radar_with([(3.0, 9.0), (2.5, 9.0), (2.0, 9.0)])
        plan = r.update_pace_plan(self.LAPS, laps_to_go=10)
        self.assertEqual(plan.mode, "HOLD_ATTACK")

    def test_push_to_catch_when_not_closing_fast_enough(self):
        r = self.radar_with([(3.0, 9.0), (3.0, 9.0), (3.0, 9.0)])
        plan = r.update_pace_plan(self.LAPS, laps_to_go=6)
        self.assertEqual(plan.mode, "PUSH_ATTACK")
        self.assertLess(plan.target_lap, 90.5)

    def test_manage_when_gaps_are_safe(self):
        r = self.radar_with([(12.0, 8.0), (12.5, 8.5), (13.0, 9.0)])
        plan = r.update_pace_plan(self.LAPS, laps_to_go=10)
        self.assertEqual(plan.mode, "MANAGE")
        reasons = r.candidates(12, laps_to_go=10)
        self.assertTrue(any(x.key == "pace:MANAGE" for x in reasons))

    def test_small_fuel_save_that_skips_the_stop(self):
        r = self.radar_with([(12.0, 8.0), (12.5, 8.5), (13.0, 9.0)])
        plan = r.update_pace_plan(self.LAPS, laps_to_go=10, fuel_avg_burn=2.30, fuel_target_burn=2.18, needs_pit_stop=True)
        self.assertEqual(plan.mode, "SAVE")
        self.assertIn("2.18 L/lap", plan.text)
        # too big a saving to be realistic -> no save plan
        plan = r.update_pace_plan(self.LAPS, laps_to_go=10, fuel_avg_burn=2.30, fuel_target_burn=1.90, needs_pit_stop=True)
        self.assertNotEqual(plan.mode, "SAVE")


class TestSpeakGate(unittest.TestCase):
    """RaceEngineerApp._should_speak on a light stand-in (the real app opens audio devices)."""

    def make_app(self, used=0, limit=200, reserve=40):
        import main
        app = SimpleNamespace(
            tts_budget=DailyRequestBudget(daily_limit=limit, path=os.path.join(tempfile.mkdtemp(), "u.json")),
            tts_budget_reserve=reserve, proactive_updates=True, proactive_max_per_race=3,
            proactive_min_laps_between=2, proactive_quiet_update_sec=420.0,
            _proactive_calls=0, _last_proactive_lap=-99, _last_radio_time=0.0,
            state_manager=SimpleNamespace(radar=RaceRadar()),
        )
        if used:
            app.tts_budget.record(used)
        app._should_speak = main.RaceEngineerApp._should_speak.__get__(app)
        app._gate_pit_wall_update = main.RaceEngineerApp._gate_pit_wall_update.__get__(app)
        return app

    @staticmethod
    def ev(event_type, **details):
        return RaceEvent(event_type, EventPriority.MEDIUM, time.time(), "msg", details)

    def test_essential_calls_always_speak_even_on_an_empty_budget(self):
        app = self.make_app(used=200)
        self.assertTrue(app._should_speak(self.ev(EventType.BOX_THIS_LAP)))
        self.assertTrue(app._should_speak(self.ev(EventType.QUALI_LAP_COMPLETED, is_pb=True)))

    def test_optional_calls_stop_when_the_reserve_is_reached(self):
        app = self.make_app(used=165)
        self.assertFalse(app._should_speak(self.ev(EventType.RIVAL_PITTED)))
        self.assertFalse(app._should_speak(self.ev(EventType.QUALI_LAP_COMPLETED, is_pb=False)))
        self.assertTrue(self.make_app(used=100)._should_speak(self.ev(EventType.RIVAL_PITTED)))

    def test_leader_pitting_only_matters_near_the_front(self):
        app = self.make_app()
        self.assertFalse(app._should_speak(self.ev(EventType.LEADER_PITTED, player_position=8)))
        self.assertTrue(app._should_speak(self.ev(EventType.LEADER_PITTED, player_position=2)))

    def test_pit_wall_updates_are_spaced_and_capped(self):
        app = self.make_app()
        upd = lambda lap, keys, urgent=False: self.ev(EventType.PIT_WALL_UPDATE, lap=lap, keys=keys, urgent=urgent)
        self.assertTrue(app._should_speak(upd(5, ["threat:A"])))
        self.assertFalse(app._should_speak(upd(6, ["catch:B"])))              # too soon
        app._last_radio_time = 0.0
        self.assertTrue(app._should_speak(upd(7, ["catch:B"])))
        app._last_radio_time = 0.0
        self.assertTrue(app._should_speak(upd(8, ["threat:A2"], urgent=True)))  # urgent: one lap is enough
        app._last_radio_time = 0.0
        self.assertFalse(app._should_speak(upd(12, ["catch:C"])))             # cap of 3 reached
        app._last_radio_time = 0.0
        self.assertTrue(app._should_speak(upd(14, ["threat:C"])))             # a car closing in still gets through

    def test_routine_update_needs_a_long_quiet_spell(self):
        app = self.make_app()
        app._last_radio_time = time.time() - 60
        self.assertFalse(app._should_speak(self.ev(EventType.PIT_WALL_UPDATE, lap=9, keys=[])))
        app._last_radio_time = time.time() - 500
        self.assertTrue(app._should_speak(self.ev(EventType.PIT_WALL_UPDATE, lap=9, keys=[])))


class TestPitWallBrain(unittest.TestCase):
    def test_no_client_means_silence_not_the_raw_notes(self):
        from engineer.brain import RaceEngineerBrain
        brain = RaceEngineerBrain(api_key=None)
        brain.client = None
        ev = RaceEvent(EventType.PIT_WALL_UPDATE, EventPriority.MEDIUM, time.time(), "Lap 5 done ...", {})
        text, _, _ = brain.generate_event_advice(ev, "ctx")
        self.assertIsNone(text)


if __name__ == "__main__":
    unittest.main()
