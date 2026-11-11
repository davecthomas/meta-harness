import { expect, test } from "vitest";
import { moneyText, usd } from "./money";

test("money reads as dollars and cents, alone and inside a sentence", () => {
  expect(usd("30.0000")).toBe("$30.00");
  expect(usd(0.8271)).toBe("$0.83");
  expect(usd(null)).toBe("$0.00");
  expect(
    moneyText("run 2 needs about $3.1140076, and goal g-7bd41fcd41c3 has spent $31.3457854 of its $30.0000 budget, which leaves $0.8271 for each of its 2 running tasks"),
  ).toBe("run 2 needs about $3.11, and goal g-7bd41fcd41c3 has spent $31.35 of its $30.00 budget, which leaves $0.83 for each of its 2 running tasks");
  expect(moneyText("costs $5 a run, or $1.50")).toBe("costs $5.00 a run, or $1.50");
  expect(moneyText(null)).toBeNull();
});
