import mne
import numpy as np
import pytest

from signalseedbci.pipeline import Graph, RunContext
from signalseedbci.pipeline.nodes.sinks import BDFRecorder
from signalseedbci.sources.synthetic import SyntheticEEG
from signalseedbci.streams import Marker, SignalChunk, SignalInfo

LSB = 0.03125


def _as_read(uv):
    """What a BDF reader returns for ActiView's header (physical -262144..262143 µV over the
    digital range -8388608..8388607): a gain just under 31.25 nV and an offset of ~-0.5 µV."""
    d = np.round(np.asarray(uv) / LSB)
    return (d + 8388608) * (524287 / 16777215) - 262144


def _header_fields(path, n):
    h = path.read_bytes()[:256 * (n + 1)]
    widths = [("label", 16), ("transducer", 80), ("dim", 8), ("pmin", 8), ("pmax", 8), ("dmin", 8),
              ("dmax", 8), ("prefilt", 80), ("ns", 8), ("reserved", 32)]
    out, off = {}, 256
    for name, w in widths:
        out[name] = [h[off + i * w:off + (i + 1) * w].decode().strip() for i in range(n)]
        off += w * n
    return h[:256], out


def _feed(rec, data, times, markers, rng):
    """Random chunk sizes; each marker is delivered one chunk after its sample (as over LSL)."""
    pos, late = 0, []
    while pos < data.shape[1]:
        n = int(rng.integers(1, 80))
        due = [m for m in markers if m.time < times[min(pos + n, data.shape[1]) - 1]]
        markers = [m for m in markers if m not in due]
        rec.process({"in": [SignalChunk(data[:, pos:pos + n], times[pos:pos + n])], "markers": late})
        late = due
        pos += n
    rec.process({"in": [], "markers": late})


def test_bdf_like_actiview_with_status_from_markers(tmp_path):
    sf, n = 512, 512 * 5 + 300  # 5 full records and an incomplete one
    info = SignalInfo(["A1", "A2", "EX1"], sf, ["eeg", "eeg", "misc"])
    rng = np.random.default_rng(0)
    data = np.round(rng.normal(0, 20000, (3, n)) / LSB) * LSB  # raw, offset-laden µV on the 31.25 nV grid
    times = 100.0 + np.arange(n) / sf
    onsets = [700, 1500, 2400]
    markers = [Marker(times[i], str(c)) for i, c in zip(onsets, (1, 2, 255))] + [Marker(times[900], "hello")]

    rec = BDFRecorder("bdf", {"path": str(tmp_path / "rec_{time}.bdf")})
    rec.setup({"in": info, "markers": None})
    _feed(rec, data, times, sorted(markers, key=lambda m: m.time), rng)
    path = rec.path
    assert "{time}" not in path.name
    rec.close()

    main, fields = _header_fields(path, 4)
    assert main[:8] == b"\xffBIOSEMI" and main[192:197] == b"24BIT"
    assert main[236:244].strip() == b"5" and main[244:252].strip() == b"1" and main[252:256].strip() == b"4"
    assert fields["label"] == ["A1", "A2", "EX1", "Status"]
    assert fields["transducer"][0] == "Active Electrode" and fields["transducer"][3] == "Triggers and Status"
    assert fields["prefilt"][0] == "HP: DC; LP: 104 Hz" and fields["prefilt"][3] == "No filtering"
    assert fields["pmin"][0] == "-262144" and fields["dmax"][0] == "8388607" and fields["ns"] == ["512"] * 4
    assert fields["reserved"] == ["MON", "MON", "MON", "TRI"]

    raw = mne.io.read_raw_bdf(path, preload=True, verbose="error")
    assert raw.n_times == 5 * sf
    np.testing.assert_allclose(raw.get_data(picks=["A1", "A2", "EX1"]) * 1e6, _as_read(data[:, :5 * sf]), atol=1e-6)
    events = mne.find_events(raw, stim_channel="Status", verbose="error")
    assert events[:, 0].tolist() == onsets and events[:, 2].tolist() == [1, 2, 255]


def test_status_channel_is_written_unchanged(tmp_path):
    sf = 256
    info = SignalInfo(["Fp1", "Status"], sf, ["eeg", "stim"])
    status = np.full(3 * sf, 0x980000)
    status[300:320] |= 7
    data = np.vstack([np.zeros(3 * sf), status])
    rec = BDFRecorder("bdf", {"path": str(tmp_path / "s.bdf")})
    rec.setup({"in": info})
    rec.process({"in": [SignalChunk(data, np.arange(3 * sf) / sf)], "markers": [Marker(0.5, "9")]})
    rec.close()
    raw = mne.io.read_raw_bdf(tmp_path / "s.bdf", preload=True, verbose="error")
    events = mne.find_events(raw, stim_channel="Status", mask=0xFFFF, mask_type="and", verbose="error")
    assert events[:, 0].tolist() == [300] and events[:, 2].tolist() == [7]
    # the status bits above the triggers are kept, as ActiView keeps them
    words = np.fromfile(tmp_path / "s.bdf", np.uint8, offset=256 * 3).reshape(-1, 2, sf, 3)[0, 1, 0]
    assert int(words[0]) | int(words[1]) << 8 | int(words[2]) << 16 == 0x980000


def test_file_is_complete_while_recording_and_never_overwritten(tmp_path):
    info = SignalInfo(["A1"], 128)
    rec = BDFRecorder("bdf", {"path": str(tmp_path / "x.bdf")})
    rec.setup({"in": info})
    rec.process({"in": [SignalChunk(np.ones((1, 128 * 4)), np.arange(128 * 4) / 128)]})
    # not closed yet (as after a crash): the records written so far can be read
    assert mne.io.read_raw_bdf(tmp_path / "x.bdf", verbose="error").n_times == 128 * 3
    rec.close()
    with pytest.raises(ValueError, match="already exists"):
        BDFRecorder("bdf", {"path": str(tmp_path / "x.bdf")}).setup({"in": info})


def test_recording_pipeline_offline(tmp_path):
    src = tmp_path / "synth_raw.fif"
    SyntheticEEG(seed=5).make_raw(20).save(src, verbose="error")
    spec = {"nodes": {"src": {"type": "FileSource", "params": {"path": str(src)}},
                      "bdf": {"type": "BDFRecorder", "params": {"path": str(tmp_path / "out.bdf")}}},
            "edges": ["src.signal -> bdf.in", "src.markers -> bdf.markers"]}
    Graph(spec, RunContext(mode="online", realtime=False)).run(verbose=False)
    orig = mne.io.read_raw_fif(src, preload=True, verbose="error")
    out = mne.io.read_raw_bdf(tmp_path / "out.bdf", preload=True, verbose="error")
    n = out.n_times
    assert n == 20 * 512
    eeg = mne.pick_types(orig.info, eeg=True)
    np.testing.assert_allclose(out.get_data(picks="eeg") * 1e6, _as_read(orig.get_data(picks=eeg)[:, :n] * 1e6),
                               atol=1e-6)
    ev_in = mne.find_events(orig, stim_channel="Status", verbose="error")
    ev_out = mne.find_events(out, stim_channel="Status", verbose="error")
    np.testing.assert_array_equal(ev_out[:, [0, 2]], ev_in[ev_in[:, 0] < n][:, [0, 2]])
