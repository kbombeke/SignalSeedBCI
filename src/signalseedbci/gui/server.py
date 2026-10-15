"""Local web server for the graphical pipeline editor.

Serves the single-page editor (``static/``) and a small JSON API:

- node schemas, pipeline load/save/validate, recording file listing
- process manager: start/stop ``signalseedbci`` subcommands (run, train, synth, replay,
  fake bridge) and read their console output
- LSL monitor: list streams and poll recent samples/markers for the live scope

Standard library only. Binds to 127.0.0.1 and only accepts requests addressed to
localhost, because the API can start processes and write files.
"""

from __future__ import annotations

import json
import mimetypes
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import yaml

STATIC = Path(__file__).parent / "static"
PARADIGMS = ("motor-imagery", "alpha-bursts", "eye-movements")
RECORDING_SUFFIXES = {".bdf", ".edf", ".gdf", ".fif", ".xdf", ".vhdr", ".set", ".fif.gz"}
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules", ".pytest_cache"}


# --------------------------------------------------------------------------- processes

class ManagedProcess:
    def __init__(self, pid: int, label: str, kind: str, args: list[str], cwd: Path):
        self.id = pid
        self.label = label
        self.kind = kind
        self.args = args
        self.lines: deque[str] = deque(maxlen=5000)
        self.n_lines = 0  # total lines ever produced (for incremental reads)
        self.started = time.time()
        self.on_exit: list = []  # callbacks run when the process ends (linked session processes)
        # Stop request on Windows: CTRL_BREAK only reaches a child sharing the editor's console
        self.stop_file = Path(tempfile.gettempdir()) / f"signalseedbci-stop-{os.getpid()}-{pid}-{time.time_ns()}"
        self.stop_file.unlink(missing_ok=True)
        # UTF-8 both ways: Windows pipes default to the ANSI code page, which can't print 'µ', '→', ...
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                   SIGNALSEEDBCI_STOP_FILE=str(self.stop_file))
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self.popen = subprocess.Popen([sys.executable, "-u", "-m", "signalseedbci", *args], cwd=cwd,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
                                      errors="replace", bufsize=1, env=env, creationflags=flags)
        self._append(f"$ signalseedbci {' '.join(args)}")
        threading.Thread(target=self._read, daemon=True).start()

    def _append(self, line: str) -> None:
        self.lines.append(line)
        self.n_lines += 1

    def _read(self) -> None:
        for line in self.popen.stdout:
            line = line.rstrip("\n")
            if "INFO|" in line and ("api_config" in line or "common.cpp" in line):
                continue  # liblsl start-up chatter
            self._append(line)
        code = self.popen.wait()
        self.stop_file.unlink(missing_ok=True)
        self._append(f"[exited with code {code}]")
        for cb in self.on_exit:
            try:
                cb(self)
            except Exception:
                traceback.print_exc()

    @property
    def running(self) -> bool:
        return self.popen.poll() is None

    def stop(self) -> None:
        if not self.running:
            return
        # an interrupt lets the pipeline close cleanly (e.g. the Recorder saves its file)
        if sys.platform == "win32":
            self.stop_file.touch()  # the command interrupts itself (cli._watch_stop_file)
        else:
            self.popen.send_signal(signal.SIGINT)

        # after Stop, live training still saves the session and fits the classifier
        grace = 300 if self.kind == "train" else 4

        def _kill_later():
            time.sleep(grace)
            if self.running:
                self.popen.kill()
        threading.Thread(target=_kill_later, daemon=True).start()

    def read(self, since: int) -> tuple[list[str], int]:
        first = self.n_lines - len(self.lines)
        start = max(since, first)
        return list(self.lines)[start - first:], self.n_lines

    def summary(self) -> dict:
        return {"id": self.id, "label": self.label, "kind": self.kind, "args": self.args,
                "running": self.running, "returncode": self.popen.poll(), "started": self.started,
                "session": getattr(self, "session", None)}


class ProcessManager:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.procs: dict[int, ManagedProcess] = {}
        self._next = 1
        self._lock = threading.Lock()

    def start(self, label: str, kind: str, args: list[str]) -> ManagedProcess:
        with self._lock:
            pid = self._next
            self._next += 1
        proc = ManagedProcess(pid, label, kind, args, self.cwd)
        self.procs[pid] = proc
        return proc

    def stop_all(self) -> None:
        for p in self.procs.values():
            p.stop()


# --------------------------------------------------------------------------- LSL monitor

