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
