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
