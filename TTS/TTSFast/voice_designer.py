"""The Voice Designer tab: make a new voice from a written description, then keep it for good.

Design, then clone: Gemini 3.8 Flash TTS designs a voice from the description (gender, accent and the person) and
speaks a sample line with it. The take you like is saved as a reference clip (.wav), with the exact words (.txt)
and how it was made (.voice.json, including the Gemini voice id). From then on the commentary clones that clip
with Qwen3-TTS, so the voice and its accent stay the same in every race. The saved Gemini voice id lets the same
voice record a new sample line later.

(Qwen3-TTS VoiceDesign was tried first and turned every accent American, so design is Gemini only.)

Drivers go in Drivers/, their engineers in Engineers/, generic voices in Pool/ and commentators at the top of
the voices folder (see voice_lookup.py for how the commentary finds them).
"""
import datetime
import gc
import glob
import json
import os
import random
import re
import threading
import time

import gradio as gr
import numpy as np

import gemini_voice
import voice_lookup as vl
from radio_fx import apply_radio

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CHAMPIONSHIPS_DIR = os.path.join(APP_DIR, "Graphics", "Championships")
DESIGN_MODEL = gemini_voice.TTS_MODEL
CLONE_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
MAX_TAKES = 4
IDLE_UNLOAD_SECONDS = 180
LANGUAGE_CODES = ["en-GB", "en-US", "en-AU", "en-IN"]

ROLES = [("Driver", vl.ROLE_DRIVER), ("Driver's engineer", vl.ROLE_ENGINEER),
         ("Generic driver (pool)", vl.ROLE_POOL_DRIVER), ("Generic engineer (pool)", vl.ROLE_POOL_ENGINEER),
         ("Commentator", vl.ROLE_COMMENTATOR)]
ROLE_LABEL = {v: k for k, v in ROLES}
RADIO_ROLES = {vl.ROLE_DRIVER: "driver", vl.ROLE_POOL_DRIVER: "driver",
               vl.ROLE_ENGINEER: "engineer", vl.ROLE_POOL_ENGINEER: "engineer"}

SAMPLE_LINES = {
    "driver": ("Yeah, copy that. The rears are going now, I'm sliding everywhere through the last sector. "
               "How far back is he? Give me the gap every lap, because I'm not letting him past. No way."),
    "engineer": ("Okay, the gap behind is one point two, and he's been quicker in sector two. Keep it tidy "
                 "through the last corner. We're going to box this lap. Box, box. Confirm."),
    "commentator": ("And they're through the first corner side by side! What a start to this race, and "
                    "there's still so much to play for in the championship this evening."),
}

