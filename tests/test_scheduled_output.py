"""Frame synchronizer (scheduled_output) with a simulated DeckLink: A/V can never drift.

Input frames carry their number in the picture *and* in every audio sample. A fake card plays
the schedule on its own clock, 1 % faster or slower than the input. Checks:
- every output frame: audio == the audio of that frame's picture (or silence for a repeat)
- audio timeline: positions contiguous, exactly samples_for_frame(n) per frame
- input slower -> repeats, input faster (4 %) -> input drops; A/V pairing intact either way

Run:  python tests/test_scheduled_output.py      (or pytest tests/test_scheduled_output.py)
"""
import os
import queue
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.decklink_com import decklink_module as dl     # noqa: E402
from src.scheduled_output import ScheduledOutput       # noqa: E402

FPS = 25


class FakeOutput:
    def __init__(self, out_fps):
        self.out_fps, self.video, self.audio = out_fps, [], []
        self.cb, self.running = None, False

    def EnableVideoOutput(self, *a): return 0
    def EnableAudioOutput(self, *a): return 0
    def DisableVideoOutput(self): return 0
    def DisableAudioOutput(self): return 0
    def BeginAudioPreroll(self): return 0
    def EndAudioPreroll(self): return 0
    def StopScheduledPlayback(self, *a): self.running = False; return 0
    def SetScheduledFrameCompletionCallback(self, cb): self.cb = cb; return 0

    def ScheduleVideoFrame(self, frame, t, dur, ts):
        self.video.append((t // dur, frame)); return 0

    def ScheduleAudioSamples(self, ptr, count, pos, rate):
        arr = np.ctypeslib.as_array((np.ctypeslib.ctypes.c_int16 * (count * 8)).from_address(ptr.value))
        self.audio.append((pos, arr.reshape(count, 8).copy())); return count

    def StartScheduledPlayback(self, *a):
        self.running = True
        threading.Thread(target=self._clock, daemon=True).start(); return 0

    def _clock(self):
        t0, k = time.perf_counter(), 0
        while self.running:
            k += 1
            d = t0 + k / self.out_fps - time.perf_counter()
            if d > 0:
                time.sleep(d)
            self.sched._on_completed(dl.bmdOutputFrameCompleted)


class FakeDevice:
    def __init__(self, out):
        self.decklink_output, self.display_mode, self._output_started = out, 0, False

    def create_filled_frame(self, data, w, h):
        return data


def _run(in_fps, out_fps, seconds=6.0):
    out = FakeOutput(out_fps)
    s = ScheduledOutput(FakeDevice(out), FPS * 1000, 1000, 4, 2, audio_channels=8, preroll=3)
    s._cb = type("CB", (), {"QueryInterface": lambda self, i: self})()
    out.sched = s
    q = queue.Queue(maxsize=4)
    running = [True]

    def feeder():
        t0, i = time.perf_counter(), 0
        while running[0]:
            i += 1
            audio = np.full((1920, 8), i, np.int16)
            try:
                q.put_nowait((i, audio))
            except queue.Full:
                pass
            d = t0 + i / in_fps - time.perf_counter()
            if d > 0:
                time.sleep(d)
    threading.Thread(target=feeder, daemon=True).start()
    th = threading.Thread(target=s.run, args=(q, lambda: running[0],
                                               lambda f: (int(f[0]).to_bytes(4, "big"), f[1], None)),
                          daemon=True)
    th.start()
    time.sleep(seconds)
    running[0] = False
    out.running = False
    th.join(2)
    return s, out


def _check(s, out):
    # timeline contiguous
    assert [n for n, _ in out.video] == list(range(len(out.video)))
    pos = 0
    for n, (p, a) in enumerate(out.audio):
        assert p == pos, f"audio gap at frame {n}: {p} != {pos}"
        assert len(a) == s.samples_for_frame(n)
        pos += len(a)
    # pairing: audio of each output frame == number in its picture, or silence (repeat)
    repeats = 0
    for (n, pic), (_, a) in zip(out.video, out.audio):
        pid = int.from_bytes(pic, "big")
        vals = set(np.unique(a).tolist())
        if vals == {0}:
            repeats += 1
        else:
            assert vals == {pid}, f"frame {n}: picture {pid} but audio {vals} -> A/V not locked"
    return repeats


def test_input_slower_repeats_keep_av_locked():
    s, out = _run(in_fps=FPS * 0.99, out_fps=FPS)
    repeats = _check(s, out)
    print(f"    input 1% slower: {s.stats['scheduled']} frames, repeated {s.stats['repeated']} "
          f"(silent {repeats}), input dropped {s.stats['dropped_input']}")
    assert s.stats["repeated"] >= 1 and repeats == s.stats["repeated"]


def test_input_faster_drops_keep_av_locked():
    s, out = _run(in_fps=FPS * 1.04, out_fps=FPS)
    _check(s, out)
    print(f"    input 4% faster: {s.stats['scheduled']} frames, input dropped {s.stats['dropped_input']}, "
          f"repeated {s.stats['repeated']}")
    assert s.stats["dropped_input"] >= 1


def test_samples_per_frame_ntsc():
    s = ScheduledOutput(FakeDevice(FakeOutput(30)), 30000, 1001, 4, 2)
    counts = [s.samples_for_frame(n) for n in range(5)]
    assert sum(counts) == 8008 and set(counts) <= {1601, 1602}, counts


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
