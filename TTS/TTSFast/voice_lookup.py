"""Finds the voice file for a speaker in the commentary script.

Commentators are matched as before: speaker_mappings.json first, then a loose match on the file names at the top
of the voices folder.

Team radio lines use the speaker tag `RADIO <driver>` for the driver and `RADIO <driver>'s engineer` for their
engineer. Those are looked up in this order:
  1. speaker_mappings.json, when the user picked a voice for that exact tag
  2. a voice designed for that driver (Drivers/) or that driver's engineer (Engineers/), matched on the names
     saved with it (its .voice.json "aliases" / "for"), so "Payne", "Dom Payne" and "[RCV]Dom.Payne" all find it
  3. a generic voice from the pool (Pool/), picked from the name so a driver gets the same one every race
"""
import hashlib
import json
import os
import re

DRIVERS_DIR = "Drivers"
ENGINEERS_DIR = "Engineers"
POOL_DIR = "Pool"

ROLE_DRIVER, ROLE_ENGINEER = "driver", "engineer"
ROLE_POOL_DRIVER, ROLE_POOL_ENGINEER = "pool_driver", "pool_engineer"
ROLE_COMMENTATOR = "commentator"
FOLDER_FOR_ROLE = {ROLE_DRIVER: DRIVERS_DIR, ROLE_ENGINEER: ENGINEERS_DIR,
                   ROLE_POOL_DRIVER: POOL_DIR, ROLE_POOL_ENGINEER: POOL_DIR, ROLE_COMMENTATOR: ""}

RADIO_RE = re.compile(r"^radio\b[\s:\-]*(.+)$", re.I)
ENGINEER_AFTER_RE = re.compile(r"^(.+?)(?:['’]s|s['’])?\s+(?:race\s+)?engineer$", re.I)
ENGINEER_BEFORE_RE = re.compile(r"^(?:race\s+)?engineer\s*(?:\(|to\s+|for\s+|-\s*)\s*(.+?)\)?$", re.I)


def radio_speaker(speaker):
    """(role, driver name) for a team radio speaker tag, or None for a commentator.

    "RADIO Dom Payne" -> ("driver", "Dom Payne"); "RADIO Dom Payne's engineer" -> ("engineer", "Dom Payne").
    """
    m = RADIO_RE.match((speaker or "").strip())
    if not m:
        return None
    name = m.group(1).strip()
    for pattern in (ENGINEER_AFTER_RE, ENGINEER_BEFORE_RE):
        e = pattern.match(name)
        if e:
            return ROLE_ENGINEER, e.group(1).strip()
    if name.lower() in ("engineer", "race engineer", "pit wall", "the pit wall"):
        return ROLE_ENGINEER, ""
    return ROLE_DRIVER, name


def is_radio(speaker):
    return radio_speaker(speaker) is not None


def norm(name):
    """Lower-case letters only, without clan tags: "[RCV]Dom.Payne" -> "dompayne", "muzzer82" -> "muzzer"."""
    name = re.sub(r"\[[^\]]*\]", " ", name or "")
    return re.sub(r"[^a-z]", "", name.lower())


def tokens(name):
    name = re.sub(r"\[[^\]]*\]", " ", name or "")
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)  # "NewOrder" -> "New Order"
    return [t for t in re.split(r"[^a-z]+", name.lower()) if len(t) >= 3]


def name_score(query, alias):
    """How well a spoken name matches one of a voice's names: 3 exact, 2 shared word, 1 one inside the other."""
    q, a = norm(query), norm(alias)
    if not q or not a:
        return 0
    if q == a:
        return 3
    if set(tokens(query)) & set(tokens(alias)):
        return 2
    if min(len(q), len(a)) >= 4 and (q in a or a in q):
        return 1
    return 0


def read_meta(wav_path):
    path = wav_path.rsplit(".", 1)[0] + ".voice.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def role_of(rel_path, meta=None):
    """The voice's role: from its .voice.json, else from the folder it sits in."""
    meta = meta or {}
    if meta.get("role") in FOLDER_FOR_ROLE:
        return meta["role"]
    folder = rel_path.replace("\\", "/").split("/")[0] if "/" in rel_path.replace("\\", "/") else ""
    if folder == DRIVERS_DIR:
        return ROLE_DRIVER
    if folder == ENGINEERS_DIR:
        return ROLE_ENGINEER
    if folder == POOL_DIR:
        return ROLE_POOL_ENGINEER if "engineer" in rel_path.lower() else ROLE_POOL_DRIVER
    return ROLE_COMMENTATOR


