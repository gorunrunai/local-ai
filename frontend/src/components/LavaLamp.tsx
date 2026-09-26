import { useEffect, useRef } from "react";

/**
 * Voice mode's lava lamp: soft blobs that merge and split (a metaball field), drifting slowly when
 * quiet and boiling with rising bubbles while you talk or the assistant speaks. Colors follow the
 * state: dusk when idle, moonlight while listening, rose while thinking, molten orange (the logo's
 * output node) while speaking.
 */
export type LavaMode = "idle" | "listening" | "hearing" | "thinking" | "speaking";

type RGB = [number, number, number];
const PALETTE: Record<LavaMode, { core: RGB; edge: RGB; energy: number }> = {
  idle: { core: [150, 128, 190], edge: [52, 44, 92], energy: 0.06 },
  listening: { core: [196, 212, 255], edge: [86, 92, 206], energy: 0.2 },
  hearing: { core: [206, 220, 255], edge: [108, 96, 240], energy: 0.55 },
  thinking: { core: [248, 170, 210], edge: [136, 74, 204], energy: 0.45 },
  speaking: { core: [255, 196, 124], edge: [226, 85, 45], energy: 0.6 },
};

const GRID = 128;           // field resolution (pixels across); the browser scales it up smoothly
const MIN_GRID = 56;        // lowest it drops to on slow devices
const SPAN = 1.15;          // the field covers [-SPAN, SPAN] in both directions
const MAX_BUBBLES = 40;

interface Blob { ax: number; ay: number; fx: number; fy: number; px: number; py: number; r: number }
interface Bubble { x: number; y: number; vy: number; r: number; age: number; phase: number }

const rand = (a: number, b: number) => a + Math.random() * (b - a);
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const smooth = (e0: number, e1: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};

