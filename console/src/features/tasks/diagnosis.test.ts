import { expect, test } from "vitest";
import { diagnose, oneLine } from "./diagnosis";

const failed = (reason: string) => diagnose({ status: "failed", reason })!;

test("each kind of failure says what happened, why and what to do, and offers the action that fixes it", () => {
  const cases: [string, string, string[]][] = [
    ["TaskFailed: the task spent its $10.00 spend limit after 4 runs", "spend_limit", ["retry_more", "task"]],
    ["TaskFailed: run 2 finished, but pi changed nothing in o/r", "no_change", ["task", "issue", "retry"]],
    ["TaskFailed: run 1 on harness:a/b:c ended for good: ended ERROR (turn_limit): out of turns", "limit", ["retry", "config"]],
    ["TaskFailed: every candidate failed (3 runs)", "model", ["retry", "task"]],
    ["TaskFailed: run 1 on harness:a/b:c ended for good: ended ERROR (harness_error): the sandbox went away", "infrastructure", ["retry", "task"]],
    ["TaskFailed: run 1 on harness:a/b:c ended for good: run_harness failed (HarnessConfigError): HarnessConfigError: profile pi-x has no image", "config", ["config", "retry"]],
  ];
  for (const [reason, kind, remedies] of cases) {
    const found = failed(reason);
    expect([found.kind, found.remedies]).toEqual([kind, remedies]);
    expect(found.summary).not.toMatch(/TaskFailed|run_harness/);
    expect(found.cause.length).toBeGreaterThan(20);
    expect(found.fix.length).toBeGreaterThan(20);
  }
  expect(failed("TaskFailed: the task spent its $10.00 spend limit after 4 runs").summary).toBe("The task used up its $10.00");
});

test("the recorded error is kept without the wrappers each layer added", () => {
  const found = failed("TaskFailed: run 1 on harness:a/b:c ended for good: run_harness failed (HarnessConfigError): HarnessConfigError: profile pi-x has no image");
  expect(found.detail).toBe("profile pi-x has no image");
  expect(found.cause).toContain("profile pi-x has no image");
});

test("a failure no rule knows still says what was recorded and what to do next", () => {
  const found = failed("deploy failed on the merge commit");
  expect(found.summary).toBe("The task failed");
  expect(found.cause).toBe("The control plane recorded: deploy failed on the merge commit");
  expect(oneLine(found)).toBe("The task failed. Open the task for its runs and their logs. Retry it once the cause is fixed.");
  // Two unknown failures with different reasons are different kinds, so a list does not group them.
  expect(failed("something else").kind).not.toBe(found.kind);
});

test("a task held for budget, a refused issue and a working task", () => {
  expect(diagnose({ status: "running", reason: "reached its $10.00 limit", waitingOn: "budget" })!.kind).toBe("budget_hold");
  expect(diagnose({ status: null, reason: null, refused: "it depends on itself" })!.cause).toBe("The planner did not make a task for it: it depends on itself.");
  expect(diagnose({ status: "running", reason: null })).toBeNull();
  expect(diagnose({ status: "review_unresolved", reason: "review round 2 got no verdict: the run ended" })!.kind).toBe("no_verdict");
});
