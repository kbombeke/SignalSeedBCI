"""Publish over LSL on this machine and read back through LSLSource (network stack included)."""

import time
import uuid

import numpy as np

from signalseedbci.lsl import marker_outlet, signal_outlet
from signalseedbci.pipeline.node import RunContext
from signalseedbci.pipeline.nodes.sources import LSLSource
from signalseedbci.streams import SignalInfo


def test_lsl_roundtrip_with_labels_and_markers():
    name = f"signalseedbci-test-{uuid.uuid4().hex[:8]}"
    info = SignalInfo(["A1", "A2", "EX1"], 256.0, ["eeg", "eeg", "misc"])
    eeg = signal_outlet(name, info)
    mk = marker_outlet(name + "-m")

    src = LSLSource("src", {"signal_name": name, "marker_name": name + "-m", "timeout": 5},
                    RunContext())
    meta = src.setup({})
    assert meta["signal"].ch_names == ["A1", "A2", "EX1"]
    assert meta["signal"].ch_types == ["eeg", "eeg", "misc"]
    time.sleep(0.5)

    sent = np.arange(3 * 64, dtype=np.float32).reshape(64, 3)
    got, markers = [], []
    t_end = time.monotonic() + 5
    while time.monotonic() < t_end and (sum(c.n_samples for c in got) < 64 or not markers):
        eeg.push_chunk(sent if not got else np.zeros((0, 3), np.float32))
        mk.push_sample(["42"])
        time.sleep(0.2)
        out = src.process({})
        got += out["signal"]
        markers += out["markers"]
    data = np.concatenate([c.data for c in got], axis=1)
    np.testing.assert_allclose(data[:, :64], sent.T)
    assert markers and markers[0].value == "42"
    src.close()
