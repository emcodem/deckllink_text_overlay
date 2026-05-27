"""Tests for the standalone vistek_analyzer module."""

import math
import os
import sys
from fractions import Fraction

# Make the repo root importable so ``import vistek_analyzer`` works.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import av
import numpy as np
import pytest

from vistek_analyzer import (
    AnalyzerConfig,
    AudioSilenceDetector,
    BlackCrossDetector,
    Correlator,
    DelayEvent,
    analyze_file,
)


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic Vistek-like clip generator
# ─────────────────────────────────────────────────────────────────────────────


def _generate_clip(
    path,
    duration_s: float = 10.0,
    fps: int = 25,
    sample_rate: int = 48000,
    event_times_s=(4.0, 8.0),
    event_duration_s: float = 0.5,
    audio_skew_ms: float = 0.0,
    width: int = 320,
    height: int = 240,
):
    """Write an mkv with:
      - lossless video: white frames, with the middle scan-line forced black
        during each event window;
      - PCM stereo audio: 1 kHz tone, with channel 1 muted during each event
        window (optionally offset by ``audio_skew_ms``).
    """
    container = av.open(str(path), mode="w")
    vstream = container.add_stream("ffv1", rate=fps)
    vstream.width = width
    vstream.height = height
    vstream.pix_fmt = "yuv420p"
    astream = container.add_stream("pcm_s16le", rate=sample_rate)
    astream.layout = "stereo"

    # ── audio ──
    n_samples = int(round(duration_s * sample_rate))
    t = np.arange(n_samples) / sample_rate
    tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    ch1 = tone.copy()
    ch2 = tone.copy()
    skew_samples = int(round(audio_skew_ms / 1000.0 * sample_rate))
    for ev_t in event_times_s:
        mute_start = int(round(ev_t * sample_rate)) + skew_samples
        mute_end = mute_start + int(round(event_duration_s * sample_rate))
        if mute_start < n_samples and mute_end > 0:
            ch1[max(0, mute_start):min(mute_end, n_samples)] = 0.0

    interleaved = np.empty(n_samples * 2, dtype=np.int16)
    interleaved[0::2] = (ch1 * 32767).astype(np.int16)
    interleaved[1::2] = (ch2 * 32767).astype(np.int16)

    chunk_size = 1024
    for i in range(0, n_samples, chunk_size):
        n = min(chunk_size, n_samples - i)
        chunk = interleaved[i * 2:(i + n) * 2].reshape(1, n * 2)
        frame = av.AudioFrame.from_ndarray(chunk, format="s16", layout="stereo")
        frame.rate = sample_rate
        frame.pts = i
        frame.time_base = Fraction(1, sample_rate)
        for pkt in astream.encode(frame):
            container.mux(pkt)
    for pkt in astream.encode():
        container.mux(pkt)

    # ── video ──
    n_frames = int(round(duration_s * fps))
    base_white = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_frames):
        t_frame = i / fps
        is_event = any(
            ev_t <= t_frame < ev_t + event_duration_s for ev_t in event_times_s
        )
        img = base_white.copy()
        if is_event:
            img[height // 2, :] = 0
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        frame.pts = i
        for pkt in vstream.encode(frame):
            container.mux(pkt)
    for pkt in vstream.encode():
        container.mux(pkt)

    container.close()


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — Correlator
# ─────────────────────────────────────────────────────────────────────────────


def test_correlator_emits_on_first_complete_pair():
    c = Correlator()
    ev = c.update(silence_pts_s=1.0, cross_pts_s=0.5, trigger="silence")
    assert isinstance(ev, DelayEvent)
    assert ev.delay_ms == 500.0
    assert ev.trigger == "silence"


def test_correlator_holds_until_both_sides_present():
    c = Correlator()
    assert c.update(None, 0.5, "cross") is None
    assert c.update(0.5, None, "silence") is None


def test_correlator_emits_every_pair():
    c = Correlator()
    ev1 = c.update(1.0, 0.5, "silence")
    assert ev1 is not None
    # Same delay still emits — one line per pair.
    assert c.update(1.0, 0.5, "cross") is not None
    assert c.update(1.0, 0.5, "silence") is not None


def test_correlator_reemits_when_value_changes():
    c = Correlator()
    c.update(1.0, 0.5, "silence")
    ev = c.update(1.1, 0.5, "silence")
    assert ev is not None and ev.delay_ms == 600.0


def test_correlator_sign_convention_audio_late():
    c = Correlator()
    ev = c.update(1.0, 0.0, "silence")
    assert ev.delay_ms == 1000.0


def test_correlator_sign_convention_audio_early():
    c = Correlator()
    ev = c.update(0.0, 1.0, "cross")
    assert ev.delay_ms == -1000.0


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — BlackCrossDetector
# ─────────────────────────────────────────────────────────────────────────────


def _mk_video_frame(pixel_rgb, pts, width=320, height=240, sample_x=10):
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    img[height // 2, sample_x] = pixel_rgb
    f = av.VideoFrame.from_ndarray(img, format="rgb24")
    f.pts = pts
    f.time_base = Fraction(1, 25)
    return f


def test_black_cross_fires_on_second_consecutive_non_white_frame():
    """Cross fires on the SECOND consecutive non-white frame; reports that frame's PTS."""
    cfg = AnalyzerConfig()
    det = BlackCrossDetector(cfg)
    tb = Fraction(1, 25)

    assert det.feed(_mk_video_frame((255, 255, 255), pts=0), tb) is None
    # First non-white frame: candidate only, no event yet.
    assert det.feed(_mk_video_frame((0, 0, 0), pts=1), tb) is None
    # Second consecutive non-white: confirmed cross at this PTS.
    e = det.feed(_mk_video_frame((0, 0, 0), pts=2), tb)
    assert e is not None and pytest.approx(e, abs=1e-9) == 2 / 25
    # Further non-white frames: already fired, no second event.
    assert det.feed(_mk_video_frame((0, 0, 0), pts=3), tb) is None
    # Return to white: no event.
    assert det.feed(_mk_video_frame((255, 255, 255), pts=4), tb) is None
    # Next cross: again requires two consecutive frames.
    assert det.feed(_mk_video_frame((0, 0, 0), pts=5), tb) is None
    e2 = det.feed(_mk_video_frame((0, 0, 0), pts=6), tb)
    assert e2 is not None and pytest.approx(e2, abs=1e-9) == 6 / 25


def test_black_cross_single_non_white_frame_suppressed():
    """A single non-white frame (encoder pre-ringing artefact) must not fire."""
    cfg = AnalyzerConfig()
    det = BlackCrossDetector(cfg)
    tb = Fraction(1, 25)

    assert det.feed(_mk_video_frame((255, 255, 255), pts=0), tb) is None
    # One non-white frame (any value well below threshold) → no event.
    assert det.feed(_mk_video_frame((100, 100, 100), pts=1), tb) is None
    # Returns to white → still no event.
    assert det.feed(_mk_video_frame((255, 255, 255), pts=2), tb) is None


def test_black_cross_threshold_boundary():
    """Exactly at white threshold is not a cross; one below requires two frames."""
    cfg = AnalyzerConfig(colorbar_white_min=242)
    det = BlackCrossDetector(cfg)
    tb = Fraction(1, 25)

    # 242 in all channels → white → no cross.
    assert det.feed(_mk_video_frame((242, 242, 242), pts=0), tb) is None
    # 241: below threshold, first frame (candidate only).
    assert det.feed(_mk_video_frame((241, 241, 241), pts=1), tb) is None
    # 241 again: confirmed → fires.
    e = det.feed(_mk_video_frame((241, 241, 241), pts=2), tb)
    assert e is not None


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — AudioSilenceDetector
# ─────────────────────────────────────────────────────────────────────────────


def _mk_audio_frame(samples_ch0, sample_rate, pts):
    """Build a mono fltp AudioFrame from a 1-D float32 buffer."""
    arr = samples_ch0.astype(np.float32, copy=False).reshape(1, -1)
    f = av.AudioFrame.from_ndarray(arr, format="fltp", layout="mono")
    f.rate = sample_rate
    f.pts = pts
    f.time_base = Fraction(1, sample_rate)
    return f


def test_audio_silence_rms_threshold():
    cfg = AnalyzerConfig(audio_threshold_db=-50.0, rms_window_ms=2.0)
    sr = 48000
    det = AudioSilenceDetector(cfg, sr)
    tb = Fraction(1, sr)

    # 100 ms of tone → no silence.
    t = np.arange(int(sr * 0.1)) / sr
    loud = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    assert det.feed(_mk_audio_frame(loud, sr, pts=0), tb) == []

    # 100 ms of zeros immediately after → exactly one rising edge.
    silent = np.zeros(int(sr * 0.1), dtype=np.float32)
    events = det.feed(_mk_audio_frame(silent, sr, pts=len(loud)), tb)
    assert len(events) == 1
    # The reported timestamp should land within the first window of silence.
    expected_start = len(loud) / sr
    assert events[0] == pytest.approx(expected_start, abs=cfg.rms_window_ms / 1000.0)


def test_audio_silence_only_rises_on_change():
    """A long stretch of continuous silence still fires only one event."""
    cfg = AnalyzerConfig()
    sr = 48000
    det = AudioSilenceDetector(cfg, sr)
    tb = Fraction(1, sr)

    # Prime with one window of tone so the state machine starts non-silent.
    t = np.arange(int(sr * 0.01)) / sr
    loud = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    det.feed(_mk_audio_frame(loud, sr, pts=0), tb)

    silent = np.zeros(int(sr * 0.5), dtype=np.float32)
    events = det.feed(_mk_audio_frame(silent, sr, pts=len(loud)), tb)
    assert len(events) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests — full pipeline on synthesized clips
# ─────────────────────────────────────────────────────────────────────────────


def test_synthesized_aligned(tmp_path):
    clip = tmp_path / "aligned.mkv"
    _generate_clip(str(clip), audio_skew_ms=0.0)
    events = list(analyze_file(str(clip)))
    assert len(events) >= 1, "expected at least one delay measurement"
    # First measurement should be small — one video frame (40 ms at 25 fps)
    # plus one RMS window (2 ms) is the worst-case quantization error.
    assert abs(events[0].delay_ms) < 45.0, f"got {events[0].delay_ms}"


def test_synthesized_audio_late(tmp_path):
    # 2-frame confirmation shifts detected cross +40 ms later (lossless, no pre-ringing),
    # so an 80 ms skew measures as ~+40 ms.
    clip = tmp_path / "late.mkv"
    _generate_clip(str(clip), audio_skew_ms=80.0)
    events = list(analyze_file(str(clip)))
    delays = [e.delay_ms for e in events]
    assert any(20 < d < 80 for d in delays), f"events: {delays}"


def test_synthesized_audio_early(tmp_path):
    # 2-frame confirmation shifts detected cross +40 ms later, so a -40 ms skew
    # measures as ~-80 ms.
    clip = tmp_path / "early.mkv"
    _generate_clip(str(clip), audio_skew_ms=-40.0)
    events = list(analyze_file(str(clip)))
    delays = [e.delay_ms for e in events]
    assert any(-100 < d < -20 for d in delays), f"events: {delays}"


def test_synthesized_no_events_emits_nothing(tmp_path):
    clip = tmp_path / "no_signal.mkv"
    _generate_clip(str(clip), event_times_s=(), audio_skew_ms=0.0)
    events = list(analyze_file(str(clip)))
    assert events == []


def test_synthesized_two_events_stable_delay_all_agree(tmp_path):
    """With a constant skew across two events, every emitted line should
    report the same delay value."""
    clip = tmp_path / "stable.mkv"
    _generate_clip(str(clip), event_times_s=(4.0, 8.0), audio_skew_ms=40.0)
    events = list(analyze_file(str(clip)))
    assert len(events) >= 2, f"expected at least one line per event, got {len(events)}"
    unique_delays = {round(e.delay_ms) for e in events}
    assert len(unique_delays) == 1, f"delay values disagree: {[e.delay_ms for e in events]}"
