"""DeckLink video/audio capture module."""

import logging
import threading
import queue
import numpy as np
import time
import os
import av
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """Represents a video frame with metadata."""
    data: np.ndarray           # Frame pixel data (H x W x C)
    format: str                # 'YUV420P', 'RGB24', etc.
    width: int
    height: int
    framerate: Tuple[int, int] # (num, den) e.g. (30000, 1001) for 29.97fps
    timestamp: float           # Seconds since capture started (wall-clock, kept for logging)
    frame_number: int
    hw_pts: int = 0            # Hardware reference clock ticks (DeckLink or PyAV demuxer)
    hw_pts_rate: int = 10_000_000  # Ticks per second (DeckLink native = 10 MHz)
    hw_pts_valid: bool = False # False when no hardware clock available


@dataclass
class AudioSample:
    """Represents an audio sample block."""
    data: np.ndarray           # Audio samples (samples x channels)
    sample_rate: int
    channels: int
    timestamp: float           # Seconds since capture started (wall-clock, kept for logging)
    hw_pts: int = 0            # Hardware reference clock ticks
    hw_pts_rate: int = 10_000_000  # Ticks per second
    hw_pts_valid: bool = False # False when no hardware clock available


class DeckLinkCapture:
    """Captures video and audio from a DeckLink device."""

    def __init__(self, device_index: int = 0, queue_size: int = 4, audio_channels: int = 8,
                 audio_queue_size: int = 0):
        """
        Initialize DeckLink capture.

        Args:
            device_index: Index of DeckLink device to use (0 = first card)
            queue_size: Maximum video frames to buffer
            audio_channels: Number of audio channels to capture (1-16, default 8)
            audio_queue_size: Maximum audio packets to buffer (0 = 8x video queue)
        """
        self.device_index = device_index
        self.audio_channels = max(1, min(audio_channels, 16))  # Clamp 1-16
        self.frame_queue = queue.Queue(maxsize=queue_size)
        effective_audio_size = audio_queue_size if audio_queue_size > 0 else queue_size * 8
        self.audio_queue = queue.Queue(maxsize=effective_audio_size)

        self.is_running = False
        self.capture_thread = None
        self.decklink = None
        self.callback = None

        self._frame_count = 0
        self._start_time = None
        self._frame_queue_hi: int = 0
        self._audio_queue_hi: int = 0

        logger.info(f"DeckLink capture initialized (device {device_index}, {self.audio_channels} audio channels)")

    def start(self):
        """Start capturing from DeckLink device."""
        if self.is_running:
            logger.warning("Capture already running")
            return

        logger.warning("DeckLinkCapture.start() is abstract - should use SimulatedCapture or real DeckLink subclass")
        self.is_running = False

    def stop(self):
        """Stop capturing and clean up resources."""
        if not self.is_running:
            return

        try:
            if self.decklink:
                self.decklink.stop_streams()
        except Exception as e:
            logger.error(f"Error stopping streams: {e}")

        self.is_running = False
        logger.info("DeckLink capture stopped")

    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        """
        Get next captured frame.

        Args:
            timeout: Maximum seconds to wait for a frame

        Returns:
            Frame object or None if timeout
        """
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_audio(self, timeout: float = 1.0) -> Optional[AudioSample]:
        """
        Get next audio sample block.

        Args:
            timeout: Maximum seconds to wait for audio

        Returns:
            AudioSample object or None if timeout
        """
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_queue_high_water(self) -> tuple:
        """Return (frame_queue_max_seen, audio_queue_max_seen) since capture started."""
        return (self._frame_queue_hi, self._audio_queue_hi)

    def _on_frame(self, frame_data: bytes, width: int, height: int, pixel_format: int,
                 framerate: Tuple[int, int], flags: int, row_bytes: int = None,
                 hw_pts: int = 0, hw_pts_rate: int = 10_000_000, hw_pts_valid: bool = None):
        """Called when a new frame arrives from DeckLink."""
        try:
            if self._frame_count == 0:
                from .decklink_com import BMDPixelFormat
                format_name = self._get_format_name(pixel_format)
                logger.info(f"===== FIRST FRAME DEBUG =====")
                logger.info(f"Pixel format: {pixel_format:#x} ({format_name})")
                logger.info(f"Is bmdFormat8BitYUV? {pixel_format == BMDPixelFormat.bmdFormat8BitYUV} (const={BMDPixelFormat.bmdFormat8BitYUV:#x})")
                logger.info(f"Width: {width}, Height: {height}")
                logger.info(f"Row bytes: {row_bytes}")
                logger.info(f"Calculated stride (width*2): {width*2}")
                logger.info(f"Frame data length: {len(frame_data)}")
                logger.info(f"Expected size (height * width * 2): {height * width * 2}")
                logger.info(f"Expected size (height * row_bytes): {height * row_bytes if row_bytes else 'N/A'}")
                logger.info(f"============================")
            # Convert DeckLink frame data to NumPy array
            frame_array = self._convert_frame_data(frame_data, width, height, pixel_format, row_bytes)

            if frame_array is None:
                logger.error(f"Frame conversion returned None! format={pixel_format:#x}, data_len={len(frame_data) if frame_data else 0}")
                return

            frame = Frame(
                data=frame_array,
                format=self._get_format_name(pixel_format),
                width=width,
                height=height,
                framerate=framerate,
                timestamp=time.time() - self._start_time,
                frame_number=self._frame_count,
                hw_pts=hw_pts,
                hw_pts_rate=hw_pts_rate,
                hw_pts_valid=hw_pts_valid if hw_pts_valid is not None else (hw_pts != 0),
            )

            try:
                self.frame_queue.put(frame, block=True, timeout=1.0)
                self._frame_count += 1
                qsize = self.frame_queue.qsize()
                if qsize > self._frame_queue_hi:
                    self._frame_queue_hi = qsize
                if self._frame_count % 30 == 0:
                    logger.debug(f"Frame {self._frame_count} captured successfully")
            except queue.Full:
                logger.warning("Frame queue full after 1s timeout, dropping frame")

        except Exception as e:
            logger.error(f"Error processing frame: {e}", exc_info=True)

    def _on_audio(self, audio_array: np.ndarray, sample_rate: int, channels: int, timestamp: float,
                  hw_pts: int = 0, hw_pts_rate: int = 10_000_000, hw_pts_valid: bool = None):
        """Called when audio samples arrive from DeckLink.

        The COM callback delivers an already-reshaped (samples, channels) int16 array
        along with sample_rate, channel count, and hardware PTS.
        """
        try:
            if self._frame_count == 1:
                logger.info(f"_on_audio callback: shape={audio_array.shape}, sample_rate={sample_rate}, channels={channels}")

            sample = AudioSample(
                data=audio_array,
                sample_rate=sample_rate,
                channels=channels,
                timestamp=time.time() - self._start_time,
                hw_pts=hw_pts,
                hw_pts_rate=hw_pts_rate,
                hw_pts_valid=hw_pts_valid if hw_pts_valid is not None else (hw_pts != 0),
            )

            try:
                self.audio_queue.put(sample, block=True, timeout=2.0)
                if self._frame_count == 1:
                    logger.info(f"First audio sample queued: shape={audio_array.shape}")
                qsize = self.audio_queue.qsize()
                if qsize > self._audio_queue_hi:
                    self._audio_queue_hi = qsize
            except queue.Full:
                logger.error("[av_sync_warn] Audio queue full after 2s timeout — dropping audio packet")

        except Exception as e:
            logger.error(f"Error processing audio: {e}", exc_info=True)

    def _convert_frame_data(self, frame_data: bytes, width: int, height: int,
                           pixel_format: int, row_bytes: int = None) -> np.ndarray:
        """
        Convert DeckLink frame data to NumPy array.

        Args:
            frame_data: Raw frame bytes from DeckLink
            width: Frame width
            height: Frame height
            pixel_format: DeckLink pixel format constant
            row_bytes: Actual row stride (may include padding)

        Returns:
            NumPy array with frame data
        """
        try:
            from .decklink_com import BMDPixelFormat
            import cv2

            # Convert based on pixel format
            if pixel_format == BMDPixelFormat.bmdFormat8BitYUV:
                # UYVY 4:2:2 → RGB24 via PyAV (libswscale through Cython bindings).
                av_frame = av.VideoFrame(width, height, 'uyvy422')
                plane = av_frame.planes[0]
                line_size = plane.line_size

                input_stride = row_bytes if row_bytes else width * 2
                if input_stride == line_size and len(frame_data) == plane.buffer_size:
                    plane.update(frame_data)
                else:
                    # Stride mismatch — repack row-by-row into the plane's expected linesize.
                    src = np.frombuffer(frame_data, dtype=np.uint8).reshape((height, input_stride))
                    if line_size == width * 2:
                        plane.update(src[:, :width * 2].tobytes())
                    else:
                        padded = np.zeros((height, line_size), dtype=np.uint8)
                        padded[:, :width * 2] = src[:, :width * 2]
                        plane.update(padded.tobytes())

                rgb = av_frame.reformat(format='rgb24').to_ndarray()

                if self._frame_count == 1:
                    logger.info(f"===== CONVERSION RESULT =====")
                    logger.info(f"Output RGB shape: {rgb.shape}")
                    logger.info(f"First RGB pixel: R={rgb[0,0,0]} G={rgb[0,0,1]} B={rgb[0,0,2]}")
                    logger.info(f"Sample RGB row (first 5 pixels):\n{rgb[0, :5, :]}")
                    logger.info(f"Frame min/max RGB values: min={rgb.min()}, max={rgb.max()}")
                    logger.info(f"==============================")

                return rgb

            elif pixel_format == BMDPixelFormat.bmdFormat8BitARGB:
                # ARGB format (A R G B)
                frame_array = np.frombuffer(frame_data, dtype=np.uint8).reshape((height, width, 4))
                # Extract RGB channels (skip alpha at index 0)
                rgb = frame_array[:, :, 1:4]  # R=1, G=2, B=3
                return rgb

            elif pixel_format == BMDPixelFormat.bmdFormat8BitBGRA:
                # BGRA format (B G R A)
                frame_array = np.frombuffer(frame_data, dtype=np.uint8).reshape((height, width, 4))
                # Convert BGRA to RGB: extract BGR and reverse to RGB
                bgr = frame_array[:, :, :3]
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                return rgb

            else:
                logger.warning(f"Unsupported pixel format: {pixel_format:#x}")
                # Fallback: assume UYVY
                frame_array = np.frombuffer(frame_data, dtype=np.uint8)
                frame_array = frame_array.reshape((height, width * 2))
                return frame_array

        except Exception as e:
            logger.error(f"Error converting frame data: {e}, pixel_format={pixel_format:#x}, size={len(frame_data)}, expected={height*width*2}", exc_info=True)
            # Return dummy array
            return np.zeros((height, width, 3), dtype=np.uint8)

    def _get_format_name(self, pixel_format: int) -> str:
        """Get human-readable format name."""
        try:
            from .decklink_com import BMDPixelFormat
            for name, value in BMDPixelFormat.__members__.items():
                if value == pixel_format:
                    return name
        except:
            pass
        return "UNKNOWN"