class StreamWatcher:
    """Background inlet keeping the last seconds of a stream for the GUI scope.

    Numeric streams are block-averaged to about ``display_rate`` Hz."""

    def __init__(self, info, keep_s: float = 65.0, display_rate: float = 250.0, filt: str = "none"):
        import pylsl

        self.pylsl = pylsl
        self.name = info.name()
        self.uid = info.uid()
        self.is_string = info.channel_format() == pylsl.cf_string
        flags = pylsl.proc_clocksync | pylsl.proc_monotonize
        if not self.is_string and info.nominal_srate() > 0:
            flags |= pylsl.proc_dejitter
        self.inlet = pylsl.StreamInlet(info, max_buflen=int(keep_s) + 5, processing_flags=flags)
        from ..lsl import read_signal_info

        full = self.inlet.info()
        self.ch_names = read_signal_info(full)[0].ch_names if not self.is_string else ["value"]
        self.sfreq = info.nominal_srate()
        self.k = max(1, int(round(self.sfreq / display_rate))) if self.sfreq > 0 else 1
        self.keep_s = keep_s
        self.sos, self.zi = self._display_filter(filt), None
        self.times: deque[float] = deque()
        self.values: deque = deque()
        self._carry_t = np.zeros(0)
        self._carry_x = np.zeros((0, len(self.ch_names)))
        self.last_access = self.last_data = self.last_check = time.monotonic()
        self.error = None
        self._stop = False
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            while not self._stop:
                if self.is_string:
                    vals, ts = self.inlet.pull_chunk(timeout=0.2)
                    new = [(float(t), str(v[0])) for v, t in zip(vals, ts)]
                else:
                    x, ts = self.inlet.pull_chunk(timeout=0.2, max_samples=8192, as_numpy=True)
                    new = self._decimate(np.asarray(x, float), np.asarray(ts, float)) if len(ts) else []
                if len(new):
                    self.last_data = time.monotonic()
                    with self._lock:
                        for t, v in new:
                            self.times.append(t)
                            self.values.append(v)
                        cutoff = self.times[-1] - self.keep_s
                        while self.times and self.times[0] < cutoff:
                            self.times.popleft()
                            self.values.popleft()
                if time.monotonic() - self.last_access > 30:
                    break
        except Exception as exc:  # stream vanished, etc.
            self.error = str(exc)
        finally:
            try:
                self.inlet.close_stream()
            except Exception:
                pass
            self._stop = True

    def _display_filter(self, filt: str):
        """Causal SOS filter for display only (removes DC drift and mains noise)."""
        from scipy import signal as sps

        fs = self.sfreq
        if self.is_string or fs <= 0 or filt == "none":
            return None
        if filt == "highpass" or fs < 100:
            return sps.butter(2, 0.5, "highpass", fs=fs, output="sos")
        sos = [sps.butter(2, [1.0, min(40.0, 0.45 * fs)], "bandpass", fs=fs, output="sos")]
        for f0 in (50.0, 60.0):
            if f0 < fs / 2:
                sos.append(sps.tf2sos(*sps.iirnotch(f0, 30, fs=fs)))
        return np.vstack(sos)

    def _filter(self, x: np.ndarray) -> np.ndarray:
        if self.sos is None or len(x) == 0:
            return x
        from scipy import signal as sps

        if self.zi is None:
            self.zi = sps.sosfilt_zi(self.sos)[:, :, None] * x[0][None, None, :]
        y, self.zi = sps.sosfilt(self.sos, x, axis=0, zi=self.zi)
        return y

    def _decimate(self, x: np.ndarray, t: np.ndarray):
        x = self._filter(x)
        if self.k == 1:
            return [(float(ti), xi.tolist()) for ti, xi in zip(t, x)]
        x = np.concatenate([self._carry_x, x])
        t = np.concatenate([self._carry_t, t])
        n = (len(t) // self.k) * self.k
        self._carry_x, self._carry_t = x[n:], t[n:]
        if n == 0:
            return []
        xb = x[:n].reshape(-1, self.k, x.shape[1]).mean(axis=1)
        tb = t[self.k - 1:n:self.k]
        return [(float(ti), xi.tolist()) for ti, xi in zip(tb, xb)]

    @property
    def alive(self) -> bool:
        return not self._stop

    def get(self, since: float, window: float = 60.0) -> dict:
        """Samples newer than ``since``, at most the last ``window`` seconds."""
        self.last_access = time.monotonic()
        with self._lock:
            if self.times:
                since = max(since, self.times[-1] - window)
            idx = [i for i in range(len(self.times) - 1, -1, -1) if self.times[i] > since]
            idx.reverse()
            times = [self.times[i] for i in idx]
            values = [self.values[i] for i in idx]
        out = {"name": self.name, "kind": "markers" if self.is_string else "numeric", "times": times,
               "now": self.pylsl.local_clock(), "error": self.error}
        if self.is_string:
            out["values"] = values
        else:
            out["ch_names"] = self.ch_names
            out["sfreq"] = self.sfreq / self.k if self.sfreq > 0 else 0
            out["data"] = values
        return out

    def stop(self) -> None:
        self._stop = True


class Monitor:
    def __init__(self):
        self.watchers: dict[str, StreamWatcher] = {}
        self._lock = threading.Lock()

    def streams(self, wait: float = 1.0) -> list[dict]:
        import pylsl

        found = pylsl.resolve_streams(wait_time=wait)
        return [{"name": s.name(), "type": s.type(), "channels": s.channel_count(),
                 "sfreq": s.nominal_srate(), "host": s.hostname(), "source_id": s.source_id(),
                 "format": "string" if s.channel_format() == pylsl.cf_string else "numeric"}
                for s in sorted(found, key=lambda s: (s.name(), s.hostname()))]

    def watch(self, name: str, since: float, filt: str = "none", window: float = 60.0) -> dict:
        """Several views may watch one stream with different display filters; idle
        watchers stop by themselves after 30 s without polls."""
        import pylsl

        key = (name, filt)
        with self._lock:
            w = self.watchers.get(key)
        if w is not None and w.alive and self._replaced(w):
            w.stop()  # a new run publishes this name now (e.g. another pipeline): follow it
        if w is None or not w.alive:
            found = pylsl.resolve_byprop("name", name, minimum=1, timeout=0.6)
            if not found:  # normal while a pipeline is not running: not an HTTP error
                return {"name": name, "missing": True, "error": f"LSL stream '{name}' not found"}
            with self._lock:
                w = self.watchers.get(key)
                if w is None or not w.alive:
                    w = self.watchers[key] = StreamWatcher(found[0], filt=filt)
        return w.get(since, window)

    @staticmethod
    def _replaced(w: "StreamWatcher") -> bool:
        """The watched stream went quiet and another stream with its name is running.
        (An LSL inlet waits for its own stream to come back and never switches.)"""
        import pylsl

        now = time.monotonic()
        if now - w.last_data < 2.0 or now - w.last_check < 2.0:
            return False
        w.last_check = now
        found = pylsl.resolve_byprop("name", w.name, minimum=1, timeout=0.3)
        return bool(found) and all(s.uid() != w.uid for s in found)

    def unwatch(self, name: str) -> None:
        with self._lock:
            keys = [k for k in self.watchers if k[0] == name]
            for k in keys:
                self.watchers.pop(k).stop()


# --------------------------------------------------------------------------- pipelines

def _header_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("#") or (not line.strip() and lines):
            lines.append(line)
        else:
            break
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def dump_pipeline(spec: dict, previous_text: str = "") -> str:
    """Readable YAML (one node per block, one edge per line, compact layout);
    keeps a leading comment block of the previous file."""

    def dump(obj) -> str:
        return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)

    out = []
    header = _header_comments(previous_text)
    if header:
        out.append(header)
    top = {k: spec[k] for k in ("name", "description") if k in spec}
    extra = {k: v for k, v in spec.items() if k not in ("name", "description", "nodes", "edges", "layout")}
    if top:
        out.append(dump(top).rstrip())
    if extra:
        out.append(dump(extra).rstrip())
    out.append("\nnodes:")
    for nid, ns in (spec.get("nodes") or {}).items():
        out.append(f"  {nid}:")
        out.append(f"    type: {ns['type']}")
        if ns.get("params"):
            out.append("    params: " + yaml.safe_dump(ns["params"], default_flow_style=True, sort_keys=False,
                                                     allow_unicode=True, width=10_000).strip())
    out.append("\nedges:")
    out += [f"  - {e}" for e in spec.get("edges") or []]
    if spec.get("layout"):
        out.append("\nlayout:  # node positions in the editor: [x, y] or [x, y, width, view height]")
        out += [f"  {nid}: [" + ", ".join(str(int(v)) for v in pos) + "]" for nid, pos in spec["layout"].items()]
    return "\n".join(out) + "\n"


