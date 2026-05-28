# Agent Notes

## DeckLink SDI Micro — Fix for Frame Buffer Access (2026-05-27)

### Problem
Video frames were arriving (signal detected, audio captured) but every frame failed
with:

```
ERROR src.decklink_com: Error processing video frame:
    module 'comtypes.gen.DeckLinkAPI' has no attribute 'IDeckLinkVideoBuffer'
```

The `IDeckLinkVideoBuffer` interface and the `bmdBufferAccessRead` /
`bmdBufferAccessWrite` constants **do not exist** in the DeckLink type library
(`DeckLinkAPI64.dll`). They are not part of the COM IDL — they belong to a
different (C++ SDK-only) access pattern.

### Root Cause
`src/decklink_com.py` — inside `VideoInputFrameArrived` — was trying to:

```python
video_buffer = videoFrame.QueryInterface(decklink_module.IDeckLinkVideoBuffer)
video_buffer.StartAccess(decklink_module.bmdBufferAccessRead)
buffer_ptr = video_buffer.GetBytes()
...
video_buffer.EndAccess(decklink_module.bmdBufferAccessRead)
```

### Fix
`IDeckLinkVideoInputFrame` (the type comtypes already passes to the callback)
exposes `GetBytes()` directly. No `QueryInterface` or `StartAccess`/`EndAccess`
is needed for input frames. Replace the block above with:

```python
buffer_ptr = videoFrame.GetBytes()
```

### Result after fix
- 1920×1080 UYVY frames captured at 25 fps (1080i50)
- HW timestamps valid (10 MHz clock, 400 000 ticks/frame)
- Audio: 8 channels @ 48 kHz

### Environment
- Python 3.11.9 portable at `C:\dev\python311_portable`
- `av==17.0.1` (vendor wheel: `vendor/av-17.0.1-cp311-abi3-win_amd64.whl`)
- `comtypes==1.4.16`, `numpy`, `opencv-python` — installed via pip
- DeckLink Desktop Video drivers installed at
  `C:\Program Files\Blackmagic Design\Blackmagic Desktop Video\DeckLinkAPI64.dll`
