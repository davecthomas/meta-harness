// The octopus inks: a click right on it lets a cloud of ink out from under its body. The cloud is a few hundred
// soft puffs, each a particle in water: let out over a second or two with a little push, slowed by drag, spread
// by diffusion (it grows fast at first, then slowly), stirred by eddies, and carried to and fro by a gentle
// current. Together they billow out over the screen, hang, and dissolve. The cloud covers the octopus, as ink is
// for; the name stays on top (splash.css).
// It is a joke, not a control: it never takes a click, and one cloud runs at a time.

// How long a cloud lasts from the first puff to the last trace, in seconds.
export const INK_SECONDS = 18;
// How long the octopus goes on letting ink out.
export const EMIT_SECONDS = 3.2;
export const PUFFS = 320;
// The ink leaves in billows: a few bursts, each its own way, so the cloud has lobes and is not a disc.
export const BILLOWS = 11;
// How long one puff lives, least and most, in seconds: they do not all dissolve at once.
export const LIFE = [10, 14.5];
// A puff's radius grows as the square root of its age, as a diffusing blob does: `GROW` times the screen's
// longer side at one second old.
export const GROW = 0.065;
// The water's drag: the share of a puff's own speed left after one second.
export const DRAG = 0.6;
// The current sways the whole cloud: how far (as a share of the screen's width a second) and how often.
export const CURRENT = { speed: 0.022, seconds: 7 };

export interface Puff {
  born: number;
  life: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  // Each puff's own size, darkness and phase, so no two move or look alike.
  size: number;
  dark: number;
  phase: number;
}

