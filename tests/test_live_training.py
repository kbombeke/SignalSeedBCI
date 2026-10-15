"""Live calibration (train --live): collect on LSL until stopped, fit, keep the raw session."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml

from signalseedbci.models import load_model

ROOT = Path(__file__).resolve().parents[1]


def _cli(args, cwd, **env):
    return subprocess.Popen([sys.executable, "-u", "-m", "signalseedbci", *args], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
                            errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8", **env))


def test_live_training_stops_fits_and_saves_raw(tmp_path):
    tag = uuid.uuid4().hex[:6]
    spec = yaml.safe_load((ROOT / "pipelines" / "inear_gaze.yaml").read_text(encoding="utf-8"))
    spec["nodes"]["src"]["params"] = {"signal_name": f"EEG-{tag}", "marker_name": f"Trig-{tag}"}
    (tmp_path / "pipelines").mkdir()
    pipe = tmp_path / "pipelines" / "gaze.yaml"
    pipe.write_text(yaml.safe_dump(spec), encoding="utf-8")

    src = _cli(["synth", "--paradigm", "eye-movements", "--name", f"EEG-{tag}", "--marker-name", f"Trig-{tag}"],
               tmp_path)
    stop = tmp_path / "stop"
    train = _cli(["train", "pipelines/gaze.yaml", "--live", "--save-raw", "data/calib_raw.fif"], tmp_path,
                 SIGNALSEEDBCI_STOP_FILE=str(stop))
    try:
        time.sleep(35)  # ~8 cued trials, every direction at least once
        stop.touch()    # what the editor's Stop does on Windows
        out, _ = train.communicate(timeout=120)
    finally:
        src.kill()
        train.kill()
    assert train.returncode == 0, out
    assert "CV accuracy" in out and "Raw calibration data: data/calib_raw.fif" in out
    _, meta = load_model(tmp_path / "models" / "inear_gaze.joblib")
    assert set(meta["classes"]) == {"up", "down", "left", "right"}

    # the saved session trains the same pipeline offline, with the same epochs
    retrain = subprocess.run([sys.executable, "-m", "signalseedbci", "train", "pipelines/gaze.yaml",
                              "--data", "data/calib_raw.fif"], cwd=tmp_path, capture_output=True,
                             encoding="utf-8", errors="replace", timeout=120)
    assert retrain.returncode == 0, retrain.stdout + retrain.stderr
    _, meta2 = load_model(tmp_path / "models" / "inear_gaze.joblib")
    assert meta2["input"] == meta["input"]
    assert abs(meta2["n_train"] - meta["n_train"]) <= 1
