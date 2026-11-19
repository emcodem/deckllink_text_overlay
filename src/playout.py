"""DeckLink video/audio playout module."""

import logging
import threading
import queue
import time
from typing import Optional
import numpy as np
import cv2

import av

from .capture import Frame, AudioSample
from . import config as _config

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

                        # Output frame (+ closed captions in VANC when enabled)
                        if self.decklink:
                            self.decklink.put_video_frame(
                                frame_data,
                                frame.width,
                                frame.height,
                                anc_packets=self._caption_packets(frame),
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

    def _caption_packets(self, frame: Frame):
        """Closed-caption ANC packets for this output frame: [] = none, [AncPacket] = one CDP.

        Always returns a list, so the output is started with VANC enabled and captions can be
        switched on/off/608/708/line at runtime (GUI or config) without restarting playout.
        Subtitle changes (frame.subtitle, set by the overlay) become pop-on show/clear.
        """
        from .captions import state as cc_state
        want = bool(getattr(_config, 'CAPTIONS_ENABLED', True))
        want608 = want and bool(getattr(_config, 'CAPTIONS_608', True))
        want708 = want and bool(getattr(_config, 'CAPTIONS_708', True))
        line = getattr(_config, 'CAPTION_VANC_LINE', 9)
        if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= 1125:
            if getattr(self, '_cc_bad_line', None) != line:
                logger.error(f"CAPTION_VANC_LINE={line!r} is not a valid line number, using 9")
                self._cc_bad_line = line
            line = 9
        if not (want608 or want708):
            if getattr(self, '_cc_key', None) is not None:
                logger.info("Closed captions off")
            self._cc_key = None
            cc_state.update(state="off", reason="switched off", c608=False, c708=False, line=line)
            return []
        rate = frame.framerate
        if not rate or not rate[0] or not rate[1]:
            cc_state.update(state="idle", reason="waiting for frame rate")
            return []
        try:
            from .captions import CaptionInserter
            from .captions.vanc import AncPacket
            key = (tuple(rate), want608, want708)
            if getattr(self, '_cc_key', None) != key:
                self._cc = CaptionInserter(rate[0], rate[1], enable_608=want608, enable_708=want708)
                self._cc_key, self._cc_text, self._cc_frames = key, None, 0
                logger.info(f"Closed captions on: {rate[0]}/{rate[1]} fps, "
                            f"608={want608} 708={want708}, VANC line {line}")
            text = frame.subtitle
            if text is not None and text != self._cc_text:
                lines = [l for l in text.split('\n') if l.strip()]
                self._cc.show(lines) if lines else self._cc.clear()
                self._cc_text = text
            packet = AncPacket(self._cc.next_cdp(), line=line)
            self._cc_frames += 1
            if self._cc_frames % 5 == 1:     # status for the GUI, a few times per second
                fps = rate[0] / rate[1]
                cc_state.update(state="active", reason="", c608=want608, c708=want708, line=line,
                                fps=f"{fps:.2f}".rstrip('0').rstrip('.'), frames=self._cc_frames,
                                backlog608_s=(self._cc.backlog_608_frames / fps) if want608 else 0.0,
                                text=(self._cc_text or "").replace('\n', ' / '), error="")
            return [packet]
        except Exception as e:
            if not getattr(self, '_cc_error_logged', False):
                logger.error(f"Closed captions failed (video continues without): {e}", exc_info=True)
                self._cc_error_logged = True
            cc_state.update(state="error", error=str(e)[:200])
            return []

    def _convert_to_decklink_format(self, frame: Frame) -> bytes:
        """
        Convert frame to DeckLink-compatible format (UYVY).

        Args:
            frame: Input frame

        Returns:
            Raw bytes in UYVY format
        """
        if getattr(frame, 'uyvy', None) is not None:
            # Passthrough: the captured UYVY picture (with the subtitle blended in) goes out unchanged
            return frame.uyvy.tobytes()

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
        """RGB/BGR/YUV420P -> UYVY in BT.601 limited range (Y 16-235, C 16-240).

        Uses libswscale (PyAV), the exact inverse of the capture-side conversion; ~2x faster than
        the numpy/cv2 matrix version (8 ms vs 16 ms per 1080 frame). Replaces the full-range cv2
        BGR2YUV conversion, which produced illegal SDI levels (clipped whites, hard text edges).
        """
        if fmt == 'YUV420P':
            frame_data = cv2.cvtColor(frame_data, cv2.COLOR_YUV2RGB_I420)
            fmt = 'RGB24'
        src = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame_data, dtype=np.uint8),
                                         format='bgr24' if fmt == 'BGR24' else 'rgb24')
        plane = src.reformat(format='uyvy422').planes[0]
        w2 = src.width * 2
        if plane.line_size == w2:
            return bytes(plane)
        return np.frombuffer(plane, np.uint8).reshape(src.height, plane.line_size)[:, :w2].tobytes()
