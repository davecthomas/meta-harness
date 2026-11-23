// Created by the meta-harness in run t-a05e83246886:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { describe, expect, test } from "vitest";
import lockup from "./octofusion-lockup.svg?raw";
import symbol from "./octofusion-symbol.svg?raw";

// The files `npm run brand` writes. The limits are docs/specs/octofusion-brand-sizes.md's, in bytes.
const FILES = [
  { name: "octofusion-symbol.svg", text: symbol, limit: 40_000 },
  { name: "octofusion-lockup.svg", text: lockup, limit: 120_000 },
];
const BRAND_FILLS = ["#670fdc", "#08e7f5", "#0a0a14", "#fb5b52", "#c402f5"];

function parse(text: string): SVGSVGElement {
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  return doc.documentElement as unknown as SVGSVGElement;
}

describe.each(FILES)("$name", ({ text, limit }) => {
  test("is one svg whose title reads Octofusion AI", () => {
    const svg = parse(text);
    expect(svg.tagName).toBe("svg");
    expect(svg.getAttribute("role")).toBe("img");
    const title = svg.querySelector(":scope > title");
    expect(title?.textContent).toBe("Octofusion AI");
    // The accessible name is the title, by id or by aria-label.
    const labelledBy = svg.getAttribute("aria-labelledby");
    if (labelledBy) expect(labelledBy.split(" ")).toContain(title?.id);
    else expect(svg.getAttribute("aria-label")).toBe("Octofusion AI");
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

test("the lockup holds the symbol and the magenta wordmark", () => {
  const svg = parse(lockup);
  expect(svg.querySelector("#octofusion-symbol-purple")?.getAttribute("fill")).toBe("#670fdc");
  expect(svg.querySelector("#octofusion-wordmark-purple")?.getAttribute("fill")).toBe("#c402f5");
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
  // The name is drawn larger than its source, so the lockup is over four times as wide as it is tall.
  expect(lockupWidth / lockupHeight).toBeGreaterThan(4);
  const scale = /scale\(([\d.]+)\)/.exec(parse(lockup).querySelector("#octofusion-lockup-wordmark")!.getAttribute("transform")!);
  expect(Number(scale![1])).toBeGreaterThan(1.5);
});
