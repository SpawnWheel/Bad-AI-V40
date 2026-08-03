import time
import threading
import logging
from typing import List, Dict, Any, Optional

from .config import LiveModeConfig, load_config
from .event_queue import EventQueue
from .commentary_engine import CommentaryEngine
from .tts_engine import TTSEngine
from .audio_player import AudioPlayer
from .live_telemetry import LiveTelemetryReader

logger = logging.getLogger(__name__)

class LiveOrchestrator:
    def __init__(self, config: Optional[LiveModeConfig] = None):
        if config is None:
            self.config = load_config()
        else:
            self.config = config

        self.state = 'IDLE'
        self.current_commentary = ''
        self.current_event_text = ''
        self.error_message = ''
        self.is_running = False
        self.is_paused = False
        self.total_tokens_used = 0
        self.commentary_history: List[str] = []

        self.event_queue: Optional[EventQueue] = None
        self.telemetry_reader: Optional[LiveTelemetryReader] = None
        self.commentary_engine: Optional[CommentaryEngine] = None
        self.tts_engine: Optional[TTSEngine] = None
        self.audio_player: Optional[AudioPlayer] = None
        self._orchestration_thread: Optional[threading.Thread] = None
        self._pipeline_busy = False
        self._pipeline_timestamps: List[float] = []

    def update_config(self, new_config: LiveModeConfig):
        """Hot-swap the active configuration."""
        self.config = new_config
        if self.commentary_engine:
            self.commentary_engine.config = new_config
        if self.tts_engine:
            self.tts_engine.config = new_config
        if self.event_queue:
            self.event_queue.ttl_multiplier = new_config.event_ttl_multiplier
        if self.telemetry_reader:
            self.telemetry_reader.leaderboard_interval_seconds = new_config.leaderboard_interval_minutes * 60.0

    def pause(self):
        """Pause the orchestration loop."""
        self.is_paused = True
        logger.info("Orchestrator paused.")

    def resume(self):
        """Resume the orchestration loop."""
        self.is_paused = False
        logger.info("Orchestrator resumed.")

    def start(self):
        try:
            self.state = 'CONNECTING'
            self.error_message = ''
            self.total_tokens_used = 0
            self.commentary_history.clear()
            self._pipeline_timestamps.clear()
            
            # Initialize engines
            self.commentary_engine = CommentaryEngine(self.config)
            self.commentary_engine.initialize()
            
            self.tts_engine = TTSEngine(self.config)
            self.tts_engine.initialize()
            
            # Create Event Queue
            self.event_queue = EventQueue(ttl_multiplier=self.config.event_ttl_multiplier)
            
            # Create and connect telemetry
            self.telemetry_reader = LiveTelemetryReader(
                self.event_queue, 
                ttl_multiplier=self.config.event_ttl_multiplier,
                leaderboard_interval_seconds=self.config.leaderboard_interval_minutes * 60.0
            )
            
            # Connect telemetry (retry loop up to 30s)
            connected = False
            for _ in range(30):
                if self.telemetry_reader.connect():
                    connected = True
                    break
                time.sleep(1.0)
                
            if not connected:
                self.error_message = 'Failed to connect to AMS2 telemetry after 30 seconds.'
                self.state = 'IDLE'
                return
                
            self.audio_player = AudioPlayer()
            
            self.telemetry_reader.start()
            
            self.is_running = True
            self.is_paused = False
            self._orchestration_thread = threading.Thread(target=self._orchestration_loop, daemon=True)
            self._orchestration_thread.start()
            
            self.state = 'IDLE'
            logger.info("Orchestrator started successfully.")
            
        except Exception as e:
            self.error_message = f"Error starting orchestrator: {e}"
            logger.error(self.error_message)
            self.state = 'IDLE'
            self.is_running = False

    def stop(self):
        self.is_running = False
        if self.telemetry_reader:
            self.telemetry_reader.stop()
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.shutdown()
        if self._orchestration_thread:
            self._orchestration_thread.join(timeout=2.0)
        self.state = 'IDLE'
        logger.info("Orchestrator stopped.")

    def _pipeline_worker(self, events, context_events, race_context):
        """Run LLM + TTS on a background thread so the main loop stays responsive."""
        try:
            self.state = 'GENERATING_COMMENTARY'
            
            self.current_event_text = " | ".join([f"[{e.category}] {e.message}" for e in events])
            
            logger.info(f"Generating commentary for: {self.current_event_text[:80]}")
            commentary_lines, token_count = self.commentary_engine.generate_commentary(events, race_context, context_events)
            self.total_tokens_used += token_count
            logger.info(f"LLM returned {len(commentary_lines)} lines, {token_count} tokens")
            
            if not self.is_running:
                logger.info("Stopped - aborting after LLM")
                return
            
            if commentary_lines:
                for i, line in enumerate(commentary_lines):
                    logger.debug(f"Line {i}: [{line.get('emotion','')}] {line.get('text','')[:60]}")
                
                # Update UI immediately so user can read while TTS generates
                full_text = " ".join([line.get("text", "") for line in commentary_lines])
                self.current_commentary = full_text
                self.commentary_history.append(full_text)
                
                self.state = 'GENERATING_TTS'
                logger.info("Generating TTS...")
                audio_data = self.tts_engine.generate_audio(commentary_lines)
                logger.info(f"TTS returned {len(audio_data)} bytes")
                
                if not self.is_running:
                    logger.info("Stopped - aborting after TTS")
                    return
                
                if audio_data:
                    self.state = 'PLAYING'
                    self.audio_player.play(audio_data)
                    logger.info(f"Audio queued for playback ({len(audio_data)} bytes, ~{len(audio_data)/48000:.1f}s)")
                else:
                    logger.warning("TTS returned empty audio!")
                    self.state = 'ERROR_COOLDOWN'
            else:
                logger.warning("LLM returned no commentary lines!")
                self.state = 'ERROR_COOLDOWN'
                
        except Exception as e:
            self.error_message = f"Pipeline error: {e}"
            logger.error(self.error_message, exc_info=True)
            self.state = 'ERROR_COOLDOWN'
        finally:
            self._pipeline_busy = False

    def _orchestration_loop(self):
        last_rerank_time = time.time()
        last_pipeline_finish = 0.0
        
        while self.is_running:
            try:
                now = time.time()
                
                if now - last_rerank_time >= 1.0:
                    self.event_queue.re_rank()
                    last_rerank_time = now
                
                if self.state == 'PLAYING' and self.audio_player.time_remaining() <= 0.1:
                    self.state = 'IDLE'
                    self.current_commentary = ''
                    self.current_event_text = ''
                    last_pipeline_finish = now
                elif self.state == 'ERROR_COOLDOWN':
                    self.state = 'IDLE'
                    last_pipeline_finish = now
                
                if self.is_paused:
                    time.sleep(0.2)
                    continue

                time_rem = self.audio_player.time_remaining()
                cooldown_seconds = 60.0 / max(1, self.config.max_requests_per_minute)
                cooldown_ok = (now - last_pipeline_finish) >= cooldown_seconds
                
                self._pipeline_timestamps = [t for t in self._pipeline_timestamps if now - t < 60.0]
                rate_limit_ok = len(self._pipeline_timestamps) < self.config.max_requests_per_minute

                if self.state == 'IDLE' and not self._pipeline_busy and cooldown_ok:
                    if not rate_limit_ok:
                        if self.state != 'RATE_LIMITED':
                            self.state = 'RATE_LIMITED'
                            logger.info(f"Rate limited! ({len(self._pipeline_timestamps)} requests in last 60s)")
                        time.sleep(0.5)
                        continue
                        
                    events = self.event_queue.pop_top()
                    
                    if events:
                        context_events = self.event_queue.get_context_events()
                        race_context = self.telemetry_reader.get_race_context()
                        
                        self._pipeline_busy = True
                        self._pipeline_timestamps.append(now)
                        logger.info(f"Launching pipeline (time_rem={time_rem:.1f}s, queue={self.event_queue.size()}, events={len(events)}, context={len(context_events)})")
                        worker = threading.Thread(
                            target=self._pipeline_worker,
                            args=(events, context_events, race_context),
                            daemon=True
                        )
                        worker.start()
                    
                time.sleep(0.2)
                
            except Exception as e:
                self.error_message = f"Orchestration loop error: {e}"
                logger.error(self.error_message)
                time.sleep(1.0)

    def get_queue_display(self) -> List[Dict[str, Any]]:
        if not self.event_queue:
            return []
        
        top_events = self.event_queue.get_top_n(5)
        return [
            {
                'category': event.category,
                'message': event.message,
                'score': score
            }
            for event, score in top_events
        ]

    def get_status_display(self) -> Dict[str, Any]:
        queue_size = self.event_queue.size() if self.event_queue else 0
        return {
            'state': self.state,
            'commentary': self.current_commentary,
            'event': self.current_event_text,
            'error': self.error_message,
            'queue_size': queue_size,
            'total_tokens': self.total_tokens_used,
            'is_paused': self.is_paused
        }

def main():
    from .live_ui import LiveModeUI
    config = load_config()
    orchestrator = LiveOrchestrator(config)
    ui = LiveModeUI(orchestrator)
    ui.run()

if __name__ == '__main__':
    main()
