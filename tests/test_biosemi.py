import numpy as np
import pytest

from bciflow.bridge.actiview import decode_24bit
from bciflow.bridge.biosemi import (LSB_UV, SYNC, FakeActiveTwo, FrameParser, RingReader, channel_labels,
                                    decode_status, encode_status, frame_channels, frames_to_uv)


@pytest.mark.parametrize("speedmode", range(10))
@pytest.mark.parametrize("mk2", [False, True])
def test_status_roundtrip(speedmode, mk2):
    w = encode_status(0xA5, speedmode, mk2, cms_in_range=False, battery_low=True)
    st = decode_status(w)
    assert st == {"trigger": 0xA5, "new_epoch": False, "speedmode": speedmode,
                  "cms_in_range": False, "battery_low": True, "mk2": mk2}


def test_labels_mk2_speedmode4():
    labels = channel_labels(True, 4, frame_channels(True, 4))
    assert len(labels) == 280
    assert labels[0] == "A1" and labels[31] == "A32" and labels[32] == "B1" and labels[255] == "H32"
    assert labels[256:264] == [f"EX{i}" for i in range(1, 9)]


def _collect(dev, parser, reader, steps, rng):
    frames = []
    for _ in range(steps):
        dev.advance(int(rng.integers(1, 40)))
        raw = reader.read()
        # feed in odd-sized pieces, not aligned to words or frames
        cut = sorted(rng.integers(0, len(raw) + 1, size=3))
        for a, b in zip([0, *cut], [*cut, len(raw)]):
            f = parser.feed(raw[a:b])
            if len(f):
                frames.append(f)
    return np.concatenate(frames)


def test_parser_with_ring_wraparound_and_odd_reads():
    rng = np.random.default_rng(0)
    # small ring (not a multiple of the frame size) forces many wrap-arounds
    dev = FakeActiveTwo(speedmode=7, mk2=True, ring_bytes=50_000, realtime=False, seed=1)
    dev.start()
    parser, reader = FrameParser(), RingReader(dev)
    frames = _collect(dev, parser, reader, 300, rng)
    assert parser.words_per_sample == frame_channels(True, 7) + 2
    assert parser.sync_errors == 0
    assert np.all(frames[:, 0] == SYNC)
    # one frame per sample written, minus what is still pending in the parser
    assert dev.n_written - 3 * parser.words_per_sample <= frames.shape[0] <= dev.n_written
    st = decode_status(frames[-1, 1])
    assert st["mk2"] and st["speedmode"] == 7 and st["cms_in_range"]


def test_parser_resyncs_after_corruption():
    dev = FakeActiveTwo(speedmode=7, mk2=True, ring_bytes=10_000_000, realtime=False, seed=2)
    dev.start()
    reader = RingReader(dev)
    dev.advance(200)
    good = reader.read()
    dev.advance(200)
    after = reader.read()
    parser = FrameParser()
    a = parser.feed(good)
    corrupt = bytearray(after)
    del corrupt[100:107]  # drop 7 bytes mid-stream
    b = parser.feed(bytes(corrupt))
    assert parser.sync_errors >= 1
    assert a.shape[0] > 150 and b.shape[0] > 150
    assert np.all(b[:, 0] == SYNC)


def test_values_roundtrip_within_quantisation():
    from bciflow.sources.synthetic import SyntheticEEG

    dev = FakeActiveTwo(speedmode=7, mk2=True, realtime=False, seed=3, ring_bytes=8_000_000)
    dev.start()
    reader = RingReader(dev)
    dev.advance(1000)
    uv = frames_to_uv(FrameParser().feed(reader.read()))
    assert uv.shape[1] == frame_channels(True, 7)
    # an identical generator reproduces what the fake device wrote into its first 32 slots
    expected, _ = SyntheticEEG("biosemi32", sfreq=dev.sfreq, seed=3).next(1000)
    n = uv.shape[0]
    np.testing.assert_allclose(uv[:, :32], expected[:, :n].T, atol=LSB_UV / 2 + 1e-9)


def test_triggers_in_status():
    dev = FakeActiveTwo(speedmode=7, mk2=True, realtime=False, seed=4, ring_bytes=64_000_000)
    dev.start()
    reader, parser = RingReader(dev), FrameParser()
    codes = []
    for _ in range(40):
        dev.advance(8192)  # 0.5 s at 16 kHz
        f = parser.feed(reader.read())
        codes.append((f[:, 1] >> 8) & 0xFFFF)
    codes = np.concatenate(codes)
    onsets = np.flatnonzero((codes != 0) & (np.diff(codes, prepend=0) != 0))
    assert len(onsets) >= 2
    assert set(np.unique(codes[onsets])) <= {1, 2}


def test_actiview_24bit_decoding():
    vals = np.array([[0, 1, -1, 2 ** 23 - 1, -(2 ** 23)]], dtype=np.int64)
    raw = b"".join(int(v & 0xFFFFFF).to_bytes(3, "little") for v in vals[0])
    out = decode_24bit(raw, 5)
    assert out.tolist() == vals.tolist()