# Country code (as in the championship files) -> (accent words for the suggested description, sample line key).
# The accent is said up front and firmly, and the sample line uses words people from there actually say: both help
# the voice keep its accent.
_NATIVE = "Strong {}, clearly {} and not American."
_SECOND = "Strong {} accent, {} speaking English as a second language, not American."
ACCENTS = {
    "GBR": (_NATIVE.format("British English accent from England", "British"), "england"),
    "ENG": (_NATIVE.format("English accent from England", "British"), "england"),
    "SCT": (_NATIVE.format("broad Scottish accent from Glasgow, Scotland", "Scottish"), "scottish"),
    "WAL": (_NATIVE.format("Welsh accent from Cardiff, Wales", "Welsh"), "england"),
    "NIR": (_NATIVE.format("Northern Irish accent from Belfast", "Northern Irish"), "irish"),
    "IRL": (_NATIVE.format("Irish accent from Dublin, Ireland", "Irish"), "irish"),
    "RSA": (_NATIVE.format("South African accent from Johannesburg", "South African"), "southafrica"),
    "ZAF": (_NATIVE.format("South African accent from Johannesburg", "South African"), "southafrica"),
    "AUS": (_NATIVE.format("broad Australian accent from Melbourne", "Australian"), "australian"),
    "NZL": (_NATIVE.format("New Zealand accent from Auckland", "a New Zealander"), "australian"),
    "USA": ("General American accent, from the USA.", "american"),
    "CAN": ("Canadian accent, from Toronto, Canada.", "american"),
    "NLD": (_SECOND.format("Dutch", "a Dutch person from Amsterdam"), "dutch"),
    "NED": (_SECOND.format("Dutch", "a Dutch person from Amsterdam"), "dutch"),
    "BEL": (_SECOND.format("Belgian Flemish", "a Belgian from Antwerp"), "dutch"),
    "ESP": (_SECOND.format("Spanish", "a Spaniard from Madrid"), "spanish"),
    "MEX": (_SECOND.format("Mexican Spanish", "a Mexican from Mexico City"), "spanish"),
    "ARG": (_SECOND.format("Argentinian Spanish", "an Argentinian from Buenos Aires"), "spanish"),
    "DEU": (_SECOND.format("German", "a German from Munich"), "german"),
    "GER": (_SECOND.format("German", "a German from Munich"), "german"),
    "AUT": (_SECOND.format("Austrian German", "an Austrian from Vienna"), "german"),
    "CHE": (_SECOND.format("Swiss German", "a Swiss from Zurich"), "german"),
    "SUI": (_SECOND.format("Swiss German", "a Swiss from Zurich"), "german"),
    "FRA": (_SECOND.format("French", "a French person from Paris"), "french"),
    "ITA": (_SECOND.format("Italian", "an Italian from Milan"), "italian"),
    "BRA": (_SECOND.format("Brazilian Portuguese", "a Brazilian from Sao Paulo"), "brazilian"),
    "POR": (_SECOND.format("Portuguese", "a Portuguese from Lisbon"), "brazilian"),
    "PRT": (_SECOND.format("Portuguese", "a Portuguese from Lisbon"), "brazilian"),
    "SWE": (_SECOND.format("Swedish", "a Swede from Stockholm"), "nordic"),
    "NOR": (_SECOND.format("Norwegian", "a Norwegian from Oslo"), "nordic"),
    "DEN": (_SECOND.format("Danish", "a Dane from Copenhagen"), "nordic"),
    "DNK": (_SECOND.format("Danish", "a Dane from Copenhagen"), "nordic"),
    "FIN": (_SECOND.format("Finnish", "a Finn from Helsinki"), "nordic"),
    "POL": (_SECOND.format("Polish", "a Pole from Warsaw"), "german"),
    "CZE": (_SECOND.format("Czech", "a Czech from Prague"), "german"),
    "HUN": (_SECOND.format("Hungarian", "a Hungarian from Budapest"), "german"),
    "RUS": (_SECOND.format("Russian", "a Russian from Moscow"), "german"),
    "JPN": (_SECOND.format("Japanese", "a Japanese person from Tokyo"), "german"),
}

# Country code -> (Gemini accent, language code). The accent is free text; the language code is BCP-47.
GEMINI_ACCENTS = {
    "GBR": ("British (England)", "en-GB"), "ENG": ("British (England)", "en-GB"),
    "SCT": ("Scottish (Glasgow)", "en-GB"), "WAL": ("Welsh (Cardiff)", "en-GB"),
    "NIR": ("Northern Irish (Belfast)", "en-GB"), "IRL": ("Irish (Dublin)", "en-GB"),
    "RSA": ("South African (Johannesburg)", "en-GB"), "ZAF": ("South African (Johannesburg)", "en-GB"),
    "AUS": ("Australian (Melbourne)", "en-AU"), "NZL": ("New Zealand (Auckland)", "en-AU"),
    "USA": ("American (General American)", "en-US"), "CAN": ("Canadian (Toronto)", "en-US"),
    "NLD": ("Dutch (speaking English)", "en-GB"), "NED": ("Dutch (speaking English)", "en-GB"),
    "BEL": ("Belgian Flemish (speaking English)", "en-GB"), "ESP": ("Spanish (speaking English)", "en-GB"),
    "MEX": ("Mexican Spanish (speaking English)", "en-US"), "ARG": ("Argentinian (speaking English)", "en-GB"),
    "DEU": ("German (speaking English)", "en-GB"), "GER": ("German (speaking English)", "en-GB"),
    "AUT": ("Austrian (speaking English)", "en-GB"), "CHE": ("Swiss German (speaking English)", "en-GB"),
    "SUI": ("Swiss German (speaking English)", "en-GB"), "FRA": ("French (speaking English)", "en-GB"),
    "ITA": ("Italian (speaking English)", "en-GB"), "BRA": ("Brazilian Portuguese (speaking English)", "en-GB"),
    "POR": ("Portuguese (speaking English)", "en-GB"), "PRT": ("Portuguese (speaking English)", "en-GB"),
    "SWE": ("Swedish (speaking English)", "en-GB"), "NOR": ("Norwegian (speaking English)", "en-GB"),
    "DEN": ("Danish (speaking English)", "en-GB"), "DNK": ("Danish (speaking English)", "en-GB"),
    "FIN": ("Finnish (speaking English)", "en-GB"), "POL": ("Polish (speaking English)", "en-GB"),
    "CZE": ("Czech (speaking English)", "en-GB"), "HUN": ("Hungarian (speaking English)", "en-GB"),
    "RUS": ("Russian (speaking English)", "en-GB"), "JPN": ("Japanese (speaking English)", "en-GB"),
}


