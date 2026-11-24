// Draws the word "MUXXY AI" from tentacles, in the octopus's own colors, as SVG: `node scripts/tentacle-wordmark.mjs <out-dir>`.
//
// Each letter is a few strokes, and each stroke is drawn as one of the octopus's arms: thick where it starts,
// tapering to a thin tip that curls over, with a slight sway along its length. The whole word is then drawn in
// three layers, as the octopus is: a cyan rim, a near-black keyline inside it, and the purple body. Drawn as
// layers over the whole word, not letter by letter, so the strokes of one letter join without a seam.
//
// It writes one SVG for each variant in VARIANTS, and gallery.html, which shows them all on dark and on light,
// large and at the header's 53 pixels, beside the octopus, for choosing. The variants differ in how much the
// arms sway, how far their tips curl, and how thick they start. Deterministic: the same variant draws the same.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// The octopus's colors (console/brand/source/muxxy-symbol.svg).
const CYAN = "#08e7f5";
const DARK = "#0a0a14";
const PURPLE = "#670fdc";

// A letter's box, in the word's units: strokes are given in it, y down, the baseline at H.
const W = 100;
const H = 140;
const GAP = 18;
const SPACE = 46;

// Each letter: its width, and its strokes. A stroke is a list of points its centerline passes through,
// from its thick start to its thin, curling tip. curl: which way the tip turns (1 or -1), or 0 for none.
const LETTERS = {
  M: { w: 100, strokes: [
    { pts: [[14, 16], [11, 70], [9, 132]], curl: -1 },
    { pts: [[16, 14], [34, 46], [50, 84]], curl: 0 },
    { pts: [[84, 14], [66, 46], [50, 84]], curl: 0 },
    { pts: [[86, 16], [89, 70], [91, 132]], curl: 1 },
  ] },
  U: { w: 96, strokes: [
    { pts: [[14, 12], [12, 84], [26, 132], [62, 134], [82, 96], [84, 40], [82, 14]], curl: 1 },
  ] },
  X: { w: 96, strokes: [
    { pts: [[12, 14], [48, 72], [86, 132]], curl: 1 },
    { pts: [[84, 14], [48, 72], [10, 132]], curl: -1 },
  ] },
  Y: { w: 96, strokes: [
    { pts: [[50, 136], [49, 104], [48, 74]], curl: 0 },
    { pts: [[48, 78], [28, 48], [12, 14]], curl: -1 },
    { pts: [[48, 78], [68, 48], [84, 14]], curl: 1 },
  ] },
  A: { w: 96, strokes: [
    { pts: [[48, 10], [28, 70], [10, 132]], curl: -1 },
    { pts: [[48, 10], [68, 70], [86, 132]], curl: 1 },
    { pts: [[26, 92], [48, 90], [70, 92]], curl: 0, thin: true },
  ] },
  I: { w: 44, strokes: [
    { pts: [[22, 10], [21, 70], [22, 132]], curl: 1 },
  ] },
};

// The variants to choose between.
export const VARIANTS = [
  { id: "calm", label: "Calm: little sway, small curls", base: 15, tip: 3.2, sway: 2.5, curl: 0.9, curlRadius: 8 },
  { id: "curled", label: "Curled: small sway, full curls", base: 15, tip: 3, sway: 3, curl: 1.6, curlRadius: 10 },
  { id: "swaying", label: "Swaying: strong sway, full curls", base: 15, tip: 3, sway: 7, curl: 1.5, curlRadius: 10 },
  { id: "heavy", label: "Heavy: thick arms, small curls", base: 19, tip: 4.5, sway: 3.5, curl: 1.0, curlRadius: 9 },
  { id: "wild", label: "Wild: strong sway, tight spirals", base: 14, tip: 2.6, sway: 8, curl: 2.4, curlRadius: 11 },
];

const add = (a, b) => [a[0] + b[0], a[1] + b[1]];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
const mul = (a, k) => [a[0] * k, a[1] * k];
const len = (a) => Math.hypot(a[0], a[1]);
const unit = (a) => mul(a, 1 / (len(a) || 1));
const normal = (a) => [-a[1], a[0]];

