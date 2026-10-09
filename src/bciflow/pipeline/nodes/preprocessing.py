"""Streaming preprocessing on continuous signal chunks.

Filters are designed with MNE (``mne.filter.create_filter``, IIR/SOS) and applied
causally with stateful ``scipy.signal.sosfilt``, so the exact same processing runs
online and when training offline through the same graph.
"""

from __future__ import annotations

import re

import numpy as np
from scipy import signal as sps

from ...montage import PRESETS, biosemi_to_1020
from ...streams import SignalChunk
from ..node import Node, Param


class _StreamingSOS:
    def __init__(self, sos: np.ndarray, n_channels: int):
        self.sos = sos
        self.zi_unit = sps.sosfilt_zi(sos)  # (n_sections, 2)
        self.zi = None
        self.n_channels = n_channels

    def __call__(self, data: np.ndarray) -> np.ndarray:
        if data.shape[1] == 0:
            return data
        if self.zi is None:  # start in steady state at the first sample to avoid a step transient
            self.zi = self.zi_unit[:, None, :] * data[None, :, 0:1]  # (n_sections, n_channels, 2)
        out, self.zi = sps.sosfilt(self.sos, data, axis=1, zi=self.zi)
        return out


def _iir_sos(sfreq: float, l_freq: float | None, h_freq: float | None, order: int) -> np.ndarray:
    import mne

    filt = mne.filter.create_filter(None, sfreq, l_freq, h_freq, method="iir",
                                    iir_params=dict(order=order, ftype="butter", output="sos"),
                                    verbose="error")
    return filt["sos"]


class _SignalNode(Node):
    category = "preprocessing"
    inputs = {"in": "signal"}
    outputs = {"out": "signal"}

    def transform(self, data: np.ndarray, times: np.ndarray) -> SignalChunk:
        raise NotImplementedError

    def process(self, inputs):
        return {"out": [self.transform(c.data, c.times) for c in inputs["in"]]}


