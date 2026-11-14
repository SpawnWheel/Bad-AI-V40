"""Reads qualifying results from a screenshot with Gemini (the results screen, a league site, a Discord post).

The AI only copies what it sees: each driver's name, best lap and gap. Its answer goes through the same name
matching as pasted text (racefiles.parse_pasted_qualifying), so both end up the same.
"""
import io
import json

import racefiles

MAX_SIDE = 2400  # larger screenshots are scaled down before sending

PROMPT = """This image shows the results of a qualifying session in a sim racing game (a results screen, a league \
website, a spreadsheet or a chat post).

List every driver in it with their best lap time. Copy the times exactly as written; never work any out. When the \
image shows only a gap to pole for a driver (like +0.214), give the gap instead. Leave the time empty for a driver \
without one (no time, DNS, DNQ).

These are the drivers in the race, as the game logged them. For each driver in the image, put the matching name from \
this list in "driver", exactly as written here, when you are sure it is them (the image may show a display name, a \
nickname, a surname or the name without its team tag); otherwise leave "driver" empty:
{roster}

Reply with JSON only:
{{"results": [{{"position": 1, "name_in_image": "...", "driver": "...", "best_lap": "1:42.315", "gap": ""}}]}}"""


def roster_text(aliases):
    lines = []
    for name, names in aliases.items():
        other = [a for a in dict.fromkeys(n for n in names if n) if a != name]
        lines.append(f"- {name}" + (f" (shown as {', '.join(other)})" if other else ""))
    return "\n".join(lines)


def image_bytes(image):
    """PNG bytes of a PIL image, scaled down when it is huge."""
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        scale = MAX_SIDE / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def read_screenshot(image, aliases, api_key, model):
    """(rows, unmatched lines, what the AI read as text) from a PIL image of qualifying results.
    Raises on an API error or an answer that isn't JSON."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    thinking = None
    if not model.lower().startswith(("gemini-1", "gemini-2")):
        thinking = types.ThinkingConfig(thinking_level="LOW")
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=image_bytes(image), mime_type="image/png"),
                  PROMPT.format(roster=roster_text(aliases))],
        config=types.GenerateContentConfig(max_output_tokens=8192, thinking_config=thinking,
                                           response_mime_type="application/json"))
    data = json.loads(resp.text or "{}")
    results = data.get("results", []) if isinstance(data, dict) else data
    # As text lines, so the matching (and gap handling) is the same as for pasted results
    lines = []
    for r in results:
        name = (r.get("driver") or "").strip()
        if name not in aliases:
            name = (r.get("name_in_image") or "").strip()
        lap, gap = (r.get("best_lap") or "").strip(), (r.get("gap") or "").strip()
        if gap and not gap.startswith("+"):
            gap = "+" + gap
        if name and (lap or gap):
            lines.append(f"{name}\t{lap}\t{gap}")
    text = "\n".join(lines)
    rows, unmatched = racefiles.parse_pasted_qualifying(text, aliases)
    return rows, unmatched, text
