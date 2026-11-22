// The octopus, alive. Every point of every path of the symbol is moved by one field of motion that follows
// each arm's centerline, so the three color layers stay in register and nothing is cut apart. An arm bends
// along its length: a wave of curl travels from where it joins the body out to its tip.

// Each arm's centerline in the symbol's own units (1418 square), from the body out to the tip.
type Point = [number, number];
const LEFT: Point[][] = [
  [[600, 715], [540, 720], [430, 725], [320, 705], [245, 650], [230, 585], [275, 535], [360, 520], [445, 555]],
  [[628, 810], [486, 841], [324, 820], [203, 851], [158, 922], [192, 1003], [247, 1037]],
  [[648, 891], [506, 972], [365, 993], [294, 1074], [324, 1175], [425, 1221]],
  [[699, 952], [608, 1013], [527, 1094], [537, 1195], [628, 1256], [705, 1215]],
];
const CENTER = 730;
export const SPINES: Point[][] = [...LEFT, ...LEFT.map((line) => line.map(([x, y]): Point => [2 * CENTER - x, y]))];

const SAMPLES = 48;
// How far from its centerline an arm reaches, at the body and at the tip, and how soon the pull fades past that.
const REACH_ROOT = 105;
const REACH_TIP = 46;
const FADE = 26;

// A centerline resampled to even steps, with each step's direction.
interface Arm {
  pts: Point[];
  step: number[];
  angle: number[];
  length: number;
}

function resample(line: Point[]): Arm {
  const lengths = [0];
  for (let i = 1; i < line.length; i++) lengths.push(lengths[i - 1] + Math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]));
  const total = lengths[lengths.length - 1];
  // A smooth curve through the points (Catmull-Rom), so the arm has no corners to bend at.
  const at = (u: number): Point => {
    const d = u * total;
    let k = 1;
    while (k < line.length - 1 && lengths[k] < d) k++;
    const t = (d - lengths[k - 1]) / (lengths[k] - lengths[k - 1] || 1);
    const p0 = line[Math.max(0, k - 2)], p1 = line[k - 1], p2 = line[k], p3 = line[Math.min(line.length - 1, k + 1)];
    const c = (a: number, b: number, c2: number, d2: number) => 0.5 * (2 * b + (-a + c2) * t + (2 * a - 5 * b + 4 * c2 - d2) * t * t + (-a + 3 * b - 3 * c2 + d2) * t * t * t);
    return [c(p0[0], p1[0], p2[0], p3[0]), c(p0[1], p1[1], p2[1], p3[1])];
  };
  const pts: Point[] = [];
  for (let i = 0; i <= SAMPLES; i++) pts.push(at(i / SAMPLES));
  const step: number[] = [], angle: number[] = [];
  let length = 0;
  for (let i = 0; i < SAMPLES; i++) {
    const dx = pts[i + 1][0] - pts[i][0], dy = pts[i + 1][1] - pts[i][1];
    step.push(Math.hypot(dx, dy));
    angle.push(Math.atan2(dy, dx));
    length += step[i];
  }
  return { pts, step, angle, length };
}

// The symbol's paths as points. Its path data is absolute M, C and Z only, each path moved by a translate.
interface Shape {
  path: SVGPathElement;
  ops: [string, number][];
  xs: Float32Array;
  ys: Float32Array;
  arm: Int8Array;
  at: Float32Array;
  share: Float32Array;
  ox: Float32Array;
  oy: Float32Array;
  pull: Float32Array;
}

function parse(svg: SVGSVGElement): Shape[] {
  const shapes: Shape[] = [];
  for (const path of Array.from(svg.querySelectorAll("path"))) {
    const move = /translate\(([-\d.]+)[ ,]+([-\d.]+)\)/.exec(path.getAttribute("transform") || "");
    const dx = move ? Number(move[1]) : 0, dy = move ? Number(move[2]) : 0;
    const tokens = (path.getAttribute("d") ?? "").match(/[MCZ]|-?\d*\.?\d+(?:e-?\d+)?/gi) ?? [];
    const ops: [string, number][] = [], xs: number[] = [], ys: number[] = [];
    let op = "";
    for (let i = 0; i < tokens.length; ) {
      if (/[MCZ]/i.test(tokens[i])) {
        op = tokens[i++].toUpperCase();
        if (op === "Z") ops.push(["Z", 0]);
        continue;
      }
      const count = op === "C" ? 3 : 1;
      ops.push([op, count]);
      for (let n = 0; n < count; n++) {
        xs.push(Number(tokens[i++]) + dx);
        ys.push(Number(tokens[i++]) + dy);
      }
      if (op === "M") op = "L";
    }
    path.removeAttribute("transform");
    const none = new Float32Array(0);
    shapes.push({ path, ops, xs: Float32Array.from(xs), ys: Float32Array.from(ys), arm: new Int8Array(0), at: none, share: none, ox: none, oy: none, pull: none });
  }
  return shapes;
}

