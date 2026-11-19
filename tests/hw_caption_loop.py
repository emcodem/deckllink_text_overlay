"""MANUAL HARDWARE TEST (needs an SDI loop between two DeckLink channels): end-to-end SDI captions, native driver on both sides.

TX: DeckLinkCOM.put_video_frame(..., anc_packets=[AncPacket(cdp)]) on OUT, script from tests.test_captions.
RX: IDeckLinkInputCallback on IN, every DID 0x61/0x01 packet collected in arrival order.
Check: the project's strict 708 decoder + FFmpeg's 608 decoder on an MCC file of the received CDPs.

usage: python tests/hw_caption_loop.py <repo_dir> <temp_dir> <out_index> <in_index> <vanc_line>
       e.g. python tests/hw_caption_loop.py . C:\temp 6 7 9   (KI-UT5: DeckLink Quad (7) -> (8))
"""
import os, sys, time, threading, ctypes
import numpy as np
repo, scratch, out_idx, in_idx, line = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
sys.path.insert(0, repo)
import comtypes
from comtypes import COMObject
from src.decklink_com import DeckLinkCOM, decklink_module as dl
from src.captions import CaptionInserter, wrap_rows
from src.captions.vanc import AncPacket
import tests.test_captions as T

rx_cdps, rx_lines, lock = [], set(), threading.Lock()


class InCb(COMObject):
    _com_interfaces_ = [dl.IDeckLinkInputCallback]

    def IDeckLinkInputCallback_VideoInputFormatChanged(self, this, events, mode, flags):
        return 0

    def IDeckLinkInputCallback_VideoInputFrameArrived(self, this, frame, audio):
        try:
            if not frame:
                return 0
            it = frame.QueryInterface(dl.IDeckLinkVideoFrameAncillaryPackets).GetPacketIterator()
            while True:
                try:
                    p = it.Next()
                except comtypes.COMError:
                    break
                if not p:
                    break
                if (p.GetDID(), p.GetSDID()) == (0x61, 0x01):
                    ptr, size = p.GetBytes(dl.bmdAncillaryPacketFormatUInt8)
                    with lock:
                        rx_cdps.append(ctypes.string_at(ptr, size)); rx_lines.add(p.GetLineNumber())
        except comtypes.COMError:
            pass
        return 0


rx = DeckLinkCOM(in_idx, dl.bmdModeHD1080i50)
cb = InCb()
rx.decklink_input.SetCallback(cb.QueryInterface(dl.IDeckLinkInputCallback))
rx.decklink_input.EnableVideoInput(dl.bmdModeHD1080i50, dl.bmdFormat8BitYUV, 0)
rx.decklink_input.StartStreams()

tx = DeckLinkCOM(out_idx, dl.bmdModeHD1080i50)
script, frames = T.caption_script(25)
ins = CaptionInserter(25, 1)
W, H = 1920, 1080
img = np.zeros((H, W * 2), np.uint8); img[:, 0::2] = 128; img[:, 1::2] = 60
data = img.tobytes()
tx_cdps = []
t0 = time.time()
for f in range(frames):
    if f in script:
        what, rows = script[f]
        ins.show(rows) if what == "show" else ins.clear()
    c = ins.next_cdp(); tx_cdps.append(c)
    tx.put_video_frame(data, W, H, anc_packets=[AncPacket(c, line=line)])
    d = t0 + (f + 1) / 25 - time.time()
    if d > 0:
        time.sleep(d)
time.sleep(0.5)
rx.decklink_input.StopStreams()

print(f"sent {len(tx_cdps)} CDPs, received {len(rx_cdps)} on line(s) {sorted(rx_lines)}")
# byte-exact: every received CDP must be one that was sent, in order
idx = {c: i for i, c in enumerate(tx_cdps)}
order = [idx.get(c) for c in rx_cdps]
print("received CDPs unknown/corrupt:", sum(o is None for o in order),
      "| out of order:", sum(1 for a, b in zip(order, order[1:]) if a is not None and b is not None and b < a),
      "| sent but not received:", len(set(range(len(tx_cdps))) - set(o for o in order if o is not None)))

dec = T.Dtvcc708Decoder(); seen708 = []
for c in rx_cdps:
    dec.feed_cdp(c)
    v = dec.visible_text()
    if v and (not seen708 or seen708[-1] != v):
        seen708.append(v)
print("708 captions decoded from SDI:", seen708)

p = os.path.join(scratch, "rx.mcc")
T.write_mcc(p, rx_cdps, 25)
got = [T.norm(t) for _, t in T.srt_texts(T.ffmpeg_to_srt(p, "mcc"))]
want = [T.norm(" ".join(wrap_rows(c))) for c in T.CAPTIONS]
print("608 captions decoded from SDI:", got)
print("608 match:", got == want, "| 708 match:", seen708 == [wrap_rows(c) for c in T.CAPTIONS])
