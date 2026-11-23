import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { STEP_EVENTS, candidateName, rows, span, steps, totals } from "./path";

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
    // The fix run is done and pushed: the code host is checking it.
    ["Fix run", "Waiting for the pull request's checks"],
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
  expect(all[1].steps.map((s) => s.title)).toEqual(["Sent back for its merge by the Task Manager", "Fix run", "Review unresolved"]);
  expect(all[1].steps[0].detail).toBe("its pull request conflicts with its base");
  expect(all[1].back).toBe("Sent back for its merge by the Task Manager");
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
  expect(path.map((s) => [s.title, s.column, s.tone])).toEqual([["Waiting its turn", 0, "plain"], ["Task run", 1, "bad"], ["Failed", 4, "bad"]]);
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

test("a task waiting for the code host's checks says so, after its pull request opens and after each fix run", () => {
  const opened = [
    ev("task_created", { status: "queued" }),
    ev("task_admitted", {}, 5),
    ...run(1, "task", "standard", 60, "0.05"),
    ev("pull_request_opened", { pr_url: "https://github.com/o/r/pull/9", files: ["a"] }, 4),
  ];
  const waiting = steps(opened).at(-1)!;
  expect([waiting.title, waiting.kind, waiting.end, waiting.now]).toEqual(["Waiting for the pull request's checks", "checks", null, true]);
  expect(waiting.url).toBe("https://github.com/o/r/pull/9/checks");
  // A first run that has just finished has no pull request yet: it is being published, not checked.
  expect(steps(opened.slice(0, -1)).at(-1)!.kind).toBe("run");

  const reviewed = [
    ...opened,
    ev("pull_request_checks", { state: "passed", failed: [] }, 300),
    ...run(2, "review", "review", 30, "0.1"),
    ev("pull_request_reviewed", { round: 1, verdict: "changes", findings: 1 }, 1),
  ];
  expect(steps(reviewed).at(-1)!.kind).toBe("review");
  const fixed = [...reviewed, ...run(3, "fix", "fix", 90, "0.1")];
  expect(steps(fixed).at(-1)!.title).toBe("Waiting for the pull request's checks");
  expect(steps([...fixed, ev("pull_request_checks", { state: "passed", failed: [] }, 300)]).at(-1)!.title).toBe("Checks passed");

  const merged = [...fixed, ev("pull_request_merged", { pr_url: "https://github.com/o/r/pull/9", merge_sha: "58e544902fca" }, 400)];
  expect(steps(merged).at(-1)!.title).toBe("Waiting for the merge commit's checks");
  const closed = [...merged, ev("task_status_changed", { from: "running", to: "closed", reason: "merged" }, 300)];
  expect(steps(closed).at(-1)!.kind).toBe("end");
});

test("the time a task waits for the code host's checks counts as waiting, and is said apart", () => {
  const path = steps([
    ev("task_created", { status: "queued" }),
    ev("task_admitted", {}, 5),
    ...run(1, "task", "standard", 60, "0.05"),
    ev("pull_request_opened", { pr_url: "https://github.com/o/r/pull/9", files: ["a"] }, 4),
    ev("pull_request_checks", { state: "passed", failed: [] }, 300),
    ...run(2, "review", "review", 30, "0.1"),
    ev("pull_request_reviewed", { round: 1, verdict: "clean", findings: 0 }, 1),
    ev("pull_request_merged", { pr_url: "https://github.com/o/r/pull/9", merge_sha: "58e544902fca" }, 5),
    ev("merge_checks", { state: "passed", failed: [] }, 420),
    ev("task_status_changed", { from: "running", to: "closed", reason: "merged" }, 1),
  ]);
  const sum = totals(path);
  expect(sum.checks).toBe((300 + 420) * 1000);
  // Queued for 5 seconds, then the two waits for checks.
  expect(sum.waited).toBe((5 + 300 + 420) * 1000);
  expect(sum.waited).toBeLessThan(sum.elapsed);

  // A wait for checks still going counts up to now.
  const going = steps([ev("task_created", { status: "queued" }), ev("task_admitted", {}, 5), ...run(3, "task", "standard", 60, "0.05"), ev("pull_request_opened", { pr_url: "https://github.com/o/r/pull/9" }, 4)]);
  const opened = Date.parse(going.at(-1)!.start);
  expect(totals(going, opened + 90_000).checks).toBe(90_000);
});

