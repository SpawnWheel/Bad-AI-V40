import json
import re
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pydub import AudioSegment
from pydub.silence import detect_leading_silence

# Padding kept either side of the commentary when clipping the video
PAD_BEFORE_SEC = 0.5
PAD_AFTER_SEC = 0.5

# Volume of the original game audio underneath the commentary (0 = commentary only), after GAME_LEVELER
GAME_AUDIO_VOLUME = 1.0

# The game audio swings ~20 LU between a distant TV shot and an onboard (and the sims record it hot, around
# -9 LUFS). A slow compressor takes the edge off the loud shots, then loudnorm rides the level to GAME_LUFS
# (the commentary track sits at -16 LUFS), leaving a range of about 5 LU so crashes still stand out.
GAME_LUFS = -15
GAME_LEVELER = ("acompressor=threshold=-26dB:ratio=3:attack=40:release=500:knee=6,"
                f"loudnorm=I={GAME_LUFS}:TP=-2:LRA=5,aresample=48000")

# How hard the game ducks while the commentators talk (higher ratio = quieter under speech).
# Ratio 2.5 takes roughly 8 dB off the game under normal speech; it was 5 (~14 dB) and buried the cars.
DUCK_THRESHOLD = 0.05
DUCK_RATIO = 2.5

# Starting grid intro (racefiles.grid_intro_window): the game fades in as the replay starts. The music starts with
# the grid (when the full video opens), sits under the commentators as an even bed (they talk over the whole grid,
# so ducking only made it sound faded) and fades out over MUSIC_FADE_SEC as the replay starts moving.
GAME_FADE_IN_SEC = 1.0
MUSIC_FADE_IN_SEC = 0.4
MUSIC_FADE_SEC = 2.5
MUSIC_TAIL_SEC = 2.0      # ... ending this long after the replay starts
MUSIC_LUFS = {"Quiet": -27, "Normal": -23, "Loud": -19}  # the commentary track sits at -16 LUFS

# The stitched timeline is built on digital silence, so anything above this is commentary
SILENCE_THRESHOLD_DBFS = -60.0

AUDIO_EXTENSIONS = ('.wav', '.mp3')

# Broadcast graphics (GraphicsOverlay package lives in the app root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import racefiles  # noqa: E402
try:
    from GraphicsOverlay import renderer as graphics
except Exception as e:  # graphics are optional; never block a plain export
    graphics = None
    print(f"Graphics overlay unavailable: {e}")

# Hardware encoder tried first (much faster), software encoder used as a fallback
ENCODERS = {
    'h264': ('h264_nvenc', 'libx264'),
    'hevc': ('hevc_nvenc', 'libx265'),
}
# Quality targets (lower = better and bigger); ~20 is visually clean for racing footage
NVENC_CQ = 20
X264_CRF = 19


def probe_video(video_path):
    """Returns the source video's codec, frame rate, bitrate, duration and audio info via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=codec_type,codec_name,bit_rate,r_frame_rate,avg_frame_rate,width,height:format=duration,bit_rate",
         "-of", "json", video_path],
        capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)
    streams = data.get('streams', [])
    fmt = data.get('format', {})

    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    if video is None:
        raise ValueError("No video stream found in the input file.")

    frame_rate = video.get('r_frame_rate')
    if not frame_rate or frame_rate.startswith('0'):
        frame_rate = video.get('avg_frame_rate')

    audio_bitrate = int(audio['bit_rate']) if audio and audio.get('bit_rate') else 192000
    video_bitrate = video.get('bit_rate')
    if video_bitrate:
        video_bitrate = int(video_bitrate)
    else:
        # Some containers (e.g. MKV) only report the overall bitrate
        video_bitrate = int(fmt.get('bit_rate', 0)) - (audio_bitrate if audio else 0)

    return {
        'codec': video.get('codec_name'),
        'size': (int(video.get('width', 1920)), int(video.get('height', 1080))),
        'frame_rate': frame_rate,
        'video_bitrate': video_bitrate,
        'duration': float(fmt.get('duration', 0)),
        'has_audio': audio is not None,
        'audio_bitrate': audio_bitrate,
    }


def find_commentary_bounds(audio_path):
    """Returns (start_sec, end_sec) of the audible commentary within the stitched audio file."""
    audio = AudioSegment.from_file(audio_path)
    if len(audio) == 0 or audio.dBFS == float('-inf'):
        raise ValueError("The commentary audio file is silent.")

    leading_ms = detect_leading_silence(audio, silence_threshold=SILENCE_THRESHOLD_DBFS, chunk_size=10)
    trailing_ms = detect_leading_silence(audio.reverse(), silence_threshold=SILENCE_THRESHOLD_DBFS, chunk_size=10)
    return leading_ms / 1000.0, (len(audio) - trailing_ms) / 1000.0


def measure_lufs(path, start, duration):
    """Integrated loudness of `duration` seconds of an audio file from `start`, or None."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
                          "-i", path, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    found = re.findall(r"I:\s*(-?[\d.]+) LUFS", out)
    try:
        return float(found[-1]) if found else None
    except ValueError:
        return None


