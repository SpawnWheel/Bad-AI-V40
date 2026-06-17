import json
import os

CONFIG_FILE = "commentator_config.json"

DEFAULT_CONFIG = {
    "logger": {
        "poll_rate": 0.5,
        "warmup_seconds": 180,
        "leaderboard_interval": 240,
        "accident_speed_threshold_kph": 30.0,
        "accident_time_threshold": 0.5,
        "battle_side_by_side_gap": 0.05,
        "battle_side_by_side_cooldown": 8.0,
        "battle_draft_gap": 0.1,
        "battle_draft_time_threshold": 3.0,
        "battle_draft_cooldown": 25.0,
        "battle_activation_delay": 30.0
    },
    "filter": {
        "commentary_duration": 8.0, 
        "interruption_threshold": 10.0,
        "priorities": {
            "ACCIDENT": 100,
            "FINISH": 95,
            "DNF": 85,
            "FLAG": 90,
            "OVERTAKE": 70,
            "CLOSEST_BATTLE": 20,
            "LEADERBOARD": 30,
            "LAP": 20,
            "PIT": 40,
            "DAMAGE": 80,
            "INCIDENT": 50,
            "SYSTEM": 10,
            "TRACK": 10,
            "SESSION": 85
        },
        "timeouts": {
            "ACCIDENT": 30,
            "FINISH": 60,
            "DNF": 45,
            "FLAG": 15,
            "OVERTAKE": 15,
            "CLOSEST_BATTLE": 5,
            "LEADERBOARD": 15,
            "LAP": 10,
            "PIT": 20,
            "DAMAGE": 20,
            "INCIDENT": 20,
            "SYSTEM": 5,
            "TRACK": 30,
            "SESSION": 30
        },
        "position_multipliers": {
            "p1": 1.5,
            "podium": 1.3,
            "top_10": 1.1,
            "mid_field": 1.0,
            "back_marker": 0.8
        }
    },
    "gemini": {
        "api_key": "",
        "llm_model": "gemini-3-flash-preview",
        "thinking_level": "HIGH",
        "tts_enabled": True,
        "voice_engine": "system", 
        "tts_model": "gemini-2.0-flash-exp",
        "voice_id": "Gemini 2.5: Puck",
        "persona_prompt": "You are an exciting motorsport commentator for a live broadcast. I will give you a race event. Your job is to generate a SINGLE, short, enthusiastic sentence of commentary describing it. Do not start with 'And' or 'Oh'. Be direct and high-energy. Keep it under 20 words."
    }
}

class SettingsManager:
    def __init__(self):
        self.config = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    loaded = json.load(f)
                    # Merge logic to ensure new keys exist if config is old
                    self._recursive_update(self.config, loaded)
            except Exception as e:
                print(f"Error loading config: {e}")
        else:
            self.save()

    def save(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def _recursive_update(self, base_dict, update_dict):
        for key, value in update_dict.items():
            if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
                self._recursive_update(base_dict[key], value)
            else:
                base_dict[key] = value

    def get(self, section, key, default=None):
        return self.config.get(section, {}).get(key, default)

    def set(self, section, key, value):
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self.save()

# Global instance for easy access
settings = SettingsManager()