const smooth = (a: number, b: number, x: number): number => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

// The head and body, which hold still: the arms' motion fades to nothing toward it, so a curling tip that
// passes close to the head does not drag the head's outline along.
const BODY: Point[] = [
  [733, 115], [1018, 408], [992, 572], [922, 632], [905, 720], [862, 800], [800, 900], [733, 965],
  [666, 900], [604, 800], [561, 720], [544, 632], [474, 572], [448, 408],
];
const BODY_FADE = 80;

// How far a point is outside the body's outline; 0 inside it.
function outsideBody(x: number, y: number): number {
  let inside = false, nearest = Infinity;
  for (let i = 0, j = BODY.length - 1; i < BODY.length; j = i++) {
    const [x1, y1] = BODY[i], [x2, y2] = BODY[j];
    if (y1 > y !== y2 > y && x < ((x2 - x1) * (y - y1)) / (y2 - y1) + x1) inside = !inside;
    const dx = x2 - x1, dy = y2 - y1;
    const t = Math.min(1, Math.max(0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)));
    nearest = Math.min(nearest, Math.hypot(x - x1 - t * dx, y - y1 - t * dy));
  }
  return inside ? 0 : nearest;
}

// For each point: the two arms nearest it, where along each, how much of each it follows, and its place
// relative to each arm there. A point in the gap between two arms follows both in part, so an outline that
// runs from one arm to the next bends smoothly and never tears.
const NEAR = 2;
function bind(shapes: Shape[], arms: Arm[]): void {
  for (const shape of shapes) {
    const n = shape.xs.length;
    shape.arm = new Int8Array(n * NEAR).fill(-1);
    shape.at = new Float32Array(n * NEAR);
    shape.share = new Float32Array(n * NEAR);
    shape.ox = new Float32Array(n * NEAR);
    shape.oy = new Float32Array(n * NEAR);
    shape.pull = new Float32Array(n);
    for (let p = 0; p < n; p++) {
      const x = shape.xs[p], y = shape.ys[p];
      const found: { a: number; at: number; score: number; pull: number; share: number }[] = [];
      for (let a = 0; a < arms.length; a++) {
        const { pts } = arms[a];
        let best = Infinity, at = 0, dist = 0;
        for (let i = 0; i <= SAMPLES; i++) {
          const d = Math.hypot(x - pts[i][0], y - pts[i][1]);
          const reach = REACH_ROOT + (REACH_TIP - REACH_ROOT) * (i / SAMPLES);
          // Judged against the arm's own width there, so a thin tip does not claim what is near it.
          if (d / reach < best) {
            best = d / reach;
            at = i;
            dist = d;
          }
        }
        const reach = REACH_ROOT + (REACH_TIP - REACH_ROOT) * (at / SAMPLES);
        found.push({ a, at, score: best, pull: 1 - smooth(reach, reach + FADE, dist), share: 0 });
      }
      found.sort((one, other) => one.score - other.score);
      let total = 0;
      for (let k = 0; k < NEAR; k++) {
        const f = found[k];
        f.share = Math.exp(-3 * f.score * f.score) * f.pull;
        total += f.share;
      }
      shape.pull[p] = Math.max(found[0].pull, found[1].pull * (found[1].share / (total || 1))) * smooth(0, BODY_FADE, outsideBody(x, y));
      for (let k = 0; k < NEAR; k++) {
        const f = found[k], pts = arms[f.a].pts;
        shape.arm[p * NEAR + k] = f.a;
        shape.at[p * NEAR + k] = f.at;
        shape.share[p * NEAR + k] = total ? f.share / total : k === 0 ? 1 : 0;
        shape.ox[p * NEAR + k] = x - pts[f.at][0];
        shape.oy[p * NEAR + k] = y - pts[f.at][1];
      }
    }
  }
}