def music_filter(music):
    """The grid music -> [music]: the part that plays (it starts at output time music['start'], before the clip
    when that is negative), set to its level with a fixed gain, faded in and out, padded to the clip's length."""
    start, seek = max(0.0, music['start']), max(0.0, -music['start'])
    length = music['end'] - start
    fade = min(MUSIC_FADE_SEC, length / 2)
    fade_in = MUSIC_FADE_IN_SEC if seek == 0 else 0.15
    return (f"[{music['input']}:a]atrim=start={seek:.3f}:duration={length:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,aformat=channel_layouts=stereo,volume={music['gain_db']:.2f}dB,"
            f"afade=t=in:st=0:d={fade_in},afade=t=out:st={length - fade:.3f}:d={fade:.3f},"
            f"adelay={int(start * 1000)}:all=1,apad=whole_dur={music['clip_duration']:.3f}[music]")


def build_audio_filter(has_game_audio, hold_rel=None, music=None):
    """hold_rel: output time the replay starts playing after the grid intro (the game is silent before it).
    music: the grid music (see grid_music), or None."""
    commentary = "[1:a]aresample=48000,aformat=channel_layouts=stereo"
    if has_game_audio and GAME_AUDIO_VOLUME > 0:
        # The game ducks under the commentators while they talk and comes back up in the gaps.
        # amix follows the game audio's length; adding apad here deadlocks ffmpeg
        intro = f",afade=t=in:st={hold_rel:.3f}:d={GAME_FADE_IN_SEC}" if hold_rel else ""
        f = (
            f"[0:a:0]aresample=48000,aformat=channel_layouts=stereo,{GAME_LEVELER},volume={GAME_AUDIO_VOLUME}{intro}[game];"
            f"{commentary},asplit=2[comm][key];"
            f"[game][key]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:attack=15:release=450:makeup=1[ducked];"
        )
        if music:
            return (f + music_filter(music) + ";"
                    "[ducked][comm][music]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95[aout]")
        return f + "[ducked][comm]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"
    if music:
        return (f"{commentary},apad=whole_dur={music['clip_duration']:.3f}[comm];" + music_filter(music) + ";"
                "[comm][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    # Pad the commentary with silence so it covers the trailing clip padding
    return f"{commentary},apad[aout]"


def build_command(video_path, audio_path, output_path, info, clip_start, clip_duration, encoder, overlay=None,
                  intro=None):
    """intro: {'hold_rel', 'hold_png', 'music' (see grid_music)} for the starting grid, or None."""
    bitrate = info['video_bitrate']
    nvenc = encoder.endswith('_nvenc')
    cmd = ["ffmpeg", "-y", "-hide_banner", "-stats", "-loglevel", "error"]
    if nvenc:
        # Decode on the GPU too (frames come back to the CPU for the overlay); falls back on its own
        cmd += ["-hwaccel", "cuda"]
    cmd += [
        # Seek both inputs by the same amount so the existing sync is preserved
        "-ss", f"{clip_start:.3f}", "-i", video_path,
        "-ss", f"{clip_start:.3f}", "-i", audio_path,
    ]
    fps = float(Fraction(info['frame_rate']))
    n_inputs = 2
    video_map, base = "0:v:0", "0:v"
    video_filters = []
    if overlay:
        # The overlay timeline already starts at clip_start, so it is not seeked
        cmd += ["-f", "concat", "-safe", "0", "-i", overlay]
        overlay_input = n_inputs
        n_inputs += 1
    hold_rel = music = None
    if intro:
        hold_rel = intro['hold_rel']
        # Before the replay starts, one frame of it is held (the recording there is the sim's menus)
        cmd += ["-loop", "1", "-framerate", f"{fps:.6g}", "-t", f"{clip_duration:.3f}", "-i", intro['hold_png']]
        w, h = info['size']
        video_filters.append(f"[{n_inputs}:v]scale={w}:{h},format=yuv420p,setsar=1[hold];"
                             f"[0:v][hold]overlay=0:0:enable='lt(t,{hold_rel:.3f})':eof_action=pass[base]")
        n_inputs += 1
        base, video_map = "base", "[base]"
        if intro.get('music'):
            cmd += ["-i", intro['music']['path']]
            music = dict(intro['music'], input=n_inputs, clip_duration=clip_duration)
            n_inputs += 1
    if overlay:
        video_filters.append(graphics.overlay_filter(f"{fps:.6g}", info['size'], overlay_input=overlay_input,
                                                     base=base))
        video_map = "[vout]"
    filters = ";".join(video_filters + [build_audio_filter(info['has_audio'], hold_rel, music)])
    cmd += [
        "-t", f"{clip_duration:.3f}",
        "-filter_complex", filters,
        "-map", video_map, "-map", "[aout]",
        "-c:v", encoder,
    ]
    # Constant quality rather than the source's bitrate: re-encoding a game capture at the same bitrate
    # turned fast motion (grass, gravel, camera cuts) into blocks. The cap keeps the file size sane.
    cap = max(int(bitrate * 2.5), 30_000_000)
    if nvenc:
        cmd += ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(NVENC_CQ), "-b:v", "0",
                "-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8", "-rc-lookahead", "32",
                "-bf", "3", "-b_ref_mode", "middle", "-profile:v", "high"]
    else:
        cmd += ["-preset", "medium", "-crf", str(X264_CRF)]
    cmd += [
        "-maxrate", str(cap), "-bufsize", str(cap * 2),
        "-r", info['frame_rate'], "-fps_mode", "cfr", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", str(info['audio_bitrate']),
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd


def prepare_graphics(project_path, info, clip_start, clip_end):
    """Renders the broadcast graphics overlay if Graphics Setup has been run for this project."""
    if graphics is None or not graphics.graphics_enabled(project_path):
        return None, None
    print("Rendering broadcast graphics...")
    out_dir = os.path.join(project_path, "graphics_frames")
    try:
        fps = float(Fraction(info['frame_rate']))
        concat = graphics.render_overlay(project_path, clip_start, clip_end, out_dir, fps=fps)
        return concat, out_dir
    except Exception as e:
        print(f"  Graphics failed ({e}); exporting without graphics.")
        return None, None


def prepare_grid_intro(project_path, video_path, clip_start, clip_duration):
    """The starting grid intro for this clip, when the commentary was written with one: grabs the frame held
    under the grid and finds the music. None without one (or when the clip starts after the replay does)."""
    if not project_path:
        return None
    window = racefiles.grid_intro_window(project_path)
    if not window:
        return None
    hold_rel = window['hold'] - clip_start
    if hold_rel <= 0 or hold_rel >= clip_duration:
        return None
    hold_png = racefiles.grid_hold_frame_path(project_path, window['hold'])
    os.makedirs(os.path.dirname(hold_png), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{window['hold']:.3f}", "-i", video_path,
                    "-frames:v", "1", hold_png], check=False)
    if not os.path.exists(hold_png):
        print("  Could not grab the frame for the starting grid; the video plays as recorded.")
        return None
    settings = racefiles.grid_intro_settings(project_path)
    print(f"Starting grid: frame at {window['hold']:.1f}s held for the first {hold_rel:.1f}s")
    return {'hold_rel': hold_rel, 'hold_png': hold_png, 'music': grid_music(settings, window, clip_start, hold_rel)}