def list_voices(voices_dir):
    """[{'rel', 'path', 'name', 'role', 'meta'}] for every .wav in the voices folder and its subfolders."""
    out = []
    if not os.path.isdir(voices_dir):
        return out
    for root, dirs, files in os.walk(voices_dir):
        dirs.sort()
        for f in sorted(files):
            if not f.lower().endswith(".wav"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, voices_dir).replace("\\", "/")
            meta = read_meta(path)
            out.append({"rel": rel, "path": path, "name": meta.get("name") or f[:-4].replace("_", " "),
                        "role": role_of(rel, meta), "meta": meta})
    return out


def _best(query, voices, names_of):
    best, best_score = None, 0
    for v in voices:
        score = max((name_score(query, n) for n in names_of(v)), default=0)
        if score > best_score:
            best, best_score = v, score
    return best


def _pool_pick(key, voices):
    """Rendezvous hashing: the same name always gets the same voice, and adding a voice to the pool only
    moves the names that now like the new voice best."""
    if not voices:
        return None
    return max(voices, key=lambda v: hashlib.md5(f"{key}|{v['rel']}".encode("utf-8")).hexdigest())


def find_commentator_voice(speaker, voices_dir, mappings):
    """The old lookup for commentators: the mapping, then a loose file-name match at the top of the folder."""
    voice_file_name = mappings.get(speaker)
    if voice_file_name:
        path = os.path.join(voices_dir, voice_file_name)
        if os.path.exists(path):
            return path
    if not os.path.isdir(voices_dir):
        return None
    norm_key = speaker.lower().replace(" ", "").replace("_", "")
    for f in sorted(os.listdir(voices_dir)):
        if not f.endswith(".wav"):
            continue
        norm_f = f.lower().replace(".wav", "").replace("commentator_", "").replace(" ", "").replace("_", "")
        if norm_key in norm_f or norm_f in norm_key:
            return os.path.join(voices_dir, f)
    return None


def resolve_all(speakers, voices_dir, mappings):
    """{speaker: (path or None, how)} for a whole script. Like resolve(), but generic pool voices are handed out
    so no two speakers in the script share one (while the pool has enough voices): every driver sounds different."""
    voices = list_voices(voices_dir)
    out = {s: resolve(s, voices_dir, mappings, voices) for s in speakers}
    taken = {path for path, how in out.values() if path and how != "generic voice from the pool"}
    pooled = sorted(s for s, (_, how) in out.items() if how == "generic voice from the pool")
    for s in pooled:
        role, driver = radio_speaker(s)
        pool_role = ROLE_POOL_DRIVER if role == ROLE_DRIVER else ROLE_POOL_ENGINEER
        pool = [v for v in voices if v["role"] == pool_role]
        ranked = sorted(pool, key=lambda v: hashlib.md5(f"{role}:{norm(driver)}|{v['rel']}".encode("utf-8")).hexdigest(),
                        reverse=True)
        free = [v for v in ranked if v["path"] not in taken]
        pick = (free or ranked)[0]
        taken.add(pick["path"])
        out[s] = (pick["path"], "generic voice from the pool")
    return out


def resolve(speaker, voices_dir, mappings, voices=None):
    """(path or None, how) for any speaker. `how` says where the voice came from, for the app to show."""
    radio = radio_speaker(speaker)
    if radio is None:
        path = find_commentator_voice(speaker, voices_dir, mappings)
        return path, ("picked" if mappings.get(speaker) and path else "matched by name" if path else "none")

    picked = mappings.get(speaker)
    if picked and os.path.exists(os.path.join(voices_dir, picked)):
        return os.path.join(voices_dir, picked), "picked"

    role, driver = radio
    voices = voices if voices is not None else list_voices(voices_dir)
    if role == ROLE_DRIVER:
        own = [v for v in voices if v["role"] == ROLE_DRIVER]
        hit = _best(driver, own, lambda v: [v["name"], v["rel"].rsplit("/", 1)[-1][:-4]] + list(v["meta"].get("aliases") or []))
        if hit:
            return hit["path"], "driver voice"
        pool = [v for v in voices if v["role"] == ROLE_POOL_DRIVER]
    else:
        own = [v for v in voices if v["role"] == ROLE_ENGINEER]
        hit = _best(driver, own, lambda v: list(v["meta"].get("for") or [])) if driver else None
        if hit:
            return hit["path"], "engineer voice"
        pool = [v for v in voices if v["role"] == ROLE_POOL_ENGINEER]
    pick = _pool_pick(f"{role}:{norm(driver)}", pool)
    if pick:
        return pick["path"], "generic voice from the pool"
    return None, "none"
