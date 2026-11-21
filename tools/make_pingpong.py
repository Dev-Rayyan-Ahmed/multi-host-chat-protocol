# tools/make_pingpong.py
"""
Build the seamless ping-pong background video used by the Connection and Login screens.

    python -m tools.make_pingpong

Input : assets/backgrounds/purple_black_hole.mp4            (never modified)
Output: assets/backgrounds/purple_black_hole_pingpong.mp4   (forward + reversed, no audio)
        assets/backgrounds/purple_black_hole_poster.jpg     (first frame, shown before playback starts)

Seamless joins: the reversed half drops its first frame (identical to the last forward frame)
and its last frame (identical to the first forward frame), so neither the forward→reverse turn
nor the wrap-around back to the start shows a duplicated, "paused" frame.

FFmpeg is required. It is taken from PATH, or from the `imageio-ffmpeg` package
(`pip install imageio-ffmpeg`), which ships a static FFmpeg binary.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BG = ROOT / "assets" / "backgrounds"
SRC = BG / "purple_black_hole.mp4"
OUT = BG / "purple_black_hole_pingpong.mp4"
POSTER = BG / "purple_black_hole_poster.jpg"


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def probe(ffmpeg: str, src: Path) -> tuple[int, str]:
    """Returns (exact video frame count, frame rate string) by decoding once."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src), "-map", "0:v:0", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    frames = re.findall(r"frame=\s*(\d+)", r.stderr)
    fps = re.search(r"(\d+(?:\.\d+)?) fps", r.stderr)
    if not frames:
        raise RuntimeError("Could not count video frames:\n" + r.stderr[-1500:])
    return int(frames[-1]), (fps.group(1) if fps else "30")


def main() -> int:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("FFmpeg not found. Install it (winget install Gyan.FFmpeg, or pip install imageio-ffmpeg) "
              "and run this script again. The app shows a static poster until the ping-pong video exists.")
        return 2
    if not SRC.exists():
        print(f"Source video missing: {SRC}")
        return 2
    assert OUT.resolve() != SRC.resolve(), "refusing to overwrite the source video"

    n, fps = probe(ffmpeg, SRC)
    print(f"Source: {SRC.name}, {n} frames @ {fps} fps")
    graph = (
        "[0:v]split=2[fw][rv0];"
        f"[rv0]reverse,trim=start_frame=1:end_frame={n - 1},setpts=PTS-STARTPTS[rv];"
        "[fw][rv]concat=n=2:v=1:a=0,format=yuv420p[out]"
    )
    cmd = [
        ffmpeg, "-hide_banner", "-y", "-i", str(SRC),
        "-filter_complex", graph, "-map", "[out]",
        "-an",                                   # no audio
        "-c:v", "libx264", "-preset", "slow", "-crf", "16",  # visually lossless
        "-r", fps,                               # keep the original frame rate
        "-movflags", "+faststart",
        str(OUT),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    subprocess.run([ffmpeg, "-hide_banner", "-y", "-i", str(SRC), "-frames:v", "1", "-q:v", "2", str(POSTER)],
                   check=True, capture_output=True)
    out_n, _ = probe(ffmpeg, OUT)
    print(f"Wrote {OUT.name}: {out_n} frames (expected {2 * n - 2}), poster {POSTER.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