def gemini_accent(country):
    return GEMINI_ACCENTS.get((country or "").upper(), ("British (England)", "en-GB"))


# Sample lines in each accent's own words: (driver line, engineer line)
ACCENT_LINES = {
    "england": ("Right, cheers mate. The tyres are knackered, I'm sliding all over the shop in the last sector. "
                "Bloody hell, how far back is he? Give us the gap every lap, yeah?",
                "Okay mate, the gap behind is one point two, and he's been quicker in sector two. Keep it tidy "
                "through the last corner. We're boxing this lap. Box, box. Confirm."),
    "scottish": ("Aye, he's just shoved me right off at turn one, the numpty. That's twice now. Tell race control, "
                 "I'm no having it. Wee bit more pace and I'll have him.",
                 "Aye, the gap behind's one point two, he's been a wee bit quicker in sector two. Keep it tidy "
                 "through the last corner. We're boxing this lap. Box, box. Confirm."),
    "irish": ("Ah, he's after shoving me right off at turn one, the eejit. That's twice now. Tell race control, "
              "would you, I'm not having it. Give us the gap every lap.",
              "Grand, the gap behind is one point two, and he's been quicker in sector two. Keep it tidy through "
              "the last corner, so. We're boxing this lap. Box, box. Confirm."),
    "southafrica": ("Ja, okay, copy. The tyres are finished, hey, I'm sliding all over the show. Just now I'll have "
                    "a go at him into the hairpin, ja? Keep the gaps coming, bru.",
                    "Ja, okay, the gap behind is one point two, hey, he's been quicker in sector two. Keep it neat "
                    "through the last corner. We're boxing this lap. Box, box. Confirm."),
    "australian": ("Yeah, nah, she's going alright now, mate. Tyres are cooked but I reckon I can have a crack at "
                   "him into the hairpin. Keep the gaps coming, no worries.",
                   "Righto mate, gap behind's one point two, he's been a bit quicker in sector two. Keep it tidy "
                   "through the last corner. We're boxing this lap. Box, box. Confirm."),
    "american": ("Ten-four. The rears are going away, man, I'm sliding all over the place through the last sector. "
                 "How far back is he? Give me the gap every lap, I'm not letting him by.",
                 "Okay buddy, gap behind is one point two, and he's been quicker in sector two. Keep it clean "
                 "through the last corner. We're pitting this lap. Box, box. Copy?"),
    "dutch": ("Okay, okay, I have him now. The car is really good, ja, the fuel is going down. Just give me the "
              "gaps, then I go for it into the hairpin. Lekker.",
              "Okay, the gap behind is one point two, he is quicker in sector two. Keep it clean in the last "
              "corner. We box this lap. Box, box. Please confirm."),
    "spanish": ("Okay, okay, I have him. The car is fantastic now, vamos. Give me the gap every lap, please, I go "
                "for the inside in the hairpin. Venga.",
                "Okay, the gap behind is one point two, he is quicker in sector two. Keep it clean in the last "
                "corner. We box this lap. Box, box. Vamos, confirm."),
    "german": ("Okay, I have him. The car is very good now, ja, the fuel is going down. Give me the gap every lap, "
               "please, then I attack into the hairpin.",
               "Okay, the gap behind is one point two, he is quicker in sector two. Keep it clean in the last "
               "corner. We box this lap. Box, box. Please confirm."),
    "french": ("Okay, okay, I 'ave him now. Ze car is very good, the fuel is coming down. Just give me ze gaps, "
               "and I go for it into the hairpin. Allez.",
               "Okay, ze gap behind is one point two, he is quicker in sector two. Keep it clean in ze last "
               "corner. We box this lap. Box, box. Confirm, s'il te plait."),
    "italian": ("Okay, okay, I have him! The car is fantastic now, the fuel is going down. Give me the gap every "
                "lap, please, I go for the inside in the hairpin. Andiamo!",
                "Okay, the gap behind is one point two, he is quicker in sector two, eh. Keep it clean in the "
                "last corner. We box this lap. Box, box. Andiamo, confirm."),
    "brazilian": ("Okay, okay, I have him! The car is fantastic now, the fuel is going down. Give me the gap every "
                  "lap, please, I go for the inside in the hairpin. Vamos!",
                  "Okay, the gap behind is one point two, he is quicker in sector two. Keep it clean in the last "
                  "corner. We box this lap. Box, box. Confirm, please."),
    "nordic": ("Okay, I have him now. The car is really good, the fuel is going down. Just give me the gaps, then "
               "I go for it into the hairpin. Ja, ja.",
               "Okay, the gap behind is one point two, he is quicker in sector two. Keep it clean in the last "
               "corner. We box this lap. Box, box. Please confirm."),
}


