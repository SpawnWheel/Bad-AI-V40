import logging
import os
import datetime
from typing import List, Dict, Any
from google import genai
from google.genai import types

from .config import LiveModeConfig
from .event_queue import LiveEvent
from .live_prompt import build_system_prompt, build_update_prompt, parse_commentary_lines

logger = logging.getLogger(__name__)

class CommentaryEngine:
    def __init__(self, config: LiveModeConfig):
        self.config = config
        self.client = None
        self.chat_session = None
        
        # Setup transcript logger
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Logs")
        os.makedirs(log_dir, exist_ok=True)
        self.transcript_file = os.path.join(log_dir, f"llm_transcript_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    def _log_transcript(self, prefix: str, content: str):
        try:
            with open(self.transcript_file, "a", encoding="utf-8") as f:
                f.write(f"\n=== {prefix} === [{datetime.datetime.now().strftime('%H:%M:%S')}]\n{content}\n")
        except Exception as e:
            logger.error(f"Failed to write to transcript log: {e}")

    def initialize(self):
        self.client = genai.Client(api_key=self.config.gemini_api_key)
        
        # Build System Instructions
        system_prompt = build_system_prompt(
            prompt_template=self.config.get_system_prompt_template(),
            commentator_name=self.config.commentator_name,
            commentator_style=self.config.commentator_style,
            user_race_context=self.config.user_race_context,
            target_words=self.config.target_words
        )
        
        self._log_transcript("SYSTEM PROMPT (INITIALIZATION)", system_prompt)

        thinking_config = None
        thinking_level = (self.config.thinking_level or "HIGH").upper()
        if thinking_level != "NONE":
            thinking_config = types.ThinkingConfig(
                include_thoughts=True,
                thinking_level=thinking_level
            )
            
        config = types.GenerateContentConfig(
            max_output_tokens=self.config.max_output_tokens,
            thinking_config=thinking_config,
            system_instruction=system_prompt
        )

        self.chat_session = self.client.chats.create(
            model=self.config.llm_model,
            config=config
        )

    def _build_context_events_text(self, context_events: List[LiveEvent]) -> str:
        if not context_events:
            return ""
        # Take the last 20 events at most
        events_to_show = context_events[-20:]
        lines = []
        for event in events_to_show:
            lines.append(f"[{event.category}] {event.message}")
        return "\n".join(lines)

    def generate_commentary(self, events: List[LiveEvent], race_context: Dict[str, Any], context_events: List[LiveEvent]) -> tuple[List[Dict[str, str]], int]:
        recent_events_summary = self._build_context_events_text(context_events)

        update_prompt = build_update_prompt(
            prompt_template=self.config.get_update_prompt_template(),
            track_name=race_context.get("track_name", "Unknown Track"),
            current_lap=race_context.get("current_lap", 1),
            total_laps=race_context.get("total_laps", 0),
            leader_name=race_context.get("leader_name", "Unknown Leader"),
            standings=race_context.get("standings", ""),
            recent_events_summary=recent_events_summary
        )

        self._log_transcript("UPDATE PROMPT", update_prompt)
        total_tokens = 0

        try:
            response = self.chat_session.send_message(update_prompt)

            full_text = ""
            if response.candidates:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    # Filter out thought parts
                    full_text = "".join([p.text for p in candidate.content.parts if p.text and getattr(p, 'thought', None) is None])
            
            if response.usage_metadata:
                total_tokens = response.usage_metadata.total_token_count

            self._log_transcript("RESPONSE", full_text)

            parsed_lines = parse_commentary_lines(full_text)
            return parsed_lines, total_tokens

        except Exception as e:
            logger.error(f"Error generating commentary: {e}")
            self._log_transcript("ERROR", str(e))
            return [], 0
