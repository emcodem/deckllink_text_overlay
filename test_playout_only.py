"""Playout-only test: generate synthetic frames and push to DeckLink playout device.

Bypasses capture entirely. Feeds color bars video + sine-wave audio directly into
DeckLinkPlayout at the configured display mode rate.

Usage:
    python test_playout_only.py [--duration 10] [--device 1] [--fps 25] [--width 1920] [--height 1080]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from src.logger import setup_logging
from src.capture import Frame, AudioSample
from src.playout import DeckLinkPlayout

logger = logging.getLogger(__name__)

# Audio config
SAMPLE_RATE = 48000
AUDIO_CHANNELS = 8
SAMPLES_PER_FRAME_25FPS = SAMPLE_RATE // 25  # 1920 samples per frame at 25fps


def make_color_bars(width: int, height: int, frame_number: int) -> np.ndarray:
    """Generate SMPTE-style color bars as a BGR numpy array."""
    colors_bgr = [
        (192, 192, 192),  # white (75%)
        (192, 192,   0),  # yellow
        (  0, 192, 192),  # cyan
        (  0, 192,   0),  # green
        (192,   0, 192),  # magenta
        (192,   0,   0),  # red
        (  0,   0, 192),  # blue
    ]
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    bar_w = width // len(colors_bgr)
    for i, color in enumerate(colors_bgr):
        x0 = i * bar_w
        x1 = x0 + bar_w if i < len(colors_bgr) - 1 else width
        frame[:, x0:x1] = color

    # Burn frame number as a moving white block so motion is visible on the output
    block_x = (frame_number * 4) % (width - 40)
    frame[height - 30: height - 10, block_x: block_x + 40] = (255, 255, 255)

    return frame


def make_audio(frame_number: int, samples_per_frame: int) -> np.ndarray:
    """Generate a 1 kHz sine wave, one frame's worth of samples."""
    t_start = frame_number * samples_per_frame / SAMPLE_RATE
    t = np.linspace(t_start, t_start + samples_per_frame / SAMPLE_RATE,
                    samples_per_frame, endpoint=False)
    sine = (np.sin(2 * np.pi * 1000 * t) * 0.5 * 32767).astype(np.int16)
    # Spread sine to all channels, silence the rest
    audio = np.zeros((samples_per_frame, AUDIO_CHANNELS), dtype=np.int16)
    audio[:, 0] = sine
    audio[:, 1] = sine
    return audio


def run(duration: float, device: int, fps: int, width: int, height: int):
    setup_logging()

    logger.info("=" * 60)
    logger.info("Playout-only test starting")
    logger.info(f"  device={device}, {width}x{height} @ {fps}fps, {duration}s")
    logger.info("=" * 60)

    playout = DeckLinkPlayout(device_index=device)
    playout.start()

    if not playout.is_running:
        logger.error("Playout failed to start — check device index and DeckLink drivers")
        sys.exit(1)

    frame_interval = 1.0 / fps
    samples_per_frame = SAMPLE_RATE // fps
    total_frames = int(duration * fps)

    logger.info(f"Sending {total_frames} frames...")

    start = time.perf_counter()
    for i in range(total_frames):
        frame_start = time.perf_counter()

        video_data = make_color_bars(width, height, i)
        frame = Frame(
            data=video_data,
            format="BGR24",
            width=width,
            height=height,
            framerate=(fps, 1),
            timestamp=i * frame_interval,
            frame_number=i,
        )
        playout.put_frame(frame)

        audio_data = make_audio(i, samples_per_frame)
        audio = AudioSample(
            data=audio_data,
            sample_rate=SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            timestamp=i * frame_interval,
        )
        playout.put_audio(audio)

        if i % fps == 0:
            logger.info(f"  frame {i}/{total_frames} ({i // fps}s elapsed)")

        # Pace to real-time
        elapsed = time.perf_counter() - frame_start
        sleep_time = frame_interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    actual_duration = time.perf_counter() - start
    logger.info(f"Done — sent {total_frames} frames in {actual_duration:.2f}s "
                f"(dropped by playout: {playout._dropped_frames})")

    playout.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Playout-only test with synthetic color bars")
    parser.add_argument("--duration", type=float, default=10.0, help="Seconds to run (default 10)")
    parser.add_argument("--device",   type=int,   default=1,    help="DeckLink device index (default 1)")
    parser.add_argument("--fps",      type=int,   default=25,   help="Frames per second (default 25)")
    parser.add_argument("--width",    type=int,   default=1920, help="Frame width (default 1920)")
    parser.add_argument("--height",   type=int,   default=1080, help="Frame height (default 1080)")
    args = parser.parse_args()

    run(args.duration, args.device, args.fps, args.width, args.height)