def accent_line(country, kind):
    """The sample line for a country's accent: kind is "driver" or "engineer"."""
    key = ACCENTS.get((country or "").upper(), ("", "england"))[1]
    driver, engineer = ACCENT_LINES[key]
    return engineer if kind == "engineer" else driver


DESCRIPTION_HINT = (
    "Who the person is, in permanent traits: age, accent, pitch, timbre, pace, manner. Make each driver different "
    "from the others (older or younger, deeper or higher, gravelly or smooth, fast or slow). Ask for a clean, "
    "close-mic voice: the radio sound is added later. E.g. \"A man in his sixties with a broad Glaswegian Scottish "
    "accent. Deep, gravelly voice, dry and impatient. Clean close-mic recording, no background noise.\"")


class Models:
    """One Qwen model in this process at a time (the designer or the cloner), unloaded after a while unused."""

    def __init__(self):
        self.lock = threading.Lock()
        self.model = None
        self.name = None
        self.last_used = 0.0
        threading.Thread(target=self._idle_watch, daemon=True).start()

    def get(self, name, make_room):
        with self.lock:
            if self.name != name:
                self._unload()
                make_room()
                from faster_qwen3_tts import FasterQwen3TTS
                self.model = FasterQwen3TTS.from_pretrained(name)
                self.name = name
            self.last_used = time.time()
            return self.model

    def touch(self):
        self.last_used = time.time()

    def unload(self):
        with self.lock:
            return self._unload()

    def _unload(self):
        if self.model is None:
            return False
        self.model, self.name = None, None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        return True

    def _idle_watch(self):
        while True:
            time.sleep(15)
            if self.model is not None and time.time() - self.last_used > IDLE_UNLOAD_SECONDS:
                if self.lock.acquire(blocking=False):
                    try:
                        if time.time() - self.last_used > IDLE_UNLOAD_SECONDS:
                            self._unload()
                    finally:
                        self.lock.release()


models = Models()


def safe_name(name):
    name = re.sub(r"[\\/:*?\"<>|]", "", (name or "").strip())
    return re.sub(r"\s+", "_", name).strip("._")


def split_names(text):
    return [n.strip() for n in re.split(r"[,;\n]", text or "") if n.strip()]


def championship_files():
    return sorted(glob.glob(os.path.join(CHAMPIONSHIPS_DIR, "*.json")))


