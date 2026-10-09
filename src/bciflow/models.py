"""Built-in estimators (scikit-learn compatible) and model persistence.

Use a built-in name, or ``path/to/file.py:function`` returning an estimator.
Epoch-based estimators take X of shape (n_epochs, n_channels, n_times);
feature-based ones take (n_epochs, n_features).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import joblib


def _csp_lda():
    from mne.decoding import CSP
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.pipeline import make_pipeline

    return make_pipeline(CSP(n_components=6, reg="ledoit_wolf", log=True), LinearDiscriminantAnalysis())


def _ts_lr():
    from pyriemann.estimation import Covariances
    from pyriemann.tangentspace import TangentSpace
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    return make_pipeline(Covariances("oas"), TangentSpace(), LogisticRegression(max_iter=1000))


def _mdm():
    from pyriemann.classification import MDM
    from pyriemann.estimation import Covariances
    from sklearn.pipeline import make_pipeline

    return make_pipeline(Covariances("oas"), MDM())


def _xdawn_ts_lr():
    from pyriemann.estimation import XdawnCovariances
    from pyriemann.tangentspace import TangentSpace
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    return make_pipeline(XdawnCovariances(nfilter=4, estimator="oas"), TangentSpace(),
                         LogisticRegression(max_iter=1000))


def _lda():
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(StandardScaler(), LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto"))


BUILTIN = {
    "csp_lda": (_csp_lda, "epochs", "CSP + LDA (motor imagery)"),
    "ts_lr": (_ts_lr, "epochs", "Riemannian tangent space + logistic regression (needs pyriemann)"),
    "mdm": (_mdm, "epochs", "Riemannian minimum distance to mean (needs pyriemann)"),
    "xdawn_ts_lr": (_xdawn_ts_lr, "epochs", "xDAWN covariances + tangent space + LR, for ERPs/P300"),
    "lda": (_lda, "features", "Shrinkage LDA on feature vectors (e.g. band power)"),
}


def make_estimator(name: str):
    if name in BUILTIN:
        return BUILTIN[name][0]()
    if ":" in name:
        path, func = name.rsplit(":", 1)
        spec = importlib.util.spec_from_file_location(Path(path).stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, func)()
    raise ValueError(f"Unknown estimator {name!r}. Built-ins: {sorted(BUILTIN)} or 'file.py:function'")


def save_model(path: str | Path, estimator, meta: dict) -> None:
    joblib.dump({"estimator": estimator, "meta": meta}, path)


def load_model(path: str | Path) -> tuple[object, dict]:
    obj = joblib.load(path)
    return obj["estimator"], obj["meta"]
