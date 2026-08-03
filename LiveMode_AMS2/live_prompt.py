"""
Live Mode Prompt Templates
--------------------------
Contains prompt template builder and parsing logic for the live commentary LLM.
"""

import logging
import re

logger = logging.getLogger(__name__)

def build_system_prompt(
    *,
    prompt_template: str,
    commentator_name: str,
    commentator_style: str,
    user_race_context: str,
    target_words: int = 100,
) -> str:
    """Build the initial system instruction prompt."""
    return prompt_template.format(
        commentator_name=commentator_name,
        commentator_style=commentator_style,
        user_race_context=user_race_context or "No additional instructions from director.",
        target_words=target_words,
    )

def build_update_prompt(
    *,
    prompt_template: str,
    track_name: str,
    current_lap: int | str,
    total_laps: int | str,
    leader_name: str,
    standings: str,
    recent_events_summary: str,
) -> str:
    """Build the periodic update prompt."""
    return prompt_template.format(
        track_name=track_name,
        current_lap=current_lap,
        total_laps=total_laps,
        leader_name=leader_name,
        standings=standings or "Not yet available.",
        recent_events_summary=recent_events_summary or "None yet — this is near the start.",
    )


def parse_commentary_lines(raw_text: str) -> list[dict]:
    """Parse the LLM output into structured lines of emotion and text."""
    lines = []
    
    logger.debug(f"\n--- RAW LLM OUTPUT ---\n{raw_text}\n----------------------")
    
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        emotion = ""
        text = line
        
        # Match emotion tag (e.g. [excited])
        emotion_match = re.search(r'^\[([^\]]+)\]', text)
        if emotion_match:
            emotion = emotion_match.group(1).strip()
            text = text[emotion_match.end():].strip()
            # Strip trailing colons, asterisks, brackets again
            text = re.sub(r'^[\:\*\]\s]+', '', text)
            
        if text:
            lines.append({
                "emotion": emotion,
                "text": text,
            })

    # Fallback: if no tagged lines found, treat entire text as valid output
    if not lines and raw_text.strip():
        lines.append({
            "emotion": "",
            "text": raw_text.strip(),
        })
    
    return lines
