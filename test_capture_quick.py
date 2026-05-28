"""Quick capture test - verifies DeckLink COM capture works."""

import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))


def test_hardware_capture(max_frames=5, timeout=15):
    """Start RealDeckLinkCapture, wait for frames, report results."""
    log.info("--- Testing RealDeckLinkCapture (COM API) ---")
    from src.capture import RealDeckLinkCapture

    cap = RealDeckLinkCapture(device_index=0, queue_size=8, audio_channels=8)
    cap.start()

    if not cap.is_running:
        log.error("Capture failed to start")
        return False

    log.info(f"Capture started, waiting up to {timeout}s for frames...")
    frames = []
    audio_packets = []
    deadline = time.time() + timeout

    while time.time() < deadline and len(frames) < max_frames:
        frame = cap.get_frame(timeout=1.0)
        if frame:
            frames.append(frame)
            log.info(f"  Frame {frame.frame_number}: {frame.width}x{frame.height} "
                     f"fmt={frame.format} hw_pts={frame.hw_pts} valid={frame.hw_pts_valid}")

        # Drain audio
        while True:
            audio = cap.get_audio(timeout=0)
            if audio is None:
                break
            audio_packets.append(audio)

    cap.stop()

    log.info(f"Captured {len(frames)} video frames, {len(audio_packets)} audio packets")
    if not frames:
        log.warning("No frames received — check signal source on DeckLink SDI Micro")

    return len(frames) > 0


def test_simulated_capture(input_file, max_frames=5):
    """Test SimulatedCapture with a video file (no hardware needed)."""
    log.info(f"--- Testing SimulatedCapture from {input_file} ---")
    if not os.path.exists(input_file):
        log.warning(f"Input file not found: {input_file}")
        return None

    from src.capture import SimulatedCapture

    cap = SimulatedCapture(input_file, queue_size=8, audio_channels=2)
    cap.start()
    time.sleep(0.5)

    frames = []
    for _ in range(max_frames):
        frame = cap.get_frame(timeout=2.0)
        if frame is None:
            break
        frames.append(frame)
        log.info(f"  Frame {frame.frame_number}: {frame.width}x{frame.height} "
                 f"fmt={frame.format} hw_pts_valid={frame.hw_pts_valid}")

    cap.stop()
    log.info(f"Got {len(frames)}/{max_frames} frames from simulation")
    return len(frames) > 0


if __name__ == '__main__':
    log.info("=== Capture Test ===")

    hw_ok = test_hardware_capture(max_frames=5, timeout=15)

    sim_file = r'C:\temp\test_video.mp4'
    sim_ok = test_simulated_capture(sim_file)

    log.info("=== Results ===")
    log.info(f"Hardware capture (DeckLink SDI Micro): {'PASS' if hw_ok else 'FAIL (no signal or driver issue)'}")
    if sim_ok is None:
        log.info("Simulated capture: SKIP (no test video at C:\\temp\\test_video.mp4)")
    else:
        log.info(f"Simulated capture: {'PASS' if sim_ok else 'FAIL'}")
