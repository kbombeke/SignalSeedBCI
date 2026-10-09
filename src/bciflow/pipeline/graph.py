"""Pipeline graph: load from YAML, validate, propagate metadata, run.

YAML format::

    name: My pipeline
    nodes:
      src:  {type: LSLSource, params: {signal_name: BioSemi}}
      filt: {type: BandpassFilter, params: {l_freq: 8, h_freq: 30}}
    edges:
      - src.signal -> filt.in
    layout: {...}   # optional, free-form (GUI node positions)

Nodes run in topological order once per tick. Online, a tick is every
``tick_s`` seconds; offline, ticks run back-to-back until all sources are finished.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import nodes as _nodes  # noqa: F401  (registers built-in nodes)
from .node import REGISTRY, Node, RunContext


@dataclass(frozen=True)
class Edge:
    src: str
    src_port: str
    dst: str
    dst_port: str

    @classmethod
    def parse(cls, text: str) -> "Edge":
        try:
            a, b = (s.strip() for s in text.split("->"))
            src, sp = a.split(".")
            dst, dp = b.split(".")
        except ValueError:
            raise ValueError(f"Bad edge {text!r}; expected 'node.port -> node.port'") from None
        return cls(src, sp, dst, dp)

    def __str__(self):
        return f"{self.src}.{self.src_port} -> {self.dst}.{self.dst_port}"


class Graph:
    def __init__(self, spec: dict, context: RunContext | None = None, base_dir: Path | None = None):
        self.spec = spec
        self.name = spec.get("name", "pipeline")
        self.context = context or RunContext()
        self.base_dir = base_dir or Path.cwd()
        self.nodes: dict[str, Node] = {}
        for nid, nspec in (spec.get("nodes") or {}).items():
            ntype = nspec.get("type")
            if ntype not in REGISTRY:
                raise ValueError(f"Node '{nid}': unknown type {ntype!r}. Available: {sorted(REGISTRY)}")
            params = self._resolve_paths(REGISTRY[ntype], dict(nspec.get("params") or {}))
            self.nodes[nid] = REGISTRY[ntype](nid, params, self.context)
        self.edges = [Edge.parse(e) for e in spec.get("edges") or []]
        self._validate()
        self.order = self._toposort()
        self.meta: dict[tuple[str, str], object] = {}

    @classmethod
    def from_yaml(cls, path: str | Path, context: RunContext | None = None,
                  overrides: dict[str, dict] | None = None) -> "Graph":
        path = Path(path)
        spec = yaml.safe_load(path.read_text())
        for nid, nspec in (overrides or {}).items():
            spec.setdefault("nodes", {})[nid] = nspec
        return cls(spec, context, base_dir=path.parent)

    def _resolve_paths(self, ntype: type[Node], params: dict) -> dict:
        for k, v in params.items():
            spec = ntype.params.get(k)
            if spec and spec.type == "path" and isinstance(v, str) and v and not Path(v).is_absolute():
                params[k] = str((self.base_dir / v).resolve())
        return params

    # -- structure ---------------------------------------------------------------
    def _validate(self) -> None:
        seen_inputs = defaultdict(list)
        for e in self.edges:
            for nid in (e.src, e.dst):
                if nid not in self.nodes:
                    raise ValueError(f"Edge '{e}': unknown node '{nid}'")
            src, dst = self.nodes[e.src], self.nodes[e.dst]
            if e.src_port not in src.outputs:
                raise ValueError(f"Edge '{e}': {e.src} has no output '{e.src_port}' (has {list(src.outputs)})")
            if e.dst_port not in dst.inputs:
                raise ValueError(f"Edge '{e}': {e.dst} has no input '{e.dst_port}' (has {list(dst.inputs)})")
            kind = src.outputs[e.src_port]
            if kind not in dst.inputs[e.dst_port].split("|"):
                raise ValueError(f"Edge '{e}': cannot connect {kind} to {dst.inputs[e.dst_port]}")
            seen_inputs[(e.dst, e.dst_port)].append(e)
        for (nid, port), es in seen_inputs.items():
            if len(es) > 1 and self.nodes[nid].inputs[port] != "markers":
                raise ValueError(f"{nid}.{port} has {len(es)} incoming edges; only markers inputs can merge")
        for nid, node in self.nodes.items():
            for port in node.inputs:
                if (nid, port) not in seen_inputs and port not in node.optional_inputs:
                    raise ValueError(f"Node '{nid}': required input '{port}' is not connected")

    def _toposort(self) -> list[str]:
        indeg = {n: 0 for n in self.nodes}
        out = defaultdict(list)
        for e in self.edges:
            indeg[e.dst] += 1
            out[e.src].append(e.dst)
        ready = [n for n in self.nodes if indeg[n] == 0]
        order = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in out[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
        if len(order) != len(self.nodes):
            raise ValueError("Pipeline graph has a cycle")
        return order

    def sources(self) -> list[str]:
        return [n for n in self.order if self.nodes[n].is_source]

    # -- execution -----------------------------------------------------------------
    def setup(self) -> None:
        for nid in self.order:
            node = self.nodes[nid]
            in_meta = {e.dst_port: self.meta.get((e.src, e.src_port)) for e in self.edges if e.dst == nid}
            out_meta = node.setup(in_meta) or {}
            for port, m in out_meta.items():
                self.meta[(nid, port)] = m

    def tick(self) -> int:
        """Run every node once. Returns the number of items produced."""
        pending: dict[tuple[str, str], list] = defaultdict(list)
        produced = 0
        for nid in self.order:
            node = self.nodes[nid]
            inputs = {port: pending.pop((nid, port), []) for port in node.inputs}
            outputs = node.process(inputs) or {}
            for port, items in outputs.items():
                if not items:
                    continue
                produced += len(items)
                for e in self.edges:
                    if e.src == nid and e.src_port == port:
                        pending[(e.dst, e.dst_port)].extend(items)
        return produced

    def run(self, duration: float | None = None, tick_s: float = 0.02, verbose: bool = True) -> None:
        self.setup()
        realtime = self.context.realtime
        if verbose:
            print(f"Running '{self.name}' ({self.context.mode}, {'real-time' if realtime else 'as fast as possible'})"
                  + (". Ctrl+C to stop." if realtime else ""))
        t0 = time.monotonic()
        slow_ticks = 0
        n_ticks = 0
        try:
            while True:
                t_tick = time.monotonic()
                produced = self.tick()
                n_ticks += 1
                if duration is not None and time.monotonic() - t0 >= duration:
                    break
                srcs = self.sources()
                if srcs and all(self.nodes[s].finished for s in srcs) and produced == 0:
                    break
                if realtime:
                    spent = time.monotonic() - t_tick
                    if spent < tick_s:
                        time.sleep(tick_s - spent)
                    elif n_ticks > 5:  # first ticks include lazy imports
                        slow_ticks += 1
                        if slow_ticks in (1, 10, 100) or slow_ticks % 1000 == 0:
                            print(f"[graph] warning: tick took {spent * 1000:.0f} ms (> {tick_s * 1000:.0f} ms); "
                                  f"{slow_ticks} slow ticks so far")
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self) -> None:
        for nid in self.order:
            try:
                self.nodes[nid].close()
            except Exception as exc:  # keep closing the others
                print(f"[{nid}] error while closing: {exc}")