def validate_spec(spec: dict, base_dir: Path) -> dict:
    """Structural validation (no streams opened) plus per-node info for the inspector."""
    from ..models import load_model
    from ..pipeline import Graph

    errors, warnings, info = [], [], {}
    try:
        graph = Graph(spec, base_dir=base_dir)
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [], "node_info": {}}
    for nid, node in graph.nodes.items():
        if type(node).__name__ == "Classifier":
            path = Path(node.p.model_path)
            if path.exists():
                try:
                    _, meta = load_model(path)
                    info[nid] = (f"Trained {meta['estimator']}: {meta['n_train']} epochs "
                                 f"{meta.get('class_counts', {})}, CV accuracy {meta.get('cv_accuracy', float('nan')):.2f}")
                except Exception as exc:
                    warnings.append(f"{nid}: cannot read model {path.name}: {exc}")
            else:
                warnings.append(f"{nid}: not trained yet ({path.name} missing) — use Train before Run")
        if type(node).__name__ == "FileSource" and node.p.path and not Path(node.p.path).exists():
            errors.append(f"{nid}: file not found: {node.p.path}")
    if not graph.sources():
        warnings.append("No source node: add an LSLSource or FileSource")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "node_info": info,
            "order": graph.order}


# --------------------------------------------------------------------------- HTTP

