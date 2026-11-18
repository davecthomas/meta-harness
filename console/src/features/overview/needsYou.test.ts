import { expect, test } from "vitest";
import type { Goal, Task } from "../../api";
import { needsYouItems, needsYouNote } from "./needsYou";

// One list of what needs the operator, for the Overview and the header's inbox, each item named by what it asks.

const task = (id: string, status: string, more: Partial<Task> = {}) => ({ id, status, title: `Task ${id}`, updated_at: "2026-11-10T10:00:00Z", ...more }) as Task;
const waiting = { owner: "operator", situation: "blocked_on_stuck", reason: "it waits on o/r#7, whose task is not moving", ask: "Open that issue's task and retry or fix it.", since: "2026-11-10T09:00:00Z", checked_at: "", attempts: 0, last_error: null };

test("each thing that needs the operator is named by what it asks for, goals first", () => {
  const items = needsYouItems(
    [
      task("a", "ready", { pr_url: "https://github.com/o/r/pull/9" }),
      task("b", "review_unresolved"),
      task("c", "failed", { updated_at: "2026-10-01T00:00:00Z" }),
      task("d", "running", { waiting_on: "budget", status_reason: "it spent $5.00 of its $5.00 limit" }),
      task("e", "running", { waiting_on: "review" }),
      task("f", "running", { waiting_on: "operator_review", review_claimed_by: "pat@example.com" }),
      task("g", "blocked", { standing: waiting } as Partial<Task>),
      task("h", "running"),
    ],
    [
      { id: "g-1", status: "budget_exhausted", title: "Saves", statement: "x" } as Goal,
      { id: "g-2", status: "active", budget_state: "over", title: "Exports", statement: "x" } as Goal,
      { id: "g-3", status: "active", budget_state: "ok", title: "Quiet", statement: "x" } as Goal,
      { id: "g-4", status: "active", title: "Stuck", statement: "x", standing: { state: "needs_you", reason: "nothing in it is moving: 2 tasks wait on you", ask: "", yours: [], moving: 0 } } as unknown as Goal,
    ],
  );

  expect(items.map((i) => `${i.action}: ${i.title}`)).toEqual([
    "Raise the budget: Saves",
    "Over budget: Exports",
    "Unstick the goal: Stuck",
    "Merge: Task a",
    "Settle the review: Task b",
    "Retry or fix: Task c",
    "Raise the spend limit: Task d",
    "Decide on the review: Task e",
    "Finish the review: Task f",
    "Look at: Task g",
  ]);
  const by = Object.fromEntries(items.map((i) => [i.key, i]));
  expect(by["task-a"].href).toBe("https://github.com/o/r/pull/9");
  expect(by["task-a"].detail).toContain("Merge it with Merge on the task's page");
  expect(by["task-c"].detail).toBeTruthy(), "an old failure still needs someone: nothing ages out of the list";
  expect(by["task-f"].detail).toBe("pat@example.com took its review over: the task waits for that verdict.");
  expect(by["task-g"].detail).toBe("It waits on o/r#7, whose task is not moving. Open that issue's task and retry or fix it.");
  expect(by["task-g"].since).toBe("2026-11-10T09:00:00Z");
  expect(by["goal-waits-g-4"].to).toBe("/goals/g-4");
});

test("a failure says its fix in plain words, not the raw error", () => {
  const grant = "TaskFailed: run 1 on harness:pi-lean/claude:claude-sonnet-5 ended for good: run_harness failed (HarnessConfigError): HarnessConfigError: the HTTP Gateway refused the run grant (422): [{'type': 'string_pattern_mismatch', 'loc': ['body', 'spend_limit_usd'], 'msg': \"String should match pattern\", 'input': '1000000.00'}]";
  const [item] = needsYouItems([task("c", "failed", { status_reason: grant })], []);
  expect(item.detail).toContain("Retry the task");
  expect(item.detail).not.toContain("string_pattern_mismatch");
});

test("the count says what it is made of", () => {
  const items = needsYouItems([task("a", "ready"), task("b", "ready"), task("c", "failed")], [{ id: "g-1", status: "budget_exhausted", title: "Saves", statement: "x" } as Goal]);
  expect(needsYouNote(items)).toBe("1 to raise the budget, 2 to merge, 1 to retry or fix.");
  expect(needsYouNote([])).toBe("Nothing needs you.");
  expect(needsYouNote(null)).toBe("");
});

test("a task held for budget is named by the amount it asks for when the service says it", () => {
  const request = { for: "task" as const, what: "fix run 2", needs_usd: "1.65", left_usd: "0.40", limit_usd: "3.00", add_usd: "2", to_usd: "5.00" };
  const [own, goals] = needsYouItems(
    [
      task("a", "running", { waiting_on: "budget", status_reason: "fix run 2 needs about $1.65", budget_request: request }),
      task("b", "running", { waiting_on: "budget", status_reason: "the review needs about $0.30", budget_request: { for: "goal", what: "the review", needs_usd: "0.30", goal_id: "g-1" } }),
    ],
    [],
  );
  expect(own.action).toBe("Grant $2.00 more");
  expect(own.detail).toContain("grant the $2.00 in one click, set another limit, or end it");
  expect(goals.action).toBe("Raise the goal's budget");
});