def grid_music(settings, window, clip_start, hold_rel):
    """The grid music for this clip: {'path', 'start' (output time; negative when the clip starts after it),
    'end', 'gain_db'}, or None. It starts with the grid, where the full video opens (PAD_BEFORE_SEC before the
    grid's commentary), so a test clip that starts earlier or later hears it at the same moment."""
    path = racefiles.grid_music_path(settings)
    if settings.get('music') and not path:
        print(f"  Grid music not found in the Music folder: {settings['music']}")
    if not path:
        return None
    start = window['start'] - PAD_BEFORE_SEC - clip_start
    end = hold_rel + MUSIC_TAIL_SEC
    if end - max(0.0, start) < 1.0:
        return None
    # A fixed gain from the loudness of the part that plays (loudnorm rode the level and shifted the timing)
    target = MUSIC_LUFS.get(settings.get('music_level'), MUSIC_LUFS["Normal"])
    lufs = measure_lufs(path, 0.0, end - start)  # all of what the full video plays, so every clip gets one level
    gain = max(-30.0, min(20.0, target - lufs)) if lufs is not None else target + 14
    print(f"  Music: {os.path.basename(path)} ({settings.get('music_level')}) from "
          f"{window['start'] - PAD_BEFORE_SEC:.1f}s in the video, {gain:+.1f} dB")
    return {'path': path, 'start': start, 'end': end, 'gain_db': gain}


