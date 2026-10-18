/* Shared LSL stream plotting for the editor (live views inside nodes) and the
 * stand-alone scope window. No dependencies. */
"use strict";

const SCOPE_COLORS = ["#3b7ddd", "#e08a00", "#15a06e", "#9b51e0", "#d9465f", "#0f9fa8", "#c98a00", "#8a5a44"];

/** Polls /api/watch for one numeric stream and keeps the last `keep` seconds. */
class StreamBuffer {
  constructor(name, { filter = "none", keep = 60 } = {}) {
    this.name = name;
    this.filter = filter;
    this.keep = keep;
    this.times = [];
    this.data = [];
    this.ch = [];
    this.sfreq = 0;
    this.error = null;
    this.missing = false;
    this.retryAt = 0;
    this.busy = false;
    this.lastArrival = 0;
  }

  setFilter(filter) {
    if (filter === this.filter) return;
    this.filter = filter;
    this.clear();
  }

  clear() {
    this.times = []; this.data = []; this.error = null;
  }

  get since() { return this.times.length ? this.times[this.times.length - 1] : 0; }

  async poll(window = 30) {
    if (this.busy || performance.now() < this.retryAt) return;
    this.busy = true;
    try {
      const q = `name=${encodeURIComponent(this.name)}&since=${this.since}&filter=${this.filter}&window=${window}`;
      const r = await fetch(`/api/watch?${q}`);
      const j = await r.json();
      if (!r.ok || j.missing) {
        this.missing = !!j.missing;
        this.error = j.error || r.statusText;
        this.retryAt = performance.now() + (this.missing ? 3000 : 1000);
        if (this.missing) this.clear();
        return;
      }
      this.missing = false;
      if (j.kind !== "numeric") { this.error = "not a numeric stream"; return; }
      this.error = j.error;
      if (j.ch_names.join() !== this.ch.join()) { this.ch = j.ch_names; this.spectrum = parseSpectrum(this.ch); this.clear(); }
      this.sfreq = j.sfreq;
      for (let i = 0; i < j.times.length; i++) { this.times.push(j.times[i]); this.data.push(j.data[i]); }
      if (j.times.length) this.lastArrival = performance.now();
      const cut = this.since - this.keep;
      let drop = 0;
      while (drop < this.times.length && this.times[drop] < cut) drop++;
      if (drop) { this.times.splice(0, drop); this.data.splice(0, drop); }
    } catch (e) {
      this.error = e.message;
      this.retryAt = performance.now() + 2000;
    } finally {
      this.busy = false;
    }
  }

  /** Seconds since data last arrived (Infinity if never). */
  get staleness() { return this.lastArrival ? (performance.now() - this.lastArrival) / 1000 : Infinity; }
}

/** Polls one or more string (marker / prediction) streams. */
class EventBuffer {
  constructor(names = [], keep = 400) {
    this.names = new Set(names);
    this.since = {};
    this.retryAt = {};
    this.events = [];
    this.keep = keep;
    this.present = {};  // stream name -> currently found on the network
  }

  setNames(names) {
    this.names = new Set(names);
    this.events = this.events.filter((e) => this.names.has(e.stream));
  }

  async poll() {
    for (const name of this.names) {
      if (performance.now() < (this.retryAt[name] || 0)) continue;
      try {
        const r = await fetch(`/api/watch?name=${encodeURIComponent(name)}&since=${this.since[name] || 0}&window=120`);
        const j = await r.json();
        if (!r.ok || j.missing) { this.present[name] = false; this.retryAt[name] = performance.now() + 3000; continue; }
        this.present[name] = true;
        if (j.kind !== "markers") continue;
        j.times.forEach((t, i) => this.events.push({ t, v: j.values[i], stream: name }));
        if (j.times.length) { this.since[name] = j.times[j.times.length - 1]; this.lastArrival = performance.now(); }
      } catch { this.retryAt[name] = performance.now() + 3000; }
    }
    this.events.sort((a, b) => a.t - b.t);
    if (this.events.length > this.keep) this.events.splice(0, this.events.length - this.keep);
  }
}

function eventLabel(v) {
  try { const j = JSON.parse(v); if (j && j.label !== undefined) return "→" + j.label; } catch { /* plain marker */ }
  return v;
}

/**
 * Draw a stream into a canvas.
 * opts: window (s), mode ("auto" | "stack" | "overlay"), scale (µV per half row, or "auto"),
 *       page / perPage (stack mode channel paging), compact (small node plot), events.
 */