class PickChannels(_SignalNode):
    """Keep a subset of channels (by name, regex, or type)."""

    params = {
        "include": Param(None, "list", "Channel names or regexes to keep (empty: all)"),
        "exclude": Param(None, "list", "Channel names or regexes to drop"),
        "types": Param(None, "list", "Channel types to keep, e.g. [eeg]"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]

        def match(name, patterns):
            return any(name == p or re.fullmatch(p, name) for p in patterns)

        idx = list(range(info.n_channels))
        if self.p.include:
            idx = [i for i in idx if match(info.ch_names[i], self.p.include)]
        if self.p.exclude:
            idx = [i for i in idx if not match(info.ch_names[i], self.p.exclude)]
        if self.p.types:
            idx = [i for i in idx if info.ch_types[i] in self.p.types]
        if not idx:
            raise ValueError(f"{self.id}: no channels left (available: {info.ch_names[:10]}...)")
        self.idx = idx
        return {"out": info.copy(ch_names=[info.ch_names[i] for i in idx],
                                 ch_types=[info.ch_types[i] for i in idx])}

    def transform(self, data, times):
        return SignalChunk(data[self.idx], times)


class RenameChannels(_SignalNode):
    """Rename channels, e.g. BioSemi A1..B32 to 10-20 names."""

    params = {
        "preset": Param(None, "choice", "BioSemi cap layout", choices=[None, *PRESETS]),
        "mapping": Param(None, "dict", "Explicit {old: new} names (applied after preset)"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]
        mapping = biosemi_to_1020(self.p.preset) if self.p.preset else {}
        mapping.update(self.p.mapping or {})
        return {"out": info.copy(ch_names=[mapping.get(c, c) for c in info.ch_names])}

    def transform(self, data, times):
        return SignalChunk(data, times)


class Rereference(_SignalNode):
    """Re-reference: common average or the mean of given channels.

    Required for BioSemi data, which is recorded reference-free (CMS/DRL)."""

    params = {
        "ref": Param("average", "list", "'average' or list of reference channel names (e.g. [EX1, EX2])"),
        "drop_ref": Param(False, "bool", "Remove the reference channels from the output"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]
        if self.p.ref == "average" or self.p.ref == ["average"]:
            self.ref_idx = [i for i, t in enumerate(info.ch_types) if t == "eeg"] or list(range(info.n_channels))
            self.apply_idx = self.ref_idx
            self.keep = list(range(info.n_channels))
        else:
            missing = [c for c in self.p.ref if c not in info.ch_names]
            if missing:
                raise ValueError(f"{self.id}: reference channel(s) {missing} not in stream")
            self.ref_idx = [info.ch_names.index(c) for c in self.p.ref]
            self.apply_idx = [i for i, t in enumerate(info.ch_types) if t == "eeg"] or list(range(info.n_channels))
            self.keep = [i for i in range(info.n_channels) if not (self.p.drop_ref and i in self.ref_idx)]
        return {"out": info.copy(ch_names=[info.ch_names[i] for i in self.keep],
                                 ch_types=[info.ch_types[i] for i in self.keep])}

    def transform(self, data, times):
        out = data.copy()
        out[self.apply_idx] -= data[self.ref_idx].mean(axis=0)
        return SignalChunk(out[self.keep], times)


class BandpassFilter(_SignalNode):
    """Causal Butterworth band-pass / high-pass / low-pass (MNE design, SOS)."""

    params = {
        "l_freq": Param(1.0, "float", "High-pass edge in Hz (empty: none)"),
        "h_freq": Param(40.0, "float", "Low-pass edge in Hz (empty: none)"),
        "order": Param(4, "int", "Filter order"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]
        self.filt = _StreamingSOS(_iir_sos(info.sfreq, self.p.l_freq, self.p.h_freq, self.p.order), info.n_channels)
        return {"out": info}

    def transform(self, data, times):
        return SignalChunk(self.filt(data), times)


class NotchFilter(_SignalNode):
    """Causal IIR notch at the mains frequency and harmonics."""

    params = {
        "freqs": Param([50.0], "list", "Frequencies to remove (Hz)"),
        "quality": Param(30.0, "float", "Quality factor (higher = narrower)"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]
        sos = [sps.tf2sos(*sps.iirnotch(f, self.p.quality, fs=info.sfreq))
               for f in self.p.freqs if f < info.sfreq / 2]
        self.filt = _StreamingSOS(np.vstack(sos), info.n_channels) if sos else None
        return {"out": info}

    def transform(self, data, times):
        return SignalChunk(self.filt(data) if self.filt else data, times)


class Decimate(_SignalNode):
    """Anti-alias low-pass then keep every N-th sample, to reach ``target_sfreq``
    (e.g. 2048 -> 256 Hz live, 512 -> 256 Hz synthetic: same pipeline for both)."""

    params = {"target_sfreq": Param(256.0, "float", "Output sampling rate; input rate must be a multiple")}

    def setup(self, in_meta):
        info = in_meta["in"]
        ratio = info.sfreq / self.p.target_sfreq
        self.k = int(round(ratio))
        if self.k < 1 or abs(ratio - self.k) > 1e-6:
            raise ValueError(f"{self.id}: {info.sfreq:g} Hz is not an integer multiple of {self.p.target_sfreq:g} Hz")
        new_sfreq = info.sfreq / self.k
        self.filt = (_StreamingSOS(_iir_sos(info.sfreq, None, 0.4 * new_sfreq, 8), info.n_channels)
                     if self.k > 1 else None)
        self.phase = 0  # index (within the next chunk) of the next sample to keep
        return {"out": info.copy(sfreq=new_sfreq)}

    def transform(self, data, times):
        if self.filt is None:
            return SignalChunk(data, times)
        y = self.filt(data)
        idx = np.arange(self.phase, y.shape[1], self.k)
        self.phase = (idx[-1] + self.k - y.shape[1]) if idx.size else self.phase - y.shape[1]
        return SignalChunk(y[:, idx], times[idx])
