import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from signalseedbci.gui.server import App, dump_pipeline, make_handler, validate_spec

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pipelines").mkdir()
    shutil.copy(ROOT / "pipelines" / "motor_imagery.yaml", tmp_path / "pipelines")
    return tmp_path


@pytest.fixture
def server(project):
    app = App(project)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://localhost:{httpd.server_address[1]}", app
    app.procs.stop_all()
    httpd.shutdown()


def call(base, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_dump_keeps_header_comments_and_roundtrips(project):
    path = project / "pipelines" / "motor_imagery.yaml"
    old = path.read_text()
    spec = yaml.safe_load(old)
    spec["layout"] = {nid: [i * 10, 0] for i, nid in enumerate(spec["nodes"])}
    text = dump_pipeline(spec, old)
    assert text.startswith("# Two-class motor imagery")
    again = yaml.safe_load(text)
    assert again["nodes"] == spec["nodes"] and again["edges"] == spec["edges"]
    assert list(again)[:3] == ["name", "nodes", "edges"]
    # resized nodes store [x, y, width, view height]
    spec["layout"]["clf"] = [10, 20, 480, 300]
    assert yaml.safe_load(dump_pipeline(spec, old))["layout"]["clf"] == [10, 20, 480, 300]


def test_validate_reports_structure_and_untrained_model(project):
    spec = yaml.safe_load((project / "pipelines" / "motor_imagery.yaml").read_text())
    res = validate_spec(spec, project / "pipelines")
    assert res["ok"] and any("not trained yet" in w for w in res["warnings"])
    spec["edges"].append("epochs.out -> band.in")
    res = validate_spec(spec, project / "pipelines")
    assert not res["ok"] and "cannot connect epochs" in res["errors"][0]


def test_api_basics_and_security(server):
    base, app = server
    code, nodes = call(base, "/api/nodes")
    assert code == 200 and any(n["type"] == "Classifier" for n in nodes["nodes"])
    assert "csp_lda" in nodes["estimators"]
    code, res = call(base, "/api/pipelines")
    assert res["pipelines"] == ["pipelines/motor_imagery.yaml"]
    # DNS-rebinding style request with a foreign Host header is refused
    assert call(base, "/api/pipelines", headers={"Host": "evil.example"})[0] == 403
    # writes outside the project are refused
    code, res = call(base, "/api/pipeline", {"path": "../outside.yaml", "spec": {"nodes": {}}})
    assert code == 400 and "outside the project" in res["error"]
    # form posts (no JSON content type) are refused
    req = urllib.request.Request(base + "/api/process/start", data=b"kind=synth")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 415


def test_save_then_load(server):
    base, app = server
    spec = {"name": "t", "nodes": {"src": {"type": "LSLSource"}, "log": {"type": "Logger"}},
            "edges": ["src.signal -> log.in"], "layout": {"src": [0, 0], "log": [250, 0]}}
    code, res = call(base, "/api/pipeline", {"path": "pipelines/sub/t", "spec": spec})
    assert code == 200 and res["path"] == "pipelines/sub/t.yaml"
    code, res = call(base, "/api/pipeline?path=pipelines/sub/t.yaml")
    assert res["spec"]["edges"] == spec["edges"] and res["spec"]["layout"]["log"] == [250, 0]


def test_process_synth_record_and_log(server):
    base, app = server
    code, p = call(base, "/api/process/start",
                   {"kind": "synth", "options": {"record": "data/gui_raw.fif", "duration": 20}})
    assert code == 200 and p["running"]
    for _ in range(120):
        code, log = call(base, f"/api/log?id={p['id']}&since=0")
        if not log["running"]:
            break
        time.sleep(0.25)
    text = "\n".join(log["lines"])
    assert "Wrote 20 s synthetic recording" in text and "exited with code 0" in text
    assert (app.project / "data" / "gui_raw.fif").exists()
    code, files = call(base, "/api/files")
    assert "data/gui_raw.fif" in files["files"]


def _wait(base, pid, pred, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, log = call(base, f"/api/log?id={pid}&since=0")
        if pred(log):
            return log
        time.sleep(0.3)
    raise AssertionError(f"timeout; log so far: {log['lines'][-8:]}")


def test_session_starts_linked_source_with_pipeline_stream_names(server):
    import uuid

    base, app = server
    tag = uuid.uuid4().hex[:6]
    spec = {"name": "s", "nodes": {
        "src": {"type": "LSLSource", "params": {"signal_name": f"EEG-{tag}", "marker_name": f"Trig-{tag}"}},
        "log": {"type": "Logger", "params": {"every_s": 0.5}}}, "edges": ["src.signal -> log.in"]}
    call(base, "/api/pipeline", {"path": "pipelines/s.yaml", "spec": spec})
    code, r = call(base, "/api/session/start", {"pipeline": "pipelines/s.yaml", "source": "synthetic",
                                                "options": {"paradigm": "alpha-bursts"}})
    assert code == 200 and r["source"]["running"] and r["warnings"] == []
    assert f"EEG-{tag}" in r["source"]["args"] and "alpha-bursts" in r["source"]["args"]
    _wait(base, r["run"]["id"], lambda log: any(f"connected to 'EEG-{tag}'" in l for l in log["lines"]))
    _wait(base, r["run"]["id"], lambda log: any("SignalChunk items" in l for l in log["lines"]))
    call(base, "/api/process/stop", {"id": r["run"]["id"]})
    _wait(base, r["source"]["id"], lambda log: not log["running"])  # linked: source stops too


def test_session_offline_and_errors(server):
    base, app = server
    from signalseedbci.sources.synthetic import SyntheticEEG
    (app.project / "data").mkdir(exist_ok=True)
    SyntheticEEG(seed=1).make_raw(30).save(app.project / "data" / "x_raw.fif", verbose="error")
    spec = {"name": "f", "nodes": {"src": {"type": "FileSource", "params": {"path": "../data/x_raw.fif"}},
                                   "log": {"type": "Logger"}}, "edges": ["src.markers -> log.in"]}
    call(base, "/api/pipeline", {"path": "pipelines/f.yaml", "spec": spec})
    code, r = call(base, "/api/session/start", {"pipeline": "pipelines/f.yaml", "source": "synthetic"})
    assert code == 400 and "no LSLSource" in r["error"]
    code, r = call(base, "/api/session/start", {"pipeline": "pipelines/f.yaml", "source": "offline",
                                                "options": {"file": "data/x_raw.fif"}})
    assert code == 200 and r["source"] is None
    log = _wait(base, r["run"]["id"], lambda log: not log["running"])
    assert "[exited with code 0]" in log["lines"][-1]
    code, r = call(base, "/api/session/start", {"pipeline": "pipelines/f.yaml", "source": "replay",
                                                "options": {"file": "data/missing.fif"}})
    assert code in (400, 404)


def test_save_then_load_non_ascii(server):
    # Windows' default file encoding is not UTF-8: pipelines must still round-trip
    base, app = server
    spec = {"name": "Oogbewegingen – µV → gaze", "nodes": {"src": {"type": "LSLSource"},
            "log": {"type": "Logger"}}, "edges": ["src.signal -> log.in"]}
    code, _ = call(base, "/api/pipeline", {"path": "pipelines/u.yaml", "spec": spec})
    assert code == 200
    assert "µV →" in (app.project / "pipelines" / "u.yaml").read_text(encoding="utf-8")
    code, res = call(base, "/api/pipeline?path=pipelines/u.yaml")
    assert code == 200 and res["spec"]["name"] == spec["name"]


def test_stop_file_ends_process_cleanly(server):
    # how the editor stops processes on Windows; works the same on every platform
    base, app = server
    code, p = call(base, "/api/process/start", {"kind": "synth", "options": {"paradigm": "alpha-bursts"}})
    assert code == 200
    proc = app.procs.procs[p["id"]]
    assert not proc.stop_file.exists()
    _wait(base, p["id"], lambda log: any("Synthetic EEG" in l for l in log["lines"]))
    proc.stop_file.touch()
    log = _wait(base, p["id"], lambda log: not log["running"], timeout=15)
    assert log["lines"][-1] == "[exited with code 0]"
    assert not proc.stop_file.exists()


def test_session_live_training(server):
    base, app = server
    code, r = call(base, "/api/session/start", {"pipeline": "pipelines/motor_imagery.yaml", "source": "offline",
                                                "options": {"file": "x"}, "train": True})
    assert code == 400 and "streaming data" in r["error"]
    code, r = call(base, "/api/session/start", {"pipeline": "pipelines/motor_imagery.yaml", "source": "synthetic",
                                                "options": {"paradigm": "motor-imagery"}, "train": True})
    assert code == 200 and r["run"]["kind"] == "train"
    assert r["run"]["args"][:3] == ["train", "pipelines/motor_imagery.yaml", "--live"]
    assert r["run"]["label"].startswith("train motor_imagery")
    call(base, "/api/process/stop", {"id": r["source"]["id"]})  # linked: stops the training too
    _wait(base, r["run"]["id"], lambda log: not log["running"])


def test_channels_of_a_recording(server):
    base, app = server
    from signalseedbci.sources.synthetic import SyntheticEEG
    (app.project / "data").mkdir(exist_ok=True)
    raw = SyntheticEEG(seed=1).make_raw(5)
    raw.save(app.project / "data" / "c_raw.fif", verbose="error")
    code, r = call(base, "/api/channels?file=data/c_raw.fif")
    stim = [c for c, t in zip(raw.ch_names, raw.get_channel_types()) if t == "stim"]
    assert code == 200 and r["channels"] == [c for c in raw.ch_names if c not in stim]
    assert call(base, "/api/channels?file=../x.bdf")[0] in (400, 403)
    code, r = call(base, "/api/channels?stream=NoSuchStream-xyz")
    assert code == 404 and "not running" in r["error"]


def test_viewer_follows_a_new_stream_with_the_same_name(tmp_path):
    # a pipeline stops and another one publishes the same output name with other channels
    import subprocess
    import sys
    import uuid

    from signalseedbci.gui.server import Monitor
    from signalseedbci.sources.synthetic import SyntheticEEG

    raw = SyntheticEEG(seed=1).make_raw(60).pick([0, 1, 2])  # 3 channels instead of 32
    raw.save(tmp_path / "three_raw.fif", verbose="error")
    name, mon = f"view-{uuid.uuid4().hex[:6]}", Monitor()

    def start(*args):
        return subprocess.Popen([sys.executable, "-m", "signalseedbci", *args, "--name", name, "--marker-name", "none"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def channels_with_fresh_data(timeout=20):
        t0 = time.time()
        while time.time() - t0 < timeout:
            d = mon.watch(name, 0)
            if d.get("times") and d["now"] - d["times"][-1] < 0.5:
                return len(d["ch_names"])
            time.sleep(0.3)
        return None

    first = start("synth", "--paradigm", "alpha-bursts")
    try:
        assert channels_with_fresh_data() == 32
    finally:
        first.kill()
        first.wait()
    time.sleep(1.0)
    second = start("replay", str(tmp_path / "three_raw.fif"))
    try:
        assert channels_with_fresh_data() == 3
    finally:
        second.kill()
        mon.unwatch(name)
