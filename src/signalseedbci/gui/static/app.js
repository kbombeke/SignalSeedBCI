/* signalseedbci graphical pipeline editor. No dependencies.
 *
 * Model: S.nodes (Map id -> {id, type, params, x, y}), S.edges ([{src, sp, dst, dp}]).
 * Serialised to the same YAML spec the CLI runs (nodes / edges / layout).
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};
const SVG = "http://www.w3.org/2000/svg";

const NODE_W = 196, HEAD_H = 33, ROW_H = 22;
const KINDS = ["signal", "markers", "epochs", "features", "predictions"];

const S = {
  schemas: {}, estimators: {}, files: [],
  path: null, name: "Untitled", description: "", extra: {},
  nodes: new Map(), edges: [],
  selected: null,           // {kind: "node", id} | {kind: "edge", index}
  view: { x: 40, y: 30, k: 1 },
  dirty: false,
  validation: { ok: true, errors: [], warnings: [], node_info: {} },
  procs: [], activeProc: null, logSince: {}, runProcId: null,
};

// ------------------------------------------------------------------ API
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, isErr = false, ms = 3200) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

// ------------------------------------------------------------------ modal
function modal(title, bodyNodes, okLabel = "OK") {
  return new Promise((resolve) => {
    $("#modalTitle").textContent = title;
    const body = $("#modalBody");
    body.replaceChildren(...bodyNodes);
    $("#modalOk").textContent = okLabel;
    $("#modal").hidden = false;
    const first = body.querySelector("input, select");
    if (first) setTimeout(() => first.focus(), 30);
    const done = (v) => {
      $("#modal").hidden = true;
      $("#modalOk").onclick = $("#modalCancel").onclick = null;
      document.removeEventListener("keydown", onKey, true);
      resolve(v);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); done(false); }
      if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") { e.stopPropagation(); e.preventDefault(); done(true); }
    };
    document.addEventListener("keydown", onKey, true);
    $("#modalOk").onclick = () => done(true);
    $("#modalCancel").onclick = () => done(false);
  });
}

async function askText(title, label, value = "") {
  const input = el("input", { type: "text", value });
  const ok = await modal(title, [el("div", { class: "field" }, el("label", {}, label), input)]);
  return ok ? input.value.trim() : null;
}

// ------------------------------------------------------------------ schema helpers
const schemaOf = (type) => S.schemas[type];
const catColor = (cat) => `var(--c-${cat}, var(--c-misc))`;
const kindColor = (kind) => `var(--k-${kind.split("|")[0]})`;

// nodes with a live view in their box: a stream plot, or the eyes animation
const NODE_VIEWS = { LSLSignalOut: "scope", SpectrumOut: "scope", EyesFeedback: "eyes", GazeFeedback: "gaze" };
const SCOPE_NODES = new Set(Object.keys(NODE_VIEWS));
const paramOf = (n, k) => n.params[k] ?? schemaOf(n.type).params[k]?.default;
const gazeParams = (n) => ({ up: paramOf(n, "up_label"), down: paramOf(n, "down_label"),
  left: paramOf(n, "left_label"), right: paramOf(n, "right_label"), hold: paramOf(n, "hold_s") });
function openNodeView(n) {
  if (NODE_VIEWS[n.type] === "eyes") openEyesWindow(miniStreamName(n), paramOf(n, "closed_label"), !!paramOf(n, "graded"));
  else if (NODE_VIEWS[n.type] === "gaze") openEyesWindow(miniStreamName(n), "closed", !!paramOf(n, "graded"), gazeParams(n));
  else openScopeWindow(miniStreamName(n), n.type === "LSLSignalOut" ? paramOf(n, "display") : null);
}
// nodes can be resized: n.w (width) for all nodes, n.vh (height of the live view) for view nodes
const VIEW_H = 86, VIEW_PAD = 32, MIN_W = NODE_W, MAX_W = 1600, MIN_VH = 60, MAX_VH = 1200;
const nodeW = (n) => n.w || NODE_W;
const viewH = (n) => n.vh || VIEW_H;
const nodeExtra = (type, n) => (SCOPE_NODES.has(type) ? (n ? viewH(n) : VIEW_H) + VIEW_PAD : 0);
const nodeH = (n) => HEAD_H + ROW_H * nodeRows(schemaOf(n.type)) + nodeExtra(n.type, n);

function nodeRows(schema) {
  return Math.max(Object.keys(schema.inputs).length, Object.keys(schema.outputs).length, 1);
}
function portPos(node, port, dir) {
  const sc = schemaOf(node.type);
  const names = Object.keys(dir === "in" ? sc.inputs : sc.outputs);
  const i = Math.max(0, names.indexOf(port));
  return { x: node.x + (dir === "in" ? 0 : nodeW(node)), y: node.y + HEAD_H + ROW_H * i + ROW_H / 2 };
}

function uniqueId(base) {
  base = base.replace(/[^A-Za-z0-9_]/g, "").replace(/^[0-9]+/, "") || "node";
  if (!S.nodes.has(base)) return base;
  for (let i = 2; ; i++) if (!S.nodes.has(base + i)) return base + i;
}
const DEFAULT_IDS = {
  LSLSource: "src", FileSource: "src", LSLMarkerSource: "markers", BandpassFilter: "band", NotchFilter: "notch",
  Rereference: "ref", PickChannels: "pick", RenameChannels: "names", Decimate: "down", Epocher: "epochs",
  SlidingWindow: "windows", BandPower: "power", Classifier: "clf", PredictionOut: "feedback", Logger: "log", SpectrumOut: "spectrum",
  ThresholdClassifier: "state", EyesFeedback: "eyes", GazeFeedback: "gaze", TimeWindowMeans: "means",
  LSLSignalOut: "out", Recorder: "rec", SignalScript: "script", EpochScript: "script",
};

function paramSummary(node) {
  const sc = schemaOf(node.type);
  const parts = [];
  for (const [k, v] of Object.entries(node.params)) {
    if (!(k in sc.params)) continue;
    let s = typeof v === "object" ? JSON.stringify(v) : String(v);
    if (sc.params[k].type === "path") s = s.split("/").pop();
    parts.push(`${k}=${s}`);
  }
  return parts.join("  ");
}

// ------------------------------------------------------------------ model <-> spec
function toSpec() {
  const nodes = {}, layout = {};
  for (const n of S.nodes.values()) {
    nodes[n.id] = Object.keys(n.params).length ? { type: n.type, params: n.params } : { type: n.type };
    layout[n.id] = [Math.round(n.x), Math.round(n.y)];
    if (n.w || n.vh) layout[n.id].push(Math.round(nodeW(n)), Math.round(SCOPE_NODES.has(n.type) ? viewH(n) : 0));
  }
  const spec = { name: S.name };
  if (S.description) spec.description = S.description;
  Object.assign(spec, S.extra, { nodes, edges: S.edges.map((e) => `${e.src}.${e.sp} -> ${e.dst}.${e.dp}`), layout });
  return spec;
}

function fromSpec(spec, path) {
  loadSpecState(spec);
  S.path = path;
  if ([...S.nodes.values()].some((n) => isNaN(n.x))) autoLayout(true);
  resolveOverlaps();
  S.selected = null; S.dirty = false;
  renderAll(); fitView(); resetHistory(); validateSoon(0);
}

function loadSpecState(spec) {
  S.name = spec.name || "Untitled"; S.description = spec.description || "";
  S.extra = {};
  for (const [k, v] of Object.entries(spec)) {
    if (!["name", "description", "nodes", "edges", "layout"].includes(k)) S.extra[k] = v;
  }
  S.nodes = new Map(); S.edges = [];
  const layout = spec.layout || {};
  for (const [id, ns] of Object.entries(spec.nodes || {})) {
    if (!schemaOf(ns.type)) { toast(`Unknown node type ${ns.type} (${id}) skipped`, true); continue; }
    const [x, y, w, vh] = layout[id] || [NaN, NaN];
    const node = { id, type: ns.type, params: { ...(ns.params || {}) }, x, y };
    if (w && w > NODE_W) node.w = Math.min(MAX_W, w);
    if (vh && SCOPE_NODES.has(ns.type) && vh !== VIEW_H) node.vh = Math.max(MIN_VH, Math.min(MAX_VH, vh));
    S.nodes.set(id, node);
  }
  for (const text of spec.edges || []) {
    const m = /^\s*(\w+)\.(\w+)\s*->\s*(\w+)\.(\w+)\s*$/.exec(text);
    if (m && S.nodes.has(m[1]) && S.nodes.has(m[3])) S.edges.push({ src: m[1], sp: m[2], dst: m[3], dp: m[4] });
  }
}

function autoLayout(onlyMissing = false) {
  const depth = new Map();
  const ids = [...S.nodes.keys()];
  ids.forEach((id) => depth.set(id, 0));
  for (let pass = 0; pass < ids.length; pass++) {
    let changed = false;
    for (const e of S.edges) {
      const d = depth.get(e.src) + 1;
      if (d > depth.get(e.dst)) { depth.set(e.dst, d); changed = true; }
    }
    if (!changed) break;
  }
  // long chains wrap into horizontal bands so nodes stay readable
  const wrapW = $("#canvasWrap").getBoundingClientRect().width || 1100;
  const perBand = Math.max(3, Math.floor((wrapW - 40) / 250));
  const cols = new Map();
  for (const id of ids) {
    const d = depth.get(id);
    if (!cols.has(d)) cols.set(d, []);
    cols.get(d).push(id);
  }
  const colHeight = (col) => col.reduce((h, id) => h + nodeH(S.nodes.get(id)) + 60, 0);
  const bandTop = [30];
  const maxDepth = Math.max(0, ...cols.keys());
  for (let b = 1; b <= Math.floor(maxDepth / perBand); b++) {
    const prev = [...cols].filter(([d]) => Math.floor(d / perBand) === b - 1).map(([, c]) => colHeight(c));
    bandTop.push(bandTop[b - 1] + Math.max(...prev, 100) + 40);
  }
  const colW = new Map([...cols].map(([d, col]) => [d, Math.max(...col.map((id) => nodeW(S.nodes.get(id))))]));
  const colX = (d) => {
    let x = 30;
    for (let k = d - (d % perBand); k < d; k++) x += (colW.get(k) || NODE_W) + 54;
    return x;
  };
  for (const [d, col] of cols) {
    let y = bandTop[Math.floor(d / perBand)];
    for (const id of col) {
      const n = S.nodes.get(id);
      if (!onlyMissing || isNaN(n.x)) { n.x = colX(d); n.y = y; }
      y += nodeH(n) + 60;
    }
  }
}

function markDirty() {
  S.dirty = true;
  $("#dirtyDot").hidden = false;
  commitHistory();
  validateSoon();
}

// ------------------------------------------------------------------ undo / redo
const H = { stack: [], index: -1 };
function commitHistory() {
  const snap = JSON.stringify(toSpec());
  if (H.stack[H.index] === snap) return;
  H.stack.splice(H.index + 1);
  H.stack.push(snap);
  if (H.stack.length > 200) H.stack.shift();
  H.index = H.stack.length - 1;
}
function resetHistory() { H.stack = []; H.index = -1; commitHistory(); }
function restoreHistory(delta) {
  const i = H.index + delta;
  if (i < 0 || i >= H.stack.length) return;
  H.index = i;
  const spec = JSON.parse(H.stack[i]);
  const keepPath = S.path, keepView = { ...S.view };
  loadSpecState(spec);
  S.path = keepPath; S.view = keepView;
  S.dirty = true;
  if (S.selected?.kind === "node" && !S.nodes.has(S.selected.id)) S.selected = null;
  if (S.selected?.kind === "edge") S.selected = null;
  renderAll(); validateSoon(0);
}

// ------------------------------------------------------------------ rendering
function renderAll() {
  renderNodes(); renderEdges(); renderInspector(); applyView();
  $("#dirtyDot").hidden = !S.dirty;
}

function nodeHasError(id) {
  return S.validation.errors.some((m) =>
    m.includes(`'${id}'`) || m.startsWith(`${id}:`) || m.startsWith(`${id} (`) ||
    new RegExp(`(^|[ '])${id}\\.\\w+ ->|-> ${id}\\.`).test(m));
}

function renderNodes() {
  const host = $("#nodes");
  host.replaceChildren();
  const connected = new Set(S.edges.flatMap((e) => [`${e.src}.out.${e.sp}`, `${e.dst}.in.${e.dp}`]));
  for (const n of S.nodes.values()) {
    const sc = schemaOf(n.type);
    const sel = S.selected?.kind === "node" && S.selected.id === n.id;
    const div = el("div", {
      class: "node" + (sel ? " selected" : "") + (nodeHasError(n.id) ? " error" : ""),
      style: `left:${n.x}px;top:${n.y}px;width:${nodeW(n)}px;--cat:${catColor(sc.category)}`, "data-id": n.id,
    });
    const head = el("div", { class: "node-head", title: sc.description },
      el("span", { class: "nid" }, n.id), el("span", { class: "ntype" }, n.type));
    if (SCOPE_NODES.has(n.type)) {
      head.append(el("button", {
        class: "node-btn", title: "Open this plot in a separate window",
        onmousedown: (e) => e.stopPropagation(), onclick: () => openNodeView(n),
      }, "⧉"));
    }
    head.addEventListener("mousedown", (ev) => startNodeDrag(ev, n));
    div.append(head);
    const ins = Object.entries(sc.inputs), outs = Object.entries(sc.outputs);
    const ports = el("div", { class: "ports" });
    for (let i = 0; i < nodeRows(sc); i++) {
      const row = el("div", { class: "port-row" });
      if (ins[i]) {
        const [p, kind] = ins[i];
        const optional = sc.optional_inputs.includes(p);
        const dot = el("span", {
          class: "dot" + (connected.has(`${n.id}.in.${p}`) ? " connected" : ""),
          "data-node": n.id, "data-port": p, "data-dir": "in", "data-kind": kind,
          style: `--kind:${kindColor(kind)}`, title: `${p}: ${kind}${optional ? " (optional)" : ""}`,
        });
        dot.addEventListener("mousedown", (ev) => startInputDetach(ev, n.id, p));
        row.append(el("span", { class: "port in" + (optional ? " optional" : "") }, dot, el("span", { class: "pname" }, p)));
      }
      if (outs[i]) {
        const [p, kind] = outs[i];
        const dot = el("span", {
          class: "dot" + (connected.has(`${n.id}.out.${p}`) ? " connected" : ""),
          "data-node": n.id, "data-port": p, "data-dir": "out", "data-kind": kind,
          style: `--kind:${kindColor(kind)}`, title: `${p}: ${kind}`,
        });
        dot.addEventListener("mousedown", (ev) => startConnect(ev, n.id, p, kind));
        row.append(el("span", { class: "port out" }, el("span", { class: "pname" }, p), dot));
      }
      ports.append(row);
    }
    div.append(ports, el("div", { class: "node-summary" }, paramSummary(n)));
    if (SCOPE_NODES.has(n.type)) {
      const plot = el("div", { class: "node-scope", title: "Live view of the stream this node publishes — double-click to open in a window" },
        el("canvas", { class: "mini", style: `height:${viewH(n)}px` }), el("div", { class: "mini-status" }, ""));
      div.append(plot);
    }
    const handle = el("div", {
      class: "resize" + (SCOPE_NODES.has(n.type) ? "" : " width-only"),
      title: SCOPE_NODES.has(n.type) ? "Drag to resize · double-click to reset" : "Drag to change the width · double-click to reset",
    });
    handle.addEventListener("mousedown", (ev) => startResize(ev, n));
    div.append(handle);
    div.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      ev.stopPropagation();
      select({ kind: "node", id: n.id });
    });
    host.append(div);
  }
  const wrap = $("#canvasWrap");
  $(".empty-canvas", wrap)?.remove();
  if (!S.nodes.size) {
    wrap.append(el("div", { class: "empty-canvas" },
      el("div", {}, el("img", { class: "watermark", src: "img/logo.png", alt: "" }), el("strong", {}, "Empty pipeline"), el("br"),
        "Drag nodes from the left, starting with a source (LSLSource).")));
  }
}

function edgePath(a, b) {
  if (b.x < a.x + 30 && Math.abs(b.y - a.y) > 40) {
    // target is left of the source (wrapped rows): leave right, cross over between rows, enter left
    const midY = (a.y + b.y) / 2, r = 40;
    return `M${a.x},${a.y} C${a.x + r},${a.y} ${a.x + r},${midY} ${a.x},${midY} ` +
           `L${b.x},${midY} C${b.x - r},${midY} ${b.x - r},${b.y} ${b.x},${b.y}`;
  }
  const dx = Math.max(40, Math.abs(b.x - a.x) * 0.5);
  return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`;
}

function renderEdges() {
  const svg = $("#edges");
  svg.replaceChildren();
  S.edges.forEach((e, i) => {
    const a = S.nodes.get(e.src), b = S.nodes.get(e.dst);
    if (!a || !b) return;
    const kind = schemaOf(a.type).outputs[e.sp] || "signal";
    const d = edgePath(portPos(a, e.sp, "out"), portPos(b, e.dp, "in"));
    const sel = S.selected?.kind === "edge" && S.selected.index === i;
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    path.setAttribute("class", "edge" + (sel ? " selected" : ""));
    path.setAttribute("stroke", kindColor(kind));
    const hit = document.createElementNS(SVG, "path");
    hit.setAttribute("d", d);
    hit.setAttribute("class", "hit");
    hit.addEventListener("mousedown", (ev) => { ev.stopPropagation(); select({ kind: "edge", index: i }); });
    svg.append(path, hit);
  });
}

function applyView() {
  const { x, y, k } = S.view;
  $("#canvas").style.transform = `translate(${x}px, ${y}px) scale(${k})`;
  $("#canvasWrap").style.backgroundPosition = `${x}px ${y}px`;
  $("#canvasWrap").style.backgroundSize = `${22 * k}px ${22 * k}px`;
}

function select(sel) {
  S.selected = sel;
  renderNodes(); renderEdges(); renderInspector();
}

// ------------------------------------------------------------------ interactions
function toCanvas(ev) {
  const r = $("#canvasWrap").getBoundingClientRect();
  return { x: (ev.clientX - r.left - S.view.x) / S.view.k, y: (ev.clientY - r.top - S.view.y) / S.view.k };
}

function drag(onMove, onUp) {
  const move = (ev) => onMove(ev);
  const up = (ev) => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    onUp?.(ev);
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

function startNodeDrag(ev, n) {
  if (ev.button !== 0) return;
  ev.stopPropagation();
  ev.preventDefault();
  select({ kind: "node", id: n.id });
  const start = toCanvas(ev), ox = n.x, oy = n.y;
  let moved = false;
  const div = $(`.node[data-id="${CSS.escape(n.id)}"]`);
  drag((e) => {
    const p = toCanvas(e);
    n.x = Math.round(ox + p.x - start.x);
    n.y = Math.round(oy + p.y - start.y);
    div.style.left = n.x + "px";
    div.style.top = n.y + "px";
    moved = true;
    renderEdges();
  }, () => { if (moved) { S.dirty = true; $("#dirtyDot").hidden = false; commitHistory(); } });
}

function startResize(ev, n) {
  if (ev.button !== 0) return;
  ev.stopPropagation(); ev.preventDefault();
  select({ kind: "node", id: n.id });
  const start = toCanvas(ev), ow = nodeW(n), ovh = viewH(n);
  const div = $(`.node[data-id="${CSS.escape(n.id)}"]`);
  const canvas = div.querySelector("canvas.mini");
  const view = SCOPE_NODES.has(n.type);
  let moved = false;
  document.body.classList.add(view ? "resizing" : "resizing-w");
  drag((e) => {
    const p = toCanvas(e);
    n.w = Math.round(Math.max(MIN_W, Math.min(MAX_W, ow + p.x - start.x)));
    div.style.width = n.w + "px";
    if (view) {
      n.vh = Math.round(Math.max(MIN_VH, Math.min(MAX_VH, ovh + p.y - start.y)));
      canvas.style.height = n.vh + "px";
    }
    moved = true;
    renderEdges();
  }, () => {
    document.body.classList.remove("resizing", "resizing-w");
    if (!moved) return;
    if (n.w === NODE_W) delete n.w;
    if (n.vh === VIEW_H) delete n.vh;
    if (makeRoom(n)) { renderNodes(); renderEdges(); }
    S.dirty = true; $("#dirtyDot").hidden = false; commitHistory();
  });
}

/** Move nodes that overlap `n` out of the way (right if beside it, down if below),
 *  cascading to nodes those then hit. Returns true if anything moved. */