// A random number from a seed, so a cloud can be made the same twice (tests) and differently every click.
export function seeded(seed: number): () => number {
  let state = seed >>> 0 || 1;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

// The cloud's puffs, let out from (x, y) over EMIT_SECONDS. `side` is the screen's longer side, which speeds
// are measured against. Most are pushed down and outward, as ink leaves a siphon, a few every way.
export function emit(x: number, y: number, side: number, random: () => number = Math.random): Puff[] {
  const puffs: Puff[] = [];
  // Each billow leaves at its own moment, in its own direction, with its own strength.
  const billows = Array.from({ length: BILLOWS }, () => ({
    angle: Math.PI / 2 + (random() - 0.5) * Math.PI * 1.5,
    strength: 0.35 + random() * 0.65,
    at: random() * EMIT_SECONDS * 0.8,
  }));
  for (let i = 0; i < PUFFS; i++) {
    const billow = billows[Math.floor(random() * BILLOWS)];
    const angle = billow.angle + (random() - 0.5) * 0.7;
    const push = side * billow.strength * (0.04 + random() * 0.3);
    puffs.push({
      born: Math.min(EMIT_SECONDS, billow.at + random() * EMIT_SECONDS * 0.2),
      life: LIFE[0] + random() * (LIFE[1] - LIFE[0]),
      x: x + (random() - 0.5) * side * 0.02,
      y: y + (random() - 0.5) * side * 0.02,
      vx: Math.cos(angle) * push,
      vy: Math.sin(angle) * push * 0.8,
      size: 0.45 + random() * random() * 1.6,
      dark: 0.4 + random() * 0.6,
      phase: random() * Math.PI * 2,
    });
  }
  return puffs;
}

// Moves every puff on by dt seconds, at time t since the click. A puff not yet let out stays where it is.
export function step(puffs: Puff[], dt: number, t: number, side: number): void {
  const drag = Math.pow(DRAG, dt);
  // The current: one slow sway for the whole cloud, with a slower one over it so it never quite repeats.
  const sway = side * CURRENT.speed * (Math.sin((t * 2 * Math.PI) / CURRENT.seconds) + 0.5 * Math.sin((t * 2 * Math.PI) / (CURRENT.seconds * 2.7) + 1.3));
  for (const p of puffs) {
    if (t < p.born) continue;
    // Eddies: each puff is turned a little by where it is and when, so the cloud's edge curls instead of
    // spreading as a disc.
    const eddy = side * 0.02;
    const ex = Math.sin(p.y / (side * 0.11) + t * 0.7 + p.phase) * eddy;
    const ey = Math.cos(p.x / (side * 0.13) - t * 0.6 + p.phase * 1.7) * eddy;
    p.vx *= drag;
    p.vy *= drag;
    p.x += (p.vx + sway + ex) * dt;
    // Ink is a little heavier than water at first, then hangs: a slight sink that fades.
    p.y += (p.vy + ey + side * 0.006 * Math.exp(-(t - p.born))) * dt;
  }
}

// How big and how dark a puff is at time t: {radius, alpha}; alpha 0 before it is let out and after it is gone.
export function look(p: Puff, t: number, side: number): { radius: number; alpha: number } {
  const age = t - p.born;
  if (age <= 0 || age >= p.life) return { radius: 0, alpha: 0 };
  const radius = side * GROW * p.size * Math.sqrt(age + 0.05);
  // It darkens quickly as it leaves the octopus, holds, and thins out over the last half of its life.
  const arrive = Math.min(1, age / 1.2);
  const leave = Math.min(1, (p.life - age) / (p.life * 0.55));
  return { radius, alpha: 0.85 * p.dark * arrive * leave * leave };
}

// Whether a click at (x, y) is on the octopus itself, its box with no margin round it.
export function onBox(box: { left: number; top: number; width: number; height: number }, x: number, y: number): boolean {
  return x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height;
}

// Where the ink comes out, for an octopus in this box: under the middle of its body, between its arms.
export function siphon(box: { left: number; top: number; width: number; height: number }): { x: number; y: number } {
  return { x: box.left + box.width / 2, y: box.top + box.height * 0.6 };
}

function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Lets a cloud of ink out from (x, y), on screen coordinates. Returns false when a cloud is already out, or
// the system asks for less motion.
export function ink(x: number, y: number, into: HTMLElement = document.body): boolean {
  if (reducedMotion() || into.querySelector(":scope > .splash-ink")) return false;
  const canvas = document.createElement("canvas");
  canvas.className = "splash-ink";
  canvas.setAttribute("aria-hidden", "true");
  const width = window.innerWidth, height = window.innerHeight;
  // Half resolution: the puffs are soft, so nothing is lost, and a full screen of them stays smooth.
  const scale = 0.5;
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  into.appendChild(canvas);
  into.classList.add("splash-inking");
  const done = () => {
    canvas.remove();
    into.classList.remove("splash-inking");
  };
  window.setTimeout(done, INK_SECONDS * 1000);
  const context = typeof canvas.getContext === "function" ? canvas.getContext("2d") : null;
  if (!context || typeof window.requestAnimationFrame !== "function") return true;
  const side = Math.max(width, height);
  const puffs = emit(x, y, side);
  const began = performance.now();
  let last = began;
  const frame = (now: number) => {
    if (!canvas.isConnected) return;
    const t = (now - began) / 1000;
    step(puffs, Math.min(0.05, (now - last) / 1000), t, side);
    last = now;
    context.clearRect(0, 0, canvas.width, canvas.height);
    for (const p of puffs) {
      const { radius, alpha } = look(p, t, side);
      if (alpha <= 0.003) continue;
      const cx = p.x * scale, cy = p.y * scale, r = radius * scale;
      // A soft-edged blob: dense in the middle, gone at the rim. Many of them overlapping make the cloud.
      const blob = context.createRadialGradient(cx, cy, 0, cx, cy, r);
      blob.addColorStop(0, `rgba(2, 2, 6, ${alpha})`);
      blob.addColorStop(0.6, `rgba(2, 2, 6, ${alpha * 0.6})`);
      blob.addColorStop(1, "rgba(2, 2, 6, 0)");
      context.fillStyle = blob;
      context.fillRect(cx - r, cy - r, r * 2, r * 2);
    }
    window.requestAnimationFrame(frame);
  };
  window.requestAnimationFrame(frame);
  return true;
}
