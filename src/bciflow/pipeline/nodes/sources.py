"""Source nodes: live LSL streams, or a recording file (for simulation and training)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pylsl

from ... import DEFAULT_EEG_STREAM, DEFAULT_MARKER_STREAM
from ...io import load_recording
from ...lsl import pull_signal, read_signal_info, resolve, unit_scale_to_uv
from ...streams import Marker, SignalChunk
from ..node import Node, Param


class _MarkerInlet:
    """Marker inlet that keeps looking for its stream in the background, so a
    stimulus program may be started before or after the pipeline."""

    def __init__(self, name: str | None, stype: str | None):
        prop, value = ("name", name) if name else ("type", stype)
        self.what = f"{prop}={value!r}"
        self.resolver = pylsl.ContinuousResolver(prop=prop, value=value)
        self.inlet = None
        self._connecting = False
        self._t_next = 0.0

    def pull(self) -> list[Marker]:
        if self.inlet is None:
            now = time.monotonic()
            if now < self._t_next or self._connecting:
                return []
            self._t_next = now + 1.0
            found = self.resolver.results()
            if found:
                # opening the stream blocks for a few hundred ms: keep that off the processing loop
                self._connecting = True
                threading.Thread(target=self._connect, args=(found[0],), daemon=True).start()
            return []
        values, stamps = self.inlet.pull_chunk(timeout=0.0)
        return [Marker(float(t), str(v[0])) for v, t in zip(values, stamps)]

    def _connect(self, info) -> None:
        try:
            inlet = pylsl.StreamInlet(info, max_buflen=360,
                                      processing_flags=pylsl.proc_clocksync | pylsl.proc_monotonize)
            inlet.open_stream(timeout=10)
            inlet.time_correction(timeout=5)
            self.inlet = inlet
            print(f"[markers] connected to {info.name()}@{info.hostname()}")
        except Exception as exc:
            print(f"[markers] could not connect to {info.name()}: {exc}")
        finally:
            self._connecting = False


class LSLSource(Node):
    """Live signal (and optionally markers) from Lab Streaming Layer."""

    category = "sources"
    outputs = {"signal": "signal", "markers": "markers"}
    is_source = True
    params = {
        "signal_name": Param(DEFAULT_EEG_STREAM, "str", "LSL stream name (empty: match by type)"),
        "signal_type": Param("EEG", "str", "LSL stream type"),
        "marker_name": Param(DEFAULT_MARKER_STREAM, "str", "Marker stream name (empty: no markers)"),
        "marker_type": Param(None, "str", "Marker stream type, used if marker_name is empty"),
        "timeout": Param(15.0, "float", "Seconds to wait for the signal stream"),
    }

    def setup(self, in_meta):
        sinfo = resolve(self.p.signal_name or None, self.p.signal_type or None, self.p.timeout)
        self.inlet = pylsl.StreamInlet(
            sinfo, max_buflen=30,
            processing_flags=pylsl.proc_clocksync | pylsl.proc_dejitter | pylsl.proc_monotonize)
        info, unit = read_signal_info(self.inlet.info())
        self.scale = unit_scale_to_uv(unit)
        self.info = info
        self.log(f"connected to '{sinfo.name()}' on {sinfo.hostname()}: {info.n_channels} ch @ {info.sfreq:g} Hz")
        self.markers = (_MarkerInlet(self.p.marker_name or None, self.p.marker_type)
                        if (self.p.marker_name or self.p.marker_type) else None)
        return {"signal": info, "markers": None}

    def process(self, inputs):
        data, times = pull_signal(self.inlet, self.info.n_channels)
        out = {"signal": [SignalChunk(data * self.scale, times)] if times.size else []}
        out["markers"] = self.markers.pull() if self.markers else []
        return out

    def close(self):
        if getattr(self, "inlet", None):
            self.inlet.close_stream()


class LSLMarkerSource(Node):
    """Additional marker stream (e.g. software markers from PsychoPy)."""

    category = "sources"
    outputs = {"markers": "markers"}
    is_source = True
    params = {
        "name": Param(None, "str", "LSL stream name"),
        "type": Param("Markers", "str", "LSL stream type, used if name is empty"),
    }

    def setup(self, in_meta):
        self.inlet = _MarkerInlet(self.p.name or None, self.p.type)
        return {"markers": None}

    def process(self, inputs):
        return {"markers": self.inlet.pull()}


class FileSource(Node):
    """Recording file (BDF/EDF/GDF/FIF/BrainVision/EEGLAB/XDF) played through the
    pipeline, either in real time (simulation) or as fast as possible (training)."""

    category = "sources"
    outputs = {"signal": "signal", "markers": "markers"}
    is_source = True
    params = {
        "path": Param(None, "path", "Recording file", required=True),
        "realtime": Param(None, "bool", "Pace like a live stream (default: from run mode)"),
        "chunk_s": Param(0.1, "float", "Chunk length when not real-time"),
        "stim_channel": Param("auto", "str", "Trigger channel ('auto' = Status/first stim)"),
        "event_mask": Param(0xFF, "int", "Bit mask applied to trigger codes"),
    }

    def setup(self, in_meta):
        self.rec = load_recording(self.p.path, stim_channel=self.p.stim_channel, event_mask=self.p.event_mask)
        self.realtime = self.context.realtime if self.p.realtime is None else self.p.realtime
        self.pos = 0
        self.marker_i = 0
        self.t0 = pylsl.local_clock() if self.realtime else 0.0
        self.log(f"{self.p.path}: {self.rec.info.n_channels} ch @ {self.rec.info.sfreq:g} Hz, "
                 f"{self.rec.duration:.0f} s, {len(self.rec.markers)} markers")
        return {"signal": self.rec.info, "markers": None}

    @property
    def finished(self):
        return self.pos >= self.rec.data.shape[1]

    def process(self, inputs):
        sf = self.rec.info.sfreq
        n_total = self.rec.data.shape[1]
        if self.realtime:
            end = min(n_total, int((pylsl.local_clock() - self.t0) * sf))
        else:
            end = min(n_total, self.pos + max(1, int(self.p.chunk_s * sf)))
        if end <= self.pos:
            return {"signal": [], "markers": []}
        chunk = SignalChunk(self.rec.data[:, self.pos:end], self.t0 + self.rec.times[self.pos:end])
        t_end = self.rec.times[end - 1] + 0.5 / sf
        markers = []
        while self.marker_i < len(self.rec.markers) and self.rec.markers[self.marker_i].time < t_end:
            m = self.rec.markers[self.marker_i]
            markers.append(Marker(self.t0 + m.time, m.value))
            self.marker_i += 1
        self.pos = end
        return {"signal": [chunk], "markers": markers}
