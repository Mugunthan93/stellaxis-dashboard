/* Jarvis's hologram: a turning sphere of light filaments, drawn on a 2D canvas with its own 3D projection (no library).
   It swells and ripples with level() (0..1, the microphone) and changes pace and colour with state()
   ("idle", "listening", "thinking", "speaking", "error"). Large on Home, small in the sidebar of every other page.

     JarvisOrb(canvas, { level: () => 0, state: () => "idle", small: false })
*/
(() => {
  const TAU = Math.PI * 2, TILT = 0.32;
  const COLOURS = { idle: [255, 176, 72], listening: [255, 216, 138], thinking: [255, 148, 44], speaking: [255, 198, 100], error: [255, 96, 96] };
  const rng = seed => () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;

  window.JarvisOrb = function (canvas, opts = {}) {
    const ctx = canvas.getContext("2d"), level = opts.level || (() => 0), state = opts.state || (() => "idle");
    const small = !!opts.small, rand = rng(7), calm = matchMedia("(prefers-reduced-motion: reduce)");
    // each filament is part of a circle at its own radius and tilt, drifting at its own speed
    const arcs = Array.from({ length: small ? 14 : 46 }, () => ({
      r: 0.42 + 0.58 * Math.sqrt(rand()), a: rand() * TAU, b: Math.acos(2 * rand() - 1), span: (0.25 + 0.75 * rand()) * TAU,
      w: (rand() < 0.5 ? -1 : 1) * (0.15 + rand() * 0.6), ph: rand() * TAU, k: 2 + Math.floor(rand() * 5), node: rand() }));
    const dust = small ? [] : Array.from({ length: 170 }, () => {
      const u = rand() * TAU, v = Math.acos(2 * rand() - 1), r = 0.92 + rand() * 0.36;
      return [r * Math.sin(v) * Math.cos(u), r * Math.cos(v), r * Math.sin(v) * Math.sin(u), rand() * TAU];
    });
    let W = 0, H = 0, D = 1, raf = 0, timer = 0, lv = 0, spin = 0, last = performance.now();
    const col = COLOURS.idle.slice(), t0 = last;

    function size() {
      D = Math.min(devicePixelRatio || 1, 2);  // the layout size: a transform on the way (Home's flight) must not shrink the drawing
      W = canvas.width = Math.max(1, Math.round(canvas.clientWidth * D)); H = canvas.height = Math.max(1, Math.round(canvas.clientHeight * D));
    }
    // unit-sphere point -> [screen x, screen y, depth 0 (far) .. 1 (near)]
    function project(x, y, z, cx, cy, R) {
      const y2 = y * Math.cos(TILT) - z * Math.sin(TILT), z2 = y * Math.sin(TILT) + z * Math.cos(TILT), f = 2.8 / (2.8 - z2);
      return [cx + x * R * f, cy + y2 * R * f, (z2 + 1.3) / 2.6];
    }
    function draw(t) {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, W, H);
      const cx = W / 2, cy = small ? H / 2 : H * 0.44, R = Math.min(W, H) * (small ? 0.4 : 0.33) * (1 + lv * 0.1);
      const rgb = col.map(Math.round).join(","), flick = 0.94 + 0.06 * Math.sin(t * 37);
      ctx.globalCompositeOperation = "lighter";
      const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.3);
      glow.addColorStop(0, `rgba(${rgb},${0.3 + lv * 0.35})`); glow.addColorStop(0.4, `rgba(${rgb},0.09)`); glow.addColorStop(1, `rgba(${rgb},0)`);
      ctx.fillStyle = glow; ctx.fillRect(cx - R * 1.35, cy - R * 1.35, R * 2.7, R * 2.7);

      if (!small) {  // the plate it is projected from, and the light rising off it
        const by = cy + R * 1.42, bw = R * 0.66, bh = R * 0.12;
        const beam = ctx.createLinearGradient(0, by, 0, cy);
        beam.addColorStop(0, "rgba(79,216,255,0.16)"); beam.addColorStop(1, "rgba(79,216,255,0)");
        ctx.fillStyle = beam; ctx.beginPath(); ctx.moveTo(cx - bw, by); ctx.lineTo(cx - R * 1.05, cy); ctx.lineTo(cx + R * 1.05, cy); ctx.lineTo(cx + bw, by); ctx.fill();
        ctx.lineWidth = 1.5 * D;
        [1, 0.72, 0.4].forEach((s, i) => {
          ctx.strokeStyle = `rgba(79,216,255,${0.6 - i * 0.16})`;
          ctx.setLineDash(i === 1 ? [6 * D, 5 * D] : []); ctx.lineDashOffset = -t * 30;
          ctx.beginPath(); ctx.ellipse(cx, by, bw * s, bh * s, 0, 0, TAU); ctx.stroke();
        });
        ctx.setLineDash([]);
      }

      const seg = small ? 16 : 34; ctx.lineCap = "round";
      for (const a of arcs) {
        const A = a.a + spin + t * a.w * 0.3, cA = Math.cos(A), sA = Math.sin(A), cb = Math.cos(a.b), sb = Math.sin(a.b);
        const at = th => {
          const r = a.r * (1 + lv * 0.16 * Math.sin(th * a.k + t * 7 + a.ph)), x1 = r * Math.cos(th), y1 = r * Math.sin(th) * cb, z1 = r * Math.sin(th) * sb;
          return project(x1 * cA + z1 * sA, y1, -x1 * sA + z1 * cA, cx, cy, R);
        };
        const start = a.ph + t * a.w * 0.4;
        let p = at(start);
        for (let i = 1; i <= seg; i++) {
          const q = at(start + a.span * i / seg), d = (p[2] + q[2]) / 2;
          ctx.strokeStyle = `rgba(${rgb},${(0.1 + 0.62 * d) * (0.65 + lv * 0.35) * flick})`;
          ctx.lineWidth = (small ? 0.9 : 1.15) * D * (0.5 + d);
          ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
          p = q;
        }
        const n = at(start + a.span * ((a.node + t * 0.07 * (1 + Math.abs(a.w))) % 1));  // a bright point riding the filament
        ctx.fillStyle = `rgba(255,244,220,${0.25 + 0.75 * n[2]})`;
        ctx.beginPath(); ctx.arc(n[0], n[1], (small ? 1 : 1.9) * D * (0.5 + n[2]), 0, TAU); ctx.fill();
      }
      const cs = Math.cos(spin * 0.6), sn = Math.sin(spin * 0.6);
      for (const [x, y, z, ph] of dust) {
        const q = project(x * cs + z * sn, y, -x * sn + z * cs, cx, cy, R * (1 + lv * 0.14));
        ctx.fillStyle = `rgba(${rgb},${(0.15 + 0.5 * q[2]) * (0.6 + 0.4 * Math.sin(t * 2 + ph))})`;
        ctx.fillRect(q[0], q[1], 1.4 * D, 1.4 * D);
      }
    }
    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000), t = (now - t0) / 1000, st = state(), target = COLOURS[st] || COLOURS.idle;
      last = now;
      const raw = st === "speaking" ? 0.4 + 0.3 * Math.sin(t * 9) * Math.sin(t * 2.3) : st === "thinking" ? 0.2 : level();
      lv += (Math.max(0, Math.min(1, raw)) - lv) * 0.18;
      for (let i = 0; i < 3; i++) col[i] += (target[i] - col[i]) * 0.08;
      spin += dt * (st === "thinking" ? 1.7 : 0.28 + lv * 0.9);
      draw(t);
      raf = requestAnimationFrame(frame);
    }
    function still() {  // reduced motion: one frame, redrawn only so the colour follows the state
      const target = COLOURS[state()] || COLOURS.idle;
      for (let i = 0; i < 3; i++) col[i] = target[i];
      lv = 0; draw(0);
    }
    function start() {
      cancelAnimationFrame(raf); clearInterval(timer);
      if (document.hidden) return;
      if (calm.matches) { still(); timer = setInterval(still, 600); } else { last = performance.now(); raf = requestAnimationFrame(frame); }
    }
    size(); start();
    new ResizeObserver(() => { size(); if (calm.matches) still(); }).observe(canvas);
    document.addEventListener("visibilitychange", start);
    calm.addEventListener("change", start);
  };
})();
