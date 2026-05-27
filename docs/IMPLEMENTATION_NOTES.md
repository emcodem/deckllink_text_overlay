# FFCapture Implementation Notes

## Current Implementation Status

### Completed (Foundation & DeckLink)
- ✅ Project structure and organization
- ✅ Virtual environment setup script
- ✅ Configuration system (hardware in `config.py`, overlay in `config_subtitles.py`)
- ✅ Logging infrastructure
- ✅ Pipeline orchestration framework
- ✅ Text overlay engine with file monitoring
- ✅ GUI framework with PyQt6
- ✅ DeckLink COM wrapper (`decklink_com.py`) + comtypes and native variants
- ✅ DeckLink capture implementation with callbacks
- ✅ DeckLink playout implementation (frame scheduling, audio output)
- ✅ Frame format conversion (UYVY/BGRA/ARGB ↔ RGB)
- ✅ pywin32 COM interface integration
- ✅ Audio capture and queue handling (8-channel, 48 kHz PCM)
- ✅ Hardware-clock-derived A/V sync with wall-clock fallback
- ✅ Encoding outputs: UDP mono mix, MPEG-TS file (`outputs.py`)
- ✅ A/V sync health logging (`[av_sync_*]` tags, drift monitoring)

### TODO (Refinement)
- ⏳ Real hardware testing and validation
- ⏳ Frame buffer pool optimization (reduce allocations)
- ⏳ Audio/video synchronization tuning
- ⏳ Performance optimization and profiling
- ⏳ Error recovery and robustness
- ⏳ Timecode support
- ⏳ Multi-card setup (more than 2 devices)

## Key Design Decisions

### 1. Modular Architecture
Each component (capture, overlay, playout, GUI) is independent and can be:
- Replaced with alternative implementations
- Tested in isolation
- Configured independently

### 2. Queue-Based Pipeline
Uses Python queues for thread-safe communication:
- Non-blocking (frame dropping if queues full)
- Allows components to run at different rates
- GUI can skip frames if behind

### 3. Polling for Text File Updates
Rather than file watches (which are platform-specific), we:
- Check file modification time every N frames
- Cache the last read line
- Never block on file I/O

### 4. NumPy for Frame Data
- Fast array operations
- Direct memory access
- Compatible with OpenCV and PyAV

## DeckLink Implementation Notes

### Architecture

The DeckLink integration uses three layers:

1. **COM Wrapper** (`src/decklink_com.py`):
   - Abstracts Windows COM interfaces via pywin32
   - Manages device iteration and interface access
   - Provides Python-friendly API to DeckLink hardware
   - Handles pixel format constants and configurations

2. **Callback Handler** (`DeckLinkInputCallback`):
   - Implements IDeckLinkInputCallback interface
   - Called by DeckLink driver when frames arrive
   - Converts raw DeckLink frames to Python format
   - Thread-safe queue delivery to capture module

3. **Capture/Playout Modules**:
   - `src/capture.py`: Starts streams and receives callbacks
   - `src/playout.py`: Queues and outputs frames to hardware
   - Both manage threading and error handling

### Frame Format Handling

DeckLink provides frames in various formats:

| Format | Description | Conversion |
|--------|-------------|-----------|
| `bmdFormat8BitYUV` | UYVY 4:2:2 (most common) | → RGB via OpenCV |
| `bmdFormat8BitARGB` | 8-bit per channel + alpha | Drop alpha, BGR→RGB |
| `bmdFormat8BitBGRA` | BGRA with alpha | BGRA→RGB |
| `bmdFormat10BitYUV` | 10-bit YUV (v210) | Requires special handling |

Current implementation supports the 8-bit formats. 10-bit formats can be added by:
1. Converting to 16-bit NumPy array
2. Unpacking v210 layout (specialized code)

### Audio Handling

Audio arrives as raw PCM bytes in callback:
- **Format**: 16-bit signed PCM, stereo
- **Sample Rate**: 48 kHz (DeckLink standard)
- **Channels**: 2 (stereo)

Conversion:
```python
audio_array = np.frombuffer(audio_data, dtype=np.int16)
audio_array = audio_array.reshape(-1, 2)  # (samples, 2)
```

