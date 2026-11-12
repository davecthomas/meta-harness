import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { candidateName, rows, span, steps, totals } from "./path";

let n = 0;
let clock = Date.parse("2026-11-11T18:00:00Z");
// An event `after` seconds later than the one before it.
const ev = (event_type: string, payload: Record<string, unknown> = {}, after = 0, more: Partial<MhEvent> = {}): MhEvent => {
  clock += after * 1000;
  return { position: ++n, event_id: String(n), event_type, time_utc: new Date(clock).toISOString(), source: "", actor: null, run_id: null, task_id: "t", payload, ...more };
};
const run = (id: number, kind: string, tier: string, seconds: number, spent: string, decision = "finished", detail = ""): MhEvent[] => [
  ev("run_prompt", { kind, run_id: `t:run-${id}` }, 0, { run_id: `t:run-${id}` }),
  ev("run_finished", { turns: 10, usage: { usd: Number(spent) } }, seconds, { run_id: `t:run-${id}` }),
  ev("attempt_finished", { attempt: id, tier, candidate: "harness:pi-careful/claude:claude-opus-5-5", decision, detail, spent_usd: spent }, 1, { run_id: `t:run-${id}` }),
];
const START = () => [ev("task_created", { status: "queued" }), ev("task_admitted", {}, 3), ev("task_status_changed", { from: "queued", to: "running" })];
const OPENED = () => [ev("pull_request_opened", { pr_url: "https://github.com/o/r/pull/257", files: ["a", "b"] }, 5), ev("pull_request_checks", { state: "passed", failed: [] }, 240)];
const REVIEWED = (round: number, verdict: string, findings: number) => ev("pull_request_reviewed", { round, verdict, findings }, 2);

test("a task that merges on its first review is one row, from its wait to its merge", () => {
  const path = steps([
    ...START(), ...run(1, "task", "complex", 600, "3.50"), ...OPENED(), ...run(2, "review", "review", 50, "0.19"), REVIEWED(1, "clean", 0),
    ev("pull_request_merged", { pr_url: "https://github.com/o/r/pull/257", merge_sha: "44aad0dae9431d12" }, 5),
    ev("merge_checks", { state: "passed", failed: [] }, 400),
    ev("task_status_changed", { from: "running", to: "closed", reason: "merged" }),
  ]);
  expect(path.map((s) => s.title)).toEqual([
    "Queued", "Task run", "Pull request #257 opened", "Checks passed", "Review round 1", "Merged", "Merge commit's checks passed", "Closed",
  ]);
  expect(rows(path)).toHaveLength(1);
  expect(path[0].end).not.toBeNull();
  expect(path[1]).toMatchObject({ usd: 3.5, turns: 10, tone: "ok", candidate: "harness:pi-careful/claude:claude-opus-5-5" });
  expect(path[4]).toMatchObject({ verdict: "clean", tone: "ok", detail: "Clean", runId: "t:run-2" });
  // A closed task is nowhere now.
  expect(path.some((s) => s.now)).toBe(false);
  expect(totals(path)).toMatchObject({ runs: 2, rounds: 1, usd: 3.69 });
});

test("each review that asks for changes sends the task back to a new row, with why", () => {
  const path = steps([
    ...START(), ...run(1, "task", "complex", 600, "3.50"), ...OPENED(), ...run(2, "review", "review", 50, "0.19"), REVIEWED(1, "changes", 3),
    ...run(3, "fix", "fix", 300, "1.12"), ev("pull_request_checks", { state: "passed", failed: [] }, 240), ...run(4, "review", "review", 50, "0.20"), REVIEWED(2, "changes", 1),
    ...run(5, "fix", "fix", 300, "1.00"),
  ]);
  const all = rows(path);
  expect(all.map((r) => r.steps.map((s) => s.title))).toEqual([
    ["Queued", "Task run", "Pull request #257 opened", "Checks passed", "Review round 1"],
    ["Fix run", "Checks passed", "Review round 2"],
    ["Fix run"],
  ]);
  expect(all.map((r) => r.back)).toEqual(["", "3 findings to fix", "1 finding to fix"]);
  expect(path.at(-1)!.now).toBe(true);
});

test("a hold for budget is a wait with its reason, how long it lasted, and what the operator answered", () => {
  const path = steps([
    ...START(), ...run(1, "task", "complex", 600, "3.50"), ...OPENED(), ...run(2, "review", "review", 50, "0.19"), REVIEWED(1, "changes", 2),
    ...run(3, "fix", "fix", 250, "1.96", "final", "ended ERROR (spend_limit)"),
    ev("budget_hold", { reason: "fix run 4 needs about $1.96" }, 1),
    ev("budget_decision", { action: "raise", max_usd: "15" }, 3600, { actor: "pat@example.com" }),
    ...run(4, "fix", "fix", 600, "2.39"),
  ]);
  const hold = path.find((s) => s.title === "Held for budget")!;
  expect(hold).toMatchObject({ kind: "wait", column: 0, detail: "fix run 4 needs about $1.96", notes: ["pat@example.com raised the spend limit to $15.00"] });
  expect(Date.parse(hold.end!) - Date.parse(hold.start)).toBe(3600_000);
  expect(path.find((s) => s.runId === "t:run-3")).toMatchObject({ tone: "bad", detail: "ended ERROR (spend_limit)" });
  // The failed fix run, the hold and the next fix run: the hold starts a row of its own.
  expect(rows(path).map((r) => r.steps[0].title)).toEqual(["Queued", "Fix run", "Held for budget"]);
});

