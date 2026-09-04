import json
import os
import logging
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

GEMINI_VOICES = [
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe", 
    "Callirrhoe", "Charon", "Despina", "Enceladus", "Erinome", "Fenrir", "Gacrux", 
    "Iapetus", "Kore", "Laomedeia", "Leda", "Orus", "Puck", "Pulcherrima", 
    "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar", "Sulafat", "Umbriel", 
    "Vindemiatrix", "Zephyr", "Zubenelgenubi"
]

GEMINI_ACCENTS = [
    "",                             # None / Default
    "Neutral",
    "American (Gen)",
    "American (Valley)",
    "American (South)",
    "British (RP)",
    "British (Brixton)",
    "Transatlantic",
    "Australian",
    "South African",
    "Irish",
    "Scottish",
    "German-accented English",
    "French-accented English",
    "Italian-accented English",
    "Brazilian-accented English",
    "Japanese-accented English",
    "Spanish-accented English",
    "Indian-accented English",
]

# Default system prompt for setting up the Chat Session
DEFAULT_SYSTEM_PROMPT = """\
You are {commentator_name}, a live motorsport commentator broadcasting RIGHT NOW.

{commentator_style}

ADDITIONAL RACE CONTEXT FROM DIRECTOR:
{user_race_context}

INSTRUCTIONS:
- Deliver a rich, detailed race update covering the current standings, battles, and storylines.
- Speak naturally as if broadcasting live on air, mid-broadcast.
- Aim for approximately {target_words} words (~30 seconds of speaking).
- Use present tense. This is happening NOW.
- NEVER start with "Welcome" or introductions.
- Include strong emotion cues in brackets at the start of EVERY line: [excited], [tense], [calm], [amazed], [thoughtful], [shouting], [concerned], [panicked], [triumphant].
- Cover: current leader and gap, notable position changes, emerging battles, strategy developments.
- Build narrative — don't just list positions.
- Reference specific events that happened since the last update to add colour and drama.

OUTPUT FORMAT — tag every line with an emotion:
[emotion] Commentary text here.
[emotion] More commentary text here.
"""

# Default update prompt sent every X minutes
DEFAULT_UPDATE_PROMPT = """\
RACE UPDATE DATA:
Track: {track_name}
Lap: {current_lap}/{total_laps}
Current leader: {leader_name}

CURRENT STANDINGS & GAPS:
{standings}

NOTABLE EVENTS SINCE LAST UPDATE:
{recent_events_summary}
"""

@dataclass
class LiveModeConfig:
    gemini_api_key: str = ""
    llm_model: str = "gemini-3.8-flash"
    thinking_level: str = "HIGH"
    max_output_tokens: int = 4096
    max_requests_per_minute: int = 4
    target_words: int = 100

    # Single commentator settings
    commentator_name: str = "Martin Haven"
    commentator_voice: str = "Charon"
    commentator_accent: str = ""
    commentator_style: str = "A seasoned British motorsport commentator with a warm, resonant voice. Energetic and animated during overtakes and battles. Brings explosive excitement to dramatic moments. Provides insight and context with natural storytelling."
    voice_refinement_text: str = ""
    user_race_context: str = ""

    # Editable prompt templates
    system_prompt_template: str = ""  # Empty = use DEFAULT_SYSTEM_PROMPT
    update_prompt_template: str = ""  # Empty = use DEFAULT_UPDATE_PROMPT

    # TTS settings
    tts_model: str = "gemini-3.1-flash-tts-preview"
    emotion_tags_enabled: bool = True

    # Timing settings
    prefetch_threshold_seconds: float = 1.0
    event_ttl_multiplier: float = 1.0
    leaderboard_interval_minutes: float = 4.0

    def get_system_prompt_template(self) -> str:
        if self.system_prompt_template and self.system_prompt_template.strip():
            return self.system_prompt_template
        return DEFAULT_SYSTEM_PROMPT

    def get_update_prompt_template(self) -> str:
        if self.update_prompt_template and self.update_prompt_template.strip():
            return self.update_prompt_template
        return DEFAULT_UPDATE_PROMPT


def _get_config_path() -> str:
    """Get the path to the launcher_config.json file in the parent directory."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(current_dir), "launcher_config.json")


def load_config() -> LiveModeConfig:
    """Load config from launcher_config.json with default fallbacks."""
    config_path = _get_config_path()
    config_data = LiveModeConfig()
    
    if not os.path.exists(config_path):
        return config_data
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        global_settings = data.get("global_settings", {})
        
        # Read API key from global_settings
        if "gemini_api_key" in global_settings:
            config_data.gemini_api_key = global_settings["gemini_api_key"]
            
        # Read live mode settings
        live_mode = global_settings.get("live_mode", {})
        
        # Migrate old dual-commentator settings to single commentator
        if "commentator_1_name" in live_mode and "commentator_name" not in live_mode:
            live_mode["commentator_name"] = live_mode.pop("commentator_1_name")
        if "commentator_1_voice" in live_mode and "commentator_voice" not in live_mode:
            live_mode["commentator_voice"] = live_mode.pop("commentator_1_voice")
        if "commentator_1_style" in live_mode and "commentator_style" not in live_mode:
            live_mode["commentator_style"] = live_mode.pop("commentator_1_style")

        # Strip out old dual-commentator keys that no longer apply
        for old_key in ["commentator_1_name", "commentator_1_voice", "commentator_1_style",
                        "commentator_2_name", "commentator_2_voice", "commentator_2_style",
                        "commentary_style", "commentary_prompt_template"]:
            live_mode.pop(old_key, None)

        # Dynamically update properties in our dataclass
        valid_fields = set(asdict(config_data).keys())
        for field_name in list(live_mode.keys()):
            if field_name == "gemini_api_key":
                continue
            if field_name in valid_fields:
                setattr(config_data, field_name, live_mode[field_name])
                
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Error loading live mode config: {e}")
        
    return config_data


def save_live_mode_config(config: LiveModeConfig) -> None:
    """Save the live_mode section back to launcher_config.json."""
    config_path = _get_config_path()
    data = {}
    
    # Load existing to avoid overwriting other fields
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Error reading existing config for saving: {e}")
            
    if "global_settings" not in data:
        data["global_settings"] = {}
        
    # Get data as a dictionary and exclude the gemini api key from live_mode sub-object
    live_mode_dict = asdict(config)
    live_mode_dict.pop("gemini_api_key", None)
    
    data["global_settings"]["live_mode"] = live_mode_dict
    
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except OSError as e:
        logger.error(f"Error saving live mode config: {e}")

def load_live_mode_presets() -> dict:
    """Load all saved commentator presets."""
    config_path = _get_config_path()
    if not os.path.exists(config_path):
        return {}
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        global_settings = data.get("global_settings", {})
        return global_settings.get("live_mode_presets", {})
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Error loading presets: {e}")
        return {}

def save_live_mode_preset(preset_name: str, config: LiveModeConfig) -> None:
    """Save the current config as a preset."""
    config_path = _get_config_path()
    data = {}
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
            
    if "global_settings" not in data:
        data["global_settings"] = {}
        
    if "live_mode_presets" not in data["global_settings"]:
        data["global_settings"]["live_mode_presets"] = {}
        
    preset_dict = asdict(config)
    preset_dict.pop("gemini_api_key", None)
    
    data["global_settings"]["live_mode_presets"][preset_name] = preset_dict
    
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except OSError as e:
        logger.error(f"Error saving preset {preset_name}: {e}")
