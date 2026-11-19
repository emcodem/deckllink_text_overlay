"""Playout colour conversion: RGB frame -> UYVY (playout) -> RGB (capture-side PyAV) round trip.

Hardware capture labels RGB frames with the BMD format name ('bmdFormat8BitYUV'); without
FIX_PLAYOUT_COLORS those were treated as BGR and red/blue swapped on SDI (verified on a
DeckLink Quad loop 2026-11-19).

Run:  python -m tests.test_playout_colors      (or pytest tests/test_playout_colors.py)
"""
import os
import sys

import av
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import config                      # noqa: E402
from src.capture import Frame               # noqa: E402
from src.playout import DeckLinkPlayout     # noqa: E402

W, H = 128, 16
BARS = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
        (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]


def _bars():
    img = np.zeros((H, W, 3), np.uint8)
    for i, c in enumerate(BARS):
        img[:, i * W // 8:(i + 1) * W // 8] = c
    return img


def _round_trip(fix, broadcast):
    config.FIX_PLAYOUT_COLORS, config.PLAYOUT_BROADCAST_RANGE = fix, broadcast
    img = _bars()
    frame = Frame(data=img, format="bmdFormat8BitYUV", width=W, height=H, framerate=(25, 1),
                  timestamp=0.0, frame_number=0)
    raw = DeckLinkPlayout.__new__(DeckLinkPlayout)._convert_to_decklink_format(frame)
    vf = av.VideoFrame(W, H, "uyvy422")
    vf.planes[0].update(raw)
    return img, np.frombuffer(raw, np.uint8).reshape(H, W * 2), vf.reformat(format="rgb24").to_ndarray()


def test_old_path_swaps_red_blue():
    img, _, back = _round_trip(False, False)
    red = 5 * W // 8 + W // 16
    assert back[H // 2, red, 2] > 200 and back[H // 2, red, 0] < 50


def test_colour_fix():
    img, _, back = _round_trip(True, False)
    assert np.abs(back.astype(int) - img).max() <= 30


def test_broadcast_range():
    img, uyvy, back = _round_trip(True, True)
    y, c = uyvy[:, 1::2], np.concatenate([uyvy[:, 0::4], uyvy[:, 2::4]])
    assert 16 <= y.min() and y.max() <= 235
    assert 16 <= c.min() and c.max() <= 240
    assert np.abs(back.astype(int) - img).max() <= 4


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
