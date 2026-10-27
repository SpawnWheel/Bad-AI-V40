"""
Audition your race engineer's voice without starting AMS2.

    python voice_test.py                      # active persona from config.yaml
    python voice_test.py --persona aussie_laidback
    python voice_test.py --list               # show available personas
    python voice_test.py --redesign           # force a fresh Voice Design for this persona
    python voice_test.py --save               # also write each line to data/voice_samples/test_*.wav
    python voice_test.py --stream-debug       # diagnose streaming: prints every event the voice stream sends

Plays a handful of radio calls across the emotional range so you can hear how the persona,
accent and emotions come across with Gemini 3.8 Flash TTS.
"""

import argparse
import sys
import wave
from pathlib import Path

import yaml

from engineer.persona import load_persona
from voice.tts import GeminiTTS, TTS_SAMPLE_RATE
from voice.player import AudioPlayer

SAMPLE_CALLS = [
    ("relaxed, unhurried", "casual radio check", "Radio check. Reading you fine, how's the car feel on the out-lap?"),
    ("clipped and tense", "rapid-fire, slightly raised over the engine", "Car behind, half a second. Defend the inside into Turn 1."),
    ("urgent, alarmed", "loud, fast and insistent", "Box, box, box. <short pause> Front left is going down, box this lap."),
    ("quietly pleased", "warm and understated, a smile in the voice", "That's P5. Lovely move, now build the gap."),
    ("controlled frustration", "low and firm, spaced-out words", "<exhales> Lost two places there. Reset, plenty of race left."),
    ("relieved", "exhaled, softening at the end", "<breath> Fuel's fine to the flag. No more stops, just bring it home."),
    ("elated", "raised, fast, full of energy", "Chequered flag, P2! Absolutely mega drive, well done!"),
    ("dry, deadpan", "slow, flat and understated", "Yes, we saw the gravel trap. Thanks for the tour."),
]


def stream_debug(tts):
    """Prints what the Gemini TTS stream actually sends, with timings (for troubleshooting)."""
    import base64
    import time as _t
    line = "Radio check, reading you five by five. Grid is forming up, lights out shortly."
    transcript, cues = tts.prepare_transcript(line)
    voice = tts.ensure_voice()
    style = tts.build_style("relaxed", "casual radio check", cues)
    for model in [tts.primary_model] + tts.fallback_models:
        print(f"\n=== streaming test on {model} (voice {voice})")
        kw = tts._interaction_kwargs(model, transcript, style, voice, "audio/l16")
        kw["stream"] = True
        t0 = _t.time()
        audio_bytes = 0
        try:
            stream = tts._create_interaction(kw)
            print(f"  request accepted after {_t.time() - t0:.2f}s")
            for ev in stream:
                data = tts._audio_from_event(ev)
                n = len(base64.b64decode(data)) if isinstance(data, str) else (len(data) if data else 0)
                audio_bytes += n
                print(f"  {_t.time() - t0:6.2f}s  {getattr(ev, 'event_type', type(ev).__name__):22s} "
                      f"audio {n:7d} B  (total {audio_bytes / 2 / TTS_SAMPLE_RATE:5.2f}s of speech)")
                if _t.time() - t0 > 30:
                    print("  ... stopping after 30s")
                    break
            dur = _t.time() - t0
            secs = audio_bytes / 2 / TTS_SAMPLE_RATE
            print(f"  done in {dur:.2f}s: {secs:.2f}s of speech -> {secs / dur if dur else 0:.2f}x real time")
        except Exception as e:
            print(f"  ERROR after {_t.time() - t0:.2f}s: {e!r}")
    print("\n=== non-streaming test")
    t0 = _t.time()
    audio = tts.synthesize(line, emotion="relaxed", delivery="casual radio check")
    print(f"  {'OK' if audio is not None else 'FAILED'} in {_t.time() - t0:.2f}s"
          + (f", {len(audio) / TTS_SAMPLE_RATE:.2f}s of speech" if audio is not None else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--persona", help="persona key from config 'personas:' section")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--redesign", action="store_true")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--stream-debug", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    if args.list:
        for key, p in (cfg.get("personas") or {}).items():
            print(f"  {key:22s} {p.get('display_name', '')}  [{p.get('accent', '')}]")
        return
    if args.persona:
        cfg.setdefault("engineer", {})["persona"] = args.persona

    persona = load_persona(cfg)
    gem = cfg.get("gemini", {}) or {}
    tts_cfg = cfg.get("tts", {}) or {}
    tts = GeminiTTS(
        api_key=gem.get("api_key"),
        model_name=tts_cfg.get("model", "gemini-3.8-flash-tts"),
        persona=persona,
        fallback_models=tts_cfg.get("fallback_models", ["gemini-3.8-flash-lite-tts"]),
        legacy_model=tts_cfg.get("legacy_fallback_model", "gemini-3.1-flash-tts-preview"),
    )
    if not tts.client:
        sys.exit("No Gemini API key found in config.yaml or GEMINI_API_KEY.")

    if args.redesign and persona.voice.mode == "designed":
        tts._invalidate_cached_voice()
        tts.design_voice(force=True)
    print(f"\nPersona: {persona.summary()}  |  voice: {tts.ensure_voice()}  |  model: {tts.primary_model}\n")

    if args.stream_debug:
        stream_debug(tts)
        return

    player = AudioPlayer(volume=cfg.get("engineer", {}).get("volume", 1.2),
                         radio_effect=cfg.get("engineer", {}).get("radio_effect", True))
    out_dir = Path("data/voice_samples")
    for i, (emotion, delivery, line) in enumerate(SAMPLE_CALLS, 1):
        print(f"[{i}] {emotion:24s} | {line}")
        audio = tts.synthesize(line, emotion=emotion, delivery=delivery)
        if audio is None:
            print("    (no audio returned -- see log above)")
            continue
        if args.save:
            out_dir.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_dir / f"test_{persona.key}_{i}.wav"), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(TTS_SAMPLE_RATE)
                wf.writeframes(audio.tobytes())
        player.play_pcm(audio, sample_rate=TTS_SAMPLE_RATE, block=True)


if __name__ == "__main__":
    main()
