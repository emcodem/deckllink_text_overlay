# ffcapture A/V Sync Log Reference

All A/V-sync-related log lines carry a greppable `[av_sync_*]` prefix so a single
`grep` over the log file surfaces any drift, gap, or hw_pts failure.

---

## 1. Tag Reference

| Tag | Level | Frequency | Content |
|-----|-------|-----------|---------|
| `[av_sync_init]` | INFO | While waiting for first valid hw_pts | "still waiting for valid hw_pts (N dropped)" |
| `[av_sync_anchor]` | INFO | Once, at anchor instant | mode, hw_pts, hw_pts_rate, t0_wall, abs_wall, pre_anchor_drops, samples seen |
| `[av_sync_debug]` | INFO | Per-frame (only when `AV_SYNC_DEBUG=True`, first 100) | hw_pts, derived_pts, delta, hw_pts_valid |
| `[av_sync_health]` | INFO | Every 60 s | encoded counts, hwpts_failures, gaps, max_drift_ms, queue high-water |
| `[av_sync_drift]` | INFO / WARNING | Every 5 s | wall_elapsed, hw_elapsed, drift_ms (WARNING if \|drift\| > `AV_SYNC_DRIFT_WARN_MS`) |
| `[av_sync_warn]` | WARNING / ERROR | Per-event | hw_pts failure, gap detected, queue full, late anchor |
| `[av_sync_stop]` | INFO | Once, at shutdown | totals: encoded, failures, max drift, anchor mode |

### Example lines

```
[av_sync_init]   TSFile(...): still waiting for valid hw_pts (50 dropped)
[av_sync_anchor] TSFile(...): mode=hw_clock anchor_hw_pts=12345678 anchor_hw_pts_rate=10000000 anchor_wall_seconds=0.040 anchor_abs_wall=1731234567.890 pre_anchor_drops=3 video_samples_seen=2 audio_samples_seen=1
[av_sync_debug]  video hw_pts=12345678 derived_pts=0 delta=0 hw_pts_valid=True
[av_sync_health] TSFile(...): video_encoded=1500 audio_encoded=1480 hwpts_failures=0 gaps=0 max_drift_ms=3.2 frame_queue_hi=4 audio_queue_hi=2
[av_sync_drift]  TSFile(...): wall=300.001s hw=299.998s drift_ms=+3.0
[av_sync_warn]   TSFile(...): video gap detected delta=800000 expected=400000 missing≈1.0 frames
[av_sync_stop]   TSFile(...): anchor_mode=hw_clock pre_anchor_drops=3 video_encoded=90000 audio_encoded=88200 hwpts_failures=0 gaps=0 max_drift_ms=8.1
```

---

## 2. Healthy 24-Hour Profile

A successful 24 h session produces a log that looks like this:

- **Exactly 1** `[av_sync_init]` line (first valid hw_pts arrived within ~2 s of capture start).
- **Exactly 1** `[av_sync_anchor]` line with `mode=hw_clock` and `pre_anchor_drops` typically < 5.
- `[av_sync_drift]` every 5 s; all `|drift_ms|` below 100 (default threshold).
- `[av_sync_health]` every 60 s; `hwpts_failures=0 gaps=0`.
- **Zero** `[av_sync_warn]` lines (`grep -c` returns 0).
- **Exactly 1** `[av_sync_stop]` at shutdown with sane totals.

---

## 3. Post-Capture Grep Cookbook

Run these after a recording session (`ffcapture.log` = your log file):

```bash
# Should return 0 — any non-zero means a problem occurred
grep -c '\[av_sync_warn\]' ffcapture.log

# Should return exactly 1 — the anchor event
grep -c '\[av_sync_anchor\]' ffcapture.log

# Check anchor mode — should be "hw_clock" on healthy hardware
grep '\[av_sync_anchor\]' ffcapture.log | grep -o 'mode=[^ ]*'

# Max drift over the session (highest absolute value wins)
grep '\[av_sync_drift\]' ffcapture.log | grep -o 'drift_ms=[^ ]*' | sed 's/drift_ms=//' | sort -g | tail -1

# Final health snapshot (shows totals just before 24 h mark)
grep '\[av_sync_health\]' ffcapture.log | tail -1

# Session summary
grep '\[av_sync_stop\]' ffcapture.log

# Count gap events
grep -c 'gap detected' ffcapture.log

# Count hw_pts mid-stream failures
grep -c 'hw_pts_valid=False mid-stream' ffcapture.log
```

