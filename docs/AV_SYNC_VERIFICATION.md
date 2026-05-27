# A/V Sync Verification Checklist

## Background

All output PTS values are now derived from the DeckLink hardware reference clock
(`IDeckLinkVideoInputFrame::GetHardwareReferenceTimestamp` and
`IDeckLinkAudioInputPacket::GetPacketTime`).  A shared `t0` anchor is captured
from the first arriving sample of either kind, and every subsequent PTS is a
pure function of `(hw_pts - t0)`.  There are no independent frame or sample
counters anywhere in the pipeline.

## Quick Sanity Tests (unit / seconds)

```
pytest tests/test_av_sync.py -v
```

All tests must pass before running hardware verification.

## Hardware-in-the-Loop Checklist

### Setup

1. Connect a stable video/audio signal to the DeckLink SDI input.
   - A signal generator outputting 1080i25 or 720p50 with continuous audio tone is ideal.
   - A PC HDMI feeding a looping video (with tone on channels 1–2) works too.
2. Enable debug logging: set `AV_SYNC_DEBUG = True` in `src/config.py`.
3. Increase `LOG_LEVEL = "DEBUG"` temporarily.
4. Configure a `ts_file` output pointing to a path with sufficient free space.

---

### Test 1 — First-frame debug log inspection (< 5 min)

Start the pipeline and let it run for ~30 seconds, then stop.

Check the log for:
```
[hw_pts debug] video hw_pts=<X>  derived_pts=<Y>  delta=<Z>
[hw_pts debug] audio hw_pts=<X>  derived_pts=<Y>  delta=<Z>
```

Verify:
- [ ] `hw_pts` values are large integers (~10^10 or more, reflecting DeckLink uptime).
- [ ] `derived_pts` of the **first** frame/packet is 0 (or very close to 0).
- [ ] Consecutive video `hw_pts` deltas ≈ `10_000_000 / fps` (400 000 ticks at 25 fps).
- [ ] Consecutive audio `hw_pts` deltas ≈ `samples_per_packet * 10_000_000 / 48000`.
- [ ] No `dropped audio` or `Audio queue full` warnings in the log.

---

### Test 2 — Short capture A/V alignment (5–10 min)

Capture 5 minutes to `test_short.ts`.

```
ffprobe -show_packets -select_streams v:0 test_short.ts 2>/dev/null | grep pts_time | head -20
ffprobe -show_packets -select_streams a:0 test_short.ts 2>/dev/null | grep pts_time | head -20
```

Verify:
- [ ] Video `pts_time` values are monotonically increasing with uniform spacing (~0.04 s at 25 fps).
- [ ] Audio `pts_time` values are monotonically increasing.
- [ ] First video `pts_time` ≈ first audio `pts_time` (both anchored to the same t0; difference < 1 frame).
- [ ] No PTS jumps > 2× the expected frame/packet interval (would indicate a dropped packet being counted).

Play back in VLC and ffplay:
- [ ] No audible A/V drift throughout the 5-minute clip.
- [ ] Clap test: clap hands in front of the camera; in the recording the clap sound and the hand motion must be synchronised (< 1 frame offset, i.e. < 40 ms at 25 fps).

---

### Test 3 — 24-hour burn-in

Capture continuously for 24 hours to `test_24h.ts` (needs ~150 GB for 1080i25 with h264).

After capture:
```
ffprobe -show_packets -select_streams v:0 test_24h.ts 2>/dev/null | grep pts_time | tail -5
ffprobe -show_packets -select_streams a:0 test_24h.ts 2>/dev/null | grep pts_time | tail -5
```

Verify:
- [ ] Last video `pts_time` ≈ 86 400 s (24 hours).
- [ ] Last audio `pts_time` ≈ 86 400 s.
- [ ] `abs(last_video_pts_time - last_audio_pts_time) < 0.1 s` (< 3 frames at 25 fps).
- [ ] No `dropped audio` or `audio queue full` warnings in the 24-hour log.

```
grep -c "dropped audio" ffcapture.log  # should be 0
grep -c "Audio queue full" ffcapture.log  # should be 0
```

---

### Test 4 — Signal interruption recovery

1. Disconnect the SDI input cable for ~2 seconds mid-capture, then reconnect.
2. Let the pipeline continue for another 5 minutes.

Verify (passthrough mode):
- [ ] After reconnect, video and audio PTS resume with a gap (not from zero).
- [ ] The resumed audio PTS is consistent with the video PTS — both reflect the same gap.
- [ ] Playback in VLC shows a brief stall then resumes in sync.

---

## MPEG-TS PTS Wraparound Note

MPEG-TS PTS is a 33-bit value at 90 kHz, wrapping every ≈ 26.5 hours.
For captures longer than 26 hours, check that PyAV's muxer increments the
programme clock reference correctly (most players handle this transparently).
This is a known limitation and does not indicate an A/V sync bug.

## Known Limitations

- **Format change mid-session** (`VideoInputFormatChanged`): the DeckLink hardware
  reference clock survives format changes, so `t0` and all subsequent PTS remain
  valid.  However, `TSFileOutput` streams are initialized at fixed resolution/fps
  from the first frame; a resolution change in the signal will corrupt the output.
  This is a separate issue tracked as a follow-up.

- **Capture path fallback** (PyAV → COM mid-session): both paths use different
  absolute `hw_pts` origins.  `TSFileOutput._t0_hw` will re-anchor automatically
  on the first sample after a path switch, keeping stream-time continuous.

- **`GAP_FILL_MODE = "fill"`**: designed but not yet implemented.  The pipeline
  hook exists; implementation is required before using this mode for 24/7
  recording with gap-free output.