def main(video_path, audio_path, output_path, use_graphics=True, preview=None):
    for path in (video_path, audio_path):
        if not os.path.exists(path):
            print(f"Error: File not found: {path}")
            return

    print(f"Video: {video_path}")
    print(f"Audio: {audio_path}")

    info = probe_video(video_path)
    print(f"  Source: {info['codec']}, {info['frame_rate']} fps, "
          f"{info['video_bitrate'] / 1_000_000:.2f} Mbps, {info['duration']:.1f}s")

    print("Locating commentary in audio...")
    audio_start, audio_end = find_commentary_bounds(audio_path)
    print(f"  Commentary runs from {audio_start:.2f}s to {audio_end:.2f}s")

    clip_start = max(0.0, audio_start - PAD_BEFORE_SEC)
    clip_end = audio_end + PAD_AFTER_SEC
    if preview:
        # Short test render (used by Graphics Setup): (start, duration) in video time
        clip_start, clip_end = preview[0], preview[0] + preview[1]
    if info['duration'] > 0:
        clip_end = min(clip_end, info['duration'])
    clip_duration = clip_end - clip_start
    if clip_duration <= 0:
        print("Error: The commentary starts after the end of the video.")
        return
    print(f"  Clipping video to {clip_start:.2f}s - {clip_end:.2f}s ({clip_duration:.1f}s)")

    project_path = os.environ.get("R3E_PROJECT_PATH")
    intro = prepare_grid_intro(project_path, video_path, clip_start, clip_duration)
    overlay, frames_dir = (None, None)
    if use_graphics and project_path:
        overlay, frames_dir = prepare_graphics(project_path, info, clip_start, clip_end)

    encoders = ENCODERS.get(info['codec'], ENCODERS['h264'])
    for encoder in encoders:
        print(f"Encoding with {encoder} -> {output_path}")
        cmd = build_command(video_path, audio_path, output_path, info, clip_start, clip_duration, encoder, overlay,
                            intro)
        if subprocess.run(cmd).returncode == 0:
            if frames_dir:
                shutil.rmtree(frames_dir, ignore_errors=True)
            print("Done!")
            return
        print(f"  {encoder} failed.")

    print("Error: Video export failed.")


if __name__ == "__main__":
    video_file = None
    audio_file = None
    use_graphics = True
    preview = None
    args = sys.argv[1:]
    if "--no-graphics" in args:
        args.remove("--no-graphics")
        use_graphics = False
    if "--preview" in args:
        i = args.index("--preview")
        preview = (float(args[i + 1]), float(args[i + 2]))
        del args[i:i + 3]
    # The launcher passes the inputs as positional args; tell them apart by extension
    for arg in args:
        if arg.lower().endswith(AUDIO_EXTENSIONS):
            audio_file = arg
        else:
            video_file = arg

    if not video_file:
        video_file = input("Enter the path to the video file: ").strip().strip('"')
    if not audio_file:
        audio_file = input("Enter the path to the stitched commentary audio: ").strip().strip('"')

    project_path = os.environ.get("R3E_PROJECT_PATH")
    if preview and project_path:
        output_file = os.path.join(project_path, "debug", "graphics_preview.mp4")
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
    elif os.environ.get("BADAI_OUTPUT"):
        output_file = os.environ["BADAI_OUTPUT"]
    elif project_path:
        output_file = os.path.join(project_path, f"{os.path.basename(project_path)}_final.mp4")
    else:
        output_file = os.path.splitext(video_file)[0] + "_final.mp4"

    main(video_file, audio_file, output_file, use_graphics, preview)
