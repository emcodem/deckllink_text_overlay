"""DeckLink COM wrapper using comtypes (auto-generated from type library)."""

import logging
from typing import Optional, Callable
import numpy as np
import ctypes
from ctypes import byref, c_void_p, c_uint32, c_uint
from comtypes.client import GetModule, CreateObject

logger = logging.getLogger(__name__)

# Load the DeckLink type library
try:
    dll_path = r"C:\Program Files\Blackmagic Design\Blackmagic Desktop Video\DeckLinkAPI64.dll"
    decklink_module = GetModule(dll_path)
    HAS_COMTYPES = True
except Exception as e:
    logger.error(f"Failed to load DeckLink type library: {e}")
    HAS_COMTYPES = False
    decklink_module = None


# Create a simple namespace for pixel format constants for backward compatibility
class BMDPixelFormat:
    """Pixel format constants."""
    bmdFormat8BitYUV = decklink_module.bmdFormat8BitYUV if decklink_module else 0x32767579
    bmdFormat10BitYUV = decklink_module.bmdFormat10BitYUV if decklink_module else 0x76323130
    bmdFormat8BitARGB = decklink_module.bmdFormat8BitARGB if decklink_module else 0x20424741
    bmdFormat8BitBGRA = decklink_module.bmdFormat8BitBGRA if decklink_module else 0x61726762

    # Expose all constants from decklink_module that look like format names
    if decklink_module:
        @classmethod
        def __getattr__(cls, name):
            if name.startswith('bmdFormat'):
                return getattr(decklink_module, name, None)
            raise AttributeError(f"No attribute {name}")

    # For backward compatibility, expose as __members__
    if decklink_module:
        __members__ = {name: getattr(decklink_module, name) for name in dir(decklink_module)
                       if name.startswith('bmdFormat')}

TIMESCALE = 10_000_000  # DeckLink hardware reference clock: 10 MHz (100 ns resolution)

# Import helper function
try:
    from .decklink_comtypes import get_device_by_index
except ImportError:
    def get_device_by_index(index):
        raise NotImplementedError("decklink_comtypes not available")


