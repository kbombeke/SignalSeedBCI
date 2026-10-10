"""Publish a recording or the synthetic generator as LSL streams in real time.

Streams use the same names as the BioSemi bridge by default, so any pipeline
runs unchanged against live data, a replayed recording, or synthetic data.
"""

from __future__ import annotations

import time

import numpy as np
import pylsl

from .. import DEFAULT_EEG_STREAM, DEFAULT_MARKER_STREAM
from ..io import Recording, load_recording
from ..lsl import marker_outlet, signal_outlet
from ..streams import SignalInfo
from .synthetic import SyntheticEEG


class _Publisher:
    def __init__(self, info: SignalInfo, eeg_name: str, marker_name: str | None, source_id: str):
        self.info = info
        self.eeg = signal_outlet(eeg_name, info, source_id=source_id + "-eeg")
        self.markers = marker_outlet(marker_name, source_id=source_id + "-markers") if marker_name else None
        self.t0 = pylsl.local_clock()
        self.n_sent = 0

    def push(self, data: np.ndarray, markers: list[tuple[float, str]] = ()):
        """data: (n_ch, n) µV; markers: (time relative to stream start, value)."""
        n = data.shape[1]
        ts = self.t0 + (self.n_sent + np.arange(n)) / self.info.sfreq
        self.eeg.push_chunk(np.ascontiguousarray(data.T, dtype=np.float32), list(ts))
        if self.markers:
            for t, value in markers:
                self.markers.push_sample([value], self.t0 + t)
        self.n_sent += n

    def due_samples(self, speed: float = 1.0) -> int:
        elapsed = (pylsl.local_clock() - self.t0) * speed
        return int(elapsed * self.info.sfreq) - self.n_sent


def replay_recording(rec: Recording, eeg_name: str = DEFAULT_EEG_STREAM,
                     marker_name: str | None = DEFAULT_MARKER_STREAM, loop: bool = False,
                     speed: float = 1.0, chunk_s: float = 0.02, verbose: bool = True) -> None:
    pub = _Publisher(rec.info, eeg_name, marker_name, source_id="signalseedbci-replay")
    n_total = rec.data.shape[1]
    marker_times = np.array([m.time for m in rec.markers])
    if verbose:
        print(f"Replaying {rec.info.n_channels} ch @ {rec.info.sfreq:g} Hz, {rec.duration:.0f} s, "
              f"{len(rec.markers)} markers as LSL '{eeg_name}'"
              + (f" + '{marker_name}'" if marker_name else "") + ". Ctrl+C to stop.")
    pos, lap_offset = 0, 0.0
    try:
        while True:
            time.sleep(chunk_s)
            n = pub.due_samples(speed)
            while n > 0:
                take = min(n, n_total - pos)
                t_a, t_b = pos / rec.info.sfreq, (pos + take) / rec.info.sfreq
                sel = np.flatnonzero((marker_times >= t_a) & (marker_times < t_b))
                pub.push(rec.data[:, pos:pos + take],
                         [(lap_offset + rec.markers[i].time, rec.markers[i].value) for i in sel])
                pos += take
                n -= take
                if pos >= n_total:
                    if not loop:
                        if verbose:
                            print("End of recording.")
                        return
                    pos, lap_offset = 0, lap_offset + rec.duration
    except KeyboardInterrupt:
        pass


def replay_file(path: str, **kwargs) -> None:
    load_kw = {k: kwargs.pop(k) for k in ("stim_channel", "event_mask") if k in kwargs}
    replay_recording(load_recording(path, **load_kw), **kwargs)


def stream_synthetic(gen, eeg_name: str = DEFAULT_EEG_STREAM,
                     marker_name: str | None = DEFAULT_MARKER_STREAM, duration: float | None = None,
                     chunk_s: float = 0.02, verbose: bool = True) -> None:
    info = SignalInfo(gen.ch_names, gen.sfreq)
    pub = _Publisher(info, eeg_name, marker_name, source_id="signalseedbci-synthetic")
    if verbose:
        print(f"Synthetic EEG: {gen.n_channels} ch @ {gen.sfreq:g} Hz as LSL '{eeg_name}' + "
              f"'{marker_name}', classes {gen.classes}. Ctrl+C to stop.")
    try:
        while duration is None or pub.n_sent < duration * gen.sfreq:
            time.sleep(chunk_s)
            n = pub.due_samples()
            if n <= 0:
                continue
            data, events = gen.next(n)
            pub.push(data, [((pub.n_sent + off) / gen.sfreq, str(code)) for off, code in events])
    except KeyboardInterrupt:
        pass
