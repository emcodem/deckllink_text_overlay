"""DeckLink COM interfaces using comtypes (vtable-based COM)."""

import logging
import ctypes
from ctypes import POINTER, c_uint, c_int, c_uint32, c_long, c_void_p, byref, c_wchar_p
from comtypes import IUnknown, GUID, HRESULT, COMMETHOD
from comtypes.client import CreateObject
import numpy as np
import os

logger = logging.getLogger(__name__)

# ============================================================================
# BMD Constants
# ============================================================================

class BMDPixelFormat:
    bmdFormat8BitYUV = 0x32767579
    bmdFormat10BitYUV = 0x76323130
    bmdFormat8BitARGB = 0x20424741
    bmdFormat8BitBGRA = 0x61726762

class BMDDisplayMode:
    bmdModeHD1080i50 = 0x48693530
    bmdModeHD1080i5994 = 0x48693539
    bmdModeHD1080i6000 = 0x48693630
    bmdModeHD1080p25 = 0x48703235
    bmdModeHD1080p50 = 0x48703530
    bmdModeHD1080p5994 = 0x48703539
    bmdModeHD1080p6000 = 0x48703630
    bmdModeHD720p50 = 0x68703530
    bmdModeHD720p5994 = 0x68703539
    bmdModeHD720p60 = 0x68703630

class BMDVideoInputFlags:
    bmdVideoInputFlagDefault = 0
    bmdVideoInputEnableFormatDetection = 1

class BMDVideoOutputFlags:
    bmdVideoOutputFlagDefault = 0

class BMDAudioSampleRate:
    bmdAudioSampleRate48kHz = 48000

class BMDAudioSampleType:
    bmdAudioSampleType16bitInteger = 16
    bmdAudioSampleType32bitInteger = 32

class BMDAudioOutputStreamType:
    bmdAudioOutputStreamContinuous = 0

class BMDFrameFlags:
    bmdFrameFlagDefault = 0

# ============================================================================
# COM Interfaces
# ============================================================================

class IDeckLinkVideoBuffer(IUnknown):
    """Provides access to frame pixel data."""
    _iid_ = GUID("{CCB4B64A-5C86-4E02-B778-885D352709FE}")
    _methods_ = [
        COMMETHOD([], HRESULT, 'GetBytes',
                  (['out'], POINTER(c_void_p), 'buffer')),
    ]

class IDeckLinkAudioInputPacket(IUnknown):
    """Audio input packet."""
    _iid_ = GUID("{E43D5870-2894-11DE-8C30-0800200C9A66}")
    _methods_ = [
        COMMETHOD([], c_long, 'GetSampleFrameCount'),
        COMMETHOD([], HRESULT, 'GetBytes',
                  (['out'], POINTER(c_void_p), 'buffer')),
        COMMETHOD([], HRESULT, 'GetPacketTime',
                  (['out'], POINTER(c_uint32), 'packetTime'),
                  (['in'], c_uint32, 'timeScale')),
    ]

class IDeckLink(IUnknown):
    """DeckLink device."""
    _iid_ = GUID("{C418FBDD-0587-48ED-8FE5-640F0A14AF91}")
    _methods_ = [
        COMMETHOD([], HRESULT, 'GetModelName',
                  (['out'], POINTER(c_wchar_p), 'modelName')),
        COMMETHOD([], HRESULT, 'GetDisplayName',
                  (['out'], POINTER(c_wchar_p), 'displayName')),
    ]

class IDeckLinkIterator(IUnknown):
    """Device iterator."""
    _iid_ = GUID("{50FB36CD-3063-4B73-BDBB-958087F2D8BA}")

class IDeckLinkDisplayMode(IUnknown):
    """Display mode information."""
    _iid_ = GUID("{550D4B8C-F0F8-4B68-B87C-FBD2FC21A87C}")
    _methods_ = [
        COMMETHOD([], HRESULT, 'GetName',
                  (['out'], POINTER(c_wchar_p), 'name')),
        COMMETHOD([], c_long, 'GetWidth'),
        COMMETHOD([], c_long, 'GetHeight'),
        COMMETHOD([], HRESULT, 'GetFrameRate',
                  (['out'], POINTER(c_uint32), 'framerate_num'),
                  (['out'], POINTER(c_uint32), 'framerate_den')),
    ]

