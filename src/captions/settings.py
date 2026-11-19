"""Read/apply/save the caption switches (CAPTIONS_ENABLED / _608 / _708 / CAPTION_VANC_LINE).

Saving touches only these four assignments in the site config.py next to the exe (it is exec'd
on top of the built-in defaults, see src/config.py), so all other site settings stay as they are.
"""
import re
import sys
from pathlib import Path
from typing import Optional

KEYS = ("CAPTIONS_ENABLED", "CAPTIONS_608", "CAPTIONS_708", "CAPTION_VANC_LINE")


def current(cfg) -> dict:
    return {k: getattr(cfg, k) for k in KEYS}


def apply(cfg, values: dict) -> None:
    """Set the values on the live config module; playout picks them up on the next frame."""
    for k in KEYS:
        if k in values:
            setattr(cfg, k, values[k])


def site_config_path() -> Optional[Path]:
    """config.py next to the exe (frozen build); None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.py"
    return None


def save(path: Path, values: dict) -> None:
    """Replace or append `KEY = value` lines for the caption keys in `path` (created if missing)."""
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    for k in KEYS:
        if k not in values:
            continue
        line = f"{k} = {values[k]!r}"
        pat = re.compile(rf"^{k}\s*=.*?(?=\s*#|$)", re.M)
        if pat.search(text):
            text = pat.sub(lambda m: line, text, count=1)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            if "# Closed captions (saved from the GUI)" not in text:
                text += "\n# Closed captions (saved from the GUI)\n"
            text += line + "\n"
    path.write_text(text, encoding="utf-8")
