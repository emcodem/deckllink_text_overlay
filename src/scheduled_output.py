"""A/V-locked DeckLink playout with scheduled playback (frame synchronizer).

Every output frame n is scheduled at display time n * frame_duration on the card's clock, and
exactly the audio that belongs to it at the same position of the timestamped audio stream
(sample position = round(n * frame_duration * 48000 / timescale)). Audio and video therefore
leave the card on one timeline and cannot drift apart.

The input (e.g. a TV signal) runs on its own clock. That difference is absorbed in whole frames,
always video *and* its audio together:
  input slower -> no new frame when the output needs one: repeat the last picture, silence
  input faster -> frames pile up: drop the oldest one (with its audio)
With genlock (house reference on the card) both clocks are locked and this (almost) never happens.

Captions: the caller supplies ANC packets per scheduled frame (repeats get a fresh CDP too).
"""
import ctypes
import logging
import queue
import threading
import time
from ctypes import c_void_p

import numpy as np
from comtypes import COMObject

from .decklink_com import decklink_module as dl

logger = logging.getLogger(__name__)

AUDIO_RATE = 48000


class _CompletionCallback(COMObject):
    _com_interfaces_ = [dl.IDeckLinkVideoOutputCallback] if dl else []

    def __init__(self, owner):
        super().__init__()
        self._owner = owner

    def IDeckLinkVideoOutputCallback_ScheduledFrameCompleted(self, this, frame, result):
        self._owner._on_completed(int(result))
        return 0

    def IDeckLinkVideoOutputCallback_ScheduledPlaybackHasStopped(self, this):
        return 0