class IDeckLinkVideoFrame(IUnknown):
    """Base video frame."""
    _iid_ = GUID("{6502091C-615F-4F51-BAF6-45C4256DD5B0}")
    _methods_ = [
        COMMETHOD([], c_long, 'GetWidth'),
        COMMETHOD([], c_long, 'GetHeight'),
        COMMETHOD([], c_long, 'GetRowBytes'),
        COMMETHOD([], c_uint32, 'GetPixelFormat'),
        COMMETHOD([], c_uint32, 'GetFlags'),
    ]

class IDeckLinkMutableVideoFrame(IDeckLinkVideoFrame):
    """Mutable video frame for output."""
    _iid_ = GUID("{CF9EB134-0374-4C5B-95FA-1EC14819FF62}")
    _methods_ = [
        COMMETHOD([], HRESULT, 'SetFlags',
                  (['in'], c_uint32, 'newFlags')),
    ]

class IDeckLinkVideoInputFrame(IDeckLinkVideoFrame):
    """Video input frame."""
    _iid_ = GUID("{C9ADD3D2-BE52-488D-AB2D-7FDEF7AF0C95}")

class IDeckLinkInputCallback(IUnknown):
    """Input callback interface."""
    _iid_ = GUID("{3A94F075-C37D-4BA8-BCC0-1D778C8F881B}")

class IDeckLinkInput(IUnknown):
    """DeckLink input interface."""
    _iid_ = GUID("{4095DB82-E294-4B8C-AAA8-3B9E80C49336}")
    _methods_ = [
        COMMETHOD([], HRESULT, 'EnableVideoInput',
                  (['in'], c_uint32, 'displayMode'),
                  (['in'], c_uint32, 'pixelFormat'),
                  (['in'], c_uint32, 'flags')),
        COMMETHOD([], HRESULT, 'DisableVideoInput'),
        COMMETHOD([], HRESULT, 'EnableAudioInput',
                  (['in'], c_uint32, 'sampleRate'),
                  (['in'], c_uint32, 'sampleType'),
                  (['in'], c_uint, 'channelCount')),
        COMMETHOD([], HRESULT, 'DisableAudioInput'),
        COMMETHOD([], HRESULT, 'StartStreams'),
        COMMETHOD([], HRESULT, 'StopStreams'),
        COMMETHOD([], HRESULT, 'SetCallback',
                  (['in'], POINTER(IDeckLinkInputCallback), 'theCallback')),
    ]

class IDeckLinkOutput(IUnknown):
    """DeckLink output interface."""
    _iid_ = GUID("{1A8077F1-9FE2-4533-8147-2294305E253F}")
    _methods_ = [
        COMMETHOD([], HRESULT, 'EnableVideoOutput',
                  (['in'], c_uint32, 'displayMode'),
                  (['in'], c_uint32, 'flags')),
        COMMETHOD([], HRESULT, 'DisableVideoOutput'),
        COMMETHOD([], HRESULT, 'CreateVideoFrame',
                  (['in'], c_int, 'width'),
                  (['in'], c_int, 'height'),
                  (['in'], c_int, 'rowBytes'),
                  (['in'], c_uint32, 'pixelFormat'),
                  (['in'], c_uint32, 'flags'),
                  (['out'], POINTER(POINTER(IDeckLinkMutableVideoFrame)), 'outFrame')),
        COMMETHOD([], HRESULT, 'DisplayVideoFrameSync',
                  (['in'], POINTER(IDeckLinkVideoFrame), 'theFrame')),
        COMMETHOD([], HRESULT, 'EnableAudioOutput',
                  (['in'], c_uint32, 'sampleRate'),
                  (['in'], c_uint32, 'sampleType'),
                  (['in'], c_uint, 'channelCount'),
                  (['in'], c_uint32, 'streamType')),
        COMMETHOD([], HRESULT, 'DisableAudioOutput'),
        COMMETHOD([], HRESULT, 'WriteAudioSamplesSync',
                  (['in'], c_void_p, 'buffer'),
                  (['in'], c_uint, 'sampleFrameCount'),
                  (['out'], POINTER(c_uint), 'sampleFramesWritten')),
    ]

# ============================================================================
# Callback Implementation
# ============================================================================