def championship_drivers(path):
    """{label: driver entry} for a championship file, with the log name kept in the entry."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            drivers = json.load(f).get("drivers") or {}
    except (OSError, ValueError):
        return {}
    out = {}
    for key, d in drivers.items():
        label = f"{d.get('display') or key}  ({key})"
        out[label] = dict(d, log_name=key)
    return out


def suggested_description(role, country=""):
    """A starting description, accent first (see ACCENTS)."""
    who = "racing driver" if role in (vl.ROLE_DRIVER, vl.ROLE_POOL_DRIVER) else \
        "race engineer" if role in (vl.ROLE_ENGINEER, vl.ROLE_POOL_ENGINEER) else "TV motorsport commentator"
    accent_text = ACCENTS.get((country or "").upper(), ("", ""))[0]
    mood = {
        "racing driver": "Talks quickly and firmly between breaths, focused and a little tense, like he is "
                         "mid-race.",
        "race engineer": "Calm, clipped and precise, steady under pressure, like he is reading numbers off a "
                         "screen.",
        "TV motorsport commentator": "Lively, warm and expressive, with a broadcaster's clear diction.",
    }[who]
    return (f"{accent_text} A man in his forties, a {who}. {mood} "
            "Clean close-mic recording, no background noise.").strip()


_accent_checker = None


def accent_check(audio, sr):
    """What an English-accent classifier hears in a take, e.g. "sounds most like england (81%)", or "" if it can't
    run. It only knows US, Canada, England, Australia and India, so it is a rough guide: mostly useful for catching
    a take that came out American."""
    global _accent_checker
    try:
        from scipy.signal import resample_poly
        if _accent_checker is None:
            from transformers import pipeline
            _accent_checker = pipeline("audio-classification", model="dima806/english_accents_classification",
                                       device="cpu")
        x = resample_poly(np.asarray(audio, dtype=np.float32), 16000, int(sr))
        top = _accent_checker({"raw": x, "sampling_rate": 16000}, top_k=2)
        return " / ".join(f"{t['label']} {t['score'] * 100:.0f}%" for t in top)
    except Exception as e:
        print(f"Accent check unavailable: {e}")
        return ""


def build_tab(runner, voices_dir, on_saved_outputs=None):
    """Adds the Voice Designer tab's contents. `runner` is the TTS run (to keep the GPU free for one model)."""

    def make_room():
        """Frees the GPU from a finished recording run that is waiting for redos; refuses during a run."""
        waiting = runner.unload()
        if waiting:
            waiting.join(timeout=60)
        if runner.busy():
            raise gr.Error("A recording run is using the graphics card. Let it finish (or press Unload Model on "
                           "the Generation tab) and try again.")

    gr.Markdown("### 🎨 Design a new voice\n"
                "Describe the person and Gemini designs a voice for them. Generate a few takes (each one is a "
                "different voice), keep the one you like, and it becomes that person's voice in every race "
                "(the commentary clones the saved clip). To change a voice you already have, use **Edit a saved "
                "voice** below.")
    with gr.Row():
        with gr.Column():
            with gr.Accordion("✏️ Edit a saved voice", open=False):
                gr.Markdown("Loads a voice's details into the form. **Record a new sample** uses the same Gemini "
                            "voice to say a new line (say, a calmer or angrier take), so it's the same person. "
                            "Changing the description and pressing **Generate takes** designs a new voice for them "
                            "instead.")
                edit_voice = gr.Dropdown(choices=voice_choices(voices_dir), label="Voice",
                                         allow_custom_value=True)
                edit_load = gr.Button("Load into the form", size="sm")
            role = gr.Radio([label for label, _ in ROLES], value="Driver", label="Who is this voice for?")
            with gr.Accordion("Fill in from a championship", open=False):
                champ = gr.Dropdown([(os.path.basename(p)[:-5].replace("_", " "), p) for p in championship_files()],
                                    label="Championship", value=None)
                champ_driver = gr.Dropdown([], label="Driver", value=None, allow_custom_value=True)
            name = gr.Textbox(label="Name", placeholder="e.g. Dom Payne, or a generic name like 'Gruff Northern Engineer'")
            aliases = gr.Textbox(label="Also known as (drivers)", placeholder="Payne, [RCV]Dom.Payne",
                                 info="Other names the commentary may use for this driver, comma separated.")
            for_driver = gr.Textbox(label="Engineer for (engineers)", placeholder="Dom Payne, Payne",
                                    info="The driver (and their other names) this engineer talks to.", visible=False)
            with gr.Row():
                gender = gr.Radio(["male", "female"], value="male", label="Gender")
                accent = gr.Textbox(label="Accent", value="British (England)",
                                    info="e.g. Scottish (Glasgow), South African (Johannesburg), Dutch (speaking "
                                         "English)")
                lang_code = gr.Dropdown(LANGUAGE_CODES, value="en-GB", label="Language", allow_custom_value=True)
            description = gr.Textbox(label="Voice description", lines=4, info=DESCRIPTION_HINT)
            sample = gr.Textbox(label="Sample line (what the voice says)", lines=3, value=accent_line("GBR", "driver"),
                                info="10-15 seconds of speech in the person's natural style works best. It is "
                                     "saved as the transcript.")
            with gr.Row():
                takes = gr.Slider(1, MAX_TAKES, value=2, step=1, label="Takes",
                                  info="Each take designs a new Gemini voice (about 20 s each).")
                design_btn = gr.Button("🎨 Generate takes", variant="primary")
                rerecord_btn = gr.Button("🎙️ Record a new sample", interactive=False)
        with gr.Column():
            design_status = gr.Markdown("")
            radio_preview = gr.Checkbox(value=True, label="Play drivers and engineers through the radio sound",
                                        info="Only for listening here. The clean take is what gets saved.")
            take_audio = [gr.Audio(label=f"Take {i + 1}", type="numpy", interactive=False, visible=False)
                          for i in range(MAX_TAKES)]
            pick = gr.Radio([], label="Keep which take?")
            replace = gr.Checkbox(value=False, label="Replace a voice with the same name")
            save_btn = gr.Button("💾 Save voice", variant="primary")
            save_status = gr.Markdown("")
    takes_state = gr.State([])
    loaded_voice_id = gr.State(None)

    gr.Markdown("---\n### 🔊 Try a saved voice\nHear any voice in the library say a new line, the way the commentary "
                "will (Qwen cloning its saved clip, on your graphics card).")
    with gr.Row():
        with gr.Column():
            try_voice = gr.Dropdown(choices=voice_choices(voices_dir), label="Voice", allow_custom_value=True)
            try_text = gr.Textbox(label="Line", lines=2, value="Box this lap. I repeat, box this lap.")
            try_radio = gr.Checkbox(value=True, label="Through the radio sound (drivers and engineers)")
            with gr.Row():
                try_btn = gr.Button("🔊 Say it")
                unload_btn = gr.Button("⏏️ Unload the clone model", size="sm")
        with gr.Column():
            try_status = gr.Markdown("")
            try_out = gr.Audio(label="Result", type="numpy", interactive=False)

    # ---------------------------------------------------------------- behaviour

    def role_value(label):
        return dict(ROLES).get(label, vl.ROLE_DRIVER)

    def on_role(label):
        r = role_value(label)
        kind = RADIO_ROLES.get(r, "commentator")
        line = SAMPLE_LINES["commentator"] if kind == "commentator" else accent_line("GBR", kind)
        return (gr.update(visible=r in (vl.ROLE_DRIVER,)), gr.update(visible=r == vl.ROLE_ENGINEER), line)

    role.change(on_role, inputs=[role], outputs=[aliases, for_driver, sample])

    def on_champ(path):
        drivers = championship_drivers(path) if path else {}
        return gr.update(choices=list(drivers), value=None)

    champ.change(on_champ, inputs=[champ], outputs=[champ_driver])

    def on_champ_driver(path, label, role_label):
        d = championship_drivers(path).get(label) if path and label else None
        if not d:
            return (gr.skip(),) * 7
        display = d.get("display") or d["log_name"]
        names = [display]
        for extra in (d.get("short", "").title(), d["log_name"]):
            if extra and vl.norm(extra) not in [vl.norm(n) for n in names]:
                names.append(extra)
        r = role_value(role_label)
        country = (d.get("country") or "").upper()
        if r == vl.ROLE_ENGINEER:
            # The engineer's accent isn't the driver's: start from British and change it as you like
            acc, code = gemini_accent("GBR")
            return (f"{display} engineer", gr.skip(), ", ".join(names), suggested_description(r, "GBR"),
                    accent_line("GBR", "engineer"), acc, code)
        acc, code = gemini_accent(country)
        return (display, ", ".join(names[1:]), gr.skip(), suggested_description(r, country),
                accent_line(country, "driver"), acc, code)

    champ_driver.change(on_champ_driver, inputs=[champ, champ_driver, role],
                        outputs=[name, aliases, for_driver, description, sample, accent, lang_code])

    def render(takes_list, radio_on, role_label):
        kind = RADIO_ROLES.get(role_value(role_label))
        out = []
        for i in range(MAX_TAKES):
            if i < len(takes_list):
                t = takes_list[i]
                audio = apply_radio(t["audio"], t["sr"], kind, seed=i) if radio_on and kind else t["audio"]
                label = f"Take {i + 1}" + ("  ·  new sample, same voice" if t.get("rerecord") else "")
                out.append(gr.update(value=(t["sr"], audio), visible=True,
                                     label=label + (f"  ·  sounds like {t['accent']}" if t.get("accent") else "")))
            else:
                out.append(gr.update(value=None, visible=False))
        return out

    def check_inputs(desc, text, nm):
        if not (text or "").strip():
            raise gr.Error("Write a sample line for the voice to say.")
        if not gemini_voice.api_key():
            raise gr.Error("No Gemini API key: add it in the launcher's Settings.")

    def design(desc, text, nm, gen, acc, code, n, radio_on, role_label):
        check_inputs(desc, text, nm)
        if not (desc or "").strip():
            raise gr.Error("Write a voice description first.")
        made = []
        yield ([], *[gr.update(visible=False, value=None)] * MAX_TAKES, gr.update(choices=[], value=None),
               "Designing the first voice with Gemini (about 20 s)...")
        for i in range(int(n)):
            started = time.time()
            try:
                voice_id, _ = gemini_voice.design_voice(desc.strip(), gen, (acc or "").strip(), code,
                                                        f"BAD AI - {(nm or 'voice').strip()}")
                audio = gemini_voice.speak(voice_id, text.strip(), code)
            except Exception as e:
                raise gr.Error(f"Gemini couldn't make take {i + 1}: {e}")
            sr = gemini_voice.SAMPLE_RATE
            made.append({"voice_id": voice_id, "sr": sr, "audio": audio, "accent": accent_check(audio, sr)})
            status = (f"Made take {i + 1} of {int(n)} ({time.time() - started:.0f} s)."
                      + (" Designing the next one..." if i + 1 < n else " Pick the one you like and save it."))
            yield (made, *render(made, radio_on, role_label),
                   gr.update(choices=[f"Take {k + 1}" for k in range(len(made))], value=None), status)

    design_btn.click(design, inputs=[description, sample, name, gender, accent, lang_code, takes, radio_preview,
                                     role],
                     outputs=[takes_state, *take_audio, pick, design_status])
    radio_preview.change(render, inputs=[takes_state, radio_preview, role], outputs=take_audio)

    def rerecord(voice_id, text, nm, code, takes_list, radio_on, role_label):
        """The loaded voice says the sample line again (same Gemini voice, new take)."""
        check_inputs("", text, nm)
        if not voice_id:
            raise gr.Error("Load a voice that was designed with Gemini first.")
        try:
            audio = gemini_voice.speak(voice_id, text.strip(), code)
        except Exception as e:
            raise gr.Error(f"Gemini couldn't record it: {e}")
        sr = gemini_voice.SAMPLE_RATE
        made = (list(takes_list) + [{"voice_id": voice_id, "sr": sr, "audio": audio, "rerecord": True,
                                     "accent": accent_check(audio, sr)}])[-MAX_TAKES:]
        return (made, *render(made, radio_on, role_label),
                gr.update(choices=[f"Take {k + 1}" for k in range(len(made))], value=f"Take {len(made)}"),
                "Recorded a new sample with the same voice. Save it to replace the old one.")

    rerecord_btn.click(rerecord, inputs=[loaded_voice_id, sample, name, lang_code, takes_state, radio_preview, role],
                       outputs=[takes_state, *take_audio, pick, design_status])

    def load_voice(rel):
        """Puts a saved voice's details into the form for editing."""
        if not rel:
            raise gr.Error("Pick a voice to edit.")
        wav = os.path.join(voices_dir, rel)
        meta = vl.read_meta(wav)
        txt = wav[:-4] + ".txt"
        line = open(txt, "r", encoding="utf-8").read().strip() if os.path.exists(txt) else ""
        r = vl.role_of(rel, meta)
        voice_id = meta.get("gemini_voice_id")
        note = ("Loaded. Change the sample line and press **Record a new sample** for a new take with the same "
                "voice, or change the description and press **Generate takes** for a new voice."
                if voice_id else
                "Loaded. This voice wasn't designed with Gemini, so it can't record a new sample: change what you "
                "like and press **Generate takes** to design it again.")
        return (ROLE_LABEL.get(r, "Driver"), meta.get("name") or os.path.basename(rel)[:-4].replace("_", " "),
                ", ".join(meta.get("aliases") or []), ", ".join(meta.get("for") or []),
                meta.get("gender") or "male", meta.get("accent") or "British (England)",
                meta.get("language_code") or "en-GB", meta.get("description") or "", line, True,
                voice_id, gr.update(interactive=bool(voice_id)), note)

    edit_load.click(load_voice, inputs=[edit_voice],
                    outputs=[role, name, aliases, for_driver, gender, accent, lang_code, description, sample, replace,
                             loaded_voice_id, rerecord_btn, design_status])

    def save(takes_list, choice, role_label, nm, alias_text, for_text, desc, text, gen, acc, code, overwrite):
        if not takes_list or not choice:
            raise gr.Error("Generate some takes and pick one to keep.")
        file_stem = safe_name(nm)
        if not file_stem:
            raise gr.Error("Give the voice a name.")
        r = role_value(role_label)
        if r == vl.ROLE_ENGINEER and not split_names(for_text):
            raise gr.Error("Say which driver this engineer is for.")
        take = takes_list[int(choice.split()[-1]) - 1]
        try:
            rel = save_voice(voices_dir, r, nm, take["audio"], take["sr"], text, desc, take["voice_id"],
                             gender=gen, accent=acc, language_code=code, aliases=split_names(alias_text),
                             for_names=split_names(for_text), overwrite=overwrite)
        except FileExistsError:
            raise gr.Error(f"There's already a voice called {file_stem}. Tick 'Replace' to overwrite it, or "
                           "change the name.")
        choices = voice_choices(voices_dir)
        return (f"✅ Saved **{nm.strip()}** as `{rel}` ({ROLE_LABEL[r]}). The commentary will use it from now on.",
                gr.update(choices=choices, value=rel), gr.update(choices=choices), take["voice_id"],
                gr.update(interactive=True))

    save_btn.click(save, inputs=[takes_state, pick, role, name, aliases, for_driver, description, sample, gender,
                                 accent, lang_code, replace],
                   outputs=[save_status, try_voice, edit_voice, loaded_voice_id, rerecord_btn])

    def unload():
        gr.Info("Clone model unloaded." if models.unload() else "No model is loaded.")

    unload_btn.click(unload, queue=False)

    def try_say(rel, text, radio_on):
        if not rel:
            raise gr.Error("Pick a voice.")
        text = (text or "").strip()
        if not text:
            raise gr.Error("Write a line to say.")
        wav = os.path.join(voices_dir, rel)
        txt_path = wav[:-4] + ".txt"
        ref_text = open(txt_path, "r", encoding="utf-8").read().strip() if os.path.exists(txt_path) else ""
        style_path = wav[:-4] + ".style.txt"
        style = open(style_path, "r", encoding="utf-8").read().strip() if os.path.exists(style_path) else None
        lang = vl.read_meta(wav).get("language") or "English"
        import torch
        model = models.get(CLONE_MODEL, make_room)
        seed = random.randint(0, 2 ** 31 - 1)
        torch.manual_seed(seed)
        audio_list, sr = model.generate_voice_clone(text=text, language=lang, ref_audio=wav, ref_text=ref_text,
                                                    xvec_only=not ref_text, instruct=style or None)
        models.touch()
        audio = np.asarray(audio_list[0], dtype=np.float32)
        kind = RADIO_ROLES.get(vl.role_of(rel, vl.read_meta(wav)))
        if radio_on and kind:
            audio = apply_radio(audio, sr, kind, seed=seed)
        return (int(sr), audio), f"Seed {seed}" + (" · with the radio sound" if radio_on and kind else "")

    try_btn.click(try_say, inputs=[try_voice, try_text, try_radio], outputs=[try_out, try_status])
    return try_voice


