"""Shared LSL publishing for bridges: EEG chunks, trigger-port markers, health logs."""

from __future__ import annotations

import time

import numpy as np
import pylsl

from ..lsl import marker_outlet, signal_outlet
from ..streams import SignalInfo


class BridgePublisher:
    """Publishes decoded amplifier data.

    - EEG: float32 µV stream, timestamped on arrival (last sample = now); receivers
      should enable LSL dejitter (bciflow's LSLSource does).
    - Triggers: one string marker per change of the masked trigger code to a non-zero
      value, timestamped at the exact sample where it changed.
    """

    def __init__(self, info: SignalInfo, eeg_name: str, marker_name: str | None,
                 trigger_mask: int = 0xFF, source_id: str = "bciflow-bridge", log_every_s: float = 5.0):
        self.info = info
        self.eeg = signal_outlet(eeg_name, info, source_id=source_id + "-eeg")
        self.markers = marker_outlet(marker_name, source_id=source_id + "-triggers") if marker_name else None
        self.trigger_mask = trigger_mask
        self.prev_code = 0
        self.n_samples = 0
        self.n_markers = 0
        self.log_every_s = log_every_s
        self._t_log = time.monotonic()
        self._n_at_log = 0
        print(f"[bridge] LSL '{eeg_name}': {info.n_channels} ch @ {info.sfreq:g} Hz"
              + (f", triggers -> '{marker_name}' (mask 0x{trigger_mask:X})" if marker_name else ""))

    def push(self, data_uv: np.ndarray, trigger: np.ndarray | None = None, now: float | None = None,
             extra: str = "") -> None:
        """data_uv: (n_samples, n_channels). trigger: (n_samples,) raw trigger codes."""
        n = data_uv.shape[0]
        if n == 0:
            return
        now = pylsl.local_clock() if now is None else now
        self.eeg.push_chunk(np.ascontiguousarray(data_uv, dtype=np.float32), now)
        if self.markers is not None and trigger is not None:
            codes = trigger.astype(np.int64) & self.trigger_mask
            prev = np.concatenate([[self.prev_code], codes[:-1]])
            for i in np.flatnonzero((codes != prev) & (codes != 0)):
                t = now - (n - 1 - i) / self.info.sfreq
                self.markers.push_sample([str(int(codes[i]))], t)
                self.n_markers += 1
            self.prev_code = int(codes[-1])
        self.n_samples += n
        self._maybe_log(extra)

    def _maybe_log(self, extra: str) -> None:
        t = time.monotonic()
        if t - self._t_log < self.log_every_s:
            return
        rate = (self.n_samples - self._n_at_log) / (t - self._t_log)
        consumers = "yes" if self.eeg.have_consumers() else "no"
        print(f"[bridge] {rate:7.1f} Hz effective | {self.n_samples} samples | {self.n_markers} triggers "
              f"| consumers: {consumers}" + (f" | {extra}" if extra else ""))
        self._t_log, self._n_at_log = t, self.n_samples
