"""Offline verification of the closed-caption encoders (no DeckLink needed).

- 608: written as SCC and decoded by FFmpeg's own 608 decoder (cc_dec) -> SRT text compared.
- CDP: written as MCC (25 fps) and decoded by FFmpeg -> checks CDP packaging + the 608 inside it.
- 708: strict decoder below (CDP checksum/sequence, DTVCC packet size/sequence, service blocks,
       window commands) replays the stream and checks what is visible when.
- Frame rates: cc_count / 608 field cadence for all CDP rates.

Run:  venv\\Scripts\\python.exe -m tests.test_captions      (or pytest tests/test_captions.py)
Needs ffmpeg.exe on PATH for the FFmpeg checks (skipped otherwise).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.captions import cea608, cea708, cdp                    # noqa: E402
from src.captions.inserter import CaptionInserter, wrap_rows   # noqa: E402

CAPTIONS = [
    ["Grüße aus Österreich, Straße"],
    ["Die Mannschaft gewann", "mit drei zu eins."],
    ["Morgen wird es überwiegend", "sonnig, mit Höchstwerten", "zwischen 2 und 7 Grad."],
]
FFMPEG = shutil.which("ffmpeg")


# ------------------------------------------------------------------ helpers
def run_inserter(fps_num, fps_den, script, total_frames, **kw):
    """script: {frame: ("show", lines) | ("clear", None)}; returns list of CDP bytes."""
    ins = CaptionInserter(fps_num, fps_den, **kw)
    out = []
    for f in range(total_frames):
        if f in script:
            what, lines = script[f]
            ins.show(lines) if what == "show" else ins.clear()
        out.append(ins.next_cdp())
    return out, ins


def ffmpeg_to_srt(path, fmt):
    srt = path + ".srt"
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", fmt, "-i", path, srt],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with open(srt, encoding="utf-8") as f:
        return f.read()


def srt_texts(srt):
    blocks = [b for b in re.split(r"\n\s*\n", srt.strip()) if b.strip()]
    out = []
    for b in blocks:
        lines = b.splitlines()
        out.append((lines[1], " ".join(l.strip() for l in lines[2:])))
    return out


def norm(s):
    # FFmpeg's 608 decoder output carries ASS-style markup: <font ...>, {\an7}, \h (hard space for indents)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\{\\[^}]*\}", "", s)
    s = s.replace("\\h", " ")
    return re.sub(r"\s+", " ", s).strip()


# ------------------------------------------------------------------ 608 via SCC
def test_608_scc_decoded_by_ffmpeg():
    if not FFMPEG:
        print("  skipped (no ffmpeg)"); return
    # one event per line, at 29.97 drop-frame timecode as SCC requires; pairs are sent one per frame
    events = []
    t = 1
    for rows in CAPTIONS:
        events.append((t, cea608.load_caption(wrap_rows(rows)) + cea608.show_caption()))
        events.append((t + 3, cea608.clear_caption()))
        t += 5
    lines = ["Scenarist_SCC V1.0", ""]
    for sec, pairs in events:
        words = " ".join(f"{a:02x}{b:02x}" for a, b in pairs)
        lines += [f"00:00:{sec:02d};00\t{words}", ""]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.scc")
        with open(p, "w", encoding="ascii") as f:
            f.write("\n".join(lines))
        texts = [norm(t) for _, t in srt_texts(ffmpeg_to_srt(p, "scc"))]
    expected = [norm(" ".join(wrap_rows(c))) for c in CAPTIONS]
    assert texts == expected, f"\n got: {texts}\nwant: {expected}"


# ------------------------------------------------------------------ CDP via MCC
MCC_HEADER = """File Format=MacCaption_MCC V2.0

///////////////////////////////////////////////////////////////////////////////////
// Computer Prompting and Captioning Company
// Ancillary Data Packet Transfer File
//
// Permission to generate this format is granted provided that
//   1. This ANC Transfer file format is used on an as-is basis and no warranty is given, and
//   2. This entire descriptive information text is included in a generated .mcc file.
//
// General file format:
//   HH:MM:SS:FF(tab)[Hexadecimal ANC data in groups of 2 characters]
//     Hexadecimal data starts with the Ancillary Data Packet DID (Data ID defined in S291M)
//       and concludes with the Check Sum following the User Data Words.
//     Each time code line must contain at most one complete ancillary data packet.
//     To transfer additional ANC Data successive lines may contain identical time code.
//     Time Code Rate=[24, 25, 30, 30DF, 50, 60]
//
//   ANC data bytes may be represented by one ASCII character according to the following schema:
//     G  FAh 00h 00h
//     H  2 x (FAh 00h 00h)
//     I  3 x (FAh 00h 00h)
//     J  4 x (FAh 00h 00h)
//     K  5 x (FAh 00h 00h)
//     L  6 x (FAh 00h 00h)
//     M  7 x (FAh 00h 00h)
//     N  8 x (FAh 00h 00h)
//     O  9 x (FAh 00h 00h)
//     P  FBh 80h 80h
//     Q  FCh 80h 80h
//     R  FDh 80h 80h
//     S  96h 69h
//     T  61h 01h
//     U  E1h 00h 00h 00h
//     Z  00h
//
///////////////////////////////////////////////////////////////////////////////////

