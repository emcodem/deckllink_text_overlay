"""DeckLink VANC output: one SMPTE 334 CDP per frame as an IDeckLinkAncillaryPacket.

The DeckLink driver calls back into this COM object when it schedules the frame, asks for the
user data words (8-bit) and does the 10-bit ANC encoding itself (ADF, DID/SDID, data count,
parity, checksum), so this works with 8-bit video frames too.
"""
import ctypes

import comtypes
from comtypes import COMObject

from ..decklink_com import decklink_module
from .cdp import ANC_DID, ANC_SDID

S_OK = 0
E_NOTIMPL = -2147467263   # 0x80004001

# Default VANC line for CDPs. Line 9 is the common choice for 720p and 1080i/p (SMPTE RP 2016 area);
# override with CAPTION_VANC_LINE in config.py if a downstream device expects another line.
DEFAULT_VANC_LINE = 9


class AncPacket(COMObject):
    """Ancillary packet carrying `data` (user data words) on `line`."""
    _com_interfaces_ = [decklink_module.IDeckLinkAncillaryPacket] if decklink_module else []

    def __init__(self, data: bytes, did: int = ANC_DID, sdid: int = ANC_SDID,
                 line: int = DEFAULT_VANC_LINE, stream_index: int = 0):
        super().__init__()
        if len(data) > 255:
            raise ValueError(f"ANC user data too long: {len(data)} > 255")
        self._buf = ctypes.create_string_buffer(bytes(data), len(data))
        self._size = len(data)
        self._did, self._sdid, self._line, self._stream = did, sdid, line, stream_index

    # Low-level implementations (comtypes passes `this`); out params are raw pointers.
    # Desktop Video 16 asks for UInt16 (with a NULL data pointer), then YCbCr10, then UInt8.
    # Answering UInt16 makes it stop there without ever fetching data, so only UInt8 is served.
    def IDeckLinkAncillaryPacket_GetBytes(self, this, fmt, data_ptr, size_ptr):
        if fmt != decklink_module.bmdAncillaryPacketFormatUInt8:
            return E_NOTIMPL
        if data_ptr:
            data_ptr[0] = ctypes.addressof(self._buf)
        if size_ptr:
            size_ptr[0] = self._size
        return S_OK

    def IDeckLinkAncillaryPacket_GetDID(self, this):
        return self._did

    def IDeckLinkAncillaryPacket_GetSDID(self, this):
        return self._sdid

    def IDeckLinkAncillaryPacket_GetLineNumber(self, this):
        return self._line

    def IDeckLinkAncillaryPacket_GetDataStreamIndex(self, this):
        return self._stream

    def IDeckLinkAncillaryPacket_GetDataSpace(self, this):
        return getattr(decklink_module, "bmdAncillaryDataSpaceVANC", 0x76616e63)  # 'vanc'


def attach_packets(video_frame, packets) -> None:
    """Attach AncPacket objects to a (mutable) DeckLink video frame before it is output."""
    if not packets:
        return
    anc = video_frame.QueryInterface(decklink_module.IDeckLinkVideoFrameAncillaryPackets)
    for p in packets:
        anc.AttachPacket(p.QueryInterface(decklink_module.IDeckLinkAncillaryPacket))
