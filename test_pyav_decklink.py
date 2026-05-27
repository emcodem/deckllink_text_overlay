#!/usr/bin/env python3
"""Test PyAV and FFmpeg DeckLink support."""

import sys
import subprocess

sys.path.insert(0, '.')

print("Testing PyAV and FFmpeg DeckLink support...\n")

try:
    import av
    print(f"[OK] PyAV available (version {av.__version__})")
except ImportError as e:
    print(f"[FAIL] PyAV not available: {e}")
    sys.exit(1)

# Check if ffmpeg has DeckLink support
print("\nChecking FFmpeg for DeckLink support...")

try:
    result = subprocess.run(
        ["ffmpeg", "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
        capture_output=True,
        text=True,
        timeout=5
    )

    found_decklink = False
    decklink_devices = []

    for line in result.stderr.split('\n'):
        if 'DeckLink' in line:
            found_decklink = True
            decklink_devices.append(line.strip())

    if found_decklink:
        print("[OK] DeckLink devices found in FFmpeg!")
        print("\nAvailable DeckLink inputs:")
        for device in decklink_devices:
            if device:
                print(f"  - {device}")
    else:
        print("[NO] No DeckLink devices found")
        print("\nAvailable DirectShow devices:")
        for line in result.stderr.split('\n'):
            if 'DirectShow' in line or '@' in line:
                if 'video' in line.lower() or 'audio' in line.lower():
                    print(f"  {line.strip()}")

except FileNotFoundError:
    print("[FAIL] ffmpeg not found in PATH")
    print("   Make sure ffmpeg is installed and accessible")
except Exception as e:
    print(f"[FAIL] Error: {e}")

print("\nConclusion:")
if found_decklink:
    print("[OK] FFmpeg with DeckLink support is available")
    print("[OK] Ready to use PyAVDeckLinkCapture")
else:
    print("[FAIL] FFmpeg DeckLink support not detected")
    print("  Options:")
    print("  1. Install FFmpeg with DeckLink support")
    print("  2. Use simulation mode (SIMULATE_HARDWARE = True)")
