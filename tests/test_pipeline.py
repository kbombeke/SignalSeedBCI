import time
from pathlib import Path

import numpy as np
import pytest
import yaml

from signalseedbci.pipeline import Graph, RunContext
from signalseedbci.pipeline.nodes.epochs import Epocher
from signalseedbci.pipeline.nodes.preprocessing import BandpassFilter, Decimate, NotchFilter
from signalseedbci.sources.synthetic import SyntheticEEG
from signalseedbci.streams import Marker, SignalChunk, SignalInfo

INFO = SignalInfo([f"C{i}" for i in range(4)], 512.0)


def _chunked(node, data, rng):
    times = np.arange(data.shape[1]) / INFO.sfreq
    pos, outs, ts = 0, [], []
    while pos < data.shape[1]:
        n = int(rng.integers(1, 60))
        c = node.process({"in": [SignalChunk(data[:, pos:pos + n], times[pos:pos + n])]})["out"][0]
        outs.append(c.data)
        ts.append(c.times)
        pos += n
    return np.concatenate(outs, axis=1), np.concatenate(ts)


@pytest.mark.parametrize("make", [
    lambda: BandpassFilter("f", {"l_freq": 8, "h_freq": 30}),
    lambda: NotchFilter("n", {"freqs": [50, 100]}),
    lambda: Decimate("d", {"target_sfreq": 128}),
])
def test_streaming_equals_one_shot(make):
    rng = np.random.default_rng(0)
    data = rng.standard_normal((4, 5000)) * 10 + 100
    a, b = make(), make()
    a.setup({"in": INFO})
    b.setup({"in": INFO})
    one, t_one = _chunked(a, data, np.random.default_rng(1))
    whole = b.process({"in": [SignalChunk(data, np.arange(5000) / 512.0)]})["out"][0]
    np.testing.assert_allclose(one, whole.data, atol=1e-9)
    np.testing.assert_allclose(t_one, whole.times)


def test_epocher_waits_for_data_and_cuts_exact_window():
    ep = Epocher("e", {"events": {"7": "target"}, "tmin": -0.1, "tmax": 0.4})
    ep.setup({"in": INFO})
    t = np.arange(1024) / 512.0
    data = np.tile(np.arange(1024.0), (4, 1))
    # marker arrives before the data covering it
    out = ep.process({"in": [SignalChunk(data[:, :300], t[:300])], "markers": [Marker(0.5, "7"), Marker(0.6, "9")]})
    assert out["out"] == []
    out = ep.process({"in": [SignalChunk(data[:, 300:], t[300:])], "markers": []})["out"]
    assert len(out) == 1 and out[0].label == "target"
    assert out[0].data.shape == (4, 256)
    assert out[0].data[0, 0] == round((0.5 - 0.1) * 512)


def _write_pipeline(tmp_path, estimator="csp_lda"):
    spec = yaml.safe_load(open("pipelines/motor_imagery.yaml"))
    spec["nodes"]["clf"]["params"] = {"estimator": estimator, "model_path": str(tmp_path / "m.joblib")}
    del spec["nodes"]["feedback"]
    spec["edges"] = [e for e in spec["edges"] if "feedback" not in e]
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(spec))
    return p


def _source(path):
    return {"src": {"type": "FileSource", "params": {"path": str(path)}}}


@pytest.mark.parametrize("estimator", ["csp_lda", "ts_lr"])
def test_train_then_pseudo_online(tmp_path, estimator):
    calib, test = tmp_path / "calib_raw.fif", tmp_path / "test_raw.fif"
    SyntheticEEG(seed=10).make_raw(300).save(calib, verbose="error")
    SyntheticEEG(seed=11).make_raw(150).save(test, verbose="error")
    pipe = _write_pipeline(tmp_path, estimator)

    ctx = RunContext(mode="train", realtime=False)
    Graph.from_yaml(pipe, ctx, _source(calib)).run(verbose=False)
    meta = ctx.store["classifiers"]["clf"].fit()
    assert meta["n_train"] > 30 and meta["cv_accuracy"] > 0.7

    ctx = RunContext(mode="online", realtime=False)
    Graph.from_yaml(pipe, ctx, _source(test)).run(verbose=False)
    assert ctx.store["accuracy"]["log"] > 0.7


