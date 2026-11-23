// Created by the meta-harness in run t-a05e83246886:run-1 on harness:pi-careful/claude:claude-opus-5-5.

// Builds the console's brand artwork from console/brand/source: `npm run brand`.
//
// It writes src/brand/octofusion-symbol.svg, the symbol optimized for the web, and
// src/brand/octofusion-lockup.svg, that symbol with the magenta wordmark to its right on one canvas.
// The outputs are committed. The build is deterministic, so a second run changes no file.
//
// It also writes the browser icons and the web app manifest into public/, each showing the whole octopus:
// favicon.svg, favicon.ico (16, 32 and 48 pixels), favicon-32.png, apple-touch-icon.png, icon-192.png,
// icon-512.png, icon-maskable-512.png and site.webmanifest. resvg's WebAssembly build turns SVG into
// pixels, so the bitmaps come out byte for byte the same on every machine.
//
// Path precision 2 and the size limits come from docs/specs/octofusion-brand-sizes.md. Artwork is
// never redrawn or recolored: the optimizer only rewrites numbers and merges same-color paths.
// octofusion-full-lockup.svg is not used, since it fills the name with the octopus's purple.

import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { initWasm, Resvg } from "@resvg/resvg-wasm";
import { optimize } from "svgo";

const consoleDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const sourceDir = join(consoleDir, "brand", "source");
const outDir = join(consoleDir, "src", "brand");
const publicDir = join(consoleDir, "public");

const TITLE = "Octofusion AI";
const DESC = "Vector brand artwork for Octofusion AI, the Open Meta-Harness for Coding at Scale.";
const PRECISION = 2;
// 40 KB and 120 KB read as 40,000 and 120,000 bytes, as the size note does.
const SYMBOL_LIMIT = 40_000;
const LOCKUP_LIMIT = 120_000;
const BRAND_FILLS = ["#670fdc", "#08e7f5", "#0a0a14", "#fb5b52", "#c402f5"];
// Space between the symbol and the wordmark, in the symbol's units.
const GAP = 120;
// The near-black behind the icons that cannot be transparent, and the browser's theme color.
const BACKDROP = "#0a0a14";
const THEME = "#670fdc";

/**
 * The bitmaps in public/. `margin` is the share of each side left empty around the octopus, and
 * `backdrop` fills the square behind it when the icon cannot be transparent. The maskable icon keeps the
 * octopus inside the middle 80 percent, the part every launcher's mask leaves visible.
 */
const BITMAPS = [
  { name: "favicon-32.png", size: 32, margin: 0 },
  { name: "apple-touch-icon.png", size: 180, margin: 0.1, backdrop: BACKDROP },
  { name: "icon-192.png", size: 192, margin: 0 },
  { name: "icon-512.png", size: 512, margin: 0 },
  { name: "icon-maskable-512.png", size: 512, margin: 0.1, backdrop: BACKDROP },
];
const ICO_SIZES = [16, 32, 48];

