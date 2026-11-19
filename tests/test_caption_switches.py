"""Caption config switches in playout: CAPTIONS_608 / CAPTIONS_708 / CAPTION_VANC_LINE / CAPTIONS_ENABLED.

Drives DeckLinkPlayout._caption_packets() with frames whose subtitle changes and decodes the
resulting CDPs: 608 with FFmpeg (MCC), 708 with the strict decoder from test_captions.

Run:  python tests/test_caption_switches.py      (or pytest tests/test_caption_switches.py)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from src import config                      # noqa: E402
from src.capture import Frame               # noqa: E402
from src.playout import DeckLinkPlayout     # noqa: E402
import test_captions as T                   # noqa: E402

TEXT = "Grüße aus Österreich"


def _run(enabled=True, c608=True, c708=True, line=9, frames=200):
    config.CAPTIONS_ENABLED, config.CAPTIONS_608, config.CAPTIONS_708 = enabled, c608, c708
    config.CAPTION_VANC_LINE = line
    p = DeckLinkPlayout.__new__(DeckLinkPlayout)
    out = []
    for n in range(frames):
        f = Frame(data=None, format="UYVY", width=1920, height=1080, framerate=(25000, 1000),
                  timestamp=n / 25, frame_number=n, subtitle=TEXT if 10 <= n < 150 else "")
        out.append(p._caption_packets(f))
    return out


def _decode(packets):
    cdps = [bytes(pk[0]._buf.raw) for pk in packets]
    dec = T.Dtvcc708Decoder(); seen708 = set()
    for c in cdps:
        dec.feed_cdp(c)
        seen708.update(dec.visible_text())
    got608 = []
    if T.FFMPEG:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.mcc")
            T.write_mcc(p, cdps, 25)
            got608 = [T.norm(t) for _, t in T.srt_texts(T.ffmpeg_to_srt(p, "mcc"))]
    return got608, seen708


def test_defaults_both_line9():
    import importlib
    from src import config as fresh
    importlib.reload(fresh)
    assert fresh.CAPTIONS_ENABLED and fresh.CAPTIONS_608 and fresh.CAPTIONS_708
    assert fresh.CAPTION_VANC_LINE == 9


def test_both():
    pk = _run()
    assert all(len(x) == 1 and x[0]._line == 9 for x in pk)
    got608, seen708 = _decode(pk)
    assert TEXT in seen708
    if T.FFMPEG:
        assert got608 == [TEXT]


def test_only_608():
    got608, seen708 = _decode(_run(c708=False))
    assert not seen708, seen708
    if T.FFMPEG:
        assert got608 == [TEXT]


def test_only_708():
    got608, seen708 = _decode(_run(c608=False))
    assert TEXT in seen708
    if T.FFMPEG:
        assert got608 == [], got608


def test_off():
    assert all(x is None for x in _run(c608=False, c708=False))
    assert all(x is None for x in _run(enabled=False))


def test_vanc_line():
    assert all(x[0]._line == 14 for x in _run(line=14, frames=5))
    assert all(x[0]._line == 9 for x in _run(line="x", frames=5))     # invalid -> default 9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
