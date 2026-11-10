import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { IssueProgress, Task } from "../../api";
import { theme } from "../../theme";
import { GoalBoard, GoalGraph, GoalProgress, columnOf, layout } from "./GoalBoard";

function issue(n: number, fields: Partial<IssueProgress> = {}): IssueProgress {
  return {
    ref: `github:o/r#${n}`,
    repo: "o/r",
    state: "open",
    depends_on: [],
    refused_reason: null,
    task_id: `t-${n}`,
    task_status: "queued",
    task_status_reason: null,
    pr_url: null,
    pr_state: null,
    checks: null,
    ...fields,
  };
}

function task(n: number, title: string): Task {
  return {
    id: `t-${n}`, external_ref: `github:o/r#${n}`, repo: "o/r", task_type: "bug", title, status: "running", status_reason: null,
    cancel_requested: false, goal_id: "g-1", pr_url: null, acceptance_checks: [], created_at: "", updated_at: "",
  };
}

const ISSUES = [
  issue(1, { task_status: "queued" }),
  issue(2, { task_status: "running" }),
  issue(3, { task_status: "running", pr_url: "https://github.com/o/r/pull/30", pr_state: "open", checks: { head_sha: "a", state: "passed", failed: [] } }),
  issue(4, { task_status: "closed", pr_url: "https://github.com/o/r/pull/40", pr_state: "merged", state: "closed" }),
  issue(5, { task_status: "failed" }),
  issue(6, { task_status: "blocked", depends_on: ["github:o/r#5"] }),
  issue(7, { task_status: "running", pr_url: "https://github.com/o/r/pull/70", pr_state: "open", checks: { head_sha: "b", state: "failed", failed: ["lint"] } }),
  issue(8, { task_id: null, task_status: null, refused_reason: "no acceptance checks" }),
];

test("each issue goes to the column its task, pull request and dependencies put it in", () => {
  const byRef = new Map(ISSUES.map((i) => [i.ref, i]));
  expect(ISSUES.map((i) => columnOf(i, byRef))).toEqual([
    "queued", "running", "review", "done", "attention", "attention", "attention", "attention",
  ]);
});

test("the dependency depth counts the longest chain to an issue, and a cycle does not loop", () => {
  const chain = [issue(1), issue(2, { depends_on: ["github:o/r#1"] }), issue(3, { depends_on: ["github:o/r#2", "github:o/r#1"] })];
  expect([...layout(chain).depth.values()]).toEqual([0, 1, 2]);
  const cycle = [issue(1, { depends_on: ["github:o/r#2"] }), issue(2, { depends_on: ["github:o/r#1"] })];
  expect(layout(cycle).depth.size).toBe(2);
});

function renderIn(element: React.ReactElement) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/g-1"]}>
        <Routes>
          <Route path="/goals/:goalId" element={element} />
          <Route path="/tasks/:taskId" element={<p>task page</p>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the board shows each card in its column with its task, cost and pull request, and a card opens its task", async () => {
  renderIn(<GoalBoard issues={ISSUES} tasks={{ "t-3": task(3, "Fix the crash") }} spend={{ "t-3": 1.25 }} />);

  const review = screen.getByTestId("column-review");
  expect(within(review).getByText("Fix the crash")).toBeTruthy();
  expect(within(review).getByText("$1.25")).toBeTruthy();
  expect(within(review).getByText("#30").getAttribute("href")).toBe("https://github.com/o/r/pull/30");
  expect(within(screen.getByTestId("column-attention")).getByText("no acceptance checks")).toBeTruthy();

  fireEvent.click(within(review).getByText("Fix the crash"));
  expect(await screen.findByText("task page")).toBeTruthy();
});

test("the graph colors each issue by its column", () => {
  renderIn(<GoalGraph issues={ISSUES} tasks={{}} />);
  expect(screen.getByTestId("node-github:o/r#4").getAttribute("data-column")).toBe("done");
  expect(screen.getByTestId("node-github:o/r#6").getAttribute("data-column")).toBe("attention");
});

test("progress counts done issues and spend against the budget", () => {
  renderIn(<GoalProgress issues={ISSUES} spend={{ "t-3": 1.25, "t-5": 2.5 }} budget="10" />);
  expect(screen.getByText("1 of 8 issues done")).toBeTruthy();
  expect(screen.getByText(/\$3.75 spent of \$10.00 · about \$30.00 to finish/)).toBeTruthy();
});
