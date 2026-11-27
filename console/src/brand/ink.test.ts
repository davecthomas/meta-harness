import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { CLEAR_SECONDS, EMIT_SECONDS, emit, ink, INK_SECONDS, INKS, LIFE, look, onBox, PUFFS, seeded, siphon, step } from "./ink";

// The test page has no canvas to draw on: the cloud still comes and goes, undrawn.
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  document.body.className = "";
});

const BOX = { left: 100, top: 100, width: 200, height: 200 };

test("a click is on the octopus only inside its box, and the ink comes from under its middle", () => {
  expect(onBox(BOX, 150, 150)).toBe(true);
  expect(onBox(BOX, 99, 150)).toBe(false);
  expect(onBox(BOX, 150, 301)).toBe(false);
  expect(siphon(BOX)).toEqual({ x: 200, y: 220 });
});

test("the cloud is let out over a moment, from one place, each puff its own", () => {
  const puffs = emit(500, 400, 1000, seeded(7));
  expect(puffs).toHaveLength(PUFFS);
  expect(puffs.every((p) => p.born >= 0 && p.born <= EMIT_SECONDS && p.life >= LIFE[0] && p.life <= LIFE[1])).toBe(true);
  expect(puffs.every((p) => Math.abs(p.x - 500) <= 10 && Math.abs(p.y - 400) <= 10)).toBe(true);
  expect(new Set(puffs.map((p) => p.phase)).size).toBeGreaterThan(PUFFS * 0.9);
  // The same seed makes the same cloud; another makes another.
  expect(emit(500, 400, 1000, seeded(7))).toEqual(puffs);
  expect(emit(500, 400, 1000, seeded(8))).not.toEqual(puffs);
});

function spread(puffs: { x: number; y: number }[]): { width: number; meanX: number } {
  const xs = puffs.map((p) => p.x);
  return { width: Math.max(...xs) - Math.min(...xs), meanX: xs.reduce((a, b) => a + b, 0) / xs.length };
}

test("the cloud spreads fast at first and then slowly, and the current carries it to and fro", () => {
  const puffs = emit(500, 400, 1000, seeded(3));
  const widths: number[] = [], means: number[] = [];
  for (let t = 0, i = 0; t < 9; t += 1 / 30, i++) {
    step(puffs, 1 / 30, t, 1000);
    if (i % 30 === 29) {
      widths.push(spread(puffs).width);
      means.push(spread(puffs).meanX);
    }
  }
  // Drag: most of the spreading is done in the first seconds.
  expect(widths[2] - widths[0]).toBeGreaterThan(widths[8] - widths[6]);
  expect(widths[8]).toBeGreaterThan(300);
  // The current: the whole cloud moves one way and then back.
  const moves = means.slice(1).map((m, i) => Math.sign(m - means[i]));
  expect(moves).toContain(1);
  expect(moves).toContain(-1);
});

test("a puff grows as it ages, more slowly as it goes, and is gone at the end of its life", () => {
  const [p] = emit(0, 0, 1000, seeded(1));
  const at = (age: number) => look(p, p.born + age, 1000);
  expect(look(p, p.born - 0.1, 1000).alpha).toBe(0);
  expect(at(1).radius).toBeGreaterThan(at(0.25).radius);
  expect(at(1).radius - at(0.25).radius).toBeGreaterThan(at(4).radius - at(3.25).radius);
  expect(at(1).alpha).toBeGreaterThan(0);
  expect(at(p.life * 0.9).alpha).toBeLessThan(at(1).alpha);
  expect(at(p.life).alpha).toBe(0);
});

test("one cloud is out at a time, the name is lifted over it, and it is gone when it has dissolved", () => {
  vi.useFakeTimers();
  expect(ink(120, 340)).toBe(true);
  const cloud = document.body.querySelector(".splash-ink") as HTMLElement;
  expect(cloud.tagName).toBe("CANVAS");
  expect(cloud.getAttribute("aria-hidden")).toBe("true");
  expect(document.body.classList.contains("splash-inking")).toBe(true);
  expect(ink(10, 10)).toBe(false);
  vi.advanceTimersByTime(INK_SECONDS * 1000);
  expect(document.body.querySelector(".splash-ink")).toBeNull();
  expect(document.body.classList.contains("splash-inking")).toBe(false);
});

test("with reduced motion on, it does not ink", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  expect(ink(1, 1)).toBe(false);
  expect(document.body.querySelector(".splash-ink")).toBeNull();
});

test("the cloud mixes three inks, each billow one of them, so it reads as ink in water", () => {
  const puffs = emit(500, 400, 1000, seeded(11));
  const inks = new Set(puffs.map((p) => p.ink));
  expect(inks).toEqual(new Set([0, 1, 2]));
  // Each ink is a real share of the cloud, not a fleck.
  for (const i of [0, 1, 2]) expect(puffs.filter((p) => p.ink === i).length).toBeGreaterThan(PUFFS * 0.08);
  // All three are near black, and two of them lean: purple and brown.
  expect(INKS.every(([r, g, b]) => Math.max(r, g, b) < 45)).toBe(true);
  const [purple, brown] = INKS;
  expect(purple[2] > purple[1] && purple[0] > purple[1]).toBe(true);
  expect(brown[0] > brown[1] && brown[1] > brown[2]).toBe(true);
});

test("a click anywhere while the cloud is out clears it, but the click that let it out does not", () => {
  vi.useFakeTimers();
  expect(ink(120, 340)).toBe(true);
  window.dispatchEvent(new Event("pointerdown"));
  expect(document.querySelector(".splash-ink")).not.toBeNull();

  vi.advanceTimersByTime(0);
  expect(document.querySelector(".splash-ink")).not.toBeNull();
  document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  expect((document.querySelector(".splash-ink") as HTMLElement).style.opacity).toBe("0");
  vi.advanceTimersByTime(CLEAR_SECONDS * 1000);
  expect(document.querySelector(".splash-ink")).toBeNull();
  expect(document.body.classList.contains("splash-inking")).toBe(false);
  expect(ink(10, 10)).toBe(true);
});
