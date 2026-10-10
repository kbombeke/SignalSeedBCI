"""Synthetic in-ear EEG/EOG with cued eye movements (up, down, left, right).

Montage (16 channels):
- 12 in-ear electrodes: LC1-3 / RC1-3 on the left / right concha, LE1-3 / RE1-3 in the
  left / right ear canal;
- references: M1 / M2 (left / right mastoid), Fpz (forehead), Neck.

Eye movements are modelled with the corneo-retinal dipole (cornea positive): looking
right makes the right ear more positive and the left ear more negative (and vice
versa); looking up / down mostly changes the forehead and only slightly the ears.
The neck sees almost nothing, so it is the natural reference for EOG.

Each trial: cue marker (1 = up, 2 = down, 3 = left, 4 = right), the eyes move after
~0.25 s, hold ~1.2 s and return to the centre. Blinks, background EEG (1/f + some
alpha), mains noise, electrode offsets and slow drift are added.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter, lfilter_zi
from scipy.special import expit

from .synthetic import PINK_A, PINK_B, _Oscillator

CHANNELS = ["LC1", "LC2", "LC3", "LE1", "LE2", "LE3", "RC1", "RC2", "RC3", "RE1", "RE2", "RE3",
            "M1", "M2", "Fpz", "Neck"]
CLASSES = {1: "up", 2: "down", 3: "left", 4: "right"}
DIRECTIONS = {1: (0.0, 1.0), 2: (0.0, -1.0), 3: (-1.0, 0.0), 4: (1.0, 0.0)}  # (horizontal, vertical); + = right / up

# µV at a full gaze shift (~30 deg) for each electrode group: (horizontal, vertical, blink).
# In-ear EOG is small: a few tens of µV horizontally, only a few µV vertically.
_WEIGHTS = {
    "LC": (-22.0, 4.0, 10.0), "LE": (-16.0, 2.5, 6.0),
    "RC": (22.0, 4.0, 10.0), "RE": (16.0, 2.5, 6.0),
    "M1": (-12.0, -2.0, 3.0), "M2": (12.0, -2.0, 3.0),
    "Fpz": (2.0, 60.0, 150.0), "Neck": (0.0, 0.5, 0.5),
}
# extra electrode noise (µV): the ear canal is noisier (fit, movement, cerumen)
_NOISE = {"LC": 1.0, "LE": 1.6, "RC": 1.0, "RE": 1.6, "M1": 0.8, "M2": 0.8, "Fpz": 1.0, "Neck": 1.2}


class InEarEOG:
    """Same interface as SyntheticEEG: ``next(n)`` -> (data µV (16, n), [(offset, code)])."""

    def __init__(self, sfreq: float = 512.0, seed: int | None = None, latency_s: float = 0.25,
                 hold_s: tuple[float, float] = (1.0, 1.4), iti_s: tuple[float, float] = (1.6, 2.4),
                 blink_rate_hz: float = 0.3, noise_uv: float = 8.0, emg_uv: float = 5.0, line_uv: float = 20.0,
                 dc_offset_uv: float = 5000.0, gaze_range: tuple[float, float] = (0.4, 1.1)):
        self.sfreq = float(sfreq)
        self.rng = np.random.default_rng(seed)
        self.classes = dict(CLASSES)
        self.ch_names = list(CHANNELS)
        n_ch = len(CHANNELS)
        rng = self.rng
        # per-electrode weights with individual variation (fit, ear anatomy), fixed per "subject"
        group = [c[:2] if c[:2] in _WEIGHTS else c for c in CHANNELS]
        w = np.array([_WEIGHTS[g] for g in group])
        self.noise_gain = np.array([_NOISE[g] for g in group]) * rng.uniform(0.8, 1.3, n_ch)
        self.emg_uv = emg_uv
        self.gaze_range = gaze_range
        w *= rng.uniform(0.85, 1.15, size=w.shape) * rng.uniform(0.8, 1.2)
        self.w_h, self.w_v, self.w_blink = w[:, 0], w[:, 1], w[:, 2]
        self.latency_s, self.hold_s, self.iti_s = latency_s, hold_s, iti_s
        self.blink_rate = blink_rate_hz
        self.noise_uv, self.line_uv = noise_uv, line_uv
        white = rng.standard_normal(20000)
        self.pink_scale = 1.0 / np.std(lfilter(PINK_B, PINK_A, white)[2000:])
        self.pink_zi = np.outer(np.ones(n_ch), lfilter_zi(PINK_B, PINK_A)) * 0.0
        self.alpha = _Oscillator(10.0, self.sfreq, rng, env_tau_s=1.0)
        self.w_alpha = np.where(np.isin(CHANNELS, ["Fpz", "Neck"]), 1.0, 3.0)
        self.dc = rng.uniform(-dc_offset_uv, dc_offset_uv, n_ch)
        self.drift_phase = rng.uniform(0, 2 * np.pi, n_ch)
        self.sample = 0
        self.next_cue = int(2.0 * self.sfreq)
        self.block: list[int] = []
        self.moves: list[tuple[int, int, float, float]] = []  # (start, end, h, v) in samples
        self.blinks: list[int] = []
        self.next_blink = int(rng.exponential(1 / blink_rate_hz) * self.sfreq) if blink_rate_hz > 0 else None

    @property
    def n_channels(self) -> int:
        return len(self.ch_names)

    def _draw_class(self) -> int:
        if not self.block:  # balanced: every class once per block of four, shuffled
            self.block = list(self.rng.permutation(list(CLASSES)))
        return int(self.block.pop())

    def _gaze(self, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        tau = 0.012 * self.sfreq  # ~50 ms saccades
        h = np.zeros(idx.size)
        v = np.zeros(idx.size)
        for s0, s1, dh, dv in self.moves:
            if s1 + 10 * tau < idx[0] or s0 - 10 * tau > idx[-1]:
                continue
            g = expit((idx - s0) / tau) - expit((idx - s1) / tau)
            h += dh * g
            v += dv * g
        return h, v

    def _blink(self, idx: np.ndarray) -> np.ndarray:
        b = np.zeros(idx.size)
        width = 0.25 * self.sfreq
        for s0 in self.blinks:
            u = (idx - s0) / width
            m = (u >= 0) & (u < 1)
            b[m] += 0.5 - 0.5 * np.cos(2 * np.pi * u[m])
        return b

    def next(self, n: int):
        sf, rng = self.sfreq, self.rng
        events = []
        while self.next_cue < self.sample + n:
            code = self._draw_class()
            cue = self.next_cue
            events.append((cue - self.sample, code))
            start = cue + int((self.latency_s + rng.normal(0, 0.04)) * sf)
            end = start + int(rng.uniform(*self.hold_s) * sf)
            amp = rng.uniform(*self.gaze_range)  # people don't look equally far every time
            dh, dv = DIRECTIONS[code]
            self.moves.append((start, end, dh * amp, dv * amp))
            self.next_cue = end + int(rng.uniform(*self.iti_s) * sf)
        while self.next_blink is not None and self.next_blink < self.sample + n:
            # people blink mostly between trials, but not only
            during = any(s0 <= self.next_blink <= s1 for s0, s1, _, _ in self.moves)
            if not during or rng.random() < 0.3:
                self.blinks.append(self.next_blink)
            self.next_blink += int(max(0.6, rng.exponential(1 / self.blink_rate)) * sf)
        self.moves = [m for m in self.moves if m[1] + sf > self.sample]
        self.blinks = [b for b in self.blinks if b + sf > self.sample]

        idx = self.sample + np.arange(n)
        n_ch = self.n_channels
        white = rng.standard_normal((n_ch, n))
        pink, self.pink_zi = lfilter(PINK_B, PINK_A, white, axis=1, zi=self.pink_zi)
        data = pink * self.pink_scale * self.noise_uv * self.noise_gain[:, None]
        data += self.emg_uv * self.noise_gain[:, None] * rng.standard_normal((n_ch, n))  # broadband (muscle, electrode)
        data += np.outer(self.w_alpha, self.alpha.next(n))
        h, v = self._gaze(idx)
        data += np.outer(self.w_h, h) + np.outer(self.w_v, v) + np.outer(self.w_blink, self._blink(idx))
        t = idx / sf
        data += self.line_uv * np.sin(2 * np.pi * 50.0 * t)
        data += 20 * np.sin(2 * np.pi * 0.02 * t[None, :] + self.drift_phase[:, None])
        data += self.dc[:, None]
        self.sample += n
        return data, events

    def make_raw(self, duration_s: float):
        """Offline recording as ``mne.io.RawArray`` with a 'Status' trigger channel."""
        import mne

        n = int(duration_s * self.sfreq)
        data, events = self.next(n)
        stim = np.zeros(n)
        for off, code in events:
            stim[off:off + int(0.01 * self.sfreq) + 1] = code
        info = mne.create_info(self.ch_names + ["Status"], self.sfreq, ["eeg"] * self.n_channels + ["stim"])
        return mne.io.RawArray(np.vstack([data * 1e-6, stim]), info, verbose="error")