UUID=4E1C2D3B-5A6F-4B7C-8D9E-0F1A2B3C4D5E
Creation Program=FFCapture caption test
Creation Date=Wednesday, November 18, 2026
Creation Time=12:00:00
Time Code Rate={rate}

"""


def write_mcc(path, cdps, fps):
    lines = []
    for i, c in enumerate(cdps):
        s, fr = divmod(i, fps)
        tc = f"00:{s // 60:02d}:{s % 60:02d}:{fr:02d}"
        lines.append(f"{tc}\t{cdp.anc_packet_8bit(c).hex().upper()}")
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(MCC_HEADER.replace("{rate}", str(fps)) + "\n".join(lines) + "\n")


def caption_script(fps):
    script, f = {}, fps
    for rows in CAPTIONS:
        script[f] = ("show", rows)
        script[f + 4 * fps] = ("clear", None)
        f += 6 * fps
    return script, f + fps


def test_cdp_mcc_decoded_by_ffmpeg():
    if not FFMPEG:
        print("  skipped (no ffmpeg)"); return
    script, frames = caption_script(25)
    cdps, ins = run_inserter(25, 1, script, frames)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mcc")
        write_mcc(p, cdps, 25)
        got = srt_texts(ffmpeg_to_srt(p, "mcc"))
    texts = [norm(t) for _, t in got]
    expected = [norm(" ".join(wrap_rows(c))) for c in CAPTIONS]
    assert texts == expected, f"\n got: {texts}\nwant: {expected}"
    # appearance latency (608 loading time) and clear time are sane
    for (times, _), (sf, _) in zip(got, [(k, v) for k, v in sorted(script.items()) if v[0] == "show"]):
        start = times.split(" --> ")[0]
        h, m, rest = start.split(":"); s, ms = rest.split(",")
        t = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        assert 0 <= t - sf / 25 < 3.0, f"608 caption appears {t - sf / 25:.2f}s after show"


# ------------------------------------------------------------------ strict 708 decoder
class Dtvcc708Decoder:
    """Minimal but strict DTVCC service-1 decoder: windows, pen location, text, show/hide/delete."""

    def __init__(self):
        self.windows = {}       # id -> {"visible": bool, "rows": {row: {col: ch}}, "pen": [r, c]}
        self.cur = None
        self.pkt = None
        self.last_seq = None

    def feed_cdp(self, data: bytes, expect_seq=None):
        assert data[0:2] == b"\x96\x69", "cdp_identifier"
        assert data[2] == len(data), "cdp_length"
        assert sum(data) & 0xFF == 0, "cdp checksum"
        seq = int.from_bytes(data[5:7], "big")
        assert data[-4] == 0x74 and int.from_bytes(data[-3:-1], "big") == seq, "footer sequence"
        if expect_seq is not None:
            assert seq == expect_seq, f"cdp sequence {seq} != {expect_seq}"
        assert data[7] == 0x72
        n = data[8] & 0x1F
        assert data[8] & 0xE0 == 0xE0
        triples = [data[9 + 3 * i: 12 + 3 * i] for i in range(n)]
        assert len(data) == 9 + 3 * n + 4, "cdp size"
        for m, b1, b2 in triples:
            assert m & 0xF8 == 0xF8, "marker bits"
            valid, t = (m >> 2) & 1, m & 3
            if not valid or t < 2:
                continue
            if t == 3:
                assert self.pkt is None or len(self.pkt) >= self.pkt_len, "packet started before previous ended"
                self.pkt = bytearray([b1, b2])
                code = b1 & 0x3F
                self.pkt_len = 128 if code == 0 else code * 2
                seq2 = b1 >> 6
                if self.last_seq is not None:
                    assert seq2 == (self.last_seq + 1) & 3, "DTVCC packet sequence"
                self.last_seq = seq2
            else:
                assert self.pkt is not None, "packet data without start"
                self.pkt += bytes([b1, b2])
            if self.pkt is not None and len(self.pkt) == self.pkt_len:
                self._packet(bytes(self.pkt))
                self.pkt = None
        return n

    def _packet(self, p):
        i = 1
        while i < len(p):
            hdr = p[i]
            if hdr == 0:
                break                                   # null block / padding
            svc, size = hdr >> 5, hdr & 0x1F
            assert svc == 1 and 0 < size <= 31, f"service block header {hdr:02x}"
            self._commands(p[i + 1: i + 1 + size])
            i += 1 + size

    def _commands(self, b):
        i = 0
        while i < len(b):
            c = b[i]
            if 0x20 <= c <= 0x7F or 0xA0 <= c <= 0xFF:
                w = self.windows[self.cur]
                r, col = w["pen"]
                w["rows"].setdefault(r, {})[col] = "♪" if c == 0x7F else bytes([c]).decode("latin-1")
                w["pen"][1] += 1
                i += 1
            elif c == 0x10:                             # EXT1 + G2 char
                w = self.windows[self.cur]; r, col = w["pen"]
                w["rows"].setdefault(r, {})[col] = "…" if b[i + 1] == 0x25 else "?"
                w["pen"][1] += 1; i += 2
            elif 0x80 <= c <= 0x87:
                self.cur = c - 0x80; i += 1
            elif c in (0x89, 0x8A, 0x8C):
                mask = b[i + 1]
                for wid in range(8):
                    if mask >> wid & 1 and wid in self.windows:
                        if c == 0x89: self.windows[wid]["visible"] = True
                        elif c == 0x8A: self.windows[wid]["visible"] = False
                        else: del self.windows[wid]
                if c == 0x8C and self.cur is not None and self.cur not in self.windows:
                    self.cur = None
                i += 2
            elif c == 0x92:
                assert self.cur in self.windows, "SPL without current window"
                self.windows[self.cur]["pen"] = [b[i + 1] & 0x0F, b[i + 2] & 0x3F]; i += 3
            elif 0x98 <= c <= 0x9F:
                wid = c - 0x98
                args = b[i + 1: i + 7]
                assert len(args) == 6, "DefineWindow split across service blocks"
                assert args[0] & 0xC0 == 0 and args[4] & 0xC0 == 0 and args[5] & 0xC0 == 0, "DF reserved bits"
                w = self.windows.setdefault(wid, {"rows": {}, "pen": [0, 0]})
                w["visible"] = bool(args[0] >> 5 & 1)
                w["nrows"], w["ncols"] = (args[3] & 0x0F) + 1, (args[4] & 0x3F) + 1
                self.cur = wid; i += 7
            else:
                raise AssertionError(f"unexpected 708 code {c:02x}")

    def visible_text(self):
        out = []
        for wid in sorted(self.windows):
            w = self.windows[wid]
            if w["visible"]:
                for r in sorted(w["rows"]):
                    cells = w["rows"][r]
                    out.append("".join(cells.get(c, " ") for c in range(max(cells) + 1)).strip())
        return out


def test_708_strict_decode():
    script, frames = caption_script(25)
    cdps, _ = run_inserter(25, 1, script, frames)
    dec = Dtvcc708Decoder()
    timeline = []
    for f, c in enumerate(cdps):
        assert dec.feed_cdp(c, expect_seq=f & 0xFFFF) == 24
        timeline.append(dec.visible_text())
    shows = sorted(k for k, v in script.items() if v[0] == "show")
    clears = sorted(k for k, v in script.items() if v[0] == "clear")
    for rows, sf, cf in zip(CAPTIONS, shows, clears):
        want = wrap_rows(rows)
        first = next((f for f in range(sf, cf) if timeline[f] == want), None)
        assert first is not None, f"caption never visible: {want}"
        assert first - sf <= 5, f"708 caption visible only {first - sf} frames after show"   # <= 200 ms @25
        assert all(timeline[f] == want for f in range(first, cf)), "caption not stable until clear"
        assert timeline[cf + 1] == [], f"not cleared at {cf + 1}: {timeline[cf + 1]}"
        print(f"    708 visible after {first - sf} frame(s): {want}")


def test_708_replace_without_clear():
    cdps, _ = run_inserter(25, 1, {5: ("show", ["eins"]), 10: ("show", ["zwei"]), 15: ("show", ["drei"])}, 30)
    dec = Dtvcc708Decoder()
    vis = []
    for c in cdps:
        dec.feed_cdp(c)
        vis.append(dec.visible_text())
    assert vis[7] == ["eins"] and vis[12] == ["zwei"] and vis[29] == ["drei"], vis


# ------------------------------------------------------------------ frame rates
def test_frame_rates_and_608_cadence():
    for (num, den), (code, cc) in {(24000, 1001): (1, 25), (24, 1): (2, 25), (25, 1): (3, 24),
                                   (30000, 1001): (4, 20), (30, 1): (5, 20), (50, 1): (6, 12),
                                   (60000, 1001): (7, 10), (60, 1): (8, 10)}.items():
        ins = CaptionInserter(num, den)
        ins.show(["Test"])
        f1 = 0
        for f in range(10):
            c = ins.next_cdp()
            assert c[3] >> 4 == code and c[8] & 0x1F == cc, (num, den)
            types = [c[9 + 3 * i] & 3 for i in range(cc)]
            f1 += types.count(0)
            if Fraction(num, den) > 30:
                assert types.count(0) + types.count(1) == 1, "one 608 field per frame above 30 fps"
            else:
                assert types[:2] == [0, 1]
        assert f1 == (5 if Fraction(num, den) > 30 else 10)


def test_wrap_rows():
    assert wrap_rows(["Morgen wird es überwiegend", "sonnig, mit Höchstwerten zwischen zwei und sieben Grad."]) == \
        ["Morgen wird es überwiegend", "sonnig, mit Höchstwerten", "zwischen zwei und sieben Grad."]
    assert all(len(r) <= 32 for r in wrap_rows(["x" * 70]))


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as e:                       # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