def _create_input_callback(frame_callback=None, audio_callback=None, audio_channels=2, decklink_input=None):
    """Create a COM-backed IDeckLinkInputCallback implementation.

    Returns a COM object that can be passed to DeckLink's SetCallback().
    """
    from comtypes import COMObject

    # None until VideoInputFormatChanged fires with the real detected format.
    # Frames are held back until then so no frame ever carries a guessed framerate.
    _framerate = [None]
    _current_mode = [None]   # last confirmed mode constant; guards against restart loops
    _video_hw_pts_fail = [0]  # graduated failure counter for GetHardwareReferenceTimestamp
    _audio_hw_pts_fail = [0]  # graduated failure counter for GetPacketTime
    # Diagnostic counters: reset on each VideoInputFormatChanged so we log the
    # first 20 video frames and 20 audio packets after every format change.
    _diag_frames = [0]
    _diag_audio = [0]

    class _CallbackImpl(COMObject):
        _com_interfaces_ = [decklink_module.IDeckLinkInputCallback]

        def VideoInputFormatChanged(self, notificationEvents, newDisplayMode, detectedSignalFlags):
            """Called when the DeckLink detects a new input signal format."""
            try:
                try:
                    mode_constant = newDisplayMode.GetDisplayMode()
                except Exception:
                    mode_constant = None

                # Guard: restarting streams re-triggers this callback with the same mode.
                # Only act when the format actually changed.
                if mode_constant is not None and mode_constant == _current_mode[0]:
                    return 0

                try:
                    mode_name = newDisplayMode.GetName()
                except Exception:
                    mode_name = "unknown"

                try:
                    width = newDisplayMode.GetWidth()
                    height = newDisplayMode.GetHeight()
                except Exception:
                    width = height = 0

                fps_str = "unknown"
                try:
                    raw = newDisplayMode.GetFrameRate()
                    logger.info(f"  GetFrameRate raw  : {raw}")
                    # comtypes returns (frameDuration, timeScale); fps = timeScale / frameDuration
                    fps_num, fps_den = int(raw[1]), int(raw[0])
                    _framerate[0] = (fps_num, fps_den)
                    fps_str = f"{fps_num}/{fps_den}"
                except Exception as e:
                    logger.error(f"  GetFrameRate failed: {e}")

                progressive = bool(detectedSignalFlags & getattr(decklink_module, 'bmdDetectedVideoInputProgressive', 0x08))
                scan = "progressive" if progressive else "interlaced"

                logger.info("=== DeckLink Signal Detected ===")
                logger.info(f"  Display mode : {mode_name}" + (f" ({mode_constant:#010x})" if mode_constant else ""))
                logger.info(f"  Resolution   : {width}x{height}")
                logger.info(f"  Frame rate   : {fps_str} fps")
                logger.info(f"  Scan type    : {scan}")
                logger.info(f"  Signal flags : {detectedSignalFlags:#010x}")
                logger.info(f"  Events       : {notificationEvents:#010x}")
                logger.info("================================")

                # Update current mode before restarting to block the re-trigger.
                _current_mode[0] = mode_constant
                # Reset diagnostic counters so the next 20 frames/packets are logged.
                _diag_frames[0] = 0
                _diag_audio[0] = 0

                # Required by DeckLink SDK: restart streams with the detected format.
                if decklink_input is not None and mode_constant is not None:
                    try:
                        decklink_input.PauseStreams()
                        decklink_input.EnableVideoInput(
                            mode_constant,
                            decklink_module.bmdFormat8BitYUV,
                            decklink_module.bmdVideoInputEnableFormatDetection
                        )
                        decklink_input.FlushStreams()
                        decklink_input.StartStreams()
                        logger.info("  Streams restarted with detected format")
                    except Exception as e:
                        logger.error(f"  Failed to restart streams: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"Error in VideoInputFormatChanged: {e}", exc_info=True)
            return 0

        def VideoInputFrameArrived(self, videoFrame, audioPacket):
            """Called when a video/audio frame arrives."""
            try:
                if _framerate[0] is None:
                    # Hold all frames until VideoInputFormatChanged has fired and
                    # given us a verified framerate from the actual signal.
                    return 0

                if videoFrame and frame_callback:
                    try:
                        width = videoFrame.GetWidth()
                        height = videoFrame.GetHeight()
                        row_bytes = videoFrame.GetRowBytes()
                        pixel_format = videoFrame.GetPixelFormat()

                        # Stream-time timestamp — same clock domain as audio GetPacketTime,
                        # resets to 0 on each StartStreams(). Used for PTS and gap detection.
                        video_hw_pts = 0
                        video_hw_pts_duration = 0
                        video_hw_pts_valid = False
                        try:
                            raw_st = videoFrame.GetStreamTime(TIMESCALE)
                            if isinstance(raw_st, (tuple, list)):
                                video_hw_pts = int(raw_st[0])
                                video_hw_pts_duration = int(raw_st[1])
                            else:
                                video_hw_pts = int(raw_st)
                            video_hw_pts_valid = True
                        except Exception as e:
                            _video_hw_pts_fail[0] += 1
                            count = _video_hw_pts_fail[0]
                            if count in (1, 10, 100, 1000) or (count > 1000 and count % 1000 == 0):
                                level = logging.ERROR if count >= 1000 else logging.WARNING
                                logger.log(level,
                                    f"[av_sync_warn] GetStreamTime failed (#{count}): {e}"
                                )

                        if _diag_frames[0] < 20:
                            _diag_frames[0] += 1
                            logger.info(
                                f"[pts_diag] video frame #{_diag_frames[0]}: "
                                f"stream_time={video_hw_pts} duration={video_hw_pts_duration} "
                                f"valid={video_hw_pts_valid}"
                            )

                        # Get the frame buffer directly via IDeckLinkVideoFrame.GetBytes()
                        buffer_ptr = videoFrame.GetBytes()

                        if buffer_ptr:
                            # Copy frame data to bytes
                            data_size = height * row_bytes
                            frame_data = (ctypes.c_uint8 * data_size).from_address(buffer_ptr)
                            frame_bytes = bytes(frame_data)

                            frame_callback(frame_bytes, width, height, pixel_format, _framerate[0], 0, row_bytes,
                                           video_hw_pts, TIMESCALE, video_hw_pts_valid)
                    except Exception as e:
                        logger.error(f"Error processing video frame: {e}")

                if audioPacket and audio_callback:
                    try:
                        sample_count = audioPacket.GetSampleFrameCount()
                        # GetBytes() returns a raw buffer pointer (single int address),
                        # not a tuple — mirror the video path's ctypes.from_address pattern.
                        buffer_ptr = audioPacket.GetBytes()

                        # Audio stream-time timestamp — same clock domain as video GetStreamTime,
                        # resets to 0 on each StartStreams(). packet_time=0 is valid (first packet).
                        audio_hw_pts = 0
                        audio_hw_pts_valid = False
                        try:
                            raw_pkt = audioPacket.GetPacketTime(TIMESCALE)
                            if isinstance(raw_pkt, (tuple, list)):
                                audio_hw_pts = int(raw_pkt[0])
                            else:
                                audio_hw_pts = int(raw_pkt)
                            audio_hw_pts_valid = True
                        except Exception as e:
                            _audio_hw_pts_fail[0] += 1
                            count = _audio_hw_pts_fail[0]
                            if count in (1, 10, 100, 1000) or (count > 1000 and count % 1000 == 0):
                                level = logging.ERROR if count >= 1000 else logging.WARNING
                                logger.log(level,
                                    f"[av_sync_warn] GetPacketTime failed (#{count}): {e}"
                                )

                        if _diag_audio[0] < 20:
                            _diag_audio[0] += 1
                            logger.info(
                                f"[pts_diag] audio packet #{_diag_audio[0]}: "
                                f"packet_time={audio_hw_pts} valid={audio_hw_pts_valid}"
                            )

                        if buffer_ptr and sample_count > 0:
                            data_size = sample_count * audio_channels * 2  # int16 samples
                            raw = (ctypes.c_uint8 * data_size).from_address(buffer_ptr)
                            audio_bytes = bytes(raw)
                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).copy()

                            try:
                                audio_array = audio_array.reshape(-1, audio_channels)
                                audio_callback(audio_array, 48000, audio_channels, 0,
                                               audio_hw_pts, TIMESCALE, audio_hw_pts_valid)
                            except ValueError as e:
                                logger.warning(f"Audio reshape error: expected {sample_count * audio_channels} samples, got {len(audio_array)}: {e}")
                    except Exception as e:
                        logger.error(f"Error processing audio packet: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"Error in VideoInputFrameArrived: {e}")

            return 0

    return _CallbackImpl()


class DeckLinkInputCallbackImpl:
    """Plain Python callback wrapper (created by _create_input_callback for COM use)."""
    def __init__(self, frame_callback=None, audio_callback=None, audio_channels=2, decklink_input=None):
        self.frame_callback = frame_callback
        self.audio_callback = audio_callback
        self.audio_channels = audio_channels
        # Create the actual COM object
        self._com_obj = _create_input_callback(frame_callback, audio_callback, audio_channels, decklink_input)

    def QueryInterface(self, iid):
        """COM QueryInterface - delegate to the COM object."""
        return self._com_obj.QueryInterface(iid)


# Alias for backward compatibility with capture.py
DeckLinkInputCallback = DeckLinkInputCallbackImpl



class DeckLinkCOM:
    """DeckLink COM wrapper for capture and playout."""

    def __init__(self, device_index: int = 0, display_mode: int = None):
        """
        Initialize DeckLink device.

        Args:
            device_index: Device index (0 = first card)
            display_mode: Display mode (e.g., 0x48693530 for bmdModeHD1080i50)
        """
        if not HAS_COMTYPES or not decklink_module:
            raise RuntimeError("comtypes and DeckLink type library are required. Check installation.")

        self.device_index = device_index
        self.display_mode = display_mode or decklink_module.bmdModeHD1080i50

        # COM interfaces
        self.decklink_input = None
        self.decklink_output = None

        # Callback object (must keep reference to prevent GC)
        self._callback_obj = None
        self._input_started = False
        self._output_started = False
        self._audio_output_started = False

        self._init_decklink()

    def _init_decklink(self):
        """Initialize DeckLink device interfaces."""
        try:
            logger.debug(f"Initializing DeckLink device {self.device_index}...")
            self.decklink_input, self.decklink_output = get_device_by_index(self.device_index)
            logger.info(f"DeckLink device {self.device_index} initialized")
        except Exception as e:
            logger.error(f"Failed to initialize DeckLink device {self.device_index}: {e}")
            raise

    def get_device_name(self) -> str:
        """Get display name of the device."""
        if not self.decklink_input:
            return "Unknown"
        try:
            # The input interface should have GetDisplayName or similar
            # Try to get the name through the interface
            name = self.decklink_input.GetDisplayName()
            return name if name else "DeckLink Device"
        except:
            return "DeckLink Device"

    def set_callback(self, callback_obj):
        """Store callback object for later use in start_streams().

        Args:
            callback_obj: DeckLinkInputCallbackImpl object
        """
        self._pending_callback = callback_obj

    def start_streams(self, frame_callback: Callable = None, audio_callback: Callable = None):
        """Start video/audio input streams.

        Args:
            frame_callback: Called when frame arrives: callback(frame_data, width, height, pixel_format, framerate, frame_number)
            audio_callback: Called when audio arrives: callback(audio_data, sample_rate, channels, timestamp)
        """
        if not self.decklink_input:
            logger.warning("No input device available")
            return

        # Use pending callback if no explicit callbacks provided
        audio_channels = 2  # default
        if not frame_callback and not audio_callback and hasattr(self, '_pending_callback'):
            pending = self._pending_callback
            frame_callback = getattr(pending, 'frame_callback', None)
            audio_callback = getattr(pending, 'audio_callback', None)
            audio_channels = getattr(pending, 'audio_channels', 2)

        try:
            # Enable video input with format detection
            hr = self.decklink_input.EnableVideoInput(
                self.display_mode,
                decklink_module.bmdFormat8BitYUV,  # UYVY format
                decklink_module.bmdVideoInputEnableFormatDetection
            )
            if hr != 0:
                raise RuntimeError(f"EnableVideoInput failed: {hr}")
            logger.debug("Video input enabled")

            # Enable audio input
            hr = self.decklink_input.EnableAudioInput(
                decklink_module.bmdAudioSampleRate48kHz,
                decklink_module.bmdAudioSampleType16bitInteger,
                audio_channels
            )
            if hr != 0:
                logger.warning(f"EnableAudioInput failed: {hr}")
            else:
                logger.info(f"Audio input enabled ({audio_channels} channels)")

            # Create and set callback (if callbacks provided)
            if frame_callback or audio_callback:
                self._callback_obj = DeckLinkInputCallbackImpl(frame_callback, audio_callback, audio_channels, self.decklink_input)
                # Pass the COM object, not the wrapper
                hr = self.decklink_input.SetCallback(self._callback_obj._com_obj)
                if hr != 0:
                    logger.warning(f"SetCallback failed: {hr}")
                else:
                    logger.debug("Callback set")

            # Start streams
            hr = self.decklink_input.StartStreams()
            if hr != 0:
                raise RuntimeError(f"StartStreams failed: {hr}")

            self._input_started = True
            logger.info("Input streams started")

        except Exception as e:
            logger.error(f"Failed to start input streams: {e}")
            raise

    def stop_streams(self):
        """Stop video/audio input streams."""
        if not self.decklink_input or not self._input_started:
            return

        try:
            self.decklink_input.StopStreams()
            self.decklink_input.DisableVideoInput()
            self.decklink_input.DisableAudioInput()
            self._input_started = False
            logger.info("Input streams stopped")
        except Exception as e:
            logger.error(f"Error stopping streams: {e}")

    def put_video_frame(self, frame_data: bytes, width: int, height: int) -> bool:
        """Send a video frame to the output.

        Args:
            frame_data: Raw frame bytes in UYVY format
            width: Frame width in pixels
            height: Frame height in pixels

        Returns:
            True if successful
        """
        if not self.decklink_output:
            logger.warning("No output device available")
            return False

        try:
            # Enable output on first frame
            if not self._output_started:
                hr = self.decklink_output.EnableVideoOutput(
                    self.display_mode,
                    decklink_module.bmdVideoOutputFlagDefault
                )
                if hr != 0:
                    logger.warning(f"EnableVideoOutput failed: {hr}")
                    return False
                self._output_started = True
                logger.debug("Video output enabled")

            # Create frame (auto-generated method returns frame directly)
            row_bytes = width * 2  # UYVY is 2 bytes per pixel

            mutable_frame = self.decklink_output.CreateVideoFrame(
                width, height, row_bytes,
                decklink_module.bmdFormat8BitYUV,
                decklink_module.bmdFrameFlagDefault
            )

            if not mutable_frame:
                logger.warning("CreateVideoFrame returned null")
                return False

            logger.debug(f"Created video frame")

            # Access frame buffer and copy data
            try:
                video_buffer = mutable_frame.QueryInterface(decklink_module.IDeckLinkVideoBuffer)

                # Start access for writing
                hr = video_buffer.StartAccess(decklink_module.bmdBufferAccessWrite)
                if hr != 0:
                    logger.warning(f"StartAccess failed: {hr}")
                    return False

                # Get buffer pointer
                buffer_ptr = video_buffer.GetBytes()
                if not buffer_ptr:
                    logger.warning("GetBytes returned null pointer")
                    return False

                # Copy frame data
                ctypes.memmove(buffer_ptr, frame_data, len(frame_data))
                logger.debug(f"Copied {len(frame_data)} bytes to frame buffer")

                # End access
                hr = video_buffer.EndAccess(decklink_module.bmdBufferAccessWrite)
                if hr != 0:
                    logger.warning(f"EndAccess failed: {hr}")
                    return False

            except Exception as e:
                logger.error(f"Error accessing frame buffer: {e}")
                return False

            # Display frame
            hr = self.decklink_output.DisplayVideoFrameSync(mutable_frame)
            if hr != 0:
                logger.warning(f"DisplayVideoFrameSync failed: {hr}")
                return False

            return True

        except Exception as e:
            logger.error(f"Error putting video frame: {e}")
            return False

    def put_audio_samples(self, audio_data: np.ndarray, sample_rate: int, channels: int) -> bool:
        """Send audio samples to the output.

        Args:
            audio_data: Audio samples as numpy array (int16)
            sample_rate: Sample rate in Hz (should be 48000)
            channels: Number of channels (usually 2 for stereo)

        Returns:
            True if successful
        """
        if not self.decklink_output:
            return False

        try:
            # Enable audio output on first call
            if not self._audio_output_started:
                hr = self.decklink_output.EnableAudioOutput(
                    decklink_module.bmdAudioSampleRate48kHz,
                    decklink_module.bmdAudioSampleType16bitInteger,
                    channels,
                    decklink_module.bmdAudioOutputStreamContinuous,
                )
                if hr != 0:
                    logger.warning(f"EnableAudioOutput failed: {hr}")
                    return False
                self._audio_output_started = True
                logger.debug("Audio output enabled")

            # Convert to contiguous bytes
            if not audio_data.flags['C_CONTIGUOUS']:
                audio_data = np.ascontiguousarray(audio_data)

            sample_count = audio_data.shape[0]
            buffer_ptr = audio_data.ctypes.data_as(c_void_p)

            # comtypes returns 'out' params as the return value — don't pass byref manually
            samples_written = self.decklink_output.WriteAudioSamplesSync(
                buffer_ptr,
                sample_count,
            )

            if samples_written < sample_count:
                logger.debug(f"Audio: wrote {samples_written}/{sample_count} samples")

            return True

        except Exception as e:
            logger.error(f"Error putting audio samples: {e}")
            return False

    def get_status(self) -> dict:
        """Get device status information."""
        return {
            'device_index': self.device_index,
            'device_name': self.get_device_name(),
            'has_input': self.decklink_input is not None,
            'has_output': self.decklink_output is not None,
            'input_started': self._input_started,
            'output_started': self._output_started,
        }