export function LavaLamp({ mode, level, size = 288 }: { mode: LavaMode; level: () => number; size?: number }) {
  const glow = useRef<HTMLDivElement>(null);
  const main = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const levelRef = useRef(level);
  levelRef.current = level;

  useEffect(() => {
    const canvas = main.current!, halo = glow.current!;
    const fctx = canvas.getContext("2d")!;
    let tinted = 0;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    // Phones: start lighter and draw at 30 fps, leaving the CPU to the audio. Everyone: if frames
    // take too long, lower the resolution (the audio matters more than the animation).
    const phone = window.matchMedia?.("(pointer: coarse)").matches ?? false;
    const frameGap = phone ? 1000 / 30 - 4 : 0;
    const maxBubbles = phone ? 24 : MAX_BUBBLES;
    let grid = 0, img: ImageData, px: Uint8ClampedArray, coord = new Float32Array(0);
    const setGrid = (g: number) => {
      grid = g;
      canvas.width = canvas.height = g;
      img = fctx.createImageData(g, g);
      px = img.data;
      coord = Float32Array.from({ length: g }, (_, i) => (i / (g - 1)) * 2 * SPAN - SPAN);
    };
    setGrid(phone ? 88 : GRID);
    let cost = 0, drawn = 0;

    const blobs: Blob[] = Array.from({ length: 6 }, (_, i) => ({
      ax: rand(0.2, 0.45), ay: rand(0.5, 0.68), fx: rand(0.25, 0.55), fy: rand(0.18, 0.42),
      px: rand(0, 6.28), py: (i / 6) * 6.28 + rand(0, 0.6), r: rand(0.12, 0.22),
    }));
    const bubbles: Bubble[] = [];
    const color = { core: [...PALETTE.idle.core] as RGB, edge: [...PALETTE.idle.edge] as RGB };
    let energy = PALETTE.idle.energy, lvl = 0, T = rand(0, 100), clock = 0, spawn = 0, last = performance.now(), raf = 0;
    const bx = new Float32Array(6 + MAX_BUBBLES), by = new Float32Array(bx.length), br2 = new Float32Array(bx.length);

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (now - drawn < frameGap) return;
      drawn = now;
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      clock += dt;
      const m = PALETTE[modeRef.current];
      const talking = modeRef.current === "hearing" || modeRef.current === "speaking" || modeRef.current === "listening";
      const raw = talking ? Math.min(1, levelRef.current()) : 0;
      lvl = mix(lvl, raw, raw > lvl ? 0.35 : 0.08);                       // quick attack, slow release
      energy = mix(energy, m.energy + lvl * (modeRef.current === "listening" ? 0.4 : 0.7), 1 - Math.exp(-dt * 2.2));
      for (let i = 0; i < 3; i++) {
        color.core[i] = mix(color.core[i], m.core[i], 1 - Math.exp(-dt * 1.6));
        color.edge[i] = mix(color.edge[i], m.edge[i], 1 - Math.exp(-dt * 1.6));
      }
      const speed = (0.12 + 1.5 * energy) * (reduced ? 0.2 : 1);
      T += dt * speed;

      // Wax: slow Lissajous drift, mostly up and down; swells with energy and throbs with the voice.
      let n = 0;
      blobs.forEach((b, i) => {
        bx[n] = b.ax * Math.sin(T * b.fx + b.px) + 0.1 * Math.sin(T * 0.37 + b.py);
        by[n] = b.ay * Math.sin(T * b.fy + b.py);
        const r = b.r * (1 + 0.08 * energy + 0.3 * lvl * (0.5 + 0.5 * Math.sin(clock * 4 + i * 1.7)));
        br2[n++] = r * r;
      });

      // Bubbles: rise from the bottom when there's activity, wobble, shrink and fade out at the top.
      if (!reduced) {
        spawn += dt * Math.max(0, energy - 0.15) * 12;
        while (spawn >= 1 && bubbles.length < maxBubbles) {
          spawn -= 1;
          bubbles.push({ x: rand(-0.45, 0.45), y: 0.72, vy: -rand(0.2, 0.45) - 0.5 * energy, r: rand(0.06, 0.12), age: 0, phase: rand(0, 6.28) });
        }
        spawn = Math.min(spawn, 1);
      }
      for (let i = bubbles.length - 1; i >= 0; i--) {
        const b = bubbles[i];
        b.age += dt;
        b.y += b.vy * dt;
        b.x += Math.sin(b.age * 3 + b.phase) * 0.12 * dt;
        if (b.y < -0.85) { bubbles.splice(i, 1); continue; }
        const r = b.r * (0.45 + 0.55 * (b.y + 1) / 1.72) * Math.min(1, b.age * 3);
        bx[n] = b.x; by[n] = b.y; br2[n++] = r * r;
      }

      // Field → pixels: solid wax where blobs overlap, a faint halo around them, fading at the rim.
      const [cr, cg, cb] = color.core, [er, eg, eb] = color.edge;
      let p = 0;
      for (let j = 0; j < grid; j++) {
        const y = coord[j];
        for (let i = 0; i < grid; i++) {
          const x = coord[i];
          let f = 0;
          for (let k = 0; k < n; k++) {
            const dx = x - bx[k], dy = y - by[k];
            f += br2[k] / (dx * dx + dy * dy + 0.0004);
          }
          const rim = smooth(1.12, 0.62, Math.sqrt(x * x + y * y));
          const a = (smooth(0.8, 1.05, f) * 0.95 + Math.min(1, f) * 0.2) * rim;
          // Shading: deep color at the wax's edge, the core color inside, a pale glint where it's thickest.
          const t = smooth(0.85, 2.2, f), glint = smooth(3.5, 7, f) * 0.35;
          const heat = 0.18 * smooth(-0.2, 1, y);                          // warmer near the bottom, like a lamp
          px[p] = mix(mix(er, cr, t), 255, glint) + heat * 40;
          px[p + 1] = mix(mix(eg, cg, t), 250, glint);
          px[p + 2] = mix(mix(eb, cb, t), 245, glint);
          px[p + 3] = a * 255;
          p += 4;
        }
      }
      fctx.putImageData(img, 0, 0);
      // The halo: a soft gradient in the wax's color, swelling with energy (a cheap GPU transform);
      // its color is repainted only a few times a second.
      halo.style.transform = `scale(${(1 + 0.35 * energy + 0.25 * lvl).toFixed(3)})`;
      halo.style.opacity = (0.45 + 0.4 * energy).toFixed(3);
      if (now - tinted > 150) {
        tinted = now;
        const [r, g, b] = color.edge.map(Math.round), [r2, g2, b2] = color.core.map(Math.round);
        halo.style.background = `radial-gradient(circle, rgb(${r2} ${g2} ${b2} / 0.55) 0%, rgb(${r} ${g} ${b} / 0.35) 32%, rgb(${r} ${g} ${b} / 0) 68%)`;
      }

      cost = mix(cost, performance.now() - now, 0.1);
      if (cost > 6 && grid > MIN_GRID) { setGrid(Math.max(MIN_GRID, Math.round(grid * 0.8))); cost = 3; }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [size]);

  return (
    <div className="pointer-events-none relative" style={{ width: size, height: size }} aria-hidden>
      {/* The wax, drawn small and scaled up smoothly by the browser, over a soft gradient halo.
          No CSS filters: they're costly to repaint every frame on phones. */}
      <div ref={glow} className="absolute -inset-1/4 rounded-full will-change-transform" />
      <canvas ref={main} className="absolute inset-0 h-full w-full" data-testid="lava-lamp" />
    </div>
  );
}
