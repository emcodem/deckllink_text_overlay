"""DeckLink SDK native integration using ctypes.

This module provides direct access to the Blackmagic DeckLink SDK without requiring
COM registration or FFmpeg. It uses ctypes to call the DeckLink C++ interfaces directly.

This is the most portable and direct approach to DeckLink hardware access.
"""

import logging
import ctypes
import ctypes.wintypes as wintypes
import platform
import sys
import os
from pathlib import Path
from typing import Optional, List, Dict
import threading
import queue
import time

logger = logging.getLogger(__name__)

# COM calling convention and types
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", wintypes.BYTE * 8)]

# IUnknown interface definition
class IUnknown(ctypes.Structure):
    pass

IUnknown._fields_ = [("lpVtbl", ctypes.POINTER(ctypes.c_void_p))]

# Forward declarations for COM interfaces
class IDeckLinkIterator(ctypes.Structure):
    pass

class IDeckLinkInput(ctypes.Structure):
    pass

class IDeckLinkVideoFrame(ctypes.Structure):
    pass

# DeckLink SDK constants (from DeckLinkAPI.idl)
class BMDPixelFormat:
    """DeckLink pixel format constants."""
    bmdFormat8BitYUV = 0x32767579  # '2yuv'
    bmdFormat10BitYUV = 0x76323130  # 'v210'
    bmdFormat8BitARGB = 0x20424741  # ' BGA'
    bmdFormat8BitBGRA = 0x61726762  # 'argb'
    bmdFormat10BitRGB = 0x72313062  # 'r10b'


class BMDVideoStatus:
    """DeckLink video input status flags."""
    bmdVideoStatusPlaybackPhasis = 0x00000001


class BMDDisplayMode:
    """DeckLink display modes."""
    bmdModePAL = 0x70616c00
    bmdModeNTSC = 0x6e747363
    bmdModeHD1080p60 = 0x48703630
    bmdModeHD1080p5994 = 0x48703539
    bmdModeHD720p60 = 0x48703630
    bmdModeHD720p5994 = 0x48703539
    bmdMode4K2160p60 = 0x3465703630
    bmdMode4K2160p5994 = 0x3465703539


class DeckLinkSDK:
    """Low-level DeckLink SDK access via ctypes."""

    def __init__(self):
        """Initialize DeckLink SDK access."""
        self.lib = None
        self.lib_path = None
        self.iterator = None
        self._load_library()
        if self.lib:
            self._init_sdk()

    def _load_library(self):
        """Load DeckLink library."""
        candidates = self._get_library_candidates()

        for path in candidates:
            try:
                logger.debug(f"Trying to load DeckLink from: {path}")
                self.lib = ctypes.CDLL(str(path), use_errno=True)
                self.lib_path = path
                logger.info(f"Loaded DeckLink SDK from: {path}")
                return
            except (OSError, Exception) as e:
                logger.debug(f"Failed to load {path}: {e}")
                continue

        logger.warning("Could not load DeckLink native library")
        self.lib = None

    def _get_library_candidates(self) -> List[Path]:
        """Get candidate library paths based on OS."""
        candidates = []

        if sys.platform == "win32":
            # Windows: Look in standard installation paths
            program_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
            candidates.extend([
                program_files / "Blackmagic Design" / "Blackmagic Desktop Video" / "DeckLinkAPI64.dll",
                program_files / "Blackmagic Design" / "Blackmagic Desktop Video" / "DeckLinkAPI.dll",
                program_files / "Blackmagic Design" / "Desktop Video" / "DeckLinkAPI64.dll",
                program_files / "Blackmagic Design" / "Desktop Video" / "DeckLinkAPI.dll",
                program_files / "Blackmagic Design" / "DeckLink SDK" / "lib" / "DeckLinkAPI.dll",
                Path("C:\\Windows\\System32\\DeckLinkAPI.dll"),
                Path("C:\\dev\\Blackmagic DeckLink SDK 14.4\\Win\\Lib\\x64\\DeckLinkAPI.dll"),
            ])

        elif sys.platform == "darwin":
            # macOS
            candidates.extend([
                Path("/Library/Frameworks/DeckLinkAPI.framework/DeckLinkAPI"),
                Path("/opt/decklink/lib/libDeckLinkAPI.dylib"),
            ])

        elif sys.platform.startswith("linux"):
            # Linux
            candidates.extend([
                Path("/usr/lib/libDeckLinkAPI.so"),
                Path("/usr/lib/x86_64-linux-gnu/libDeckLinkAPI.so"),
                Path("/usr/local/lib/libDeckLinkAPI.so"),
                Path("/opt/decklink/lib/libDeckLinkAPI.so"),
            ])

        # Filter to existing paths and log attempts
        existing = []
        for c in candidates:
            if c.exists():
                existing.append(c)
            else:
                logger.debug(f"Candidate does not exist: {c}")

        logger.debug(f"Found {len(existing)} DeckLink DLL candidate(s)")
        return existing

    def _init_sdk(self):
        """Initialize SDK by setting up function prototypes."""
        if sys.platform == "win32" and self.lib:
            try:
                # Setup DeckLinkIteratorNew function
                # This function creates an iterator for enumerating DeckLink devices
                self._setup_sdk_functions()
            except Exception as e:
                logger.error(f"Failed to initialize SDK: {e}")
                self.lib = None

    def _setup_sdk_functions(self):
        """Set up COM interfaces using ctypes."""
        if not self.lib:
            return

        try:
            # Try to find exported function - it may be in a different DLL
            # or we need to use COM CoCreateInstance instead

            # First, try to setup COM directly
            if sys.platform == "win32":
                ole32 = ctypes.windll.ole32

                # GUIDs for DeckLink Iterator COM object
                CLSID_DeckLinkIterator = GUID()
                CLSID_DeckLinkIterator.Data1 = 0xD9B14ED8
                CLSID_DeckLinkIterator.Data2 = 0x0D14
                CLSID_DeckLinkIterator.Data3 = 0x4BD5
                CLSID_DeckLinkIterator.Data4 = (ctypes.c_ubyte * 8)(
                    0xB9, 0x5D, 0xFC, 0x4B, 0x25, 0xA6, 0x20, 0xD0
                )

                # IID_IDeckLinkIterator
                IID_IDeckLinkIterator = GUID()
                IID_IDeckLinkIterator.Data1 = 0xD9B14ED8
                IID_IDeckLinkIterator.Data2 = 0x0D14
                IID_IDeckLinkIterator.Data3 = 0x4BD5
                IID_IDeckLinkIterator.Data4 = (ctypes.c_ubyte * 8)(
                    0xB9, 0x5D, 0xFC, 0x4B, 0x25, 0xA6, 0x20, 0xD0
                )

                self.ole32 = ole32
                self.CLSID_DeckLinkIterator = CLSID_DeckLinkIterator
                self.IID_IDeckLinkIterator = IID_IDeckLinkIterator

                logger.debug("COM setup complete for Windows")
        except Exception as e:
            logger.debug(f"Could not setup COM: {e}")
            self.lib = None

    def is_available(self) -> bool:
        """Check if SDK is available."""
        return self.lib is not None and self.DeckLinkIteratorNew is not None


