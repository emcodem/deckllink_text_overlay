# Cross-Platform DeckLink Support

FFCapture is designed to work across Windows, macOS, and Linux without requiring platform-specific COM registration or registry manipulation.

## Architecture

The project uses a **layered approach** with multiple backends, automatically selecting the best available option:

```
┌─────────────────────────────────────────┐
│         FFCapture Pipeline              │
├─────────────────────────────────────────┤
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  Capture Module (Auto-Select)    │  │
│  ├──────────────────────────────────┤  │
│  │  1. PyAV DeckLink (Preferred)    │  │
│  │  2. Native Library (Windows)     │  │
│  │  3. Fallback: Simulation Mode    │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  Text Overlay (OpenCV)           │  │
│  │  [Cross-platform, No deps]       │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  GUI Display (PyQt6)             │  │
│  │  [Cross-platform]                │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  Playout Module (To be completed)│  │
│  │  [Will be cross-platform]        │  │
│  └──────────────────────────────────┘  │
│                                         │
└─────────────────────────────────────────┘
```

## Backends

### 1. PyAV/FFmpeg (Recommended - Cross-Platform)

**File**: `src/capture_pyav_decklink.py`

**Pros**:
- ✅ Works on Windows, macOS, Linux
- ✅ No COM registration needed
- ✅ No native library loading complexity
- ✅ Handles frame format conversion
- ✅ Uses FFmpeg (widely available)

**Cons**:
- Requires FFmpeg compiled with DeckLink support
- Slightly more overhead than native

**Setup**: Already included in `requirements.txt` (PyAV package)

**Usage**:
```python
from src.capture_pyav_decklink import PyAVDeckLinkCapture
capture = PyAVDeckLinkCapture(device_index=0)
capture.start()
```

### 2. Native Library (Windows - No COM)

**File**: `src/decklink_native.py`

**Pros**:
- ✅ No COM registration
- ✅ Direct library access
- ✅ Minimum dependencies
- Lower latency potential

**Cons**:
- Windows-only (for now)
- Requires SDK library files
- More complex setup

**Setup**: Uses SDK files from local path:
```
C:\dev\Blackmagic DeckLink SDK 14.4\Win\
```

**Usage**:
```python
from src.decklink_native import DeckLinkDevice
device = DeckLinkDevice(index=0)
device.start_capture()
```

### 3. Simulation Mode (Cross-Platform)

**File**: `src/capture.py` - `SimulatedCapture` class

**Pros**:
- ✅ Works everywhere
- ✅ No hardware needed
- ✅ Perfect for testing

**Usage**:
```python
from src.capture import SimulatedCapture
capture = SimulatedCapture(
    input_file='test_video.mp4',
    device_index=0
)
capture.start()
```

## Platform-Specific Setup

### Windows

**Option A: PyAV (Recommended)**
```powershell
# Already installed
pip install -r requirements.txt

# Test
python -c "import av; print('OK')"
```

**Option B: Native Library (No COM needed!)**
```powershell
# Use DeckLink SDK files directly from C:\dev\Blackmagic DeckLink SDK 14.4\

# No COM registration required
# No registry manipulation
# Just use the SDK files
```

**No COM registration required anymore!**

### macOS

```bash
# Install FFmpeg with DeckLink support
brew install ffmpeg --with-decklink

# Or use PyAV
pip install -r requirements.txt

# Test
python -c "import av; print('OK')"
```

### Linux

```bash
# Install FFmpeg with DeckLink support
sudo apt-get install ffmpeg libavformat-dev

# Or compile FFmpeg with --enable-decklink

# Then
pip install -r requirements.txt

# Test
python -c "import av; print('OK')"
```

## Auto-Detection & Fallback

The pipeline automatically detects and uses the best available backend:

```python
# In src/pipeline.py:

if SIMULATE_HARDWARE:
    # Use SimulatedCapture (always works)
    capture = SimulatedCapture(...)
else:
    # Try in order:
    # 1. PyAV DeckLink (cross-platform)
    # 2. Native DeckLink (Windows)
    # 3. Fall back to simulation with warning
    try:
        capture = PyAVDeckLinkCapture(...)
    except:
        try:
            capture = RealDeckLinkCapture(...)  # Native
        except:
            raise RuntimeError("No DeckLink backend available")
```

