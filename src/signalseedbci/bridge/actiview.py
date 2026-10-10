"""BioSemi ActiView TCP server -> LSL.

Alternative to the USB driver: ActiView keeps ownership of the amplifier (so you
still see electrode offsets, CMS and battery in ActiView) and serves the data on its
"TCP Server" tab. This bridge connects to it and republishes as LSL.

Wire format: a continuous byte stream, per sample all channels interleaved, each
value 3 bytes little-endian signed (24 bit, 1 LSB = 31.25 nV). The channel count is
whatever is configured in ActiView's TCP tab (electrodes + EX, plus the trigger
channel as the last channel if "Add 8 EX / triggers" is enabled). Set
``n_channels``/``has_trigger`` to match ActiView exactly, and check one known signal
(e.g. a calibration square) to confirm the scale on your ActiView version.
"""

from __future__ import annotations

import socket
import time

import numpy as np
import pylsl

from .. import DEFAULT_EEG_STREAM, DEFAULT_MARKER_STREAM
from ..montage import biosemi_labels
from ..streams import SignalInfo
from .biosemi import LSB_UV
from .publisher import BridgePublisher


def decode_24bit(raw: bytes, n_channels: int) -> np.ndarray:
    """Interleaved little-endian int24 -> (n_samples, n_channels) int32."""
    b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, n_channels, 3).astype(np.int32)
    v = b[..., 0] | (b[..., 1] << 8) | (b[..., 2] << 16)
    return np.where(v & 0x800000, v - (1 << 24), v)


def run_actiview_bridge(host: str = "127.0.0.1", port: int = 778, n_channels: int = 73,
                        sfreq: float = 2048.0, has_trigger: bool = True, n_ex: int = 8,
                        eeg_name: str = DEFAULT_EEG_STREAM, marker_name: str | None = DEFAULT_MARKER_STREAM,
                        trigger_mask: int = 0xFF, labels: dict[str, str] | None = None,
                        duration: float | None = None) -> None:
    n_data = n_channels - (1 if has_trigger else 0)
    n_e = n_data - n_ex
    names = biosemi_labels(n_e, n_ex)
    if labels:
        names = [labels.get(n, n) for n in names]
    info = SignalInfo(names, sfreq, ["eeg"] * n_e + ["misc"] * n_ex)

    print(f"[actiview] connecting to {host}:{port} ...")
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(2.0)
    pub = BridgePublisher(info, eeg_name, marker_name if has_trigger else None, trigger_mask,
                          "signalseedbci-actiview")
    frame_bytes = 3 * n_channels
    buf = bytearray()
    t_start = time.monotonic()
    try:
        while duration is None or time.monotonic() - t_start < duration:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                print("[actiview] no data for 2 s: is acquisition running in ActiView?")
                continue
            if not chunk:
                raise ConnectionError("ActiView closed the connection")
            now = pylsl.local_clock()
            buf += chunk
            n = len(buf) // frame_bytes
            if n == 0:
                continue
            values = decode_24bit(bytes(buf[:n * frame_bytes]), n_channels)
            del buf[:n * frame_bytes]
            uv = values[:, :n_data] * LSB_UV
            trig = values[:, -1] & 0xFFFF if has_trigger else None
            pub.push(uv, trig, now=now)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        print("[actiview] stopped")
