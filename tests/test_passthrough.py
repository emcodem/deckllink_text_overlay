"""Video passthrough: subtitle blended directly into the captured UYVY picture.

- outside the subtitle box the UYVY bytes stay bit-identical (no colour/range conversion)
- inside the box: white text / black background in legal BT.601 levels
- the RGB path (old pipeline / GUI preview) shows the same subtitle in the right colours
- playout sends the UYVY bytes unchanged

Run:  python tests/test_passthrough.py      (or pytest tests/test_passthrough.py)
"""
import os
import sys
import tempfile
import time
import types

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import config_subtitles                 # noqa: E402
from src.capture import Frame                    # noqa: E402
from src.overlay import TextOverlay              # noqa: E402
from src.playout import DeckLinkPlayout          # noqa: E402

W, H = 1920, 1080


def _overlay(text, **over):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "lines.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    cfg = types.SimpleNamespace(**{k: v for k, v in vars(config_subtitles).items() if not k.startswith("__")})
    cfg.TEXT_FILE = p
    cfg.TEXT_CHECK_INTERVAL = 1
    for k, v in over.items():
        setattr(cfg, k, v)
    return TextOverlay(cfg)


def _uyvy_frame(seed=0):
    rng = np.random.default_rng(seed)
    u = rng.integers(16, 236, (H, W * 2), dtype=np.uint8)
    return Frame(data=None, uyvy=u, format="UYVY", width=W, height=H, framerate=(25, 1),
                 timestamp=0.0, frame_number=0)


def test_outside_box_bit_identical():
    ov = _overlay("Grüße aus Österreich\nzweite Zeile")
    f = _uyvy_frame()
    orig = f.uyvy.copy()
    ov.apply(f)
    y1, y2, x1, x2 = ov._cache_roi
    mask = np.ones_like(orig, bool)
    mask[y1:y2, 2 * x1:2 * x2] = False
    assert np.array_equal(f.uyvy[mask], orig[mask]), "picture outside the subtitle box changed"
    assert not np.array_equal(f.uyvy[~mask], orig[~mask]), "subtitle box not blended"


def test_box_levels_and_text():
    ov = _overlay("XXXXXXXXXXXXXXXX", TEXT_BG_ALPHA=255)
    f = _uyvy_frame()
    ov.apply(f)
    y1, y2, x1, x2 = ov._cache_roi
    # inside of the box only (the ROI edges are rounded to whole UYVY pairs and show background)
    box = f.uyvy[y1 + 2:y2 - 2, 2 * (x1 + 4):2 * (x2 - 4)]
    y, c = box[:, 1::2], np.concatenate([box[:, 0::4], box[:, 2::4]])
    assert 16 <= y.min() and y.max() <= 235, (y.min(), y.max())
    assert 16 <= c.min() and c.max() <= 240, (c.min(), c.max())
    assert y.min() <= 17 and y.max() >= 233, "expected black background and white text"
    assert np.abs(c.astype(int) - 128).max() <= 2, "black/white subtitle must not carry colour"


def test_rgb_path_same_subtitle_and_colours():
    ov = _overlay("Rot", TEXT_COLOR=(255, 0, 0), TEXT_BG_ALPHA=0)
    f = Frame(data=np.zeros((H, W, 3), np.uint8), format="RGB24", width=W, height=H, framerate=(25, 1),
              timestamp=0.0, frame_number=0)
    ov.apply(f)
    y1, y2, x1, x2 = ov._cache_roi
    reddest = f.data[y1:y2, x1:x2].reshape(-1, 3)[np.argmax(f.data[y1:y2, x1:x2, 0])]
    assert reddest[0] > 200 and reddest[2] < 50, f"red text came out as {reddest} (BGR/RGB mix-up)"


def test_playout_sends_uyvy_unchanged_and_gui_rgb():
    ov = _overlay("Test")
    f = _uyvy_frame(1)
    ov.apply(f)
    raw = DeckLinkPlayout.__new__(DeckLinkPlayout)._convert_to_decklink_format(f)
    assert raw == f.uyvy.tobytes()
    rgb = f.ensure_rgb()
    assert rgb.shape == (H, W, 3) and f.format == "RGB24"


def test_per_frame_cost():
    ov = _overlay("Grüße aus Österreich\nzweite Zeile mit etwas mehr Text")
    f = _uyvy_frame()
    ov.apply(f)
    n, t = 50, time.perf_counter()
    for _ in range(n):
        ov.apply(f)
        DeckLinkPlayout.__new__(DeckLinkPlayout)._convert_to_decklink_format(f)
    ms = (time.perf_counter() - t) / n * 1000
    print(f"    overlay + playout per 1080 frame: {ms:.1f} ms")
    assert ms < 20


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