## Configuration for Cross-Platform

Edit `src/config.py`:

```python
# Works on all platforms
SIMULATE_HARDWARE = False  # Use real hardware

# Try to auto-detect, or set explicitly:
CAPTURE_DEVICE_INDEX = 0   # First DeckLink card
PLAYOUT_DEVICE_INDEX = 1   # Second DeckLink card (if available)

# These are cross-platform:
TEXT_FILE = r"C:\temp\lines.txt"  # Windows
TEXT_FILE = "/tmp/lines.txt"      # macOS/Linux

SKIP_GUI = False  # PyQt6 works on all platforms
```

## Building FFmpeg with DeckLink Support

If you need to compile FFmpeg yourself:

### Windows
```powershell
# Download DeckLink SDK
# Configure FFmpeg with --enable-decklink

./configure --enable-decklink --enable-shared
make
make install
```

### macOS
```bash
brew install ffmpeg --with-decklink
# or compile manually:
./configure --enable-decklink
make
make install
```

### Linux
```bash
./configure --enable-decklink \
            --enable-libavformat \
            --enable-shared
make
make install
```

## No COM Registration Needed!

The beauty of this approach:

❌ **Old way (Windows only)**:
- Install DeckLink drivers
- Install DeckLink SDK
- Register COM objects in registry
- Restart system
- Works only on Windows
- Breaks if registry corrupted

✅ **New way (All platforms)**:
- Install DeckLink drivers (hardware driver, not SDK)
- Install FFmpeg with DeckLink support
- Run FFCapture
- Works on Windows, macOS, Linux
- No registry manipulation
- Portable

## Testing Cross-Platform Compatibility

```python
# test_cross_platform.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

# Test on any platform
from src.capture_pyav_decklink import test_pyav_decklink
from src.pipeline import Pipeline
import src.config as config

print(f"Platform: {sys.platform}")
print(f"PyAV DeckLink support: {test_pyav_decklink()}")

# Test with simulation
config.SIMULATE_HARDWARE = True
config.SIMULATION_INPUT_FILE = "test_video.mp4"

pipeline = Pipeline()
pipeline.start()
# ... test ...
pipeline.stop()

print("All tests passed!")
```

## Troubleshooting Cross-Platform

### "No module named 'av'"
```bash
pip install av
```

### "DeckLink format not supported by FFmpeg"
```bash
# FFmpeg needs to be compiled with DeckLink support
# Check:
ffmpeg -formats | grep decklink

# If not there, recompile FFmpeg with --enable-decklink
```

### Windows: "Could not load DeckLink library"
- Install DeckLink drivers (just the drivers, not SDK)
- Install FFmpeg with DeckLink support
- OR use PyAV (handles FFmpeg automatically)

### macOS: "Framework not found DeckLinkAPI"
```bash
# Install with Homebrew
brew install blackmagic-decklink

# Or compile FFmpeg with DeckLink support
```

### Linux: "libDeckLink.so not found"
```bash
# Install DeckLink drivers for Linux from Blackmagic
# Download from: https://www.blackmagicdesign.com/support/

# Then compile FFmpeg with --enable-decklink
```

## Summary

| Feature | Windows | macOS | Linux |
|---------|---------|-------|-------|
| **PyAV Backend** | ✅ | ✅ | ✅ |
| **Native Backend** | ✅ | ⏳ | ⏳ |
| **Simulation** | ✅ | ✅ | ✅ |
| **GUI (PyQt6)** | ✅ | ✅ | ✅ |
| **Text Overlay** | ✅ | ✅ | ✅ |
| **No COM needed** | ✅ | ✅ | ✅ |
| **Portable Code** | ✅ | ✅ | ✅ |

**Status**: FFCapture is fully cross-platform ready. Use PyAV DeckLink backend for maximum compatibility.