def save_voice(voices_dir, role, name, audio, sr, text, description, gemini_voice_id, gender="male", accent="",
               language_code="en-GB", aliases=None, for_names=None, overwrite=False):
    """Saves a designed take as a library voice: <name>.wav, the words as <name>.txt (for high-quality cloning)
    and how it was made as <name>.voice.json. Returns its path relative to the voices folder."""
    import soundfile as sf
    file_stem = safe_name(name)
    folder = os.path.join(voices_dir, vl.FOLDER_FOR_ROLE[role])
    os.makedirs(folder, exist_ok=True)
    wav_path = os.path.join(folder, file_stem + ".wav")
    if os.path.exists(wav_path) and not overwrite:
        raise FileExistsError(wav_path)
    audio = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) or 1.0
    sf.write(wav_path, audio / peak * 0.9, int(sr), subtype="PCM_16")
    with open(wav_path[:-4] + ".txt", "w", encoding="utf-8") as f:
        f.write(text.strip())
    # "language" is what the Qwen cloning is told; the rest is how Gemini designed it
    meta = {"name": name.strip(), "role": role, "description": description.strip(), "language": "English",
            "gender": gender, "accent": accent.strip(), "language_code": language_code,
            "gemini_voice_id": gemini_voice_id, "model": DESIGN_MODEL,
            "created": datetime.datetime.now().isoformat(timespec="seconds")}
    if role == vl.ROLE_DRIVER:
        meta["aliases"] = list(aliases or [])
    if role == vl.ROLE_ENGINEER:
        meta["for"] = list(for_names or [])
    with open(wav_path[:-4] + ".voice.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return os.path.relpath(wav_path, voices_dir).replace("\\", "/")


def voice_choices(voices_dir):
    """Dropdown choices (label, relative path) for every voice, grouped by role."""
    order = {vl.ROLE_COMMENTATOR: 0, vl.ROLE_DRIVER: 1, vl.ROLE_ENGINEER: 2, vl.ROLE_POOL_DRIVER: 3,
             vl.ROLE_POOL_ENGINEER: 4}
    voices = sorted(vl.list_voices(voices_dir), key=lambda v: (order.get(v["role"], 9), v["name"].lower()))
    return [(f"{v['name']}  ·  {ROLE_LABEL.get(v['role'], v['role'])}", v["rel"]) for v in voices]
