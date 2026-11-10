import { expect, test } from "vitest";
import { parallelLabel } from "./parallel";

test("a goal's parallel limit reads as its own, or the system's it inherits", () => {
  expect(parallelLabel(2, 8)).toBe("2, set for this goal");
  expect(parallelLabel(null, 8)).toBe("8, the system setting");
  expect(parallelLabel(undefined, null)).toBe("The system setting");
});
