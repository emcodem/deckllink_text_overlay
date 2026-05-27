#!/usr/bin/env python3
"""Test native DeckLink SDK integration."""

import sys
import logging

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, 'src')

from decklink_sdk_ctypes import DeckLinkSDK, DeckLinkDeviceIterator, test_decklink_sdk

print("=" * 60)
print("Testing Native DeckLink SDK Integration")
print("=" * 60)

# Test 1: Load SDK
print("\n[TEST 1] Loading DeckLink SDK...")
sdk = DeckLinkSDK()
if sdk.is_available():
    print(f"[OK] SDK loaded from: {sdk.lib_path}")
else:
    print("[FAIL] Failed to load SDK")
    print("[DEBUG] Checking DLL candidates...")
    candidates = sdk._get_library_candidates()
    for cand in candidates:
        print(f"  Checked: {cand}")
    sys.exit(1)

# Test 2: Enumerate devices
print("\n[TEST 2] Enumerating devices...")
iterator = DeckLinkDeviceIterator(sdk)
count = iterator.get_device_count()
print(f"Found {count} device(s)")

if count > 0:
    for i in range(count):
        device = iterator.get_device(i)
        if device:
            print(f"  - Device {i}: {device.name}")

# Test 3: Run full test
print("\n[TEST 3] Running full SDK test...")
result = test_decklink_sdk()
if result:
    print("[OK] Native DeckLink SDK is functional")
else:
    print("[FAIL] Native DeckLink SDK test failed")

print("\n" + "=" * 60)
