# DeckLink Setup Guide

## Prerequisites

- Windows 10 or Windows 11
- Blackmagic DeckLink card installed in PCIe slot
- Administrator access for driver installation

## Step 1: Install DeckLink Drivers

### Option A: Official Blackmagic Installer (Recommended)

1. Visit https://www.blackmagicdesign.com/support/
2. Download "DeckLink Desktop Video" drivers
   - Choose the latest version for your OS (Windows 10/11)
   - Download the `.exe` installer
3. Run the installer with administrator privileges
4. Follow the installation wizard
5. Reboot your computer (usually required)
6. Verify installation in **Device Manager**:
   - Open Device Manager
   - Look for "Blackmagic" or "DeckLink" devices
   - Should show your card(s) without yellow warning icons

### Option B: Driver Update Only

If you already have drivers installed but want to update:

1. In Device Manager, right-click the DeckLink device
2. Select "Update driver"
3. Choose "Browse my computer for driver software"
4. Navigate to extracted driver folder
5. Follow prompts and reboot

## Step 2: Set Up Python Environment

```powershell
cd C:\dev\ffcapture

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Step 3: Register Windows COM Objects (One-Time)

After installing pywin32, you may need to register the COM interfaces:

```powershell
cd C:\dev\ffcapture
.\venv\Scripts\Activate.ps1

# Register COM objects
python -m pip install --upgrade pywin32
python -m pywin32_postinstall -install
```

If you get a "permission denied" error, run PowerShell as Administrator.

## Step 4: Verify Installation

Run the verification script:

```powershell
cd C:\dev\ffcapture
.\verify_decklink.ps1
```

This will check:
- ✓ DeckLink hardware detected
- ✓ DeckLink drivers installed
- ✓ Python environment set up
- ✓ pywin32 working correctly
- ✓ DeckLink COM objects registered
- ✓ All dependencies installed

Expected output for a successful setup:
```
[1/5] Checking DeckLink drivers in Device Manager...
  ✓ Found 2 DeckLink device(s)
    - Blackmagic DeckLink Quad 2

[2/5] Checking Python environment...
  ✓ Virtual environment found at C:\dev\ffcapture\venv

[3/5] Checking pywin32 installation...
  ✓ pywin32 is installed and working

[4/5] Checking DeckLink COM registration...
  ✓ DeckLink COM objects are registered

[5/5] Checking Python dependencies...
  ✓ numpy
  ✓ cv2
  ✓ PyQt6
  ✓ av
  ✓ win32com

✓ All checks passed! System is ready for DeckLink.
```

## Step 5: Configure Device Indices

Edit `src/config.py` and set the correct device indices:

```python
# If you only have one DeckLink card:
CAPTURE_DEVICE_INDEX = 0      # Use same card for both
PLAYOUT_DEVICE_INDEX = 0

# If you have two DeckLink cards:
CAPTURE_DEVICE_INDEX = 0      # First card (input)
PLAYOUT_DEVICE_INDEX = 1      # Second card (output)

# If you have three cards and want to use 2nd for input, 3rd for output:
CAPTURE_DEVICE_INDEX = 1
PLAYOUT_DEVICE_INDEX = 2
```

To find device indices:
```powershell
python -c "
from src.decklink_com import DeckLinkCOM

# List all available devices
for i in range(4):
    try:
        decklink = DeckLinkCOM(i)
        print(f'Device {i}: {decklink.get_device_name()}')
    except:
        print(f'Device {i}: Not available')
        break