---

## 4. Failure Pattern Catalogue

### `mode=wall_clock_fallback` at anchor

```
[av_sync_anchor] ... mode=wall_clock_fallback pre_anchor_drops=1000
```

**Cause:** `GetHardwareReferenceTimestamp` / `GetPacketTime` never returned a non-zero
value for the first 1000 samples.

**Diagnosis:**
- Check `[av_sync_warn] GetHardwareReferenceTimestamp failed` lines for the SDK error.
- Verify DeckLink driver version supports the hardware reference clock API.
- In wall-clock fallback mode, sync quality degrades to wall-clock precision (~ms jitter).

---

### "gap detected"

```
[av_sync_warn] TSFile(...): video gap detected delta=800000 expected=400000 missing≈1.0 frames
```

**Cause:** A video or audio packet arrived with a hw_pts more than 1.5× the expected
frame/packet interval after the previous one.

**Likely sources:** DeckLink driver buffer underrun, bad SDI cable, input signal dropout,
CPU stall causing the capture queue to back up.

**Severity:** A single isolated gap is usually recoverable. Many gaps in quick succession
indicate a persistent problem.

---

### "hw_pts_valid=False mid-stream"

```
[av_sync_warn] TSFile(...): hw_pts_valid=False mid-stream (video #1)
```

**Cause:** `GetHardwareReferenceTimestamp` failed on a frame after anchor was already set.
A single occurrence (count=1) is acceptable — PTS falls back to wall-clock for that frame
with ms-level jitter. A rapidly accumulating count indicates a hardware or driver issue.

**Threshold:** WARNING until count reaches 1000, then ERROR for every 1000th thereafter.

---

### `drift_ms` growing steadily over time

```
[av_sync_drift] TSFile(...): wall=3600.001s hw=3599.992s drift_ms=+9.0
```

**Cause:** Normal physics — crystal oscillators in the Windows system clock and the
DeckLink card drift relative to each other. At ±20 ppm (Windows) and ±5 ppm (DeckLink),
worst-case crystal divergence over 24 h is ~2.2 seconds.

**Acceptable range:** A steady increase of a few ms per hour is expected. The output
`.ts` file has correct hw-derived PTS throughout — this metric only measures how
the two clocks diverge relative to each other, not A/V sync in the output.

**Action needed:** Only if drift exceeds 1 frame duration (40 ms at 25 fps) per hour,
or if drift resets or jumps abruptly (indicates a software bug or NTP step adjustment).

---

## 5. Drift Analysis Worked Example

Over 24 h at 25 fps, the expected drift from crystal tolerances alone is:

```
wall clock accuracy:   ±20 ppm (typical Windows PC with NTP)
DeckLink reference:    ±5 ppm (typical Blackmagic hardware)
worst-case divergence: 25 ppm × 86400 s = 2.16 s over 24 h
```

So `drift_ms` values growing from ~0 to ~2000 over 24 h are **normal physics**.

A drift that:
- **jumps abruptly** by hundreds of ms → likely an NTP step or software bug
- **stays flat at a large value** → anchor was mis-set (investigate `[av_sync_anchor]`)
- **grows > 1 frame/hour** (> 40 ms/hr at 25 fps) → investigate the clock source

Note: `time.time()` on Windows has ~1 ms typical precision, ~15 ms worst case. Drift
readings below 20 ms should be treated as noise.

---

## 6. ffprobe Output Verification

After capture, verify PTS integrity with:

```bash
# Video PTS — should be monotonically increasing, constant delta
ffprobe -v quiet -show_packets -select_streams v:0 out.ts \
  | grep pts_time | awk -F= '{print $2}' | sort -n | head -20

# Audio PTS — same check
ffprobe -v quiet -show_packets -select_streams a:0 out.ts \
  | grep pts_time | awk -F= '{print $2}' | sort -n | head -20

# Check for non-monotonic PTS (output should be empty)
ffprobe -v quiet -show_packets -select_streams v:0 out.ts \
  | grep pts_time | awk -F= 'NR>1 && $2 <= prev {print NR, prev, $2} {prev=$2}'
```

**Pass criteria:**
- PTS deltas for video: constant at `1/fps` seconds (e.g. 0.04 s at 25 fps).
- PTS deltas for audio: constant at `samples_per_packet / sample_rate` seconds.
- First video and audio PTS within 1 frame of each other.
- Zero non-monotonic PTS warnings.
