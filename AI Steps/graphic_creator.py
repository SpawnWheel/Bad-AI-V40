"""Graphic Creator: asks Gemini to plan the pop-up graphics for the race and writes graphics_plan.json.

Run from the launcher after Graphics Setup. Falls back to a rule-based plan if the AI fails.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import racefiles  # noqa: E402
from GraphicsOverlay import config as gcfg  # noqa: E402
from GraphicsOverlay import creator  # noqa: E402
from GraphicsOverlay.renderer import OverlayRenderer  # noqa: E402


def main():
    project_path = os.environ.get("R3E_PROJECT_PATH")
    if not project_path:
        project_path = input("Enter the project folder: ").strip().strip('"')
    if not os.path.isdir(project_path):
        print(f"Error: project folder not found: {project_path}")
        return

    if not os.path.exists(os.path.join(project_path, gcfg.CONFIG_NAME)):
        print("Note: no graphics setup yet - run Graphics Setup first for names, logos and layout.")
    cfg = gcfg.load_config(project_path)
    race = gcfg.build_race(project_path, cfg)
    if not race.drivers:
        print("Error: could not read any drivers from the race log.")
        return
    gcfg.ensure_drivers(cfg, race.drivers, race.entries())
    print(f"Race: {race.track} | {len(race.drivers)} drivers | sync offset {race.offset:+.0f}s")

    renderer = OverlayRenderer(project_path, cfg, {'graphics': []}, race=race)
    brief = creator.build_brief(project_path, cfg, race, renderer) + creator.settings_block(cfg, race)

    prompt_file = os.environ.get("GEMINI_PROMPT_FILE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "Graphics", "Graphic Creator V1.txt")
    with open(prompt_file, 'r', encoding='utf-8') as f:
        prompt_text = f.read()

    notes = racefiles.notes_text(racefiles.race_file(project_path, "race_notes"))
    full_prompt = f"{prompt_text}\n\nProject Specific Notes:\n{notes or '(none)'}\n\n{brief}"

    debug_dir = os.path.join(project_path, racefiles.DEBUG_DIR)
    os.makedirs(debug_dir, exist_ok=True)
    with open(os.path.join(debug_dir, "graphics_prompt.txt"), 'w', encoding='utf-8') as f:
        f.write(full_prompt)

    api_key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.8-flash")
    thinking = os.environ.get("GEMINI_THINKING_LEVEL", "MEDIUM")
    raw_entries, source, driver_info = None, 'ai', {}

    if api_key:
        print(f"Asking {model} (thinking {thinking}) to plan the graphics...")
        print(f"Prompt: {os.path.basename(prompt_file)} | {len(full_prompt):,} characters")
        try:
            ask = creator.make_gemini_asker(api_key, model, thinking,
                                            int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", 32768)))
            data = None
            for attempt in range(2):
                response = ask(full_prompt)
                with open(os.path.join(debug_dir, "graphics_ai_response.json"), 'w', encoding='utf-8') as f:
                    f.write(response)
                try:
                    data = creator.parse_ai_json(response)
                    break
                except ValueError as e:
                    # A reply that is not valid JSON is usually a one-off slip: ask once more
                    if attempt:
                        raise
                    print(f"The AI's reply was not valid JSON ({e}); asking again...")
            raw_entries = data.get('graphics', [])
            driver_info = data.get('driver_info') or {}
            print(f"AI proposed {len(raw_entries)} graphics.")
        except Exception as e:
            print(f"AI step failed ({e}); using the rule-based plan instead.")
    else:
        print("No Gemini API key set; using the rule-based plan.")

    if raw_entries is None:
        raw_entries, source = creator.rule_based(cfg, race), 'rules'

    if driver_info:
        changed = creator.merge_driver_info(cfg, driver_info)
        if changed:
            gcfg.save_config(project_path, cfg)
            print(f"Filled {changed} missing driver details from the race notes.")

    plan = creator.build_plan(project_path, cfg, race, raw_entries, source)
    path = creator.save_plan(project_path, plan)

    counts = {}
    for g in plan['graphics']:
        counts[g['type']] = counts.get(g['type'], 0) + 1
    print("\nPlanned graphics:")
    for g in plan['graphics']:
        who = g.get('driver', '')
        print(f"  {g['at']:>8}  {g['type']:<13} {who:<18} {g.get('title', '')}")
    print(f"\nBy type: {json.dumps(counts)}")
    print(f"Saved to: {path}")
    print("Review it in Graphics Setup (Preview tab), then run Combine Video and Commentary.")


if __name__ == "__main__":
    main()
