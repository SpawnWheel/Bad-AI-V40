"""Team radio sound for the drivers and engineers in the commentary.

Rougher than the live race engineer's filter on purpose: on a broadcast the radio is a recording of a
recording, so it gets a narrow telephone band, a squashed and slightly broken-up transmission, static and
crackle, and what the microphone picks up around the speaker - the engine and wind in the car, the pit wall
behind the engineer. Each transmission opens and closes with a short digital tone.

The tones are made here. To use your own, put radio_in.wav and/or radio_out.wav in the RadioSounds folder next
to this file (any sample rate, mono or stereo).
"""
import os

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt, sosfiltfilt

SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RadioSounds")

# Levels are dB relative to the voice
SETTINGS = {
    "driver": dict(band=(480, 2900), drive=5.0, bed_db=-13.0, static_db=-25.0, crackle_rate=12.0,
                   dropouts=2, codec_rate=8000),
    "engineer": dict(band=(420, 3100), drive=3.5, bed_db=-21.0, static_db=-27.0, crackle_rate=7.0,
                     dropouts=1, codec_rate=8000),
}


def _db(x):
    return 10 ** (x / 20.0)


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def _band(x, sr, lo, hi, order=4, zero_phase=True):
    sos = butter(order, [lo / (sr / 2), min(hi / (sr / 2), 0.99)], btype="band", output="sos")
    return sosfiltfilt(sos, x) if zero_phase and len(x) > 30 else sosfilt(sos, x)


def _lowpass(x, sr, hz, order=2):
    sos = butter(order, hz / (sr / 2), btype="low", output="sos")
    return sosfilt(sos, x)


def _smooth_random(rng, n, sr, rate_hz):
    """A slow random wobble between 0 and 1, changing about `rate_hz` times a second."""
    points = max(2, int(n / sr * rate_hz) + 2)
    return np.interp(np.arange(n), np.linspace(0, n, points), rng.random(points))


def _engine(rng, n, sr):
    """Engine and wind in the cockpit: a sawtooth-like drone that drifts with the revs, rumble and wind hiss."""
    t = np.arange(n) / sr
    revs = 95 + 70 * _smooth_random(rng, n, sr, 0.8)          # firing frequency, Hz
    phase = 2 * np.pi * np.cumsum(revs) / sr
    drone = sum(np.sin(k * phase + rng.random() * 6.28) / k for k in range(1, 14))
    drone *= 0.7 + 0.3 * np.sin(2 * np.pi * 7.3 * t)          # a little engine flutter
    rumble = _lowpass(rng.standard_normal(n), sr, 180)
    wind = _band(rng.standard_normal(n), sr, 900, 4000, order=2) * (0.4 + 0.6 * _smooth_random(rng, n, sr, 1.5))
    bed = drone / (_rms(drone) + 1e-9) + 0.6 * rumble / (_rms(rumble) + 1e-9) + 0.35 * wind / (_rms(wind) + 1e-9)
    return bed / (_rms(bed) + 1e-9)


def _pit_wall(rng, n, sr):
    """Behind the engineer: garage hubbub, a generator hum and a car going past on the straight now and then."""
    t = np.arange(n) / sr
    pink = np.cumsum(rng.standard_normal(n))
    pink = pink - _lowpass(pink, sr, 40)                       # roughly pink, without the DC drift
    hubbub = _band(pink, sr, 200, 3500, order=2) * (0.6 + 0.4 * _smooth_random(rng, n, sr, 3))
    hum = np.sin(2 * np.pi * 100 * t) + 0.5 * np.sin(2 * np.pi * 200 * t)
    bed = hubbub / (_rms(hubbub) + 1e-9) + 0.15 * hum / (_rms(hum) + 1e-9)
    if n > sr:  # a car passing: a whine that swells and falls in pitch
        mid = rng.uniform(0.3, 0.8) * n / sr
        swell = np.exp(-((t - mid) / 0.9) ** 2)
        pitch = 180 * (1 + 0.12 * np.tanh(-(t - mid) * 2.5))
        car = sum(np.sin(k * 2 * np.pi * np.cumsum(pitch) / sr) / k for k in range(1, 8)) * swell
        bed = bed + 1.6 * car / (_rms(car) + 1e-9) * float(np.max(swell) > 0.5)
    return bed / (_rms(bed) + 1e-9)


def _static(rng, n, sr, crackle_rate):
    """Hiss with crackle: band-limited noise plus sparse pops and short bursts."""
    hiss = rng.standard_normal(n)
    pops = np.zeros(n)
    count = rng.poisson(crackle_rate * n / sr)
    for pos in rng.integers(0, max(1, n - 200), size=count):
        length = int(rng.integers(20, 180))
        pops[pos:pos + length] += rng.standard_normal(length) * rng.uniform(2, 7) * np.hanning(length)
    flutter = 0.7 + 0.3 * _smooth_random(rng, n, sr, 6)
    return hiss * flutter + pops