class ScheduledOutput:
    """Scheduled, A/V-locked output on an opened DeckLinkCOM device."""

    def __init__(self, device, fps_num: int, fps_den: int, width: int, height: int,
                 audio_channels: int = 8, preroll: int = 3, max_input_backlog: int = 2):
        self.dev = device
        self.out = device.decklink_output
        self.timescale, self.duration = int(fps_num), int(fps_den)      # e.g. 25000 / 1000
        self.width, self.height = width, height
        self.channels = audio_channels
        self.preroll = max(2, int(preroll))
        self.max_input_backlog = max(1, int(max_input_backlog))
        self._n = 0                        # next frame index to schedule
        self._completed = 0
        self._cv = threading.Condition()
        self.stats = {"scheduled": 0, "repeated": 0, "dropped_input": 0,
                      "late": 0, "dropped_output": 0, "flushed": 0}
        self._last_video = None
        self._started = False
        self._cb = _CompletionCallback(self)

    # ---------------------------------------------------------------- timeline
    def _audio_pos(self, n: int) -> int:
        return (n * self.duration * AUDIO_RATE) // self.timescale

    def samples_for_frame(self, n: int) -> int:
        return self._audio_pos(n + 1) - self._audio_pos(n)

    @property
    def buffered(self) -> int:
        return self._n - self._completed

    # ---------------------------------------------------------------- driver side
    def _on_completed(self, result: int):
        with self._cv:
            self._completed += 1
            if result == dl.bmdOutputFrameDisplayedLate:
                self.stats["late"] += 1
            elif result == dl.bmdOutputFrameDropped:
                self.stats["dropped_output"] += 1
            elif result == dl.bmdOutputFrameFlushed:
                self.stats["flushed"] += 1
            self._cv.notify_all()

    def start(self, vanc: bool = True):
        hr = self.out.EnableVideoOutput(self.dev.display_mode,
                                        dl.bmdVideoOutputVANC if vanc else dl.bmdVideoOutputFlagDefault)
        if hr:
            raise RuntimeError(f"EnableVideoOutput failed: {hr}")
        hr = self.out.EnableAudioOutput(dl.bmdAudioSampleRate48kHz, dl.bmdAudioSampleType16bitInteger,
                                        self.channels, dl.bmdAudioOutputStreamTimestamped)
        if hr:
            raise RuntimeError(f"EnableAudioOutput failed: {hr}")
        self.out.SetScheduledFrameCompletionCallback(self._cb.QueryInterface(dl.IDeckLinkVideoOutputCallback))
        # Audio may only be scheduled before StartScheduledPlayback inside an audio preroll
        # (ScheduleAudioSamples returns E_ACCESSDENIED otherwise)
        hr = self.out.BeginAudioPreroll()
        if hr:
            raise RuntimeError(f"BeginAudioPreroll failed: {hr}")
        self.dev._output_started = True          # keep the sync API out of the way

    def stop(self):
        try:
            if self._started:
                self.out.StopScheduledPlayback(0, self.timescale)
            self.out.DisableAudioOutput()
            self.out.DisableVideoOutput()
        except Exception as e:
            logger.debug(f"ScheduledOutput.stop: {e}")
        self._started = False

    # ---------------------------------------------------------------- scheduling
    def _fit_audio(self, audio, n: int) -> np.ndarray:
        """Exactly samples_for_frame(n) samples x channels int16 (pad with silence / cut)."""
        need = self.samples_for_frame(n)
        out = np.zeros((need, self.channels), np.int16)
        if audio is not None and len(audio):
            a = np.asarray(audio, dtype=np.int16)
            if a.ndim == 1:
                a = a.reshape(-1, 1)
            k, c = min(need, a.shape[0]), min(self.channels, a.shape[1])
            out[:k, :c] = a[:k, :c]
        return out

    def schedule(self, video_bytes: bytes, audio, anc_packets=None):
        """Schedule one output frame with its audio; returns the frame index."""
        n = self._n
        frame = self.dev.create_filled_frame(video_bytes, self.width, self.height)
        if anc_packets:
            from .captions.vanc import attach_packets
            try:
                attach_packets(frame, anc_packets)
            except Exception as e:
                logger.debug(f"VANC attach failed: {e}")
        hr = self.out.ScheduleVideoFrame(frame, n * self.duration, self.duration, self.timescale)
        if hr:
            raise RuntimeError(f"ScheduleVideoFrame failed: {hr}")
        pcm = np.ascontiguousarray(self._fit_audio(audio, n))
        written = self.out.ScheduleAudioSamples(c_void_p(pcm.ctypes.data), pcm.shape[0],
                                                self._audio_pos(n), AUDIO_RATE)
        if int(written or 0) != pcm.shape[0]:
            logger.warning(f"ScheduleAudioSamples wrote {written}/{pcm.shape[0]} for frame {n}")
        with self._cv:
            self._n += 1
        self.stats["scheduled"] += 1
        self._last_video = video_bytes
        if not self._started and self._n >= self.preroll:
            self.out.EndAudioPreroll()
            hr = self.out.StartScheduledPlayback(0, self.timescale, 1.0)
            if hr:
                raise RuntimeError(f"StartScheduledPlayback failed: {hr}")
            self._started = True
            logger.info(f"Scheduled playback started: {self.timescale}/{self.duration} fps, "
                        f"preroll {self.preroll} frames, audio {self.channels} ch timestamped")
        return n

    def run(self, frame_queue: "queue.Queue", is_running, prepare):
        """Frame-synchronizer loop. prepare(frame) -> (video_bytes, audio, anc_packets).

        Called by the playout thread; returns when is_running() is False.
        """
        frame_s = self.duration / self.timescale
        last = None
        while is_running():
            # Wait until the card needs another frame (buffer below preroll)
            with self._cv:
                while self._started and self.buffered >= self.preroll and is_running():
                    self._cv.wait(timeout=frame_s)
            if not is_running():
                break
            # Input faster than output: keep at most max_input_backlog frames waiting
            while frame_queue.qsize() > self.max_input_backlog:
                try:
                    frame_queue.get_nowait()
                    self.stats["dropped_input"] += 1
                except queue.Empty:
                    break
            # Give the input the slack the output buffer still has before repeating
            slack = max(0.0, (self.buffered - 1) * frame_s) if self._started else 1.0
            try:
                frame = frame_queue.get(timeout=max(0.002, slack))
                video, audio, anc = prepare(frame)
                last = frame
            except queue.Empty:
                if self._last_video is None or last is None:
                    continue
                # Input slower than output: repeat the last picture with silence
                # (the caption inserter still produces a fresh CDP for this output frame)
                video, _, anc = prepare(last)
                audio = None
                self.stats["repeated"] += 1
            self.schedule(video, audio, anc)
