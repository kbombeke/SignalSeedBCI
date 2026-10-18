/* Emu Runner, the game of the RunnerGame node (editor node box and runner.html window).
 * A black-and-white side-scroller: an emu keeps running through the outback, and a decoded
 * "jump" (e.g. a blink) makes it leap over rocks and logs and pick up the seeds above them.
 * Hitting an obstacle scatters the seeds. "cleared x/y" is a simple BCI score: how often the
 * decoded jumps came in time. No dependencies. */
"use strict";

const RUN = {
  charX: 0.2,      // emu position, fraction of the width
  ground: 0.8,     // ground line, fraction of the height
  size: 0.06,      // emu body radius, fraction of the height
  lift: 2.0,       // body centre above the ground while running, in body radii (the legs)
  jumpS: 0.9,      // time in the air (s)
  jumpH: 0.34,     // jump height, fraction of the height
  obstH: 0.1,      // obstacle height, fraction of the height
  obstW: 0.08,     // obstacle width, fraction of the height
  assistS: 2.5,    // assist: how long a jump command waits for the right moment (s)
  hitS: 1.2,       // blinking (and not hit again) after a hit (s)
  liveS: 3,        // predictions older than this: the game pauses
};

const rnd = (k) => { const v = Math.sin(k * 12.9898 + 78.233) * 43758.5453; return v - Math.floor(v); };

class RunnerGame {
  constructor({ crossingS = 4, assist = true } = {}) {
    this.crossingS = crossingS;
    this.assist = assist;
    this.reset();
    this.lastFrame = performance.now();
    this.lastT = null;      // LSL time of the last prediction used
    this.prevJump = false;  // last prediction was a jump (for trigger "change")
    this.lastPred = null;
  }

  reset() {
    this.t = 0;
    this.world = 0;             // distance run, in screen widths (scrolls the scenery)
    this.obstacles = [];        // {x (screen widths), kind, seed, passed, hit}
    this.seeds = [];            // {x, y (fraction of height above the ground)}
    this.scatter = [];          // seeds flying off after a hit
    this.nextSpawn = 2.0;
    this.air = -1;              // time since take-off, -1 on the ground
    this.armedUntil = -1;       // assist: jump command waiting until this time
    this.hitUntil = -1;
    this.flash = -1;            // time of the last jump command ("JUMP!" cue)
    this.aspect = 2.5;          // width / height of the canvas last drawn
    this.nSpawned = 0;
    this.score = { seeds: 0, cleared: 0, hit: 0, jumps: 0 };
  }

  get speed() { return 1 / Math.max(1, this.crossingS); }  // screen widths per second

  height() {  // jump height now, fraction of the canvas height
    if (this.air < 0) return 0;
    const u = this.air / RUN.jumpS;
    return 4 * RUN.jumpH * u * (1 - u);
  }

  jumpNow() {
    if (this.air >= 0) return;
    this.air = 0;
    this.score.jumps++;
  }

  /** Seconds to wait so that the next obstacle passes under the top of the jump; null if none is near. */
  waitForNext() {
    const reach = (RUN.size + RUN.obstW / 2) / this.aspect;  // obstacle and emu touch (screen widths)
    const next = this.obstacles.find((o) => !o.passed && o.x + reach > RUN.charX);
    if (!next) return null;
    const wait = (next.x - RUN.charX) / this.speed - RUN.jumpS / 2;
    return wait > RUN.assistS ? null : wait;
  }

  /** A decoded jump. With assist, it is held until the moment that clears the next obstacle. */
  command() {
    this.flash = this.t;
    if (!this.assist) return this.jumpNow();
    const wait = this.waitForNext();
    if (wait == null || wait <= 0.02) {
      if (this.air < 0) this.jumpNow();
      else if (wait != null) this.armedUntil = this.t + RUN.assistS;  // in the air: jump again on landing
    } else this.armedUntil = this.t + wait;
  }

  /** Advance the game; it only runs while predictions arrive. */
  step(live) {
    const now = performance.now(), dt = Math.min(0.1, (now - this.lastFrame) / 1000);
    this.lastFrame = now;
    if (live) this.update(dt);
  }