test("a task the supervisor sends back for its merge shows where it was parked, for how long, and the return", () => {
  const path = steps([
    ...START(), ...run(1, "task", "complex", 600, "3.50"), ...OPENED(), ...run(2, "review", "review", 50, "0.19"), REVIEWED(1, "clean", 0),
    ev("task_status_changed", { from: "running", to: "ready", reason: "GitHub would not merge it" }, 5),
    ev("task_standing", { owner: "operator", ask: "Merge the pull request.", reason: "", situation: "ready" }, 60),
    ev("task_status_changed", { from: "ready", to: "queued", reason: "the supervisor asked for its merge (try 1 of 3)" }, 7200, { actor: "supervisor" }),
    ev("task_driven", { action: "merge", why: "its pull request conflicts with its base", drives: 1 }, 0, { actor: "supervisor" }),
    ev("task_status_changed", { from: "queued", to: "running" }, 3),
    ...run(3, "fix", "fix", 200, "0.47"),
    ev("task_status_changed", { from: "running", to: "review_unresolved", reason: "fix round 3 changed nothing" }, 1),
  ]);
  const parked = path.find((s) => s.title === "Ready to merge")!;
  expect(parked).toMatchObject({ kind: "parked", column: 4, notes: ["Needs you: Merge the pull request."] });
  expect(Date.parse(parked.end!) - Date.parse(parked.start)).toBe(7260_000);
  const all = rows(path);
  expect(all[1].steps.map((s) => s.title)).toEqual(["Sent back for its merge by the supervisor", "Fix run", "Review unresolved"]);
  expect(all[1].steps[0].detail).toBe("its pull request conflicts with its base");
  expect(all[1].back).toBe("Sent back for its merge by the supervisor");
  expect(path.at(-1)).toMatchObject({ title: "Review unresolved", now: true, end: null });
});

test("a task held by the scheduler and failed on its first run jumps from its run to its end", () => {
  const path = steps([
    ev("task_created", { status: "queued" }),
    ev("task_held", { reason: "repo area docs is held by t-1" }, 2),
    ev("task_held", { reason: "repo area docs is held by t-2" }, 300),
    ev("task_admitted", {}, 100),
    ev("task_status_changed", { from: "queued", to: "running" }),
    ev("run_prompt", { kind: "task" }, 1, { run_id: "t:run-1" }),
    ev("attempt_finished", { attempt: 1, tier: "complex", candidate: "harness:pi-careful/claude:claude-opus-5-5", decision: "final", detail: "run_harness failed", spent_usd: "0" }, 9, { run_id: "t:run-1" }),
    ev("task_status_changed", { from: "running", to: "failed", reason: "TaskFailed: run 1 ended for good" }),
  ]);
  expect(path.map((s) => [s.title, s.column, s.tone])).toEqual([["Held by the scheduler", 0, "plain"], ["Task run", 1, "bad"], ["Failed", 4, "bad"]]);
  expect(path[0].detail).toBe("repo area docs is held by t-2");
  expect(Date.parse(path[0].end!) - Date.parse(path[0].start)).toBe(402_000);
  expect(rows(path)).toHaveLength(1);
});

test("a run recorded before prompts were is known by its tier, and an operator's words sit on the step they were said in", () => {
  const path = steps([
    ...START(),
    ev("operator_message", { text: "keep the old name", when: "next_turn" }, 30, { actor: "pat@example.com" }),
    ev("attempt_finished", { attempt: 1, tier: "complex", candidate: "c", decision: "next_candidate", detail: "ended ERROR", spent_usd: "1" }, 60, { run_id: "t:run-1" }),
    ev("attempt_finished", { attempt: 2, tier: "complex", candidate: "d", decision: "finished", spent_usd: "2" }, 60, { run_id: "t:run-2" }),
    ev("attempt_finished", { attempt: 3, tier: "review", candidate: "e", decision: "finished", spent_usd: "0.2" }, 60, { run_id: "t:run-3" }),
  ]);
  expect(path.map((s) => [s.title, s.column])).toEqual([["Queued", 0], ["Task run", 1], ["Task run", 1], ["Review", 3]]);
  expect(path[0].notes).toEqual(["pat@example.com: keep the old name"]);
  expect(path[1].detail).toBe("ended ERROR. The next candidate takes it");
  // Two candidates' runs share the Run stage of one row.
  expect(rows(path)).toHaveLength(1);
});

test("lengths of time and candidates read short", () => {
  expect([4_000, 372_000, 3_720_000, 183_600_000].map(span)).toEqual(["4s", "6m 12s", "1h 2m", "2d 3h"]);
  expect(candidateName("harness:pi-careful/claude:claude-opus-5-5")).toBe("pi-careful · claude-opus-5-5");
  expect(candidateName("someone")).toBe("someone");
});
