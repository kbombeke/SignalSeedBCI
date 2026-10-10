"""Cut continuous signal into epochs: marker-locked (cue-based BCIs, ERPs) or
sliding windows (continuous control)."""

from __future__ import annotations

import numpy as np

from ...streams import Epoch, EpochsMeta, SignalBuffer
from ..node import Node, Param


class Epocher(Node):
    """Marker-locked epochs [tmin, tmax] around selected markers."""

    category = "epoching"
    inputs = {"in": "signal", "markers": "markers"}
    outputs = {"out": "epochs"}
    params = {
        "events": Param(None, "dict", "Marker value -> class label, e.g. {'1': left, '2': right}. "
                                      "Empty: every marker, labelled by its value"),
        "tmin": Param(0.5, "float", "Epoch start relative to marker (s)"),
        "tmax": Param(2.5, "float", "Epoch end relative to marker (s)"),
        "baseline": Param(None, "list", "[start, end] (s) to subtract the mean of, or empty"),
        "buffer_s": Param(30.0, "float", "Signal history kept to wait for late markers"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]
        if self.p.tmax <= self.p.tmin:
            raise ValueError(f"{self.id}: tmax must be > tmin")
        self.n_times = int(round((self.p.tmax - self.p.tmin) * info.sfreq))
        self.buf = SignalBuffer(info.n_channels, info.sfreq, max(self.p.buffer_s, 2 * (self.p.tmax - self.p.tmin)))
        self.pending = []
        self.events = {str(k): str(v) for k, v in (self.p.events or {}).items()}
        self.dropped = 0
        if self.p.baseline:
            b0, b1 = self.p.baseline
            self.bl = slice(int(round((b0 - self.p.tmin) * info.sfreq)), int(round((b1 - self.p.tmin) * info.sfreq)))
        else:
            self.bl = None
        return {"out": EpochsMeta(info, self.p.tmin, self.n_times)}

    def process(self, inputs):
        for chunk in inputs["in"]:
            self.buf.append(chunk)
        for m in inputs["markers"]:
            if not self.events or m.value in self.events:
                self.pending.append(m)
        out, keep = [], []
        sfreq = self.buf.sfreq
        for m in self.pending:
            start = m.time + self.p.tmin
            if self.buf.first_time is not None and start < self.buf.first_time - 0.5 / sfreq:
                self.dropped += 1
                self.log(f"dropped marker {m.value!r}: data already discarded ({self.dropped} total)")
                continue
            data = self.buf.window(start, self.n_times) if self.buf.last_time is not None \
                and self.buf.last_time >= m.time + self.p.tmax - 1.0 / sfreq else None
            if data is None:
                keep.append(m)
                continue
            if self.bl is not None:
                data -= data[:, self.bl].mean(axis=1, keepdims=True)
            out.append(Epoch(data, m.time, self.events.get(m.value, m.value)))
        self.pending = keep
        return {"out": out}


class SlidingWindow(Node):
    """Overlapping windows for continuous (asynchronous) decoding."""

    category = "epoching"
    inputs = {"in": "signal"}
    outputs = {"out": "epochs"}
    params = {
        "window_s": Param(1.0, "float", "Window length (s)"),
        "step_s": Param(0.1, "float", "Step between windows (s)"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]
        self.n_times = int(round(self.p.window_s * info.sfreq))
        self.step = max(1, int(round(self.p.step_s * info.sfreq)))
        self.buf_data = np.zeros((info.n_channels, 0))
        self.buf_times = np.zeros(0)
        return {"out": EpochsMeta(info, -self.p.window_s, self.n_times)}

    def process(self, inputs):
        for c in inputs["in"]:
            self.buf_data = np.concatenate([self.buf_data, c.data], axis=1)
            self.buf_times = np.concatenate([self.buf_times, c.times])
        out = []
        while self.buf_times.size >= self.n_times:
            out.append(Epoch(self.buf_data[:, :self.n_times].copy(), float(self.buf_times[self.n_times - 1])))
            self.buf_data = self.buf_data[:, self.step:]
            self.buf_times = self.buf_times[self.step:]
        return {"out": out}