"
```

## Step 6: Run the Application

```powershell
cd C:\dev\ffcapture
.\venv\Scripts\Activate.ps1
python src\main.py
```

You should see:
```
2025-01-15 10:30:45 - FFCapture - INFO - ============================
2025-01-15 10:30:45 - FFCapture - INFO - FFCapture Starting
2025-01-15 10:30:45 - FFCapture - INFO - ============================
2025-01-15 10:30:45 - FFCapture - INFO - Using Blackmagic DeckLink Quad 2 for capture
2025-01-15 10:30:45 - FFCapture - INFO - Using Blackmagic DeckLink Quad 2 for playout
2025-01-15 10:30:45 - FFCapture - INFO - Streams started
```

## Troubleshooting

### Problem: "DeckLink device 0 not found"

**Cause**: Device not detected by Python

**Solutions**:
1. Verify device is visible in Device Manager
2. Check device has no yellow warning icons
3. Try reinstalling drivers
4. Try a different PCIe slot
5. Try a different PCIe card

### Problem: "DeckLink COM objects are not registered"

**Cause**: Windows registry not updated with COM object locations

**Solutions**:
1. Re-run `python -m pywin32_postinstall -install` as Administrator
2. Uninstall and reinstall DeckLink drivers
3. Check `HKEY_CLASSES_ROOT\DeckLinkSDK.DeckLinkIterator_1` exists in Registry Editor

### Problem: "No display mode found" when querying modes

**Cause**: Device not configured or requires different input settings

**Solutions**:
1. Check physical SDI/HDMI connections
2. Use Blackmagic Media Express to test the card directly
3. Check input video signal is valid (correct resolution, framerate)
4. Try different display modes in config

### Problem: Frames are garbled or wrong colors

**Cause**: Pixel format conversion error

**Solutions**:
1. Check frame format reported in logs
2. Verify format is one of: UYVY, ARGB, BGRA
3. For 10-bit formats, full support not yet implemented
4. Check if overlay is corrupting data

### Problem: Audio sync is off or audio cuts out

**Cause**: Audio/video timing not synchronized

**Solutions**:
1. Enable verbose logging: set `LOG_LEVEL = "DEBUG"` in config.py
2. Check audio sample rate (should be 48000 Hz)
3. Monitor CPU usage - may be dropping frames
4. Reduce overlay complexity

## Performance Tips

1. **Reduce text rendering load**:
   - Use simpler fonts
   - Reduce font size
   - Minimize background rendering

2. **Monitor system load**:
   - Watch CPU usage during operation
   - If >70%, may need to optimize
   - Check GPU utilization if available

3. **Frame rate optimization**:
   - Set `GUI_UPDATE_RATE` to lower value if GUI is laggy
   - Enable `ENABLE_FRAME_DROPPING` for robust operation

4. **Buffer tuning**:
   - Increase `CAPTURE_QUEUE_SIZE` if frames drop
   - Increase `PLAYOUT_QUEUE_SIZE` if playout stutters

## Advanced: Using Multiple Devices

If you have more than 2 DeckLink cards:

```python
# config.py
CAPTURE_DEVICE_INDEX = 0  # Input from card 1
PLAYOUT_DEVICE_INDEX = 2  # Output to card 3 (skip card 2)
```

This is useful for:
- Multi-input scenarios (one input, two monitors output)
- Failover setups (monitor output while recording)
- Complex routing (different cards for different purposes)

## Logs and Debugging

FFCapture writes logs to `logs/ffcapture.log`. Check here for detailed error messages:

```powershell
# View latest logs
Get-Content -Path "logs/ffcapture.log" -Tail 50
```

For verbose debugging:
```python
# In config.py
LOG_LEVEL = "DEBUG"
VERBOSE = True
BENCHMARK_FRAME_PROCESSING = True
```

## Testing Without Live Signal

Use simulation mode to test without input signal:

```python
# In config.py
SIMULATE_HARDWARE = True
SIMULATION_INPUT_FILE = r"C:\path\to\test_video.mp4"
```

This lets you test the complete pipeline with a video file instead of hardware.

## Further Help

- [Blackmagic Support](https://www.blackmagicdesign.com/support/)
- [DeckLink SDK Documentation](https://www.blackmagicdesign.com/developer/)
- Check `docs/IMPLEMENTATION_NOTES.md` for technical details