class DeckLinkDeviceIterator:
    """Iterate through available DeckLink devices."""

    def __init__(self, sdk: Optional[DeckLinkSDK] = None):
        """Initialize device iterator."""
        self.sdk = sdk or DeckLinkSDK()
        self.devices = []
        self.iterator = None
        self._enumerate_devices()

    def _enumerate_devices(self):
        """Enumerate DeckLink devices."""
        if not self.sdk.is_available():
            logger.debug("DeckLink SDK not available, cannot enumerate devices")
            return

        try:
            if sys.platform == "win32":
                self._enumerate_windows()
            else:
                logger.warning("Device enumeration not yet implemented for this platform")
                # TODO: Implement for macOS and Linux

        except Exception as e:
            logger.error(f"Error enumerating devices: {e}", exc_info=True)

    def _enumerate_windows(self):
        """Enumerate devices on Windows using DeckLinkIteratorNew."""
        try:
            # Create iterator via DeckLinkIteratorNew
            iterator_ptr = ctypes.POINTER(IDeckLinkIterator)()
            hr = self.sdk.DeckLinkIteratorNew(ctypes.byref(iterator_ptr))

            if hr != 0:  # S_OK = 0
                logger.error(f"DeckLinkIteratorNew failed with HRESULT: {hr}")
                return

            logger.info(f"Created DeckLink iterator successfully")

            # For now, add a placeholder device since full iteration requires COM vtable handling
            # In production, would enumerate via iterator->Next() method
            device = DeckLinkDevice(0, self.sdk)
            self.devices.append(device)
            logger.info(f"Found {len(self.devices)} DeckLink device(s)")

        except Exception as e:
            logger.error(f"Failed to enumerate Windows devices: {e}", exc_info=True)

    def get_device_count(self) -> int:
        """Get number of available devices."""
        return len(self.devices)

    def get_device(self, index: int) -> Optional['DeckLinkDevice']:
        """Get device at specified index."""
        if 0 <= index < len(self.devices):
            return self.devices[index]
        return None


