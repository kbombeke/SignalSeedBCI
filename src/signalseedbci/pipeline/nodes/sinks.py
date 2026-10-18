"""Sink nodes: publish results over LSL, log, record."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from ...lsl import marker_outlet, signal_outlet
from ...streams import SignalInfo
from ..node import Node, Param


class DataVisualizer(Node):
    """Live plot of a signal or feature vectors in the editor, also published as an LSL stream.

    Connected markers are drawn as event lines and republished as '<name>-markers'.
    (Pipeline files may still use the old type name LSLSignalOut.)"""

    category = "outputs"
    title = "Data Visualizer"
    inputs = {"in": "signal|features", "markers": "markers"}
    optional_inputs = {"markers"}
    params = {
        "name": Param("signalseedbci-out", "str", "LSL stream name"),
        "type": Param("EEG", "str", "LSL stream type"),
        "display": Param("bandpass", "choice", "Filter for the editor's plots only (published data is unchanged): "
                         "1-40 Hz + notch, high-pass 0.5 Hz, or none (use for EOG / slow signals)",
                         choices=["bandpass", "highpass", "none"]),
    }

    def setup(self, in_meta):
        meta = in_meta["in"]
        if isinstance(meta, SignalInfo):
            info, self.kind = meta, "signal"
        else:
            info, self.kind = SignalInfo(list(meta.names), 0.0, ["misc"] * len(meta.names)), "features"
        self.outlet = signal_outlet(self.p.name, info, stype=self.p.type, source_id=f"signalseedbci-{self.id}")
        self.marker_outlet = (marker_outlet(self.p.name + "-markers", source_id=f"signalseedbci-{self.id}-markers")
                              if "markers" in in_meta else None)
        return {}

    def process(self, inputs):
        for item in inputs["in"]:
            if self.kind == "signal":
                self.outlet.push_chunk(np.ascontiguousarray(item.data.T, dtype=np.float32), list(item.times))
            else:
                self.outlet.push_sample(item.values.astype(np.float32), item.time)
        if self.marker_outlet is not None:
            for m in inputs.get("markers", []):
                self.marker_outlet.push_sample([m.value], m.time)
        return {}


MAX_SPECTRUM_VALUES = 8192  # channels x frequency bins per LSL sample


class SpectrumOut(Node):
    """Real-time power spectrum (Welch, µV²/Hz) of the incoming signal, published as LSL.

    Every ``step_s`` the last ``window_s`` seconds are analysed. Channels of the LSL
    stream are labelled 'CHANNEL@FREQ' (e.g. 'Oz@10'); the editor and the scope window
    draw them as a spectrum or spectrogram. Connected markers are republished as
    '<name>-markers'."""

    category = "outputs"
    inputs = {"in": "signal", "markers": "markers"}
    optional_inputs = {"markers"}
    params = {
        "name": Param("signalseedbci-spectrum", "str", "LSL stream name"),
        "window_s": Param(2.0, "float", "Analysis window (s)"),
        "step_s": Param(0.25, "float", "Update interval (s)"),
        "resolution_hz": Param(1.0, "float", "Frequency resolution (Welch segment = 1/resolution s)"),
        "fmin": Param(1.0, "float", "Lowest frequency published (Hz)"),
        "fmax": Param(40.0, "float", "Highest frequency published (Hz)"),
        "average_channels": Param(False, "bool", "Publish one channel-averaged spectrum instead of one per channel"),
    }

    def setup(self, in_meta):
        from scipy.fft import rfftfreq

        info = in_meta["in"]
        sf = info.sfreq
        self.window_n = max(8, int(round(self.p.window_s * sf)))
        self.step_n = max(1, int(round(self.p.step_s * sf)))
        self.nperseg = min(self.window_n, max(8, int(round(sf / self.p.resolution_hz))))
        if self.nperseg < round(sf / self.p.resolution_hz):
            self.log(f"resolution limited to {sf / self.nperseg:.2f} Hz by window_s={self.p.window_s}")
        freqs = rfftfreq(self.nperseg, 1.0 / sf)
        self.sel = np.flatnonzero((freqs >= self.p.fmin) & (freqs <= min(self.p.fmax, sf / 2)))
        if not self.sel.size:
            raise ValueError(f"{self.id}: no frequency bins between fmin and fmax")
        self.freqs = freqs[self.sel]
        chans = ["mean"] if self.p.average_channels else list(info.ch_names)
        labels = [f"{c}@{f:g}" for c in chans for f in self.freqs]
        if len(labels) > MAX_SPECTRUM_VALUES:
            raise ValueError(f"{self.id}: {len(chans)} channels x {len(self.freqs)} bins = {len(labels)} values; "
                             "pick fewer channels, lower fmax/resolution, or set average_channels")
        self.sfreq = sf
        self.outlet = signal_outlet(self.p.name, SignalInfo(labels, 0.0, ["misc"] * len(labels)),
                                    stype="Spectrum", unit="uV^2/Hz", source_id=f"signalseedbci-{self.id}")
        self.marker_outlet = (marker_outlet(self.p.name + "-markers", source_id=f"signalseedbci-{self.id}-markers")
                              if "markers" in in_meta else None)
        self.buf = np.zeros((info.n_channels, 0))
        self.last_time = None
        self.since_emit = 0
        return {}

    def process(self, inputs):
        from scipy.signal import welch

        for c in inputs["in"]:
            if not c.n_samples:
                continue
            self.buf = np.concatenate([self.buf, c.data], axis=1)[:, -self.window_n:]
            self.last_time = float(c.times[-1])
            self.since_emit += c.n_samples
        if self.buf.shape[1] >= self.window_n and self.since_emit >= self.step_n:
            # a display stream: one spectrum of the latest window per update, never a backlog
            _, pxx = welch(self.buf, fs=self.sfreq, nperseg=self.nperseg, noverlap=self.nperseg // 2,
                           detrend="constant", axis=-1)
            pxx = pxx[:, self.sel]
            if self.p.average_channels:
                pxx = pxx.mean(axis=0, keepdims=True)
            self.outlet.push_sample(pxx.ravel().astype(np.float32), self.last_time)
            self.since_emit = 0
        if self.marker_outlet is not None:
            for m in inputs.get("markers", []):
                self.marker_outlet.push_sample([m.value], m.time)
        return {}


class PredictionOut(Node):
    """Publish predictions as an LSL string marker stream (JSON per prediction),
    for feedback programs (PsychoPy, Unity, ...)."""

    category = "outputs"
    inputs = {"in": "predictions"}
    params = {"name": Param("signalseedbci-predictions", "str", "LSL stream name")}

    def setup(self, in_meta):
        self.outlet = marker_outlet(self.p.name, stype="Predictions", source_id=f"signalseedbci-{self.id}")
        return {}

    def process(self, inputs):
        for pr in inputs["in"]:
            self.outlet.push_sample([json.dumps({"label": pr.label, "proba": pr.proba})], pr.time)
        return {}


class EyesFeedback(PredictionOut):
    """Animated eyes driven by predictions: closed when the label is ``closed_label``
    (e.g. high alpha), open otherwise. Shown live in the editor node; ⧉ opens a
    full-size window for the participant. Predictions are published as LSL (JSON)."""

    category = "outputs"
    params = {
        "name": Param("signalseedbci-eyes", "str", "LSL stream name for the predictions shown"),
        "closed_label": Param("closed", "str", "Prediction label that closes the eyes"),
        "graded": Param(False, "bool", "Eyelids follow the probability of 'closed' instead of the decision"),
    }


class GazeFeedback(PredictionOut):
    """Animated eyes that look in the predicted direction (up / down / left / right),
    hold it for ``hold_s`` and return to looking ahead. Shown live in the editor node;
    ⧉ opens a full-size window. Predictions are published as LSL (JSON)."""

    category = "outputs"
    params = {
        "name": Param("signalseedbci-gaze", "str", "LSL stream name for the predictions shown"),
        "up_label": Param("up", "str", "Prediction label for looking up"),
        "down_label": Param("down", "str", "Prediction label for looking down"),
        "left_label": Param("left", "str", "Prediction label for looking left"),
        "right_label": Param("right", "str", "Prediction label for looking right"),
        "hold_s": Param(1.5, "float", "Seconds the eyes keep looking before returning to the centre"),
        "graded": Param(False, "bool", "Gaze follows the probabilities (weighted direction) instead of the decision"),
    }


class Logger(Node):
    """Print what arrives; for predictions with known true labels, running accuracy."""

    category = "outputs"
    inputs = {"in": "predictions|markers|epochs|features|signal"}
    params = {"every_s": Param(2.0, "float", "Minimum seconds between summary lines for signal/features")}

    def setup(self, in_meta):
        self.n_ok = self.n_known = 0
        self.n_items = 0
        self._t = time.monotonic()
        return {}

    def process(self, inputs):
        for item in inputs["in"]:
            kind = type(item).__name__
            if kind == "Prediction":
                msg = f"t={item.time:.2f} -> {item.label}"
                if item.proba:
                    msg += " (" + ", ".join(f"{k}={v:.2f}" for k, v in item.proba.items()) + ")"
                if item.true_label is not None:
                    self.n_known += 1
                    self.n_ok += item.label == item.true_label
                    msg += f" | true {item.true_label} | running acc {self.n_ok / self.n_known:.2f} ({self.n_known})"
                self.log(msg)
            elif kind == "Marker":
                self.log(f"marker {item.value!r} at {item.time:.3f}")
            elif kind == "Epoch":
                self.log(f"epoch {item.label!r} at {item.time:.2f}, shape {item.data.shape}")
            else:
                self.n_items += 1
                if time.monotonic() - self._t >= self.p.every_s:
                    self.log(f"{self.n_items} {kind} items")
                    self._t = time.monotonic()
        return {}

    def close(self):
        if self.n_known:
            self.log(f"final accuracy {self.n_ok / self.n_known:.3f} over {self.n_known} labelled predictions")
            self.context.store.setdefault("accuracy", {})[self.id] = self.n_ok / self.n_known


class Recorder(Node):
    """Save the incoming signal and markers to a .fif file when the pipeline stops."""

    category = "outputs"
    inputs = {"in": "signal", "markers": "markers"}
    optional_inputs = {"markers"}
    params = {"path": Param("recording_raw.fif", "path", "Output .fif file")}

    def setup(self, in_meta):
        self.info = in_meta["in"]
        self.chunks, self.times, self.markers = [], [], []
        return {}

    def process(self, inputs):
        for c in inputs["in"]:
            self.chunks.append(c.data)
            self.times.append(c.times)
        self.markers += inputs.get("markers", [])
        return {}

    def close(self):
        # training from files replays existing data; live calibration is new data worth keeping
        if not self.chunks or (self.context.mode == "train" and not self.context.realtime):
            return
        import mne

        data = np.concatenate(self.chunks, axis=1)
        times = np.concatenate(self.times)
        scale = np.array([1e-6 if t in ("eeg", "eog", "emg", "ecg") else 1.0 for t in self.info.ch_types])
        raw = mne.io.RawArray(data * scale[:, None], self.info.to_mne(), verbose="error")
        if self.markers:
            onsets = [m.time - times[0] for m in self.markers if m.time >= times[0]]
            descs = [m.value for m in self.markers if m.time >= times[0]]
            raw.set_annotations(mne.Annotations(onsets, [0.0] * len(onsets), descs))
        path = Path(self.p.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw.save(path, overwrite=True, verbose="error")
        self.log(f"saved {data.shape[1] / self.info.sfreq:.1f} s, {len(self.markers)} markers to {path}")
