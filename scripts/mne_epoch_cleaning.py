"""Example EpochScript: per-epoch cleaning with MNE.

Use in a pipeline:

    clean:
      type: EpochScript
      params:
        script: ../scripts/mne_epoch_cleaning.py
        params: {reject_uv: 150, ica_path: models/ica.fif}   # relative to the working dir

- Optionally applies a fitted MNE ICA (``mne.preprocessing.read_ica``) whose
  excluded components were chosen offline (e.g. blinks via EOG correlation).
- Rejects epochs whose peak-to-peak amplitude exceeds ``reject_uv``.
"""

import mne
import numpy as np


def process(epoch_data, meta, state):
    params = state["params"]
    if "ica" not in state:
        path = params.get("ica_path")
        state["ica"] = mne.preprocessing.read_ica(path, verbose="error") if path else None
        state["info"] = meta.info.to_mne()
    ica = state["ica"]
    if ica is not None:
        epochs = mne.EpochsArray(epoch_data[None] * 1e-6, state["info"], verbose="error")
        epoch_data = ica.apply(epochs, verbose="error").get_data()[0] * 1e6

    reject = params.get("reject_uv")
    if reject and np.ptp(epoch_data, axis=1).max() > reject:
        return None  # rejected
    return epoch_data
