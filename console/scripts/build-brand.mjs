// Created by the meta-harness in run t-a05e83246886:run-1 on harness:pi-careful/claude:claude-opus-5-5.

// Builds the console's brand artwork from console/brand/source: `npm run brand`.
//
// It writes src/brand/octofusion-symbol.svg, the symbol optimized for the web, and
// src/brand/octofusion-lockup.svg, that symbol with the magenta wordmark to its right on one canvas.
// The outputs are committed. The build is deterministic, so a second run changes no file.
//
// Path precision 2 and the size limits come from docs/specs/octofusion-brand-sizes.md. Artwork is
// never redrawn or recolored: the optimizer only rewrites numbers and merges same-color paths.
// octofusion-full-lockup.svg is not used, since it fills the name with the octopus's purple.

import { readFile, writeFile } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { optimize } from "svgo";

const consoleDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const sourceDir = join(consoleDir, "brand", "source");
const outDir = join(consoleDir, "src", "brand");

const TITLE = "Octofusion AI";
const DESC = "Vector brand artwork for Octofusion AI, the Open Meta-Harness for Coding at Scale.";
const PRECISION = 2;
// 40 KB and 120 KB read as 40,000 and 120,000 bytes, as the size note does.
const SYMBOL_LIMIT = 40_000;
const LOCKUP_LIMIT = 120_000;
const BRAND_FILLS = ["#670fdc", "#08e7f5", "#0a0a14", "#fb5b52", "#c402f5"];
// Space between the symbol and the wordmark, in the symbol's units.
const GAP = 120;

/** svgo's preset-default at the measured precision, keeping the title, the description and the ids. */
function svgoConfig() {
  return {
    floatPrecision: PRECISION,
    js2svg: { eol: "lf", finalNewline: true },
    plugins: [
      {
        name: "preset-default",
        params: { overrides: { removeDesc: false, cleanupIds: false } },
      },
      {
        // svgo drops role="img" from the root; it carries the accessible name.
        name: "keepRoleImg",
        fn: () => ({
          element: {
            enter(node, parent) {
              if (node.name === "svg" && parent.type === "root") node.attributes.role = "img";
            },
          },
        }),
      },
    ],
  };
}

/** Optimizes one source file and returns its text. */
async function optimizeSource(name) {
  const text = await readFile(join(sourceDir, name), "utf8");
  return optimize(text, { path: name, ...svgoConfig() }).data;
}

/** The viewBox's width and height of an optimized SVG. */
function viewBoxSize(svg) {
  const match = /<svg\b[^>]*\bviewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(svg);
  if (!match) throw new Error("no viewBox at the origin in an optimized brand file");
  return { width: Number(match[1]), height: Number(match[2]) };
}

/** The drawing inside an optimized SVG: everything between the root tags, minus title and desc. */
function drawing(svg) {
  const match = /^<svg\b[^>]*>([\s\S]*)<\/svg>\s*$/.exec(svg);
  if (!match) throw new Error("an optimized brand file is not one root svg element");
  return match[1].replace(/<title\b[^>]*>[\s\S]*?<\/title>/, "").replace(/<desc\b[^>]*>[\s\S]*?<\/desc>/, "");
}

/**
 * The symbol with the wordmark to its right, the wordmark at its own scale and centered on the symbol.
 * Its title is a plain `<title>`, with no id, and the root names itself with aria-label.
 */
function lockup(symbol, wordmark) {
  const s = viewBoxSize(symbol);
  const w = viewBoxSize(wordmark);
  const width = s.width + GAP + w.width;
  const height = Math.max(s.height, w.height);
  const symbolY = (height - s.height) / 2;
  const wordmarkY = (height - w.height) / 2;
  const at = (x, y) => (x || y ? ` transform="translate(${x} ${y})"` : "");
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${TITLE}"` +
    ` shape-rendering="geometricPrecision" viewBox="0 0 ${width} ${height}">` +
    `<title>${TITLE}</title>` +
    `<desc>${DESC}</desc>` +
    `<g id="octofusion-lockup-symbol"${at(0, symbolY)}>${drawing(symbol)}</g>` +
    `<g id="octofusion-lockup-wordmark"${at(s.width + GAP, wordmarkY)}>${drawing(wordmark)}</g>` +
    `</svg>\n`
  );
}

/** Refuses a file that lost its title, passed its limit or holds a fill outside the brand's colors. */
function check(name, svg, limit) {
  const bytes = Buffer.byteLength(svg, "utf8");
  if (bytes >= limit) throw new Error(`${name} is ${bytes} bytes, over its limit of ${limit}`);
  if (!svg.includes(`>${TITLE}</title>`)) throw new Error(`${name} lost its title "${TITLE}"`);
  const fills = [...svg.matchAll(/\bfill[=:]"?\s*([^";\s>]+)/g)].map((m) => m[1].toLowerCase());
  const stray = fills.filter((fill) => !BRAND_FILLS.includes(fill));
  if (stray.length) throw new Error(`${name} has fills outside the brand's colors: ${[...new Set(stray)]}`);
  return bytes;
}

/**
 * The comments ahead of the root element of a committed output, such as the provenance header the
 * meta-harness adds to a file a run created. The optimizer drops comments, so the build keeps these, or a
 * second run, or `npm run brand` in the checks, would remove them and change a committed file.
 */
function leadingComments(old) {
  return /^(?:\s*<!--[\s\S]*?-->)+\s*/.exec(old ?? "")?.[0] ?? "";
}

/** Writes a file only when its text changed, so a second run touches nothing. */
async function writeIfChanged(path, text, old) {
  if (old === text) return "unchanged";
  await writeFile(path, text);
  return "written";
}

async function main() {
  const symbol = await optimizeSource("octofusion-symbol.svg");
  const wordmark = await optimizeSource("octofusion-wordmark.svg");
  const outputs = [
    ["octofusion-symbol.svg", symbol, SYMBOL_LIMIT],
    ["octofusion-lockup.svg", lockup(symbol, wordmark), LOCKUP_LIMIT],
  ];
  for (const [name, built, max] of outputs) {
    const path = join(outDir, name);
    const old = await readFile(path, "utf8").catch(() => null);
    const svg = leadingComments(old) + built;
    const bytes = check(name, svg, max);
    const state = await writeIfChanged(path, svg, old);
    console.log(`${relative(consoleDir, path)}: ${bytes} bytes, ${state}`);
  }
}

main().catch((error) => {
  console.error(`brand build failed: ${error.message}`);
  process.exit(1);
});
