"""User Python script nodes: plug in MNE / SciPy / any Python code.

The script is a plain module. For ``SignalScript``::

    def setup(info, params):            # optional; may return a new SignalInfo
        ...
    def process(data, times, info, state):   # data: (n_channels, n_samples) µV
        return data                           # same number of samples

For ``EpochScript``::

    def setup(meta, params):            # optional; may return a new EpochsMeta
        ...
    def process(epoch_data, meta, state):    # (n_channels, n_times) µV
        return epoch_data                     # or None to reject the epoch

``state`` is a dict that persists between calls (for filter states, models, ...);
``state["params"]`` holds the node's ``params``.
Per-epoch processing is where whole-window MNE functions (ICA.apply, baseline,
resampling, ...) fit naturally; chunk-wise processing must be causal/stateful.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ...streams import Epoch, SignalChunk
from ..node import Node, Param


def _load(path: str):
    p = Path(path)
    spec = importlib.util.spec_from_file_location(f"bciflow_user_{p.stem}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "process"):
        raise AttributeError(f"{path}: script must define process(...)")
    return mod


class SignalScript(Node):
    """Run a user Python function on every continuous chunk."""

    category = "scripting"
    inputs = {"in": "signal"}
    outputs = {"out": "signal"}
    params = {
        "script": Param(None, "path", "Python file defining process()", required=True),
        "params": Param(None, "dict", "Passed to the script's setup()"),
    }

    def setup(self, in_meta):
        self.mod = _load(self.p.script)
        self.info = in_meta["in"]
        self.state = {"params": dict(self.p.params or {})}
        if hasattr(self.mod, "setup"):
            self.info = self.mod.setup(self.info, dict(self.p.params or {})) or self.info
        return {"out": self.info}

    def process(self, inputs):
        out = []
        for c in inputs["in"]:
            y = self.mod.process(c.data, c.times, self.info, self.state)
            out.append(SignalChunk(y, c.times))
        return {"out": out}


class EpochScript(Node):
    """Run a user Python function on every epoch (return None to reject it)."""

    category = "scripting"
    inputs = {"in": "epochs"}
    outputs = {"out": "epochs"}
    params = SignalScript.params

    def setup(self, in_meta):
        self.mod = _load(self.p.script)
        self.meta = in_meta["in"]
        self.state = {"params": dict(self.p.params or {})}
        if hasattr(self.mod, "setup"):
            self.meta = self.mod.setup(self.meta, dict(self.p.params or {})) or self.meta
        return {"out": self.meta}

    def process(self, inputs):
        out = []
        for ep in inputs["in"]:
            y = self.mod.process(ep.data, self.meta, self.state)
            if y is not None:
                out.append(Epoch(y, ep.time, ep.label))
        return {"out": out}