/** Points along a smooth curve through the given points (Catmull-Rom), about every `step` units. */
function smooth(points, step = 1.5) {
  const pts = [points[0], ...points, points[points.length - 1]];
  const out = [];
  for (let i = 1; i < pts.length - 2; i++) {
    const [p0, p1, p2, p3] = [pts[i - 1], pts[i], pts[i + 1], pts[i + 2]];
    const n = Math.max(2, Math.ceil(len(sub(p2, p1)) / step));
    for (let j = 0; j < n; j++) {
      const t = j / n;
      const t2 = t * t;
      const t3 = t2 * t;
      out.push([0, 1].map((k) =>
        0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3),
      ));
    }
  }
  out.push(points[points.length - 1]);
  return out;
}

/** The centerline swayed sideways a little, as an arm is: a gentle S along its length, none at its two ends. */
function sway(points, amount, phase) {
  const total = points.reduce((s, p, i) => (i ? s + len(sub(p, points[i - 1])) : 0), 0);
  let run = 0;
  return points.map((p, i) => {
    if (i) run += len(sub(p, points[i - 1]));
    const t = run / (total || 1);
    const dir = unit(sub(points[Math.min(i + 1, points.length - 1)], points[Math.max(i - 1, 0)]));
    const off = amount * Math.sin(Math.PI * 2 * t + phase) * Math.sin(Math.PI * t);
    return add(p, mul(normal(dir), off));
  });
}

/** The tip curled over: the line goes on from its end, turning ever tighter, for `turns` half-turns. */
function curlTip(points, side, turns, radius) {
  if (!side || turns <= 0) return points;
  const out = [...points];
  let dir = unit(sub(points[points.length - 1], points[points.length - 4] ?? points[0]));
  let at = points[points.length - 1];
  const steps = Math.round(28 * turns);
  for (let i = 1; i <= steps; i++) {
    const r = radius * (1 - (0.75 * i) / steps);
    const turn = (Math.PI * turns) / steps;
    const angle = side * turn;
    dir = [dir[0] * Math.cos(angle) - dir[1] * Math.sin(angle), dir[0] * Math.sin(angle) + dir[1] * Math.cos(angle)];
    at = add(at, mul(dir, r * turn));
    out.push(at);
  }
  return out;
}

/** The outline of an arm along the centerline: `base` wide at its start, tapering to `tip`, rounded at both ends. */
function arm(points, base, tip) {
  const total = points.reduce((s, p, i) => (i ? s + len(sub(p, points[i - 1])) : 0), 0);
  let run = 0;
  const left = [];
  const right = [];
  points.forEach((p, i) => {
    if (i) run += len(sub(p, points[i - 1]));
    const t = run / (total || 1);
    // The width falls off faster near the tip, as an arm thins at its end.
    const width = tip + (base - tip) * Math.pow(1 - t, 1.35);
    const dir = unit(sub(points[Math.min(i + 1, points.length - 1)], points[Math.max(i - 1, 0)]));
    const n = mul(normal(dir), width / 2);
    left.push(add(p, n));
    right.push(sub(p, n));
  });
  // Each end rounded by a half circle of points around it, from one side to the other: drawn as points, so
  // it always bulges outward, whichever way the arm runs.
  const cap = (center, from, to, steps = 10) => {
    const a0 = Math.atan2(from[1] - center[1], from[0] - center[0]);
    let a1 = Math.atan2(to[1] - center[1], to[0] - center[0]);
    const r = len(sub(from, center));
    // The way round that passes the far side, away from the arm.
    const away = unit(sub(center, points[center === points[0] ? 1 : points.length - 2]));
    const mid = (x) => [center[0] + r * Math.cos(x), center[1] + r * Math.sin(x)];
    let span = a1 - a0;
    while (span <= -Math.PI) span += 2 * Math.PI;
    while (span > Math.PI) span -= 2 * Math.PI;
    const probe = sub(mid(a0 + span / 2), center);
    if (probe[0] * away[0] + probe[1] * away[1] < 0) span = span > 0 ? span - 2 * Math.PI : span + 2 * Math.PI;
    return Array.from({ length: steps - 1 }, (_, i) => mid(a0 + (span * (i + 1)) / steps));
  };
  const last = points.length - 1;
  const ring = [
    ...left,
    ...cap(points[last], left[last], right[last]),
    ...right.slice().reverse(),
    ...cap(points[0], right[0], left[0]),
  ];
  const f = (q) => `${q[0].toFixed(2)} ${q[1].toFixed(2)}`;
  return `M${ring.map(f).join("L")}Z`;
}

