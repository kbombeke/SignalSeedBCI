"""Classifier node: collects labelled epochs/features when training, predicts online."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ...models import load_model, make_estimator, save_model
from ...streams import EpochsMeta, Prediction
from ..node import Node, Param


def _describe(meta) -> dict:
    if isinstance(meta, EpochsMeta):
        return {"kind": "epochs", "ch_names": list(meta.info.ch_names), "sfreq": meta.info.sfreq,
                "n_times": meta.n_times}
    return {"kind": "features", "names": list(meta.names)}


class Classifier(Node):
    """scikit-learn / pyRiemann / MNE estimator on epochs or feature vectors.

    ``signalseedbci train`` runs the graph offline and fits the model on the labelled
    items reaching this node; ``signalseedbci run`` loads it and predicts."""

    category = "classification"
    inputs = {"in": "epochs|features"}
    outputs = {"out": "predictions"}
    params = {
        "estimator": Param("csp_lda", "str", "Built-in name (csp_lda, ts_lr, mdm, xdawn_ts_lr, lda) "
                                             "or 'file.py:function'"),
        "model_path": Param("model.joblib", "path", "Where the trained model is saved/loaded"),
        "cv_folds": Param(5, "int", "Cross-validation folds reported after training"),
    }

    def setup(self, in_meta):
        self.in_desc = _describe(in_meta["in"])
        if self.context.mode == "train":
            # shared across graphs so training can span several recordings
            self.X, self.y = self.context.store.setdefault("train", {}).setdefault(self.id, ([], []))
            self.context.store.setdefault("classifiers", {})[self.id] = self
        else:
            path = Path(self.p.model_path)
            if not path.exists():
                raise FileNotFoundError(f"{self.id}: no trained model at {path}. Train it first: "
                                        "signalseedbci train <pipeline.yaml> --data <recording>")
            self.model, meta = load_model(path)
            if meta.get("input") != self.in_desc:
                raise ValueError(f"{self.id}: model was trained on different input "
                                 f"({meta.get('input')}) than this pipeline provides ({self.in_desc})")
            self.classes = [str(c) for c in meta["classes"]]
            self.log(f"loaded {meta['estimator']} model, classes {self.classes}, "
                     f"CV accuracy {meta.get('cv_accuracy', float('nan')):.2f}")
        return {"out": None}

    @staticmethod
    def _x(item) -> np.ndarray:
        return item.data if hasattr(item, "data") else item.values

    def process(self, inputs):
        items = inputs["in"]
        if not items:
            return {"out": []}
        if self.context.mode == "train":
            for it in items:
                if it.label is not None:
                    self.X.append(self._x(it))
                    self.y.append(str(it.label))
            return {"out": []}
        X = np.stack([self._x(it) for it in items])
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(X)
            labels = [self.classes[i] for i in proba.argmax(axis=1)]
            probas = [dict(zip(self.classes, map(float, p))) for p in proba]
        else:
            labels = [str(v) for v in self.model.predict(X)]
            probas = [{}] * len(labels)
        return {"out": [Prediction(it.time, lab, pr, it.label if it.label in self.classes else None)
                        for it, lab, pr in zip(items, labels, probas)]}

    def fit(self) -> dict:
        from sklearn.model_selection import StratifiedKFold, cross_val_score

        if not self.X:
            raise RuntimeError(f"{self.id}: no labelled training data reached the classifier "
                               "(check the Epocher 'events' mapping against the recording's markers)")
        X, y = np.stack(self.X), np.array(self.y)
        classes, counts = np.unique(y, return_counts=True)
        if classes.size < 2:
            raise RuntimeError(f"{self.id}: need at least 2 classes, got {dict(zip(classes, counts))}")
        folds = int(min(self.p.cv_folds, counts.min()))
        cv_acc = float("nan")
        if folds >= 2:
            scores = cross_val_score(make_estimator(self.p.estimator), X, y,
                                     cv=StratifiedKFold(folds, shuffle=True, random_state=0))
            cv_acc = float(scores.mean())
        est = make_estimator(self.p.estimator).fit(X, y)
        meta = {"estimator": self.p.estimator, "classes": [str(c) for c in est.classes_]
                if hasattr(est, "classes_") else [str(c) for c in classes],
                "input": self.in_desc, "cv_accuracy": cv_acc, "n_train": int(len(y)),
                "class_counts": {str(k): int(v) for k, v in zip(classes, counts)}}
        Path(self.p.model_path).parent.mkdir(parents=True, exist_ok=True)
        save_model(self.p.model_path, est, meta)
        return meta


class ThresholdClassifier(Node):
    """Two-state decision on one feature (e.g. relative alpha power): high vs low.

    No training needed. In 'adaptive' mode the threshold is the midpoint between the
    10th and 90th percentile of the (smoothed) feature over the last ``history_s``
    seconds, so it calibrates itself to the user; it starts deciding once both levels
    have been seen (spread >= ``min_contrast`` x median). 'fixed' uses ``threshold``. Hysteresis and smoothing prevent flicker.
    With a markers input and ``truth`` (e.g. {'1': closed, '2': open}) it reports how
    often the decision matches the marked state."""

    category = "classification"
    inputs = {"in": "features", "markers": "markers"}
    optional_inputs = {"markers"}
    outputs = {"out": "predictions"}
    params = {
        "feature": Param("mean", "str", "'mean' of all features, or a name/regex (mean of matches), e.g. 'Oz:alpha'"),
        "mode": Param("adaptive", "choice", "Threshold from recent history, or a fixed value", choices=["adaptive", "fixed"]),
        "threshold": Param(None, "float", "Threshold for mode 'fixed'"),
        "history_s": Param(60.0, "float", "Adaptive mode: seconds of history used for calibration"),
        "min_history_s": Param(8.0, "float", "Adaptive mode: no decisions before this much history"),
        "min_contrast": Param(0.5, "float", "Adaptive mode: (high - low) / median needed before deciding"),
        "hysteresis": Param(0.15, "float", "Dead band as fraction of the low-high range (0 = none)"),
        "smooth_s": Param(0.5, "float", "Time constant of the feature smoothing (s, 0 = none)"),
        "high_label": Param("closed", "str", "Label when the feature is high"),
        "low_label": Param("open", "str", "Label when the feature is low"),
        "truth": Param(None, "dict", "Marker value -> true label, to report accuracy, e.g. {'1': closed, '2': open}"),
        "report_s": Param(10.0, "float", "Seconds between status lines in the console"),
    }

    def setup(self, in_meta):
        import re

        names = list(in_meta["in"].names)
        if self.p.feature in (None, "", "mean"):
            self.idx = list(range(len(names)))
        else:
            self.idx = [i for i, n in enumerate(names) if n == self.p.feature or re.fullmatch(self.p.feature, n)]
            if not self.idx:
                raise ValueError(f"{self.id}: no feature matches {self.p.feature!r}; available: {names}")
        if self.p.mode == "fixed" and self.p.threshold is None:
            raise ValueError(f"{self.id}: mode 'fixed' needs a threshold")
        self.hist_t: list[float] = []
        self.hist_v: list[float] = []
        self.smoothed = None
        self.last_t = None
        self.state = None
        self.truth_events: list[tuple[float, str]] = []
        self.truth = {str(k): str(v) for k, v in (self.p.truth or {}).items()}
        self.n_ok = self.n_known = 0
        self.levels = None
        self._t_report = None
        self._calibrating_logged = False
        return {"out": None}

    def _levels(self):
        if self.p.mode == "fixed":
            return None, None, float(self.p.threshold)
        if not self.hist_t or self.hist_t[-1] - self.hist_t[0] < self.p.min_history_s:
            return None
        lo, hi = np.percentile(self.hist_v, [10, 90])
        med = float(np.median(self.hist_v))
        if hi - lo <= self.p.min_contrast * max(abs(med), 1e-12):
            return None  # only one level seen so far (e.g. eyes open the whole time)
        return float(lo), float(hi), float((lo + hi) / 2)

    def _truth_at(self, t: float) -> str | None:
        label = None
        for mt, lab in self.truth_events:
            if mt <= t:
                label = lab
            else:
                break
        return label

    def process(self, inputs):
        for m in inputs.get("markers", []):
            if m.value in self.truth:
                self.truth_events.append((m.time, self.truth[m.value]))
        self.truth_events.sort()
        out = []
        for item in inputs["in"]:
            x = float(np.mean(item.values[self.idx]))
            t = float(item.time)
            if self.smoothed is None or not self.p.smooth_s:
                self.smoothed = x
            else:
                a = 1.0 - np.exp(-max(0.0, t - self.last_t) / self.p.smooth_s)
                self.smoothed += a * (x - self.smoothed)
            self.last_t = t
            self.hist_t.append(t)
            self.hist_v.append(self.smoothed)
            while self.hist_t and self.hist_t[0] < t - self.p.history_s:
                self.hist_t.pop(0)
                self.hist_v.pop(0)
            levels = self._levels()
            if levels is None:
                if not self._calibrating_logged and self.hist_t[-1] - self.hist_t[0] >= self.p.min_history_s:
                    self.log("calibrating: waiting until both high and low levels have been seen")
                    self._calibrating_logged = True
                continue
            lo, hi, thr = levels
            if self.levels is None:
                self.log(f"calibrated: threshold {thr:.3g}" + (f" (low {lo:.3g}, high {hi:.3g})" if lo is not None else ""))
            self.levels = levels
            rng = (hi - lo) if lo is not None else max(abs(thr), 1e-9)
            band = self.p.hysteresis * rng / 2
            v = self.smoothed
            if self.state is None:
                self.state = self.p.high_label if v >= thr else self.p.low_label
            elif self.state == self.p.low_label and v > thr + band:
                self.state = self.p.high_label
            elif self.state == self.p.high_label and v < thr - band:
                self.state = self.p.low_label
            p_high = float(1.0 / (1.0 + np.exp(-(v - thr) / (0.15 * rng + 1e-12))))
            truth = self._truth_at(t)
            if truth is not None:
                self.n_known += 1
                self.n_ok += truth == self.state
            out.append(Prediction(t, self.state, {self.p.high_label: p_high, self.p.low_label: 1 - p_high}, truth))
            if self._t_report is None or t - self._t_report >= self.p.report_s:
                self._t_report = t
                acc = f" | matches markers {self.n_ok / self.n_known:.0%} ({self.n_known})" if self.n_known else ""
                self.log(f"{self.state:<7} feature {v:.3g} (threshold {thr:.3g}){acc}")
        if len(self.truth_events) > 1000:
            self.truth_events = self.truth_events[-1000:]
        return {"out": out}

    def close(self):
        if self.n_known:
            self.log(f"final: decision matched the marked state {self.n_ok / self.n_known:.1%} of {self.n_known} updates")
            self.context.store.setdefault("accuracy", {})[self.id] = self.n_ok / self.n_known


class BlinkDetector(Node):
    """Detects eye blinks in frontal / EOG channels: one 'blink' prediction per blink.

    Causal and training-free: the selected channels are averaged, band-passed (0.5-10 Hz) and
    compared with a threshold. In adaptive mode that is ``threshold_sd`` times the robust
    spread (median absolute deviation) of the last ``history_s`` seconds, so it adapts to each
    person and electrode; ``threshold_uv`` sets a fixed one. After a blink, ``refractory_s``
    must pass before the next. With a markers input and ``truth`` (marker values of cued
    blinks) it reports how many blinks it caught and how many extra detections it made."""

    category = "classification"
    title = "Blink Detector"
    inputs = {"in": "signal", "markers": "markers"}
    optional_inputs = {"markers"}
    outputs = {"out": "predictions"}
    params = {
        "channels": Param(["Fp1", "Fp2"], "list", "Channels (names or regexes) averaged for detection, "
                          "e.g. [Fp1, Fp2] or [VEOG]"),
        "polarity": Param("positive", "choice", "Direction of a blink in these channels (positive at Fp1/Fp2 "
                          "against an average or mastoid reference)", choices=["positive", "negative", "both"]),
        "threshold_sd": Param(6.0, "float", "Adaptive threshold: this many robust SDs (MAD) from the median"),
        "threshold_uv": Param(None, "float", "Fixed threshold in µV instead of the adaptive one"),
        "refractory_s": Param(0.5, "float", "Minimum time between two blinks (s)"),
        "history_s": Param(20.0, "float", "Seconds of signal behind the adaptive threshold"),
        "label": Param("blink", "str", "Prediction label of a detected blink"),
        "truth": Param(None, "list", "Marker values of cued blinks, to report how many were caught, e.g. ['1']"),
        "report_s": Param(10.0, "float", "Seconds between status lines in the console"),
    }
    MIN_HISTORY_S = 3.0
    MATCH_S = (-0.15, 0.8)  # a detection this long before/after a cue marker counts for that cue

    def setup(self, in_meta):
        import re

        from scipy import signal as sps

        from .preprocessing import _StreamingSOS

        info = in_meta["in"]
        pats = [str(c) for c in (self.p.channels or [])]
        self.idx = [i for i, c in enumerate(info.ch_names) if any(c == p or re.fullmatch(p, c) for p in pats)]
        if not self.idx:
            raise ValueError(f"{self.id}: none of the channels {pats} in the signal "
                             f"(available: {', '.join(info.ch_names[:12])}{'…' if info.n_channels > 12 else ''})")
        if info.sfreq < 25:
            raise ValueError(f"{self.id}: needs at least 25 Hz, got {info.sfreq:g} Hz")
        self.sfreq = info.sfreq
        # first order: a steeper filter rings after a blink and would count it twice
        self.filt = _StreamingSOS(sps.butter(1, [0.5, 10.0], "bandpass", fs=info.sfreq, output="sos"), 1)
        self.hist = np.zeros(0)
        self.n_hist = int(self.p.history_s * info.sfreq)
        self.above = False
        self.last_blink = -np.inf
        self.truth = {str(v) for v in (self.p.truth or [])}
        self.cues: list[float] = []
        self.dets: list[float] = []
        self.n_hit = self.n_miss = self.n_extra = self.n_blinks = 0
        self._t_report = None
        return {"out": None}

    def _threshold(self) -> float | None:
        if self.p.threshold_uv is not None:
            return float(self.p.threshold_uv)
        if self.hist.size < self.MIN_HISTORY_S * self.sfreq:
            return None
        med = np.median(self.hist)
        return float(self.p.threshold_sd * 1.4826 * np.median(np.abs(self.hist - med)))

    def _score(self, now: float) -> None:
        """Match cue markers with detections once both are old enough to be final."""
        lo, hi = self.MATCH_S
        for m in [m for m in self.cues if m + hi < now]:
            match = next((d for d in self.dets if m + lo <= d <= m + hi), None)
            if match is None:
                self.n_miss += 1
            else:
                self.n_hit += 1
                self.dets.remove(match)
            self.cues.remove(m)
        old = [d for d in self.dets if d + hi - lo < now and not any(m + lo <= d <= m + hi for m in self.cues)]
        self.n_extra += len(old)
        self.dets = [d for d in self.dets if d not in old]

    def process(self, inputs):
        for m in inputs.get("markers", []):
            if m.value in self.truth:
                self.cues.append(m.time)
        out = []
        for chunk in inputs["in"]:
            if not chunk.n_samples:
                continue
            x = self.filt(chunk.data[self.idx].mean(axis=0, keepdims=True))[0]
            thr = self._threshold()
            if thr is not None:
                center = 0.0 if self.p.threshold_uv is not None else float(np.median(self.hist))
                v = x - center
                v = v if self.p.polarity == "positive" else -v if self.p.polarity == "negative" else np.abs(v)
                above = v > thr
                rising = np.flatnonzero(above & ~np.concatenate([[self.above], above[:-1]]))
                for i in rising:
                    t = float(chunk.times[i])
                    if t - self.last_blink < self.p.refractory_s:
                        continue
                    self.last_blink = t
                    self.n_blinks += 1
                    out.append(Prediction(t, self.p.label, {self.p.label: 1.0}))
                    if self.truth:
                        self.dets.append(t)
                self.above = bool(above[-1])
            self.hist = np.concatenate([self.hist, x])[-self.n_hist:]
            now = float(chunk.times[-1])
            if self.truth:
                self._score(now)
            if self._t_report is None or now - self._t_report >= self.p.report_s:
                self._t_report = now
                thr_txt = "calibrating" if thr is None else f"threshold {thr:.0f} µV"
                score = (f" | caught {self.n_hit}/{self.n_hit + self.n_miss} cued blinks, {self.n_extra} extra"
                         if self.n_hit + self.n_miss else "")
                self.log(f"{self.n_blinks} blinks, {thr_txt}{score}")
        return {"out": out}

    def close(self):
        if self.n_hit + self.n_miss:
            self.log(f"final: caught {self.n_hit} of {self.n_hit + self.n_miss} cued blinks, {self.n_extra} extra detections")
            self.context.store.setdefault("accuracy", {})[self.id] = self.n_hit / (self.n_hit + self.n_miss)
