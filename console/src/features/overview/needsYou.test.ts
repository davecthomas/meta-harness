import { afterEach, expect, test, vi } from "vitest";
import type { Goal, Task } from "../../api";
import { forgetNeeds, needsYouItems, needsYouNote, reloadNeeds, type ServerNeed } from "./needsYou";

afterEach(() => {
  vi.unstubAllGlobals();
  forgetNeeds();
});

function need(more: Partial<ServerNeed>): ServerNeed {
  return { key: "k", what: "Why it waits.", ref: "", action: "open_task", task_id: "", pr_url: "", asks: "Look at", title: "", since: "", goal_id: "", goal: "", label: "", ...more };
}

test("a row is what the planner's list says: what it asks, what it is about, why, and where to go", () => {
  const rows = needsYouItems([
    need({ key: "approve_plan:g-1", action: "approve_plan", asks: "Approve the plan", goal_id: "g-1", goal: "Add exports", what: "The plan is ready, with 3 new tasks. Nothing runs until you approve it." }),
    need({ key: "merge:t-1", action: "merge", asks: "Merge", task_id: "t-1", title: "CSV export", goal_id: "g-1", goal: "Add exports", pr_url: "https://github.com/o/r/pull/9", what: "CSV export: its pull request passed checks and review.", since: "2026-11-23T10:00:00Z" }),
    need({ key: "raise_budget:g-2", action: "raise_budget", asks: "Raise the budget", goal_id: "g-2", goal: "Add imports" }),
  ]);

  expect(rows.map((r) => [r.key, r.action, r.title, r.to])).toEqual([
    ["approve_plan:g-1", "Approve the plan", "Add exports", "/goals/g-1"],
    ["task-t-1", "Merge", "CSV export", "/tasks/t-1"],
    ["goal-g-2", "Raise the budget", "Add imports", "/goals/g-2"],
  ]);
  expect(rows[0].detail).toBe("The plan is ready, with 3 new tasks. Nothing runs until you approve it.");
  expect([rows[1].href, rows[1].hrefLabel, rows[1].since]).toEqual(["https://github.com/o/r/pull/9", "Open the pull request", "2026-11-23T10:00:00Z"]);
  expect(rows[0].href).toBeUndefined();
});

test("the console decides nothing: a task or goal read beside the list only adds to what a row draws", () => {
  const failed = { id: "t-2", title: "Import", status: "failed", status_reason: "the run reached its turn limit", external_ref: "mh:i-2" } as unknown as Task;
  const running = { id: "t-9", title: "Search", status: "failed", status_reason: "x" } as unknown as Task;
  const goal = { id: "g-1", statement: "Add exports", status: "planning" } as unknown as Goal;

  // Tasks and goals with no need in the list make no row, whatever their status.
  expect(needsYouItems([], [failed, running], [goal])).toEqual([]);
  const [row] = needsYouItems([need({ key: "retry:t-2", action: "retry", asks: "Retry or fix", task_id: "t-2", title: "Import", what: "Import failed." })], [failed, running], [goal]);
  expect([row.key, row.action, row.task?.id]).toEqual(["task-t-2", "Retry or fix", "t-2"]);
  // A need whose task could not be read is still a row.
  const [bare] = needsYouItems([need({ key: "retry:t-3", action: "retry", asks: "Retry or fix", task_id: "t-3", title: "Export", what: "Export failed." })]);
  expect([bare.title, bare.detail, bare.task]).toEqual(["Export", "Export failed.", undefined]);
});

test("the count's note says what it is made of", () => {
  expect(needsYouNote(null)).toBe("");
  expect(needsYouNote([])).toBe("Nothing needs you.");
  const rows = needsYouItems([need({ key: "a", asks: "Merge" }), need({ key: "b", asks: "Merge" }), need({ key: "c", asks: "Approve the plan" })]);
  expect(needsYouNote(rows)).toBe("2 to merge, 1 to approve the plan.");
});

test("every page shares one read of the list, and a read that fails keeps the list it had", async () => {
  const asked: string[] = [];
  let fail = false;
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    const body = url.startsWith("/v1/needs") ? { needs: [need({ key: "approve_plan:g-1", action: "approve_plan", asks: "Approve the plan", goal_id: "g-1", goal: "Add exports" })], count: 1 } : { tasks: [], goals: [], total: 0 };
    return Promise.resolve(new Response(JSON.stringify(fail && url.startsWith("/v1/needs") ? { detail: "the planner is restarting" } : body), { status: fail && url.startsWith("/v1/needs") ? 503 : 200, headers: { "Content-Type": "application/json" } }));
  });

  // Two pages asking at once make one read.
  await Promise.all([reloadNeeds(), reloadNeeds()]);
  expect(asked.filter((u) => u.startsWith("/v1/needs"))).toHaveLength(1);
});
