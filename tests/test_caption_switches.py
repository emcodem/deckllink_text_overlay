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
    # off = no packets, but still a list so the output keeps VANC enabled for switching back on
    assert all(x == [] for x in _run(c608=False, c708=False))
    assert all(x == [] for x in _run(enabled=False))


def test_live_switching():
    config.CAPTIONS_ENABLED, config.CAPTIONS_608, config.CAPTIONS_708, config.CAPTION_VANC_LINE = True, True, True, 9
    p = DeckLinkPlayout.__new__(DeckLinkPlayout)

    def frame(n):
        return Frame(data=None, format="UYVY", width=1920, height=1080, framerate=(25000, 1000),
                     timestamp=n / 25, frame_number=n, subtitle=TEXT)
    assert len(p._caption_packets(frame(0))) == 1
    config.CAPTIONS_ENABLED = False
    assert p._caption_packets(frame(1)) == []
    config.CAPTIONS_ENABLED, config.CAPTIONS_708 = True, False      # back on, 608 only
    pk = [p._caption_packets(frame(n)) for n in range(2, 150)]
    got608, seen708 = _decode(pk)
    assert not seen708
    if T.FFMPEG:
        assert got608 == [TEXT], got608      # current subtitle is re-sent after switching


def test_save_only_touches_caption_lines():
    from src.captions import settings
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.py")
        original = ("CAPTURE_DISPLAY_MODE = 0x68703530   # 720p50 trick\n"
                    "CAPTIONS_608 = True                # keep this comment\n"
                    "SKIP_PLAYOUT = False\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
        from pathlib import Path
        settings.save(Path(path), {"CAPTIONS_ENABLED": True, "CAPTIONS_608": False,
                                   "CAPTIONS_708": True, "CAPTION_VANC_LINE": 14})
        text = open(path, encoding="utf-8").read()
        assert "CAPTURE_DISPLAY_MODE = 0x68703530   # 720p50 trick" in text
        assert "SKIP_PLAYOUT = False" in text
        assert "CAPTIONS_608 = False                # keep this comment" in text
        assert text.count("CAPTIONS_608") == 1
        assert "CAPTION_VANC_LINE = 14" in text and "CAPTIONS_708 = True" in text
        ns = {}
        exec(text, ns)
        assert (ns["CAPTIONS_608"], ns["CAPTIONS_708"], ns["CAPTION_VANC_LINE"]) == (False, True, 14)


def test_vanc_line():
    assert all(x[0]._line == 14 for x in _run(line=14, frames=5))
    assert all(x[0]._line == 9 for x in _run(line="x", frames=5))     # invalid -> default 9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