/** The manifest's icons: the ones a browser installs the console with. */
const MANIFEST_ICONS = [
  { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
  { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
  { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
];

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

/** Writes a binary file only when its bytes changed. */
async function writeBytesIfChanged(path, bytes) {
  const old = await readFile(path).catch(() => null);
  if (old && old.equals(bytes)) return "unchanged";
  await writeFile(path, bytes);
  return "written";
}

/** Loads resvg's WebAssembly module from node_modules. */
async function startRenderer() {
  const require = createRequire(import.meta.url);
  await initWasm(await readFile(require.resolve("@resvg/resvg-wasm/index_bg.wasm")));
}

/** Rounds to two places, the precision of the optimized artwork. */
const round = (n) => Math.round(n * 100) / 100;

/**
 * The square, in the symbol's units, that holds the octopus with its arms. The symbol's viewBox leaves
 * empty space around the drawing; the icons drop it so the octopus fills a 16-pixel tab icon.
 */
function octopusSquare(symbol) {
  const renderer = new Resvg(symbol, { font: { loadSystemFonts: false } });
  const box = renderer.getBBox();
  renderer.free();
  if (!box) throw new Error("the optimized symbol draws nothing");
  const side = Math.ceil(Math.max(box.width, box.height));
  const x = Math.floor(box.x + box.width / 2 - side / 2);
  const y = Math.floor(box.y + box.height / 2 - side / 2);
  return { x, y, side: side + 1 };
}

/** The symbol's root element with a new viewBox, the drawing and its title left as they are. */
function reframe(symbol, square) {
  const box = `viewBox="${square.x} ${square.y} ${square.side} ${square.side}"`;
  const framed = symbol.replace(/\bviewBox="[^"]*"/, box);
  if (framed === symbol) throw new Error("the optimized symbol has no viewBox to reframe");
  return framed;
}

/** An SVG of the octopus at `size` pixels, `margin` of each side empty, on `backdrop` when given. */
function iconSvg(symbol, square, { size, margin, backdrop }) {
  const view = square.side / (1 - 2 * margin);
  const x = round(square.x + square.side / 2 - view / 2);
  const y = round(square.y + square.side / 2 - view / 2);
  const v = round(view);
  const fill = backdrop ? `<rect x="${x}" y="${y}" width="${v}" height="${v}" fill="${backdrop}"/>` : "";
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" shape-rendering="geometricPrecision"` +
    ` width="${size}" height="${size}" viewBox="${x} ${y} ${v} ${v}">${fill}${drawing(symbol)}</svg>`
  );
}

/** Renders an SVG to PNG bytes at its own width and height. */
function renderPng(svg) {
  const renderer = new Resvg(svg, { font: { loadSystemFonts: false }, fitTo: { mode: "original" } });
  const image = renderer.render();
  const png = Buffer.from(image.asPng());
  image.free();
  renderer.free();
  return png;
}

/**
 * An ICO file holding one PNG per size. Every browser and Windows since Vista read PNG entries.
 * Layout: a 6-byte header, a 16-byte directory entry per image, then the images in order.
 */
function ico(images) {
  const header = Buffer.alloc(6 + 16 * images.length);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach(({ size, png }, i) => {
    const at = 6 + 16 * i;
    header.writeUInt8(size >= 256 ? 0 : size, at);
    header.writeUInt8(size >= 256 ? 0 : size, at + 1);
    header.writeUInt8(0, at + 2);
    header.writeUInt8(0, at + 3);
    header.writeUInt16LE(1, at + 4);
    header.writeUInt16LE(32, at + 6);
    header.writeUInt32LE(png.length, at + 8);
    header.writeUInt32LE(offset, at + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...images.map(({ png }) => png)]);
}

/** The web app manifest's text. */
function manifest() {
  const body = {
    name: TITLE,
    short_name: TITLE,
    icons: MANIFEST_ICONS,
    background_color: BACKDROP,
    theme_color: THEME,
  };
  return `${JSON.stringify(body, null, 2)}\n`;
}

/** Writes the browser icons and the manifest into public/. */
async function writePublic(symbol) {
  await startRenderer();
  const square = octopusSquare(symbol);

  const faviconPath = join(publicDir, "favicon.svg");
  const oldFavicon = await readFile(faviconPath, "utf8").catch(() => null);
  const favicon = leadingComments(oldFavicon) + reframe(symbol, square);
  const faviconBytes = check("favicon.svg", favicon, SYMBOL_LIMIT);
  const faviconState = await writeIfChanged(faviconPath, favicon, oldFavicon);
  console.log(`${relative(consoleDir, faviconPath)}: ${faviconBytes} bytes, ${faviconState}`);

  const binaries = BITMAPS.map((bitmap) => [bitmap.name, renderPng(iconSvg(symbol, square, bitmap))]);
  const entries = ICO_SIZES.map((size) => ({ size, png: renderPng(iconSvg(symbol, square, { size, margin: 0 })) }));
  binaries.push(["favicon.ico", ico(entries)]);
  for (const [name, bytes] of binaries) {
    const path = join(publicDir, name);
    const state = await writeBytesIfChanged(path, bytes);
    console.log(`${relative(consoleDir, path)}: ${bytes.length} bytes, ${state}`);
  }

  const manifestPath = join(publicDir, "site.webmanifest");
  const oldManifest = await readFile(manifestPath, "utf8").catch(() => null);
  const manifestState = await writeIfChanged(manifestPath, manifest(), oldManifest);
  console.log(`${relative(consoleDir, manifestPath)}: ${manifestState}`);
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
  await writePublic(symbol);
}

main().catch((error) => {
  console.error(`brand build failed: ${error.message}`);
  process.exit(1);
});
