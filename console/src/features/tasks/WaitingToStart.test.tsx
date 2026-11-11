import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Task, TaskHold } from "../../api";
import { theme } from "../../theme";
import { WaitingToStart, waitReason } from "./WaitingToStart";

afterEach(() => vi.unstubAllGlobals());

const QUEUED = { id: "t-1", title: "Fix it", status: "queued", goal_id: "g-1", external_ref: "github:o/r#7" } as Task;
const hold = (kind: TaskHold["kind"], reason: string, detail: Record<string, unknown> = {}): TaskHold => ({
  kind, reason, detail, since: "2026-11-11T09:00:00Z", last_checked: "2026-11-11T13:00:00Z",
});

function show(task: Task) {
  const { container } = render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <WaitingToStart task={task} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return container.querySelector('[data-testid="waiting-to-start"]');
}

test("a task queued behind its goal's budget says so in dollars and cents, with a field for the goal's new budget", async () => {
  const goal = { id: "g-1", max_usd: "10.0000", spent_usd: "26.7900", budget_block_at: "11.00", budget_resume_to: "45.0000", issues: [] };
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify(goal), { status: 200, headers: { "Content-Type": "application/json" } })));
  const notice = show({ ...QUEUED, held: hold("goal_budget", "goal g-1 spent $26.79 of its $10.0000 budget, and stops at $11.00", { goal_id: "g-1" }) });
  expect(notice?.textContent).toContain("Waiting to start");
  expect(notice?.textContent).toContain("goal g-1 spent $26.79 of its $10.00 budget");
  expect(((await screen.findByLabelText(/New goal budget/)) as HTMLInputElement).value).toBe("$45");
  expect(notice?.textContent).toContain("Now $10.00, and the goal has spent $26.79; it stops at $11.00");
  expect(screen.getByRole("button", { name: "Raise the goal's budget and carry on" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "The goal's page" }).getAttribute("href")).toBe("/goals/g-1");
});

test("a task waiting for a place links to the tasks that hold them", () => {
  show({ ...QUEUED, held: hold("goal_parallel", "goal g-1 already runs 2 tasks, its limit", { goal_id: "g-1", holders: ["t-a", "t-b"] }) });
  expect(screen.getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/tasks/t-a", "/tasks/t-b"]);
});

test("a task waiting for a repo area names the task holding it and the paths they share", () => {
  const notice = show({ ...QUEUED, held: hold("repo_area", "repo area src/api/tasks.py of o/r is held by t-a", { holders: ["t-a"], paths: ["src/api/tasks.py"], held_paths: ["src/api"] }) });
  expect(screen.getByRole("link", { name: "t-a" }).getAttribute("href")).toBe("/tasks/t-a");
  expect(notice?.textContent).toContain("on src/api, which this task's src/api/tasks.py shares");
});

test("a task whose goal is paused links to the goal, and one stopped by the kill switch to the Overview", () => {
  show({ ...QUEUED, held: hold("goal_status", "goal g-1 is paused", { goal_id: "g-1", status: "paused" }) });
  expect(screen.getByRole("link", { name: "Open the goal" }).getAttribute("href")).toBe("/goals/g-1");
  show({ ...QUEUED, id: "t-2", held: hold("kill_switch", "the kill switch is on: no task may start") });
  expect(screen.getByRole("link", { name: "See the Overview" }).getAttribute("href")).toBe("/");
});

test("a queued task nothing holds starts at the next pass, and a running task shows no notice", () => {
  expect(show({ ...QUEUED, held: null })?.textContent).toContain("it starts at the scheduler's next pass");
  expect(waitReason({ status: "queued", held: null })).toBe("starts at the scheduler's next pass");
  expect(waitReason({ status: "running", held: null })).toBeNull();
});

test("a blocked task lists each issue it waits on with its state", async () => {
  const issues = [
    { ref: "github:o/r#5", repo: "o/r", state: "open", depends_on: [], refused_reason: null, task_id: "t-5", task_status: "running", task_status_reason: null, pr_url: null, pr_state: null, checks: null },
    { ref: "github:o/r#6", repo: "o/r", state: "closed", depends_on: [], refused_reason: null, task_id: null, task_status: null, task_status_reason: null, pr_url: null, pr_state: null, checks: null },
  ];
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ issues }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const blocked = { ...QUEUED, status: "blocked", depends_on: ["github:o/r#5", "github:o/r#6"] } as Task;
  show(blocked);

  expect(await screen.findByRole("link", { name: "t-5" })).toBeTruthy();
  expect(screen.getByTestId("waits-on-github:o/r#5").textContent).toContain("open");
  expect(screen.getByTestId("waits-on-github:o/r#6").textContent).toContain("closed");
  expect(waitReason(blocked)).toBe("waits on 2 issues");
});