def _tone(sr, freqs, seg_ms=55, gap_ms=12, level=0.5):
    """A short two- or three-step digital tone, like a radio channel opening or closing."""
    parts = []
    for f in freqs:
        n = int(sr * seg_ms / 1000)
        t = np.arange(n) / sr
        env = np.minimum(1, np.minimum(t, t[::-1]) / 0.004)
        parts.append(level * env * (np.sin(2 * np.pi * f * t) + 0.25 * np.sign(np.sin(2 * np.pi * f * t))))
        parts.append(np.zeros(int(sr * gap_ms / 1000)))
    return np.concatenate(parts[:-1])


def _load_sound(name, sr):
    """RadioSounds/<name>.wav as mono float at `sr`, or None."""
    path = os.path.join(SOUNDS_DIR, name + ".wav")
    if not os.path.exists(path):
        return None
    try:
        import soundfile as sf
        data, file_sr = sf.read(path, dtype="float32", always_2d=True)
        data = data.mean(axis=1)
        if file_sr != sr:
            from math import gcd
            g = gcd(int(sr), int(file_sr))
            data = resample_poly(data, int(sr) // g, int(file_sr) // g)
        peak = float(np.max(np.abs(data))) or 1.0
        return data / peak * 0.6
    except Exception as e:
        print(f"Could not read {path}: {e}")
        return None


def _codec(x, sr, codec_rate):
    """Down to a low sample rate and back: the dull, slightly gritty sound of a digital radio link."""
    from math import gcd
    g = gcd(int(sr), int(codec_rate))
    low = resample_poly(x, int(codec_rate) // g, int(sr) // g)
    low = np.round(low * 180) / 180                            # a touch of bit-crush
    back = resample_poly(low, int(sr) // g, int(codec_rate) // g)
    return back[:len(x)] if len(back) >= len(x) else np.pad(back, (0, len(x) - len(back)))


def apply_radio(audio, sr, role="driver", seed=None):
    """The voice as a broadcast team radio message: float32 mono at the same sample rate, a little longer
    (static before and after, and the open and close tones)."""
    s = SETTINGS.get(role, SETTINGS["driver"])
    rng = np.random.default_rng(seed)
    voice = np.asarray(audio, dtype=np.float64).flatten()
    if not len(voice):
        return np.zeros(1, dtype=np.float32)
    voice = voice / (np.max(np.abs(voice)) + 1e-9) * 0.8

    pre, post = int(sr * 0.30), int(sr * 0.22)
    voice = np.concatenate([np.zeros(pre), voice, np.zeros(post)])
    n = len(voice)
    level = _rms(voice[pre:n - post]) or 0.1

    # What the microphone hears: the voice and the noise around it
    bed = _engine(rng, n, sr) if role == "driver" else _pit_wall(rng, n, sr)
    mic = voice + bed * level * _db(s["bed_db"])

    # The transmission: band-limited, driven hard into a soft clipper, through a low-rate digital link
    x = _band(mic, sr, *s["band"])
    x = np.tanh(x / (np.max(np.abs(x)) + 1e-9) * s["drive"])
    x = _codec(x, sr, s["codec_rate"])
    x = _band(x, sr, *s["band"], order=2)

    # Static on top, in the same band, louder as the channel opens and closes (squelch)
    stat = _band(_static(rng, n, sr, s["crackle_rate"]), sr, s["band"][0] * 0.8, s["band"][1] * 1.3, order=2)
    stat = stat / (_rms(stat) + 1e-9) * _rms(x) * _db(s["static_db"])
    squelch = np.ones(n)
    squelch[:pre] = np.linspace(3.0, 1.2, pre)
    squelch[n - post:] = np.linspace(1.2, 2.5, post)
    x = x + stat * squelch

    # Dropouts: the signal ducks for a few tens of milliseconds
    for _ in range(s["dropouts"]):
        if n - post - pre < sr:
            break
        pos = int(rng.integers(pre + sr // 4, n - post - sr // 4))
        length = int(sr * rng.uniform(0.02, 0.05))
        x[pos:pos + length] *= np.hanning(length) * -0.8 + 1

    # Short fades so the noise doesn't click in and out
    fade = int(sr * 0.012)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)

    tone_in = _load_sound("radio_in", sr)
    if tone_in is None:
        tone_in = _band(_tone(sr, (1180, 1760)), sr, *s["band"], order=2)
    tone_out = _load_sound("radio_out", sr)
    if tone_out is None:
        tone_out = _band(_tone(sr, (1760, 1180), seg_ms=45), sr, *s["band"], order=2)
    out = np.concatenate([tone_in * np.max(np.abs(x)), np.zeros(int(sr * 0.02)), x,
                          np.zeros(int(sr * 0.015)), tone_out * np.max(np.abs(x))])
    out = out / (np.max(np.abs(out)) + 1e-9) * 0.89
    return out.astype(np.float32)