// How far each arm's direction turns at each step along it, at time t (seconds). energy: 1 while it flails,
// falling to 0 for the slow sway.
function turns(index: number, t: number, energy: number, out: Float32Array): void {
  const seed = index * 2.399963; // each arm out of step with the others
  const side = index < 4 ? 1 : -1;
  // The two upper arms curl back toward the head, so they move less than the rest.
  const upper = index % 4 === 0 ? 0.6 : 1;
  const wide = (0.07 + 0.47 * energy) * upper;
  const quick = 0.9 + 5.2 * energy;
  for (let i = 0; i < SAMPLES; i++) {
    const u = i / SAMPLES;
    // Nothing at the body, more toward the tip: the tip trails the base.
    const grow = Math.pow(u, 1.25);
    const wave =
      Math.sin(quick * t - 4.4 * u + seed) +
      0.42 * Math.sin(quick * 1.73 * t - 7.1 * u + seed * 1.7) +
      0.25 * Math.sin(quick * 0.41 * t + seed * 0.6);
    out[i] = side * wide * grow * wave;
  }
}

export interface Octopus {
  // Draw the octopus at time t (seconds) with the given energy; an energy below 0 draws the symbol as it is.
  draw: (t: number, energy: number) => void;
  points: number;
}

export function createOctopus(svg: SVGSVGElement): Octopus {
  const arms = SPINES.map(resample);
  const shapes = parse(svg);
  bind(shapes, arms);
  const turn = new Float32Array(SAMPLES + 1);
  const qx = arms.map(() => new Float32Array(SAMPLES + 1));
  const qy = arms.map(() => new Float32Array(SAMPLES + 1));
  const rot = arms.map(() => new Float32Array(SAMPLES + 1));

  // Draw the octopus at time t with the given energy. energy < 0 draws the symbol as it is.
  function draw(t: number, energy: number): void {
    const still = energy < 0;
    for (let a = 0; a < arms.length; a++) {
      const arm = arms[a];
      if (still) turn.fill(0);
      else turns(a, t, energy, turn);
      qx[a][0] = arm.pts[0][0];
      qy[a][0] = arm.pts[0][1];
      for (let i = 0; i < SAMPLES; i++) {
        rot[a][i] = turn[i];
        const angle = arm.angle[i] + turn[i];
        qx[a][i + 1] = qx[a][i] + arm.step[i] * Math.cos(angle);
        qy[a][i + 1] = qy[a][i] + arm.step[i] * Math.sin(angle);
      }
      rot[a][SAMPLES] = turn[SAMPLES - 1];
    }
    for (const shape of shapes) {
      const { xs, ys, ops } = shape;
      let d = "", p = 0;
      for (const [op, count] of ops) {
        if (op === "Z") {
          d += "Z";
          continue;
        }
        d += op;
        for (let n = 0; n < count; n++, p++) {
          let x = xs[p], y = ys[p];
          const pull = shape.pull[p];
          if (pull > 0 && !still) {
            let nx = 0, ny = 0;
            for (let k = 0; k < NEAR; k++) {
              const share = shape.share[p * NEAR + k];
              if (!share) continue;
              const a = shape.arm[p * NEAR + k], i = shape.at[p * NEAR + k];
              const r = rot[a][i], cos = Math.cos(r), sin = Math.sin(r);
              const ox = shape.ox[p * NEAR + k], oy = shape.oy[p * NEAR + k];
              nx += share * (qx[a][i] + ox * cos - oy * sin);
              ny += share * (qy[a][i] + ox * sin + oy * cos);
            }
            x += (nx - x) * pull;
            y += (ny - y) * pull;
          }
          d += (n ? " " : "") + x.toFixed(1) + " " + y.toFixed(1);
        }
      }
      shape.path.setAttribute("d", d);
    }
  }
  return { draw, points: shapes.reduce((n, s) => n + s.xs.length, 0) };
}

// The whole splash on a timeline: the arms flail, settle, and keep a slow sway.
export const FLAIL_SECONDS = 2.8;
export const SETTLE_SECONDS = 1.6;

export function energyAt(seconds: number): number {
  if (seconds < FLAIL_SECONDS) return 1;
  const u = Math.min(1, (seconds - FLAIL_SECONDS) / SETTLE_SECONDS);
  return 1 - u * u * (3 - 2 * u);
}
