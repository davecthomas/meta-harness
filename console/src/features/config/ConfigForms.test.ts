import { expect, test } from "vitest";
import { joinCandidate, lineDiff, reasonsFor, splitCandidate } from "./ConfigForms";

test("a candidate splits into its profile and model, and joins back", () => {
  expect(splitCandidate("harness:pi-lean/openai-responses:gpt-6-sol")).toEqual({ profile: "pi-lean", model: "openai-responses:gpt-6-sol" });
  expect(joinCandidate("pi-lean", "claude:claude-sonnet-5")).toBe("harness:pi-lean/claude:claude-sonnet-5");
});

test("the diff marks removed and added lines and keeps the rest", () => {
  expect(lineDiff("a\nb\nc", "a\nB\nc")).toEqual([
    { sign: " ", text: "a" },
    { sign: "-", text: "b" },
    { sign: "+", text: "B" },
    { sign: " ", text: "c" },
  ]);
});

test("reasons go to the tier or profile they name", () => {
  const reasons = ["tier standard has no candidates", "pi-lean: max_turns must be positive", "default_tier is required"];
  expect(reasonsFor(reasons, "tier standard")).toEqual(["tier standard has no candidates"]);
  expect(reasonsFor(reasons, "pi-lean:")).toEqual(["pi-lean: max_turns must be positive"]);
});
