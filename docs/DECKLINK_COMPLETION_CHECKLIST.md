# DeckLink Implementation Completion Checklist

This document tracks work needed to complete the DeckLink integration for full hardware support.

## Phase 1: Pre-Hardware (Can be done without real DeckLink cards)

- [x] Project structure and scaffolding
- [x] COM wrapper (`decklink_com.py`)
- [x] Capture callback infrastructure
- [x] Playout queue infrastructure
- [x] Frame format enums and constants
- [x] Audio/video sample dataclasses
- [x] Setup verification script
- [x] Documentation

## Phase 2: Capture Implementation (with real hardware)

### Frame Reception
- [ ] Test callback receives frames
  - Run with real SDI/HDMI input
  - Verify `VideoInputFrameArrived` is called
  - Check frame count increases
  
- [ ] Validate frame data
  - Verify frame width/height match input
  - Check pixel format is correct (UYVY, ARGB, etc.)
  - Inspect raw frame data for corruption

### Frame Format Conversion
- [ ] Test UYVY → RGB conversion
  - Capture UYVY frame
  - Convert to RGB
  - Save to file and inspect colors
  - Compare with reference image
  
- [ ] Test ARGB → RGB conversion
  - If supported by hardware
  - Verify alpha channel handling
  - Check color accuracy

- [ ] Test frame alignment
  - Check stride/rowBytes calculations
  - Verify no data corruption on row boundaries

### Audio Reception
- [ ] Test audio callback
  - Verify `VideoInputFrameArrived` with audio_frame
  - Check sample count and format
  - Validate audio timing aligns with video

### Integration
- [ ] Test full capture pipeline
  - Run application with real input
  - Verify frames arrive in GUI
  - Check no frame drops
  - Monitor queue depths
  
- [ ] Latency measurement
  - Time from capture to display
  - Target: < 100ms from hardware to screen

## Phase 3: Playout Implementation (with real hardware)

### Frame Output
- [ ] Implement `put_video_frame()` in `decklink_com.py`
  - Create IDeckLinkMutableVideoFrame
  - Fill with UYVY data
  - Calculate frame timing
  - Call ScheduleVideoFrame()
  
- [ ] Test frame output
  - Send test pattern to second card
  - Verify pattern appears on output
  - Check resolution and format on equipment
  
- [ ] Test frame rate
  - Output 30fps stream
  - Measure actual output framerate
  - Check for frame drops
  - Verify sync with capture input

### Audio Output
- [ ] Implement audio output in `decklink_com.py`
  - Format audio from queue to PCM
  - Call ScheduleAudioSamples()
  - Synchronize with video timing
  
- [ ] Test audio output
  - Output test tone
  - Verify audio appears on output
  - Check level and quality
  - Sync with video output

### Integration
- [ ] Full pipeline test
  - Capture on card 1 → overlay → playout on card 2
  - Monitor both input and output
  - Verify text appears correctly on output
  - Check audio/video sync

## Phase 4: Optimization & Refinement

### Performance
- [ ] Profile frame processing time
  - Identify bottlenecks (capture/overlay/playout)
  - Target: Process frame in < 16ms for 60fps
  
- [ ] Optimize memory
  - Implement frame buffer pool
  - Reduce allocations during streaming
  - Monitor memory usage over time
  
- [ ] Optimize frame conversion
  - Consider SIMD or GPU acceleration
  - Test OpenCV's CUDA modules if available
  - Profile color space conversion

### Reliability
- [ ] Implement error recovery
  - Handle dropped frames gracefully
  - Detect and recover from input loss
  - Implement reconnection logic
  
- [ ] Test edge cases
  - Format changes during streaming
  - Queue overflow conditions
  - Audio/video desynchronization
  - Thread safety under load
  
- [ ] Monitor and log
  - Frame drop statistics
  - Queue depths
  - Timing information
  - Error conditions

### Robustness
- [ ] Multi-hour stress test
  - Run for 4+ hours continuously
  - Monitor for memory leaks
  - Check for frame rate drift
  - Verify text file updates still work
  
- [ ] Handle mode changes
  - Switch input resolution
  - Switch framerate
  - Verify graceful handling
  
- [ ] Graceful shutdown
  - Verify cleanup on Ctrl+C
  - Check COM object release
  - Ensure no hanging threads

## Phase 5: Advanced Features (Optional)

### Display Mode Management
- [ ] Auto-detect input mode
  - Query actual resolution/framerate
  - Configure output to match
  - Handle mode change callbacks
  
- [ ] List and select modes
  - Display available modes
  - Allow user selection
  - Configure in real-time

### Timecode Support
- [ ] Capture timecode from frames
  - Extract from DeckLink frame
  - Store in metadata
  - Display in GUI
  
- [ ] Output timecode
  - Calculate timecode for output
  - Schedule with frames
  - Verify on external equipment

### 10-Bit Color
- [ ] Implement v210 format support
  - Unpack 10-bit samples
  - Convert to 16-bit arrays
  - Test with 10-bit hardware

### Recording
- [ ] Record to disk
  - Use PyAV to encode
  - ProRes or H.264
  - Synchronized with text overlay

## Testing Checklist

### Setup Verification
- [ ] DeckLink drivers installed
- [ ] Devices visible in Device Manager
- [ ] verify_decklink.ps1 passes all checks
- [ ] COM objects accessible from Python

### Basic Capture
- [ ] Can initialize DeckLink device
- [ ] Can query display modes
- [ ] Can set callback
- [ ] Can start/stop streams
- [ ] Frames arrive in queue

### Basic Playout
- [ ] Can initialize playout device
- [ ] Can queue frames
- [ ] Frames appear on output
- [ ] Timing is accurate

### Full Pipeline
- [ ] Capture → overlay → display works
- [ ] Capture → overlay → playout works
- [ ] Text file updates reflected in output
- [ ] Audio plays correctly
- [ ] No frames dropped

### Edge Cases
- [ ] Handle missing text file
- [ ] Handle text file read errors
- [ ] Handle format conversion errors
- [ ] Handle queue overflow
- [ ] Handle device disconnect

### Performance
- [ ] Maintains target framerate
- [ ] Text rendering not a bottleneck
- [ ] Memory stable over time
- [ ] CPU usage reasonable (< 50%)

## Known Limitations

### Current Implementation
1. Frame data must be copied (no zero-copy in callback)
2. No frame pool pre-allocation (allocations during streaming)
3. Audio/video independent (no strict sync)
4. No timecode support
5. No 10-bit color support
6. Playout not fully implemented

### Hardware Limitations
- Depends on DeckLink device capabilities
- May vary between Quad, Decklink, and other models
- Some formats may not be supported by all cards

## Success Criteria

A complete implementation should:

1. ✓ Capture video from DeckLink input
2. ✓ Overlay dynamic text on each frame
3. ✓ Display live preview in GUI
4. ✓ Output to DeckLink playout card
5. ✓ Maintain 1-2 frame latency
6. ✓ Support at least HD and 4K resolutions
7. ✓ Handle audio passthrough
8. ✓ Gracefully handle errors
9. ✓ Performance: < 50% CPU on modern processor
10. ✓ Stability: 8+ hour continuous operation

## Notes

- Always test with real hardware when possible
- Use simulation mode for development
- Monitor logs carefully for timing issues
- Frame synchronization is critical for A/V products
- Consider using external test equipment to verify output quality
