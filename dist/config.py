"""Configuration settings for FFCapture."""

import os
import sys
from pathlib import Path
from .config_subtitles import (
    TEXT_FILE, TEXT_CHECK_INTERVAL,
    TEXT_FONT_PATH, TEXT_FONT_SIZE, TEXT_FONT_BOLD, TEXT_FONT_ITALIC,
    TEXT_COLOR, TEXT_BG_COLOR, TEXT_BG_ALPHA,
    TEXT_BG_PADDING_X, TEXT_BG_PADDING_Y,
    TEXT_OFFSET_BOTTOM, TEXT_ALIGN, TEXT_LINE_SPACING,
)

# Project paths — when frozen by PyInstaller, write logs next to the .exe
if getattr(sys, 'frozen', False):
    PROJECT_ROOT = Path(sys.executable).parent
else:
    PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
LOGS_DIR = PROJECT_ROOT / "logs"

# Create logs directory if it doesn't exist
LOGS_DIR.mkdir(exist_ok=True)

# DeckLink device configuration
CAPTURE_DEVICE_INDEX = 0      # Primary DeckLink card for input
#PLAYOUT_DEVICE_INDEX = 1      # Secondary DeckLink card for output
SIMULATE_HARDWARE = False      # Set True to use video file instead of hardware

# Hardware display mode (when SIMULATE_HARDWARE=False)
# Common modes: 0x48693530 = HD1080i50, 0x48703235 = HD1080p25, 0x68703630 = HD720p60
CAPTURE_DISPLAY_MODE = 0x48693530   # bmdModeHD1080i50 (auto-detects with EnableFormatDetection)
PLAYOUT_DISPLAY_MODE = 0x48693530   # bmdModeHD1080i50 (must match input or desired output)

# Hardware simulation (when SIMULATE_HARDWARE=True)
SIMULATION_INPUT_FILE = r"C:\temp\test_video.mp4"   # Path to video file for testing

# Performance configuration
CAPTURE_QUEUE_SIZE = 16            # Frames to buffer from capture
AUDIO_QUEUE_SIZE = 32              # Audio packets to buffer (audio arrives more frequently than video)
PLAYOUT_QUEUE_SIZE = 4             # Frames to buffer for playout
GUI_UPDATE_RATE = 25               # Hz - max GUI refresh rate (match 25i signal)
ENABLE_FRAME_DROPPING = True       # Drop frames if pipeline falls behind

# Audio configuration
AUDIO_ENABLED = True
AUDIO_SAMPLE_RATE = 48000          # Hz
AUDIO_CHANNELS = 8                 # Number of channels to capture from DeckLink

# Encoding outputs (optional)
# Each output runs FFmpeg to encode/stream video and/or audio
OUTPUTS = [
    {
        "type": "udp_mono",
        "channels": [1, 2],              # 1-based channel indices to mix to mono
        "url": "udp://127.0.0.1:12345",  # UDP destination
    }
]

# Logging
LOG_LEVEL = "INFO"                 # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE = LOGS_DIR / "ffcapture.log"
LOG_TO_CONSOLE = True
LOG_TO_FILE = True

# A/V sync configuration
GAP_FILL_MODE = "passthrough"      # "passthrough": forward samples as-is (default, STT use case)
                                   # "fill": repeat last frame + silence on gaps (24/7 recording; not yet implemented)
AV_SYNC_DEBUG = False              # Log (hw_pts, derived_pts) for first 100 frames/packets when True
AV_SYNC_DRIFT_WARN_MS = 100        # Emit [av_sync_drift] at WARNING level when |drift| exceeds this
AV_SYNC_ANCHOR_TIMEOUT_SAMPLES = 1000  # Drop count before falling back to wall-clock anchor
AV_SYNC_DRIFT_CHECK_INTERVAL_S = 5.0   # Seconds between [av_sync_drift] log lines
AV_SYNC_HEALTH_INTERVAL_S = 60.0       # Seconds between [av_sync_health] log lines
AV_SYNC_GAP_THRESHOLD = 1.5            # delta/expected ratio that triggers [av_sync_warn] gap

# Debugging/Development
VERBOSE = False                    # Extra logging output
SKIP_PLAYOUT = True               # Skip sending frames to playout device (False = enable)
SKIP_GUI = False                    # Skip GUI window (headless mode) - set True for hardware test
SKIP_OVERLAY = False               # Skip text overlay - set False to enable
BENCHMARK_FRAME_PROCESSING = False # Print frame processing times