function makeRoom(n) {
  const gap = 30, queue = [n], movedAny = new Set();
  for (let guard = 0; queue.length && guard < 400; guard++) {
    const a = queue.shift(), A = nodeBox(a);
    for (const m of S.nodes.values()) {
      if (m === a || m === n) continue;
      const B = nodeBox(m);
      const overlap = A.x0 < B.x1 + 10 && A.x1 + 10 > B.x0 && A.y0 < B.y1 + 10 && A.y1 + 10 > B.y0;
      if (!overlap) continue;
      if (B.y0 < A.y0 + HEAD_H + 10 && B.x0 > A.x0) m.x = A.x1 + gap;   // beside: push right
      else m.y = A.y1 + gap;                                           // below: push down
      movedAny.add(m.id);
      queue.push(m);
    }
  }
  return movedAny.size > 0;
}

function tempPath() {
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("class", "temp");
  $("#edges").append(p);
  return p;
}

function dotUnder(ev) {
  const t = document.elementFromPoint(ev.clientX, ev.clientY);
  return t && t.classList.contains("dot") ? t : null;
}

function accepts(dstKind, srcKind) { return dstKind.split("|").includes(srcKind); }

function connectDrag(from, kind, initialTarget = null) {
  const path = tempPath();
  path.setAttribute("stroke", kindColor(kind));
  let hover = null;
  const draw = (e) => {
    const p = toCanvas(e);
    path.setAttribute("d", edgePath(from, p));
    const t = dotUnder(e);
    if (hover && hover !== t) hover.classList.remove("target");
    hover = null;
    if (t && t.dataset.dir === "in" && accepts(t.dataset.kind, kind)) { t.classList.add("target"); hover = t; }
  };
  if (initialTarget) draw(initialTarget);
  return new Promise((resolve) => drag(draw, (e) => {
    path.remove();
    hover?.classList.remove("target");
    const t = dotUnder(e);
    resolve(t && t.dataset.dir === "in" ? t : null);
  }));
}

