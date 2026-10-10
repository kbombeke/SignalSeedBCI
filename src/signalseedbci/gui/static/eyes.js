/* Animated eyes for the EyesFeedback node (editor node box and eyes.html window).
 * EyesAnimator.update(closure) sets the target (0 = open, 1 = closed); draw() renders
 * with smooth eyelids, occasional blinks and small gaze shifts while the eyes are open. */
"use strict";

class EyesAnimator {
  constructor() {
    this.lid = 0;            // current closure 0..1
    this.target = 0;
    this.gaze = { x: 0, y: 0 };
    this.gazeTarget = { x: 0, y: 0 };
    this.nextGaze = 0;
    this.nextBlink = performance.now() + 2500;
    this.blinkStart = -1;
    this.last = performance.now();
  }

  update(closure) { this.target = Math.max(0, Math.min(1, closure)); }

  /** Steer the gaze: (gx, gy) in -1..1 (+x = right, +y = up), or null to look around freely. */
  look(dir) { this.directed = dir; }

  step() {
    const now = performance.now(), dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    // eyelids: ~0.25 s to close or open
    this.lid += (this.target - this.lid) * (1 - Math.exp(-dt / 0.08));
    if (this.directed) {
      // far enough that the iris reaches the corner of the eye
      this.gazeTarget = { x: this.directed[0] * 1.7, y: -this.directed[1] * 1.6 };
      this.nextGaze = now + 600;
    } else if (now > this.nextGaze) {
      this.gazeTarget = { x: (Math.random() - 0.5) * 1.2, y: (Math.random() - 0.5) * 0.6 };
      this.nextGaze = now + 900 + Math.random() * 2200;
    }
    const g = 1 - Math.exp(-dt / 0.06);  // quick, saccade-like shifts
    this.gaze.x += (this.gazeTarget.x - this.gaze.x) * g;
    this.gaze.y += (this.gazeTarget.y - this.gaze.y) * g;
    let blink = 0;
    if (this.target < 0.5) {
      if (this.blinkStart < 0 && now > this.nextBlink) this.blinkStart = now;
      if (this.blinkStart >= 0) {
        const u = (now - this.blinkStart) / 180;  // 180 ms blink
        if (u >= 1) { this.blinkStart = -1; this.nextBlink = now + 2500 + Math.random() * 4000; }
        else blink = Math.sin(Math.PI * u);
      }
    } else {
      this.blinkStart = -1;
      this.nextBlink = now + 1500 + Math.random() * 3000;
    }
    // looking down lowers the upper lids a little; looking up lifts the brows (in _eye)
    const down = Math.max(0, this.gaze.y / 1.6);
    return Math.max(this.lid, blink, 0.35 * down);
  }

  /** opts: dim (waiting, no data), compact (node box), label (text under the eyes) */
  draw(canvas, opts = {}) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue("--text").trim() || "#222";
    const closure = this.step();