class RealDeckLinkCapture(DeckLinkCapture):
    """Real DeckLink hardware capture."""

    def start(self):
        """Start capturing from real DeckLink device."""
        if self.is_running:
            logger.warning("Capture already running")
            return

        try:
            from .decklink_com import DeckLinkCOM, DeckLinkInputCallback

            # Initialize DeckLink device
            self.decklink = DeckLinkCOM(self.device_index)
            logger.info(f"Using {self.decklink.get_device_name()}")

            # Create callback handler
            self.callback = DeckLinkInputCallback(
                frame_callback=self._on_frame,
                audio_callback=self._on_audio,
                audio_channels=self.audio_channels
            )

            # Set callback and start streams
            self.decklink.set_callback(self.callback)
            self.decklink.start_streams()

            self.is_running = True
            self._start_time = time.time()

            logger.info("Real DeckLink capture started")

        except Exception as e:
            logger.error(f"Failed to start DeckLink capture: {e}", exc_info=True)
            self.is_running = False


class SimulatedCapture(DeckLinkCapture):
    """Simulated capture using a video file for testing without hardware."""

    def __init__(self, input_file: str, device_index: int = 0, queue_size: int = 4,
                 audio_channels: int = 8, audio_queue_size: int = 0):
        """
        Initialize simulated capture from video file.

        Args:
            input_file: Path to video file to use as input
            device_index: Ignored, for API compatibility
            queue_size: Maximum frames to buffer
            audio_channels: Number of audio channels to simulate
            audio_queue_size: Maximum audio packets to buffer (0 = 8x video queue)
        """
        super().__init__(device_index, queue_size, audio_channels, audio_queue_size)
        self.input_file = input_file
        self.container = None
        self.stream = None
        self.audio_stream = None

        logger.info(f"Simulated capture initialized from {input_file}")

    def start(self):
        """Start simulated capture from video file."""
        if self.is_running:
            logger.warning("Capture already running")
            return

        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        logger.info("Simulated capture started")

    def _capture_loop(self):
        """Simulate capture by reading frames (and audio) from video file."""
        try:
            import av

            self.container = av.open(self.input_file)
            self.stream = self.container.streams.video[0]
            self.audio_stream = next(
                (s for s in self.container.streams if s.type == 'audio'), None
            )

            logger.info(f"Opened {self.input_file}")
            logger.info(f"Video: {self.stream.width}x{self.stream.height}, "
                        f"framerate: {self.stream.average_rate}")
            if self.audio_stream:
                logger.info(f"Audio: {self.audio_stream.channels}ch @ {self.audio_stream.sample_rate}Hz")
            else:
                logger.warning(f"No audio stream in {self.input_file} — TSFileOutput init will stall")

            streams_to_demux = [self.stream]
            if self.audio_stream:
                streams_to_demux.append(self.audio_stream)

            for packet in self.container.demux(*streams_to_demux):
                if not self.is_running:
                    break

                for av_frame in packet.decode():
                    if not self.is_running:
                        break

                    if isinstance(av_frame, av.VideoFrame):
                        self._process_sim_video_frame(av_frame)
                    elif isinstance(av_frame, av.AudioFrame):
                        self._process_sim_audio_frame(av_frame)

        except Exception as e:
            logger.error(f"Simulated capture error: {e}", exc_info=True)
        finally:
            if self.container:
                self.container.close()
            self.is_running = False

    def _process_sim_video_frame(self, av_frame):
        """Process a decoded video frame from the simulated file source."""
        try:
            tb = self.stream.time_base
            if av_frame.pts is not None and tb and tb.denominator:
                hw_pts = int(av_frame.pts) * int(tb.numerator)
                hw_pts_rate = int(tb.denominator)
                hw_pts_valid = True
            else:
                hw_pts, hw_pts_rate, hw_pts_valid = 0, 10_000_000, False

            rate = self.stream.average_rate or self.stream.guessed_rate
            frame = Frame(
                data=av_frame.to_ndarray(format='rgb24'),
                format='RGB24',
                width=av_frame.width,
                height=av_frame.height,
                framerate=(int(rate.numerator), int(rate.denominator)),
                timestamp=float(av_frame.pts * tb) if av_frame.pts and tb else 0.0,
                frame_number=self._frame_count,
                hw_pts=hw_pts,
                hw_pts_rate=hw_pts_rate,
                hw_pts_valid=hw_pts_valid,
            )

            try:
                self.frame_queue.put(frame, block=True, timeout=1.0)
                self._frame_count += 1
                if self._frame_count % 30 == 0:
                    logger.debug(f"Sim frame {self._frame_count} captured")
            except queue.Full:
                logger.debug("Sim frame queue full, dropping frame")

        except Exception as e:
            logger.error(f"Sim video frame error: {e}", exc_info=True)

    def _process_sim_audio_frame(self, av_frame):
        """Process a decoded audio frame from the simulated file source."""
        try:
            if not self.audio_stream:
                return

            tb = self.audio_stream.time_base
            if av_frame.pts is not None and tb and tb.denominator:
                hw_pts = int(av_frame.pts) * int(tb.numerator)
                hw_pts_rate = int(tb.denominator)
                hw_pts_valid = True
            else:
                hw_pts, hw_pts_rate, hw_pts_valid = 0, 10_000_000, False

            # to_ndarray() returns (channels, samples) for planar or (1, channels*samples) for packed
            audio_data = av_frame.to_ndarray()
            n_ch = av_frame.layout.channels if hasattr(av_frame.layout, 'channels') else 1
            if audio_data.ndim == 2 and audio_data.shape[0] == n_ch:
                audio_data = audio_data.T.copy()  # (channels, samples) → (samples, channels)
            elif audio_data.ndim == 2 and audio_data.shape[0] == 1 and n_ch > 1:
                audio_data = audio_data.reshape(-1, n_ch)  # packed → (samples, channels)

            # Ensure int16
            if audio_data.dtype != np.int16:
                audio_data = (audio_data * 32767).clip(-32768, 32767).astype(np.int16) \
                    if audio_data.dtype.kind == 'f' else audio_data.astype(np.int16)

            sample = AudioSample(
                data=audio_data,
                sample_rate=av_frame.sample_rate,
                channels=n_ch,
                timestamp=float(av_frame.pts * tb) if av_frame.pts and tb else 0.0,
                hw_pts=hw_pts,
                hw_pts_rate=hw_pts_rate,
                hw_pts_valid=hw_pts_valid,
            )

            try:
                self.audio_queue.put(sample, block=True, timeout=2.0)
            except queue.Full:
                logger.debug("Sim audio queue full, dropping packet")

        except Exception as e:
            logger.error(f"Sim audio frame error: {e}", exc_info=True)
