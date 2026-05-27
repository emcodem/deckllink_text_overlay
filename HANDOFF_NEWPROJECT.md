# ffrecord — Handoff / Scoping Document

New sibling project to be created at `C:\dev\ffrecord`, derived from ffcapture's DeckLink capture core. 24/7 SDI recording service with multiple parallel outputs.

---

## 1. Project at a glance

- **Location:** `C:\dev\ffrecord` (sibling of `C:\dev\ffcapture`)
- **Relationship to ffcapture:** new sibling project, copy what's needed. No shared library. Independent codebases.
- **Purpose:** headless 24/7 recording service. One DeckLink SDI input → N user-configurable parallel outputs, each with its own codec/container/segment policy.
- **Process model:** one binary instance per SDI channel. Multiple instances run side by side. Crash isolation by process boundary; a watchdog (NSSM / Windows Service / systemd-equivalent) restarts whole processes.

---

## 2. Architecture

### 2.1 Capture

- One DeckLink SDI input per service instance.
- Deinterlace **once at capture**: 25i → 50p. All outputs receive deinterlaced 50p frames (and the audio stream).
- No overlay, no subtitles, no GUI.

### 2.2 Outputs

User-configurable list of N outputs per service instance. Each output config specifies:

- Codec + container (typically NVENC h264/hevc into MOV / MP4 / MXF, plus HLS for live preview).
- All encoder settings come from user config (YAML).
- Newest NVENC support required: 4:2:2 and interlaced — but since we deinterlace at capture, encoders see 50p.
- Audio: per-output channel selection from embedded SDI audio + optional downmix.
- Segment policy: per-output (duration, etc.).
- Path template: per-output, default `{output}/{CH}/{YYYYMMDDHH}/{starttime_unix_ms}.mov`.
- HLS outputs: rolling window of last 2 chunks only (live web preview, not DVR).

### 2.3 Concurrency

- **Capture thread → fan-out queue → thread per output.**
- One thread reads from DeckLink, pushes frames into per-output bounded queues.
- Each output has its own encoder thread.
- Slow outputs drop frames from their *own* queue — never block the capture thread or other outputs.

### 2.4 Configuration

- **YAML**, one file per service instance.
- Sections: capture (device, format, deinterlace), outputs (list), logging, http, etc.

### 2.5 Control plane

- **Signals:** `SIGHUP` reload, `SIGTERM` graceful shutdown.
- **HTTP server (local):**
  - Read-only status / metrics.
  - Global pause/resume (stops all output writing; capture stays open).
  - Per-output enable/disable.

### 2.6 Resilience

- Auto-reconnect on SDI signal loss. On loss: close current segment cleanly, leave a gap, start new segment on signal return.
- Per-output encoder auto-restart inside the process if an individual encoder dies.
- Disk full → pause writes, emit alert in logs, resume automatically when space frees.
- External watchdog restarts the whole service on hard crash.
- External retention watchdog (separate app, not part of ffrecord) deletes oldest files when disk threshold reached. ffrecord only writes.

### 2.7 Logging

- **Rotating log file per service instance**, 7-day rollover, scoped to the channel (e.g. `logs/ffrecord_CH1.log`).
- stderr stream for the service supervisor (NSSM/journald) to collect.
- Logging must cover:
  - DeckLink capture events (open, format change, signal loss/return, dropped frames, hardware queue state).
  - A/V sync diagnostics (timestamps, drift, gap detection).
  - Per-output encoding events (start/stop, restart, segment open/close, errors).

---

## 3. What gets extracted from ffcapture

**Copied / adapted:**
- DeckLink capture core (`PyAVDeckLinkCapture` and/or `RealDeckLinkCapture` — see open question below).
- Audio drain patterns and queue handling.
- Pipeline lifecycle / threading skeleton from `src/pipeline.py`.

**Not extracted:**
- GUI (`src/gui.py`).
- Overlay / subtitle rendering (`src/overlay.py`, `src/config_subtitles.py`, `SubtitleConfigDialog`).
- Audio preview / playback into GUI.

---

## 4. Decisions made during scoping

| Topic                          | Decision                                                                                                    |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| Code relationship to ffcapture | New sibling project, copy what's needed                                                                     |
| Output configuration           | User-configurable list of N outputs                                                                         |
| Segment strategy               | Per-output segment policy                                                                                   |
| Operator interface             | Headless service (config file + logs)                                                                       |
| Encoder selection              | PyAV in-process — requires NVENC-enabled libav build (operator's responsibility)                            |
| Resilience features            | Auto-reconnect on signal loss; auto-restart crashed encoders; disk-full pause/alert/resume; external watchdog process-level restart |
| Retention                      | External watchdog app deletes oldest files when disk threshold reached. ffrecord does not handle retention. |
| Runtime control                | Signals (HUP/TERM) **+** HTTP for status and pause/enable                                                   |
| DeckLink input count           | One per service instance; multiple instances for multiple channels                                          |
| Output codecs                  | NVENC (h264/hevc) into MOV/MP4/MXF; HLS for preview. All encoder settings from user config.                 |
| Interlaced handling            | Must support 25i → 50p (deinterlace at capture)                                                             |
| HLS behavior                   | Live rolling window, last 2 chunks                                                                          |
| Lifecycle                      | Both: global pause AND per-output toggles via HTTP                                                          |
| Audio routing                  | Per-output audio config (channel selection, downmix)                                                        |
| Deinterlace placement          | Once at capture; all outputs receive 50p                                                                    |
| Config format                  | YAML per service instance                                                                                   |
| Project name                   | `ffrecord`                                                                                                  |
| Project path                   | `C:\dev\ffrecord`                                                                                           |
| HTTP /stop semantics           | Both: global pause AND per-output toggles                                                                   |
| Signal-loss behavior           | Close current segment cleanly, gap, new segment on return                                                   |
| Filename template              | Per-output template; default `{output}/{CH}/{YYYYMMDDHH}/{starttime_unix_ms}.mov`                           |
| Logging                        | Rotating log file per service instance (7-day rollover) + stderr for supervisor                             |
| Internal concurrency           | Capture thread → fan-out queue → thread per output                                                          |

---

## 5. Open questions — RESOLVED

All open questions from §5.1 and §5.2 were resolved during a follow-up planning session. The complete implementation plan is at:

**`C:\dev\ffrecord\PLAN.md`**

Key resolutions:
- **Capture path:** COM/native primary, PyAV dropped from capture (PyAV stays for encoding only).
- **HLS muxing:** PyAV's libav HLS muxer (`hls_list_size=2`, `hls_flags=delete_segments`).
- **Segment rollover:** handled inside the output thread; capture thread is unaware.
- **HTTP library:** stdlib `http.server` (zero deps).
- **Format-change handling:** treated as signal-loss event — close all segments, reinit, resume.
- **Channel naming:** comes from YAML `channel.name` field.

---

## 6. Next steps

See `C:\dev\ffrecord\PLAN.md` for the full implementation plan and ordered task list.