async function startConnect(ev, srcId, sp, kind) {
  if (ev.button !== 0) return;
  ev.stopPropagation(); ev.preventDefault();
  const from = portPos(S.nodes.get(srcId), sp, "out");
  const target = await connectDrag(from, kind);
  if (target) addEdge(srcId, sp, target.dataset.node, target.dataset.port);
}

async function startInputDetach(ev, dstId, dp) {
  // dragging from a connected input picks the edge up so it can be moved or dropped
  if (ev.button !== 0) return;
  ev.stopPropagation(); ev.preventDefault();
  const idx = S.edges.findIndex((e) => e.dst === dstId && e.dp === dp);
  if (idx < 0) return;
  const e = S.edges[idx];
  S.edges.splice(idx, 1);
  renderEdges();
  const kind = schemaOf(S.nodes.get(e.src).type).outputs[e.sp];
  const target = await connectDrag(portPos(S.nodes.get(e.src), e.sp, "out"), kind, ev);
  if (target) addEdge(e.src, e.sp, target.dataset.node, target.dataset.port, true);
  else { markDirty(); renderNodes(); renderEdges(); }
}

function addEdge(src, sp, dst, dp, quiet = false) {
  if (src === dst) return toast("Cannot connect a node to itself", true);
  const srcKind = schemaOf(S.nodes.get(src).type).outputs[sp];
  const dstKind = schemaOf(S.nodes.get(dst).type).inputs[dp];
  if (!accepts(dstKind, srcKind)) return toast(`Cannot connect ${srcKind} to ${dstKind}`, true);
  if (S.edges.some((e) => e.src === src && e.sp === sp && e.dst === dst && e.dp === dp)) return;
  if (dstKind !== "markers") {
    const old = S.edges.findIndex((e) => e.dst === dst && e.dp === dp);
    if (old >= 0) { S.edges.splice(old, 1); if (!quiet) toast(`Replaced the previous connection to ${dst}.${dp}`); }
  }
  if (createsCycle(src, dst)) return toast("That connection would create a cycle", true);
  S.edges.push({ src, sp, dst, dp });
  markDirty(); renderNodes(); renderEdges();
}

function createsCycle(src, dst) {
  const seen = new Set(), stack = [dst];
  while (stack.length) {
    const n = stack.pop();
    if (n === src) return true;
    if (seen.has(n)) continue;
    seen.add(n);
    S.edges.filter((e) => e.src === n).forEach((e) => stack.push(e.dst));
  }
  return false;
}

function nodeBox(n) {
  return { x0: n.x, y0: n.y, x1: n.x + nodeW(n), y1: n.y + nodeH(n) + 26 };
}
function overlaps(x, y, type, ignore) {
  const h = HEAD_H + ROW_H * nodeRows(schemaOf(type)) + nodeExtra(type) + 26;
  return [...S.nodes.values()].some((n) => {
    if (n.id === ignore) return false;
    const b = nodeBox(n);
    return x < b.x1 + 20 && x + NODE_W + 20 > b.x0 && y < b.y1 + 16 && y + h + 16 > b.y0;
  });
}
function resolveOverlaps() {
  // saved layouts may predate taller nodes (e.g. the plot in LSLSignalOut): push nodes down
  const placed = [];
  for (const n of [...S.nodes.values()].sort((a, b) => a.y - b.y || a.x - b.x)) {
    const h = nodeH(n) + 26;
    for (let guard = 0; guard < 200; guard++) {
      const hit = placed.find((m) => n.x < m.x + nodeW(m) + 20 && n.x + nodeW(n) + 20 > m.x &&
                                      n.y < m.y + nodeH(m) + 26 + 16 && n.y + h + 16 > m.y);
      if (!hit) break;
      n.y = hit.y + nodeH(hit) + 26 + 20;
    }
    placed.push(n);
  }
}

function freeSpot(x, y, type) {
  for (let i = 0; i < 60 && overlaps(x, y, type); i++) y += 40;
  return { x, y };
}

function addNode(type, at) {
  const sc = schemaOf(type);
  const id = uniqueId(DEFAULT_IDS[type] || type.toLowerCase());
  const prev = !at && S.selected?.kind === "node" ? S.nodes.get(S.selected.id) : null;
  let p;
  if (at) p = at;
  else if (prev) p = freeSpot(prev.x + 250, prev.y, type);  // continue the chain to the right
  else {
    const r = $("#canvasWrap").getBoundingClientRect();
    p = freeSpot((r.width / 2 - S.view.x) / S.view.k - NODE_W / 2, (r.height / 3 - S.view.y) / S.view.k, type);
  }
  const params = {};
  for (const [k, ps] of Object.entries(sc.params)) if (ps.required && ps.default != null) params[k] = ps.default;
  S.nodes.set(id, { id, type, params, x: Math.round(p.x), y: Math.round(p.y) });
  if (prev) autoConnect(prev, S.nodes.get(id));
  markDirty();
  select({ kind: "node", id });
  ensureVisible(S.nodes.get(id));
}

function autoConnect(from, to) {
  // first output of `from` that a required input of `to` accepts (signal before markers)
  const outs = Object.entries(schemaOf(from.type).outputs);
  for (const [ip, ikind] of Object.entries(schemaOf(to.type).inputs)) {
    const match = outs.find(([, okind]) => accepts(ikind, okind));
    if (match) { S.edges.push({ src: from.id, sp: match[0], dst: to.id, dp: ip }); return; }
  }
}

function ensureVisible(n) {
  const r = $("#canvasWrap").getBoundingClientRect();
  const sx = n.x * S.view.k + S.view.x, sy = n.y * S.view.k + S.view.y;
  if (sx < 10 || sy < 10 || sx + nodeW(n) * S.view.k > r.width - 10 || sy + 80 * S.view.k > r.height - 10) {
    S.view.x = r.width / 2 - (n.x + nodeW(n) / 2) * S.view.k;
    S.view.y = r.height / 3 - n.y * S.view.k;
    applyView();
  }
}

