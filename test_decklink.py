#!/usr/bin/env python
"""Test DeckLink COM enumeration."""

from src.decklink_comtypes import create_decklink_iterator, IDeckLink
from ctypes import POINTER, byref
import ctypes

try:
    print("Creating iterator...")
    it = create_decklink_iterator()
    print(f"Iterator type: {type(it)}")
    print(f"Iterator _iid_: {it._iid_}")

    print("\nCalling Next()...")
    # Try calling with no arguments - comtypes might return the output value
    result = it.Next()
    print(f"Next() returned: {result}")
    print(f"Result type: {type(result)}")

    if device_ptr.value:
        device = ctypes.cast(device_ptr.value, POINTER(IDeckLink))
        print(f"Device (cast): {device}")

    if device:
        print("Getting device name...")
        name_ptr = byref(ctypes.c_wchar_p())
        device.GetDisplayName(name_ptr)
        print(f"Device name: {name_ptr}")

except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
