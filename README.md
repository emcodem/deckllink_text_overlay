# FFCapture - DeckLink Video Capture Reference Implementation

⚠️ **ARCHIVED — Active development moved to [ffrecord](https://github.com/emcodem/decklink_record_24).**

This repository contains **reference code** for DeckLink video capture with text overlay. It is maintained for architectural reference and pattern documentation but is no longer actively developed. For production 24/7 recording, use ffrecord.

## What This Project Demonstrates

Real-time video/audio capture from Blackmagic DeckLink cards with optional dynamic text overlay and dual output (GUI display and playout).

**Key Patterns:**
- DeckLink hardware integration (COM, comtypes, native bindings)
- A/V synchronization and frame alignment
- Real-time text overlay rendering
- PyQt6 GUI for live monitoring
- Cross-platform architecture (Windows/macOS/Linux)

## Features

- **DeckLink Input Capture**: Capture high-quality video/audio from one DeckLink card (when configured)
- **Dynamic Text Overlay**: Read and overlay text from a local file on each frame
- **Real-time Monitoring**: Live GUI display of the current frame
- **DeckLink Playout**: Output processed video/audio to a second DeckLink card (when configured)
- **Simulation Mode**: Test with video files instead of hardware (no dependencies)
- **Portable Setup**: Virtual environment ensures all dependencies are isolated
- **Cross-Platform**: Works on Windows, macOS, and Linux with the same code
- **Multiple Backends**: Auto-detects and uses the best available capture method

## Current Status

| Feature | Status | Notes |
|---------|--------|-------|
| Application Architecture | ✅ Complete | Fully working pipeline |
| Simulation Mode | ✅ Working | Test with video files - no hardware needed |
| Text Overlay Engine | ✅ Working | Real-time file monitoring implemented |
| GUI Display | ✅ Working | PyQt6 displays frames correctly |
| DeckLink Hardware | ⚠️  Requires Setup | See [Hardware Setup Guide](docs/DECKLINK_SETUP.md) |

**Recommendation:** Start with **Simulation Mode** to verify everything works. See [Simulation Mode Quick Start](SIMULATION_MODE_QUICKSTART.md). For hardware setup see [DeckLink Setup Guide](docs/DECKLINK_SETUP.md).

## Quick Start (Simulation Mode - No Hardware Needed)

### 1. Initial Setup

```powershell
cd C:\dev\ffcapture
.\setup.ps1
```

### 2. Create a Test Video

```powershell
# Create a simple 10-second test video
ffmpeg -f lavfi -i color=c=blue:s=1920x1080:d=10 `
       -f lavfi -i sine=f=1000:d=10 `
       C:\temp\test_video.mp4
```

### 3. Enable Simulation Mode

Edit `src/config.py`:
```python
SIMULATE_HARDWARE = True
SIMULATION_INPUT_FILE = r"C:\temp\test_video.mp4"  # already present below
```

### 4. Create Text File

```powershell
"Initial text line" | Out-File -Encoding UTF8 C:\temp\lines.txt
```

### 5. Run the Application

```powershell
python src\main.py
```

You should see the video with text overlay in the GUI window!

---

## For Hardware Access (DeckLink)

See **[DeckLink Setup Guide](docs/DECKLINK_SETUP.md)** for:
- Driver and COM object installation
- Device index configuration
- Step-by-step verification with `verify_decklink.ps1`

## Testing & Development

### Simulation Mode (Recommended)

**Perfect for development, testing, and hardware-independent verification.**

```python
# In src/config.py:
SIMULATE_HARDWARE = True
SIMULATION_INPUT_FILE = r'C:\temp\test_video.mp4'
```

Benefits:
- ✅ No hardware required
- ✅ Fully portable
- ✅ Instant feedback
- ✅ Perfect for testing overlays and GUI

See **[Simulation Mode Quick Start](SIMULATION_MODE_QUICKSTART.md)** for detailed guide.

### Hardware Mode

When you're ready to use real DeckLink hardware:

1. First read **[DeckLink Setup Guide](docs/DECKLINK_SETUP.md)**
2. Install drivers and register COM objects (requires admin privileges)
3. Set `SIMULATE_HARDWARE = False` in `src/config.py`
4. Application auto-detects and uses hardware

## Configuration

Hardware and pipeline settings are in `src/config.py`. Text overlay settings are in `src/config_subtitles.py`. Key settings:

**`src/config.py`**

| Setting | Purpose | Default |
|---------|---------|---------|
| `CAPTURE_DEVICE_INDEX` | Which DeckLink card to use for input | 0 |
| `PLAYOUT_DEVICE_INDEX` | Which DeckLink card to use for output | 1 |
| `SIMULATE_HARDWARE` | Use video file instead of hardware | False |
| `SIMULATION_INPUT_FILE` | Video file path for simulation mode | `C:\temp\test_video.mp4` |
| `CAPTURE_QUEUE_SIZE` | Frame buffer depth from capture | 16 |
| `GUI_UPDATE_RATE` | Max GUI refresh rate (Hz) | 25 |

**`src/config_subtitles.py`**

| Setting | Purpose | Default |
|---------|---------|---------|
| `TEXT_FILE` | Path to text file for overlay | `C:\temp\lines.txt` |
| `TEXT_FONT_SIZE` | Font size in pixels | 70 |
| `TEXT_OFFSET_BOTTOM` | Pixels from bottom edge | 98 |
| `TEXT_ALIGN` | Horizontal alignment (`left`/`center`/`right`) | `center` |
| `TEXT_COLOR` | Text colour (BGR tuple) | `(255, 255, 255)` |
| `TEXT_BG_COLOR` | Background colour (BGR tuple) | `(0, 0, 0)` |

## Updating Text Dynamically

While the application is running, you can update the text:

```powershell
# PowerShell: append new line to text file
"New overlay text" | Out-File -Append C:\temp\lines.txt

# The overlay updates within 1-2 frames
```

## Architecture

```
┌─────────────────┐
│  DeckLink Input │
└────────┬────────┘
         │
    ┌────▼─────────┐
    │ Frame Capture │
    └────┬─────────┘
         │
    ┌────▼────────────────────┐
    │ Text Overlay Engine      │
    │ (reads C:\temp\lines.txt)│
    └────┬───────────┬──────────┘
         │           │
    ┌────▼───┐  ┌────▼────────┐
    │ GUI    │  │ DeckLink     │
    │Display │  │ Playout Card │
    └────────┘  └─────────────┘
```

## Troubleshooting

### "DeckLink not found"
- Verify DeckLink drivers installed: Check Windows Device Manager
- Check card is properly seated in PCIe slot
- Try restarting the computer

### GUI doesn't appear
- Check if PyQt6 installed correctly: `pip list | grep PyQt6`
- Try running in headless mode: set `SKIP_GUI = True` in config.py

### Text not updating
- Verify text file exists at configured path
- Check file permissions (must be readable)
- Look at logs in `logs/ffcapture.log`

### Performance issues
- Check CPU usage (should be under 50%)
- Reduce overlay complexity (smaller font, simpler text)
- Increase `CAPTURE_QUEUE_SIZE` in config.py

## Logs

Application logs are saved to `logs/ffcapture.log`. Check here for detailed error messages.

## Project Structure

```
C:\dev\ffcapture/
├── src/
│   ├── __init__.py               # Package definition
│   ├── main.py                   # Entry point
│   ├── config.py                 # Hardware/pipeline configuration
│   ├── config_subtitles.py       # Text overlay configuration
│   ├── logger.py                 # Logging setup
│   ├── capture.py                # Capture backends (sim + DeckLink COM)
│   ├── capture_pyav_decklink.py  # PyAV/FFmpeg DeckLink backend
│   ├── decklink_com.py           # DeckLink COM wrapper (pywin32)
│   ├── decklink_comtypes.py      # DeckLink comtypes backend
│   ├── decklink_native.py        # DeckLink native library backend
│   ├── overlay.py                # Text overlay engine
│   ├── outputs.py                # Encoding outputs (UDP, TS file)
│   ├── playout.py                # DeckLink playout
│   ├── gui.py                    # GUI display
│   └── pipeline.py               # Main orchestration
├── tests/                        # Unit tests
├── docs/                         # Documentation
├── requirements.txt              # Python dependencies
├── setup.ps1                     # Setup script
├── verify_decklink.ps1           # Hardware verification script
└── README.md                     # This file
```

## Development

For architectural notes and implementation details, see `docs/IMPLEMENTATION_NOTES.md`.

## Dependencies

- **av** (PyAV): FFmpeg bindings
- **opencv-python**: Image processing
- **numpy**: Array operations
- **PyQt6**: GUI framework

All managed via virtual environment in `venv/`.

## License

[Your License Here]

## Support

For issues or questions, check the logs and AGENTS.md documentation.