function deleteSelected() {
  const s = S.selected;
  if (!s) return;
  if (s.kind === "node") {
    S.nodes.delete(s.id);
    S.edges = S.edges.filter((e) => e.src !== s.id && e.dst !== s.id);
  } else S.edges.splice(s.index, 1);
  S.selected = null;
  markDirty(); renderAll();
}

function fitView() {
  const ns = [...S.nodes.values()];
  const r = $("#canvasWrap").getBoundingClientRect();
  if (!ns.length || !r.width) { S.view = { x: 40, y: 30, k: 1 }; return applyView(); }
  const x0 = Math.min(...ns.map((n) => n.x)), y0 = Math.min(...ns.map((n) => n.y));
  const x1 = Math.max(...ns.map((n) => n.x + nodeW(n)));
  const y1 = Math.max(...ns.map((n) => n.y + nodeH(n) + 30));
  const k = Math.min(1.2, Math.max(0.5, Math.min((r.width - 80) / (x1 - x0), (r.height - 80) / (y1 - y0))));
  S.view = { k, x: (r.width - (x1 - x0) * k) / 2 - x0 * k, y: (r.height - (y1 - y0) * k) / 2 - y0 * k };
  applyView();
}

function zoomAt(factor, cx, cy) {
  const k = Math.min(2.5, Math.max(0.25, S.view.k * factor));
  S.view.x = cx - (cx - S.view.x) * (k / S.view.k);
  S.view.y = cy - (cy - S.view.y) * (k / S.view.k);
  S.view.k = k;
  applyView();
}

function setupCanvas() {
  const wrap = $("#canvasWrap");
  wrap.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0 && ev.button !== 1) return;
    wrap.focus();
    const sx = ev.clientX, sy = ev.clientY, vx = S.view.x, vy = S.view.y;
    let moved = false;
    wrap.classList.add("panning");
    drag((e) => {
      S.view.x = vx + e.clientX - sx; S.view.y = vy + e.clientY - sy;
      moved = moved || Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 3;
      applyView();
    }, () => {
      wrap.classList.remove("panning");
      if (!moved && S.selected) select(null);
    });
  });
  wrap.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const r = wrap.getBoundingClientRect();
    if (ev.ctrlKey || ev.metaKey) {  // pinch on a trackpad arrives as ctrl+wheel
      zoomAt(Math.exp(-ev.deltaY * 0.01), ev.clientX - r.left, ev.clientY - r.top);
    } else {
      const unit = ev.deltaMode === 1 ? 16 : 1;
      S.view.x -= ev.deltaX * unit; S.view.y -= ev.deltaY * unit;
      applyView();
    }
  }, { passive: false });
  // Double-clicks on a node's resize handle (reset size) or live view (open window) are
  // detected here from two quick presses: the first press selects the node and re-renders
  // it, and browsers then drop the native dblclick because its target was replaced.
  let lastPress = { t: 0, key: "" };
  $("#nodes").addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    const nodeEl = ev.target.closest(".node");
    const zone = !nodeEl ? null : ev.target.closest(".resize") ? "resize" : ev.target.closest(".node-scope") ? "view" : null;
    if (!zone) { lastPress = { t: 0, key: "" }; return; }
    const key = `${nodeEl.dataset.id}:${zone}`, now = performance.now();
    if (key !== lastPress.key || now - lastPress.t > 450) { lastPress = { t: now, key }; return; }
    lastPress = { t: 0, key: "" };
    ev.stopPropagation(); ev.preventDefault();  // capture phase: the node's own handlers don't run
    const n = S.nodes.get(nodeEl.dataset.id);
    if (zone === "resize") { delete n.w; delete n.vh; markDirty(); renderNodes(); renderEdges(); }
    else openNodeView(n);
  }, true);
  wrap.addEventListener("dragover", (ev) => ev.preventDefault());
  wrap.addEventListener("drop", (ev) => {
    ev.preventDefault();
    const type = ev.dataTransfer.getData("text/signalseedbci-node");
    if (type) { const p = toCanvas(ev); addNode(type, { x: p.x - NODE_W / 2, y: p.y - 15 }); }
  });
  $("#btnZoomIn").onclick = () => { const r = wrap.getBoundingClientRect(); zoomAt(1.2, r.width / 2, r.height / 2); };
  $("#btnZoomOut").onclick = () => { const r = wrap.getBoundingClientRect(); zoomAt(1 / 1.2, r.width / 2, r.height / 2); };
  $("#btnFit").onclick = fitView;
  $("#btnLayout").onclick = () => { autoLayout(false); markDirty(); renderAll(); fitView(); };
  $("#legend").replaceChildren(...KINDS.map((k) => el("span", { style: `--kind:${kindColor(k)}` }, k)));

  document.addEventListener("keydown", (ev) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "s") { ev.preventDefault(); save(); return; }
    if (typing || !$("#modal").hidden) return;
    if ((ev.key === "Delete" || ev.key === "Backspace") && S.selected) { ev.preventDefault(); deleteSelected(); }
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "d" && S.selected?.kind === "node") { ev.preventDefault(); duplicateNode(S.selected.id); }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "z") { ev.preventDefault(); restoreHistory(ev.shiftKey ? 1 : -1); }
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "y") { ev.preventDefault(); restoreHistory(1); }
  });
}

function duplicateNode(id) {
  const n = S.nodes.get(id);
  const nid = uniqueId(n.id);
  S.nodes.set(nid, { id: nid, type: n.type, params: structuredClone(n.params), x: n.x + 30, y: n.y + 30,
                     ...(n.w ? { w: n.w } : {}), ...(n.vh ? { vh: n.vh } : {}) });
  markDirty();
  select({ kind: "node", id: nid });
}

// ------------------------------------------------------------------ palette
function renderPalette() {
  const q = $("#paletteSearch").value.toLowerCase();
  const order = ["sources", "preprocessing", "scripting", "epoching", "features", "classification", "outputs"];
  const byCat = {};
  for (const sc of Object.values(S.schemas)) {
    if (q && !(sc.type.toLowerCase().includes(q) || sc.description.toLowerCase().includes(q))) continue;
    (byCat[sc.category] ||= []).push(sc);
  }
  const host = $("#paletteList");
  host.replaceChildren();
  const cats = Object.keys(byCat).sort((a, b) => (order.indexOf(a) + 99 * (order.indexOf(a) < 0)) - (order.indexOf(b) + 99 * (order.indexOf(b) < 0)));
  for (const cat of cats) {
    host.append(el("div", { class: "pal-cat", style: `--cat:${catColor(cat)}` }, cat));
    for (const sc of byCat[cat].sort((a, b) => a.type.localeCompare(b.type))) {
      const item = el("div", { class: "pal-item", draggable: "true", title: sc.description },
        sc.type, el("small", {}, sc.description));
      item.addEventListener("dragstart", (ev) => ev.dataTransfer.setData("text/signalseedbci-node", sc.type));
      item.addEventListener("click", () => addNode(sc.type));
      host.append(item);
    }
  }
}

