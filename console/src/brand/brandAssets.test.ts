// Created by the meta-harness in run t-a05e83246886:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { describe, expect, test } from "vitest";
import lockup from "./muxxy-lockup.svg?raw";
import symbol from "./muxxy-symbol.svg?raw";
import tagline from "./tagline.svg?raw";
import wordmark from "./wordmark.svg?raw";

// The files `npm run brand` writes. The limits are docs/specs/octofusion-brand-sizes.md's, in bytes.
const FILES = [
  { name: "muxxy-symbol.svg", text: symbol, limit: 40_000 },
  { name: "muxxy-lockup.svg", text: lockup, limit: 120_000 },
];
const BRAND_FILLS = ["#670fdc", "#08e7f5", "#0a0a14", "#fb5b52", "#c402f5"];

function parse(text: string): SVGSVGElement {
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  return doc.documentElement as unknown as SVGSVGElement;
}

describe.each(FILES)("$name", ({ text, limit }) => {
  test("is one svg whose title reads Muxxy", () => {
    const svg = parse(text);
    expect(svg.tagName).toBe("svg");
    expect(svg.getAttribute("role")).toBe("img");
    const title = svg.querySelector(":scope > title");
    expect(title?.textContent).toBe("Muxxy");
    // The accessible name is the title, by id or by aria-label.
    const labelledBy = svg.getAttribute("aria-labelledby");
    if (labelledBy) expect(labelledBy.split(" ")).toContain(title?.id);
    else expect(svg.getAttribute("aria-label")).toBe("Muxxy");
  });

  test("is under its size limit before compression", () => {
    expect(new TextEncoder().encode(text).length).toBeLessThan(limit);
  });

  test("fills only with the brand's colors", () => {
    const svg = parse(text);
    const fills = Array.from(svg.querySelectorAll("[fill]")).map((el) => el.getAttribute("fill")!.toLowerCase());
    expect(fills.length).toBeGreaterThan(0);
    for (const fill of fills) expect(BRAND_FILLS).toContain(fill);
    expect(text).not.toMatch(/fill\s*:/);
  });
});

test("the lockup holds the symbol and the Quiet Tentacle wordmark: the name in the octopus's purple alone", () => {
  const svg = parse(lockup);
  expect(svg.querySelector("#muxxy-symbol-purple")?.getAttribute("fill")).toBe("#670fdc");
  const name = svg.querySelector("#muxxy-lockup-wordmark")!;
  const fills = new Set(Array.from(name.querySelectorAll("[fill]")).map((el) => el.getAttribute("fill")!.toLowerCase()));
  expect([...fills]).toEqual(["#670fdc"]);
  // No outline and no separate "AI": the name is Muxxy.
  expect(name.querySelector("[stroke]")).toBeNull();
});

test("the splash's name and tagline are built from the same artwork, each on a canvas it fills", () => {
  const name = parse(wordmark);
  expect(name.getAttribute("aria-label")).toBe("Muxxy");
  // One path for each of its five letters, so the splash can move them one by one, all in the octopus's purple.
  const letters = Array.from(name.querySelectorAll("path"));
  expect(letters.length).toBe(5);
  for (const letter of letters) expect(letter.getAttribute("fill")).toBe("#670fdc");
  const said = parse(tagline);
  expect(said.getAttribute("aria-label")).toBe("The Open Meta-Harness for Coding at Scale");
  const [, , width, height] = said.getAttribute("viewBox")!.split(" ").map(Number);
  expect(width / height).toBeGreaterThan(10);
});

// The source artwork leaves a tenth of its canvas empty on each side and draws the name at under a third of
// the octopus's height. Shipped that way, a header-sized logo was an octopus of 25 pixels beside letters of 7.
test("the shipped artwork fills its canvas, and the lockup's name is most of the octopus's height", () => {
  const box = (text: string) => parse(text).getAttribute("viewBox")!.split(" ").map(Number);
  const [x, y, width, height] = box(symbol);
  expect(x).toBeGreaterThan(100);
  expect(y).toBeGreaterThan(100);
  expect(Math.max(width, height)).toBeLessThan(1250);
  const [, , lockupWidth, lockupHeight] = box(lockup);
  expect(lockupHeight).toBeLessThan(1200);
  // Wider than tall however short the name: the name sits beside the octopus, not under it. How much wider
  // depends on the name's length (Octofusion's lockup was over four times as wide as tall, Muxxy's about three).
  expect(lockupWidth / lockupHeight).toBeGreaterThan(2.5);
  // The name is drawn at a share of the octopus's height (WORDMARK_SHARE in build-brand.mjs): scaled up from its
  // source, not shrunk, or a header-sized logo puts tiny letters beside the octopus.
  const scale = /scale\(([\d.]+)\)/.exec(parse(lockup).querySelector("#muxxy-lockup-wordmark")!.getAttribute("transform")!);
  expect(Number(scale![1])).toBeGreaterThan(1);
});

// A stray closing brace once made the browser drop the rule after it, which placed the splash's name: the name
// then sat wherever it fell, with nothing in the tests to say so.
const sheets = import.meta.glob("/src/**/*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
test("every stylesheet's braces balance", () => {
  expect(Object.keys(sheets).length).toBeGreaterThan(0);
  for (const [sheet, css] of Object.entries(sheets)) {
    let depth = 0;
    for (const char of css.replace(/\/\*[\s\S]*?\*\//g, "")) {
      if (char === "{") depth += 1;
      if (char === "}") depth -= 1;
      expect(depth, `${sheet} closes a brace it never opened`).toBeGreaterThanOrEqual(0);
    }
    expect(depth, `${sheet} leaves a brace open`).toBe(0);
  }
});