def test_graph_rejects_bad_connections():
    spec = {"nodes": {"a": {"type": "SlidingWindow"}, "b": {"type": "BandpassFilter"}},
            "edges": ["a.out -> b.in"]}
    with pytest.raises(ValueError, match="cannot connect epochs"):
        Graph(spec)
    with pytest.raises(ValueError, match="unknown type"):
        Graph({"nodes": {"x": {"type": "Nope"}}})
    with pytest.raises(ValueError, match="unknown parameter"):
        Graph({"nodes": {"x": {"type": "BandpassFilter", "params": {"lfreq": 1}}}})


def test_renamed_node_type_still_loads():
    from signalseedbci.pipeline import REGISTRY
    from signalseedbci.pipeline.nodes.sinks import DataVisualizer

    graph = Graph({"nodes": {"src": {"type": "LSLSource"}, "out": {"type": "LSLSignalOut"}},
                   "edges": ["src.signal -> out.in"]})
    assert type(graph.nodes["out"]) is DataVisualizer and "LSLSignalOut" not in REGISTRY
    assert DataVisualizer.schema()["title"] == "Data Visualizer"


def test_model_input_mismatch_is_reported(tmp_path):
    calib = tmp_path / "calib_raw.fif"
    SyntheticEEG(seed=12).make_raw(200).save(calib, verbose="error")
    pipe = _write_pipeline(tmp_path)
    ctx = RunContext(mode="train", realtime=False)
    Graph.from_yaml(pipe, ctx, _source(calib)).run(verbose=False)
    ctx.store["classifiers"]["clf"].fit()

    spec = yaml.safe_load(pipe.read_text())
    spec["nodes"]["epochs"]["params"]["tmax"] = 3.0
    pipe.write_text(yaml.safe_dump(spec))
    with pytest.raises(ValueError, match="trained on different input"):
        Graph.from_yaml(pipe, RunContext(mode="online", realtime=False), _source(calib)).run(verbose=False)


def test_alpha_bursts_paradigm():
    from scipy.signal import welch

    gen = SyntheticEEG(seed=3, paradigm="alpha-bursts", burst_s=10, period_s=20)
    chunks, events, pos = [], [], 0
    for n in [37] * 1000 + [1000] * 50:  # mixed chunk sizes must give the same schedule
        d, ev = gen.next(n)
        chunks.append(d)
        events += [(pos + off, code) for off, code in ev]
        pos += n
    data = np.concatenate(chunks, axis=1)
    sf = gen.sfreq
    assert [(round(i / sf, 3), c) for i, c in events[:4]] == [(10.0, 1), (20.0, 2), (30.0, 1), (40.0, 2)]
    oz = gen.names_1020.index("Oz")
    x = data[oz] - data.mean(axis=0)

    def alpha(t0):
        f, p = welch(x[int(t0 * sf):int((t0 + 6) * sf)], sf, nperseg=int(sf))
        return p[(f >= 8) & (f <= 12)].mean()

    assert alpha(12) > 10 * alpha(2) and alpha(32) > 10 * alpha(22)


