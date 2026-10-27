"""
AI Race Engineer - Main Application Entry Point
Connects to Automobilista 2 or RaceRoom shared memory (auto-detected), monitors race telemetry,
and provides live radio engineering powered by Gemini 3.8 Flash and Gemini 3.8 Flash TTS,
with a fully config-driven engineer persona (accent, personality, voice) and situational emotions.
"""

import json
import os
import random
import sys
import time
import signal
import threading
import yaml
from pathlib import Path
from typing import Optional

from utils.logging import logger
from utils.interaction_logger import INTERACTIONS_LOG_FILE
from telemetry import TelemetryCollector, TelemetrySnapshot, GameState, SIM_DISPLAY_NAMES
from race import RaceStateManager, RaceEvent, EventPriority, EventType
from engineer import RaceEngineerBrain, MessageScheduler, build_telemetry_context
from engineer.persona import load_persona
from voice import GeminiTTS, AudioPlayer, PushToTalkManager, TTS_SAMPLE_RATE
from voice.stream import RadioTransmission, ReplyStreamSink


LAUNCHER_SETTINGS_FILE = "config.launcher.json"


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Merges overrides into base in place; nested sections are merged key by key."""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class RaceEngineerApp:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.config = self._load_config(config_path)

        # Config sections
        gemini_cfg = self.config.get("gemini", {})
        ptt_cfg = self.config.get("push_to_talk", {})
        eng_cfg = self.config.get("engineer", {})
        telem_cfg = self.config.get("telemetry", {})

        # 1. API Key resolution
        self.api_key = (
            gemini_cfg.get("api_key")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )

        # Engineer persona (personality, accent, voice, emotional range) -- all from config.yaml
        self.persona = load_persona(self.config)
        self.personality = self.persona.personality
        self.accent = self.persona.accent
        self.gender = self.persona.gender
        self.delivery = self.persona.baseline_delivery
        self.emotion_mode = eng_cfg.get("emotion_mode", "dynamic")
        self.speak_only_when_spoken_to = eng_cfg.get("speak_only_when_spoken_to", True)
        self.allow_tactical_alerts = eng_cfg.get("allow_tactical_alerts_when_silent", True)

        incidents_cfg = self.config.get("incidents", {})
        strategy_cfg = self.config.get("strategy", {})

        # 2. Subsystems
        self.brain = RaceEngineerBrain(
            api_key=self.api_key,
            model_name=gemini_cfg.get("thinking_model", "gemini-3.8-flash"),
            personality=self.personality,
            accent=self.accent,
            delivery=self.delivery,
            verbosity=eng_cfg.get("verbosity", "balanced"),
            thinking_level=gemini_cfg.get("thinking_level", "HIGH"),
            thinking_budget=int(gemini_cfg["thinking_budget"]) if "thinking_budget" in gemini_cfg else None,
            max_output_tokens=int(gemini_cfg.get("max_output_tokens", 8192)),
            persona=self.persona,
            temperature=float(gemini_cfg.get("temperature", 1.0)),
            report_temperature=(
                float(gemini_cfg["report_temperature"]) if "report_temperature" in gemini_cfg else None
            ),
            fast_model=gemini_cfg.get("fast_model") or None,
            fast_thinking_level=gemini_cfg.get("fast_thinking_level") or None,
            fallback_model=gemini_cfg.get("fallback_model") or None,
            structured_output=bool(gemini_cfg.get("structured_output", True)),
            request_timeout_sec=float(gemini_cfg.get("request_timeout_sec", 20)),
            notebook_size=int(eng_cfg.get("notebook_size", 8)),
            scoped_system_prompt=bool(gemini_cfg.get("scoped_system_prompt", True)),
        )

        tts_cfg = self.config.get("tts", {}) or {}
        self.tts = GeminiTTS(
            api_key=self.api_key,
            model_name=tts_cfg.get("model", gemini_cfg.get("tts_model", "gemini-3.8-flash-tts")),
            voice_name=self.persona.voice.prebuilt or gemini_cfg.get("tts_voice", "Charon"),
            persona=self.persona,
            fallback_models=tts_cfg.get("fallback_models", ["gemini-3.8-flash-lite-tts"]),
            legacy_model=tts_cfg.get("legacy_fallback_model", "gemini-3.1-flash-tts-preview"),
            radio_context=bool(tts_cfg.get("radio_context", True)),
            request_timeout_sec=float(tts_cfg.get("request_timeout_sec", 20)),
            stream_first_audio_timeout_sec=float((self.config.get("streaming", {}) or {}).get("first_audio_timeout_sec", 6)),
            stream_stall_timeout_sec=float((self.config.get("streaming", {}) or {}).get("stall_timeout_sec", 4)),
        )

        # Streaming: speak while Gemini is still writing / synthesising (config: streaming.*)
        stream_cfg = self.config.get("streaming", {}) or {}
        self.streaming_enabled = bool(stream_cfg.get("enabled", True))
        self.stream_split_first_sentence = bool(stream_cfg.get("split_first_sentence", True))
        self.stream_min_first_words = int(stream_cfg.get("min_first_sentence_words", 5))
        self.stream_prebuffer_sec = float(stream_cfg.get("prebuffer_ms", 150)) / 1000.0

        self.player = AudioPlayer(
            volume=eng_cfg.get("volume", 1.2),
            radio_effect=eng_cfg.get("radio_effect", False),
            gain_boost=eng_cfg.get("gain_boost", 1.6)
        )

        self.scheduler = MessageScheduler(
            quiet_zone_braking=eng_cfg.get("quiet_zone_braking", True),
            min_interval_sec=3.5
        )

        self.state_manager = RaceStateManager(
            verbosity=eng_cfg.get("verbosity", "balanced"),
            incident_speed_threshold_kmh=float(incidents_cfg.get("speed_threshold_kmh", 20.0)),
            incident_duration_sec=float(incidents_cfg.get("duration_sec", 2.0)),
            incident_cooldown_sec=float(incidents_cfg.get("cooldown_per_car_sec", 60.0)),
            incident_retention_sec=float(incidents_cfg.get("memory_retention_sec", 120.0)),
            estimated_pit_loss_sec=float(strategy_cfg.get("estimated_pit_loss_sec", 45.0)),
            fuel_margin_liters=float(strategy_cfg.get("fuel_margin_liters", 1.0)),
            fuel_margin_laps=float(strategy_cfg.get("fuel_margin_laps", 0.0) or 0.0),
            predict_leader_pit_stop=bool(strategy_cfg.get("predict_leader_pit_stop", True)),
            pit_lap_preference=str(strategy_cfg.get("pit_lap_preference", "latest")),
            pit_arrival_reserve_laps=float(strategy_cfg.get("pit_arrival_reserve_laps", 0.6)),
            box_reminder_sec=float(strategy_cfg.get("box_reminder_before_pit_entry_sec", 20)),
            timed_race_extra_laps=strategy_cfg.get("timed_race_extra_laps", "auto"),
        )

        invert_bias = bool(telem_cfg.get("brake_bias_is_rear_share", False))
        self.brake_bias_note = "" if telem_cfg.get("brake_bias_verified", False) else (
            "bias convention from the sim is unverified - if the driver quotes a different number from his dash, trust his")
        self.collector = TelemetryCollector(
            poll_rate_hz=telem_cfg.get("poll_rate_hz", 5),
            shm_tag=telem_cfg.get("shm_name", "$pcars2$"),
            tyre_pressure_unit=str(telem_cfg.get("tyre_pressure_unit", "auto")),
            invert_brake_bias=invert_bias,
            sim=str(telem_cfg.get("sim", "auto")),
            r3e_data_path=telem_cfg.get("r3e_data_path") or None,
            r3e_brake_bias_is_rear_share=bool(telem_cfg.get("r3e_brake_bias_is_rear_share", True)),
        )
        self.race_poll_rate_hz = int(telem_cfg.get("poll_rate_hz", 5))

        self.ptt = PushToTalkManager(
            mode=ptt_cfg.get("mode", "auto"),
            joystick_index=ptt_cfg.get("joystick_index", 0),
            button_index=ptt_cfg.get("button_index", 0),
            keyboard_key=ptt_cfg.get("keyboard_key", "caps_lock"),
            hold_threshold_sec=ptt_cfg.get("hold_threshold_sec", 0.35),
            beep_feedback=ptt_cfg.get("beep_feedback", True),
            on_audio_ready=self._handle_driver_audio,
            on_tap=self._handle_race_update_request,
            beep_callback=self.player.play_radio_beep
        )

        self._running = False
        self._speech_thread: Optional[threading.Thread] = None

        # What the engineer learns about the car / setup work goes into the persistent setup journal
        self.brain.knowledge_sink = self._on_engineer_knowledge

    def _on_engineer_knowledge(self, kind: str, text: str):
        pm = self.state_manager.practice_manager
        snap = self.collector.latest
        car = (snap.car_name if snap else "") or pm.car
        if kind == "car_fact":
            pm.journal.add_car_fact(car, text)
        elif kind == "setup_change":
            # a change made in the box applies to the run about to start
            run_no = pm.current_run_number + (1 if pm.is_car_on_track else 0)
            pm.journal.add_change(pm.journal_key, text, run_no)
            pm.record_setup_change(text)
        elif kind == "setup_conclusion":
            pm.journal.add_conclusion(pm.journal_key, text)

    def _load_config(self, path: str) -> dict:
        config_file = Path(path)
        if not config_file.exists() and path == "config.yaml" and Path("config.example.yaml").exists():
            import shutil
            try:
                shutil.copy("config.example.yaml", config_file)
                logger.info("Initialized 'config.yaml' from 'config.example.yaml'.")
            except Exception as e:
                logger.warning(f"Could not copy config.example.yaml to config.yaml: {e}")
        config = {}
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Error loading config file '{path}': {e}")

        # Settings saved from the BAD AI launcher sit on top of config.yaml
        launcher_file = config_file.with_name(LAUNCHER_SETTINGS_FILE)
        if launcher_file.exists():
            try:
                with open(launcher_file, "r", encoding="utf-8") as f:
                    _deep_merge(config, json.load(f) or {})
                logger.info(f"Applied launcher settings from '{launcher_file.name}'.")
            except Exception as e:
                logger.error(f"Error loading launcher settings '{launcher_file}': {e}")
        return config

    def _get_current_context_string(self) -> str:
        snap = self.collector.latest
        fuel = self.state_manager.get_current_fuel_status()
        tires = self.state_manager.get_current_tire_status()
        gaps = self.state_manager.get_current_gap_analysis()
        strategy = self.state_manager.get_current_strategy()
        timing_table = self.state_manager.get_timing_board_string()
        incidents = self.state_manager.get_recent_incidents_summary()
        storylines = self.state_manager.get_race_storylines_summary()
        lap_history = self.state_manager.get_lap_times()
        practice_debrief = self.state_manager.get_practice_debrief()
        quali_summary = self.state_manager.get_quali_summary()
        try:
            extras = self.state_manager.get_context_extras()
        except Exception as e:
            logger.debug(f"context extras failed: {e}")
            extras = {}
        if self.brake_bias_note:
            extras["brake_bias_note"] = self.brake_bias_note
        return build_telemetry_context(
            snap, fuel, tires, gaps,
            timing_board_table=timing_table,
            strategy=strategy,
            incidents_summary=incidents,
            lap_times=lap_history,
            practice_debrief=practice_debrief,
            quali_summary=quali_summary,
            race_storylines_summary=storylines,
            extras=extras,
        )

    def _resolve_emotion(self, dynamic_emotion: str) -> str:
        if self.emotion_mode == "dynamic":
            return dynamic_emotion
        elif self.emotion_mode == "calm":
            return "calm and composed"
        elif self.emotion_mode == "urgent":
            return "urgent and high intensity"
        return dynamic_emotion

    def _handle_race_event(self, event: RaceEvent):
        """Called when RaceStateManager fires a race event."""
        logger.info(f"Event detected [{event.event_type}]: {event.message}")

        # Every event nudges the engineer's mood, even ones he doesn't call out
        self.brain.note_event(event.event_type)

        # Yellow flags and competitor incidents are NOT called out proactively over radio.
        # They are tracked in background memory for Push-to-Talk queries (e.g. "Who was stopped on track?").
        if event.event_type in (EventType.FLAG_YELLOW, EventType.FLAG_DOUBLE_YELLOW, EventType.RIVAL_INCIDENT):
            return

        # Interrupt current speech if critical
        if event.is_critical():
            self.player.interrupt()

        if event.event_type == EventType.FLAG_CHEQUERED:
            # Purge any queued tactical messages upon chequered flag
            self.scheduler.clear()

        # Tactical alerts bypass silence mode if allowed
        tactical_alerts = {
            EventType.PRACTICE_BRIEFING,
            EventType.QUALI_LAP_COMPLETED,
            EventType.BOX_NEXT_LAP,
            EventType.BOX_THIS_LAP,
            EventType.BOX_REMINDER,
            EventType.LEADER_PITTED,
            EventType.LEADER_FINISHED,
            EventType.PIT_EXIT_PROXIMITY,
            EventType.FUEL_EMERGENCY,
            EventType.FUEL_CRITICAL,
            EventType.TIME_WARNING_10MIN,
            EventType.TIME_WARNING_5MIN,
            EventType.TIME_WARNING_2MIN,
            EventType.TIME_WARNING_1MIN,
            EventType.LAPS_WARNING_5LAPS,
            EventType.LAPS_WARNING_2LAPS,
            EventType.FINAL_LAP,
            EventType.FLAG_BLACK,
            EventType.FLAG_CHEQUERED,
            EventType.COLLISION_DAMAGE,
            EventType.TIRE_PUNCTURE_RISK,
            EventType.WEATHER_RAIN_STARTED,
            EventType.WEATHER_RAIN_INTENSIFIED,
            EventType.WEATHER_RAIN_STOPPED,
        }

        # If configured to only speak when spoken to, suppress unprompted proactive speech
        # UNLESS allow_tactical_alerts is true and this is a tactical alert
        if self.speak_only_when_spoken_to:
            if not (self.allow_tactical_alerts and event.event_type in tactical_alerts):
                return

        context_str = self._get_current_context_string()

        def process_event():
            if event.event_type == EventType.PRACTICE_BRIEFING:
                advice_res = self.brain.generate_practice_briefing(context_str)
            else:
                advice_res = self.brain.generate_event_advice(event, context_str)
            if len(advice_res) == 3:
                radio_text, detected_emotion, delivery = advice_res
            else:
                radio_text, detected_emotion = advice_res
                delivery = ""
            emotion = self._resolve_emotion(detected_emotion)
            if radio_text:
                try:
                    self.scheduler.push(
                        text=radio_text,
                        priority=event.priority,
                        event_type=event.event_type,
                        emotion=emotion,
                        delivery=delivery
                    )
                except TypeError:
                    self.scheduler.push(
                        text=radio_text,
                        priority=event.priority,
                        event_type=event.event_type,
                        emotion=emotion
                    )

        threading.Thread(target=process_event, name="EventAI-Worker", daemon=True).start()

    def _handle_session_reset(self, session_group: str):
        """Called by RaceStateManager whenever a new session starts or an existing session restarts."""
        logger.info(f"Session reset triggered for [{session_group}]. Flushing message queue and resetting brain history.")
        self.scheduler.clear()
        self.player.interrupt()
        snap = self.state_manager.current_snapshot or self.collector.latest
        if snap is not None:
            self.brain.set_context_key(f"{snap.car_name}|{snap.track_name}|{snap.track_variation}")
        self.brain.reset_session()
        self.brain.set_session_group(session_group)
        self.tts.reset_session()
        self.collector.reset_tyre_cache()

        # Dynamic telemetry polling rate based on session type:
        # 10Hz for Practice (high-frequency suspension, tyre slip, bottoming diagnostics)
        # 2Hz for Quali and Race
        if session_group == "PRACTICE":
            self.collector.set_poll_rate_hz(10)
        else:
            self.collector.set_poll_rate_hz(max(2, self.race_poll_rate_hz))

    # ------------------------------------------------------------------ speaking
    def _new_transmission(self, label: str, t_start: Optional[float] = None) -> RadioTransmission:
        return RadioTransmission(
            self.tts, self.player, sample_rate=TTS_SAMPLE_RATE,
            prebuffer_sec=self.stream_prebuffer_sec, t_start=t_start, label=label,
        )

    def _speak(self, text: str, emotion: str, delivery: Optional[str], label: str = "radio",
               t_start: Optional[float] = None):
        """Speaks a finished line. Streaming mode starts playback on the first audio chunk."""
        if not text:
            return
        logger.speech(f"[{(emotion or '').upper()}] {text}")
        if self.streaming_enabled:
            tx = self._new_transmission(label, t_start)
            tx.say(text, emotion, delivery)
            tx.finish()
            tx.wait(timeout=90)
            return
        t0 = t_start or time.time()
        audio = self.tts.synthesize(text, emotion=emotion, delivery=delivery)
        if audio is not None:
            logger.info(f"[LATENCY] {label}: first audio {time.time() - t0:.2f}s after trigger")
            self.player.play_pcm(audio, sample_rate=TTS_SAMPLE_RATE, block=True)

    def _ask_brain_and_speak(self, brain_call, label: str, t_start: float, after=None):
        """
        Runs a brain call and voices the reply. In streaming mode the brain streams its reply and
        the engineer starts talking as soon as the first full sentence (with its emotion) exists.
        """
        if not self.streaming_enabled:
            res = brain_call(None)
            text, detected_emotion, delivery = (res + (None,))[:3] if len(res) == 2 else res
            if after:
                after()
            self._speak(text, self._resolve_emotion(detected_emotion), delivery, label=label, t_start=t_start)
            return

        tx = self._new_transmission(label, t_start)
        sink = ReplyStreamSink(
            tx, emotion_resolver=self._resolve_emotion,
            split_first_sentence=self.stream_split_first_sentence,
            min_first_sentence_words=self.stream_min_first_words,
        )
        try:
            res = brain_call(sink)
            text, detected_emotion, delivery = (res + (None,))[:3] if len(res) == 2 else res
            if after:
                after()
            if text:
                logger.speech(f"[{self._resolve_emotion(detected_emotion).upper()}] {text}")
            if not sink.started and text:
                # Nothing was streamed (e.g. error fallback line) -> speak the returned text
                tx.say(text, self._resolve_emotion(detected_emotion), delivery)
        finally:
            tx.finish()
            tx.wait(timeout=90)

    def _handle_driver_audio(self, wav_bytes: bytes):
        """Called when driver releases push-to-talk button."""
        t_start = time.time()
        snap = self.collector.latest
        if not snap or snap.game_state == GameState.GAME_EXITED:
            context_str = "Driver is testing radio before the sim (Automobilista 2 or RaceRoom) has started. Respond in character as the race engineer doing a radio check, confirming reception and standing by."
        elif snap.game_state == GameState.GAME_FRONT_END:
            context_str = "Driver is in the game menus / paddock before entering the car. Respond in character as the race engineer standing by."
        else:
            context_str = self._get_current_context_string()

        logger.info("Driver asking question over radio. Analyzing audio with Gemini...")
        self._ask_brain_and_speak(
            lambda speaker: self.brain.respond_to_driver_audio(wav_bytes, context_str, speaker=speaker),
            label="driver call", t_start=t_start,
        )

    def _handle_race_update_request(self):
        """Called when driver taps the radio button for a comprehensive SitRep."""
        t_start = time.time()
        snap = self.collector.latest
        if not snap or snap.game_state == GameState.GAME_EXITED:
            msg = random.choice([
                "Radio check, loud and clear. Standing by for the car.",
                "Got you five by five. Waiting on the telemetry link.",
                "Reading you fine. Load up when you're ready.",
            ])
            self._speak(msg, "relaxed, unhurried", "casual radio check", label="radio check", t_start=t_start)
            return
        elif snap.game_state == GameState.GAME_FRONT_END:
            msg = random.choice([
                "Loud and clear. We're in the garage, ready when you are.",
                "Radio's good. Jump in when you're ready.",
                "Copy, reading you. Standing by in the garage.",
            ])
            self._speak(msg, "relaxed, unhurried", "casual radio check", label="radio check", t_start=t_start)
            return

        context_str = self._get_current_context_string()
        sess_grp = self.state_manager.current_session_group

        logger.info(f"Driver tapped radio button -> Generating {sess_grp} Situation Report / Debrief...")
        after = self.state_manager.mark_practice_debrief_delivered if sess_grp == "PRACTICE" else None
        self._ask_brain_and_speak(
            lambda speaker: self.brain.generate_race_update(context_str, session_group=sess_grp, speaker=speaker),
            label=f"{sess_grp.lower()} sitrep", t_start=t_start, after=after,
        )

    def _speech_loop(self):
        """Dedicated background loop pulling and synthesizing scheduled engineer speech."""
        while self._running:
            snap = self.collector.latest
            msg = self.scheduler.poll_next_message(snap)

            if msg:
                # Proactive calls are fully written before they're queued, so the whole line goes
                # to the voice in one request (best intonation); streaming just starts it sooner.
                self._speak(msg.text, msg.emotion, getattr(msg, "delivery", None), label=f"alert:{msg.event_type}")

            time.sleep(0.1)

    def start(self):
        """Starts all background services."""
        self._running = True

        print("=" * 65)
        print(">>>  AI RACE ENGINEER - AUTOMOBILISTA 2 / RACEROOM (GEMINI 3.8 FLASH)")
        print(f">>>  ENGINEER: {self.persona.display_name} | {self.gender.capitalize()} {self.accent} | EMOTION: {self.emotion_mode.capitalize()} ({self.persona.expressiveness})")
        print(f">>>  VOICE: {self.tts.primary_model} | mode: {self.persona.voice.mode} | streaming: {'ON' if self.streaming_enabled else 'OFF'}")
        if self.speak_only_when_spoken_to:
            print(">>>  MODE: SPEAK ONLY WHEN SPOKEN TO (Silent until requested)")
        else:
            print(">>>  MODE: PROACTIVE & ON-DEMAND")
        print("=" * 65)

        logger.info(f"AI interaction log: {INTERACTIONS_LOG_FILE.resolve()}")

        if not self.api_key:
            logger.warning("No GEMINI_API_KEY detected in config.yaml or environment!")
            logger.warning("Please set GEMINI_API_KEY to enable AI reasoning and voice synthesis.")

        # Show detected controllers
        joysticks = PushToTalkManager.list_joysticks()
        if joysticks:
            logger.info("Detected Gaming Controllers / Steering Wheels:")
            for j in joysticks:
                logger.info(f"  [{j['index']}] {j['name']} (Buttons: {j['num_buttons']})")
        else:
            logger.info("No steering wheel detected yet. Keyboard PTT (Caps Lock) active.")

        # Resolve / design the engineer's voice up front so the first radio call isn't delayed
        if self.api_key:
            try:
                voice = self.tts.ensure_voice()
                logger.info(f"Engineer voice: {voice}")
            except Exception as e:
                logger.warning(f"Could not prepare engineer voice: {e}")

        # Wire up event listeners
        self.collector.register_listener(self.state_manager.on_telemetry)
        self.state_manager.register_event_listener(self._handle_race_event)
        self.state_manager.register_session_reset_listener(self._handle_session_reset)

        # Start telemetry collection (2Hz default)
        self.collector.start()

        # Start push-to-talk (Mic will ONLY turn on when button is held)
        self.ptt.start()

        # Start speech processor thread
        self._speech_thread = threading.Thread(target=self._speech_loop, name="SpeechLoop", daemon=True)
        self._speech_thread.start()

        logger.info(f"Telemetry polling set to {self.collector.poll_rate_hz}Hz (0.5s interval).")
        logger.info("Race Engineer is active and listening.")
        sim_pref = self.collector.sim_preference
        if sim_pref == "auto":
            logger.info("Launch Automobilista 2 or RaceRoom to begin - the sim is detected automatically.")
        else:
            logger.info(f"Launch {SIM_DISPLAY_NAMES.get(sim_pref, sim_pref)} to begin session (telemetry.sim = {sim_pref}).")
        logger.info("Hold steering wheel PTT button or [Caps Lock] to speak to your engineer.\n")

        # Keep main thread alive
        try:
            while self._running:
                time.sleep(1.0)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stops all threads cleanly."""
        if not self._running:
            return
        print("\n")
        logger.info("Shutting down AI Race Engineer...")
        if self.brain.usage_totals.get("calls"):
            logger.info(f"LLM usage this session: {self.brain._usage_summary()}")
        self._running = False
        self.player.interrupt()
        self.ptt.stop()
        self.collector.stop()
        logger.info("Shutdown complete. Good race!")


def main():
    app = RaceEngineerApp("config.yaml")

    def signal_handler(sig, frame):
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    app.start()


if __name__ == "__main__":
    main()
