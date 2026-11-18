"""CEA-608 pop-on caption encoder (data channel CC1, field 1).

Pop-on: the caption is loaded into the decoder's non-displayed memory and shown at once with
End Of Caption (EOC); Erase Displayed Memory (EDM) removes it. No roll-up.

Output is a list of 2-byte pairs (odd parity applied) to send one pair per field-1 slot.
Control codes are sent twice (standard redundancy); extended characters are preceded by a
basic fallback character which the extended code replaces (608 backspace semantics).
"""
import unicodedata

COLUMNS = 32
MAX_ROWS = 4


def odd_parity(b: int) -> int:
    b &= 0x7F
    return b | 0x80 if bin(b).count("1") % 2 == 0 else b


# CC1 control codes
RCL = (0x14, 0x20)   # Resume Caption Loading (pop-on mode)
EDM = (0x14, 0x2C)   # Erase Displayed Memory
ENM = (0x14, 0x2E)   # Erase Non-displayed Memory
EOC = (0x14, 0x2F)   # End Of Caption (swap memories = show)
TAB_OFFSET = {1: (0x17, 0x21), 2: (0x17, 0x22), 3: (0x17, 0x23)}

# Preamble Address Code base per row (white, no underline); indent cursor codes add 0x10 + 2 per 4 columns
_PAC_ROW = {1: (0x11, 0x40), 2: (0x11, 0x60), 3: (0x12, 0x40), 4: (0x12, 0x60), 5: (0x15, 0x40),
            6: (0x15, 0x60), 7: (0x16, 0x40), 8: (0x16, 0x60), 9: (0x17, 0x40), 10: (0x17, 0x60),
            11: (0x10, 0x40), 12: (0x13, 0x40), 13: (0x13, 0x60), 14: (0x14, 0x40), 15: (0x14, 0x60)}


