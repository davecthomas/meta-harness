import { expect, test } from "vitest";
import type { Goal, Task } from "../api";
import { inboxItems } from "./Inbox";

const task = (id: string, status: string, updated_at = "2026-11-10T10:00:00Z") => ({ id, status, title: `Task ${id}`, updated_at }) as Task;

test("the inbox lists what waits on the operator, and a failure ages out", () => {
  const now = Date.parse("2026-11-10T12:00:00Z");
  const items = inboxItems(
    [task("a", "ready"), task("b", "review_unresolved"), task("c", "failed"), task("d", "failed", "2026-11-01T00:00:00Z"), task("e", "running")],
    [
      { id: "g-1", status: "budget_exhausted", title: "Saves", statement: "x" } as Goal,
      { id: "g-2", status: "active", budget_state: "over", title: "Exports", statement: "x" } as Goal,
      { id: "g-3", status: "active", budget_state: "ok", title: "Quiet", statement: "x" } as Goal,
    ],
    { engaged: true, reason: "runaway", changed_by: "dave", changed_at: "" },
    now,
  );
  expect(items.map((i) => i.text)).toEqual([
    "The kill switch is on: runaway",
    "Stopped by its budget: Saves",
    "Over budget: Exports",
    "Ready to merge: Task a",
    "Review unresolved: Task b",
    "Failed: Task c",
  ]);
  expect(items[3].to).toBe("/tasks/a");
});

test("each item says what to do, a ready task links its pull request, and a failure says its fix", () => {
  const grant = "TaskFailed: run 1 on harness:pi-lean/claude:claude-sonnet-5 ended for good: run_harness failed (HarnessConfigError): HarnessConfigError: the HTTP Gateway refused the run grant (422): [{'type': 'string_pattern_mismatch', 'loc': ['body', 'spend_limit_usd'], 'msg': \"String should match pattern\", 'input': '1000000.00'}]";
  const items = inboxItems(
    [
      { ...task("a", "ready"), pr_url: "https://github.com/o/r/pull/9" } as Task,
      { ...task("c", "failed"), status_reason: grant } as Task,
    ],
    [],
    null,
    Date.parse("2026-11-10T12:00:00Z"),
  );
  expect(items[0].href).toBe("https://github.com/o/r/pull/9");
  expect(items[0].detail).toContain("Merge it");
  expect(items[1].detail).toBe(
    "The run could not start: the HTTP Gateway refused its spend limit. Retry the task. If it fails the same way, the deployed control plane is older than the fix: check the last deploy.",
  );
  expect(items[1].detail).not.toContain("string_pattern_mismatch");
});
