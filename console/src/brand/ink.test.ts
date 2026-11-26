import { afterEach, expect, test, vi } from "vitest";
import { ink, INK_MS, onBox, PUFFS } from "./ink";

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
  document.body.className = "";
});

test("a click is on the octopus only inside its box, with no margin round it", () => {
  const box = { left: 100, top: 100, width: 200, height: 200 };
  expect(onBox(box, 150, 150)).toBe(true);
  expect(onBox(box, 300, 300)).toBe(true);
  expect(onBox(box, 99, 150)).toBe(false);
  expect(onBox(box, 150, 301)).toBe(false);
});

test("the ink spreads from the click over the screen, one cloud at a time, and is gone when it fades", () => {
  vi.useFakeTimers();
  expect(ink(120, 340)).toBe(true);
  const cloud = document.body.querySelector(".splash-ink") as HTMLElement;
  expect(cloud.getAttribute("aria-hidden")).toBe("true");
  const puffs = [...cloud.querySelectorAll<HTMLElement>(".splash-ink-puff")];
  expect(puffs).toHaveLength(PUFFS.length);
  expect(puffs.every((p) => p.style.left === "120px" && p.style.top === "340px")).toBe(true);
  // The name is lifted over the ink while it is out.
  expect(document.body.classList.contains("splash-inking")).toBe(true);
  expect(ink(10, 10)).toBe(false);
  expect(document.body.querySelectorAll(".splash-ink")).toHaveLength(1);
  vi.advanceTimersByTime(INK_MS);
  expect(document.body.querySelector(".splash-ink")).toBeNull();
  expect(document.body.classList.contains("splash-inking")).toBe(false);
  expect(ink(10, 10)).toBe(true);
});
