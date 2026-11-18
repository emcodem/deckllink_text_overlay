"""SMPTE 334-2 Caption Distribution Packet (CDP) and SMPTE 334-1 ANC wrapping.

One CDP per video frame carries cc_count cc_data triples: the 608 pair(s) first, then DTVCC data.
cc_count and the CDP frame-rate code depend on the output frame rate (any DeckLink mode).
"""
from fractions import Fraction

ANC_DID = 0x61     # SMPTE 334-1: DID 0x61 / SDID 0x01 = CEA-708 CDP
ANC_SDID = 0x01

# frame rate -> (cdp_frame_rate code, cc_count per frame)   [CTA-708 table 3 / SMPTE 334-2]
_RATES = {
    Fraction(24000, 1001): (1, 25), Fraction(24): (2, 25), Fraction(25): (3, 24),
    Fraction(30000, 1001): (4, 20), Fraction(30): (5, 20), Fraction(50): (6, 12),
    Fraction(60000, 1001): (7, 10), Fraction(60): (8, 10),
}


def rate_info(fps_num: int, fps_den: int):
    """(frame_rate_code, cc_count, progressive_high_rate) for a frame rate given as num/den
    (DeckLink GetFrameRate returns duration/timescale: fps = timescale / duration)."""
    fps = Fraction(fps_num, fps_den)
    if fps not in _RATES:
        raise ValueError(f"unsupported caption frame rate {float(fps):.3f} fps")
    code, cc_count = _RATES[fps]
    return code, cc_count, fps > 30


def build_cdp(frame_rate_code: int, sequence: int, triples) -> bytes:
    """triples: list of (cc_valid, cc_type, b1, b2), exactly cc_count entries."""
    cc_count = len(triples)
    body = bytearray()
    body += bytes([0x96, 0x69])                       # cdp_identifier
    body += b"\x00"                                   # cdp_length, filled below
    body += bytes([(frame_rate_code << 4) | 0x0F])    # cdp_frame_rate + reserved
    body += bytes([0x43])                             # ccdata_present | caption_service_active | reserved
    body += sequence.to_bytes(2, "big")               # cdp_hdr_sequence_cntr
    body += bytes([0x72, 0xE0 | cc_count])            # ccdata_section: id, marker bits + cc_count
    for valid, cc_type, b1, b2 in triples:
        body += bytes([0xF8 | (int(valid) << 2) | (cc_type & 3), b1, b2])
    body += bytes([0x74]) + sequence.to_bytes(2, "big")   # cdp_footer + sequence
    body += b"\x00"                                   # packet_checksum, filled below
    body[2] = len(body)
    body[-1] = (-sum(body[:-1])) & 0xFF               # all bytes sum to 0 mod 256
    return bytes(body)


def anc_packet_8bit(cdp: bytes) -> bytes:
    """DID, SDID, DC, UDW, checksum (8-bit values) as used in MCC files.
    On SDI the DeckLink SDK adds 10-bit parity/checksum itself from DID/SDID/data."""
    data = bytes([ANC_DID, ANC_SDID, len(cdp)]) + cdp
    return data + bytes([sum(data) & 0xFF])