class App:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.pipelines_dir = self.project / "pipelines"
        self.pipelines_dir.mkdir(exist_ok=True)
        self.procs = ProcessManager(self.project)
        self.monitor = Monitor()
        self.channel_cache: dict[tuple[Path, int], list[str]] = {}

    def safe_path(self, rel: str, must_exist: bool = False) -> Path:
        p = (self.project / rel).resolve()
        if self.project not in p.parents and p != self.project:
            raise PermissionError(f"Path outside the project folder: {rel}")
        if must_exist and not p.exists():
            raise FileNotFoundError(rel)
        return p

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.project).as_posix()

    # ---- API handlers: (query, body) -> json-able
    def api_nodes(self, q, b):
        from ..models import BUILTIN
        from ..pipeline import REGISTRY
        return {"nodes": [cls.schema() for cls in REGISTRY.values()],
                "estimators": {k: v[2] for k, v in BUILTIN.items()}}

    def api_pipelines(self, q, b):
        files = sorted(self.pipelines_dir.rglob("*.y*ml"))
        return {"pipelines": [self.rel(f) for f in files]}

    def api_pipeline_get(self, q, b):
        path = self.safe_path(q["path"], must_exist=True)
        return {"path": self.rel(path), "spec": yaml.safe_load(path.read_text(encoding="utf-8")) or {}}

    def api_pipeline_save(self, q, b):
        path = self.safe_path(b["path"])
        if path.suffix not in (".yaml", ".yml"):
            path = path.with_suffix(".yaml")
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_pipeline(b["spec"], previous), encoding="utf-8")
        return {"path": self.rel(path)}

    def api_validate(self, q, b):
        base = self.safe_path(b["path"]).parent if b.get("path") else self.pipelines_dir
        return validate_spec(b["spec"], base)

    def api_files(self, q, b):
        out = []
        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            if Path(root).relative_to(self.project).parts.__len__() > 4:
                continue
            for f in files:
                low = f.lower()
                if any(low.endswith(s) for s in RECORDING_SUFFIXES):
                    out.append(self.rel(Path(root) / f))
        return {"files": sorted(out)}

    def api_channels(self, q, b):
        """Channel names a pipeline gets from a recording (file=) or a running LSL stream (stream=),
        for the RenameChannels table."""
        if q.get("stream"):
            import pylsl

            from ..lsl import read_signal_info, resolve

            try:
                full = pylsl.StreamInlet(resolve(q["stream"], timeout=2.0)).info(timeout=2.0)
            except TimeoutError:
                raise LookupError(f"LSL stream '{q['stream']}' is not running") from None
            return {"stream": q["stream"], "channels": read_signal_info(full)[0].ch_names}
        from ..io import recording_channels

        path = self.safe_path(q["file"], must_exist=True)
        key = (path, path.stat().st_mtime_ns)
        if key not in self.channel_cache:
            self.channel_cache[key] = recording_channels(path)
        return {"file": self.rel(path), "channels": self.channel_cache[key]}

    def api_processes(self, q, b):
        return {"processes": [p.summary() for p in self.procs.procs.values()]}

    def api_process_start(self, q, b):
        kind = b["kind"]
        o = b.get("options") or {}
        if kind in ("run", "train"):
            pipe = self.rel(self.safe_path(b["pipeline"], must_exist=True))
            args = [kind, pipe]
            if kind == "run":
                mode = o.get("mode", "live")
                if mode in ("replay", "offline"):
                    args += [f"--{mode}", self.rel(self.safe_path(o["file"], must_exist=True))]
                if o.get("duration"):
                    args += ["--duration", str(o["duration"])]
                label = f"run {Path(pipe).stem} ({mode})"
            else:
                files = [self.rel(self.safe_path(f, must_exist=True)) for f in o.get("files", [])]
                if not files:
                    raise ValueError("Select at least one training recording")
                args += ["--data", *files]
                label = f"train {Path(pipe).stem}"
        elif kind == "synth":
            paradigm = o.get("paradigm", "motor-imagery")
            if paradigm not in PARADIGMS:
                raise ValueError(f"Unknown paradigm {paradigm!r}")
            args = ["synth", "--preset", o.get("preset", "biosemi32"), "--sfreq", str(o.get("sfreq", 512)),
                    "--paradigm", paradigm]
            if o.get("record"):
                args += ["--record", self.rel(self.safe_path(o["record"])), "--duration", str(o.get("duration", 300))]
                label = f"synth -> {o['record']}"
            else:
                label = f"synthetic EEG stream ({paradigm})"
        elif kind == "replay":
            args = ["replay", self.rel(self.safe_path(o["file"], must_exist=True))]
            if o.get("loop"):
                args.append("--loop")
            label = f"replay {Path(o['file']).name}"
        elif kind == "fake-bridge":
            args = ["bridge", "biosemi", "--fake", "--channels", str(o.get("channels", 32)),
                    "--fake-preset", o.get("preset", "biosemi32")]
            label = "fake ActiveTwo bridge"
        else:
            raise ValueError(f"Unknown process kind {kind!r}")
        return self.procs.start(label, kind, args).summary()

    SOURCES = ("live", "replay", "synthetic", "fake-bridge", "offline")

    def api_session_start(self, q, b):
        """Start the data source the toolbar selected (if it is a simulation) and the
        pipeline together. They are linked: when one ends, the other is stopped.
        With ``train: true`` the pipeline calibrates on the live streams instead
        (``train --live``) and fits its classifiers when stopped."""
        from .. import DEFAULT_EEG_STREAM, DEFAULT_MARKER_STREAM

        path = self.safe_path(b["pipeline"], must_exist=True)
        pipe = self.rel(path)
        source = b.get("source", "live")
        o = b.get("options") or {}
        train = bool(b.get("train"))
        if source not in self.SOURCES:
            raise ValueError(f"Unknown data source {source!r}")
        if train and source == "offline":
            raise ValueError("Live training needs streaming data; to train on a recording, tick it in the file list")
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        lsl = next(((n.get("params") or {}) for n in (spec.get("nodes") or {}).values()
                    if n.get("type") == "LSLSource"), None)
        warnings, src_args, src_label = [], None, None
        if source in ("replay", "synthetic", "fake-bridge"):
            if lsl is None:
                raise ValueError("This pipeline has no LSLSource node, so it would not use a simulated stream. "
                                 "Choose 'Evaluate offline' or add an LSLSource.")
            eeg = lsl.get("signal_name") or DEFAULT_EEG_STREAM
            markers = lsl.get("marker_name", DEFAULT_MARKER_STREAM) or DEFAULT_MARKER_STREAM
            names = ["--name", eeg, "--marker-name", markers]
            try:
                import pylsl
                existing = pylsl.resolve_byprop("name", eeg, minimum=1, timeout=0.5)
            except Exception:
                existing = []
            if existing:
                warnings.append(f"A stream named '{eeg}' is already on the network ({existing[0].hostname()}); "
                                "the pipeline may connect to that one instead of the simulation.")
            if source == "replay":
                rec = self.rel(self.safe_path(o.get("file") or "", must_exist=True))
                src_args, src_label = ["replay", rec, "--loop", *names], f"replay {Path(rec).name}"
            elif source == "synthetic":
                paradigm = o.get("paradigm", "motor-imagery")
                if paradigm not in PARADIGMS:
                    raise ValueError(f"Unknown paradigm {paradigm!r}")
                src_args = ["synth", "--paradigm", paradigm, "--preset", "biosemi32", "--sfreq", "512", *names]
                src_label = f"synthetic EEG ({paradigm})"
            else:
                src_args = ["bridge", "biosemi", "--fake", "--channels", "32", "--fake-preset", "biosemi32", *names]
                src_label = "simulated ActiveTwo"
        run_args = ["train", pipe, "--live"] if train else ["run", pipe]
        if source == "offline":
            rec = self.rel(self.safe_path(o.get("file") or "", must_exist=True))
            run_args += ["--offline", rec]
            what = f"offline {Path(rec).name}"
        else:
            what = src_label or "live LSL"
        session = int(time.time() * 1000)
        src = self.procs.start(src_label, "source", src_args) if src_args else None
        run = self.procs.start(f"{'train ' if train else ''}{Path(pipe).stem} · {what}",
                               "train" if train else "run", run_args)
        for proc in (src, run):
            if proc is not None:
                proc.session = session
        if src is not None:
            run.on_exit.append(lambda _p: src.stop())
            src.on_exit.append(lambda _p: run.stop())
        return {"run": run.summary(), "source": src.summary() if src else None, "warnings": warnings}

    def api_process_stop(self, q, b):
        self.procs.procs[int(b["id"])].stop()
        return {"ok": True}

    def api_process_clear(self, q, b):
        for pid in [p.id for p in self.procs.procs.values() if not p.running]:
            del self.procs.procs[pid]
        return {"ok": True}

    def api_log(self, q, b):
        proc = self.procs.procs[int(q["id"])]
        lines, n = proc.read(int(q.get("since", 0)))
        return {"lines": lines, "next": n, "running": proc.running}

    def api_streams(self, q, b):
        return {"streams": self.monitor.streams(float(q.get("wait", 1.0)))}

    def api_watch(self, q, b):
        filt = q.get("filter", "none")
        if filt not in ("none", "highpass", "bandpass"):
            raise ValueError(f"unknown display filter {filt!r}")
        return self.monitor.watch(q["name"], float(q.get("since", 0)), filt, float(q.get("window", 60)))

    def api_unwatch(self, q, b):
        self.monitor.unwatch(b["name"])
        return {"ok": True}


