import { expect, test } from "vitest";
import { countChanges, getIn, isChanged, joinCandidate, lineDiff, reasonTarget, setIn, splitCandidate } from "./staging";

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

test("a value is set at a path without touching the original, and undefined removes it", () => {
  const doc = { tiers: [{ name: "a", models: ["x"] }], profiles: { p: { image: "i", tools: ["read"] } } };
  const next = setIn(doc, ["tiers", 0, "models"], ["x", "y"]);
  expect(getIn(next, ["tiers", 0, "models"])).toEqual(["x", "y"]);
  expect(doc.tiers[0].models).toEqual(["x"]);
  expect(setIn(doc, ["profiles", "p", "image"], undefined).profiles.p).toEqual({ tools: ["read"] });
  expect(isChanged(doc, next, ["tiers", 0, "models"])).toBe(true);
  expect(isChanged(doc, next, ["profiles"])).toBe(false);
});

test("changes are counted by value: a list of strings is one, and tiers are compared one by one", () => {
  const before = { tiers: [{ name: "a", description: "d", models: ["x"] }], default_tier: "a" };
  expect(countChanges(before, before)).toBe(0);
  const after = { tiers: [{ name: "a", description: "e", models: ["x", "y", "z"] }], default_tier: "a", on_exhausted: "fail" };
  expect(countChanges(before, after)).toBe(3);
});

test("a refused save's reasons go to the tier, profile, repo or field they name", () => {
  const tiers = { tiers: [{ name: "standard" }, { name: "standard-plus" }] };
  expect(reasonTarget("tiers", tiers, "tier standard: a reason about the whole tier")).toBe("tier:standard");
  expect(reasonTarget("tiers", tiers, "tier standard has no candidates")).toBe("tier:standard:candidates");
  expect(reasonTarget("tiers", tiers, "tier standard-plus: thinking huge is not one the HTTP Gateway accepts")).toBe("tier:standard-plus:thinking");
  expect(reasonTarget("tiers", tiers, "default_tier is required: the router needs a tier")).toBe("default_tier");
  expect(reasonTarget("tiers", tiers, "the router does not parse: bad")).toBeNull();
  const named = { tiers: [{ name: "standard", models: ["harness:pi-lean/claude:opus", "harness:pi-gone/openai:gpt"] }] };
  expect(reasonTarget("tiers", named, "tier standard: harness:pi-gone/openai:gpt names harness profile pi-gone, which the profiles lack")).toBe("tier:standard:candidate:1:profile");
  expect(reasonTarget("tiers", named, "tier standard: harness:pi-gone/openai:gpt names engine openai, which sends ...")).toBe("tier:standard:candidate:1:model");
  expect(reasonTarget("tiers", named, "tier standard: harness:pi-lean/claude:opus is not harness:<profile>/<engine>:<model>")).toBe("tier:standard:candidate:0");
  expect(reasonTarget("profiles", { profiles: { "pi-lean": {} } }, "pi-lean: max_turns must be a positive number")).toBe("profile:pi-lean:max_turns");
  expect(reasonTarget("profiles", { profiles: { "pi-lean": {} } }, "pi-lean: resources.memory_gb must be a positive number")).toBe("profile:pi-lean:resources.memory_gb");
  expect(reasonTarget("profiles", { profiles: { "pi-lean": {} } }, "pi-lean: unknown tools teleport")).toBe("profile:pi-lean:tools");
  expect(reasonTarget("profiles", { profiles: { "pi-lean": {} } }, "pi-lean: a harness profile is an object")).toBe("profile:pi-lean");
  expect(reasonTarget("profiles", { profiles: { "pi-lean": {} } }, "tier standard still names harness profile pi-gone")).toBeNull();
  expect(reasonTarget("repos", { repos: { "o/a": {} } }, "o/a: default_checks must be a list of commands")).toBe("repo:o/a:default_checks");
  expect(reasonTarget("repos", { repos: { "o/a": {} } }, "o/a: tracker must be one of github")).toBe("repo:o/a:tracker");
  expect(reasonTarget("repos", { repos: { "o/a": {} } }, "o/a: a repo's entry is an object")).toBe("repo:o/a");
  expect(reasonTarget("task_policy", {}, "max_parallel 12 is more than the 8 sandboxes this deployment's VM holds")).toBe("max_parallel");
  expect(reasonTarget("task_policy", {}, "reviewer x names harness profile y, which the profiles lack")).toBe("reviewers");
});
