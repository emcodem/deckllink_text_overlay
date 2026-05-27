"""DeckLink video/audio playout module."""

import logging
import threading
import queue
import time
from typing import Optional
import numpy as np
import cv2

from .capture import Frame, AudioSample

logger = logging.getLogger(__name__)


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

            # Convert to BGR if needed (OpenCV standard)
            if frame.format == 'RGB24':
                bgr = cv2.cvtColor(frame_data, cv2.COLOR_RGB2BGR)
            elif frame.format == 'BGR24':
                bgr = frame_data
            elif frame.format == 'YUV420P':
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
