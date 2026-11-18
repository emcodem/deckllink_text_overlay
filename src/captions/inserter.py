"""Per-frame closed caption scheduler: pop-on subtitles in, one CDP per output frame out.

Usage (playout thread, once per output frame):
    ins = CaptionInserter(fps_num=25, fps_den=1)
    ins.show(["Erste Zeile", "zweite Zeile"])   # whenever a subtitle appears
    ins.clear()                                  # whenever it disappears
    cdp = ins.next_cdp()                         # attach as ANC DID 0x61 / SDID 0x01 to this frame

608 runs at one byte pair per field-1 slot (one per frame up to 30 fps, every other frame above),
so loading a pop-on caption takes ~ (pairs) frames and the caption appears when loading is done.
708 has ~10-24 pairs per frame and is near-instant. A caption that arrives while an older one is
still waiting in the 608 queue replaces it (live subtitles must not build up a backlog).
"""
import threading
from collections import deque

from . import cea608, cea708, cdp


def wrap_rows(lines, width=32, max_rows=4):
    """Re-wrap subtitle lines to `width` columns on word boundaries, at most `max_rows` rows."""
    words = " ".join(l.strip() for l in lines if l and l.strip()).split()
    rows, cur = [], ""
    for w in words:
        while len(w) > width:              # hard-split words longer than a row
            if cur:
                rows.append(cur); cur = ""
            rows.append(w[:width]); w = w[width:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            rows.append(cur); cur = w
    if cur:
        rows.append(cur)
    return rows[-max_rows:] if len(rows) > max_rows else rows


class CaptionInserter:
    def __init__(self, fps_num: int, fps_den: int, enable_608=True, enable_708=True, bottom_row=15):
        self.rate_code, self.cc_count, self.high_rate = cdp.rate_info(fps_num, fps_den)
        self.enable_608, self.enable_708 = enable_608, enable_708
        self.bottom_row = bottom_row
        self._lock = threading.Lock()
        self._q608 = deque()        # jobs: list of pairs; a job is atomic once started
        self._cur608 = deque()      # pairs of the job being sent
        self._q708 = deque()        # (cc_type, b1, b2) pairs
        self._enc708 = cea708.Encoder708()
        self._pw = cea708.PacketWriter()
        self._seq = 0
        self._frame = 0
        self.stats = {"frames": 0, "pairs608": 0, "pairs708": 0, "dropped608": 0}

    # ---- input
    def show(self, lines):
        rows = wrap_rows(lines, width=min(cea608.COLUMNS, cea708.COLUMNS), max_rows=cea608.MAX_ROWS)
        with self._lock:
            if self.enable_608:
                if not rows:
                    self._queue608(cea608.clear_caption())
                else:
                    # drop captions still waiting (not started): the newest one wins
                    self.stats["dropped608"] += len(self._q608)
                    self._q608.clear()
                    self._queue608(cea608.load_caption(rows, self.bottom_row) + cea608.show_caption())
            if self.enable_708:
                cmds = (self._enc708.load(rows) + self._enc708.show()) if rows else self._enc708.clear()
                self._queue708(cmds)

    def clear(self):
        with self._lock:
            if self.enable_608:
                self._queue608(cea608.clear_caption())
            if self.enable_708:
                self._queue708(self._enc708.clear())

    def _queue608(self, pairs):
        self._q608.append(list(pairs))

    def _queue708(self, cmds):
        if cmds:
            pkts = self._pw.packets(cea708.service_blocks(cmds))
            self._q708.extend(cea708.packets_to_cc_pairs(pkts))

    # ---- output, once per frame
    def next_cdp(self) -> bytes:
        with self._lock:
            triples = []
            # 608: one field-1 + one field-2 pair per frame up to 30 fps; above 30 fps fields alternate
            field1_slot = (not self.high_rate) or (self._frame % 2 == 0)
            if field1_slot:
                pair = self._next608() if self.enable_608 else None
                b1, b2 = pair if pair else cea608.NULL_PAIR
                if pair:
                    self.stats["pairs608"] += 1
                triples.append((1, 0, b1, b2))
            if (not self.high_rate) or (self._frame % 2 == 1):
                triples.append((1, 1, 0x80, 0x80))          # field 2 (CC3/CC4) unused
            while len(triples) < self.cc_count:
                if self._q708:
                    t, b1, b2 = self._q708.popleft()
                    triples.append((1, t, b1, b2))
                    self.stats["pairs708"] += 1
                else:
                    triples.append((0, 2, 0x00, 0x00))     # DTVCC padding
            out = cdp.build_cdp(self.rate_code, self._seq, triples)
            self._seq = (self._seq + 1) & 0xFFFF
            self._frame += 1
            self.stats["frames"] += 1
            return out

    def _next608(self):
        if not self._cur608 and self._q608:
            self._cur608 = deque(self._q608.popleft())
        return self._cur608.popleft() if self._cur608 else None

    @property
    def backlog_608_frames(self) -> int:
        """Frames until everything queued for 608 has been sent (= current 608 display latency)."""
        pairs = len(self._cur608) + sum(len(j) for j in self._q608)
        return pairs * (2 if self.high_rate else 1)
