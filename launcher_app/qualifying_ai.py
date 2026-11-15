"""Reads qualifying results from a screenshot with Gemini (the results screen, a league site, a Discord post).

The AI only copies what it sees: each driver's name, best lap and gap. Its answer goes through the same name
matching as pasted text (racefiles.parse_pasted_qualifying), so both end up the same.
"""
import io
import json

import racefiles

MAX_SIDE = 2400  # larger screenshots are scaled down before sending

PROMPT = """This image shows the drivers of a sim race: qualifying results, or the entry list or leaderboard at the \
start of the race (a game screen, a league website, a spreadsheet or a chat post).

List every driver in it, with their best lap time when the image shows one. Copy the times exactly as written; never \
work any out. When the image shows only a gap to pole for a driver (like +0.214), give the gap instead. Leave the \
time empty for a driver without one (no time, DNS, DNQ), and leave every time empty when the image shows no lap times \
at all (a grid set before the session, a leaderboard of positions only).

When a driver has a small country flag next to their name, give that country in "country" as one of these codes: \
{flags}. Use the code for the flag you actually see (England, Scotland and Wales have their own: ENG, SCO, WAL). \
Leave "country" empty when there is no flag or you can't tell which one it is; never guess it from the name.

These are the drivers in the race, as the game logged them. For each driver in the image, put the matching name from \
this list in "driver", exactly as written here, when you are sure it is them (the image may show a display name, a \
nickname, an initial and surname or the name without its team tag); otherwise leave "driver" empty:
{roster}

Reply with JSON only:
{{"results": [{{"position": 1, "name_in_image": "...", "driver": "...", "best_lap": "1:42.315", "gap": "", \
"country": "GBR"}}]}}"""

# The codes the AI may answer with: the sporting codes on motorsport graphics, where Graphics/Flags has them
PREFERRED_FLAGS = ("GBR", "ENG", "SCO", "WAL", "NIR", "IRL", "GER", "FRA", "ESP", "POR", "ITA", "NED", "BEL", "LUX",
                   "SUI", "AUT", "DEN", "NOR", "SWE", "FIN", "ISL", "POL", "CZE", "SVK", "HUN", "SLO", "CRO", "SRB",
                   "ROU", "BUL", "GRE", "TUR", "UKR", "RUS", "EST", "LAT", "LTU", "USA", "CAN", "MEX", "BRA", "ARG",
                   "CHI", "COL", "URU", "AUS", "NZL", "JPN", "CHN", "KOR", "IND", "RSA", "MAR", "UAE", "KSA", "ISR")


def flag_list():
    """The codes to offer the AI: the preferred sporting codes that have a flag file, then any other flag files."""
    have = set(flag_files())
    preferred = [c for c in PREFERRED_FLAGS if c in have]
    return preferred + sorted(have - set(preferred) - {"GB", "UK"})


def flag_files():
    import os
    folder = os.path.join(racefiles.APP_DIR, "Graphics", "Flags")
    try:
        return [os.path.splitext(f)[0].upper() for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg"))]
    except OSError:
        return []


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
    """(rows, unmatched lines, what the AI read as text, {log name: country code}) from a PIL image of qualifying
    results or the race's entry list. Rows are empty when the image has no lap times. Raises on an API error or
    an answer that isn't JSON."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    thinking = None
    if not model.lower().startswith(("gemini-1", "gemini-2")):
        thinking = types.ThinkingConfig(thinking_level="LOW")
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=image_bytes(image), mime_type="image/png"),
                  PROMPT.format(roster=roster_text(aliases), flags=", ".join(flag_list()))],
        config=types.GenerateContentConfig(max_output_tokens=8192, thinking_config=thinking,
                                           response_mime_type="application/json"))
    data = json.loads(resp.text or "{}")
    results = data.get("results", []) if isinstance(data, dict) else data
    # As text lines, so the matching (and gap handling) is the same as for pasted results
    lines = []
    countries = {}
    flags = set(flag_files())
    for r in results:
        name = (r.get("driver") or "").strip()
        code = (r.get("country") or "").strip().upper()
        if name in aliases and code in flags:
            countries[name] = code
        if name not in aliases:
            name = (r.get("name_in_image") or "").strip()
        lap, gap = (r.get("best_lap") or "").strip(), (r.get("gap") or "").strip()
        if gap and not gap.startswith("+"):
            gap = "+" + gap
        if name and (lap or gap):
            lines.append(f"{name}\t{lap}\t{gap}")
    text = "\n".join(lines)
    rows, unmatched = racefiles.parse_pasted_qualifying(text, aliases)
    return rows, unmatched, text, countries
