"""Thin helpers around pylsl: creating outlets with channel metadata,
resolving streams, and configuring LSL for a multi-machine (Mac + Windows) lab."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import numpy as np
import pylsl

from .streams import SignalInfo


def signal_outlet(name: str, info: SignalInfo, stype: str = "EEG", source_id: str | None = None,
                  unit: str = "microvolts", chunk_size: int = 0) -> pylsl.StreamOutlet:
    sinfo = pylsl.StreamInfo(name, stype, info.n_channels, info.sfreq, pylsl.cf_float32,
                             source_id or f"{name}@{socket.gethostname()}")
    channels = sinfo.desc().append_child("channels")
    for ch, kind in zip(info.ch_names, info.ch_types):
        c = channels.append_child("channel")
        c.append_child_value("label", ch)
        c.append_child_value("unit", unit)
        c.append_child_value("type", kind.upper())
    sinfo.desc().append_child_value("manufacturer", "signalseedbci")
    return pylsl.StreamOutlet(sinfo, chunk_size=chunk_size)


def marker_outlet(name: str, stype: str = "Markers", source_id: str | None = None) -> pylsl.StreamOutlet:
    sinfo = pylsl.StreamInfo(name, stype, 1, pylsl.IRREGULAR_RATE, pylsl.cf_string,
                             source_id or f"{name}@{socket.gethostname()}")
    return pylsl.StreamOutlet(sinfo)


def resolve(name: str | None = None, stype: str | None = None, timeout: float = 10.0) -> pylsl.StreamInfo:
    """Find one stream by name (preferred) or type. Raises TimeoutError."""
    if name:
        found = pylsl.resolve_byprop("name", name, minimum=1, timeout=timeout)
    elif stype:
        found = pylsl.resolve_byprop("type", stype, minimum=1, timeout=timeout)
    else:
        raise ValueError("Give a stream name or type")
    if stype and name:
        found = [s for s in found if s.type() == stype] or found
    if not found:
        what = f"name={name!r}" if name else f"type={stype!r}"
        raise TimeoutError(
            f"No LSL stream with {what} found within {timeout:.0f}s. "
            "Is the bridge/replay running, and are both machines on the same subnet "
            "(or listed in KnownPeers, see `signalseedbci lsl-config`)?")
    if len(found) > 1:
        hosts = ", ".join(f"{s.name()}@{s.hostname()}" for s in found)
        print(f"[lsl] warning: {len(found)} streams match ({hosts}); using the first")
    return found[0]


def read_signal_info(info: pylsl.StreamInfo) -> tuple[SignalInfo, str]:
    """Channel labels/types/unit from an LSL stream's description."""
    n = info.channel_count()
    names, types, unit = [], [], "microvolts"
    ch = info.desc().child("channels").child("channel")
    while not ch.empty() and len(names) < n:
        names.append(ch.child_value("label") or f"Ch{len(names) + 1}")
        types.append((ch.child_value("type") or "EEG").lower())
        unit = ch.child_value("unit") or unit
        ch = ch.next_sibling()
    if len(names) != n:
        names = [f"Ch{i + 1}" for i in range(n)]
        types = ["eeg"] * n
    return SignalInfo(names, info.nominal_srate(), types), unit


def unit_scale_to_uv(unit: str) -> float:
    u = unit.strip().lower()
    if u in ("v", "volt", "volts"):
        return 1e6
    if u in ("mv", "millivolt", "millivolts"):
        return 1e3
    if u in ("nv", "nanovolt", "nanovolts"):
        return 1e-3
    return 1.0  # microvolts or unknown


def pull_signal(inlet: pylsl.StreamInlet, n_channels: int, max_samples: int = 4096):
    """Pull everything available as (data (n_ch, n), times) numpy arrays."""
    data, times = inlet.pull_chunk(timeout=0.0, max_samples=max_samples, as_numpy=True)
    times = np.asarray(times, dtype=float)
    if times.size == 0:
        return np.zeros((n_channels, 0)), times
    return np.asarray(data, dtype=float).T, times


def lsl_config_path() -> Path:
    """Per-user liblsl config file location (read on every platform)."""
    return Path.home() / "lsl_api" / "lsl_api.cfg"


def write_lsl_config(peers: list[str], session_id: str | None = None, path: Path | None = None) -> Path:
    """Write a liblsl config so streams are found across subnets / Wi-Fi without multicast.

    Put the same file (same SessionID) on the Windows bridge laptop and the Mac.
    """
    path = Path(path) if path else lsl_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[lab]"]
    if peers:
        lines.append("KnownPeers = {" + ", ".join(peers) + "}")
    if session_id:
        lines.append(f"SessionID = {session_id}")
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def describe_environment() -> str:
    cfg = os.environ.get("LSLAPICFG") or (str(lsl_config_path()) if lsl_config_path().exists() else "none")
    return f"liblsl {pylsl.library_version()} | host {socket.gethostname()} | config: {cfg}"