### Playout Implementation

Playout requires:
1. Creating an `IDeckLinkMutableVideoFrame`
2. Filling with pixel data in DeckLink format (UYVY)
3. Calling `ScheduleVideoFrame()` with timing info
4. Audio output via `ScheduleAudioSamples()`

Current stub (`put_video_frame`) needs completion with:
```python
# Create mutable video frame
video_frame = decklink_output.CreateVideoFrame(
    width, height,
    width * 2,  # rowBytes for UYVY
    bmdFormat8BitYUV,
    bmdFrameFlagDefault
)
# Fill frame data
video_frame.GetBytes()[:] = uyvy_data
# Schedule for output
decklink_output.ScheduleVideoFrame(video_frame, timestamp, duration, scale)
```

## Performance Considerations

### Bottlenecks to Monitor
1. **Text rendering** - OpenCV putText is relatively slow
   - Consider using pre-rendered text or cached surfaces
   - Can parallelize with capture using separate threads (already done)

2. **Frame format conversion** - YUV ↔ RGB conversion is CPU-intensive
   - Consider YUV-native text rendering if possible
   - Or use GPU acceleration (CUDA/OpenGL)

3. **GUI updates** - Slow on some systems
   - Frame skipping is automatic
   - Consider separating capture and display threads further

### Optimization Strategies
- Profile with `cProfile` before optimizing
- Consider CUDA for format conversion (if GPU available)
- Cache font metrics for text overlay
- Use frame-dropping strategy when CPU-bound

## Testing Strategy

### Hardware-Free Testing
1. Use simulated capture with test video files
2. Can test overlay, GUI, and pipeline logic
3. Unit tests for individual components

### With Hardware
1. Start with single DeckLink card
2. Verify input capture works
3. Add playout to second card
4. Measure latency and frame drops

### Test Cases
- [ ] Text overlay with various fonts/sizes
- [ ] Text file updates while running
- [ ] GUI responsiveness at high framerates
- [ ] Frame drop behavior under load
- [ ] Audio/video sync (once audio implemented)
- [ ] Graceful shutdown
- [ ] Error recovery

## Known Limitations

1. **Audio/video sync is hardware-clock-derived** — `GAP_FILL_MODE = "fill"` hook exists but is not yet implemented; use `"passthrough"` for 24/7 recording
2. **Playout not fully exercised with hardware** — frame scheduling (`ScheduleVideoFrame`) and audio output (`ScheduleAudioSamples`) are implemented but require real-hardware validation
3. **No persistent configuration** — Requires code editing (`src/config.py` / `src/config_subtitles.py`)
4. **No UI for runtime config** — Would be a future enhancement
5. **Windows only for COM/native backends** — PyAV backend works cross-platform; COM and native backends are Windows-specific

## Future Enhancements

### Short-term
- [ ] Real-hardware validation of playout path
- [ ] Implement `GAP_FILL_MODE = "fill"` (repeat last frame + silence)
- [ ] Configuration file (JSON/YAML) to avoid code edits
- [ ] Performance profiling tools

### Long-term
- [ ] Recording to disk
- [ ] Multiple text fields
- [ ] Real-time configuration UI
- [ ] Timecode synchronization
- [ ] Broadcast-grade reliability features
- [ ] Cross-platform support (Linux)

## Debugging Tips

### Enable Verbose Logging
```python
# In config.py
LOG_LEVEL = "DEBUG"
VERBOSE = True
BENCHMARK_FRAME_PROCESSING = True
```

### Monitor Performance
```python
# In pipeline.py logs, you'll see:
# Frame {N} - capture: 5.2ms, overlay: 3.8ms, total: 9.0ms
```

### Test Text Overlay
```powershell
# Append new text and watch it appear
"Test: $(Get-Date)" | Out-File -Append C:\temp\lines.txt
```

## References

- [Blackmagic DeckLink SDK](https://www.blackmagicdesign.com/support/)
- [PyAV Documentation](https://pyav.org/)
- [OpenCV Text Rendering](https://docs.opencv.org/4.5.0/d6/d6e/group__imgproc__draw.html)
- [PyQt6 Documentation](https://www.riverbankcomputing.com/static/Docs/PyQt6/)