class DeckLinkInputCallbackImpl:
    """Implements IDeckLinkInputCallback."""

    def __init__(self, frame_callback=None, audio_callback=None):
        self.frame_callback = frame_callback
        self.audio_callback = audio_callback

    def VideoInputFormatChanged(self, notificationEvents, newDisplayMode, detectedSignalFlags):
        """Called when input format changes."""
        logger.debug(f"Video input format changed (events: {notificationEvents})")
        return 0

    def VideoInputFrameArrived(self, videoFrame, audioPacket):
        """Called when a video frame arrives."""
        try:
            if videoFrame:
                width = videoFrame.GetWidth()
                height = videoFrame.GetHeight()
                row_bytes = videoFrame.GetRowBytes()
                pixel_format = videoFrame.GetPixelFormat()

                try:
                    video_buffer = videoFrame.QueryInterface(IDeckLinkVideoBuffer)
                    pixel_ptr = c_void_p()
                    video_buffer.GetBytes(byref(pixel_ptr))

                    if pixel_ptr.value:
                        data_size = height * row_bytes
                        frame_data = (ctypes.c_uint8 * data_size).from_address(pixel_ptr.value)
                        frame_bytes = bytes(frame_data)

                        if self.frame_callback:
                            self.frame_callback(frame_bytes, width, height, pixel_format, None, 0)
                except Exception as e:
                    logger.error(f"Error getting frame bytes: {e}")

            if audioPacket and self.audio_callback:
                try:
                    sample_count = audioPacket.GetSampleFrameCount()
                    audio_ptr = c_void_p()
                    audioPacket.GetBytes(byref(audio_ptr))

                    if audio_ptr.value and sample_count > 0:
                        data_size = sample_count * 2 * 2
                        audio_data = (ctypes.c_int16 * (data_size // 2)).from_address(audio_ptr.value)
                        audio_array = np.frombuffer(audio_data, dtype=np.int16).copy()
                        audio_array = audio_array.reshape(-1, 2)
                        self.audio_callback(audio_array, 48000, 2, 0)
                except Exception as e:
                    logger.debug(f"Error getting audio packet: {e}")

        except Exception as e:
            logger.error(f"Error in VideoInputFrameArrived: {e}")

        return 0

# ============================================================================
# Helper Functions
# ============================================================================

def get_device_by_index(index: int) -> tuple:
    """Get DeckLink device by index. Returns (IDeckLinkInput, IDeckLinkOutput)."""
    try:
        from comtypes.client import CreateObject, GetModule

        # Load the DeckLink type library from the DLL
        dll_path = r"C:\Program Files\Blackmagic Design\Blackmagic Desktop Video\DeckLinkAPI64.dll"
        logger.debug(f"Loading DeckLink type library from {dll_path}")
        decklink_module = GetModule(dll_path)

        # Create iterator using auto-generated CDeckLinkIterator CoClass
        logger.debug(f"Creating DeckLink iterator")
        iterator = CreateObject(decklink_module.CDeckLinkIterator)
        logger.info(f"Created DeckLink iterator successfully")

        # Enumerate to the desired device
        device = None
        for i in range(index + 1):
            try:
                device = iterator.Next()
                if device is None:
                    raise RuntimeError(f"Device index {index} not found (iterator returned None)")
                logger.debug(f"Got device at index {i}")
            except Exception as e:
                logger.error(f"Iterator.Next() failed at index {i}: {e}")
                raise RuntimeError(f"Device index {index} not found: {e}")

        if not device:
            raise RuntimeError(f"Device index {index} not found")

        # Get device name
        try:
            device_name = device.GetDisplayName()
            logger.info(f"Found DeckLink device {index}: {device_name}")
        except Exception as e:
            logger.warning(f"Could not get device name: {e}")

        # Query for input and output interfaces
        decklink_input = None
        decklink_output = None

        try:
            decklink_input = device.QueryInterface(decklink_module.IDeckLinkInput)
            logger.debug(f"Got IDeckLinkInput interface")
        except Exception as e:
            logger.error(f"Could not get IDeckLinkInput: {e}")

        try:
            decklink_output = device.QueryInterface(decklink_module.IDeckLinkOutput)
            logger.debug(f"Got IDeckLinkOutput interface")
        except Exception as e:
            logger.debug(f"Could not get IDeckLinkOutput: {e}")

        if not decklink_input:
            raise RuntimeError(f"Device {index} does not support IDeckLinkInput")

        return (decklink_input, decklink_output)

    except Exception as e:
        logger.error(f"Failed to get device {index}: {e}")
        raise RuntimeError(f"DeckLink device {index} not found: {e}")