def pac(row: int, column: int):
    """PAC for row 1-15 and an indent (multiple of 4); returns (pac, tab_offset or None)."""
    b1, b2 = _PAC_ROW[row]
    indent = (column // 4) * 4
    tab = column - indent
    return (b1, b2 + 0x10 + (indent // 4) * 2), (TAB_OFFSET[tab] if tab else None)


# Basic character set: ASCII except these positions
_BASIC_OVERRIDE = {"á": 0x2A, "é": 0x5C, "í": 0x5E, "ó": 0x5F, "ú": 0x60,
                   "ç": 0x7B, "÷": 0x7C, "Ñ": 0x7D, "ñ": 0x7E, "█": 0x7F}
_BASIC_TAKEN = {0x2A, 0x5C, 0x5E, 0x5F, 0x60, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F}
# Special North American characters: 0x11 0x30-0x3F (no backspace)
_SPECIAL = {"®": 0x30, "°": 0x31, "½": 0x32, "¿": 0x33, "™": 0x34, "¢": 0x35, "£": 0x36, "♪": 0x37,
            "à": 0x38, "è": 0x3A, "â": 0x3B, "ê": 0x3C, "î": 0x3D, "ô": 0x3E, "û": 0x3F}
# Extended Western European characters (replace the preceding fallback character)
_EXT_12 = {"Á": 0x20, "É": 0x21, "Ó": 0x22, "Ú": 0x23, "Ü": 0x24, "ü": 0x25, "‘": 0x26, "¡": 0x27,
           "*": 0x28, "’": 0x29, "—": 0x2A, "©": 0x2B, "℠": 0x2C, "•": 0x2D, "“": 0x2E, "”": 0x2F,
           "À": 0x30, "Â": 0x31, "Ç": 0x32, "È": 0x33, "Ê": 0x34, "Ë": 0x35, "ë": 0x36, "Î": 0x37,
           "Ï": 0x38, "ï": 0x39, "Ô": 0x3A, "Ù": 0x3B, "ù": 0x3C, "Û": 0x3D, "«": 0x3E, "»": 0x3F}
_EXT_13 = {"Ã": 0x20, "ã": 0x21, "Í": 0x22, "Ì": 0x23, "ì": 0x24, "Ò": 0x25, "ò": 0x26, "Õ": 0x27,
           "õ": 0x28, "{": 0x29, "}": 0x2A, "\\": 0x2B, "^": 0x2C, "_": 0x2D, "|": 0x2E, "~": 0x2F,
           "Ä": 0x30, "ä": 0x31, "Ö": 0x32, "ö": 0x33, "ß": 0x34, "¥": 0x35, "¤": 0x36, "¦": 0x37,
           "Å": 0x38, "å": 0x39, "Ø": 0x3A, "ø": 0x3B, "┌": 0x3C, "┐": 0x3D, "└": 0x3E, "┘": 0x3F}
_FALLBACK = {"ß": "s", "*": ".", "{": "(", "}": ")", "\\": "/", "^": " ", "_": "-", "|": "!", "~": "-",
             "‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "«": '"', "»": '"', "©": "c", "•": "."}


def _basic_code(ch: str):
    if ch in _BASIC_OVERRIDE:
        return _BASIC_OVERRIDE[ch]
    o = ord(ch)
    if 0x20 <= o <= 0x7E and o not in _BASIC_TAKEN:
        return o
    return None


def char_codes(ch: str):
    """Codes for one character: list of ('chr', byte) / ('ctl', (b1, b2)) items."""
    code = _basic_code(ch)
    if code is not None:
        return [("chr", code)]
    if ch in _SPECIAL:
        return [("ctl", (0x11, _SPECIAL[ch]))]
    for b1, table in ((0x12, _EXT_12), (0x13, _EXT_13)):
        if ch in table:
            fb = _FALLBACK.get(ch) or unicodedata.normalize("NFKD", ch)[0]
            fb_code = _basic_code(fb)
            return [("chr", fb_code if fb_code is not None else 0x20), ("ctl", (b1, table[ch]))]
    base = unicodedata.normalize("NFKD", ch)[0]
    code = _basic_code(base)
    return [("chr", code if code is not None else ord("?"))]


def text_width(text: str) -> int:
    return len(text)   # every character occupies one 608 column (extended chars replace their fallback)


def _pack(items):
    """Turn items into parity-coded byte pairs. Control codes are doubled and pair-aligned."""
    pairs, pending = [], None
    for kind, val in items:
        if kind == "chr":
            if pending is None:
                pending = val
            else:
                pairs.append((odd_parity(pending), odd_parity(val)))
                pending = None
        else:
            if pending is not None:
                pairs.append((odd_parity(pending), 0x80))
                pending = None
            p = (odd_parity(val[0]), odd_parity(val[1]))
            pairs.extend([p, p])
    if pending is not None:
        pairs.append((odd_parity(pending), 0x80))
    return pairs


def load_caption(rows, bottom_row: int = 15):
    """Pairs that load `rows` (list of str, <= 32 chars each, <= 4 rows) centred into non-displayed memory."""
    rows = [r for r in rows if r is not None][:MAX_ROWS]
    items = [("ctl", RCL), ("ctl", ENM)]
    first_row = bottom_row - len(rows) + 1
    for i, text in enumerate(rows):
        text = text[:COLUMNS]
        col = max(0, (COLUMNS - text_width(text)) // 2)
        p, tab = pac(first_row + i, col)
        items.append(("ctl", p))
        if tab:
            items.append(("ctl", tab))
        for ch in text:
            items.extend(char_codes(ch))
    return _pack(items)


def show_caption():
    """Pairs that show the loaded caption (EOC swaps displayed/non-displayed memory)."""
    return _pack([("ctl", EOC)])


def clear_caption():
    """Pairs that remove the displayed caption."""
    return _pack([("ctl", EDM)])


NULL_PAIR = (0x80, 0x80)