function drawStream(canvas, buf, opts = {}) {
  const css = getComputedStyle(document.documentElement);
  const color = (v, fb) => (css.getPropertyValue(v).trim() || fb);
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const compact = !!opts.compact;
  const muted = color("--muted", "#888"), border = color("--border", "#ccc"), text = color("--text", "#222");
  ctx.font = `${compact ? 9 : 11}px ${color("--mono", "monospace")}`;
  ctx.textBaseline = "alphabetic";

  if (!buf || !buf.times.length) {
    ctx.fillStyle = muted;
    const msg = !buf ? "" : buf.missing ? "not streaming — Run the pipeline" : buf.error || "waiting for data…";
    ctx.fillText(msg, 8, compact ? h / 2 + 3 : 20);
    return;
  }

  if (buf.spectrum) return drawSpectrum(ctx, w, h, buf, opts, color);

  const win = opts.window || 30;
  const tEnd = buf.times[buf.times.length - 1], tStart = tEnd - win;
  const overlay = opts.mode === "overlay" || (opts.mode !== "stack" && (buf.sfreq === 0 || buf.sfreq < 20));
  const left = compact ? 4 : overlay ? 52 : 64, right = compact ? 4 : 10, top = compact ? 4 : 8;
  const bottom = compact ? 4 : 18;
  const plotH = h - top - bottom;
  const xOf = (t) => left + ((t - tStart) / win) * (w - left - right);
  let i0 = 0;
  while (i0 < buf.times.length && buf.times[i0] < tStart) i0++;

  // time grid
  ctx.strokeStyle = border; ctx.lineWidth = 1;
  const stepS = win <= 10 ? 1 : win <= 30 ? 5 : 10;
  for (let rel = 0; rel >= -win; rel -= stepS) {  // ticks counted back from the newest sample
    const x = xOf(tEnd + rel);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, h - bottom); ctx.stroke();
    if (!compact) {
      ctx.fillStyle = muted;
      ctx.fillText(rel === 0 ? "now" : `${rel} s`, x - 10, h - 4);
    }
  }

  // events
  const evColor = color("--k-markers", "#e08a00");
  for (const e of opts.events || []) {
    if (e.t < tStart || e.t > tEnd) continue;
    const x = xOf(e.t);
    ctx.strokeStyle = evColor; ctx.lineWidth = compact ? 1 : 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, h - bottom); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = evColor;
    ctx.fillText(eventLabel(e.v).slice(0, 14), x + 3, top + (compact ? 8 : 11));
  }

  const n = buf.times.length;
  const step = Math.max(1, Math.floor((n - i0) / (w * 1.5)));
  if (overlay) {
    const chans = buf.ch.map((_, c) => c).slice(0, compact ? 8 : 16);
    let lo = Infinity, hi = -Infinity;
    for (let i = i0; i < n; i++) for (const c of chans) { const v = buf.data[i][c]; if (v < lo) lo = v; if (v > hi) hi = v; }
    if (!(hi > lo)) { hi = lo + 1; lo -= 1; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    const yOf = (v) => top + (1 - (v - lo) / (hi - lo)) * plotH;
    if (!compact) {
      ctx.fillStyle = muted;
      for (const v of [lo + pad, (lo + hi) / 2, hi - pad]) {
        const y = yOf(v);
        ctx.strokeStyle = border; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(w - right, y); ctx.stroke();
        ctx.fillText(fmtNum(v), 4, y + 4);
      }
    }
    chans.forEach((c, k) => {
      ctx.strokeStyle = SCOPE_COLORS[k % SCOPE_COLORS.length];
      ctx.lineWidth = compact ? 1.2 : 1.6;
      ctx.beginPath();
      for (let i = i0; i < n; i += step) {
        const x = xOf(buf.times[i]), y = yOf(buf.data[i][c]);
        if (i === i0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    });
    if (!compact) {  // legend
      let x = left + 6;
      chans.forEach((c, k) => {
        const label = buf.ch[c];
        ctx.fillStyle = SCOPE_COLORS[k % SCOPE_COLORS.length];
        ctx.fillRect(x, top + 16, 10, 3);
        ctx.fillStyle = text;
        ctx.fillText(label, x + 13, top + 21);
        x += 20 + ctx.measureText(label).width;
      });
    }
    return;
  }

  // stacked traces, per-channel mean removed (BioSemi data carries large DC offsets)
  const perPage = compact ? Math.min(6, buf.ch.length) : (opts.perPage || 16);
  const page = compact ? 0 : (opts.page || 0);
  const chans = buf.ch.map((_, c) => c).slice(page * perPage, page * perPage + perPage);
  const rowH = plotH / Math.max(1, chans.length);
  const trace = color("--k-signal", "#3b7ddd");
  chans.forEach((c, k) => {
    const y0 = top + rowH * (k + 0.5);
    let mean = 0, sq = 0, cnt = 0;
    for (let i = i0; i < n; i++) { const v = buf.data[i][c]; mean += v; sq += v * v; cnt++; }
    mean /= Math.max(1, cnt);
    const sd = Math.sqrt(Math.max(0, sq / Math.max(1, cnt) - mean * mean));
    const half = opts.scale === "auto" || compact ? Math.max(1e-6, 3 * sd) : Number(opts.scale || 50);
    const kY = (rowH / 2) / half;
    if (!compact) { ctx.fillStyle = text; ctx.fillText(buf.ch[c].slice(0, 9), 6, y0 + 4); }
    ctx.strokeStyle = trace; ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = i0; i < n; i += step) {
      const x = xOf(buf.times[i]);
      const y = Math.max(y0 - rowH / 2, Math.min(y0 + rowH / 2, y0 - (buf.data[i][c] - mean) * kY));
      if (i === i0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  });
}

function fmtNum(v) {
  const a = Math.abs(v);
  if (a >= 1000 || (a > 0 && a < 0.01)) return v.toExponential(1);
  return v.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2);
}

function openScopeWindow(stream, filter = null) {
  const url = `/scope.html?stream=${encodeURIComponent(stream)}` + (filter ? `&filter=${filter}` : "");
  window.open(url, `signalseedbci-scope-${stream}`, "width=1100,height=640");
}

// ------------------------------------------------------------------ spectra (SpectrumOut streams)

/** Channel labels 'CH@FREQ' (channel-major) -> {chans, freqs}, else null. */
function parseSpectrum(ch) {
  if (!ch.length || !ch.every((c) => c.includes("@"))) return null;
  const chans = [], freqs = [];
  for (const label of ch) {
    const i = label.lastIndexOf("@");
    const name = label.slice(0, i), f = Number(label.slice(i + 1));
    if (!Number.isFinite(f)) return null;
    if (chans[chans.length - 1] !== name) chans.push(name);
    if (chans.length === 1) freqs.push(f);
  }
  return chans.length * freqs.length === ch.length ? { chans, freqs } : null;
}

/** Mean of the last `avg` frames -> per-channel arrays (and the channel mean). */
function spectrumFrame(buf, avg = 4) {
  const { chans, freqs } = buf.spectrum, nf = freqs.length;
  const n = buf.data.length, k = Math.max(1, Math.min(avg, n));
  const per = chans.map(() => new Float64Array(nf));
  for (let i = n - k; i < n; i++) {
    const row = buf.data[i];
    for (let c = 0; c < chans.length; c++) for (let f = 0; f < nf; f++) per[c][f] += row[c * nf + f] / k;
  }
  const mean = new Float64Array(nf);
  for (const a of per) for (let f = 0; f < nf; f++) mean[f] += a[f] / per.length;
  return { per, mean };
}

/** Frequency of the strongest local maximum (>= 3 Hz) of the averaged spectrum, or null
 *  when the spectrum just falls off (1/f) without a peak. */
function spectrumPeak(buf, avg = 4) {
  if (!buf?.spectrum || !buf.data.length) return null;
  const { mean } = spectrumFrame(buf, avg), freqs = buf.spectrum.freqs;
  let best = -1;
  for (let f = 1; f < freqs.length - 1; f++) {
    if (freqs[f] < 3 || !(mean[f] > mean[f - 1] && mean[f] >= mean[f + 1])) continue;
    if (best < 0 || mean[f] > mean[best]) best = f;
  }
  return best < 0 ? null : freqs[best];
}

const toDb = (v) => 10 * Math.log10(Math.max(v, 1e-12));

function viridis(t) {
  // compact viridis approximation, t in [0, 1]
  const c = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
  t = Math.min(1, Math.max(0, t)) * (c.length - 1);
  const i = Math.min(c.length - 2, Math.floor(t)), u = t - i;
  return c[i].map((v, k) => Math.round(v + (c[i + 1][k] - v) * u));
}

/**
 * opts.view: "spectrum" | "spectrogram"; opts.channel: "all" | "mean" | channel name;
 * opts.avg: frames averaged (spectrum view); opts.db: dB (default) or linear;
 * opts.window: seconds shown in the spectrogram; opts.events; opts.compact.
 */
function drawSpectrum(ctx, w, h, buf, opts, color) {
  const compact = !!opts.compact;
  const { chans, freqs } = buf.spectrum, nf = freqs.length;
  const db = opts.db !== false;
  const muted = color("--muted", "#888"), border = color("--border", "#ccc"), text = color("--text", "#222");
  const view = opts.view || "spectrum";
  const left = compact ? 4 : 52, right = compact ? 4 : 12, top = compact ? 4 : 22;
  const bottom = compact ? 4 : view === "spectrogram" ? 36 : 24;
  const pw = w - left - right, ph = h - top - bottom;
  const f0 = freqs[0], f1 = freqs[nf - 1];
  const xOfF = (f) => left + ((f - f0) / Math.max(1e-9, f1 - f0)) * pw;
  const channel = opts.channel || (compact ? "mean" : "all");

  const freqTicks = () => {
    const step = f1 - f0 > 60 ? 20 : f1 - f0 > 25 ? 5 : 2;
    return Array.from({ length: Math.floor(f1 / step) - Math.ceil(f0 / step) + 1 }, (_, i) => (Math.ceil(f0 / step) + i) * step);
  };

  if (view === "spectrogram") {
    const win = opts.window || 30;
    const tEnd = buf.times[buf.times.length - 1], tStart = tEnd - win;
    let i0 = 0;
    while (i0 < buf.times.length && buf.times[i0] < tStart) i0++;
    const frames = buf.data.slice(i0), times = buf.times.slice(i0);
    const ci = channel === "mean" || channel === "all" ? -1 : chans.indexOf(channel);
    const vals = frames.map((row) => {
      const out = new Float64Array(nf);
      for (let f = 0; f < nf; f++) {
        let v = 0;
        if (ci >= 0) v = row[ci * nf + f];
        else { for (let c = 0; c < chans.length; c++) v += row[c * nf + f]; v /= chans.length; }
        out[f] = db ? toDb(v) : v;
      }
      return out;
    });
    const flat = vals.flatMap((a) => [...a]).sort((a, b) => a - b);
    const lo = flat[Math.floor(flat.length * 0.02)] ?? 0, hi = flat[Math.floor(flat.length * 0.98)] ?? 1;
    const img = new ImageData(Math.max(1, vals.length), nf);
    vals.forEach((col, x) => {
      for (let f = 0; f < nf; f++) {
        const [r, g, b] = viridis((col[f] - lo) / Math.max(1e-9, hi - lo));
        const o = ((nf - 1 - f) * img.width + x) * 4;
        img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = 255;
      }
    });
    const off = document.createElement("canvas");
    off.width = img.width; off.height = nf;
    off.getContext("2d").putImageData(img, 0, 0);
    const xOfT = (t) => left + ((t - tStart) / win) * pw;
    ctx.imageSmoothingEnabled = false;
    if (times.length) {
      const x0 = xOfT(times[0]), x1 = xOfT(tEnd) + pw / Math.max(1, win / 0.25) ;
      ctx.drawImage(off, x0, top, Math.max(1, x1 - x0), ph);
    }
    if (!compact) {
      ctx.fillStyle = muted;
      for (const f of freqTicks()) ctx.fillText(`${f} Hz`, 6, top + (1 - (f - f0) / (f1 - f0)) * ph + 4);
      const stepS = win <= 10 ? 1 : win <= 30 ? 5 : 10;
      for (let rel = 0; rel >= -win; rel -= stepS) {
        ctx.fillText(rel === 0 ? "now" : `${rel} s`, Math.max(left, xOfT(tEnd + rel) - 10), top + ph + 14);
      }
      ctx.fillText(`colour: ${channel === "all" ? "channel mean" : channel}, ${db ? "dB" : "µV²/Hz"} ${fmtNum(lo)} … ${fmtNum(hi)}`, left, h - 5);
    }
    const evColor = color("--k-markers", "#e08a00");
    for (const e of opts.events || []) {
      if (e.t < tStart || e.t > tEnd) continue;
      const x = xOfT(e.t);
      ctx.strokeStyle = evColor; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + ph); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = evColor; ctx.fillText(eventLabel(e.v).slice(0, 14), x + 3, top + 11);
    }
    return;
  }

  // ---- spectrum view
  const { per, mean } = spectrumFrame(buf, opts.avg || 4);
  const tr = (v) => (db ? toDb(v) : v);
  const lines = [];
  if (channel === "all") {
    per.slice(0, 16).forEach((a, k) => lines.push({ v: [...a].map(tr), color: SCOPE_COLORS[k % SCOPE_COLORS.length], width: compact ? 0.8 : 1.2, alpha: compact ? 0.35 : 0.75, name: chans[k] }));
    lines.push({ v: [...mean].map(tr), color: text, width: compact ? 1.6 : 2.4, alpha: 1, name: "mean" });
  } else if (channel === "mean") {
    if (compact) per.slice(0, 16).forEach((a, k) => lines.push({ v: [...a].map(tr), color: SCOPE_COLORS[k % SCOPE_COLORS.length], width: 0.8, alpha: 0.3 }));
    lines.push({ v: [...mean].map(tr), color: compact ? color("--k-features", "#9b51e0") : text, width: compact ? 1.8 : 2.4, alpha: 1, name: "mean" });
  } else {
    const c = Math.max(0, chans.indexOf(channel));
    lines.push({ v: [...per[c]].map(tr), color: SCOPE_COLORS[0], width: 2.2, alpha: 1, name: chans[c] });
  }
  let lo = Infinity, hi = -Infinity;
  for (const l of lines) for (const v of l.v) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!db) lo = 0;
  if (!(hi > lo)) hi = lo + 1;
  const pad = (hi - lo) * 0.08; lo -= db ? pad : 0; hi += pad;
  const yOf = (v) => top + (1 - (v - lo) / (hi - lo)) * ph;

  // frequency bands
  const bands = [["θ", 4, 8, 0.05], ["α", 8, 12, 0.13], ["β", 13, 30, 0.06]];
  for (const [label, a, b, alpha] of bands) {
    if (b < f0 || a > f1) continue;
    const xa = xOfF(Math.max(a, f0)), xb = xOfF(Math.min(b, f1));
    ctx.globalAlpha = alpha; ctx.fillStyle = color("--k-features", "#9b51e0");
    ctx.fillRect(xa, top, xb - xa, ph); ctx.globalAlpha = 1;
    if (!compact) { ctx.fillStyle = muted; ctx.fillText(label, (xa + xb) / 2 - 3, top + ph - 6); }
  }
  if (!compact) {
    ctx.strokeStyle = border; ctx.fillStyle = muted; ctx.lineWidth = 1;
    for (const f of freqTicks()) {
      const x = xOfF(f);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + ph); ctx.stroke();
      ctx.fillText(`${f}`, x - 5, h - 8);
    }
    ctx.fillText("Hz", 4, h - 8);
    for (const v of [lo + (db ? pad : 0), (lo + hi) / 2, hi - pad]) {
      const y = yOf(v);
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(w - right, y); ctx.stroke();
      ctx.fillText(fmtNum(v), 4, y + 4);
    }
    ctx.fillText(db ? "dB" : "µV²/Hz", 4, top - 8);
  }
  for (const l of lines) {
    ctx.globalAlpha = l.alpha; ctx.strokeStyle = l.color; ctx.lineWidth = l.width;
    ctx.beginPath();
    l.v.forEach((v, f) => { const x = xOfF(freqs[f]), y = yOf(v); if (f === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  const peak = spectrumPeak(buf, opts.avg || 4);
  if (peak != null) {
    const fi = freqs.indexOf(peak), mv = tr(mean[fi]);
    const x = xOfF(peak), y = yOf(mv);
    ctx.fillStyle = color("--k-predictions", "#d9465f");
    ctx.beginPath(); ctx.arc(x, y, compact ? 2.5 : 4, 0, 2 * Math.PI); ctx.fill();
    if (!compact) ctx.fillText(`peak ${peak.toFixed(1)} Hz`, Math.min(x + 6, w - right - 90), Math.max(top + 12, y - 6));
  }
  if (!compact && channel === "all" && chans.length <= 16) {
    let x = left + 30;
    lines.forEach((l) => {
      if (!l.name) return;
      ctx.fillStyle = l.color; ctx.fillRect(x, top - 12, 10, 3);
      ctx.fillStyle = text; ctx.fillText(l.name, x + 13, top - 7);
      x += 22 + ctx.measureText(l.name).width;
    });
  }
}
