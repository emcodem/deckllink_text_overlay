"""Closed captions for SDI playout: CEA-608 + CTA-708 pop-on in SMPTE 334 CDPs (VANC DID 0x61/0x01).

See TODO.md in the parent repo and tests/test_captions.py (offline verification with FFmpeg).
"""
from .inserter import CaptionInserter, wrap_rows
from .cdp import ANC_DID, ANC_SDID, build_cdp, anc_packet_8bit, rate_info

__all__ = ["CaptionInserter", "wrap_rows", "ANC_DID", "ANC_SDID", "build_cdp", "anc_packet_8bit", "rate_info"]
