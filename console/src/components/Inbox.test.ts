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
    "Ready for you to merge: Task a",
    "Review unresolved: Task b",
    "Failed: Task c",
  ]);
  expect(items[3].to).toBe("/tasks/a");
});
