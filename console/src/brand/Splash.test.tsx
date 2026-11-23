import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { NAME, Splash, TAGLINE } from "./Splash";
import { createOctopus, energyAt, FLAIL_SECONDS, SETTLE_SECONDS, SPINES } from "./octopus";
import symbol from "./octopus.svg?raw";

function motion(reduced: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduced && query.includes("reduce"), addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
}

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

test("it plays from the start every time its screen opens", () => {
  motion(false);
  const first = render(<Splash where="help" />);
  expect(Number(screen.getByTestId("splash").getAttribute("data-shown"))).toBeLessThan(2);
  expect(screen.getByAltText(NAME)).toBeTruthy();
  expect(screen.getByAltText(TAGLINE)).toBeTruthy();
  first.unmount();

  // Opened again in the same session: from the start again, never already settled.
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

test("the octopus on the page is the one that moves, through every redraw of the splash", async () => {
  // The splash redraws itself when the name and the tagline come in. A redraw once replaced the octopus's
  // drawing with a fresh copy, and the arms on the page never moved.
  motion(false);
  const queued: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (next: FrameRequestCallback) => queued.push(next));
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  let clock = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  const frame = (seconds: number) => {
    clock = 1000 + seconds * 1000;
    const next = queued.splice(0);
    act(() => next.forEach((run) => run(clock)));
  };

  render(<Splash where="sign-in" />);
  const drawing = screen.getByTestId("splash").querySelector("svg")!;
  const shape = () => screen.getByTestId("splash").querySelector("svg path")!.getAttribute("d");

  frame(0.5);
  const early = shape();
  frame(1.0);
  expect(shape()).not.toBe(early);

  // Past the moments the name and the tagline come in, each of which redraws the splash.
  frame(FLAIL_SECONDS + 0.5);
  frame(FLAIL_SECONDS + 1.5);
  expect(screen.getByTestId("splash").getAttribute("data-shown")).toBe("3");
  const late = shape();
  frame(FLAIL_SECONDS + 2.5);

  expect(screen.getByTestId("splash").querySelector("svg")).toBe(drawing);
  expect(shape()).not.toBe(late);
});

test("the spacebar turns bubbles behind the octopus on and off, and leaves a field's typing alone", () => {
  motion(false);
  render(<Splash where="help" />);
  const splash = screen.getByTestId("splash");
  expect(screen.queryByTestId("splash-bubbles")).toBeNull();

  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", key: " " }));
  });
  const bubbles = screen.getByTestId("splash-bubbles");
  expect(splash.dataset.bubbles).toBe("true");
  expect(bubbles.getAttribute("aria-hidden")).toBe("true");
  // Several, small, of varying sizes, each rising at its own pace.
  const sizes = [...bubbles.querySelectorAll<HTMLElement>(".splash-bubble")].map((b) => parseInt(b.style.width));
  expect(sizes.length).toBeGreaterThanOrEqual(8);
  expect(Math.max(...sizes)).toBeLessThanOrEqual(14);
  expect(new Set(sizes).size).toBeGreaterThan(4);

  // A space typed into a field is the field's.
  const field = document.createElement("input");
  document.body.appendChild(field);
  act(() => {
    field.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", key: " ", bubbles: true }));
  });
  expect(screen.getByTestId("splash-bubbles")).toBeTruthy();
  field.remove();

  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", key: " " }));
  });
  expect(screen.queryByTestId("splash-bubbles")).toBeNull();
});