test("a hold and a cancel that repeat with the same reason show once, counted", () => {
  const again: MhEvent[] = [];
  for (let i = 0; i < 5; i++) {
    again.push(ev("task_held", { reason: "goal g-1 is canceled" }, 240, { actor: "service" }));
    again.push(ev("task_status_changed", { from: "queued", to: "canceled", reason: "its goal was canceled" }, 1, { actor: "service" }));
    if (i < 4) again.push(ev("task_status_changed", { from: "canceled", to: "queued" }, 1));
  }
  const path = steps([ev("task_created", { status: "queued" }), ...again]);
  const held = path.filter((s) => s.title === "Waiting its turn");
  expect(held).toHaveLength(1);
  expect(held[0].times).toBe(5);
  expect(path.filter((s) => s.title === "Canceled")).toHaveLength(1);
});

test("a repeat inside a repeating cycle keeps every count", () => {
  const hold = (reason: string) => ev("task_held", { reason }, 60, { actor: "service" });
  const cancel = (reason: string) => ev("task_status_changed", { from: "queued", to: "canceled", reason }, 60, { actor: "service" });
  const requeue = () => ev("task_status_changed", { from: "canceled", to: "queued" }, 60);
  // Hold and cancel twice, then a different hold and cancel; all of that twice over.
  const round = () => [hold("a"), cancel("a"), requeue(), hold("a"), cancel("a"), requeue(), hold("b"), ev("task_admitted", {}, 60)];
  const path = steps([ev("task_created", { status: "queued" }), ...round(), ...round()]);
  const held = path.filter((s) => s.title === "Waiting its turn" && s.detail === "a");
  expect(held).toHaveLength(1);
  expect(held[0].times).toBe(4);
});

test("the page reads the supervisor's remedies with the rest of a task's steps", () => {
  expect(STEP_EVENTS).toContain("remedy_applied");
});

test("a task made blocked waits as Blocked, is Queued once what it waited on is done, and shows what unstuck it", () => {
  const path = steps([
    ev("task_created", { status: "blocked" }),
    ev("task_status_changed", { from: "blocked", to: "queued", reason: "every issue it depends on is closed" }, 3600),
    ev("task_unblocked", { ref: "jira:VL-1" }, 3600),
    ev("task_admitted", {}, 3700),
    ev("task_status_changed", { from: "queued", to: "running" }, 3700),
    ev("task_status_changed", { from: "running", to: "failed", reason: "TaskFailed: pi changed nothing" }, 4000),
    ev("task_driven", { action: "retry", why: "the task failed on its own account" }, 4300),
    ev("remedy_applied", { remedy: "retry_stronger", try: 1, of: 1, did: "ran it again on the complex tier, with a stronger model", next: "if it fails again, I will ask you what to do" }, 4300),
  ]);
  expect(path.filter((s) => s.column === 0).map((s) => s.title)).toEqual(["Blocked", "Queued", "Sent back for its retry by the Task Manager"]);
  expect(path.at(-1)!.notes).toContain("Unstuck: ran it again on the complex tier, with a stronger model (try 1 of 1). Next: if it fails again, I will ask you what to do");
});

test("a fix run asked to resolve a conflict is named for that, and any other fix run stays a fix run", () => {
  const asks = "--- requested ---\nThe branch conflicts with origin/main. The meta-harness has merged origin/main into it in /workspace";
  const path = steps([
    ev("run_prompt", { kind: "fix", run_id: "t:run-8", prompt: asks }, 0, { run_id: "t:run-8" }),
    ev("attempt_finished", { tier: "fix", decision: "finished", candidate: "harness:pi/a:b" }, 60, { run_id: "t:run-8" }),
    ev("run_prompt", { kind: "fix", run_id: "t:run-9", prompt: "--- requested ---\nFindings:\n- app.py line 3: Off by one." }, 5, { run_id: "t:run-9" }),
  ]);

  expect(path.map((s) => s.title)).toEqual(["Resolving the merge conflict", "Fix run"]);
});
