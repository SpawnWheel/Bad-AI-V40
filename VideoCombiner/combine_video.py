import json
import os
import subprocess
import sys
from pydub import AudioSegment
from pydub.silence import detect_leading_silence

# Padding kept either side of the commentary when clipping the video
PAD_BEFORE_SEC = 0.5
PAD_AFTER_SEC = 0.5

# Volume of the original game audio underneath the commentary (0 = commentary only)
GAME_AUDIO_VOLUME = 0.20

# The stitched timeline is built on digital silence, so anything above this is commentary
SILENCE_THRESHOLD_DBFS = -60.0

AUDIO_EXTENSIONS = ('.wav', '.mp3')

# Hardware encoder tried first (much faster), software encoder used as a fallback
ENCODERS = {
    'h264': ('h264_nvenc', 'libx264'),
    'hevc': ('hevc_nvenc', 'libx265'),
}


def probe_video(video_path):
    """Returns the source video's codec, frame rate, bitrate, duration and audio info via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=codec_type,codec_name,bit_rate,r_frame_rate,avg_frame_rate:format=duration,bit_rate",
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


def build_audio_filter(has_game_audio):
    commentary = "[1:a]aresample=48000,aformat=channel_layouts=stereo"
    if has_game_audio and GAME_AUDIO_VOLUME > 0:
        # amix follows the game audio's length; adding apad here deadlocks ffmpeg
        return (
            f"[0:a:0]aresample=48000,aformat=channel_layouts=stereo,volume={GAME_AUDIO_VOLUME}[game];"
            f"{commentary}[comm];"
            "[game][comm]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"
        )
    # Pad the commentary with silence so it covers the trailing clip padding
    return f"{commentary},apad[aout]"


def build_command(video_path, audio_path, output_path, info, clip_start, clip_duration, encoder):
    bitrate = info['video_bitrate']
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-stats", "-loglevel", "error",
        # Seek both inputs by the same amount so the existing sync is preserved
        "-ss", f"{clip_start:.3f}", "-i", video_path,
        "-ss", f"{clip_start:.3f}", "-i", audio_path,
        "-t", f"{clip_duration:.3f}",
        "-filter_complex", build_audio_filter(info['has_audio']),
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", encoder,
    ]
    if encoder.endswith('_nvenc'):
        cmd += ["-preset", "p5", "-tune", "hq", "-rc", "vbr"]
    else:
        cmd += ["-preset", "medium"]
    cmd += [
        "-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.5)), "-bufsize", str(bitrate * 2),
        "-r", info['frame_rate'], "-fps_mode", "cfr", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", str(info['audio_bitrate']),
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd


def main(video_path, audio_path, output_path):
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
    if info['duration'] > 0:
        clip_end = min(clip_end, info['duration'])
    clip_duration = clip_end - clip_start
    if clip_duration <= 0:
        print("Error: The commentary starts after the end of the video.")
        return
    print(f"  Clipping video to {clip_start:.2f}s - {clip_end:.2f}s ({clip_duration:.1f}s)")

    encoders = ENCODERS.get(info['codec'], ENCODERS['h264'])
    for encoder in encoders:
        print(f"Encoding with {encoder} -> {output_path}")
        cmd = build_command(video_path, audio_path, output_path, info, clip_start, clip_duration, encoder)
        if subprocess.run(cmd).returncode == 0:
            print("Done!")
            return
        print(f"  {encoder} failed.")

    print("Error: Video export failed.")


if __name__ == "__main__":
    video_file = None
    audio_file = None
    # The launcher passes the inputs as positional args; tell them apart by extension
    for arg in sys.argv[1:]:
        if arg.lower().endswith(AUDIO_EXTENSIONS):
            audio_file = arg
        else:
            video_file = arg

    if not video_file:
        video_file = input("Enter the path to the video file: ").strip().strip('"')
    if not audio_file:
        audio_file = input("Enter the path to the stitched commentary audio: ").strip().strip('"')

    project_path = os.environ.get("R3E_PROJECT_PATH")
    if project_path:
        output_file = os.path.join(project_path, f"{os.path.basename(project_path)}_final.mp4")
    else:
        output_file = os.path.splitext(video_file)[0] + "_final.mp4"

    main(video_file, audio_file, output_file)