  update(dt) {
    this.t += dt;
    const dx = this.speed * dt;
    this.world += dx;
    for (const o of this.obstacles) o.x -= dx;
    for (const s of this.seeds) s.x -= dx;

    // new obstacles, with an arc of seeds over each (where a well-timed jump passes)
    this.nextSpawn -= dt;
    if (this.nextSpawn <= 0) {
      const x = 1.15;
      this.nSpawned++;
      this.obstacles.push({ x, kind: Math.random() < 0.55 ? "rock" : "log", seed: this.nSpawned, passed: false, hit: false });
      for (const k of [-1, 0, 1]) {
        const u = 0.5 + k * 0.22;  // position along the jump; 0.5 = its top, above the obstacle
        this.seeds.push({ x: x + k * 0.22 * RUN.jumpS * this.speed, y: 4 * RUN.jumpH * u * (1 - u) + RUN.size * RUN.lift });
      }
      this.nextSpawn = (0.45 + Math.random() * 0.45) * this.crossingS;
    }

    // assist: jump when the held command's moment comes
    if (this.armedUntil >= 0 && this.air < 0) {
      const wait = this.waitForNext();
      if ((wait != null && wait <= 0) || this.t >= this.armedUntil) { this.jumpNow(); this.armedUntil = -1; }
    }
    if (this.air >= 0) {
      this.air += dt;
      if (this.air >= RUN.jumpS) this.air = -1;
    }

    // obstacles: hit (the feet are lower than its top), or cleared once passed
    const h = this.height(), a = this.aspect;
    for (const o of this.obstacles) {
      const gap = Math.abs(o.x - RUN.charX) * a;  // in canvas heights
      if (!o.passed && !o.hit && gap < RUN.size * 0.8 + RUN.obstW / 2 && h < RUN.obstH * 0.9 && this.t >= this.hitUntil) {
        o.hit = true;
        this.score.hit++;
        this.hitUntil = this.t + RUN.hitS;
        for (let i = 0; i < Math.min(this.score.seeds, 10); i++) {
          const ang = Math.PI * (0.15 + 0.7 * Math.random());
          this.scatter.push({ x: RUN.charX, y: RUN.size * RUN.lift + h, vx: Math.cos(ang) * 0.25 * (Math.random() < 0.5 ? -1 : 1),
                              vy: Math.sin(ang) * 0.9, life: 1.2 });
        }
        this.score.seeds = 0;
      }
      if (!o.passed && (RUN.charX - o.x) * a > RUN.size + RUN.obstW / 2) {
        o.passed = true;
        if (!o.hit) this.score.cleared++;
      }
    }
    // seeds: picked up when the emu's body (or head) touches them
    const cy = RUN.size * RUN.lift + h;
    this.seeds = this.seeds.filter((s) => {
      const d = Math.hypot((s.x - RUN.charX) * a, s.y - cy);
      if (d < RUN.size * 1.6) { this.score.seeds++; return false; }
      return s.x > -0.1;
    });
    this.obstacles = this.obstacles.filter((o) => o.x > -0.2);
    for (const s of this.scatter) { s.x += s.vx * dt / a; s.y += s.vy * dt; s.vy -= 2.2 * dt; s.life -= dt; }
    this.scatter = this.scatter.filter((s) => s.life > 0 && s.y > -0.1);
  }

