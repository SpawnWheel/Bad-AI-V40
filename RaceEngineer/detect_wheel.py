"""
Interactive Wheel Button & API Key Setup Wizard for AMS2 AI Race Engineer.
Press any button on your steering wheel to automatically configure it in config.yaml.
"""

import os
import sys
import time
import yaml
from pathlib import Path

# Enable DirectInput background events
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame

CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.example.yaml")


def load_config() -> dict:
    if not CONFIG_PATH.exists() and EXAMPLE_CONFIG_PATH.exists():
        with open(EXAMPLE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
    print(f"\n[OK] Configuration successfully saved to {CONFIG_PATH.resolve()}!")


def setup_wizard():
    print("=" * 65)
    print("🏎️   AMS2 AI RACE ENGINEER - WHEEL & SETUP WIZARD")
    print("=" * 65)

    config = load_config()
    gemini_cfg = config.get("gemini", {})
    ptt_cfg = config.get("push_to_talk", {})

    # --- STEP 1: Gemini API Key ---
    current_key = gemini_cfg.get("api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "")
    print("\n--- STEP 1: Google Gemini API Key ---")
    if current_key:
        masked_key = current_key[:6] + "..." + current_key[-4:] if len(current_key) > 10 else "******"
        print(f"Current API Key: {masked_key}")
        change = input("Do you want to change it? (y/N): ").strip().lower()
        if change == 'y':
            new_key = input("Enter your Gemini API key: ").strip()
            if new_key:
                gemini_cfg["api_key"] = new_key
                config["gemini"] = gemini_cfg
    else:
        print("No Gemini API key detected.")
        print("(Get a free key at: https://aistudio.google.com/app/apikey)")
        key_input = input("Enter your Gemini API key (or press Enter to skip for now): ").strip()
        if key_input:
            gemini_cfg["api_key"] = key_input
            config["gemini"] = gemini_cfg

    # --- STEP 2: Wheel Button Detection ---
    print("\n--- STEP 2: Steering Wheel Button Detection ---")
    pygame.init()
    pygame.joystick.init()

    num_devices = pygame.joystick.get_count()
    if num_devices == 0:
        print("[!] No gaming controllers or steering wheels detected by Windows.")
        print("    * Ensure your wheel (Logitech, Fanatec, Moza, Thrustmaster, Simucube) is plugged in.")
        print("    * Default keyboard fallback is 'Caps Lock' (tap for update, hold to speak).")
        save_config(config)
        return

    print(f"Found {num_devices} connected gaming controller(s):")
    joysticks = []
    for i in range(num_devices):
        j = pygame.joystick.Joystick(i)
        j.init()
        joysticks.append(j)
        print(f"  [{i}] {j.get_name()} ({j.get_numbuttons()} buttons)")

    print("\n" + "=" * 65)
    print(">>> PLEASE PRESS ANY BUTTON ON YOUR STEERING WHEEL NOW... <<<")
    print("=" * 65)

    detected_joy_idx = None
    detected_btn_idx = None

    try:
        running = True
        while running:
            pygame.event.pump()
            for joy_idx, j in enumerate(joysticks):
                num_buttons = j.get_numbuttons()
                for b_idx in range(num_buttons):
                    if j.get_button(b_idx):
                        detected_joy_idx = joy_idx
                        detected_btn_idx = b_idx
                        running = False
                        break
                if not running:
                    break
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nDetection cancelled.")
        pygame.quit()
        return

    chosen_wheel = joysticks[detected_joy_idx]
    print(f"\n🎯 BUTTON DETECTED!")
    print(f"  Controller: [{detected_joy_idx}] '{chosen_wheel.get_name()}'")
    print(f"  Button:     Button {detected_btn_idx}")

    confirm = input(f"\nUse this button for your Race Engineer? (Y/n): ").strip().lower()
    if confirm in ("", "y", "yes"):
        ptt_cfg["joystick_index"] = detected_joy_idx
        ptt_cfg["button_index"] = detected_btn_idx
        ptt_cfg["mode"] = "auto"
        config["push_to_talk"] = ptt_cfg
        save_config(config)
        print(f"\n[SUCCESS] Configured Button {detected_btn_idx} on '{chosen_wheel.get_name()}'.")
        print("How it works during your race:")
        print("  - Quick tap  (<350ms): Engineer gives full Race Situation Update (gaps, fuel, tires).")
        print("  - Hold down  (>350ms): Radio mic opens -> Speak your question -> Release to send!")
    else:
        print("\nButton not saved.")

    pygame.quit()


if __name__ == "__main__":
    setup_wizard()
