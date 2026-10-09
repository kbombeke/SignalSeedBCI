"""bciflow command line.

Acquisition (any machine, typically the Windows laptop next to the amplifier):
  bciflow bridge biosemi            ActiveTwo USB driver -> LSL
  bciflow bridge biosemi --fake     simulated ActiveTwo (no hardware) -> LSL
  bciflow bridge actiview           ActiView TCP server -> LSL

Simulation (no amplifier):
  bciflow synth                     synthetic motor-imagery EEG -> LSL
  bciflow synth --record x.fif      write a synthetic training recording
  bciflow replay rec.bdf            replay a recording as LSL in real time

Processing (Mac):
  bciflow train pipeline.yaml --data rec.bdf    fit the classifier(s) offline
  bciflow run pipeline.yaml                     run online on live LSL streams
  bciflow run pipeline.yaml --replay rec.bdf    simulate online, real-time pacing
  bciflow run pipeline.yaml --offline rec.bdf   pseudo-online evaluation, fast

Editor:
  bciflow gui                       graphical pipeline editor in the browser

Network / tools:
  bciflow discover                  list LSL streams on the network (+ clock offsets)
  bciflow lsl-config --peers IP..   configure LSL discovery across machines
  bciflow nodes [--json]            list pipeline node types (for the GUI)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import DEFAULT_EEG_STREAM, DEFAULT_MARKER_STREAM, __version__


def _int_auto(s: str) -> int:
    return int(s, 0)


def _add_stream_names(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", default=DEFAULT_EEG_STREAM, help="LSL EEG stream name (default: %(default)s)")
    p.add_argument("--marker-name", default=DEFAULT_MARKER_STREAM,
                   help="LSL trigger stream name; 'none' to disable (default: %(default)s)")


def _marker_name(args) -> str | None:
    return None if str(args.marker_name).lower() == "none" else args.marker_name


# --------------------------------------------------------------------------- commands

def cmd_bridge(args) -> None:
    labels = None
    if getattr(args, "montage", None):
        from .montage import biosemi_to_1020
        labels = biosemi_to_1020(args.montage)
    if args.backend == "biosemi":
        from .bridge.biosemi import FakeActiveTwo, LabviewDriver, run_biosemi_bridge
        driver = FakeActiveTwo(speedmode=args.fake_speedmode, preset=args.fake_preset) if args.fake else LabviewDriver(args.lib)
        run_biosemi_bridge(driver, n_electrodes=args.channels, use_ex=not args.no_ex,
                           include_status=args.status, eeg_name=args.name, marker_name=_marker_name(args),
                           trigger_mask=args.trigger_mask, labels=labels, duration=args.duration)
    else:
        from .bridge.actiview import run_actiview_bridge
        run_actiview_bridge(args.host, args.port, args.tcp_channels, args.sfreq, not args.no_trigger, args.ex,
                            args.name, _marker_name(args), args.trigger_mask, labels, args.duration)


def cmd_synth(args) -> None:
    from .sources.synthetic import SyntheticEEG
    gen = SyntheticEEG(args.preset, sfreq=args.sfreq, seed=args.seed, raw_labels=not args.names_1020,
                       paradigm=args.paradigm, burst_s=args.burst_s, period_s=args.period_s)
    if args.record:
        raw = gen.make_raw(args.duration or 300.0)
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        raw.save(args.record, overwrite=True, verbose="error")
        print(f"Wrote {raw.times[-1]:.0f} s synthetic recording to {args.record}")
        return
    from .sources.replay import stream_synthetic
    stream_synthetic(gen, args.name, _marker_name(args), duration=args.duration)


def cmd_replay(args) -> None:
    from .sources.replay import replay_file
    replay_file(args.file, eeg_name=args.name, marker_name=_marker_name(args), loop=args.loop,
                speed=args.speed, event_mask=args.event_mask)


def cmd_discover(args) -> None:
    import pylsl

    from .lsl import describe_environment
    print(describe_environment())
    streams = pylsl.resolve_streams(wait_time=args.wait)
    if not streams:
        print("No LSL streams found. Check: same subnet / firewall (UDP 16571, TCP 16572-16604), "
              "or set KnownPeers with `bciflow lsl-config`.")
        return
    print(f"{'name':<24}{'type':<12}{'ch':>4}{'rate':>9}  {'host':<20}" + ("offset ms" if args.check else ""))
    for s in streams:
        offset = ""
        if args.check:
            try:
                inlet = pylsl.StreamInlet(s)
                inlet.open_stream(timeout=3)
                offset = f"{inlet.time_correction(timeout=3) * 1000:10.2f}"
                if s.nominal_srate() > 0:
                    inlet.pull_chunk(timeout=0.0, max_samples=100000)
                    t0 = time.monotonic()
                    n = 0
                    while time.monotonic() - t0 < 2.0:
                        _, ts = inlet.pull_chunk(timeout=0.2, max_samples=100000)
                        n += len(ts)
                    offset += f"  ({n / 2.0:.0f} Hz measured)"
                inlet.close_stream()
            except Exception as exc:  # report and continue with other streams
                offset = f"error: {exc}"
        print(f"{s.name():<24}{s.type():<12}{s.channel_count():>4}{s.nominal_srate():>9g}  "
              f"{s.hostname():<20}{offset}")


def cmd_lsl_config(args) -> None:
    from .lsl import write_lsl_config
    path = write_lsl_config(args.peers, args.session_id, args.path)
    print(f"Wrote {path}:\n{path.read_text()}")
    print("Use the same file (same SessionID) on every machine, then restart LSL programs.")


def _source_override(graph_path: str, data: str, source_node: str | None, realtime: bool) -> dict:
    import yaml
    spec = yaml.safe_load(Path(graph_path).read_text())
    nodes = spec.get("nodes") or {}
    if source_node is None:
        candidates = [n for n, s in nodes.items() if s.get("type") in ("LSLSource", "FileSource")]
        if len(candidates) != 1:
            raise SystemExit(f"Cannot pick the source node automatically ({candidates}); use --source-node")
        source_node = candidates[0]
    old = nodes[source_node].get("params") or {}
    params = {"path": str(Path(data).resolve()), "realtime": realtime}
    for k in ("stim_channel", "event_mask"):
        if k in old:
            params[k] = old[k]
    return {source_node: {"type": "FileSource", "params": params}}


def cmd_run(args) -> None:
    from .pipeline import Graph, RunContext
    data = args.replay or args.offline
    realtime = args.offline is None
    overrides = _source_override(args.pipeline, data, args.source_node, realtime) if data else None
    ctx = RunContext(mode="online", realtime=realtime)
    graph = Graph.from_yaml(args.pipeline, ctx, overrides)
    graph.run(duration=args.duration, tick_s=args.tick)
    if ctx.store.get("accuracy"):
        print("Accuracy:", {k: round(v, 3) for k, v in ctx.store["accuracy"].items()})


def cmd_train(args) -> None:
    from .pipeline import Graph, RunContext
    ctx = RunContext(mode="train", realtime=False)
    for data in args.data:
        print(f"== {data}")
        graph = Graph.from_yaml(args.pipeline, ctx, _source_override(args.pipeline, data, args.source_node, False))
        graph.run(verbose=False)
    classifiers = ctx.store.get("classifiers", {})
    if not classifiers:
        raise SystemExit("The pipeline has no Classifier node")
    for nid, node in classifiers.items():
        meta = node.fit()
        print(f"[{nid}] {meta['estimator']}: {meta['n_train']} epochs {meta['class_counts']}, "
              f"CV accuracy {meta['cv_accuracy']:.3f} -> {node.p.model_path}")


def cmd_nodes(args) -> None:
    from .models import BUILTIN
    from .pipeline import REGISTRY
    schemas = [cls.schema() for cls in REGISTRY.values()]
    if args.json:
        print(json.dumps({"nodes": schemas, "estimators": {k: v[2] for k, v in BUILTIN.items()}},
                         indent=2, default=str))
        return
    for s in sorted(schemas, key=lambda s: (s["category"], s["type"])):
        ins = ", ".join(f"{k}:{v}" for k, v in s["inputs"].items()) or "-"
        outs = ", ".join(f"{k}:{v}" for k, v in s["outputs"].items()) or "-"
        print(f"{s['category']:<15}{s['type']:<16} in({ins}) out({outs})")
        print(f"{'':<15}  {s['description']}")


def cmd_gui(args) -> None:
    from .gui.server import serve
    serve(args.project, args.port, open_browser=not args.no_browser)


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bciflow", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"bciflow {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bridge", help="Amplifier -> LSL (run next to the amplifier)")
    bsub = b.add_subparsers(dest="backend", required=True)
    bb = bsub.add_parser("biosemi", help="ActiveTwo via BioSemi USB driver library")
    bb.add_argument("--lib", help="Path to Labview_DLL.dll / liblabview_dll (or env BIOSEMI_DRIVER)")
    bb.add_argument("--fake", action="store_true", help="Simulated ActiveTwo, no hardware needed")
    bb.add_argument("--fake-speedmode", type=int, default=4, help="Speed mode of the fake device")
    bb.add_argument("--fake-preset", default="biosemi32", help="Cap simulated by the fake device")
    bb.add_argument("--channels", type=int, help="Number of electrode channels to stream (e.g. 64)")
    bb.add_argument("--no-ex", action="store_true", help="Do not stream EX1-EX8")
    bb.add_argument("--status", action="store_true", help="Add the trigger code as a 'Status' channel")
    ba = bsub.add_parser("actiview", help="ActiView TCP server")
    ba.add_argument("--host", default="127.0.0.1")
    ba.add_argument("--port", type=int, default=778)
    ba.add_argument("--tcp-channels", type=int, required=True,
                    help="Channels per sample as set in ActiView's TCP tab (incl. trigger channel)")
    ba.add_argument("--sfreq", type=float, required=True, help="Sampling rate as set in ActiView")
    ba.add_argument("--ex", type=int, default=8, help="EX channels included (default 8)")
    ba.add_argument("--no-trigger", action="store_true", help="ActiView sends no trigger channel")
    for p in (bb, ba):
        _add_stream_names(p)
        p.add_argument("--montage", help="Label channels with 10-20 names (biosemi32, biosemi64, ...)")
        p.add_argument("--trigger-mask", type=_int_auto, default=0xFF, help="Trigger bit mask (default 0xFF)")
        p.add_argument("--duration", type=float, help="Stop after N seconds")
    b.set_defaults(func=cmd_bridge)

    s = sub.add_parser("synth", help="Synthetic motor-imagery EEG")
    _add_stream_names(s)
    s.add_argument("--preset", default="biosemi32", help="Montage (biosemi16/32/64/...)")
    s.add_argument("--sfreq", type=float, default=512.0)
    s.add_argument("--seed", type=int)
    s.add_argument("--names-1020", action="store_true", help="10-20 channel names instead of A1..")
    s.add_argument("--duration", type=float, help="Seconds (streaming: stop after; --record: length)")
    s.add_argument("--record", help="Write a .fif recording instead of streaming")
    s.add_argument("--paradigm", default="motor-imagery", choices=["motor-imagery", "alpha-bursts"],
                   help="motor-imagery: left/right cues 1/2; alpha-bursts: alpha on (1) / off (2)")
    s.add_argument("--burst-s", type=float, default=10.0, help="alpha-bursts: burst length (s)")
    s.add_argument("--period-s", type=float, default=20.0, help="alpha-bursts: one burst every N s")
    s.set_defaults(func=cmd_synth)

    r = sub.add_parser("replay", help="Replay a recording as LSL")
    r.add_argument("file")
    _add_stream_names(r)
    r.add_argument("--loop", action="store_true")
    r.add_argument("--speed", type=float, default=1.0)
    r.add_argument("--event-mask", type=_int_auto, default=0xFF)
    r.set_defaults(func=cmd_replay)

    d = sub.add_parser("discover", help="List LSL streams on the network")
    d.add_argument("--wait", type=float, default=2.0)
    d.add_argument("--check", action="store_true", help="Measure clock offset and effective rate")
    d.set_defaults(func=cmd_discover)

    c = sub.add_parser("lsl-config", help="Write lsl_api.cfg for multi-machine setups")
    c.add_argument("--peers", nargs="*", default=[], help="IP addresses/hostnames of all lab machines")
    c.add_argument("--session-id", help="Isolate this lab's streams from others on the network")
    c.add_argument("--path", help="Config path (default: ~/lsl_api/lsl_api.cfg)")
    c.set_defaults(func=cmd_lsl_config)

    for name, func, hlp in (("run", cmd_run, "Run a pipeline"), ("train", cmd_train, "Train classifiers offline")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("pipeline")
        p.add_argument("--source-node", help="Node to replace with the file source (default: auto)")
        if name == "run":
            g = p.add_mutually_exclusive_group()
            g.add_argument("--replay", help="Use this recording in real time instead of LSL")
            g.add_argument("--offline", help="Use this recording as fast as possible")
            p.add_argument("--duration", type=float)
            p.add_argument("--tick", type=float, default=0.02, help="Seconds per processing tick")
        else:
            p.add_argument("--data", nargs="+", required=True, help="Training recording(s)")
        p.set_defaults(func=func)

    gu = sub.add_parser("gui", help="Graphical pipeline editor (opens the browser)")
    gu.add_argument("--project", default=".", help="Folder with pipelines/, data/, models/")
    gu.add_argument("--port", type=int, default=8765)
    gu.add_argument("--no-browser", action="store_true")
    gu.set_defaults(func=cmd_gui)

    n = sub.add_parser("nodes", help="List available pipeline nodes")
    n.add_argument("--json", action="store_true")
    n.set_defaults(func=cmd_nodes)
    return ap


def _enable_interrupts() -> None:
    """Make Ctrl+C / the editor's Stop end every command cleanly (KeyboardInterrupt), so
    pipelines close their nodes (e.g. Recorder saves). Processes started in the background
    inherit an *ignored* SIGINT, and on Windows the editor sends CTRL_BREAK instead."""
    import signal

    signal.signal(signal.SIGINT, signal.default_int_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)


def main(argv: list[str] | None = None) -> None:
    _enable_interrupts()
    args = build_parser().parse_args(argv)
    try:
        import mne
        mne.set_log_level("WARNING")
    except ImportError:  # bridge-only installs
        pass
    try:
        args.func(args)
    except (FileNotFoundError, TimeoutError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