/** The word as SVG, for one variant: the arms' paths, drawn as rim, keyline and body. */
export function wordmark(variant, word = "MUXXY AI") {
  const paths = [];
  let x = 0;
  let index = 0;
  for (const char of word) {
    if (char === " ") {
      x += SPACE;
      continue;
    }
    const letter = LETTERS[char];
    for (const stroke of letter.strokes) {
      const placed = stroke.pts.map(([px, py]) => [x + px, py]);
      const line = smooth(placed);
      const swayed = stroke.thin ? line : sway(line, variant.sway, index * 1.7);
      const curled = curlTip(swayed, stroke.curl, variant.curl, variant.curlRadius);
      const base = stroke.thin ? variant.base * 0.55 : variant.base;
      paths.push(arm(curled, base, variant.tip));
      index += 1;
    }
    x += letter.w + GAP;
  }
  const width = x - GAP;
  const pad = variant.base + 14;
  const d = paths.join("");
  // Rim and keyline as strokes around the same shapes, under the body: wide cyan, then dark inside it.
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Muxxy AI" viewBox="${-pad} ${-pad} ${width + 2 * pad} ${H + 2 * pad}">` +
    `<title>Muxxy AI</title>` +
    `<g stroke-linejoin="round" stroke-linecap="round">` +
    `<path d="${d}" fill="${CYAN}" stroke="${CYAN}" stroke-width="13"/>` +
    `<path d="${d}" fill="${DARK}" stroke="${DARK}" stroke-width="8"/>` +
    `<path d="${d}" fill="${PURPLE}"/>` +
    `</g></svg>\n`
  );
}

async function main() {
  const out = process.argv[2];
  if (!out) throw new Error("say where to write: node scripts/tentacle-wordmark.mjs <out-dir>");
  await mkdir(out, { recursive: true });
  const here = dirname(fileURLToPath(import.meta.url));
  const octopus = await readFile(join(here, "..", "src", "brand", "muxxy-symbol.svg"), "utf8");
  const octopusInline = octopus.replace(/^[\s\S]*?(<svg)/, "$1").replace("<svg", '<svg style="height:100%;width:auto"');
  const cards = [];
  for (const variant of VARIANTS) {
    const svg = wordmark(variant);
    await writeFile(join(out, `muxxy-wordmark-${variant.id}.svg`), svg);
    const inline = (h) => svg.replace("<svg", `<svg style="height:${h}px;width:auto"`);
    cards.push(`
      <section>
        <h2>${variant.label} <small>muxxy-wordmark-${variant.id}.svg</small></h2>
        <div class="row dark"><span class="oct">${octopusInline}</span>${inline(120)}</div>
        <div class="row light"><span class="oct">${octopusInline}</span>${inline(120)}</div>
        <div class="row dark small"><span class="oct s">${octopusInline}</span>${inline(30)}<em>header size</em></div>
        <div class="row light small"><span class="oct s">${octopusInline}</span>${inline(30)}<em>header size</em></div>
      </section>`);
  }
  await writeFile(
    join(out, "gallery.html"),
    `<!doctype html><html><head><meta charset="utf-8"><title>Muxxy tentacle wordmarks</title><style>
      body{margin:0;font:14px/1.4 system-ui,sans-serif;background:#f4f4f6;color:#222}
      header{padding:20px 28px}h1{margin:0;font-size:20px}section{padding:8px 28px 24px}
      h2{font-size:15px;margin:10px 0}h2 small{font-weight:400;color:#777;margin-left:8px}
      .row{display:flex;align-items:center;gap:18px;padding:14px 20px;border-radius:10px;margin:6px 0}
      .dark{background:#242424;color:#aaa}.light{background:#fff;border:1px solid #ddd;color:#777}
      .oct{display:inline-flex;height:150px}.oct.s{height:53px}.small{gap:10px}em{font-size:12px;margin-left:auto}
    </style></head><body><header><h1>Muxxy: tentacle wordmarks</h1>
    <p>Each name is drawn from the octopus's arms: thick at the start, tapering and curling at the tip, in its purple with its dark keyline and cyan rim. "AI" is the same purple.</p></header>
    ${cards.join("")}</body></html>\n`,
  );
  console.log(`wrote ${VARIANTS.length} wordmarks and gallery.html to ${out}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    console.error(`tentacle wordmark failed: ${error.message}`);
    process.exit(1);
  });
}
