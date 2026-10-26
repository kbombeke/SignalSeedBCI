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


class RunnerGame(PredictionOut):
    """BCI showcase: Emu Runner, a black-and-white platform game. The emu keeps running; the
    prediction ``jump_label`` (e.g. a detected blink) makes it jump over rocks and logs,
    picking up seeds on the way. Shown live in the editor node; ⧉ opens a full-size window
    for the participant. The score 'cleared x/y' shows how often the decoded jumps came in
    time. Predictions are published as LSL (JSON)."""

    category = "outputs"
    title = "Emu Runner"
    params = {
        "name": Param("signalseedbci-runner", "str", "LSL stream name for the predictions shown"),
        "jump_label": Param("blink", "str", "Prediction label that makes the emu jump "
                            "(e.g. 'blink' from the Blink Detector, 'left' for left-hand imagery)"),
        "trigger": Param("every", "choice", "'every': jump on every jump_label prediction (events such as "
                         "blinks, or one prediction per trial); 'change': jump when a continuous decision "
                         "changes to jump_label (e.g. a ThresholdClassifier)", choices=["every", "change"]),
        "min_proba": Param(0.0, "float", "Only jump when the probability of jump_label is at least this (0 = any)"),
        "assist": Param(True, "bool", "Hold a jump command for up to 2.5 s and jump at the right moment for the "
                        "next obstacle (makes slow BCI decisions playable)"),
        "crossing_s": Param(4.0, "float", "Seconds an obstacle takes to cross the screen (higher = slower, easier)"),
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


BDF_LSB_UV = 0.03125  # 1 bit = 31.25 nV: ActiView's ±262144 µV over the 24-bit range
BDF_PULSE_S = 0.01  # how long a marker's code stays in Status when it is rebuilt from markers


def _bdf_header(labels: list[str], n_samples: int, start: time.struct_time, n_records: int = -1) -> bytes:
    """BDF header laid out exactly as ActiView writes it; the last label is the Status channel."""

    def field(value, width: int) -> bytes:
        return str(value)[:width].ljust(width).encode("ascii", "replace")

    n = len(labels)
    head = (b"\xffBIOSEMI" + field("", 80) + field("", 80) + field(time.strftime("%d.%m.%y", start), 8)
            + field(time.strftime("%H.%M.%S", start), 8) + field(256 * (n + 1), 8) + field("24BIT", 44)
            + field(n_records, 8) + field(1, 8) + field(n, 4))
    lowpass = f"HP: DC; LP: {round(n_samples * 0.2035)} Hz"  # the ActiveTwo's decimation filter
    eeg = ["Active Electrode", "uV", -262144, 262143, -8388608, 8388607, lowpass, n_samples, "MON"]
    status = ["Triggers and Status", "Boolean", -8388608, 8388607, -8388608, 8388607, "No filtering", n_samples, "TRI"]
    rows = [[lab, *eeg] for lab in labels[:-1]] + [[labels[-1], *status]]
    widths = (16, 80, 8, 8, 8, 8, 8, 80, 8, 32)
    return head + b"".join(field(row[i], w) for i, w in enumerate(widths) for row in rows)


class BDFRecorder(Node):
    """Record the signal to a BioSemi .bdf file, the way ActiView does: 24 bit at 31.25 nV,
    1-second data records, the same header, and a 'Status' channel at the end.

    Connect it straight to the source so the file holds the raw, reference-free data. If the
    signal has a 'Status' channel (bridge started with --status) it is written as it is: the
    full status word, as in ActiView. Otherwise Status is rebuilt from the markers (numeric
    codes, each held for 10 ms). The file is written while recording and stays readable if the
    program stops unexpectedly. A last incomplete second is not saved, as in ActiView."""

    category = "outputs"
    title = "BDF Recorder"
    inputs = {"in": "signal", "markers": "markers"}
    optional_inputs = {"markers"}
    params = {
        "path": Param("recording_{time}.bdf", "path", "Output .bdf file; {time} becomes the start time "
                      "(YYYYmmdd-HHMMSS). An existing file is never overwritten"),
    }

    def setup(self, in_meta):
        info = in_meta["in"]
        self.active = not (self.context.mode == "train" and not self.context.realtime)
        if not float(info.sfreq).is_integer():
            raise ValueError(f"{self.id}: BDF needs an integer sampling rate, got {info.sfreq:g} Hz")
        self.ns = int(info.sfreq)
        self.status_idx = next((i for i, (c, t) in enumerate(zip(info.ch_names, info.ch_types))
                                if c == "Status" or t == "stim"), None)
        self.sig_idx = [i for i in range(info.n_channels) if i != self.status_idx]
        self.labels = [info.ch_names[i] for i in self.sig_idx] + ["Status"]
        self.pulse_n = max(2, round(BDF_PULSE_S * info.sfreq))
        self.dig = np.zeros((len(self.sig_idx), 0), np.int64)
        self.stat = np.zeros(0, np.int64)
        self.times = np.zeros(0)
        self.events: list[list] = []  # [time, code, written] from the markers input
        self.n_records = self.n_triggers = self.n_late = self.n_text = self.n_clipped = 0
        self.file = None
        if not self.active:
            return {}
        self.path = Path(self.p.path.replace("{time}", time.strftime("%Y%m%d-%H%M%S")))
        if self.path.exists():
            raise ValueError(f"{self.id}: {self.path} already exists; choose another path (or use {{time}})")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "wb")
        self.log(f"recording {len(self.labels)} channels ({len(self.sig_idx)} + Status) @ {self.ns} Hz to "
                 f"{self.path}; Status from {'the Status channel' if self.status_idx is not None else 'markers'}")
        return {}

    def process(self, inputs):
        if not self.active:
            return {}
        for m in inputs.get("markers", []):
            try:
                code = int(m.value)
            except ValueError:
                self.n_text += 1  # e.g. text markers from PsychoPy: no place for them in Status
                continue
            self.n_triggers += 1
            if self.status_idx is None:
                self.events.append([m.time, code & 0xFFFFFF, False])
        for c in inputs["in"]:
            if not c.n_samples:
                continue
            if self.file.tell() == 0:
                self.file.write(_bdf_header(self.labels, self.ns, time.localtime()))
            d = np.round(c.data[self.sig_idx] / BDF_LSB_UV)
            clipped = np.abs(d) > 2 ** 23 - 1
            if clipped.any():
                self.n_clipped += int(clipped.sum())
                d = np.clip(d, -2 ** 23, 2 ** 23 - 1)
            self.dig = np.concatenate([self.dig, d.astype(np.int64)], axis=1)
            st = (np.round(c.data[self.status_idx]).astype(np.int64) if self.status_idx is not None
                  else np.zeros(c.n_samples, np.int64))
            self.stat = np.concatenate([self.stat, st])
            self.times = np.concatenate([self.times, c.times])
        # keep one second in hand so that markers arriving a little after their samples still fit
        while self.times.size >= 2 * self.ns:
            self._write_record()
        return {}

    def _write_record(self) -> None:
        ns, half = self.ns, 0.5 / self.ns
        t = self.times[:ns]
        stat = self.stat[:ns].copy()
        for ev in self.events:
            hit = (t >= ev[0] - half) & (t < ev[0] + (self.pulse_n - 0.5) / self.ns)
            if hit.any():
                stat[hit] = ev[1]
                ev[2] = True
        done = [ev for ev in self.events if ev[0] + self.pulse_n / self.ns < t[-1]]
        self.n_late += sum(not ev[2] for ev in done)
        self.events = [ev for ev in self.events if ev not in done]
        values = np.vstack([self.dig[:, :ns], stat[None]]) & 0xFFFFFF  # 24-bit two's complement
        raw = np.stack([values & 0xFF, (values >> 8) & 0xFF, values >> 16], axis=-1).astype(np.uint8)
        self.file.write(raw.tobytes())
        self.n_records += 1
        self.file.seek(236)  # number of records, kept up to date so the file is always complete
        self.file.write(str(self.n_records).ljust(8).encode())
        self.file.seek(0, 2)
        self.file.flush()
        self.dig, self.stat, self.times = self.dig[:, ns:], self.stat[ns:], self.times[ns:]

    def close(self):
        if self.file is None:
            return
        while self.times.size >= self.ns:
            self._write_record()
        self.file.close()
        self.file = None
        if self.n_records == 0:
            self.path.unlink(missing_ok=True)
            self.log("nothing recorded (less than 1 s of data); no file written")
            return
        notes = [f"{self.times.size / self.ns:.2f} s at the end not saved (incomplete record)"] if self.times.size else []
        if self.n_late:
            notes.append(f"{self.n_late} markers arrived too late for Status")
        if self.n_text:
            notes.append(f"{self.n_text} non-numeric markers not saved")
        if self.n_clipped:
            notes.append(f"{self.n_clipped} samples beyond ±262144 µV clipped")
        self.log(f"saved {self.n_records} s, {self.n_triggers} triggers to {self.path}"
                 + (f" ({'; '.join(notes)})" if notes else ""))