ROUTES = {
    ("GET", "/api/nodes"): "api_nodes",
    ("GET", "/api/pipelines"): "api_pipelines",
    ("GET", "/api/pipeline"): "api_pipeline_get",
    ("POST", "/api/pipeline"): "api_pipeline_save",
    ("POST", "/api/validate"): "api_validate",
    ("GET", "/api/files"): "api_files",
    ("GET", "/api/channels"): "api_channels",
    ("GET", "/api/processes"): "api_processes",
    ("POST", "/api/process/start"): "api_process_start",
    ("POST", "/api/session/start"): "api_session_start",
    ("POST", "/api/process/stop"): "api_process_stop",
    ("POST", "/api/process/clear"): "api_process_clear",
    ("GET", "/api/log"): "api_log",
    ("GET", "/api/streams"): "api_streams",
    ("GET", "/api/watch"): "api_watch",
    ("POST", "/api/unwatch"): "api_unwatch",
}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SignalSeedBCI"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _handle(self, method: str) -> None:
            if not self._host_ok():
                return self._json(403, {"error": "forbidden host"})
            url = urlparse(self.path)
            route = ROUTES.get((method, url.path))
            if route is None:
                if method == "GET":
                    return self._static(url.path)
                return self._json(404, {"error": "not found"})
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            body = {}
            if method == "POST":
                if "application/json" not in (self.headers.get("Content-Type") or ""):
                    return self._json(415, {"error": "JSON body required"})
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            try:
                self._json(200, getattr(app, route)(query, body))
            except (FileNotFoundError, LookupError) as exc:
                self._json(404, {"error": str(exc)})
            except (ValueError, PermissionError, KeyError) as exc:
                self._json(400, {"error": str(exc) if not isinstance(exc, KeyError) else f"missing {exc}"})
            except Exception as exc:
                traceback.print_exc()
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            f = (STATIC / rel).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read_bytes(), ctype)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return Handler


def serve(project: str | Path = ".", port: int = 8765, open_browser: bool = True) -> None:
    app = App(Path(project))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    url = f"http://localhost:{port}/"
    print(f"SignalSeedBCI editor: {url}  (project {app.project}). Ctrl+C to quit.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.procs.stop_all()
        server.server_close()
