"""Shared caption status (written by playout once per output frame, read by the GUI)."""
import threading

_lock = threading.Lock()
_status = {
    "state": "idle",        # idle | off | active | error
    "reason": "",           # why not active (e.g. "playout off")
    "c608": False,
    "c708": False,
    "line": 0,
    "fps": "",
    "frames": 0,            # CDPs sent since captions (re)started
    "backlog608_s": 0.0,    # seconds of 608 data still waiting to be sent
    "text": "",             # caption currently shown / loading
    "error": "",
}


def update(**kw):
    with _lock:
        _status.update(kw)


def get() -> dict:
    with _lock:
        return dict(_status)
