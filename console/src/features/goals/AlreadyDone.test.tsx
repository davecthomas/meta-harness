import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Task } from "../../api";
import { theme } from "../../theme";
import { AlreadyDone, found } from "./AlreadyDone";
import type { GoalPlan } from "./api";

afterEach(() => vi.unstubAllGlobals());

const PLAN = {
  version: 1,
  drafts: [],
  items: [
    { ref: "github:o/a#4", decision: "leave_out", reason: "Already done: src/export.py has the endpoint, from commit 1a2b3c.", depends_on: [], replaced_by: [] },
    { ref: "github:o/a#5", decision: "leave_out", reason: "Same work as github:o/a#4, which is kept.", depends_on: [], replaced_by: [] },
    { ref: "github:o/a#6", decision: "leave_out", reason: "Not this goal's work.", depends_on: [], replaced_by: [] },
    { ref: "github:o/a#7", decision: "keep", reason: "Runs as written.", depends_on: [], replaced_by: [] },
  ],
  issues: [{ ref: "github:o/a#4", repo: "o/a", title: "Add the export endpoint" }],
  gaps: ["Already done: the CSV writer is in src/csv.py.", "Nothing covers the audit log."],
} as unknown as NonNullable<GoalPlan["plan"]>;

const task = (id: string, status: string, reason: string, title = "Add a button"): Task => ({ id, status, status_reason: reason, title }) as unknown as Task;
const TASKS = [
  task("t-1", "closed", "the work is already on the base branch: app.tsx has the button, from pull request #5."),
  task("t-2", "closed", "the pull request merged"),
  task("t-3", "failed", "the work is already on the base branch: no"),
];

test("the work a goal did not need to build is what the plan left out as done, and tasks a check closed", () => {
  const items = found(PLAN, TASKS);
  expect(items.map((i) => [i.by, i.ref || i.taskId, i.where])).toEqual([
    ["plan", "github:o/a#4", "src/export.py has the endpoint, from commit 1a2b3c."],
    ["plan", "github:o/a#5", "Same work as github:o/a#4, which is kept."],
    ["plan", "", "the CSV writer is in src/csv.py."],
    ["task", "t-1", "app.tsx has the button, from pull request #5."],
  ]);
  expect(found(null, [])).toEqual([]);
});

function show(plan: unknown, tasks: Task[]) {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ goal_id: "g-1", status: "active", plan }), { status: 200, headers: { "Content-Type": "application/json" } })));
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <AlreadyDone goalId="g-1" tasks={tasks} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the goal's page counts it in one line, and lists each with where the work is", async () => {
  show(PLAN, TASKS);

  const toggle = await screen.findByRole("button", { name: "Work that was already done" });
  expect(screen.getByTestId("already-done").textContent).toContain("4 pieces of work were already done");
  expect(screen.getByTestId("already-done").textContent).toContain("3 found by the plan, 1 by a task's check.");
  expect(screen.queryByTestId("already-done-list")).toBeNull();

  fireEvent.click(toggle);
  const list = screen.getByTestId("already-done-list").textContent ?? "";
  expect(list).toContain("Add the export endpoint");
  expect(list).toContain("src/export.py has the endpoint, from commit 1a2b3c.");
  expect(list).toContain("Left out of the plan, so no task ran.");
  expect(screen.getByRole("link", { name: "Add a button" }).getAttribute("href")).toBe("/tasks/t-1");
  expect(list).toContain("a second model confirmed the work is on the base branch");
});

test("a goal with nothing already done shows nothing", async () => {
  show(null, [task("t-2", "closed", "the pull request merged")]);
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByTestId("already-done")).toBeNull();
});