def test_lsl_signal_out_republishes_markers():
    import uuid

    import pylsl

    from signalseedbci.pipeline.nodes.sinks import DataVisualizer
    from signalseedbci.streams import FeaturesMeta, Features

    name = f"signalseedbci-test-{uuid.uuid4().hex[:6]}"
    node = DataVisualizer("out", {"name": name})
    node.setup({"in": FeaturesMeta(["a", "b"]), "markers": None})
    info = pylsl.resolve_byprop("name", name + "-markers", timeout=5)
    assert info, "marker stream not published"
    inlet = pylsl.StreamInlet(info[0])
    inlet.open_stream(timeout=5)
    time.sleep(0.3)
    t = pylsl.local_clock()
    node.process({"in": [Features(np.array([1.0, 2.0]), t)], "markers": [Marker(t, "1")]})
    vals, ts = inlet.pull_chunk(timeout=3.0)
    assert vals and vals[0][0] == "1" and abs(ts[0] - t) < 1e-3


def test_spectrum_out_peak_labels_and_limits():
    import uuid

    import pylsl

    from signalseedbci.pipeline.nodes.sinks import SpectrumOut

    sf = 256.0
    info = SignalInfo(["Oz", "Pz"], sf)
    name = f"signalseedbci-test-{uuid.uuid4().hex[:6]}"
    node = SpectrumOut("spec", {"name": name, "window_s": 2, "step_s": 0.25, "fmin": 1, "fmax": 40})
    node.setup({"in": info})
    assert len(node.freqs) == 40 and node.freqs[0] == 1 and node.freqs[-1] == 40
    found = pylsl.resolve_byprop("name", name, timeout=5)
    inlet = pylsl.StreamInlet(found[0])
    labels = [c.child_value("label") for c in [inlet.info().desc().child("channels").child("channel")]]
    assert labels[0] == "Oz@1"
    inlet.open_stream(timeout=5)
    time.sleep(0.3)
    t = np.arange(int(4 * sf)) / sf
    x = np.vstack([20 * np.sin(2 * np.pi * 10 * t), 5 * np.sin(2 * np.pi * 22 * t)])
    x += np.random.default_rng(0).standard_normal(x.shape)
    for i in range(0, x.shape[1], 32):  # stream in chunks
        node.process({"in": [SignalChunk(x[:, i:i + 32], t[i:i + 32])], "markers": []})
    vals, _ = inlet.pull_chunk(timeout=3.0, as_numpy=True)
    assert len(vals) >= 6  # 2 s of data after the first full window, one spectrum per 0.25 s
    spec = np.asarray(vals[-1]).reshape(2, 40)
    assert node.freqs[spec[0].argmax()] == 10 and node.freqs[spec[1].argmax()] == 22

    with pytest.raises(ValueError, match="values"):
        SpectrumOut("big", {"name": name + "x", "resolution_hz": 0.1, "window_s": 10, "fmax": 100}).setup(
            {"in": SignalInfo([f"c{i}" for i in range(16)], 512.0)})


