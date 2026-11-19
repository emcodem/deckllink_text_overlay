"""DeckLink video/audio playout module."""

import logging
import threading
import queue
import time
from typing import Optional
import numpy as np
import cv2

from .capture import Frame, AudioSample
from . import config as _config

logger = logging.getLogger(__name__)

# BT.601 limited-range RGB -> YCbCr (Y 16-235, Cb/Cr 16-240), the exact inverse of the
# capture-side PyAV conversion. 3x4 matrix for cv2.transform (last column = offset).
_KR, _KB = 0.299, 0.114
_KG = 1.0 - _KR - _KB
_RGB_TO_YCC_601_LIMITED = np.hstack([
    np.array([
        [_KR, _KG, _KB],
        [-_KR / (2 * (1 - _KB)), -_KG / (2 * (1 - _KB)), 0.5],
        [0.5, -_KG / (2 * (1 - _KR)), -_KB / (2 * (1 - _KR))],
    ], np.float32) * np.array([219 / 255, 224 / 255, 224 / 255], np.float32)[:, None],
    np.array([[16], [128], [128]], np.float32),
])


class DeckLinkPlayout:
    """Outputs video and audio to a DeckLink device."""

    def __init__(self, device_index: int = 1):
        """
        Initialize DeckLink playout.

        Args:
            device_index: Index of DeckLink device to use (0 = first card)
        """
        self.device_index = device_index
        self.frame_queue = queue.Queue(maxsize=4)
        self.audio_queue = queue.Queue(maxsize=4)

        self.is_running = False
        self.playout_thread = None
        self.decklink = None

        self._frame_count = 0
        self._dropped_frames = 0
        self._start_time = None

        logger.info(f"DeckLink playout initialized (device {device_index})")

    def start(self):
        """Start playout to DeckLink device."""
        if self.is_running:
            logger.warning("Playout already running")
            return

        try:
            from .decklink_com import DeckLinkCOM

            # Initialize DeckLink device
            from . import config as _config
            self.decklink = DeckLinkCOM(self.device_index, _config.PLAYOUT_DISPLAY_MODE)
            logger.info(f"Using {self.decklink.get_device_name()} for playout")

            # Start playout thread
            self.is_running = True
            self._start_time = time.time()
            self.playout_thread = threading.Thread(target=self._playout_loop, daemon=True)
            self.playout_thread.start()
            logger.info("DeckLink playout started")

        except Exception as e:
            logger.error(f"Failed to start DeckLink playout: {e}", exc_info=True)
            self.is_running = False

    def stop(self):
        """Stop playout and clean up resources."""
        if not self.is_running:
            return

        self.is_running = False
        if self.playout_thread:
            self.playout_thread.join(timeout=2.0)

        logger.info(f"DeckLink playout stopped (dropped {self._dropped_frames} frames)")

    def put_frame(self, frame: Frame):
        """
        Queue a frame for playout.

        Args:
            frame: Frame to output
        """
        try:
            self.frame_queue.put(frame, block=False)
        except queue.Full:
            self._dropped_frames += 1
            if self._dropped_frames % 30 == 0:
                logger.warning(f"Playout queue full, dropped {self._dropped_frames} frames")

    def put_audio(self, audio: AudioSample):
        """
        Queue audio samples for playout.

        Args:
            audio: AudioSample to output
        """
        try:
            self.audio_queue.put(audio, block=False)
        except queue.Full:
            if self._dropped_frames % 30 == 0:
                logger.debug("Audio queue full")

    def _playout_loop(self):
        """Main playout thread loop."""
        try:
            logger.info("Playout thread started")

            while self.is_running:
                try:
                    # Get frame from queue (non-blocking to handle audio too)
                    try:
                        frame = self.frame_queue.get(block=False)
                        # Convert frame to DeckLink format (UYVY for YUV422)
                        frame_data = self._convert_to_decklink_format(frame)

                        # Output frame
                        if self.decklink:
                            self.decklink.put_video_frame(
                                frame_data,
                                frame.width,
                                frame.height
                            )

                        self._frame_count += 1
                        if self._frame_count % 100 == 0:
                            logger.debug(f"Playout frame {self._frame_count}")
                    except queue.Empty:
                        pass

                    # Also handle audio queue
                    try:
                        audio = self.audio_queue.get(block=False)
                        if self.decklink:
                            self.decklink.put_audio_samples(
                                audio.data,
                                audio.sample_rate,
                                audio.channels
                            )
                    except queue.Empty:
                        pass

                    # Sleep briefly to avoid busy-waiting
                    time.sleep(0.001)

                except Exception as e:
                    logger.debug(f"Playout loop error: {e}")

        except Exception as e:
            logger.error(f"Playout thread error: {e}", exc_info=True)
        finally:
            self.is_running = False
            logger.info("Playout thread ended")

    def _convert_to_decklink_format(self, frame: Frame) -> bytes:
        """
        Convert frame to DeckLink-compatible format (UYVY).

        Args:
            frame: Input frame

        Returns:
            Raw bytes in UYVY format
        """
        try:
            frame_data = frame.data
            h, w = frame_data.shape[:2]

            # Hardware capture delivers RGB pixels but labels the frame with the BMD format name
            # (e.g. 'bmdFormat8BitYUV'). Treating those as BGR swaps red and blue on SDI out.
            fmt = frame.format
            if fmt not in ('RGB24', 'BGR24', 'YUV420P') and getattr(_config, 'FIX_PLAYOUT_COLORS', True):
                fmt = 'RGB24'

            if getattr(_config, 'PLAYOUT_BROADCAST_RANGE', True):
                try:
                    return self._to_uyvy_broadcast_range(frame_data, fmt)
                except Exception as e:
                    logger.debug(f"Broadcast-range conversion failed, using legacy path: {e}")

            # Legacy path: full-range cv2 conversion, no chroma filtering
            # Convert to BGR if needed (OpenCV standard)
            if fmt == 'RGB24':
                bgr = cv2.cvtColor(frame_data, cv2.COLOR_RGB2BGR)
            elif fmt == 'BGR24':
                bgr = frame_data
            elif fmt == 'YUV420P':
                bgr = cv2.cvtColor(frame_data, cv2.COLOR_YUV2BGR_I420)
            else:
                bgr = frame_data

            # Convert BGR to YUV (OpenCV YUV is YCrCb)
            yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV)
            Y = yuv[:, :, 0]
            U = yuv[:, :, 1]
            V = yuv[:, :, 2]

            # Pack to UYVY: subsample U,V horizontally (4:2:2)
            # For each pair of pixels: [U, Y0, V, Y1]
            uyvy = np.zeros((h, w * 2), dtype=np.uint8)
            uyvy[:, 0::4] = U[:, 0::2]    # U from even columns
            uyvy[:, 1::4] = Y[:, 0::2]    # Y0 from even columns
            uyvy[:, 2::4] = V[:, 0::2]    # V from even columns
            uyvy[:, 3::4] = Y[:, 1::2]    # Y1 from odd columns

            return uyvy.tobytes()

        except Exception as e:
            logger.error(f"Error converting frame to UYVY: {e}")
            # Return dummy data
            return bytes(frame.width * frame.height * 2)

    @staticmethod
    def _to_uyvy_broadcast_range(frame_data: np.ndarray, fmt: str) -> bytes:
        """RGB/BGR/YUV420P -> UYVY in BT.601 limited range, chroma averaged per pixel pair.

        Replaces the full-range cv2 BGR2YUV conversion, which produced illegal SDI levels
        (clipped whites, hard text edges).
        """
        if fmt == 'BGR24':
            rgb = cv2.cvtColor(frame_data, cv2.COLOR_BGR2RGB)
        elif fmt == 'YUV420P':
            rgb = cv2.cvtColor(frame_data, cv2.COLOR_YUV2RGB_I420)
        else:
            rgb = frame_data
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
        h, w = rgb.shape[:2]
        ycc = cv2.transform(rgb, _RGB_TO_YCC_601_LIMITED)
        uyvy = np.empty((h, w * 2), np.uint8)
        uyvy[:, 1::2] = ycc[:, :, 0]
        chroma = ycc[:, :, 1:].astype(np.uint16)
        chroma = ((chroma[:, 0::2] + chroma[:, 1::2] + 1) >> 1).astype(np.uint8)
        uyvy[:, 0::4] = chroma[:, :, 0]
        uyvy[:, 2::4] = chroma[:, :, 1]
        return uyvy.tobytes()
