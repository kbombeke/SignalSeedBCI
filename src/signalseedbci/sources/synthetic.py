"""Synthetic EEG for testing without an amplifier.

Paradigms:
- ``motor-imagery`` (default): random left/right cues (codes 1/2) with lateralised
  mu desynchronisation.
- ``alpha-bursts``: posterior alpha alternates between low and high, e.g. 10 s of
  strong alpha every 20 s; code 1 marks burst onset, code 2 burst offset. Useful to
  check visualisations and alpha neurofeedback pipelines.

The signal mimics raw ActiveTwo data: large per-channel DC offsets and a
common-mode component (CMS/DRL, line noise) on top of 1/f background, a posterior
alpha rhythm and two sensorimotor mu sources (around C3 and C4). During a
"left" trial mu power drops over the right hemisphere (C4) and vice versa
(event-related desynchronisation), so a CSP/Riemannian classifier can learn it.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter, lfilter_zi

from ..montage import biosemi_labels

PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
PINK_A = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])


class _Oscillator:
    def __init__(self, freq: float, sfreq: float, rng: np.random.Generator, env_tau_s: float = 0.5):
        self.freq, self.sfreq, self.rng = freq, sfreq, rng
        self.phase = rng.uniform(0, 2 * np.pi)
        alpha = 1.0 / max(1.0, env_tau_s * sfreq)
        self.env_b, self.env_a = [alpha], [1.0, alpha - 1.0]
        self.env_zi = np.array([1.0 - alpha]) * 1.0
        self.env_mean = 1.0

    def next(self, n: int) -> np.ndarray:
        drift = 1.0 + 0.03 * self.rng.standard_normal()
        steps = 2 * np.pi * self.freq * drift / self.sfreq * np.ones(n)
        phases = self.phase + np.cumsum(steps)
        self.phase = phases[-1] % (2 * np.pi)
        noise = self.env_mean + 0.6 * self.rng.standard_normal(n) * np.sqrt(self.sfreq * 0.5)
        env, self.env_zi = lfilter(self.env_b, self.env_a, noise, zi=self.env_zi)
        return np.clip(env, 0.1, None) * np.sin(phases)


PARADIGMS = ("motor-imagery", "alpha-bursts")


class SyntheticEEG:
    """Stateful generator: ``next(n)`` returns (data_uV (n_ch, n), events).

    ``events`` is a list of (sample offset within the chunk, code).
    """

    def __init__(self, preset: str = "biosemi32", sfreq: float = 512.0, seed: int | None = None,
                 classes: dict[int, str] | None = None, raw_labels: bool = True,
                 trial_s: float = 4.0, iti_s: tuple[float, float] = (2.0, 3.0), erd: float = 0.5,
                 noise_uv: float = 6.0, mu_uv: float = 12.0, alpha_uv: float = 8.0,
                 line_freq: float = 50.0, line_uv: float = 40.0, dc_offset_uv: float = 15000.0,
                 paradigm: str = "motor-imagery", burst_s: float = 10.0, period_s: float = 20.0,
                 alpha_low: float = 0.3, alpha_high: float = 3.0):
        import mne

        if paradigm not in PARADIGMS:
            raise ValueError(f"Unknown paradigm {paradigm!r}; choose from {PARADIGMS}")
        if not 0 < burst_s < period_s:
            raise ValueError("burst_s must be between 0 and period_s")
        self.paradigm = paradigm
        # alpha bursts: [k*period + (period - burst), (k+1)*period), so each cycle starts at baseline
        self.period_n = int(round(period_s * sfreq))
        self.burst_n = int(round(burst_s * sfreq))
        self.alpha_low, self.alpha_high = alpha_low, alpha_high
        self.sfreq = float(sfreq)
        self.rng = np.random.default_rng(seed)
        self.classes = classes or {1: "left", 2: "right"}
        montage = mne.channels.make_standard_montage(preset)
        names_1020 = montage.ch_names
        pos = montage.get_positions()["ch_pos"]
        self.ch_names = biosemi_labels(len(names_1020)) if raw_labels else list(names_1020)
        self.names_1020 = names_1020
        n_ch = len(names_1020)
        xyz = np.array([pos[c] for c in names_1020])

        def spread(center: str, sigma: float) -> np.ndarray:
            d = np.linalg.norm(xyz - pos[center], axis=1)
            return np.exp(-0.5 * (d / sigma) ** 2)

        self.w_mu_left = spread("C3", 0.03) * mu_uv
        self.w_mu_right = spread("C4", 0.03) * mu_uv
        self.w_alpha = spread("Oz", 0.05) * alpha_uv
        self.mu_left = _Oscillator(11.0, self.sfreq, self.rng)
        self.mu_right = _Oscillator(11.5, self.sfreq, self.rng)
        self.alpha = _Oscillator(10.0, self.sfreq, self.rng, env_tau_s=1.0)

        self.noise_uv = noise_uv
        white = self.rng.standard_normal(20000)
        self.pink_scale = 1.0 / np.std(lfilter(PINK_B, PINK_A, white)[2000:])
        self.pink_zi = np.outer(np.ones(n_ch), lfilter_zi(PINK_B, PINK_A)) * 0.0
        self.dc = self.rng.uniform(-dc_offset_uv, dc_offset_uv, n_ch)
        self.line_freq, self.line_uv = line_freq, line_uv

        self.trial_n = int(trial_s * self.sfreq)
        self.iti = iti_s
        self.sample = 0
        self.next_cue = int(2.0 * self.sfreq)
        self.active: list[tuple[int, int, str]] = []  # (start, end, label)
        self.erd = erd
        self.ramp_n = max(1, int(0.3 * self.sfreq))

    @property
    def n_channels(self) -> int:
        return len(self.ch_names)

    def _envelope(self, label: str, n: int) -> np.ndarray:
        """1 - ERD depth over time for the hemisphere contralateral to ``label``."""
        idx = self.sample + np.arange(n)
        g = np.ones(n)
        for start, end, lab in self.active:
            if lab != label:
                continue
            up = np.clip((idx - start) / self.ramp_n, 0, 1)
            down = np.clip((end - idx) / self.ramp_n, 0, 1)
            g -= self.erd * np.minimum(up, down) * (idx >= start) * (idx < end)
        return g

    def _alpha_gain(self, n: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """Alpha amplitude multiplier and burst on/off events for the next n samples."""
        idx = self.sample + np.arange(n)
        on_start = self.period_n - self.burst_n
        phase = idx % self.period_n
        since_on = phase - on_start
        ramp = np.clip(np.minimum(since_on + 1, self.period_n - phase) / max(1, int(0.5 * self.sfreq)), 0, 1)
        gain = self.alpha_low + (self.alpha_high - self.alpha_low) * ramp * (since_on >= 0)
        events = [(int(i), 1) for i in np.flatnonzero(phase == on_start)]
        events += [(int(i), 2) for i in np.flatnonzero((phase == 0) & (idx > 0))]
        return gain, sorted(events)

    def next(self, n: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
        if self.paradigm == "alpha-bursts":
            alpha_gain, events = self._alpha_gain(n)
            self.next_cue = self.sample + n + 1  # no motor-imagery cues
        else:
            alpha_gain, events = 1.0, []
        while self.next_cue < self.sample + n:
            code = int(self.rng.choice(list(self.classes)))
            start = self.next_cue
            events.append((start - self.sample, code))
            # imagery starts 0.5 s after the cue
            self.active.append((start + int(0.5 * self.sfreq), start + self.trial_n, self.classes[code]))
            self.next_cue = start + self.trial_n + int(self.rng.uniform(*self.iti) * self.sfreq)
        self.active = [a for a in self.active if a[1] > self.sample]

        n_ch = len(self.names_1020)
        white = self.rng.standard_normal((n_ch, n))
        pink, self.pink_zi = lfilter(PINK_B, PINK_A, white, axis=1, zi=self.pink_zi)
        data = pink * self.pink_scale * self.noise_uv

        # "left" imagery desynchronises the right-hemisphere mu (C4) and vice versa
        data += np.outer(self.w_mu_left, self.mu_left.next(n) * self._envelope("right", n))
        data += np.outer(self.w_mu_right, self.mu_right.next(n) * self._envelope("left", n))
        data += np.outer(self.w_alpha, self.alpha.next(n) * alpha_gain)

        t = (self.sample + np.arange(n)) / self.sfreq
        common = self.line_uv * np.sin(2 * np.pi * self.line_freq * t) + 30 * np.sin(2 * np.pi * 0.05 * t)
        data += common + self.dc[:, None]
        self.sample += n
        return data, events

    def make_raw(self, duration_s: float):
        """Generate an offline recording as ``mne.io.RawArray`` with a stim channel."""
        import mne

        n = int(duration_s * self.sfreq)
        data, events = self.next(n)
        stim = np.zeros(n)
        for off, code in events:
            stim[off:off + int(0.01 * self.sfreq) + 1] = code
        info = mne.create_info(self.ch_names + ["Status"], self.sfreq, ["eeg"] * self.n_channels + ["stim"])
        return mne.io.RawArray(np.vstack([data * 1e-6, stim]), info, verbose="error")
