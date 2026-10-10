"""Feature extraction on epochs."""

from __future__ import annotations

import numpy as np
from scipy.signal import welch

from ...streams import Features, FeaturesMeta
from ..node import Node, Param


class BandPower(Node):
    """Per-channel band power (Welch), optionally log-transformed."""

    category = "features"
    inputs = {"in": "epochs"}
    outputs = {"out": "features"}
    params = {
        "bands": Param({"mu": [8, 12], "beta": [13, 30]}, "dict", "Band name -> [fmin, fmax] Hz"),
        "log": Param(True, "bool", "Log10 of the power"),
        "relative": Param(False, "bool", "Divide by total power in 1-40 Hz"),
    }

    def setup(self, in_meta):
        meta = in_meta["in"]
        self.sfreq = meta.info.sfreq
        self.nperseg = min(meta.n_times, int(self.sfreq))
        names = [f"{ch}:{b}" for ch in meta.info.ch_names for b in self.p.bands]
        return {"out": FeaturesMeta(names)}

    def process(self, inputs):
        out = []
        for ep in inputs["in"]:
            f, pxx = welch(ep.data, fs=self.sfreq, nperseg=self.nperseg, axis=-1)
            powers = [pxx[:, (f >= lo) & (f <= hi)].mean(axis=1) for lo, hi in self.p.bands.values()]
            p = np.stack(powers, axis=1)  # (n_ch, n_bands)
            if self.p.relative:
                p = p / pxx[:, (f >= 1) & (f <= 40)].mean(axis=1, keepdims=True)
            if self.p.log:
                p = np.log10(p + 1e-12)
            out.append(Features(p.ravel(), ep.time, ep.label))
        return {"out": out}


class TimeWindowMeans(Node):
    """Mean amplitude per channel in time windows of each epoch (ERP / EOG features).

    For eye movements: the sustained potential shift while the gaze is held. Windows are
    in seconds relative to the epoch's event (the Epocher's marker)."""

    category = "features"
    inputs = {"in": "epochs"}
    outputs = {"out": "features"}
    params = {
        "windows": Param([[0.3, 0.7], [0.7, 1.1]], "list", "[[start, end], ...] in s relative to the event"),
    }

    def setup(self, in_meta):
        meta = in_meta["in"]
        sf = meta.info.sfreq
        self.slices = []
        for a, b in self.p.windows:
            i0 = int(round((a - meta.tmin) * sf))
            i1 = int(round((b - meta.tmin) * sf))
            if i0 < 0 or i1 > meta.n_times or i1 <= i0:
                raise ValueError(f"{self.id}: window [{a}, {b}] s is outside the epoch "
                                 f"[{meta.tmin}, {meta.tmin + meta.n_times / sf:.2f}] s")
            self.slices.append(slice(i0, i1))
        names = [f"{ch}:{a:g}-{b:g}s" for (a, b) in self.p.windows for ch in meta.info.ch_names]
        return {"out": FeaturesMeta(names)}

    def process(self, inputs):
        out = []
        for ep in inputs["in"]:
            vals = np.concatenate([ep.data[:, sl].mean(axis=1) for sl in self.slices])
            out.append(Features(vals, ep.time, ep.label))
        return {"out": out}