  /** opts: dim (no predictions), compact (node box), label (text in the sky) */
  draw(canvas, opts = {}) {
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (!W || !H) return;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.aspect = W / H;
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue("--text").trim() || "#111";
    const bg = css.getPropertyValue(opts.compact ? "--panel-2" : "--bg").trim() || "#fff";
    const gy = RUN.ground * H, lw = Math.max(1, H * 0.006);
    const wrap = (v, m) => ((v % m) + m) % m;

    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = ink; ctx.fillStyle = ink; ctx.lineWidth = lw; ctx.lineJoin = "round"; ctx.lineCap = "round";

    // sky: the sun, and clouds drifting slowly
    const sr = H * 0.055, sx = W * 0.68, sy = H * 0.2;
    ctx.beginPath(); ctx.arc(sx, sy, sr, 0, Math.PI * 2); ctx.stroke();
    for (let k = 0; k < 12; k++) {
      const an = (k / 12) * Math.PI * 2 + this.t * 0.1;
      ctx.beginPath(); ctx.moveTo(sx + Math.cos(an) * sr * 1.35, sy + Math.sin(an) * sr * 1.35);
      ctx.lineTo(sx + Math.cos(an) * sr * 1.7, sy + Math.sin(an) * sr * 1.7); ctx.stroke();
    }
    for (let i = 0; i < 3; i++) {
      const x = (wrap(i * 0.41 - this.world * 0.12, 1.24) - 0.12) * W, y = H * (0.13 + 0.08 * (i % 2)), r = H * 0.032;
      ctx.beginPath();
      ctx.arc(x, y, r, Math.PI * 0.5, Math.PI * 1.5);
      ctx.arc(x + r * 1.2, y - r * 0.7, r * 1.1, Math.PI, Math.PI * 2);
      ctx.arc(x + r * 2.4, y, r, Math.PI * 1.5, Math.PI * 0.5);
      ctx.closePath(); ctx.stroke();
    }
    // far away: low hills and gum trees (parallax)
    for (let i = 0; i < 4; i++) {
      const x = (wrap(i * 0.37 - this.world * 0.3, 1.48) - 0.2) * W, r = H * (0.13 + 0.05 * (i % 2));
      ctx.beginPath(); ctx.ellipse(x, gy, r * 1.8, r, 0, Math.PI, 0); ctx.stroke();
    }
    for (let i = 0; i < 3; i++) {
      const x = (wrap(i * 0.53 + 0.2 - this.world * 0.45, 1.6) - 0.25) * W, th = H * (0.2 + 0.05 * (i % 2));
      ctx.lineWidth = lw * 1.4;
      ctx.beginPath(); ctx.moveTo(x, gy); ctx.quadraticCurveTo(x - th * 0.08, gy - th * 0.5, x + th * 0.05, gy - th); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x - th * 0.02, gy - th * 0.55); ctx.lineTo(x + th * 0.18, gy - th * 0.75); ctx.stroke();
      ctx.lineWidth = lw;
      for (const [ox, oy, rr] of [[0.05, -1.05, 0.2], [-0.16, -0.95, 0.15], [0.26, -0.9, 0.16], [0.05, -0.85, 0.14]]) {
        ctx.beginPath(); ctx.arc(x + ox * th, gy + oy * th, rr * th, 0, Math.PI * 2);
        ctx.fillStyle = bg; ctx.fill(); ctx.stroke();
      }
    }

