#!/usr/bin/env python3
"""Detect and list DeckLink hardware devices."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

try:
    from src.decklink_com import DeckLinkCOM

    print("\nDeckLink Hardware Detection")
    print("=" * 60)
    print("Attempting to detect DeckLink devices...\n")

    devices = []
    for i in range(4):
        try:
            decklink = DeckLinkCOM(i)
            name = decklink.get_device_name()
            modes = decklink.get_display_modes()
            devices.append({
                'index': i,
                'name': name,
                'modes': len(modes)
            })
            print(f"[Device {i}] {name}")
            print(f"  Display modes: {len(modes)}")
            if modes:
                for mode in modes[:3]:
                    print(f"    - {mode['name']}: {mode['width']}x{mode['height']}")
                if len(modes) > 3:
                    print(f"    ... and {len(modes)-3} more modes")
            print()
        except Exception as e:
            if "not found" in str(e).lower():
                break

    if devices:
        print("=" * 60)
        print(f"SUCCESS: Detected {len(devices)} DeckLink device(s)\n")
        for dev in devices:
            print(f"  Device {dev['index']}: {dev['name']}")
        sys.exit(0)
    else:
        print("ERROR: No DeckLink devices found")
        print("\nPossible issues:")
        print("  1. Drivers not installed")
        print("  2. Hardware not properly seated")
        print("  3. System needs restart after driver install")
        print("  4. Device is in use by another application")
        sys.exit(1)

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
