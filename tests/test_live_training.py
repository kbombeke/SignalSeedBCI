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
    spec = yaml.safe_load((ROOT / "pipelines" / "motor_imagery.yaml").read_text(encoding="utf-8"))
    spec["nodes"]["src"]["params"] = {"signal_name": f"EEG-{tag}", "marker_name": f"Trig-{tag}"}
    spec["nodes"]["feedback"]["params"]["name"] += f"-{tag}"
    (tmp_path / "pipelines").mkdir()
    pipe = tmp_path / "pipelines" / "mi.yaml"
    pipe.write_text(yaml.safe_dump(spec), encoding="utf-8")

    # alpha bursts give cue 1 / 2 every 2 s, alternating: both classes, whatever the timing
    src = _cli(["synth", "--paradigm", "alpha-bursts", "--burst-s", "2", "--period-s", "4",
                "--name", f"EEG-{tag}", "--marker-name", f"Trig-{tag}"], tmp_path)
    stop = tmp_path / "stop"
    train = _cli(["train", "pipelines/mi.yaml", "--live", "--save-raw", "data/calib_raw.fif"], tmp_path,
                 SIGNALSEEDBCI_STOP_FILE=str(stop))
    try:
        time.sleep(30)  # ~12 cues
        stop.touch()    # what the editor's Stop does on Windows
        out, _ = train.communicate(timeout=120)
    finally:
        src.kill()
        train.kill()
    assert train.returncode == 0, out
    assert "CV accuracy" in out and "Raw calibration data: data/calib_raw.fif" in out
    _, meta = load_model(tmp_path / "models" / "motor_imagery.joblib")
    assert set(meta["classes"]) == {"left", "right"}

    # the saved session trains the same pipeline offline, with the same epochs
    retrain = subprocess.run([sys.executable, "-m", "signalseedbci", "train", "pipelines/mi.yaml",
                              "--data", "data/calib_raw.fif"], cwd=tmp_path, capture_output=True,
                             encoding="utf-8", errors="replace", timeout=120)
    assert retrain.returncode == 0, retrain.stdout + retrain.stderr
    _, meta2 = load_model(tmp_path / "models" / "motor_imagery.joblib")
    assert meta2["input"] == meta["input"]
    assert abs(meta2["n_train"] - meta["n_train"]) <= 1
