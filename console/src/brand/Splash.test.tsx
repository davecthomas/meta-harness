import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { NAME, Splash, TAGLINE } from "./Splash";
import { createOctopus, energyAt, FLAIL_SECONDS, SETTLE_SECONDS, SPINES } from "./octopus";
import symbol from "./octopus.svg?raw";

function motion(reduced: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduced && query.includes("reduce"), addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
}

beforeEach(() => window.sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

function drawn(): SVGSVGElement {
  const host = document.createElement("div");
  host.innerHTML = symbol;
  return host.querySelector("svg") as SVGSVGElement;
}

const numbers = (svg: SVGSVGElement) =>
  Array.from(svg.querySelectorAll("path")).flatMap((p) => (p.getAttribute("d") ?? "").match(/-?\d+(?:\.\d+)?/g)!.map(Number));

test("with no motion the octopus is the symbol, point for point", () => {
  const before = drawn();
  // The artwork's paths are each moved into place by a translate, which drawing folds into the points.
  const expected = Array.from(before.querySelectorAll("path")).flatMap((p) => {
    const move = /translate\(([-\d.]+)[ ,]+([-\d.]+)\)/.exec(p.getAttribute("transform") ?? "");
    const [dx, dy] = move ? [Number(move[1]), Number(move[2])] : [0, 0];
    return (p.getAttribute("d") ?? "").match(/-?\d+(?:\.\d+)?/g)!.map((n, i) => Number(n) + (i % 2 ? dy : dx));
  });
  const svg = drawn();
  createOctopus(svg).draw(0, -1);

  const got = numbers(svg);
  expect(got.length).toBe(expected.length);
  expect(Math.max(...got.map((n, i) => Math.abs(n - expected[i])))).toBeLessThan(0.11);
});

test("the arms move and the head holds still", () => {
  const still = drawn(), moving = drawn();
  createOctopus(still).draw(0, -1);
  createOctopus(moving).draw(1.3, 1);
  const a = numbers(still), b = numbers(moving);

  let most = 0, headMost = 0;
  for (let i = 0; i < a.length; i += 2) {
    const moved = Math.hypot(b[i] - a[i], b[i + 1] - a[i + 1]);
    most = Math.max(most, moved);
    // The top of the head: well above where any arm starts.
    if (a[i + 1] < 420) headMost = Math.max(headMost, moved);
  }
  expect(most).toBeGreaterThan(40);
  expect(headMost).toBeLessThan(0.11);
  expect(SPINES).toHaveLength(8);
});

test("the flail gives way to the slow sway", () => {
  expect(energyAt(0)).toBe(1);
  expect(energyAt(FLAIL_SECONDS - 0.01)).toBe(1);
  expect(energyAt(FLAIL_SECONDS + SETTLE_SECONDS / 2)).toBeCloseTo(0.5, 5);
  expect(energyAt(FLAIL_SECONDS + SETTLE_SECONDS + 5)).toBe(0);
});

test("a first visit starts with nothing shown, and the name and tagline are named for a screen reader", () => {
  motion(false);
  render(<Splash where="sign-in" />);
  const splash = screen.getByTestId("splash");
  expect(Number(splash.getAttribute("data-shown"))).toBeLessThan(2);
  expect(screen.getByAltText(NAME)).toBeTruthy();
  expect(screen.getByAltText(TAGLINE)).toBeTruthy();
  expect(window.sessionStorage.getItem("splash:sign-in")).toBe("played");
});

test("a later visit in the session starts settled, on each screen by itself", () => {
  motion(false);
  window.sessionStorage.setItem("splash:sign-in", "played");
  const first = render(<Splash where="sign-in" />);
  expect(screen.getByTestId("splash").getAttribute("data-shown")).toBe("3");
  first.unmount();

  render(<Splash where="help" />);
  expect(Number(screen.getByTestId("splash").getAttribute("data-shown"))).toBeLessThan(2);
});

test("with reduced motion nothing moves: the symbol, the name and the tagline show at once", () => {
  motion(true);
  const frames = vi.fn();
  vi.stubGlobal("requestAnimationFrame", frames);
  render(<Splash where="sign-in" />);
  const splash = screen.getByTestId("splash");
  expect(splash.getAttribute("data-shown")).toBe("3");
  expect(splash.hasAttribute("data-still")).toBe(true);
  expect(frames).not.toHaveBeenCalled();
});
