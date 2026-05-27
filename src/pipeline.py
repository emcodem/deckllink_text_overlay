"""Main processing pipeline orchestration."""

import logging
import queue
import threading
import time
from typing import Optional

from . import config
from . import config_subtitles
from .capture import Frame, DeckLinkCapture, RealDeckLinkCapture, SimulatedCapture
from .capture_pyav_decklink import PyAVDeckLinkCapture
from .overlay import TextOverlay
from .playout import DeckLinkPlayout
from .gui import create_gui
from .outputs import BaseOutput, UDPMonoOutput, TSFileOutput

logger = logging.getLogger(__name__)


class Pipeline:
    """Main processing pipeline for video capture, overlay, and playout."""

    def __init__(self, outputs: list = None):
        """
        Initialize the processing pipeline.

        Args:
            outputs: List of BaseOutput instances for encoding/streaming
        """
        self.is_running = False
        self.start_time = None
        self.outputs = outputs or []

        # Initialize components
        self._init_capture()
        self._init_overlay()
        self._init_playout()
        self._init_gui()
        self._init_outputs()

        # Processing thread
        self.processing_thread = None

        # Statistics
        self.stats = {
            'frames_captured': 0,
            'frames_processed': 0,
            'frames_displayed': 0,
            'frames_output': 0,
            'dropped_frames': 0,
        }

        logger.info("Pipeline initialized")

    def _init_capture(self):
        """Initialize capture component."""
        if config.SIMULATE_HARDWARE:
            if config.SIMULATION_INPUT_FILE:
                self.capture = SimulatedCapture(
                    config.SIMULATION_INPUT_FILE,
                    config.CAPTURE_DEVICE_INDEX,
                    config.CAPTURE_QUEUE_SIZE,
                    config.AUDIO_CHANNELS,
                    audio_queue_size=config.AUDIO_QUEUE_SIZE,
                )
                logger.info("Using simulated capture from video file")
            else:
                raise ValueError(
                    "SIMULATE_HARDWARE=True but SIMULATION_INPUT_FILE not set"
                )
        else:
            # Try PyAV DeckLink first (cross-platform, no COM needed)
            try:
                self.capture = PyAVDeckLinkCapture(
                    config.CAPTURE_DEVICE_INDEX,
                    config.CAPTURE_QUEUE_SIZE,
                    config.AUDIO_CHANNELS,
                    audio_queue_size=config.AUDIO_QUEUE_SIZE,
                )
                logger.info("Using PyAV DeckLink capture (cross-platform)")
            except Exception as e:
                logger.warning(f"PyAV DeckLink not available: {e}")
                logger.info("Falling back to real DeckLink capture (Windows COM)")
                self.capture = RealDeckLinkCapture(
                    config.CAPTURE_DEVICE_INDEX,
                    config.CAPTURE_QUEUE_SIZE,
                    config.AUDIO_CHANNELS,
                    audio_queue_size=config.AUDIO_QUEUE_SIZE,
                )

    def _init_overlay(self):
        """Initialize text overlay component."""
        self.overlay = TextOverlay(config_subtitles)

    def _init_playout(self):
        """Initialize playout component."""
        if config.SKIP_PLAYOUT or config.SIMULATE_HARDWARE:
            self.playout = None
            logger.info("Playout disabled (simulation mode or config)")
        else:
            self.playout = DeckLinkPlayout(config.PLAYOUT_DEVICE_INDEX)

    def _init_gui(self):
        """Initialize GUI component."""
        if config.SKIP_GUI:
            self.gui = None
            self.gui_queue = None
            self.gui_audio_queue = None
            logger.info("GUI disabled via config")
        else:
            logger.info("Initializing GUI...")
            self.gui_queue = queue.Queue(maxsize=2)
            self.gui_audio_queue = queue.Queue(maxsize=200)
            self.gui = create_gui(
                self.gui_queue,
                config.GUI_UPDATE_RATE,
                audio_queue=self.gui_audio_queue,
            )
            if self.gui is None:
                logger.warning("GUI could not be created - continuing without GUI")
            else:
                logger.info(f"GUI initialized successfully: {self.gui}")

    def _init_outputs(self):
        """Initialize encoding output components."""
        if not self.outputs:
            logger.info("No encoding outputs configured")
        else:
            logger.info(f"Initialized {len(self.outputs)} encoding output(s)")

    def start(self):
        """Start the processing pipeline."""
        if self.is_running:
            logger.warning("Pipeline already running")
            return

        logger.info("Starting pipeline...")
        self.is_running = True
        self.start_time = time.time()

        # Start components
        self.capture.start()

        # If capture failed to start and it's PyAV, try RealDeckLinkCapture fallback
        if not self.capture.is_running and isinstance(self.capture, PyAVDeckLinkCapture):
            logger.warning("PyAV DeckLink capture failed to start, falling back to RealDeckLinkCapture...")
            try:
                self.capture = RealDeckLinkCapture(
                    config.CAPTURE_DEVICE_INDEX,
                    config.CAPTURE_QUEUE_SIZE,
                    config.AUDIO_CHANNELS,
                    audio_queue_size=config.AUDIO_QUEUE_SIZE,
                )
                self.capture.start()
                logger.info("RealDeckLinkCapture started successfully")
            except Exception as e:
                logger.error(f"RealDeckLinkCapture also failed: {e}", exc_info=True)
                raise

        if self.playout:
            self.playout.start()

        # Start outputs
        for output in self.outputs:
            try:
                output.start()
            except Exception as e:
                logger.error(f"Failed to start output {output.name}: {e}")

        # Start processing thread
        self.processing_thread = threading.Thread(
            target=self._process_loop,
            daemon=True
        )
        self.processing_thread.start()

        logger.info("Pipeline started")

    def stop(self):
        """Stop the processing pipeline."""
        if not self.is_running:
            return

        logger.info("Stopping pipeline...")
        self.is_running = False

        # Stop components
        self.capture.stop()
        if self.playout:
            self.playout.stop()

        # Stop outputs
        for output in self.outputs:
            try:
                output.stop()
            except Exception as e:
                logger.error(f"Failed to stop output {output.name}: {e}")

        # Wait for processing thread
        if self.processing_thread:
            self.processing_thread.join(timeout=2.0)

        # Close GUI
        if self.gui:
            try:
                self.gui.close()
            except Exception:
                pass

        self._log_stats()
        logger.info("Pipeline stopped")

    def _dispatch_audio(self, audio):
        """Forward a single audio packet to all outputs (called once per packet)."""
        for output in self.outputs:
            try:
                output.push(None, audio)
            except Exception as e:
                logger.error(f"Error sending audio to output {output.name}: {e}")

    def _dispatch_video(self, frame):
        """Forward a single video frame to all outputs."""
        for output in self.outputs:
            try:
                output.push(frame, None)
            except Exception as e:
                logger.error(f"Error sending video to output {output.name}: {e}")

    def _drain_audio(self):
        """Drain all pending audio packets from the capture queue, dispatching each once."""
        while True:
            audio = self.capture.get_audio(timeout=0)
            if audio is None:
                break
            if self.stats['frames_captured'] == 1:
                logger.info(f"Audio drained: shape={audio.data.shape}, channels={audio.channels}, "
                            f"hw_pts_valid={audio.hw_pts_valid}")
            self._dispatch_audio(audio)
            # Feed GUI audio preview (non-blocking — drop on full)
            if self.gui_audio_queue is not None:
                try:
                    self.gui_audio_queue.put_nowait(audio)
                except queue.Full:
                    pass

    def _process_loop(self):
        """Main processing loop (runs in separate thread).

        Audio and video are dispatched independently — each sample is forwarded
        exactly once.  No audio packet is ever reused across multiple video frames.
        """
        try:
            logger.info("Processing loop started")
            while self.is_running:
                # 1. Drain all pending audio before blocking on video
                self._drain_audio()

                # 2. Wait for a video frame
                frame = self.capture.get_frame(timeout=1.0)
                if frame is None:
                    continue

                self.stats['frames_captured'] += 1

                # 3. Drain audio that arrived while we were waiting for the video frame
                self._drain_audio()

                if self.stats['frames_captured'] == 1:
                    logger.info(f"First video frame: {frame.width}x{frame.height}, "
                                f"hw_pts_valid={frame.hw_pts_valid}, hw_pts={frame.hw_pts}")

                # 4. Apply text overlay
                if not config.SKIP_OVERLAY:
                    try:
                        frame = self.overlay.apply(frame)
                        self.stats['frames_processed'] += 1
                    except Exception as e:
                        logger.error(f"Error applying overlay: {e}")
                        continue
                else:
                    self.stats['frames_processed'] += 1

                # 5. Send to GUI (skip if behind)
                if self.gui and self.gui_queue:
                    try:
                        self.gui_queue.put(frame, block=False)
                        self.stats['frames_displayed'] += 1
                    except queue.Full:
                        if config.ENABLE_FRAME_DROPPING:
                            logger.debug("GUI queue full, skipping frame")

                # 6. Send to playout
                if self.playout and not config.SKIP_PLAYOUT:
                    try:
                        self.playout.put_frame(frame)
                        self.stats['frames_output'] += 1
                    except Exception as e:
                        logger.error(f"Error sending to playout: {e}")

                # 7. Forward video to encoding outputs
                if self.outputs:
                    if self.stats['frames_captured'] % 100 == 1:
                        logger.debug(f"Sending video frame {self.stats['frames_captured']} to "
                                     f"{len(self.outputs)} output(s)")
                    self._dispatch_video(frame)

                # Log progress every 100 frames
                if self.stats['frames_captured'] % 100 == 0:
                    elapsed = time.time() - self.start_time
                    fps = self.stats['frames_captured'] / elapsed
                    logger.info(
                        f"Progress: {self.stats['frames_captured']} frames "
                        f"({fps:.1f} fps), dropped: {self.stats['dropped_frames']}"
                    )

        except Exception as e:
            logger.error(f"Processing loop error: {e}", exc_info=True)
        finally:
            logger.info("Processing loop ended")

    def _log_stats(self):
        """Log pipeline statistics."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        logger.info("=== Pipeline Statistics ===")
        logger.info(f"Total runtime: {elapsed:.1f} seconds")
        logger.info(f"Frames captured: {self.stats['frames_captured']}")
        logger.info(f"Frames processed: {self.stats['frames_processed']}")
        logger.info(f"Frames displayed: {self.stats['frames_displayed']}")
        logger.info(f"Frames output: {self.stats['frames_output']}")
        logger.info(f"Dropped frames: {self.stats['dropped_frames']}")
        if elapsed > 0:
            logger.info(f"Average FPS: {self.stats['frames_captured'] / elapsed:.1f}")
