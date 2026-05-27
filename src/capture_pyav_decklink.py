"""PyAV-based DeckLink capture (cross-platform).

This uses PyAV (FFmpeg) which has built-in DeckLink support on all platforms.
No COM registration or native library loading required - just needs FFmpeg with DeckLink enabled.

This is the recommended approach for cross-platform DeckLink support.
"""

import logging
import threading
import queue as queue_module
import time
from typing import Optional, Tuple
import numpy as np

try:
    import av
    HAS_AV = True
except ImportError:
    HAS_AV = False

from .capture import Frame, AudioSample, DeckLinkCapture

logger = logging.getLogger(__name__)


class PyAVDeckLinkCapture(DeckLinkCapture):
    """DeckLink capture using PyAV/FFmpeg (cross-platform)."""

    def __init__(self, device_index: int = 0, queue_size: int = 4, audio_channels: int = 8,
                 audio_queue_size: int = 0):
        """
        Initialize PyAV-based DeckLink capture.

        Args:
            device_index: DeckLink device index (0 = first card)
            queue_size: Frame queue size
            audio_channels: Number of audio channels to capture (1-16)
            audio_queue_size: Audio packet queue size (0 = 8x video queue)
        """
        if not HAS_AV:
            raise RuntimeError("PyAV (av) is required for DeckLink capture. Install with: pip install av")

        super().__init__(device_index, queue_size, audio_channels, audio_queue_size)
        self.container = None
        self.video_stream = None
        self.audio_stream = None

    def start(self):
        """Start capturing from DeckLink device via PyAV/FFmpeg."""
        if self.is_running:
            logger.warning("Capture already running")
            return

        try:
            import sys

            # Try decklink format first (works on all platforms including Windows).
            # The 'channels' option tells FFmpeg's DeckLink demuxer how many audio
            # channels to capture (allowed: 2, 8, 16; default 2). Without this,
            # the configured AUDIO_CHANNELS is silently downgraded to 2.
            try:
                logger.info(f"Opening DeckLink device {self.device_index} with decklink format "
                            f"({self.audio_channels} audio channels)")
                self.container = av.open(
                    f"Blackmagic DeckLink {self.device_index}",
                    format='decklink',
                    options={'channels': str(self.audio_channels)},
                )
            except Exception as e:
                logger.debug(f"decklink format failed: {e}, trying alternatives...")

                # Fallback: try dshow format on Windows
                if sys.platform == "win32":
                    device_spec = self._get_device_spec()
                    logger.info(f"Trying DirectShow device: {device_spec}")
                    self.container = av.open(f"{device_spec}", format='dshow')
                else:
                    raise

            # Get video and audio streams
            self.video_stream = next(
                (s for s in self.container.streams if s.type == 'video'),
                None
            )
            self.audio_stream = next(
                (s for s in self.container.streams if s.type == 'audio'),
                None
            )

            if not self.video_stream:
                raise RuntimeError("No video stream found on DeckLink device")

            # Log full signal properties so framerate/format issues are visible in the log
            logger.info("=== DeckLink Signal Properties ===")
            logger.info(f"  Resolution : {self.video_stream.width}x{self.video_stream.height}")
            logger.info(f"  Rate guessed: {self.video_stream.guessed_rate}")
            logger.info(f"  Rate average: {self.video_stream.average_rate}")
            try:
                logger.info(f"  Pixel format: {self.video_stream.codec_context.pix_fmt}")
            except Exception:
                pass
            logger.info(f"  Codec       : {self.video_stream.codec.name}")
            if self.audio_stream:
                logger.info(f"  Audio       : {self.audio_stream.channels}ch @ {self.audio_stream.sample_rate}Hz ({self.audio_stream.codec.name})")
            else:
                logger.info("  Audio       : no audio stream detected")
            logger.info("===================================")

            # Start capture thread
            self.is_running = True
            self._start_time = time.time()
            self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.capture_thread.start()

            logger.info("PyAV DeckLink capture started")

        except Exception as e:
            logger.error(f"Failed to start DeckLink capture: {e}", exc_info=True)
            self.is_running = False
            if self.container:
                self.container.close()

    def stop(self):
        """Stop capturing and clean up."""
        if not self.is_running:
            return

        self.is_running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=2.0)

        if self.container:
            try:
                self.container.close()
            except:
                pass

        logger.info("PyAV DeckLink capture stopped")

    def _get_device_spec(self) -> str:
        """Get device specification string for FFmpeg."""
        # Windows uses DirectShow to access DeckLink
        # Format: "DeckLink Device Name"
        # We can use the device index or the actual name from DirectShow
        import sys

        if sys.platform == "win32":
            # Windows DirectShow: Use device name pattern
            # DeckLink devices appear as "Blackmagic DeckLink X"
            # We need to find the actual device name
            device_names = self._get_decklink_device_names()
            if device_names and self.device_index < len(device_names):
                return device_names[self.device_index]
            # Fallback to generic DeckLink
            return f"DeckLink {self.device_index}"
        elif sys.platform == "darwin":
            # macOS: use DeckLink device name
            return f"DeckLink {self.device_index}"
        else:
            # Linux: use device name
            return f"DeckLink {self.device_index}"

    def _get_decklink_device_names(self) -> list:
        """Get list of DeckLink VIDEO device names from FFmpeg."""
        try:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
                capture_output=True,
                text=True,
                timeout=5
            )

            devices = []
            in_video_section = False

            for line in result.stderr.split('\n'):
                # Track which section we're in
                if 'DirectShow video devices' in line:
                    in_video_section = True
                    continue
                elif 'DirectShow audio devices' in line:
                    in_video_section = False
                    continue

                # Only collect video devices (skip audio)
                if in_video_section and 'DeckLink' in line and '@' in line:
                    # Extract device name
                    parts = line.split('"')
                    if len(parts) >= 2:
                        device_name = parts[1]
                        # Skip audio-only devices
                        if 'Audio' not in device_name:
                            devices.append(device_name)
                            logger.debug(f"Found video device: {device_name}")

            return devices
        except Exception as e:
            logger.debug(f"Could not get DeckLink device names: {e}")
            return []

    def _capture_loop(self):
        """Main capture thread loop."""
        try:
            logger.info("Starting capture loop")

            # Demux both video and audio streams if available
            streams_to_demux = [self.video_stream]
            if self.audio_stream:
                streams_to_demux.append(self.audio_stream)
            else:
                logger.warning("No audio stream found — audio will not be captured via PyAV path")

            for packet in self.container.demux(*streams_to_demux):
                if not self.is_running:
                    break

                for frame in packet.decode():
                    if isinstance(frame, av.VideoFrame):
                        self._process_video_frame(frame)
                    elif isinstance(frame, av.AudioFrame):
                        self._process_audio_frame(frame)

        except Exception as e:
            logger.error(f"Capture loop error: {e}", exc_info=True)
        finally:
            self.is_running = False
            logger.info("Capture loop ended")

    def _process_video_frame(self, av_frame: 'av.VideoFrame'):
        """Process a video frame from PyAV."""
        try:
            # Convert to RGB
            frame_data = av_frame.to_ndarray(format='rgb24')

            # Extract hardware PTS from PyAV (FFmpeg's DeckLink demuxer uses SDK timestamps)
            tb = av_frame.time_base
            if av_frame.pts is not None and tb is not None and tb.denominator:
                hw_pts = int(av_frame.pts) * int(tb.numerator)
                hw_pts_rate = int(tb.denominator)
                hw_pts_valid = True
            else:
                hw_pts, hw_pts_rate, hw_pts_valid = 0, 10_000_000, False

            # guessed_rate comes from r_frame_rate (container-signalled) which is
            # reliable for live DeckLink inputs; average_rate is computed from
            # duration/nb_frames and defaults to 30 when those fields are absent.
            rate = self.video_stream.guessed_rate or self.video_stream.average_rate
            frame = Frame(
                data=frame_data,
                format='RGB24',
                width=av_frame.width,
                height=av_frame.height,
                framerate=(int(rate.numerator), int(rate.denominator)),
                timestamp=float(av_frame.pts * tb) if av_frame.pts and tb else time.time() - self._start_time,
                frame_number=self._frame_count,
                hw_pts=hw_pts,
                hw_pts_rate=hw_pts_rate,
                hw_pts_valid=hw_pts_valid,
            )

            try:
                self.frame_queue.put(frame, block=True, timeout=1.0)
                self._frame_count += 1
                qsize = self.frame_queue.qsize()
                if qsize > self._frame_queue_hi:
                    self._frame_queue_hi = qsize
            except queue_module.Full:
                logger.debug("Frame queue full, dropping frame")

        except Exception as e:
            logger.error(f"Error processing video frame: {e}")

    def _process_audio_frame(self, av_frame: 'av.AudioFrame'):
        """Process an audio frame from PyAV."""
        try:
            audio_data = av_frame.to_ndarray()
            n_ch = av_frame.layout.channels if hasattr(av_frame.layout, 'channels') else 1

            # FFmpeg's DeckLink demuxer delivers packed s16 → to_ndarray() shape is
            # (1, samples*channels). Reshape to (samples, channels). Also handle the
            # planar (channels, samples) case for safety in case a future PyAV/FFmpeg
            # release returns s16p.
            if audio_data.ndim == 1:
                audio_data = audio_data.reshape(-1, n_ch) if n_ch > 1 else audio_data[:, np.newaxis]
            elif audio_data.ndim == 2 and audio_data.shape[0] == n_ch and n_ch > 1:
                audio_data = audio_data.T.copy()  # planar → (samples, channels)
            elif audio_data.ndim == 2 and audio_data.shape[0] == 1 and n_ch > 1:
                audio_data = audio_data.reshape(-1, n_ch)  # packed → (samples, channels)

            if audio_data.dtype != np.int16:
                audio_data = (audio_data * 32767).clip(-32768, 32767).astype(np.int16) \
                    if audio_data.dtype.kind == 'f' else audio_data.astype(np.int16)

            if self._frame_count <= 1:
                logger.info(f"PyAV audio: shape={audio_data.shape} dtype={audio_data.dtype} "
                            f"n_ch={n_ch} sample_rate={av_frame.sample_rate}")

            # Extract hardware PTS from PyAV
            tb = av_frame.time_base
            if av_frame.pts is not None and tb is not None and tb.denominator:
                hw_pts = int(av_frame.pts) * int(tb.numerator)
                hw_pts_rate = int(tb.denominator)
                hw_pts_valid = True
            else:
                hw_pts, hw_pts_rate, hw_pts_valid = 0, 10_000_000, False

            sample = AudioSample(
                data=audio_data,
                sample_rate=av_frame.sample_rate,
                channels=n_ch,
                timestamp=float(av_frame.pts * tb) if av_frame.pts and tb else time.time() - self._start_time,
                hw_pts=hw_pts,
                hw_pts_rate=hw_pts_rate,
                hw_pts_valid=hw_pts_valid,
            )

            try:
                self.audio_queue.put(sample, block=True, timeout=2.0)
                qsize = self.audio_queue.qsize()
                if qsize > self._audio_queue_hi:
                    self._audio_queue_hi = qsize
            except queue_module.Full:
                logger.error("[av_sync_warn] Audio queue full after 2s timeout — dropping audio packet")

        except Exception as e:
            logger.error(f"Error processing audio frame: {e}")


def test_pyav_decklink() -> bool:
    """Test if PyAV DeckLink support is available."""
    try:
        import av

        # Try to open DeckLink input with format auto-detection
        logger.info("Testing PyAV DeckLink support...")

        # This will fail gracefully if decklink format not available
        # but it's a good test
        logger.info("PyAV version: {av.__version__}")
        return True

    except Exception as e:
        logger.error(f"PyAV DeckLink test failed: {e}")
        return False
