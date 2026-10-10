"""Load recordings (BDF/EDF/GDF/FIF/BrainVision/EEGLAB via MNE, XDF via pyxdf)
into one in-memory representation used by replay and offline pipeline runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .streams import Marker, SignalInfo

SIGNAL_TYPES = {"eeg", "eog", "emg", "ecg", "seeg", "ecog", "misc", "bio", "resp", "gsr"}
UV_TYPES = {"eeg", "eog", "emg", "ecg", "seeg", "ecog"}


@dataclass
class Recording:
    data: np.ndarray  # (n_channels, n_samples), microvolts for electrophysiology channels
    times: np.ndarray  # (n_samples,), seconds from recording start
    info: SignalInfo
    markers: list[Marker]

    @property
    def duration(self) -> float:
        return self.data.shape[1] / self.info.sfreq


def stim_to_markers(stim: np.ndarray, times: np.ndarray, mask: int = 0xFF) -> list[Marker]:
    """Turn a trigger/status channel into markers at every change to a non-zero code."""
    codes = np.asarray(stim).astype(np.int64) & mask
    change = np.flatnonzero(np.diff(codes, prepend=0) != 0)
    return [Marker(float(times[i]), str(int(codes[i]))) for i in change if codes[i] != 0]


def load_recording(path: str | Path, stim_channel: str | None = "auto", event_mask: int = 0xFF,
                   use_annotations: bool = True, include_types: set[str] | None = None) -> Recording:
    path = Path(path)
    if path.suffix.lower() == ".xdf":
        return _load_xdf(path)

    import mne

    raw = mne.io.read_raw(path, preload=True, verbose="error")
    times = raw.times.copy()
    types = raw.get_channel_types()

    markers: list[Marker] = []
    if stim_channel == "auto":
        stims = [c for c, t in zip(raw.ch_names, types) if t == "stim"]
        stim_channel = "Status" if "Status" in stims else (stims[0] if stims else None)
    if stim_channel:
        markers += stim_to_markers(raw.get_data(picks=[stim_channel])[0], times, event_mask)
    if use_annotations:
        # With an orig_time, onsets count from meas_date; otherwise from the first sample.
        offset = raw.first_time if raw.annotations.orig_time is not None else 0.0
        for ann in raw.annotations:
            if ann["description"].upper().startswith(("BAD", "EDGE")):
                continue
            markers.append(Marker(float(ann["onset"] - offset), str(ann["description"])))
    markers.sort(key=lambda m: m.time)

    keep_types = include_types or SIGNAL_TYPES
    picks = [i for i, t in enumerate(types) if t in keep_types]
    data = raw.get_data(picks=picks)
    kinds = [types[i] for i in picks]
    scale = np.array([1e6 if k in UV_TYPES else 1.0 for k in kinds])
    info = SignalInfo([raw.ch_names[i] for i in picks], float(raw.info["sfreq"]), kinds)
    return Recording(data * scale[:, None], times, info, markers)


def _load_xdf(path: Path) -> Recording:
    try:
        import pyxdf
    except ImportError as e:  # pragma: no cover
        raise ImportError("Reading XDF needs pyxdf: pip install 'signalseedbci[xdf]'") from e

    streams, _ = pyxdf.load_xdf(str(path))
    numeric = [s for s in streams if s["info"]["channel_format"][0] != "string"
               and float(s["info"]["nominal_srate"][0]) > 0]
    if not numeric:
        raise ValueError(f"{path}: no regularly sampled stream")
    eeg = next((s for s in numeric if s["info"]["type"][0].upper() == "EEG"), numeric[0])
    sfreq = float(eeg["info"]["nominal_srate"][0])
    data = np.asarray(eeg["time_series"], dtype=float).T
    ts = np.asarray(eeg["time_stamps"])
    n = data.shape[0]
    try:
        chans = eeg["info"]["desc"][0]["channels"][0]["channel"]
        names = [c["label"][0] for c in chans]
    except (KeyError, IndexError, TypeError):
        names = [f"Ch{i + 1}" for i in range(n)]
    t0 = ts[0]
    markers = []
    for s in streams:
        if s["info"]["channel_format"][0] == "string":
            markers += [Marker(float(t - t0), str(v[0])) for t, v in zip(s["time_stamps"], s["time_series"])]
    markers.sort(key=lambda m: m.time)
    return Recording(data, ts - t0, SignalInfo(names, sfreq), markers)
