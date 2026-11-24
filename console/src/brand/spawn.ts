// The name's letters spawned by the octopus: each comes out from under its body, small, and swims out to its
// place in the word, writhing as the octopus's arms do, then settles into the exact shape of the wordmark.
//
// Each letter is one closed outline, a little tentacle of a shape. It is sampled into points along its edge, and
// each frame bends those points: the letter curls and uncurls from its base as an arm does, a ripple runs along
// its edge, and the whole of it swims from the spawn point to its own place, growing as it goes. The writhing
// dies down as it arrives, but never quite stops: like the octopus's own arms, the letters keep a faint wriggle,
// small enough that the word stays the wordmark to read.

// When each letter starts after the one before it, how long it takes to arrive, and how fast it writhes.
export const LETTER_GAP_SECONDS = 0.22;
export const LETTER_SECONDS = 2.6;
const WRITHE = 8.5;
// How much a letter still writhes once it has arrived: a faint wriggle, as the octopus's arms keep. And how slowly.
const IDLE = 0.06;
const IDLE_PACE = 0.32;
// How many points each letter's edge is sampled into.
const SAMPLES = 260;

type Point = [number, number];

interface Letter {
  path: SVGPathElement;
  rest: string;
  points: Point[];
  center: Point;
  base: number;
  height: number;
  phase: number;
}

/** How long the whole word takes to spawn: the last letter's start, and its swim. */
export function spawnSeconds(letters: number): number {
  return (letters - 1) * LETTER_GAP_SECONDS + LETTER_SECONDS;
}

const clamp = (x: number) => Math.min(1, Math.max(0, x));
const easeOut = (t: number) => 1 - (1 - t) ** 3;

/**
 * Gets the letters ready to spawn from `from`, a point in the word's own units (under the octopus's body).
 * Returns what draws them `seconds` after they start, and whether they have all arrived. Once they have, it
 * goes on drawing their faint wriggle for as long as it is called. Null where the browser
 * cannot measure a path (a test's): the letters then stay at rest.
 */
export function spawner(paths: SVGPathElement[], from: Point): { draw: (seconds: number) => boolean } | null {
  if (!paths.length || typeof paths[0].getTotalLength !== "function") return null;
  let letters: Letter[];
  try {
    letters = paths.map((path, i) => {
      const total = path.getTotalLength();
      const points: Point[] = [];
      for (let s = 0; s < SAMPLES; s++) {
        const at = path.getPointAtLength((total * s) / SAMPLES);
        points.push([at.x, at.y]);
      }
      const box = path.getBBox();
      return {
        path,
        rest: path.getAttribute("d") ?? "",
        points,
        center: [box.x + box.width / 2, box.y + box.height / 2],
        base: box.y + box.height,
        height: box.height,
        phase: i * 1.9,
      };
    });
  } catch {
    return null;
  }
  if (!letters.every((l) => l.height > 0)) return null;

  const draw = (seconds: number): boolean => {
    let done = true;
    letters.forEach((letter, i) => {
      const t = clamp((seconds - i * LETTER_GAP_SECONDS) / LETTER_SECONDS);
      if (t < 1) done = false;
      if (t <= 0) {
        letter.path.style.opacity = "0";
        return;
      }
      const go = easeOut(t);
      // How much it still writhes: all of it as it leaves the octopus, falling to a faint wriggle as it arrives,
      // which it keeps, slower.
      const writhe = IDLE + (1 - IDLE) * (1 - t) ** 1.5;
      const pace = t < 1 ? 1 : IDLE_PACE;
      const clock = seconds * WRITHE * pace + letter.phase;
      const h = letter.height;
      // Where its middle is now: from the spawn point to its place, swimming in a curve, wriggling side to side.
      const [cx, cy] = letter.center;
      const travel: Point = [from[0] + (cx - from[0]) * go, from[1] + (cy - from[1]) * go];
      const side = Math.sin(clock * 0.6) * h * 0.35 * (t < 1 ? writhe : 0);
      const dx = cx - from[0];
      const dy = cy - from[1];
      const run = Math.hypot(dx, dy) || 1;
      const now: Point = [travel[0] + (-dy / run) * side, travel[1] + (dx / run) * side];
      // Small as it is spawned, its full size as it arrives.
      const size = 0.12 + 0.88 * go;
      const d = letter.points
        .map(([px, py], k) => {
          // How far up the letter this point is, from its base: an arm bends most at its free end.
          const up = clamp((letter.base - py) / h);
          // The whole letter curls one way and the other from its base, as an arm does.
          const bend = writhe * 0.85 * Math.sin(clock + up * 2.4) * up ** 1.4;
          const ox = px - cx;
          const oy = py - letter.base;
          let x = cx + ox * Math.cos(bend) - oy * Math.sin(bend);
          let y = letter.base + ox * Math.sin(bend) + oy * Math.cos(bend);
          // A ripple runs along its edge, as along an arm's.
          const ripple = (t < 1 ? writhe : writhe * 0.4) * h * 0.05 * Math.sin((k / SAMPLES) * Math.PI * 14 - clock * 1.7);
          x += ripple;
          y += ripple * 0.6;
          // Swum to where it is now, at the size it is now.
          return `${(now[0] + (x - cx) * size).toFixed(1)} ${(now[1] + (y - cy) * size).toFixed(1)}`;
        })
        .join("L");
      letter.path.setAttribute("d", `M${d}Z`);
      letter.path.style.opacity = String(clamp(t * 8));
    });
    return done;
  };
  return { draw };
}