    const labelH = opts.label ? Math.min(60, h * 0.16) : 0;
    const areaH = h - labelH;
    // brow + eye + lashes span about 3.1 eye half-heights: size the eyes so all of it fits
    const eh = Math.min(w * 0.36 * 0.5, areaH / 3.3), ew = eh * 2;
    const cy = areaH * 0.5 + eh * 0.35;
    const bg = opts.background || css.getPropertyValue(opts.compact ? "--panel-2" : "--bg").trim() || "#f4f5f7";
    ctx.globalAlpha = opts.dim ? 0.45 : 1;
    for (const side of [-1, 1]) {
      const cx = w / 2 + side * ew * 0.62;
      this._eye(ctx, cx, cy, ew, eh, closure, side, ink, bg, opts.compact);
    }
    ctx.globalAlpha = 1;
    if (opts.label) {
      ctx.fillStyle = ink;
      ctx.font = `600 ${Math.round(labelH * 0.42)}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText(opts.label, w / 2, areaH + labelH * 0.55);
      ctx.textAlign = "start";
    }
  }

  _eye(ctx, cx, cy, ew, eh, closure, side, ink, bg, compact) {
    const L = cx - ew / 2, R = cx + ew / 2, top = cy - eh, bot = cy + eh;
    const almond = () => {
      ctx.beginPath();
      ctx.moveTo(L, cy);
      ctx.quadraticCurveTo(cx, top, R, cy);
      ctx.quadraticCurveTo(cx, bot, L, cy);
      ctx.closePath();
    };
    const lw = Math.max(1.2, ew * 0.035);

    // eyebrow: lifts slightly when open
    ctx.strokeStyle = ink; ctx.lineWidth = lw * 1.3; ctx.lineCap = "round";
    const browY = top - eh * (0.45 - 0.15 * closure + 0.18 * Math.max(0, -this.gaze.y / 1.6));
    ctx.beginPath();
    ctx.moveTo(cx - ew * 0.45, browY + eh * 0.12);
    ctx.quadraticCurveTo(cx + side * ew * 0.05, browY - eh * 0.3, cx + ew * 0.45, browY + eh * 0.12);
    ctx.stroke();

    ctx.save();
    almond();
    ctx.clip();
    ctx.fillStyle = "#fbfbf8";
    ctx.fill();
    // iris + pupil follow the gaze
    const ir = eh * 0.62;
    const ix = cx + this.gaze.x * ew * 0.16, iy = cy + this.gaze.y * eh * 0.25;
    const grad = ctx.createRadialGradient(ix, iy, ir * 0.2, ix, iy, ir);
    grad.addColorStop(0, "#6f9fcf"); grad.addColorStop(0.75, "#3d6f9e"); grad.addColorStop(1, "#24496b");
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(ix, iy, ir, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#101418";
    ctx.beginPath(); ctx.arc(ix, iy, ir * 0.45, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.beginPath(); ctx.arc(ix - ir * 0.3, iy - ir * 0.32, ir * 0.16, 0, Math.PI * 2); ctx.fill();
    // upper eyelid sweeps from the top curve down to the bottom curve; it takes the
    // background colour, so a closed eye reads as the classic lid line with lashes
    const ctrl = top + (bot - top) * closure * 1.02;
    ctx.fillStyle = bg;
    ctx.beginPath();
    ctx.moveTo(L - 2, cy);
    ctx.quadraticCurveTo(cx, top - 2, R + 2, cy);
    ctx.quadraticCurveTo(cx, ctrl, L - 2, cy);
    ctx.fill();
    ctx.restore();

    // outline: the upper half fades as the lid covers it; the lower curve stays
    ctx.strokeStyle = ink; ctx.lineWidth = lw;
    const a0 = ctx.globalAlpha;
    if (closure <= 0.03) {  // once the lid moves, its edge is the visible upper outline
      ctx.beginPath(); ctx.moveTo(L, cy); ctx.quadraticCurveTo(cx, top, R, cy); ctx.stroke();
    }
    ctx.beginPath(); ctx.moveTo(L, cy); ctx.quadraticCurveTo(cx, bot, R, cy); ctx.stroke();
    if (closure > 0.03) {
      ctx.beginPath();
      ctx.moveTo(L, cy);
      ctx.quadraticCurveTo(cx, ctrl, R, cy);
      ctx.lineWidth = lw * 1.2; ctx.stroke();
    }
    // lashes when (nearly) closed
    if (closure > 0.85) {
      ctx.lineWidth = lw * 0.8;
      for (let i = 1; i <= 5; i++) {
        const u = i / 6, x = (1 - u) * (1 - u) * L + 2 * u * (1 - u) * cx + u * u * R;
        const y = (1 - u) * (1 - u) * cy + 2 * u * (1 - u) * ctrl + u * u * cy;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + (u - 0.5) * eh * 0.25, y + eh * 0.28); ctx.stroke();
      }
    }
  }
}

/** Latest prediction -> eyelid target. Returns {closure, label, pClosed, live}. */
function eyesStateFrom(events, closedLabel = "closed", graded = false) {
  const last = events.events[events.events.length - 1];
  const live = last && events.lastArrival && performance.now() - events.lastArrival < 3000;
  if (!last) return { closure: 0, label: null, pClosed: null, live: false };
  let label = last.v, proba = {};
  try { const j = JSON.parse(last.v); label = j.label; proba = j.proba || {}; } catch { /* plain marker */ }
  const pClosed = closedLabel in proba ? proba[closedLabel] : (label === closedLabel ? 1 : 0);
  const closure = graded ? pClosed : (label === closedLabel ? 1 : 0);
  return { closure, label, pClosed, live };
}

/** Latest prediction -> gaze direction for GazeFeedback. labels: {up, down, left, right}. */
function gazeStateFrom(events, stream, labels, holdS = 1.5, graded = false) {
  const dirs = { [labels.up]: [0, 1], [labels.down]: [0, -1], [labels.left]: [-1, 0], [labels.right]: [1, 0] };
  const present = !!events.present?.[stream];
  const last = events.events[events.events.length - 1];
  if (!last) return { dir: null, label: null, p: null, present, active: false, age: null };
  let label = last.v, proba = {};
  try { const j = JSON.parse(last.v); label = j.label; proba = j.proba || {}; } catch { /* plain marker */ }
  const age = events.lastArrival ? (performance.now() - events.lastArrival) / 1000 : Infinity;
  let dir = dirs[label] || null;
  if (graded && Object.keys(proba).length) {
    dir = [0, 0];
    for (const [k, p] of Object.entries(proba)) if (dirs[k]) { dir[0] += p * dirs[k][0]; dir[1] += p * dirs[k][1]; }
  }
  const active = age < holdS && !!dir;
  return { dir: active ? dir : null, label, p: proba[label] ?? null, present, active, age };
}

function openEyesWindow(stream, closedLabel = "closed", graded = false, gaze = null) {
  const q = new URLSearchParams({ stream, closed: closedLabel, graded: graded ? "1" : "0" });
  if (gaze) {
    q.set("mode", "gaze");
    q.set("labels", [gaze.up, gaze.down, gaze.left, gaze.right].join(","));
    q.set("hold", String(gaze.hold));
  }
  window.open(`/eyes.html?${q}`, `signalseedbci-eyes-${stream}`, "width=900,height=520");
}