// ------------------------------------------------------------------ inspector
function fmtValue(v) {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function parseLoose(text, type) {
  text = text.trim();
  if (text === "") return undefined;
  if (type === "float" || type === "int") {
    const v = type === "int" ? Number.parseInt(text, text.startsWith("0x") ? 16 : 10) : Number(text);
    if (!Number.isFinite(v) || (type === "int" && !/^(0x[0-9a-f]+|-?\d+)$/i.test(text))) throw new Error(`not a ${type}`);
    return v;
  }
  if (type === "list" || type === "dict") {
    try { return JSON.parse(text); } catch { /* fall through to lenient forms */ }
    const scalar = (s) => {
      s = s.trim().replace(/^["']|["']$/g, "");
      return s !== "" && !isNaN(Number(s)) ? Number(s) : s;
    };
    const inner = text.replace(/^[[{]|[\]}]$/g, "");
    if (type === "list") {
      if (!/[,[\]]/.test(text) && /^[A-Za-z_]\w*$/.test(text)) return text; // bare word, e.g. "average"
      return inner.split(",").map(scalar).filter((s) => s !== "");
    }
    const out = {};
    for (const part of inner.split(",")) {
      if (!part.trim()) continue;
      const m = /^\s*(.+?)\s*:\s*(.+)\s*$/.exec(part);
      if (!m) throw new Error("use key: value pairs or JSON");
      let v = m[2].trim();
      if (/^\[.*\]$/.test(v)) v = v.slice(1, -1).split(/\s*;\s*|\s+/).map(scalar);
      out[String(scalar(m[1]))] = Array.isArray(v) ? v : scalar(v);
    }
    return out;
  }
  return text;
}

function paramField(node, name, ps) {
  const has = Object.prototype.hasOwnProperty.call(node.params, name);
  const value = has ? node.params[name] : undefined;
  const field = el("div", { class: "field" + (has ? " set" : "") });
  field.append(el("label", {}, el("span", {}, name + (ps.required ? " *" : "")), el("span", { class: "ptype" }, ps.type)));
  const set = (v) => {
    if (v === undefined || (!ps.required && JSON.stringify(v) === JSON.stringify(ps.default))) delete node.params[name];
    else node.params[name] = v;
    field.classList.toggle("set", Object.prototype.hasOwnProperty.call(node.params, name));
    const summary = $(`.node[data-id="${CSS.escape(node.id)}"] .node-summary`);
    if (summary) summary.textContent = paramSummary(node);
    markDirty();
  };
  let input;
  if (ps.type === "bool") {
    input = el("select", {}, el("option", { value: "" }, `default (${fmtValue(ps.default) || "auto"})`),
      el("option", { value: "true" }, "true"), el("option", { value: "false" }, "false"));
    input.value = has ? String(value) : "";
    input.onchange = () => set(input.value === "" ? undefined : input.value === "true");
  } else if (ps.type === "choice") {
    input = el("select", {}, ...ps.choices.map((c) => el("option", { value: c === null ? "" : c }, c === null ? "— none —" : c)));
    input.value = has ? fmtValue(value) : fmtValue(ps.default);
    input.onchange = () => set(input.value === "" ? null : input.value);
  } else if (node.type === "RenameChannels" && name === "mapping") {
    input = renameTable(node, has ? value : {}, set);
  } else if (node.type === "Classifier" && name === "estimator") {
    const listId = "estimators-list";
    input = el("input", { type: "text", list: listId, value: has ? value : "", placeholder: fmtValue(ps.default) });
    field.append(el("datalist", { id: listId }, ...Object.entries(S.estimators).map(([k, d]) => el("option", { value: k }, d))));
    input.onchange = () => set(input.value.trim() || undefined);
  } else {
    const isPath = ps.type === "path";
    const listId = isPath ? "files-list-" + name : null;
    input = el("input", { type: "text", value: has ? fmtValue(value) : "", placeholder: fmtValue(ps.default), list: listId });
    if (isPath) field.append(el("datalist", { id: listId }, ...S.files.map((f) => el("option", { value: relToPipeline(f) }))));
    input.onchange = () => {
      try { set(parseLoose(input.value, ps.type)); input.classList.remove("invalid"); input.title = ""; }
      catch (e) { input.classList.add("invalid"); input.title = e.message; }
    };
  }
  input.dataset.key = `${node.id}.${name}`;
  field.append(input);
  if (ps.help) field.append(el("div", { class: "help" }, ps.help));
  return field;
}

// ------------------------------------------------------------------ RenameChannels: one row per channel
const channelCache = new Map();  // "file:<path>" | "stream:<name>" -> channel names (null while loading)
let lslStreams = null;           // numeric LSL streams seen by the editor (null: not looked yet)

function currentRecording() {
  const src = $("#dataSource").value, f = $("#dataDetail").value;
  return needsFile(src) && f && f !== CREATE ? f : null;
}

// where the table takes its channel names from: the node's own choice, else the recording under Data
function channelSourceKey() {
  const own = localStorageGet("renameFrom");
  if (own?.startsWith("stream:") || (own?.startsWith("file:") && S.files.includes(own.slice(5)))) return own;
  const rec = currentRecording() || localStorageGet("dataFile");
  return S.files.includes(rec) ? `file:${rec}` : (S.files[0] ? `file:${S.files[0]}` : "");
}

function loadChannels(key) {
  if (!key || channelCache.has(key)) return;
  channelCache.set(key, null);  // loading
  const [kind, name] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
  api(`/api/channels?${kind}=${encodeURIComponent(name)}`)
    .then((r) => channelCache.set(key, r.channels))
    .catch((e) => { channelCache.delete(key); toast(`Channels of ${name}: ${e.message}`, true); })
    .finally(() => renderInspector());
}

function lookForStreams() {
  lslStreams = lslStreams || [];
  api("/api/streams?wait=1")
    .then((r) => { lslStreams = r.streams.filter((x) => x.format === "numeric").map((x) => x.name); })
    .catch(() => {})
    .finally(() => renderInspector());
}

function renameTable(node, mapping, set) {
  if (lslStreams === null) lookForStreams();
  const key = channelSourceKey();
  loadChannels(key);
  const channels = channelCache.get(key) || null;
  const label = key.startsWith("stream:") ? `live stream ${key.slice(7)}` : key.slice(5).split("/").pop();

  const from = el("select", { "data-key": `${node.id}.mapping.__from`, title: "Where the channel names come from" },
    ...S.files.map((f) => el("option", { value: `file:${f}` }, f.replace(/^data\//, ""))),
    ...(lslStreams?.length ? [el("optgroup", { label: "Running LSL streams" },
      ...lslStreams.map((n) => el("option", { value: `stream:${n}` }, n)))] : []));
  if (key.startsWith("stream:") && !lslStreams?.includes(key.slice(7)))
    from.append(el("option", { value: key }, `${key.slice(7)} (not running)`));
  from.value = key;
  from.onchange = () => { localStorageSet("renameFrom", from.value); renderInspector(); };
  const refresh = el("button", { title: "Look for running LSL streams again", onclick: () => { channelCache.delete(key); lookForStreams(); } }, "↻");

  const names = [...(channels || [])];
  for (const old of Object.keys(mapping)) if (!names.includes(old)) names.push(old);

  const box = el("div", { class: "rename" });
  const rows = el("div", { class: "rename-rows" });
  const collect = () => {
    const out = {};
    for (const inp of rows.querySelectorAll("input[data-old]")) {
      const v = inp.value.trim();
      if (v && v !== inp.dataset.old) out[inp.dataset.old] = v;
    }
    set(Object.keys(out).length ? out : undefined);
  };
  for (const old of names) {
    const inp = el("input", { type: "text", value: mapping[old] ?? "", placeholder: old, "data-old": old,
      "data-key": `${node.id}.mapping.${old}`, onchange: collect });
    const extra = channels && !channels.includes(old);
    rows.append(el("div", { class: "rename-row" + (extra ? " extra" : "") },
      el("span", { class: "old", title: extra ? "Not in the selected recording" : old }, old), el("span", { class: "arrow" }, "→"), inp,
      extra || !channels ? el("button", { class: "x", title: "Remove", onclick: () => { inp.value = ""; collect(); renderInspector(); } }, "×") : null));
  }
  // a channel that is not listed (no recording selected, or a live stream)
  const addOld = el("input", { type: "text", placeholder: "old name", "data-key": `${node.id}.mapping.__old` });
  const addNew = el("input", { type: "text", placeholder: "new name", "data-key": `${node.id}.mapping.__new` });
  const add = () => {
    const o = addOld.value.trim(), n = addNew.value.trim();
    if (!o || !n) return;
    addNew.blur();  // or the re-render would put the typed text back
    set({ ...mapping, [o]: n });
    renderInspector();
  };
  addNew.onkeydown = (e) => { if (e.key === "Enter") add(); };
  const note = !key ? "No recordings or running streams found: add names by hand below."
    : channels ? `${channels.length} channels in ${label}. Leave a box empty to keep the name.` : `Reading channels of ${label}…`;
  box.append(el("div", { class: "rename-from" }, el("span", {}, "Channels from"), from, refresh),
    el("div", { class: "help" }, note), rows,
    el("div", { class: "rename-row add" }, addOld, el("span", { class: "arrow" }, "→"), addNew,
      el("button", { title: "Add", onclick: add }, "＋")));
  if (names.length > 12) rows.classList.add("scroll");
  return box;
}

function relToPipeline(projectRel) {
  // paths in a pipeline are relative to the pipeline file's folder
  const dir = (S.path || "pipelines/x.yaml").split("/").slice(0, -1);
  const target = projectRel.split("/");
  let i = 0;
  while (i < dir.length && dir[i] === target[i]) i++;
  return [...Array(dir.length - i).fill(".."), ...target.slice(i)].join("/");
}

function messagesFor(id) {
  const v = S.validation;
  const mine = (m) => m.includes(`'${id}'`) || m.startsWith(`${id}:`) || m.startsWith(`${id} (`) || m.includes(` ${id}.`) || m.includes(`'${id}.`);
  return [...v.errors.filter(mine).map((m) => el("div", { class: "msg err" }, m)),
          ...v.warnings.filter(mine).map((m) => el("div", { class: "msg warn" }, m))];
}

function renderInspector() {
  // keep focus, caret and any uncommitted text when re-rendering (validation runs while typing)
  const active = document.activeElement;
  const keep = active && $("#inspector").contains(active) && active.dataset.key
    ? { key: active.dataset.key, value: active.value, start: active.selectionStart, end: active.selectionEnd } : null;
  buildInspector();
  if (keep) {
    const again = $(`#inspector [data-key="${CSS.escape(keep.key)}"]`);
    if (again) {
      again.value = keep.value;
      again.focus();
      try { again.setSelectionRange(keep.start, keep.end); } catch { /* selects have no caret */ }
    }
  }
}

function buildInspector() {
  const host = $("#inspector");
  host.replaceChildren();
  const s = S.selected;
  if (s?.kind === "node" && S.nodes.has(s.id)) {
    const n = S.nodes.get(s.id), sc = schemaOf(n.type);
    const idInput = el("input", { type: "text", value: n.id, "data-key": "__id" });
    idInput.onchange = () => renameNode(n.id, idInput.value.trim(), idInput);
    host.append(
      el("h2", {}, n.type),
      el("div", { class: "sub" }, sc.category),
      el("div", { class: "desc" }, sc.description),
      el("div", { class: "field" }, el("label", {}, "Node id"), idInput),
      ...messagesFor(n.id),
    );
    if (S.validation.node_info?.[n.id]) host.append(el("div", { class: "msg info" }, S.validation.node_info[n.id]));
    for (const [name, ps] of Object.entries(sc.params)) host.append(paramField(n, name, ps));
    const unknown = Object.keys(n.params).filter((k) => !(k in sc.params));
    if (unknown.length) host.append(el("div", { class: "msg err" }, `Unknown parameters: ${unknown.join(", ")}`));
    host.append(el("div", { class: "actions" },
      el("button", { onclick: () => duplicateNode(n.id) }, "Duplicate"),
      el("button", { class: "danger", onclick: deleteSelected }, "Delete")));
    return;
  }
  if (s?.kind === "edge" && S.edges[s.index]) {
    const e = S.edges[s.index];
    const kind = schemaOf(S.nodes.get(e.src).type).outputs[e.sp];
    host.append(el("h2", {}, "Connection"),
      el("div", { class: "desc" }, `${e.src}.${e.sp} → ${e.dst}.${e.dp}`),
      el("div", { class: "msg info", style: `color:${kindColor(kind)}` }, `carries ${kind}`),
      el("div", { class: "actions" }, el("button", { class: "danger", onclick: deleteSelected }, "Delete connection")));
    return;
  }
  // pipeline properties
  const name = el("input", { type: "text", value: S.name, "data-key": "__name" });
  name.onchange = () => { S.name = name.value.trim() || "Untitled"; markDirty(); };
  const desc = el("textarea", { rows: 4, "data-key": "__desc" }, S.description);
  desc.onchange = () => { S.description = desc.value.trim(); markDirty(); };
  const v = S.validation;
  host.append(
    el("h2", {}, "Pipeline"),
    el("div", { class: "sub" }, S.path || "not saved yet"),
    el("div", { class: "field" }, el("label", {}, "Name"), name),
    el("div", { class: "field" }, el("label", {}, "Description"), desc),
    ...v.errors.map((m) => el("div", { class: "msg err" }, m)),
    ...v.warnings.map((m) => el("div", { class: "msg warn" }, m)),
    ...Object.entries(v.node_info || {}).map(([k, m]) => el("div", { class: "msg info" }, `${k}: ${m}`)),
    el("h2", { style: "margin-top:16px" }, "How to"),
    el("ul", {},
      el("li", {}, "Drag from an output dot to an input dot to connect; colours show data kinds."),
      el("li", {}, "Drag a connected input dot away to move or remove that connection."),
      el("li", {}, "Select a node to edit its parameters; ● marks values that differ from the default."),
      el("li", {}, el("span", { class: "kbd" }, "Del"), " deletes, ", el("span", { class: "kbd" }, "⌘D"), " duplicates, ", el("span", { class: "kbd" }, "⌘Z"), " undoes, ",
        el("span", { class: "kbd" }, "⌘S"), " saves; scroll to pan, pinch or ⌘-scroll to zoom."),
      el("li", {}, "Train fits Classifier nodes on recordings; Run uses live LSL, a replay, or a fast offline pass.")));
}

function renameNode(old, nu, input) {
  if (nu === old) return;
  if (!/^[A-Za-z_]\w*$/.test(nu)) { input.classList.add("invalid"); return toast("Ids: letters, digits, underscore", true); }
  if (S.nodes.has(nu)) { input.classList.add("invalid"); return toast(`Id '${nu}' already exists`, true); }
  const n = S.nodes.get(old);
  S.nodes.delete(old);
  n.id = nu;
  S.nodes.set(nu, n);
  for (const e of S.edges) { if (e.src === old) e.src = nu; if (e.dst === old) e.dst = nu; }
  S.selected = { kind: "node", id: nu };
  markDirty(); renderAll();
}

// ------------------------------------------------------------------ validation
let validateTimer = null;
function validateSoon(ms = 350) {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(validate, ms);
}
async function validate() {
  try {
    S.validation = await api("/api/validate", { spec: toSpec(), path: S.path });
  } catch (e) {
    S.validation = { ok: false, errors: [e.message], warnings: [], node_info: {} };
  }
  const v = S.validation, box = $("#validity");
  if (v.errors.length) { box.className = "validity err"; box.textContent = "✕ " + v.errors[0]; }
  else if (v.warnings.length) { box.className = "validity warn"; box.textContent = "⚠ " + v.warnings[0]; }
  else { box.className = "validity ok"; box.textContent = `✓ valid · ${S.nodes.size} nodes`; }
  box.title = [...v.errors, ...v.warnings].join("\n");
  renderNodes(); renderEdges(); renderInspector();
}

// ------------------------------------------------------------------ files
async function refreshPipelines(selectPath) {
  const { pipelines } = await api("/api/pipelines");
  const sel = $("#pipelineSelect");
  sel.replaceChildren(el("option", { value: "" }, S.path ? "— open pipeline —" : "(unsaved pipeline)"),
    ...pipelines.map((p) => el("option", { value: p }, p.replace(/^pipelines\//, ""))));
  sel.value = selectPath || S.path || "";
}

async function refreshFiles() {
  const files = (await api("/api/files")).files;
  if (S.filesLoaded && files.join("\n") === S.files.join("\n")) return;  // unchanged: keep the open menu as it is
  S.files = files;
  S.filesLoaded = true;
  updateDataDetail();
}

// ------------------------------------------------------------------ data source (toolbar)
const PARADIGMS = { "motor-imagery": "Motor imagery (cues 1/2)", "alpha-bursts": "Alpha bursts, 10 s every 20 s",
  "eye-movements": "Eye movements, in-ear (cues 1-4)" };
const CREATE = "__create__";
const needsFile = (src) => src === "replay" || src === "offline";

function updateDataDetail() {
  const src = $("#dataSource").value, sel = $("#dataDetail");
  if (needsFile(src)) {
    const want = localStorageGet("dataFile");
    sel.replaceChildren(
      ...(S.files.length ? S.files.map((f) => el("option", { value: f }, f)) : [el("option", { value: "" }, "no recordings yet")]),
      el("option", { value: CREATE }, "＋ Create synthetic recording…"));
    sel.value = S.files.includes(want) ? want : (S.files[0] || "");
    sel.title = "Recording";
  } else if (src === "synthetic") {
    sel.replaceChildren(...Object.entries(PARADIGMS).map(([k, v]) => el("option", { value: k }, v)));
    sel.value = localStorageGet("dataParadigm") || "motor-imagery";
    sel.title = "What the synthetic EEG contains";
  }
  sel.hidden = !(needsFile(src) || src === "synthetic");
}

async function onDataDetailChange() {
  const src = $("#dataSource").value, v = $("#dataDetail").value;
  if (v === CREATE) { updateDataDetail(); return createRecording(); }
  if (needsFile(src)) { localStorageSet("dataFile", v); localStorageSet("renameFrom", ""); renderInspector(); }
  if (src === "synthetic") localStorageSet("dataParadigm", v);
}

async function createRecording() {
  const paradigm = el("select", {}, ...Object.entries(PARADIGMS).map(([k, v]) => el("option", { value: k }, v)));
  paradigm.value = localStorageGet("dataParadigm") || "motor-imagery";
  const dur = el("input", { type: "text", value: "300" });
  const file = el("input", { type: "text", value: "" });
  const suggest = () => (file.value = `data/${paradigm.value.replace("-", "_")}_${dur.value || 300}s_raw.fif`);
  paradigm.onchange = dur.oninput = suggest;
  suggest();
  const ok = await modal("Create synthetic recording", [
    el("p", { class: "muted" }, "A labelled recording (512 Hz) for training, replay and offline tests. Motor imagery and "
      + "alpha bursts use a 32-electrode BioSemi cap; eye movements a 16-channel in-ear montage (12 in-ear electrodes, "
      + "mastoids, forehead, neck)."),
    el("div", { class: "field" }, el("label", {}, "Contents"), paradigm),
    el("div", { class: "field" }, el("label", {}, "Duration (s)"), dur),
    el("div", { class: "field" }, el("label", {}, "Save as"), file),
  ], "Create");
  if (!ok) return;
  try {
    const p = await api("/api/process/start", { kind: "synth", options: {
      record: file.value.trim(), duration: Number(dur.value) || 300, paradigm: paradigm.value } });
    S.pendingRecording = { id: p.id, file: file.value.trim().replace(/^\.\//, "") };
    S.activeProc = p.id;
    showTab("console");
    toast("Creating recording…");
    pollProcs();
  } catch (e) { toast(e.message, true); }
}

async function openPipeline(path) {
  if (S.dirty && !(await modal("Discard changes?", [el("p", {}, "The current pipeline has unsaved changes.")], "Discard"))) {
    $("#pipelineSelect").value = S.path || "";
    return;
  }
  const { spec, path: p } = await api(`/api/pipeline?path=${encodeURIComponent(path)}`);
  fromSpec(spec, p);
  await refreshPipelines(p);
  localStorageSet("lastPipeline", p);
}

async function save(forceAs = false) {
  let path = S.path;
  if (!path || forceAs) {
    const suggestion = path || "pipelines/" + S.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") + ".yaml";
    path = await askText("Save pipeline as", "File (inside the project)", suggestion);
    if (!path) return false;
  }
  try {
    const r = await api("/api/pipeline", { path, spec: toSpec() });
    S.path = r.path; S.dirty = false;
    $("#dirtyDot").hidden = true;
    await refreshPipelines(r.path);
    localStorageSet("lastPipeline", r.path);
    toast(`Saved ${r.path}`);
    validateSoon(0);
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

async function newPipeline() {
  if (S.dirty && !(await modal("Discard changes?", [el("p", {}, "The current pipeline has unsaved changes.")], "Discard"))) return;
  const name = await askText("New pipeline", "Name", "My pipeline");
  if (name === null) return;
  fromSpec({ name: name || "Untitled", nodes: { src: { type: "LSLSource" } }, edges: [] }, null);
  S.dirty = true;
  await refreshPipelines();
  renderAll();
}

function localStorageGet(k) { try { return localStorage.getItem("signalseedbci." + k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem("signalseedbci." + k, v); } catch { /* ignore */ } }

// ------------------------------------------------------------------ run & train
async function ensureSaved() {
  if (!S.dirty && S.path) return true;
  return save();
}

// Start the toolbar's data source together with the pipeline: a run, or (train) a live calibration
async function startSession(train = false) {
  const source = $("#dataSource").value, detail = $("#dataDetail").value;
  const options = {};
  if (needsFile(source)) {
    if (!detail || detail === CREATE) return toast("Choose a recording, or create one with ＋ Create synthetic recording", true);
    options.file = detail;
  }
  if (source === "synthetic") options.paradigm = detail;
  try {
    const r = await api("/api/session/start", { pipeline: S.path, source, options, train });
    S.runProcId = r.run.id;
    S.activeProc = r.run.id;
    S.logSince[r.run.id] = 0;
    for (const w of r.warnings) toast(w, true, 7000);
    if (train) toast("Calibrating: give the cues, then press ■ Stop to train", false, 6000);
    showTab("console");
    await pollProcs();
    if (monitorVisible()) setTimeout(refreshStreams, 2000);
  } catch (e) { toast(e.message, true); }
}

async function run() {
  if (!(await ensureSaved())) return;
  await validate();
  if (S.validation.errors.length) return toast("Fix the validation errors first", true);
  return startSession(false);
}

async function train() {
  if (!(await ensureSaved())) return;
  await validate();
  if (![...S.nodes.values()].some((n) => n.type === "Classifier")) {
    return toast("This pipeline has no Classifier node to train", true);
  }
  if (S.runProcId != null && S.procs.find((p) => p.id === S.runProcId)?.running) {
    return toast("Stop the current run first", true);
  }
  await refreshFiles();
  const source = $("#dataSource"), liveOk = source.value !== "offline";
  const dataLabel = source.selectedOptions[0].textContent
    + (needsFile(source.value) || source.value === "synthetic" ? ` › ${$("#dataDetail").selectedOptions[0]?.textContent || ""}` : "");
  const modeLive = el("input", { type: "radio", name: "trainMode", value: "live", disabled: !liveOk });
  const modeFiles = el("input", { type: "radio", name: "trainMode", value: "files" });
  (liveOk && !S.files.length ? modeLive : modeFiles).checked = true;
  const checks = S.files.map((f) => el("label", {}, el("input", { type: "checkbox", value: f }), f));
  const body = [
    el("label", { class: "train-mode" }, modeFiles, el("b", {}, "Recordings"),
      el("span", { class: "muted" }, "Run the pipeline offline over the ticked files and fit every Classifier on the labelled epochs.")),
    S.files.length ? el("div", { class: "file-checks", onchange: () => { modeFiles.checked = true; } }, ...checks)
      : el("div", { class: "msg warn" }, "No recordings in the project. Use Data › Replay a recording › ＋ Create synthetic recording, or copy BDF files into data/."),
    el("label", { class: "train-mode" }, modeLive, el("b", {}, "Live session"),
      el("span", { class: "muted" }, liveOk
        ? `Calibrate now on ${dataLabel}: present the cues, then press ■ Stop. The classifiers are fitted and the raw session is saved in data/ for retraining.`
        : "Needs streaming data: choose a Data source other than “Recording, offline”.")),
  ];
  if (!(await modal("Train classifiers", body, "Train"))) return;
  if (modeLive.checked) return startSession(true);
  const files = checks.map((c) => c.querySelector("input")).filter((i) => i.checked).map((i) => i.value);
  if (!files.length) return toast("Select at least one recording", true);
  try {
    const p = await api("/api/process/start", { kind: "train", pipeline: S.path, options: { files } });
    showTab("console");
    S.activeProc = p.id;
    S.trainProcId = p.id;
    await pollProcs();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ console
function classifyLine(line) {
  if (/error|Traceback|exited with code [1-9]/i.test(line)) return "e";
  if (/warning|dropped/i.test(line)) return "w";
  if (/accuracy|saved|Wrote|connected to|exited with code 0/i.test(line)) return "ok";
  return "";
}

async function pollProcs() {
  try {
    const { processes } = await api("/api/processes");
    const wasRunning = new Map(S.procs.map((p) => [p.id, p.running]));
    S.procs = processes;
    for (const p of processes) {
      if (wasRunning.get(p.id) && !p.running) {
        if (p.kind === "train") { toast(p.returncode === 0 ? "Training finished" : "Training failed — see console", p.returncode !== 0); validateSoon(0); }
        if (S.pendingRecording?.id === p.id) {
          const rec = S.pendingRecording;
          S.pendingRecording = null;
          if (p.returncode === 0) localStorageSet("dataFile", rec.file);  // select the new file once
          await refreshFiles();
          if (p.returncode === 0) toast(`Created ${rec.file}`);
          else toast("Creating the recording failed — see console", true);
        }
      }
    }
    if (S.activeProc == null && processes.length) S.activeProc = processes[processes.length - 1].id;
    const sig = JSON.stringify([processes.map((p) => [p.id, p.running, p.returncode]), S.activeProc]);
    if (sig !== S.procSig) { S.procSig = sig; renderProcs(); }
    const run = processes.find((p) => p.id === S.runProcId);
    const running = !!(run && run.running);
    $("#btnStop").hidden = !running;
    $("#btnRun").hidden = running;
    $("#dataSource").disabled = $("#dataDetail").disabled = running;
    $("#runStatus").hidden = !running;
    if (running) { $("#runStatus").textContent = run.kind === "train" ? "● calibrating" : "● running"; $("#runStatus").title = run.label; }
  } catch { /* server gone */ }
}

function renderProcs() {
  const host = $("#procList");
  host.replaceChildren(...S.procs.slice().reverse().map((p) => {
    const st = p.running ? "run" : p.returncode ? "fail" : "";
    return el("div", { class: "proc" + (p.id === S.activeProc ? " active" : ""), onclick: () => { S.activeProc = p.id; S.procSig = null; renderProcs(); $("#log").replaceChildren(); S.logSince[p.id] = 0; pollLog(); } },
      el("span", { class: "st " + st }), el("span", { class: "lbl", title: "signalseedbci " + p.args.join(" ") }, p.label),
      p.running ? el("button", { onclick: (e) => { e.stopPropagation(); stopProc(p.id); } }, "Stop") : null);
  }));
  if (!S.procs.length) host.append(el("p", { class: "muted", style: "padding:6px" }, "Runs (with their data source), training and recordings you create appear here."));
  else if (S.procs.some((p) => !p.running)) host.append(el("button", { style: "margin:6px", onclick: async () => { await api("/api/process/clear", {}); S.activeProc = null; $("#log").replaceChildren(); pollProcs(); } }, "Clear finished"));
}

async function pollLog() {
  const id = S.activeProc;
  if (id == null || $("#bottom").classList.contains("collapsed")) return;
  try {
    const r = await api(`/api/log?id=${id}&since=${S.logSince[id] || 0}`);
    if (id !== S.activeProc) return;
    const log = $("#log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    if ((S.logSince[id] || 0) === 0) log.replaceChildren();
    for (const line of r.lines) log.append(el("span", { class: classifyLine(line) }, line + "\n"));
    while (log.childNodes.length > 3000) log.firstChild.remove();
    S.logSince[id] = r.next;
    if (atBottom) log.scrollTop = log.scrollHeight;
  } catch { /* process cleared */ }
}

async function stopProc(id) {
  await api("/api/process/stop", { id });
  setTimeout(pollProcs, 300);
}

// ------------------------------------------------------------------ monitor
const MON = { buf: null, events: new EventBuffer([]) };
const monitorVisible = () => !$('[data-body="monitor"]').hidden && !$("#bottom").classList.contains("collapsed");

async function refreshStreams() {
  const host = $("#streamList");
  host.replaceChildren(el("p", { class: "muted" }, "Searching…"));
  try {
    const { streams } = await api("/api/streams?wait=1.0");
    host.replaceChildren();
    if (!streams.length) host.append(el("p", { class: "muted" }, "No LSL streams. Start a data source, the Windows bridge, or a pipeline with LSL outputs."));
    for (const s of streams) {
      const isEvents = s.format === "string";
      const active = isEvents ? MON.events.names.has(s.name) : MON.buf?.name === s.name;
      host.append(el("div", {
        class: "stream" + (active ? " active" : ""), title: isEvents ? "Click to show/hide events" : "Click to plot",
        onclick: () => { if (isEvents) toggleEvents(s.name); else setScope(s.name); refreshStreams(); },
      }, el("strong", {}, s.name),
        el("small", {}, `${s.type || "?"} · ${s.channels} ch · ${s.sfreq ? s.sfreq + " Hz" : "irregular"} · ${s.host}`)));
    }
  } catch (e) { host.replaceChildren(el("p", { class: "muted" }, e.message)); }
}

function setScope(name) {
  MON.buf = new StreamBuffer(name, { filter: $("#scopeFilter").value });
  $("#scopeTitle").textContent = name;
  $("#scopeTitle").classList.remove("muted");
  $("#btnScopeWindow").hidden = false;
}

function toggleEvents(name) {
  const names = new Set(MON.events.names);
  if (names.has(name)) names.delete(name); else names.add(name);
  MON.events.setNames([...names]);
}

async function pollMonitor() {
  if (!monitorVisible() || !MON.buf) return;
  MON.buf.setFilter($("#scopeFilter").value);
  await MON.buf.poll(Number($("#scopeWin").value) + 2);
  if (MON.buf.ch.length) updatePages();
}

function updatePages() {
  const ch = MON.buf.ch, n = ch.length, per = 16, sel = $("#scopePage");
  if (sel.dataset.sig === ch.join()) return;
  const pages = Math.max(1, Math.ceil(n / per));
  sel.replaceChildren(...Array.from({ length: pages }, (_, i) =>
    el("option", { value: i }, `${ch[i * per]}–${ch[Math.min(n, (i + 1) * per) - 1]}`)));
  sel.dataset.sig = ch.join();
}

async function pollMonitorEvents() {
  if (!monitorVisible()) return;
  await MON.events.poll();
  renderEvents();
}

function renderEvents() {
  const evs = MON.events.events;
  $("#eventList").replaceChildren(...evs.slice(-80).reverse().map((e) =>
    el("li", { title: e.stream }, el("span", { class: "t" }, e.t.toFixed(2)), el("span", {}, eventLabel(e.v)))));
  const lastPred = [...evs].reverse().find((e) => e.v.startsWith("{"));
  const bars = $("#probaBars");
  bars.replaceChildren();
  if (!lastPred) return;
  try {
    const j = JSON.parse(lastPred.v);
    for (const [k, p] of Object.entries(j.proba || {})) {
      bars.append(el("div", { class: "bar" }, el("span", { class: "name" }, k),
        el("span", { class: "fill", style: `width:${Math.round(p * 150)}px;${k === j.label ? "" : "opacity:.45"}` }),
        el("span", {}, p.toFixed(2))));
    }
  } catch { /* not a prediction */ }
}

// ------------------------------------------------------------------ plots inside LSLSignalOut nodes
const MINI = new Map();  // stream name -> {buf, events}
const displayFilter = new Map();  // stream name -> display filter of its LSLSignalOut node
function miniStreamName(n) { return n.params.name ?? schemaOf(n.type).params.name.default; }
const scopeNodes = () => [...S.nodes.values()].filter((n) => SCOPE_NODES.has(n.type));

async function pollMiniScopes() {
  if (document.hidden) return;
  const wanted = new Map(scopeNodes().map((n) => [miniStreamName(n), n.type]));
  displayFilter.clear();
  for (const n of scopeNodes()) if (n.type === "LSLSignalOut") displayFilter.set(miniStreamName(n), paramOf(n, "display"));
  for (const k of [...MINI.keys()]) if (!wanted.has(k)) MINI.delete(k);
  await Promise.all([...wanted].map(async ([name, type]) => {
    if (NODE_VIEWS[type] === "eyes" || NODE_VIEWS[type] === "gaze") {
      if (!MINI.has(name)) MINI.set(name, { eyes: new EventBuffer([name], 50), anim: new EyesAnimator() });
      return MINI.get(name).eyes.poll();
    }
    const spectrum = type === "SpectrumOut";  // the node plot only needs the latest spectra
    const filter = displayFilter.get(name) || "bandpass";
    if (!MINI.has(name)) {
      MINI.set(name, { buf: new StreamBuffer(name, { filter, keep: spectrum ? 3 : 35 }), events: new EventBuffer([`${name}-markers`]) });
    }
    const m = MINI.get(name);
    m.buf.setFilter(filter);
    await m.buf.poll(spectrum ? 2 : 32);
    if (!m.buf.missing) await m.events.poll();
  }));
}

function drawMiniScopes() {
  for (const n of scopeNodes()) {
    const node = document.querySelector(`.node[data-id="${CSS.escape(n.id)}"]`);
    const cv = node?.querySelector("canvas.mini");
    if (!cv) continue;
    const m = MINI.get(miniStreamName(n));
    const status = node.querySelector(".mini-status");
    let text = "○ not streaming — Run the pipeline";
    if (NODE_VIEWS[n.type] === "gaze") {
      if (!m?.anim) continue;
      const g = gazeParams(n);
      const st = gazeStateFrom(m.eyes, miniStreamName(n), g, g.hold, !!paramOf(n, "graded"));
      const say = { [g.up]: "looking up", [g.down]: "looking down", [g.left]: "looking left", [g.right]: "looking right" };
      m.anim.update(0);
      m.anim.look(st.dir);
      const big = cv.clientWidth >= 320 && cv.clientHeight >= 180;
      m.anim.draw(cv, { compact: !big, dim: !st.present, label: big && st.present ? (st.active ? say[st.label] : "looking ahead") : null });
      text = !st.present ? "○ waiting for predictions — Run the pipeline"
        : st.label == null ? "● connected · waiting for a cue"
        : st.active ? `● ${say[st.label] || st.label}` + (st.p != null ? ` · P=${st.p.toFixed(2)}` : "")
        : `● looking ahead · last: ${st.label}`;
      if (status.textContent !== text) { status.textContent = text; status.classList.toggle("live", st.present); }
      continue;
    }
    if (NODE_VIEWS[n.type] === "eyes") {
      if (!m?.anim) continue;
      const closed = paramOf(n, "closed_label");
      const st = eyesStateFrom(m.eyes, closed, !!paramOf(n, "graded"));
      m.anim.update(st.live ? st.closure : 0);
      const bigEyes = cv.clientWidth >= 320 && cv.clientHeight >= 180;
      const eyesLabel = bigEyes && st.live ? (st.closure >= 0.5 ? "eyes closed" : "eyes open") : null;
      m.anim.draw(cv, { compact: !bigEyes, dim: !st.live, label: eyesLabel });
      if (st.live) text = `● eyes ${st.closure >= 0.5 ? "closed" : "open"}` + (st.pClosed != null ? ` · P(${closed})=${st.pClosed.toFixed(2)}` : "");
      else text = "○ waiting for predictions — Run the pipeline";
      if (status.textContent !== text) { status.textContent = text; status.classList.toggle("live", st.live); }
      continue;
    }
    // enlarged nodes get the full plot (axes, labels, legend); small ones the thumbnail
    const big = cv.clientWidth >= 300 && cv.clientHeight >= 150;
    drawStream(cv, m?.buf, { window: 30, compact: !big, events: m?.events.events, perPage: 16 });
    if (m && !m.buf.missing && m.buf.times.length) {
      const last = m.buf.data[m.buf.data.length - 1];
      const live = m.buf.staleness < 3;
      const peak = spectrumPeak(m.buf);
      text = m.buf.spectrum
        ? `${live ? "● live" : "◌ paused"} · ${m.buf.spectrum.chans.length} ch · ${peak == null ? "no peak" : "peak " + peak.toFixed(1) + " Hz"}`
        : `${live ? "● live" : "◌ paused"} · ${m.buf.ch.length} ch · last ${m.buf.ch[0]}=${fmtNum(last[0])}`;
    } else if (m && !m.buf.missing && !m.buf.error) text = "connecting…";
    if (status.textContent !== text) {
      status.textContent = text;
      status.classList.toggle("live", text.startsWith("●"));
    }
  }
}

function frame() {
  if (monitorVisible()) {
    const sc = $("#scopeScale").value;
    drawStream($("#scopeCanvas"), MON.buf, {
      window: Number($("#scopeWin").value), mode: $("#scopeMode").value, scale: sc === "auto" ? "auto" : Number(sc),
      page: Number($("#scopePage").value || 0), perPage: 16, events: MON.events.events,
    });
  }
  drawMiniScopes();
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ tabs & toolbar
function showTab(name) {
  $$(".tabs [data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-body").forEach((b) => (b.hidden = b.dataset.body !== name));
  $("#bottom").classList.remove("collapsed");
  if (name === "monitor") refreshStreams();
  localStorageSet("tab", name);
}

function setupToolbar() {
  $("#pipelineSelect").onchange = (e) => e.target.value && openPipeline(e.target.value);
  $("#btnNew").onclick = newPipeline;
  $("#btnSave").onclick = () => save();
  $("#btnSaveAs").onclick = () => save(true);
  $("#btnRun").onclick = run;
  $("#btnStop").onclick = () => S.runProcId != null && stopProc(S.runProcId);
  $("#btnTrain").onclick = train;
  $("#dataSource").value = localStorageGet("dataSource") || "live";
  if (!$("#dataSource").value) $("#dataSource").value = "live";
  $("#dataSource").onchange = () => { localStorageSet("dataSource", $("#dataSource").value); updateDataDetail(); refreshFiles(); renderInspector(); };
  $("#dataDetail").onchange = onDataDetailChange;
  // files copied into data/ while the page is open: pick them up when you come back or use the menus
  $("#dataDetail").onfocus = refreshFiles;
  window.addEventListener("focus", refreshFiles);
  $("#paletteSearch").oninput = renderPalette;
  $$(".tabs [data-tab]").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $("#btnCollapse").onclick = () => {
    const c = $("#bottom").classList.toggle("collapsed");
    $("#btnCollapse").textContent = c ? "▴" : "▾";
  };
  $("#btnStreams").onclick = refreshStreams;
  $("#btnScopeWindow").onclick = () => MON.buf && openScopeWindow(MON.buf.name);
  window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
}

// ------------------------------------------------------------------ boot
async function boot() {
  const { nodes, estimators } = await api("/api/nodes");
  for (const sc of nodes) S.schemas[sc.type] = sc;
  S.estimators = estimators;
  renderPalette();
  setupCanvas();
  setupToolbar();
  await refreshFiles();
  const { pipelines } = await api("/api/pipelines");
  const last = localStorageGet("lastPipeline");
  const start = pipelines.includes(last) ? last : pipelines[0];
  if (start) {
    const { spec, path } = await api(`/api/pipeline?path=${encodeURIComponent(start)}`);
    fromSpec(spec, path);
  } else {
    fromSpec({ name: "Untitled", nodes: { src: { type: "LSLSource" } }, edges: [] }, null);
  }
  await refreshPipelines();
  const tab = localStorageGet("tab");
  if (tab === "console" || tab === "monitor") showTab(tab);
  setInterval(pollProcs, 1000);
  setInterval(pollLog, 500);
  setInterval(pollMonitor, 120);
  setInterval(pollMonitorEvents, 300);
  setInterval(pollMiniScopes, 250);
  requestAnimationFrame(frame);
  pollProcs();
}

boot().catch((e) => toast("Failed to start: " + e.message, true, 10000));
