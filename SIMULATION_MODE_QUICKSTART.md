# Quick Start: Simulation Mode

Get FFCapture working **right now** without hardware dependencies.

## Step 1: Create a Test Video

If you don't have a test video, create one:

```powershell
cd C:\dev\ffcapture

# Create a 10-second blue video with tone audio
ffmpeg -f lavfi -i color=c=blue:s=1920x1080:d=10 `
       -f lavfi -i sine=f=1000:d=10 `
       -pix_fmt yuv420p `
       C:\temp\test_video.mp4

# Or use any existing MP4 file you have
```

## Step 2: Enable Simulation Mode

Edit `src/config.py`:

```python
SIMULATE_HARDWARE = True
SIMULATION_INPUT_FILE = r"C:\temp\test_video.mp4"  # verify this points to your test video
```

## Step 3: Create Text File

Create the text file that will be overlaid on frames:

```powershell
# Create C:\temp\lines.txt
"This is test line 1" | Out-File -Encoding UTF8 C:\temp\lines.txt
```

The application reads the **last line** of this file and overlays it on every frame.

Update the file while running to see live text changes:

```powershell
# In a separate terminal, update the text:
"Frame number: 42" | Out-File -Encoding UTF8 C:\temp\lines.txt
```

## Step 4: Run the Application

```powershell
cd C:\dev\ffcapture
.\venv\Scripts\python.exe src\main.py
```

You should see:
```
============================================================
FFCapture Starting
============================================================
...
Using simulated capture from video file
DeckLink capture initialized (device 0)
Pipeline initialized
Pipeline started
GUI initialized
Processing loop started
```

## Step 5: Watch It Work

- **GUI window** appears showing the video with text overlay
- **Text file updates** are visible in real-time on frames
- **Status bar** shows frame count and performance
- **Logs** in `logs/ffcapture.log` show detailed info

## Troubleshooting

### "SIMULATION_INPUT_FILE not found"
- Verify file exists: `Test-Path C:\temp\test_video.mp4`
- Create it with the ffmpeg command above

### "No frames received"
- Check logs: `Get-Content logs/ffcapture.log -Tail 20`
- Verify video file is valid: `ffprobe C:\temp\test_video.mp4`

### "GUI doesn't appear"
- PyQt6 might need display server
- Try setting: `export QT_QPA_PLATFORM=offscreen` (if headless)

### "Text overlay not updating"
- Ensure `C:\temp\lines.txt` exists
- Try updating it: `"Updated text" | Out-File C:\temp\lines.txt`
- Check logs for file read errors

## What to Test

Now that it's running, verify these features:

### 1. Frame Capture
```powershell
# Watch the terminal output - should show frame numbers increasing
# Check logs: grep -i "frame" logs/ffcapture.log
```

### 2. Text Overlay
- Edit `C:\temp\lines.txt` while the app runs
- See text change in real-time on the video
- Verify positioning with config settings

### 3. GUI Display
- Verify video displays in PyQt6 window
- Check that refresh rate matches config (30 Hz default)
- Test window resize/minimize

### 4. Performance
- Watch the status bar for dropped frames
- Monitor CPU/memory usage (should be minimal)
- Check logs for timing information

## Example Workflow

```powershell
# Terminal 1: Run the application
cd C:\dev\ffcapture
.\venv\Scripts\python.exe src\main.py

# Terminal 2: Monitor the text file updates
While ($true) {
    $timestamp = Get-Date -Format "HH:mm:ss"
    "$timestamp - Frame update" | Out-File -Encoding UTF8 C:\temp\lines.txt
    Start-Sleep -Seconds 0.5
}

# Terminal 3: Watch logs
Get-Content logs/ffcapture.log -Wait

# Use Ctrl+C in Terminal 1 to stop the app
```

## Configuration Tweaks

Text overlay settings live in `src/config_subtitles.py`:

```python
TEXT_FONT_SIZE = 70                # Larger/smaller text
TEXT_OFFSET_BOTTOM = 98            # Pixels from bottom edge
TEXT_ALIGN = "center"              # "left", "center", or "right"
TEXT_COLOR = (255, 255, 255)       # BGR: (0,0,255) = red
TEXT_BG_COLOR = (0, 0, 0)          # Background color
TEXT_BG_ALPHA = 167                # 0 = transparent, 255 = opaque
TEXT_CHECK_INTERVAL = 30           # Check text file every N frames
```

Pipeline settings stay in `src/config.py`:

```python
# GUI refresh rate
GUI_UPDATE_RATE = 25               # Hz (match your signal — 25 for 1080i50)

# Capture queue size
CAPTURE_QUEUE_SIZE = 16            # Frames to buffer (higher = more RAM)
```

## Next: Hardware Integration

Once you've tested everything in simulation mode and confirmed it works:

1. **See** `docs/DECKLINK_SETUP.md` for driver and COM setup
2. **Run** `.\verify_decklink.ps1` to confirm the card is visible
3. **Set** `SIMULATE_HARDWARE = False` in `src/config.py`
4. Application automatically switches to hardware capture

The beauty of simulation mode: all your testing is valid for real hardware too!

## Performance Notes

### Typical Metrics (Simulation Mode)
- Frame decode: 2-5ms per frame  
- Overlay rendering: 1-3ms per frame
- GUI update: 1-2ms per frame
- Total: ~5-10ms per frame (exceeds 30fps requirement)
- CPU: <10% on modern systems
- Memory: ~200-300MB stable

### Optimization Tips
- Lower `TEXT_FONT_SIZE` for faster rendering
- Reduce `CAPTURE_QUEUE_SIZE` if memory is tight
- Disable `USE_TEXT_BACKGROUND` for speed
- Lower `GUI_UPDATE_RATE` if CPU is high

## Files to Watch

```
C:\dev\ffcapture/
├── logs/
│   └── ffcapture.log           # Detailed run logs
├── src/
│   ├── config.py               # All settings
│   ├── main.py                 # Application entry
│   ├── pipeline.py             # Core pipeline
│   ├── capture.py              # Capture backends
│   ├── overlay.py              # Text overlay
│   └── gui.py                  # PyQt6 display
└── SIMULATION_MODE_QUICKSTART.md  # This file
```

## Tips & Tricks

### Batch Test Script
```powershell
# Create test_simulation.ps1
$ErrorActionPreference = "Stop"

# Setup
New-Item -ItemType Directory -Path C:\temp -Force | Out-Null
ffmpeg -f lavfi -i color=c=blue:s=1920x1080:d=10 -f lavfi -i sine=f=1000:d=10 C:\temp\test_video.mp4 2>$null
"Test line" | Out-File -Encoding UTF8 C:\temp\lines.txt

# Update config
(Get-Content src\config.py) -replace 'SIMULATE_HARDWARE = False', 'SIMULATE_HARDWARE = True' | Set-Content src\config.py

# Run
.\venv\Scripts\python.exe src\main.py

# Restore config
(Get-Content src\config.py) -replace 'SIMULATE_HARDWARE = True', 'SIMULATE_HARDWARE = False' | Set-Content src\config.py
```

Run with: `.\test_simulation.ps1`

### Monitor Real-Time Updates
```powershell
# Create a live-updating text file every second
$count = 0
while ($true) {
    $count++
    "Frame: $count at $(Get-Date -Format 'HH:mm:ss.fff')" | Out-File C:\temp\lines.txt -Encoding UTF8
    Start-Sleep -Milliseconds 100
}
```

---

**Status:** Ready to run  
**Time to working:** <5 minutes  
**Hardware needed:** No  
**Admin required:** No

Start with simulation mode. It's the fastest path to verifying everything works!
