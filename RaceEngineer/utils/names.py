"""
Speakable driver names.

Online names come with clan tags, numbers and run-together words: "[RCV]RayFin", "Bill(AuchAye)_[SR-50]",
"ATIBoy69", "kf.MTG". Read out raw, the voice mangles them (a "RayFin" was heard as "Raikkonen"). This turns
them into what an engineer would say - "Ray Fin", "Bill", "ATI Boy", "MTG" - and builds a map the TTS uses to
fix the reply text before it is spoken.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable

_TAGS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-z])(?=[A-Z])")
_CAMEL_ACRONYM = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")


def _split_camel(token: str) -> str:
    """'RayFin' -> 'Ray Fin', 'ATIBoy' -> 'ATI Boy'; leaves 'hOnDa' / 'cAmInO' style names alone."""
    parts = _CAMEL_ACRONYM.sub(" ", _CAMEL_LOWER_UPPER.sub(" ", token)).split()
    if len(parts) > 1 and all(len(p) >= 2 for p in parts):
        return " ".join(parts)
    return token


def spoken_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        return ""
    s = _TAGS.sub(" ", name)
    s = re.sub(r"[._\-|/\\]+", " ", s)
    tokens = []
    for t in s.split():
        t = re.sub(r"\d+$", "", t) or t          # "ATIBoy69" -> "ATIBoy"
        if t.isdigit():
            continue
        t = _split_camel(t)
        tokens.extend(t.split())
    if not tokens:
        return name
    # A short lowercase prefix before an all-caps name is a team tag ("kf.MTG" -> "MTG")
    if len(tokens) == 2 and tokens[0].islower() and len(tokens[0]) <= 3 and tokens[1].isupper():
        tokens = tokens[1:]
    # Names written all in lowercase with separators read better capitalised ("clinton.nortje")
    if len(tokens) > 1 and all(t.islower() for t in tokens):
        tokens = [t.capitalize() for t in tokens]
    return " ".join(tokens)


def name_variants(raw: str) -> set:
    """Spellings of a name the model might write in a reply."""
    raw = (raw or "").strip()
    out = {raw}
    no_tags = re.sub(r"\s+", " ", _TAGS.sub(" ", raw)).strip(" _-.")
    out.add(no_tags)
    out.add(re.sub(r"\d+$", "", no_tags))
    out.add(no_tags.replace("_", " ").replace(".", " ").strip())
    if " " not in raw and not _TAGS.search(raw):
        out.add(raw.split(".")[-1])
    return {v for v in out if len(v) >= 3}


def build_name_map(names: Iterable[str]) -> Dict[str, str]:
    """{spelling as written: spoken form} for every name whose spoken form differs."""
    m: Dict[str, str] = {}
    for raw in names:
        sp = spoken_name(raw)
        if not sp:
            continue
        for v in name_variants(raw):
            if v != sp and v.lower() != sp.lower():
                m[v] = sp
    return m


def apply_name_map(text: str, name_map: Dict[str, str]) -> str:
    if not text or not name_map:
        return text
    for written in sorted(name_map, key=len, reverse=True):
        pat = r"(?<![\w\[])" + re.escape(written) + r"(?![\w\]])"
        text = re.sub(pat, name_map[written], text)
    return text


def spoken_names_line(names: Iterable[str], limit: int = 12) -> str:
    pairs = []
    for raw in names:
        sp = spoken_name(raw)
        if sp and sp != raw:
            pairs.append(f"{raw} = '{sp}'")
        if len(pairs) >= limit:
            break
    return ("[SPOKEN NAMES — say them like this on the radio]: " + ", ".join(pairs)) if pairs else ""