def test_threshold_classifier_adaptive_hysteresis_and_truth():
    from signalseedbci.pipeline.nodes.classify import ThresholdClassifier
    from signalseedbci.streams import Features, FeaturesMeta

    rng = np.random.default_rng(0)
    node = ThresholdClassifier("state", {"truth": {"1": "closed", "2": "open"}, "smooth_s": 0.5})
    node.setup({"in": FeaturesMeta(["Oz:alpha", "Pz:alpha"]), "markers": None})
    preds, t = [], 0.0
    # 20 s low (open), then alternating 10 s high / 10 s low, 4 Hz updates with noise
    schedule = [(0, 20, 1.8)] + [(s, s + 10, 6.0 if (s // 10) % 2 == 0 else 1.8) for s in range(20, 100, 10)]
    markers = [Marker(float(s), "1" if lvl > 3 else "2") for s, _, lvl in schedule[1:]]
    for s0, s1, lvl in schedule:
        while t < s1:
            vals = lvl + 0.3 * rng.standard_normal(2)
            mk = [m for m in markers if t - 0.25 < m.time <= t]
            preds += node.process({"in": [Features(vals, t)], "markers": mk})["out"]
            t += 0.25
    first = preds[0].time
    assert first >= 20, "must not decide before a high level has been seen"
    labels = [p.label for p in preds]
    flips = sum(a != b for a, b in zip(labels, labels[1:]))
    assert flips <= 8 + 1, f"hysteresis should prevent flicker ({flips} flips)"
    assert node.n_ok / node.n_known > 0.85
    for s0, s1, lvl in schedule[2:]:  # from the second block on, well inside each block
        inside = [p.label for p in preds if s0 + 3 <= p.time < s1]
        assert inside and set(inside) == {"closed" if lvl > 3 else "open"}, (s0, inside)


def test_threshold_classifier_fixed():
    from signalseedbci.pipeline.nodes.classify import ThresholdClassifier
    from signalseedbci.streams import Features, FeaturesMeta

    node = ThresholdClassifier("s", {"mode": "fixed", "threshold": 3.0, "smooth_s": 0, "hysteresis": 0,
                                     "feature": "Oz:alpha"})
    node.setup({"in": FeaturesMeta(["Oz:alpha", "Pz:alpha"])})
    out = node.process({"in": [Features(np.array([5.0, 0.0]), 0.0), Features(np.array([1.0, 9.0]), 0.25)],
                        "markers": []})["out"]
    assert [p.label for p in out] == ["closed", "open"] and out[0].proba["closed"] > 0.9
    with pytest.raises(ValueError, match="threshold"):
        ThresholdClassifier("x", {"mode": "fixed"}).setup({"in": FeaturesMeta(["a"])})


def test_eyes_pipeline_on_alpha_bursts(tmp_path):
    rec = tmp_path / "bursts_raw.fif"
    SyntheticEEG(seed=5, paradigm="alpha-bursts").make_raw(120).save(rec, verbose="error")
    spec = yaml.safe_load(open("pipelines/eyes_alpha.yaml"))
    spec["nodes"]["eyes"]["params"]["name"] = f"eyes-test-{tmp_path.name[-6:]}"
    spec["nodes"]["alphaplot"]["params"]["name"] = f"alpha-test-{tmp_path.name[-6:]}"
    p = tmp_path / "eyes.yaml"
    p.write_text(yaml.safe_dump(spec))
    ctx = RunContext(mode="online", realtime=False)
    Graph.from_yaml(p, ctx, _source(rec)).run(verbose=False)
    assert ctx.store["accuracy"]["state"] > 0.85


def test_time_window_means():
    from signalseedbci.pipeline.nodes.features import TimeWindowMeans
    from signalseedbci.streams import Epoch, EpochsMeta

    info = SignalInfo(["a", "b"], 100.0)
    node = TimeWindowMeans("m", {"windows": [[0.0, 0.5], [0.5, 1.0]]})
    meta = node.setup({"in": EpochsMeta(info, -0.2, 120)})["out"]
    assert meta.names == ["a:0-0.5s", "b:0-0.5s", "a:0.5-1s", "b:0.5-1s"]
    data = np.vstack([np.r_[np.zeros(20), np.ones(50), 3 * np.ones(50)], np.full(120, 2.0)])
    f = node.process({"in": [Epoch(data, 1.0, "x")]})["out"][0]
    np.testing.assert_allclose(f.values, [1.0, 2.0, 3.0, 2.0])
    with pytest.raises(ValueError, match="outside the epoch"):
        TimeWindowMeans("m", {"windows": [[0.5, 2.0]]}).setup({"in": EpochsMeta(info, -0.2, 120)})


EXP2 = "pipelines/Exp2_replay_attempt1.yaml"
EXP2_CHANNELS = (["R_CAN_1", "R_CON_1", "R_CAN_2", "R_CON_2", "R_CAN_3", "R_CON_3",
                  "L_CON_1", "L_CAN_1", "L_CON_2", "L_CAN_2", "L_CON_3", "L_CAN_3",
                  "L_MASTOID", "R_MASTOID", "L_EARLOBE", "R_EARLOBE"] + [f"A{i}" for i in range(17, 33)])


def _exp2_spec(tmp_path):
    """The Exp2 pipeline with a unique output name and a Recorder on what it publishes."""
    spec = yaml.safe_load(open(EXP2, encoding="utf-8"))
    spec["nodes"]["eog"]["params"]["name"] += f"-test-{tmp_path.name[-6:]}"
    spec["nodes"]["rec"] = {"type": "Recorder", "params": {"path": str(tmp_path / "out_raw.fif")}}
    spec["edges"] += ["lowpass.out -> rec.in", "src.markers -> rec.markers"]
    p = tmp_path / "exp2.yaml"
    p.write_text(yaml.safe_dump(spec))
    return p


def test_exp2_replay_pipeline(tmp_path):
    import mne

    # a recording with the Exp2 montage: a slow, large drift common to every electrode
    # (as with BioSemi's reference-free recording) on top of each channel's own 2 Hz wave
    sf, n = 512.0, 512 * 30
    t = np.arange(n) / sf
    own = 20 * np.sin(2 * np.pi * 2 * t[None, :] + np.arange(len(EXP2_CHANNELS))[:, None])
    common = 5000 + 300 * np.sin(2 * np.pi * 0.05 * t)
    stim = np.zeros(n)
    stim[int(5 * sf):int(5 * sf) + 6] = 12
    info = mne.create_info(EXP2_CHANNELS + ["Status"], sf, ["eeg"] * len(EXP2_CHANNELS) + ["stim"])
    rec = tmp_path / "exp2_raw.fif"
    mne.io.RawArray(np.vstack([(own + common) * 1e-6, stim]), info, verbose="error").save(rec, verbose="error")

    Graph.from_yaml(_exp2_spec(tmp_path), RunContext(mode="online", realtime=False), _source(rec)).run(verbose=False)
    out = mne.io.read_raw(tmp_path / "out_raw.fif", preload=True, verbose="error")
    assert out.info["sfreq"] == 128.0
    assert out.ch_names == [c for c in EXP2_CHANNELS if c != "L_MASTOID"]  # referenced to it, then dropped
    assert [a["description"] for a in out.annotations] == ["12"]
    x = out.get_data()[:, int(5 * 128):] * 1e6  # after the filters have settled
    expected = np.delete(own - own[EXP2_CHANNELS.index("L_MASTOID")], EXP2_CHANNELS.index("L_MASTOID"), axis=0)
    assert np.abs(x).max() < 60  # the common drift is gone
    # each channel keeps its own wave minus the mastoid's (the causal filters only delay it)
    np.testing.assert_allclose(x.std(axis=1), expected.std(axis=1), rtol=0.1)


@pytest.mark.skipif(not list(Path("data").glob("*.bdf")), reason="no BDF recording in data/")
def test_exp2_pipeline_accepts_the_recorded_session(tmp_path):
    rec = sorted(Path("data").glob("*.bdf"))[0].resolve()
    graph = Graph.from_yaml(_exp2_spec(tmp_path), RunContext(mode="online", realtime=False), _source(rec))
    graph.setup()
    assert graph.meta[("lowpass", "out")].sfreq == 128.0
    assert "L_MASTOID" not in graph.meta[("lowpass", "out")].ch_names
    graph.close()


def test_rename_channels_mapping_checks(capsys):
    from signalseedbci.pipeline.nodes.preprocessing import RenameChannels

    node = RenameChannels("names", {"mapping": {"C0": "L_ear", "C2": "R_ear", "Nope": "X"}})
    out = node.setup({"in": INFO})["out"]
    assert out.ch_names == ["L_ear", "C1", "R_ear", "C3"]
    assert "not in the stream, ignored: Nope" in capsys.readouterr().out
    with pytest.raises(ValueError, match="more than once"):
        RenameChannels("names", {"mapping": {"C0": "C1"}}).setup({"in": INFO})