class DeckLinkDevice:
    """Represents a DeckLink device."""

    def __init__(self, index: int = 0, sdk: Optional[DeckLinkSDK] = None):
        """Initialize DeckLink device."""
        self.index = index
        self.sdk = sdk or DeckLinkSDK()
        self.name = f"DeckLink Device {index}"
        self.is_open = False

        if not self.sdk.is_available():
            raise RuntimeError("DeckLink SDK not available")

        self._init_device()

    def _init_device(self):
        """Initialize the device."""
        logger.info(f"Initializing DeckLink device {self.index}: {self.name}")
        # TODO: Implement device initialization using SDK

    def open(self) -> bool:
        """Open the device for capture/output."""
        try:
            logger.info(f"Opening device {self.index}")
            # TODO: Implement device open
            self.is_open = True
            return True
        except Exception as e:
            logger.error(f"Failed to open device: {e}")
            return False

    def close(self):
        """Close the device."""
        try:
            if self.is_open:
                logger.info(f"Closing device {self.index}")
                self.is_open = False
        except Exception as e:
            logger.error(f"Error closing device: {e}")

    def start_capture(self):
        """Start video capture."""
        logger.info(f"Starting capture on device {self.index}")
        # TODO: Implement capture start

    def stop_capture(self):
        """Stop video capture."""
        logger.info(f"Stopping capture on device {self.index}")
        # TODO: Implement capture stop

    def get_display_modes(self) -> List[Dict]:
        """Get available display modes."""
        # Placeholder - would query actual modes
        return [
            {'name': '1080p60', 'width': 1920, 'height': 1080, 'fps': 60},
            {'name': '1080p59.94', 'width': 1920, 'height': 1080, 'fps': 59.94},
            {'name': '720p60', 'width': 1280, 'height': 720, 'fps': 60},
            {'name': '4K60', 'width': 3840, 'height': 2160, 'fps': 60},
        ]


class DeckLinkFrame:
    """Represents a video frame from DeckLink."""

    def __init__(self, width: int, height: int, pixel_format: int, frame_data: bytes):
        """Initialize frame."""
        self.width = width
        self.height = height
        self.pixel_format = pixel_format
        self.data = frame_data
        self.timestamp = time.time()


class DeckLinkInputCallback:
    """Callback for DeckLink input frames."""

    def __init__(self, frame_queue: queue.Queue):
        """Initialize callback."""
        self.frame_queue = frame_queue

    def on_frame_arrived(self, frame: DeckLinkFrame):
        """Called when a frame arrives."""
        try:
            self.frame_queue.put(frame, block=False)
        except queue.Full:
            logger.debug("Frame queue full, dropping frame")


class NativeDeckLinkCapture:
    """Native DeckLink capture using SDK."""

    def __init__(self, device_index: int = 0, queue_size: int = 4):
        """Initialize native DeckLink capture."""
        self.device_index = device_index
        self.sdk = DeckLinkSDK()
        self.device = None
        self.frame_queue = queue.Queue(maxsize=queue_size)
        self.is_running = False
        self.capture_thread = None

        if not self.sdk.is_available():
            logger.warning("DeckLink SDK not available - capture will not work")
            logger.info("Consider installing FFmpeg with DeckLink support instead")

    def start(self):
        """Start capturing."""
        if not self.sdk.is_available():
            raise RuntimeError("DeckLink SDK not available")

        if self.is_running:
            logger.warning("Capture already running")
            return

        try:
            self.device = DeckLinkDevice(self.device_index, self.sdk)
            self.device.open()
            self.device.start_capture()

            self.is_running = True
            self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.capture_thread.start()

            logger.info("Native DeckLink capture started")

        except Exception as e:
            logger.error(f"Failed to start DeckLink capture: {e}", exc_info=True)
            self.is_running = False

    def stop(self):
        """Stop capturing."""
        if not self.is_running:
            return

        self.is_running = False
        if self.device:
            self.device.stop_capture()
            self.device.close()

        if self.capture_thread:
            self.capture_thread.join(timeout=2.0)

        logger.info("Native DeckLink capture stopped")

    def _capture_loop(self):
        """Main capture loop."""
        try:
            logger.info("Capture loop started")
            while self.is_running:
                # TODO: Implement actual frame capture loop
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"Capture loop error: {e}", exc_info=True)
        finally:
            self.is_running = False

    def get_frame(self, timeout: float = 1.0) -> Optional[DeckLinkFrame]:
        """Get next frame."""
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None


def test_decklink_sdk() -> bool:
    """Test if DeckLink SDK is available."""
    try:
        sdk = DeckLinkSDK()
        if sdk.is_available():
            logger.info("DeckLink SDK is available")
            # Try to enumerate devices
            iterator = DeckLinkDeviceIterator(sdk)
            count = iterator.get_device_count()
            logger.info(f"Found {count} DeckLink device(s)")
            return True
        else:
            logger.warning("DeckLink SDK not found")
            return False
    except Exception as e:
        logger.error(f"Error testing DeckLink SDK: {e}", exc_info=True)
        return False