    // ground: a line with grass tufts, and pebbles in the dirt below, scrolling with the emu
    ctx.fillStyle = bg; ctx.fillRect(0, gy, W, H - gy);
    ctx.fillStyle = ink;
    for (let k = 0; k < 46; k++) {
      const x = wrap(rnd(k) - this.world, 1) * W, y = gy + (0.12 + 0.8 * rnd(k + 100)) * (H - gy);
      const len = H * (0.008 + 0.016 * rnd(k + 200));
      if (k % 3) { ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + len, y); ctx.stroke(); }
      else { ctx.beginPath(); ctx.arc(x, y, len * 0.35, 0, Math.PI * 2); ctx.fill(); }
    }
    ctx.lineWidth = lw * 1.8; ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke(); ctx.lineWidth = lw;
    for (let k = 0; k < 14; k++) {
      const x = wrap(k / 14 + rnd(k + 300) * 0.04 - this.world, 1) * W, g = H * 0.025;
      ctx.beginPath();
      for (const dxg of [-0.5, 0, 0.5]) { ctx.moveTo(x, gy); ctx.lineTo(x + dxg * g, gy - g * (1 - Math.abs(dxg) * 0.4)); }
      ctx.stroke();
    }

    // obstacles: rocks and logs
    const ow = RUN.obstW * H, oh = RUN.obstH * H;
    for (const o of this.obstacles) {
      const x0 = o.x * W - ow / 2;
      ctx.fillStyle = ink;
      if (o.kind === "rock") {
        ctx.beginPath();
        const n = 7;
        for (let k = 0; k <= n; k++) {
          const an = Math.PI + (k / n) * Math.PI, r = 0.8 + 0.25 * rnd(o.seed * 10 + k);
          const px = o.x * W + Math.cos(an) * ow * 0.55 * r, py = gy + Math.sin(an) * oh * r;
          if (k === 0) ctx.moveTo(px, gy); else ctx.lineTo(px, Math.min(gy, py));
        }
        ctx.closePath(); ctx.fill();
        ctx.strokeStyle = bg; ctx.lineWidth = lw * 0.9;
        ctx.beginPath(); ctx.moveTo(o.x * W - ow * 0.15, gy - oh * 0.75); ctx.lineTo(o.x * W, gy - oh * 0.45); ctx.lineTo(o.x * W + ow * 0.08, gy - oh * 0.2); ctx.stroke();
        ctx.strokeStyle = ink; ctx.lineWidth = lw;
      } else {
        // a log lying across the path: bark lines, and the sawn end with its rings
        const lh = oh * 0.8, r = lh / 2, lx = x0 - ow * 0.15, lwid = ow * 1.3, ex = lx + lwid - r * 0.45;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(lx, gy - lh, lwid, lh, r); else ctx.rect(lx, gy - lh, lwid, lh);
        ctx.fill();
        ctx.strokeStyle = bg; ctx.lineWidth = lw * 0.9;
        ctx.beginPath(); ctx.moveTo(lx + r * 0.6, gy - lh * 0.35); ctx.lineTo(lx + lwid * 0.55, gy - lh * 0.35); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(lx + lwid * 0.25, gy - lh * 0.7); ctx.lineTo(lx + lwid * 0.7, gy - lh * 0.7); ctx.stroke();
        ctx.fillStyle = bg;
        ctx.beginPath(); ctx.ellipse(ex, gy - r, r * 0.4, r * 0.82, 0, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = ink;
        ctx.beginPath(); ctx.ellipse(ex, gy - r, r * 0.2, r * 0.42, 0, 0, Math.PI * 2); ctx.stroke();
        ctx.lineWidth = lw;
      }
    }

    // seeds (bobbing), and seeds scattered by a hit
    const seed = (x, y, k, alpha = 1) => {
      const r = H * 0.02;
      y += Math.sin(this.t * 4 + k) * H * 0.008;
      ctx.save(); ctx.globalAlpha *= alpha; ctx.translate(x, y); ctx.rotate(0.35 * Math.sin(this.t * 2 + k));
      ctx.beginPath();
      ctx.moveTo(0, -r * 1.4);
      ctx.bezierCurveTo(r * 1.1, -r * 0.5, r * 0.9, r, 0, r);
      ctx.bezierCurveTo(-r * 0.9, r, -r * 1.1, -r * 0.5, 0, -r * 1.4);
      ctx.lineWidth = lw * 1.3; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, -r * 0.8); ctx.lineTo(0, r * 0.5); ctx.lineWidth = lw * 0.8; ctx.stroke();
      ctx.restore();
    };
    this.seeds.forEach((s, i) => seed(s.x * W, gy - s.y * H, i));
    this.scatter.forEach((s, i) => seed(s.x * W, gy - s.y * H, i * 0.7, Math.min(1, s.life)));

    // the emu; blinks after a hit
    const blink = this.t < this.hitUntil && Math.floor(this.t * 12) % 2 === 0;
    if (!blink) this._emu(ctx, RUN.charX * W, gy - RUN.size * RUN.lift * H - this.height() * H, RUN.size * H, gy, lw, ink, bg);

    // score, cue and label
    const fs = Math.max(opts.compact ? 9 : 12, Math.round(H * (opts.compact ? 0.075 : 0.05)));
    ctx.font = `700 ${fs}px ui-monospace, "SF Mono", Menlo, Consolas, monospace`;
    ctx.fillStyle = ink; ctx.textBaseline = "top";
    const pad = Math.max(4, H * 0.025);
    ctx.textAlign = "left";
    ctx.fillText(`SEEDS ${this.score.seeds}`, pad, pad);
    ctx.textAlign = "right";
    ctx.fillText(`CLEARED ${this.score.cleared}/${this.score.cleared + this.score.hit}`, W - pad, pad);
    if (this.flash >= 0 && this.t - this.flash < 0.7 && !opts.dim) {
      ctx.textAlign = "center";
      ctx.font = `800 ${Math.round(fs * 1.3)}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
      ctx.fillText("JUMP!", RUN.charX * W, Math.max(pad + fs * 1.4, gy - (RUN.size * 5.2 + RUN.size * RUN.lift + this.height()) * H));
    }
    if (opts.dim) {  // paused: a veil over the scene
      ctx.globalAlpha = 0.6; ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1;
    }
    if (opts.label) {
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = `600 ${Math.max(12, Math.round(H * 0.055))}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
      ctx.fillStyle = ink;
      ctx.fillText(opts.label, W / 2, H * 0.36);
    }
    ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
  }

  /** An emu facing right: shaggy body, long neck, small head with a short beak, and long
   *  legs whose ankles bend backwards. On the ground it runs; in the air it tucks its legs,
   *  stretches its neck forward and flaps its small wings. (cx, cy) = body centre, s = radius. */
  _emu(ctx, cx, cy, s, gy, lw, ink, bg) {
    const air = this.air >= 0, ph = this.world * 70;
    const hip = { x: cx + s * 0.15, y: cy + s * 0.55 };
    const leg = (foot, joint, back) => {
      ctx.strokeStyle = ink; ctx.globalAlpha = back ? 0.55 : 1;
      ctx.lineWidth = s * 0.17; ctx.beginPath(); ctx.moveTo(hip.x, hip.y); ctx.lineTo(joint.x, joint.y); ctx.stroke();
      ctx.lineWidth = s * 0.12; ctx.beginPath(); ctx.moveTo(joint.x, joint.y); ctx.lineTo(foot.x, foot.y); ctx.stroke();
      ctx.lineWidth = s * 0.08; ctx.beginPath();  // three toes forward
      for (const [tx, ty] of [[0.42, 0.02], [0.36, -0.12], [0.3, 0.12]]) { ctx.moveTo(foot.x, foot.y); ctx.lineTo(foot.x + tx * s, foot.y + ty * s * 0.5); }
      ctx.stroke(); ctx.globalAlpha = 1;
    };
    if (air) {  // legs tucked under the body
      for (const [k, back] of [[0, true], [1, false]]) {
        leg({ x: cx + s * (0.35 + 0.15 * k), y: cy + s * (1.0 + 0.05 * k) }, { x: cx - s * (0.45 - 0.1 * k), y: cy + s * 1.05 }, back);
      }
    } else {
      for (const [side, back] of [[Math.PI, true], [0, false]]) {
        const sw = Math.sin(ph + side), lift = Math.max(0, Math.cos(ph + side));
        const foot = { x: hip.x + sw * s * 1.0, y: gy - lift * s * 0.55 - s * 0.04 };
        const joint = { x: (hip.x + foot.x) / 2 - s * (0.45 - 0.2 * lift), y: (hip.y + foot.y) / 2 - lift * s * 0.15 };
        leg(foot, joint, back);
      }
    }
    // neck and head (bobbing while running, stretched forward in the air)
    const bob = air ? 0 : Math.sin(ph * 2) * s * 0.1;
    const head = air ? { x: cx + s * 1.9, y: cy - s * 1.25 } : { x: cx + s * 1.15, y: cy - s * 2.05 + bob };
    ctx.strokeStyle = ink; ctx.lineWidth = s * 0.34;
    ctx.beginPath(); ctx.moveTo(cx + s * 0.75, cy - s * 0.35);
    ctx.quadraticCurveTo(cx + s * (air ? 1.5 : 1.45), cy - s * (air ? 0.7 : 0.95), head.x - s * 0.05, head.y + s * 0.15); ctx.stroke();
    ctx.fillStyle = ink;
    ctx.beginPath(); ctx.arc(head.x, head.y, s * 0.33, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath();  // short, broad beak
    ctx.moveTo(head.x + s * 0.22, head.y - s * 0.12); ctx.quadraticCurveTo(head.x + s * 0.75, head.y - s * 0.02, head.x + s * 0.78, head.y + s * 0.06);
    ctx.lineTo(head.x + s * 0.22, head.y + s * 0.16); ctx.closePath(); ctx.fill();
    ctx.fillStyle = bg;
    ctx.beginPath(); ctx.arc(head.x + s * 0.07, head.y - s * 0.08, s * 0.12, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = ink;
    ctx.beginPath(); ctx.arc(head.x + s * 0.1, head.y - s * 0.08, s * 0.055, 0, Math.PI * 2); ctx.fill();
    // shaggy body: an egg shape with a ragged, feathered lower edge and a tuft for a tail
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(air ? -0.15 : -0.08 + Math.sin(ph) * 0.03);
    ctx.beginPath();
    ctx.moveTo(-s * 1.35, -s * 0.05);
    ctx.bezierCurveTo(-s * 1.2, -s * 1.0, s * 0.6, -s * 1.05, s * 1.0, -s * 0.3);
    const fr = 9;
    for (let k = 0; k <= fr; k++) {  // feather fringe from front to back
      const u = k / fr, x = s * 1.0 - u * s * 2.45, base = s * (0.55 + 0.25 * Math.sin(Math.PI * u));
      ctx.lineTo(x + s * 0.1, base);
      ctx.lineTo(x - s * 0.06, base + s * (0.22 + 0.08 * (k % 2)));
    }
    ctx.lineTo(-s * 1.65, s * 0.15); ctx.lineTo(-s * 1.4, s * 0.05); ctx.lineTo(-s * 1.7, -s * 0.12);  // tail tuft
    ctx.closePath(); ctx.fillStyle = ink; ctx.fill();
    ctx.strokeStyle = bg; ctx.lineWidth = Math.max(1, s * 0.06);  // feather texture and a small wing
    for (const [x, y] of [[-0.7, -0.35], [-0.2, -0.55], [0.3, -0.45], [-0.45, 0.05], [0.1, 0.0]]) {
      ctx.beginPath(); ctx.moveTo(x * s, y * s); ctx.quadraticCurveTo((x - 0.15) * s, (y + 0.15) * s, (x - 0.35) * s, (y + 0.12) * s); ctx.stroke();
    }
    const flap = air ? Math.sin(this.t * 30) * 0.35 : 0;
    ctx.lineWidth = Math.max(1, s * 0.09);
    ctx.beginPath(); ctx.moveTo(s * 0.1, -s * 0.2); ctx.quadraticCurveTo(-s * 0.3, -s * (0.25 + flap), -s * 0.6, -s * (0.05 + flap * 1.4)); ctx.stroke();
    ctx.restore();
    if (air) {  // speed lines
      ctx.strokeStyle = ink; ctx.lineWidth = Math.max(1, s * 0.07);
      for (let k = 0; k < 3; k++) {
        const y = cy - s * 0.3 + k * s * 0.4;
        ctx.beginPath(); ctx.moveTo(cx - s * (2.0 + 0.3 * k), y); ctx.lineTo(cx - s * (2.9 + 0.3 * k), y); ctx.stroke();
      }
    }
    ctx.lineWidth = lw;
  }
}

/** Turn newly arrived predictions into jump commands.
 *  opts: jumpLabel, trigger ("every" | "change"), minProba. Returns whether predictions are live. */
function runnerFeed(game, events, stream, { jumpLabel = "blink", trigger = "every", minProba = 0 } = {}) {
  const evs = events.events;
  if (game.lastT == null) {  // skip predictions left over from before this view opened
    game.lastT = evs.length ? evs[evs.length - 1].t : -Infinity;
  }
  for (const e of evs) {
    if (e.t <= game.lastT) continue;
    game.lastT = e.t;
    let label = e.v, proba = {};
    try { const j = JSON.parse(e.v); label = j.label; proba = j.proba || {}; } catch { /* plain marker */ }
    const p = jumpLabel in proba ? proba[jumpLabel] : (label === jumpLabel ? 1 : 0);
    const isJump = label === jumpLabel && p >= minProba;
    if (isJump && (trigger === "every" || !game.prevJump)) game.command();
    game.prevJump = isJump;
    game.lastPred = { label, p };
  }
  // events (e.g. blinks) may come seconds apart: then the game runs while the pipeline's stream
  // exists; a continuous decision ("change") must also keep arriving
  const present = !!events.present?.[stream];
  if (trigger === "every") return present;
  return present && !!events.lastArrival && performance.now() - events.lastArrival < RUN.liveS * 1000;
}

/** What to tell the participant, from the jump label. */
function runnerHint(jumpLabel) {
  return jumpLabel === "blink" ? "blink to jump" : jumpLabel === "closed" ? "close your eyes to jump" : `${jumpLabel} → jump`;
}

function openRunnerWindow(stream, o) {
  const q = new URLSearchParams({ stream, jump: o.jumpLabel, trigger: o.trigger, minp: String(o.minProba),
    assist: o.assist ? "1" : "0", crossing: String(o.crossingS) });
  window.open(`/runner.html?${q}`, `signalseedbci-runner-${stream}`, "width=1100,height=560");
}
