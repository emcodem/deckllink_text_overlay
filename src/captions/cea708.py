"""CTA-708 (DTVCC) pop-on caption encoder, caption service 1.

Pop-on with two windows used alternately: the next caption is written into a hidden window
(DefineWindow visible=0), and at display time it is shown (DisplayWindows) while the previous one
is deleted (DeleteWindows) in the same service block. Clear = DeleteWindows of the visible window.

Output: DTVCC packets (bytes). `packets_to_cc_pairs` splits them into cc_data pairs
(cc_type 3 = packet start, 2 = packet data) for the CDP.
"""
import unicodedata

SERVICE = 1
COLUMNS = 32          # same line length as the 608 track; 708 would allow up to 42 (16:9)
MAX_ROWS = 4

# C1 commands
def CW(w): return bytes([0x80 + w])                     # SetCurrentWindow
def DSW(mask): return bytes([0x89, mask])               # DisplayWindows
def HDW(mask): return bytes([0x8A, mask])               # HideWindows
def DLW(mask): return bytes([0x8C, mask])               # DeleteWindows
def SPL(row, col): return bytes([0x92, row & 0x0F, col & 0x3F])   # SetPenLocation


def DF(w, rows, cols, visible=False, anchor_v=94, anchor_h=50, anchor_point=7, priority=0,
       window_style=2, pen_style=1):
    """DefineWindow w. Relative positioning (percent); anchor point 7 = bottom centre.
    Predefined window style 2 = pop-up with transparent window fill; predefined pen style 1 = default
    pen with solid black character background, so the look matches 608 (black box behind the text)."""
    return bytes([
        0x98 + w,
        (int(visible) << 5) | (1 << 4) | (1 << 3) | (priority & 7),   # row lock, column lock
        0x80 | (anchor_v & 0x7F),                                       # relative positioning
        anchor_h & 0xFF,
        ((anchor_point & 0x0F) << 4) | ((rows - 1) & 0x0F),
        (cols - 1) & 0x3F,
        ((window_style & 7) << 3) | (pen_style & 7),
    ])


def encode_text(text: str) -> bytes:
    """G0 (ASCII) and G1 (Latin-1) characters; ♪ = 0x7F; others approximated."""
    out = bytearray()
    for ch in text:
        o = ord(ch)
        if ch == "♪":
            out.append(0x7F)
        elif 0x20 <= o <= 0x7E or 0xA0 <= o <= 0xFF:
            out.append(o)
        elif ch in "‘’":
            out.append(0x27)
        elif ch in "“”":
            out.append(0x22)
        elif ch in "–—":
            out.append(0x2D)
        elif ch == "…":
            out += b"\x10\x25"              # G2 ellipsis
        elif ch == "€":
            out.append(0x45)                # no Euro sign in 708 G0-G3: "E"
        else:
            base = unicodedata.normalize("NFKD", ch)[0]
            out.append(ord(base) if 0x20 <= ord(base) <= 0x7E else 0x3F)
    return bytes(out)


class Encoder708:
    """Keeps window state; returns command byte strings (each a list of whole commands)."""

    def __init__(self):
        self.visible = None      # window id currently shown, or None
        self.loaded = None       # window id loaded and hidden, or None

    def load(self, rows):
        rows = [r[:COLUMNS] for r in rows if r is not None][:MAX_ROWS]
        if not rows:
            return []
        w = 1 if self.visible == 0 else 0
        cols = max(1, max(len(r) for r in rows))
        cmds = [DLW(1 << w), DF(w, len(rows), cols, visible=False)]   # DF makes w the current window
        for i, r in enumerate(rows):
            cmds.append(SPL(i, (cols - len(r)) // 2) + encode_text(r))
        self.loaded = w
        return cmds

    def show(self):
        if self.loaded is None:
            return []
        w, old = self.loaded, self.visible
        cmds = [DSW(1 << w)]
        if old is not None and old != w:
            cmds.append(DLW(1 << old))
        self.visible, self.loaded = w, None
        return cmds

    def clear(self):
        if self.visible is None:
            return []
        cmds = [DLW(1 << self.visible)]
        self.visible = None
        return cmds


def service_blocks(cmds, service=SERVICE):
    """Pack whole commands into service blocks (max 31 data bytes each).
    Long SPL+text commands are split into SPL+part and text-only continuations."""
    pieces = []
    for c in cmds:
        if len(c) <= 31:
            pieces.append(c)
            continue
        if c[0] == 0x92:           # SPL + text: split the text
            head, text = c[:3], c[3:]
            pieces.append(head + text[:28])
            text = text[28:]
            while text:
                pieces.append(text[:31])
                text = text[31:]
        else:
            raise ValueError("command longer than a service block")
    blocks, cur = [], b""
    for p in pieces:
        if len(cur) + len(p) > 31:
            blocks.append(cur)
            cur = b""
        cur += p
    if cur:
        blocks.append(cur)
    return [bytes([(service << 5) | len(b)]) + b for b in blocks]


class PacketWriter:
    """Builds DTVCC packets from service blocks, with the 2-bit sequence number."""

    def __init__(self):
        self.seq = 0

    def packets(self, blocks):
        out, cur = [], b""
        for b in blocks:
            if len(cur) + len(b) > 127:          # packet = header + up to 127 bytes
                out.append(self._finish(cur))
                cur = b""
            cur += b
        if cur:
            out.append(self._finish(cur))
        return out

    def _finish(self, data: bytes) -> bytes:
        total = 1 + len(data)
        if total % 2:
            data += b"\x00"                     # pad to an even packet size (null service block)
            total += 1
        size_code = 0 if total == 128 else total // 2
        hdr = ((self.seq & 3) << 6) | size_code
        self.seq = (self.seq + 1) & 3
        return bytes([hdr]) + data


def packets_to_cc_pairs(packets):
    """[(cc_type, b1, b2)] for the CDP: first pair of each packet type 3, rest type 2."""
    out = []
    for p in packets:
        for i in range(0, len(p), 2):
            out.append((3 if i == 0 else 2, p[i], p[i + 1]))
    return out
