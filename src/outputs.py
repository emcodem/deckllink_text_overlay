"""Encoding outputs using PyAV: UDP streaming, file recording, etc."""

import logging
import os
import socket
import threading
import time
from abc import ABC, abstractmethod
from fractions import Fraction
from typing import Optional, List
from urllib.parse import urlparse
from .capture import Frame, AudioSample
from . import config as _config
import numpy as np


try:
    import av
    HAS_AV = True
except ImportError:
    HAS_AV = False

logger = logging.getLogger(__name__)


class BaseOutput(ABC):
    """Abstract base for encoding outputs."""

    def __init__(self, name: str):
        self.name = name
        self.is_running = False

    @abstractmethod
    def start(self):
        """Start the output."""
        pass

    @abstractmethod
    def push(self, frame: Optional[Frame], audio: Optional[AudioSample]):
        """Push a frame and/or audio block to the output."""
        pass

    @abstractmethod
    def stop(self):
        """Stop the output gracefully."""
        pass


class UDPMonoOutput(BaseOutput):
    """Audio-only UDP output: raw little-endian 16-bit PCM, mono, 16 kHz.

    Equivalent to ``ffmpeg -f s16le -acodec pcm_s16le -ar 16k`` — no container,
    no codec framing, just raw samples in UDP datagrams.
    """

    def __init__(self, channels: List[int], url: str = "udp://127.0.0.1:12345"):
        super().__init__(f"UDPMono({','.join(map(str, channels))})")
        self.channels = [c - 1 for c in channels]  # Convert to 0-based
        self.url = url
        self._sock = None
        self._dest = None
        self._frame_count = 0

    def start(self):
        if self.is_running:
            return

        try:
            parsed = urlparse(self.url)
            host = parsed.hostname or '127.0.0.1'
            port = parsed.port or 12345
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._dest = (host, port)
            self.is_running = True
            logger.info(
                f"{self.name}: Started (raw s16le mono @16kHz → {host}:{port}, "
                f"mixing channels {[c+1 for c in self.channels]})"
            )
        except Exception as e:
            logger.error(f"{self.name}: Failed to start: {e}", exc_info=True)
            self.is_running = False

    def push(self, frame: Optional[Frame], audio: Optional[AudioSample]):
        if not self.is_running or not audio:
            return

        try:
            if self._frame_count == 0:
                logger.info(
                    f"{self.name}: First audio - shape={audio.data.shape}, "
                    f"channels={audio.channels}, sample_rate={audio.sample_rate}"
                )

            if audio.data.shape[1] < max(self.channels) + 1:
                logger.warning(
                    f"{self.name}: Audio has {audio.data.shape[1]} channels, "
                    f"but requested {[c+1 for c in self.channels]} (1-based)"
                )
                return

            # Mix selected channels in int32 to avoid overflow, then downcast.
            selected = audio.data[:, self.channels].astype(np.int32)
            mono = (selected.sum(axis=1) // len(self.channels)).astype(np.int16)

            # 48 kHz → 16 kHz by simple decimation (matches existing behavior).
            mono_16k = mono[::3]

            self._sock.sendto(mono_16k.tobytes(), self._dest)
            self._frame_count += 1

        except Exception as e:
            logger.error(f"{self.name}: Error sending audio: {e}", exc_info=True)

    def stop(self):
        if not self.is_running:
            return
        try:
            if self._sock:
                self._sock.close()
            logger.info(f"{self.name}: Stopped ({self._frame_count} audio blocks sent)")
        except Exception as e:
            logger.error(f"{self.name}: Error stopping: {e}")
        finally:
            self.is_running = False


class TSFileOutput(BaseOutput):
    """Video + audio file output with NVENC encoding."""

    def __init__(self, path: str = r"C:\temp\monitoring.ts"):
        if not HAS_AV:
            raise RuntimeError("PyAV is required. Install with: pip install av")

        super().__init__(f"TSFile({path})")
        self.path = path
        self._base_path = path  # Original path; used to compute rotation filenames on framerate change.
        self.container = None
        self.video_stream = None
        self.audio_streams: List = []

        # Cached audio config — lets us re-init streams on rotation without waiting for a fresh AudioSample.
        self._audio_sample_rate: int = 48000
        self._audio_channels_n: int = 0

        # Shared startup anchor — set once from the first hw_pts_valid sample (or wall-clock fallback).
        # Used only to gate the start of recording until the DeckLink signal has stabilized;
        # PTS values themselves are derived from frame/sample counters (see _video_pts + _audio_samples_emitted).
        self._t0_hw: int = 0
        self._t0_hw_rate: int = 10_000_000
        self._t0_hw_set: bool = False
        self._t0_wall: float = 0.0          # sample.timestamp at anchor instant (wall-relative)
        self._t0_set_wall_time: float = 0.0  # time.time() at anchor instant (for drift calc)
        self._t0_anchor_mode: str = "none"   # "hw_clock" | "wall_clock_fallback" | "none"

        # Pre-anchor drop counting and graduated escalation
        self._pre_t0_drop_count: int = 0
        self._video_samples_seen: int = 0    # invalid video samples before anchor
        self._audio_samples_seen: int = 0    # invalid audio samples before anchor

        # Stored during _init_streams for PTS derivation
        self._fps_num: int = 25
        self._fps_den: int = 1

        # Monotonic counter PTS — decoupled from hw_pts to avoid clock-domain mismatch.
        # Video PTS = frame counter; audio PTS = cumulative samples emitted.
        self._video_frame_counter: int = 0
        self._audio_samples_emitted: int = 0   # cumulative samples written to the current file

        # Audio buffer: all incoming AudioSample.data is appended here. Drained per video frame
        # so each frame's encoder packet carries exactly samples_per_frame samples.
        self._audio_buf: Optional[np.ndarray] = None        # (samples, channels), int16
        self._aligned_startup: bool = False                 # True once first video+audio pair is encoded

        # Selected audio codec (set during _create_streams via codec fallback chain).
        self._audio_codec_name: str = "aac"

        # Buffered first video frame — held until audio buffer is non-empty so we can align.
        self._buffered_frame: Optional[Frame] = None

        # Gap detection: last valid hw_pts seen for each stream
        self._last_video_hw_pts: Optional[int] = None
        self._last_audio_hw_pts: Optional[int] = None

        # Mid-stream hw_pts failure counters (graduated logging)
        self._mid_stream_hwpts_failures_video: int = 0
        self._mid_stream_hwpts_failures_audio: int = 0

        # Totals for [av_sync_stop] summary
        self._video_encoded: int = 0
        self._audio_encoded: int = 0
        self._gaps_detected: int = 0
        self._max_drift_ms: float = 0.0

        # Periodic log timing
        self._last_drift_check_wall_time: float = 0.0
        self._last_health_log_wall_time: float = 0.0

        # Debug counters (AV_SYNC_DEBUG only)
        self._video_debug_count: int = 0
        self._audio_debug_count: int = 0

        # Optional capture reference for queue high-water marks in health log
        self._capture_ref = None

    def start(self):
        """Initialize file output container and streams."""
        if self.is_running:
            return
        self.is_running = True
        logger.info(f"{self.name}: Waiting for first valid hw_pts sample to initialize...")

    def _set_anchor(self, anchor, mode: str):
        """Record the t0 anchor from the given sample."""
        self._t0_hw = anchor.hw_pts if mode == "hw_clock" else 0
        self._t0_hw_rate = anchor.hw_pts_rate
        self._t0_hw_set = True
        self._t0_wall = anchor.timestamp
        self._t0_set_wall_time = time.time()
        self._t0_anchor_mode = mode
        logger.info(
            f"[av_sync_anchor] {self.name}: mode={mode} anchor_hw_pts={self._t0_hw} "
            f"anchor_hw_pts_rate={self._t0_hw_rate} anchor_wall_seconds={self._t0_wall:.3f} "
            f"anchor_abs_wall={self._t0_set_wall_time:.3f} "
            f"pre_anchor_drops={self._pre_t0_drop_count} "
            f"video_samples_seen={self._video_samples_seen} "
            f"audio_samples_seen={self._audio_samples_seen}"
        )

    # Output extension → (PyAV/FFmpeg container format, ordered audio codec candidates).
    # First codec accepted by the muxer wins.
    _FORMAT_BY_EXT = {
        '.ts':  ('mpegts',   ('aac', 's302m', 'pcm_s16le')),
        '.mkv': ('matroska', ('aac', 'pcm_s16le')),
        '.mp4': ('mp4',      ('aac',)),
        '.avi': ('avi',      ('pcm_s16le', 'aac')),
        '.mxf': ('mxf',      ('pcm_s16le',)),
    }

    def _format_and_codecs_for_path(self):
        ext = os.path.splitext(self.path)[1].lower()
        try:
            return self._FORMAT_BY_EXT[ext]
        except KeyError:
            raise RuntimeError(
                f"Unsupported output extension '{ext}' for {self.path} "
                f"— known: {list(self._FORMAT_BY_EXT)}"
            )

    def _create_streams(self, width: int, height: int, fps_num: int, fps_den: int,
                        audio_channels: int, audio_sample_rate: int):
        """Open container at self.path and create video + audio streams.

        Container format is derived from the file extension; audio codec is selected
        by trying that format's candidates in order. First one the muxer accepts wins.
        """
        container_format, codec_candidates = self._format_and_codecs_for_path()
        last_err = None
        for codec in codec_candidates:
            try:
                self._open_container_with_codec(
                    width=width, height=height, fps_num=fps_num, fps_den=fps_den,
                    audio_channels=audio_channels, audio_sample_rate=audio_sample_rate,
                    audio_codec=codec, container_format=container_format,
                )
                self._audio_codec_name = codec
                logger.info(
                    f"{self.name}: container = {container_format}, audio codec = {codec} "
                    f"@ {audio_sample_rate}Hz ({audio_channels}x mono)"
                )
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    f"{self.name}: audio codec '{codec}' not usable with {container_format}: {e}"
                )
                try:
                    if self.container:
                        self.container.close()
                except Exception:
                    pass
                self.container = None
                self.video_stream = None
                self.audio_streams = []
        else:
            raise RuntimeError(
                f"No audio codec from {codec_candidates} worked for {container_format}: {last_err}"
            )

        self._fps_num = fps_num
        self._fps_den = fps_den
        self._audio_sample_rate = audio_sample_rate
        self._audio_channels_n = audio_channels

    def _open_container_with_codec(self, width: int, height: int, fps_num: int, fps_den: int,
                                   audio_channels: int, audio_sample_rate: int, audio_codec: str,
                                   container_format: str):
        """Open container and create video + audio streams for one codec choice."""
        self.container = av.open(self.path, 'w', format=container_format)
        rate = Fraction(fps_num, fps_den)

        try:
            self.video_stream = self.container.add_stream('h264_nvenc', rate=rate) #h264_nvenc
            self.video_stream.options = {'preset': 'fast', 'crf': '23' ,'g': '1', 'bf':'0'}
            logger.info(f"{self.name}: Using h264_nvenc for video encoding @ {rate}")
        except Exception as e:
            logger.warning(f"{self.name}: NVENC not available ({e}), falling back to h264")
            self.video_stream = self.container.add_stream('h264', rate=rate)
            self.video_stream.options = {'preset': 'fast', 'crf': '23' ,'g': '1', 'bf':0}

        self.video_stream.width = width
        self.video_stream.height = height
        self.video_stream.pix_fmt = 'yuv420p'

        # One mono stream per input channel — avoids FFmpeg assigning surround roles
        # (e.g. LFE on ch4) from a multi-channel layout.
        self.audio_streams = []
        for _ in range(audio_channels):
            stream = self.container.add_stream(audio_codec, rate=audio_sample_rate, layout='mono')
            if audio_codec == 'aac':
                stream.options = {'b:a': '128k'}
            self.audio_streams.append(stream)

    def _init_streams(self, frame: Frame, audio_channels: int, audio_sample_rate: int):
        """Initialize video and audio streams on first frame.

        Audio characteristics (channels, sample_rate) come from the already-buffered
        audio samples rather than a fresh AudioSample, so we can re-init on rotation
        without waiting.
        """
        if self.container:
            return

        try:
            fps_num, fps_den = frame.framerate
            if not fps_den:
                fps_num, fps_den = 30, 1

            self._create_streams(
                width=frame.width, height=frame.height,
                fps_num=fps_num, fps_den=fps_den,
                audio_channels=audio_channels, audio_sample_rate=audio_sample_rate,
            )

            logger.info(
                f"{self.name}: Initialized - video {frame.width}x{frame.height}@"
                f"{fps_num}/{fps_den}fps, audio {audio_channels}x mono@{audio_sample_rate}Hz "
                f"-> {self.path}"
            )

        except Exception as e:
            logger.error(f"{self.name}: Failed to initialize streams: {e}", exc_info=True)
            self.is_running = False

    # --- Audio buffer / alignment helpers -------------------------------------------------

    def _append_audio(self, audio: AudioSample):
        """Append samples to _audio_buf."""
        if self._audio_buf is None or self._audio_buf.shape[0] == 0:
            self._audio_buf = audio.data.astype(np.int16, copy=True)
        else:
            self._audio_buf = np.concatenate(
                [self._audio_buf, audio.data.astype(np.int16, copy=False)], axis=0
            )
        # Cache config in case streams aren't open yet.
        self._audio_sample_rate = audio.sample_rate
        self._audio_channels_n = audio.channels

    def _samples_target_after_frames(self, n_frames: int) -> int:
        """Cumulative audio samples that should be emitted after n_frames video frames.

        Integer arithmetic — for fractional rates (e.g. 30000/1001) the delta
        alternates 1601/1602 samples but the cumulative count stays exact.
        """
        return n_frames * self._audio_sample_rate * self._fps_den // self._fps_num

    def _consume_audio_for_frame(self) -> np.ndarray:
        """Pop the audio samples paired with the just-encoded video frame.

        Uses cumulative target so fractional rates don't drift. Pads with silence
        (and warns) if the buffer underruns — indicates a capture stall.
        """
        target = self._samples_target_after_frames(self._video_frame_counter)
        need = target - self._audio_samples_emitted
        if need <= 0:
            return np.zeros((0, self._audio_channels_n), dtype=np.int16)

        avail = self._audio_buf.shape[0] if self._audio_buf is not None else 0
        if avail >= need:
            chunk = self._audio_buf[:need].copy()
            self._audio_buf = self._audio_buf[need:]
            return chunk

        # Underrun — splice whatever we have with silence.
        pad = need - avail
        if avail > 0:
            head = self._audio_buf
            self._audio_buf = self._audio_buf[:0]
        else:
            head = np.zeros((0, self._audio_channels_n), dtype=np.int16)
        silence = np.zeros((pad, self._audio_channels_n), dtype=np.int16)
        logger.warning(
            f"[av_sync_warn] {self.name}: audio buffer underrun for frame "
            f"#{self._video_frame_counter} — padded {pad} silent samples (had {avail}, needed {need})"
        )
        return np.concatenate([head, silence], axis=0) if head.shape[0] else silence

    def _next_rotation_path(self) -> str:
        """Return the first '{root}{N}{ext}' path that does not exist on disk (N >= 1)."""
        root, ext = os.path.splitext(self._base_path)
        n = 1
        while True:
            candidate = f"{root}{n}{ext}"
            if not os.path.exists(candidate):
                return candidate
            n += 1

    def _rotate_for_framerate_change(self, frame: Frame):
        """Flush+close the current file and open a new counter-suffixed file.

        Resets PTS counters and gap-detection state so the new file starts at PTS 0.
        Audio stream config (sample_rate, channels) is reused from the previous file.
        """
        new_num, new_den = frame.framerate
        if not new_den:
            new_num, new_den = 30, 1
        old_num, old_den, old_path = self._fps_num, self._fps_den, self.path

        try:
            if self.video_stream:
                for packet in self.video_stream.encode():
                    self.container.mux(packet)
            for stream in self.audio_streams:
                for packet in stream.encode():
                    self.container.mux(packet)
            if self.container:
                self.container.close()
        except Exception as e:
            logger.error(f"{self.name}: Error closing {old_path} during rotation: {e}", exc_info=True)

        new_path = self._next_rotation_path()
        logger.warning(
            f"[av_sync_rotate] {self.name}: framerate change {old_num}/{old_den} -> "
            f"{new_num}/{new_den}; rotating {old_path} -> {new_path}"
        )
        self.path = new_path

        # Reset per-file state. Audio buffer is preserved across rotation so we
        # don't drop samples; PTS counters restart at 0 for the new file.
        self.container = None
        self.video_stream = None
        self.audio_streams = []
        self._video_frame_counter = 0
        self._audio_samples_emitted = 0
        self._last_video_hw_pts = None
        self._last_audio_hw_pts = None
        # stream_time resets to 0 after each StartStreams(), so the old anchor is
        # invalid for the new file — force re-anchor on first valid frame.
        self._t0_hw_set = False
        self._last_drift_check_wall_time = 0.0
        self._last_health_log_wall_time = 0.0
        # Steady state already aligned; only the original first-video pairing matters.
        # Keep _aligned_startup = True so the new file doesn't try to re-align.

        try:
            self._create_streams(
                width=frame.width, height=frame.height,
                fps_num=new_num, fps_den=new_den,
                audio_channels=self._audio_channels_n,
                audio_sample_rate=self._audio_sample_rate,
            )
            logger.info(
                f"{self.name}: Rotated - video {frame.width}x{frame.height}@"
                f"{new_num}/{new_den}fps, audio {self._audio_channels_n}x mono@"
                f"{self._audio_sample_rate}Hz -> {self.path}"
            )
        except Exception as e:
            logger.error(f"{self.name}: Failed to open rotated file {self.path}: {e}", exc_info=True)
            self.is_running = False

    def _video_pts(self, frame: Frame) -> int:
        """Monotonic frame-count PTS. Advances on each call."""
        pts = self._video_frame_counter
        self._video_frame_counter += 1
        return pts

    def _maybe_log_drift(self):
        """Emit [av_sync_drift] every AV_SYNC_DRIFT_CHECK_INTERVAL_S seconds."""
        if self._t0_anchor_mode != "hw_clock" or self._last_video_hw_pts is None:
            return
        now = time.time()
        if now - self._last_drift_check_wall_time < _config.AV_SYNC_DRIFT_CHECK_INTERVAL_S:
            return
        self._last_drift_check_wall_time = now
        wall_elapsed = now - self._t0_set_wall_time
        hw_elapsed = (self._last_video_hw_pts - self._t0_hw) / self._t0_hw_rate
        drift_ms = (wall_elapsed - hw_elapsed) * 1000
        if abs(drift_ms) > self._max_drift_ms:
            self._max_drift_ms = abs(drift_ms)
        level = logging.WARNING if abs(drift_ms) > _config.AV_SYNC_DRIFT_WARN_MS else logging.INFO
        logger.log(level,
            f"[av_sync_drift] {self.name}: wall={wall_elapsed:.3f}s hw={hw_elapsed:.3f}s "
            f"drift_ms={drift_ms:+.1f}"
        )

    def _maybe_log_health(self):
        """Emit [av_sync_health] every AV_SYNC_HEALTH_INTERVAL_S seconds."""
        now = time.time()
        if now - self._last_health_log_wall_time < _config.AV_SYNC_HEALTH_INTERVAL_S:
            return
        self._last_health_log_wall_time = now
        frame_hi, audio_hi = self._capture_ref.get_queue_high_water() if self._capture_ref else (0, 0)
        total_failures = self._mid_stream_hwpts_failures_video + self._mid_stream_hwpts_failures_audio
        logger.info(
            f"[av_sync_health] {self.name}: video_encoded={self._video_encoded} "
            f"audio_encoded={self._audio_encoded} hwpts_failures={total_failures} "
            f"gaps={self._gaps_detected} max_drift_ms={self._max_drift_ms:.1f} "
            f"frame_queue_hi={frame_hi} audio_queue_hi={audio_hi}"
        )

    def _encode_video(self, frame: Frame):
        """Encode and mux a single video frame."""
        if not self.video_stream:
            return

        # Framerate change → rotate to next counter-suffixed file before encoding this frame.
        new_num, new_den = frame.framerate
        if not new_den:
            new_num, new_den = 30, 1
        if new_num != self._fps_num or new_den != self._fps_den:
            self._rotate_for_framerate_change(frame)
            if not self.video_stream:
                return

        try:
            # Mid-stream hw_pts failure detection
            if not frame.hw_pts_valid:
                self._mid_stream_hwpts_failures_video += 1
                count = self._mid_stream_hwpts_failures_video
                if count in (1, 10, 100, 1000) or (count > 1000 and count % 1000 == 0):
                    level = logging.ERROR if count >= 1000 else logging.WARNING
                    logger.log(level,
                        f"[av_sync_warn] {self.name}: hw_pts_valid=False mid-stream (video #{count})"
                    )

            # Gap detection (only on valid samples)
            if frame.hw_pts_valid and self._last_video_hw_pts is not None:
                expected = frame.hw_pts_rate * self._fps_den / self._fps_num
                delta = frame.hw_pts - self._last_video_hw_pts
                if delta > _config.AV_SYNC_GAP_THRESHOLD * expected:
                    missing = (delta - expected) / expected
                    logger.warning(
                        f"[av_sync_warn] {self.name}: video gap detected "
                        f"delta={delta} expected={expected:.0f} missing≈{missing:.1f} frames"
                    )
                    self._gaps_detected += 1
            if frame.hw_pts_valid:
                self._last_video_hw_pts = frame.hw_pts

            pts = self._video_pts(frame)
            yuv = av.VideoFrame.from_ndarray(frame.data, format='rgb24').reformat(format='yuv420p')
            yuv.pts = pts

            if self._video_encoded < 5:
                logger.info(f"[video_debug] frame#{self._video_encoded} hw_pts={frame.hw_pts} hw_pts_valid={frame.hw_pts_valid} "
                           f"derived_pts={pts} fps=({self._fps_num}/{self._fps_den})")

            for i, packet in enumerate(self.video_stream.encode(yuv)):
                if self._video_encoded < 5:
                    logger.info(f"[video_packet] frame#{self._video_encoded} packet#{i} pts={packet.pts} dts={packet.dts}")
                self.container.mux(packet)
            self._video_encoded += 1

            if _config.AV_SYNC_DEBUG and self._video_debug_count < 100:
                delta_d = (frame.hw_pts - self._t0_hw) if (frame.hw_pts_valid and self._t0_hw_set) else None
                logger.info(
                    f"[av_sync_debug] video hw_pts={frame.hw_pts} derived_pts={pts} "
                    f"delta={delta_d} hw_pts_valid={frame.hw_pts_valid}"
                )
                self._video_debug_count += 1

            self._maybe_log_drift()
            self._maybe_log_health()

        except Exception as e:
            logger.error(f"{self.name}: Error encoding video frame #{self._video_encoded}: {e}", exc_info=True)
            logger.error(f"[mux_error_context] video_encoded={self._video_encoded} audio_encoded={self._audio_encoded} "
                        f"t0_hw_set={self._t0_hw_set} anchor_mode={self._t0_anchor_mode}")
            raise

    def _encode_audio_chunk(self, chunk: np.ndarray):
        """Encode one audio chunk (samples_per_frame × channels, int16) across all mono streams.

        Called once per video frame with exactly the audio that pairs with it.
        PTS is the cumulative sample count emitted so far.
        """
        if not self.audio_streams or chunk.shape[0] == 0:
            return
        try:
            pts = self._audio_samples_emitted

            if self._audio_encoded < 3:
                logger.info(
                    f"[audio_debug] frame#{self._video_frame_counter - 1} pts={pts} "
                    f"shape={chunk.shape} codec={self._audio_codec_name}"
                )

            for ch_idx, stream in enumerate(self.audio_streams):
                if ch_idx >= chunk.shape[1]:
                    break
                ch_data = np.ascontiguousarray(chunk[:, ch_idx].reshape(1, -1))
                af = av.AudioFrame.from_ndarray(ch_data, format='s16', layout='mono')
                af.sample_rate = self._audio_sample_rate
                af.pts = pts
                af.time_base = Fraction(1, self._audio_sample_rate)

                for pkt_idx, packet in enumerate(stream.encode(af)):
                    if self._audio_encoded < 3:
                        logger.info(
                            f"[audio_packet] frame#{self._video_frame_counter - 1} ch#{ch_idx} "
                            f"pkt#{pkt_idx} pts={packet.pts} dts={packet.dts} "
                            f"size={packet.size if packet else 0}"
                        )
                    self.container.mux(packet)

            self._audio_samples_emitted += chunk.shape[0]
            self._audio_encoded += 1
            self._maybe_log_health()

        except Exception as e:
            logger.error(f"{self.name}: Error encoding audio chunk: {e}", exc_info=True)

    def push(self, frame: Optional[Frame], audio: Optional[AudioSample]):
        """Buffer audio, encode video frame-by-frame with paired audio.

        Flow:
          1. Anchor gate: drop everything until a hw_pts_valid sample (or wall-clock
             fallback after AV_SYNC_ANCHOR_TIMEOUT_SAMPLES) is seen.
          2. Append any incoming audio to the internal buffer; do not encode it directly.
          3. On the first video frame after init: align the audio buffer to the frame's
             wall-clock timestamp (slice or pad silence).
          4. On every subsequent video frame: encode the frame, then pop exactly
             samples_per_frame audio samples from the buffer and encode them as one
             audio packet so each frame ships with its own audio.
        """
        if not self.is_running:
            return
        if not frame and not audio:
            return

        anchor = frame if frame else audio

        # --- Hold-until-anchored startup phase ---
        if not self._t0_hw_set:
            if anchor.hw_pts_valid:
                self._set_anchor(anchor, "hw_clock")
                # Fall through
            else:
                self._pre_t0_drop_count += 1
                if frame:
                    self._video_samples_seen += 1
                else:
                    self._audio_samples_seen += 1
                n = self._pre_t0_drop_count
                if n == 50:
                    logger.info(
                        f"[av_sync_init] {self.name}: still waiting for valid hw_pts ({n} dropped)"
                    )
                elif n == 250:
                    logger.warning(
                        f"[av_sync_warn] {self.name}: still waiting for valid hw_pts ({n} dropped)"
                        f" — SDK may not expose GetHardwareReferenceTimestamp"
                    )
                elif n == 500:
                    logger.warning(
                        f"[av_sync_warn] {self.name}: still waiting for valid hw_pts ({n} dropped)"
                    )

                if n >= _config.AV_SYNC_ANCHOR_TIMEOUT_SAMPLES:
                    self._set_anchor(anchor, "wall_clock_fallback")
                else:
                    return  # drop this sample

        # --- Buffer incoming audio (never encoded directly) ---
        if audio:
            self._append_audio(audio)

        # --- Startup: buffer latest video frame, wait until audio is present, then start.
        #     GetStreamTime (video) and GetPacketTime (audio) share the same stream clock
        #     zeroed at StartStreams(), so no wall-clock alignment trim/pad is needed. ---
        if not self._aligned_startup:
            if frame:
                self._buffered_frame = frame  # latest frame (stale frames are discarded)

            if self._buffered_frame is None:
                return  # need at least one video frame to know dimensions
            if self._audio_buf is None or self._audio_buf.shape[0] == 0:
                return  # wait for first audio packet

            if not self.container:
                logger.info(f"{self.name}: Initializing streams")
                self._init_streams(
                    self._buffered_frame,
                    audio_channels=self._audio_channels_n,
                    audio_sample_rate=self._audio_sample_rate,
                )
                if not self.container:
                    return  # init failed

            self._aligned_startup = True
            first_frame = self._buffered_frame
            self._buffered_frame = None
            self._encode_video(first_frame)
            self._encode_audio_chunk(self._consume_audio_for_frame())
            return

        # --- Steady-state: video frame triggers encode + paired audio dispatch ---
        if frame:
            self._encode_video(frame)
            self._encode_audio_chunk(self._consume_audio_for_frame())

    def stop(self):
        """Flush, close file output, and emit [av_sync_stop] summary."""
        if not self.is_running:
            return

        try:
            # Drain any trailing buffered audio that didn't pair with a final video frame.
            # Emit it as one tail chunk so the audio track ends cleanly rather than mid-stream.
            if self.audio_streams and self._audio_buf is not None and self._audio_buf.shape[0] > 0:
                tail = self._audio_buf
                self._audio_buf = self._audio_buf[:0]
                logger.info(
                    f"{self.name}: flushing {tail.shape[0]} trailing audio samples on stop"
                )
                self._encode_audio_chunk(tail)

            if self.video_stream:
                for packet in self.video_stream.encode():
                    self.container.mux(packet)

            for stream in self.audio_streams:
                for packet in stream.encode():
                    self.container.mux(packet)

            if self.container:
                self.container.close()

            total_failures = self._mid_stream_hwpts_failures_video + self._mid_stream_hwpts_failures_audio
            logger.info(
                f"[av_sync_stop] {self.name}: anchor_mode={self._t0_anchor_mode} "
                f"pre_anchor_drops={self._pre_t0_drop_count} "
                f"video_encoded={self._video_encoded} audio_encoded={self._audio_encoded} "
                f"hwpts_failures={total_failures} gaps={self._gaps_detected} "
                f"max_drift_ms={self._max_drift_ms:.1f}"
            )
        except Exception as e:
            logger.error(f"{self.name}: Error stopping: {e}", exc_info=True)
        finally:
            self.is_running = False
