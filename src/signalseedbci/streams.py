"""Data containers flowing between pipeline nodes.

Conventions:
- Signal data is float64, shape (n_channels, n_samples), in microvolts.
- Times are seconds on the LSL clock (online) or file time (offline).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np


@dataclass
class SignalInfo:
    ch_names: list[str]
    sfreq: float
    ch_types: list[str] | None = None

    def __post_init__(self):
        if self.ch_types is None:
            self.ch_types = ["eeg"] * len(self.ch_names)

    @property
    def n_channels(self) -> int:
        return len(self.ch_names)

    def copy(self, **changes) -> "SignalInfo":
        changes.setdefault("ch_names", list(self.ch_names))
        changes.setdefault("ch_types", list(self.ch_types))
        return replace(self, **changes)

    def to_mne(self):
        """Return an ``mne.Info`` (data in Volts when handed to MNE)."""
        import mne

        types = [t if t in _MNE_TYPES else "misc" for t in self.ch_types]
        return mne.create_info(list(self.ch_names), self.sfreq, types)


_MNE_TYPES = {"eeg", "eog", "emg", "ecg", "stim", "misc", "resp", "bio", "gsr", "temperature"}


@dataclass
class SignalChunk:
    data: np.ndarray  # (n_channels, n_samples)
    times: np.ndarray  # (n_samples,)

    @property
    def n_samples(self) -> int:
        return self.data.shape[1]


@dataclass
class Marker:
    time: float
    value: str


@dataclass
class EpochsMeta:
    info: SignalInfo
    tmin: float
    n_times: int


@dataclass
class Epoch:
    data: np.ndarray  # (n_channels, n_times)
    time: float  # time of the locking event (or window end for sliding windows)
    label: str | None = None


@dataclass
class FeaturesMeta:
    names: list[str]


@dataclass
class Features:
    values: np.ndarray  # (n_features,)
    time: float
    label: str | None = None


@dataclass
class Prediction:
    time: float
    label: str
    proba: dict[str, float] = field(default_factory=dict)
    true_label: str | None = None


class SignalBuffer:
    """Growing buffer of recent samples, trimmed to ``max_seconds``."""

    def __init__(self, n_channels: int, sfreq: float, max_seconds: float):
        self.sfreq = sfreq
        self.capacity = max(1, int(np.ceil(max_seconds * sfreq)))
        self.data = np.zeros((n_channels, 0))
        self.times = np.zeros(0)

    def append(self, chunk: SignalChunk) -> None:
        self.data = np.concatenate([self.data, chunk.data], axis=1)
        self.times = np.concatenate([self.times, chunk.times])
        if self.times.size > 2 * self.capacity:
            self.data = self.data[:, -self.capacity:]
            self.times = self.times[-self.capacity:]

    @property
    def last_time(self) -> float | None:
        return float(self.times[-1]) if self.times.size else None

    @property
    def first_time(self) -> float | None:
        return float(self.times[0]) if self.times.size else None

    def window(self, start_time: float, n_samples: int) -> np.ndarray | None:
        """Return ``n_samples`` starting at the first sample >= start_time."""
        i0 = int(np.searchsorted(self.times, start_time - 0.5 / self.sfreq))
        if i0 + n_samples > self.times.size:
            return None
        return self.data[:, i0:i0 + n_samples].copy()
