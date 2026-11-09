import type { ReactElement } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { GoalPage } from "./GoalPage";
import { GoalsPage } from "./GoalsPage";
import { theme } from "../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

const SAVED = {
  id: "g-1",
  statement: "Fix the save bugs",
  repos: ["o/a"],
  label: "bug",
  modality: "fix",
  status: "paused",
  auto_merge: false,
  max_usd: null,
  priority: 0,
  created_by: "dave",
  created_at: "2026-11-08T20:40:00Z",
  started_at: null,
};

function renderAt(path: string, element: ReactElement) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/goals" element={element} />
          <Route path="/goals/:goalId" element={element} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("a saved goal shows Not started with its label's open issues, and Start resumes it", async () => {
  const posted: string[] = [];
  let goal: Record<string, unknown> = SAVED;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posted.push(url);
      goal = { ...SAVED, status: "active", started_at: "2026-11-08T20:45:00Z" };
      return respond(goal);
    }
    if (url === "/v1/goals/g-1") return respond({ ...goal, issues: [] });
    if (url.startsWith("/v1/tasks")) return respond({ tasks: [], total: 0 });
    if (url === "/v1/repos/o/a/issues?label=bug")
      return respond({
        issues: [
          { ref: "github:o/a#1", number: 1, title: "Crash on save", labels: ["bug"], updated_at: "2026-11-08T20:00:00Z", held_by: null },
        ],
      });
    return respond({ detail: "not stubbed" }, 404);
  });

  renderAt("/goals/g-1", <GoalPage />);

  expect(await screen.findByText("Crash on save")).toBeTruthy();
  expect(screen.getAllByText("Not started").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Start" }));

  await waitFor(() => expect(posted).toEqual(["/v1/goals/g-1/resume"]));
  expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();
  expect(screen.queryByText("Crash on save")).toBeNull();
});

test("the Goals list tells a goal not started from one paused after running", async () => {
  vi.stubGlobal("fetch", () =>
    respond({
      goals: [SAVED, { ...SAVED, id: "g-2", statement: "Paused one", started_at: "2026-11-08T20:45:00Z" }],
      total: 2,
    }),
  );

  renderAt("/goals", <GoalsPage />);

  expect(await screen.findByText("Not started")).toBeTruthy();
  expect(screen.getByText("paused")).toBeTruthy();
});

function goal(status: string) {
  return {
    id: "g-1",
    statement: "Fix issue #55",
    repos: ["o/r"],
    label: "mh:fix",
    modality: "fix",
    status,
    auto_merge: false,
    max_usd: null,
    priority: 0,
    created_by: "dave",
    created_at: "2026-11-08T20:40:00Z",
    // Started: these goals ran before they reached this status.
    started_at: "2026-11-08T20:41:00Z",
    closed_at: ["met", "canceled"].includes(status) ? "2026-11-09T10:00:00Z" : null,
    issues: [],
  };
}

// The goal page over a fake API: GET returns the goal in `status`, and a POST action moves it to `after`.
function renderGoal(status: string, after: Record<string, string> = {}) {
  const posted: string[] = [];
  let current = status;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const action = url.split("/").pop() ?? "";
      posted.push(action);
      current = after[action] ?? current;
      return respond(goal(current));
    }
    if (url.startsWith("/v1/tasks")) return respond({ tasks: [], total: 0 });
    return respond(goal(current));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/g-1"]}>
        <Routes>
          <Route path="/goals/:goalId" element={<GoalPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  return posted;
}

test.each(["active", "paused", "stalled"])("a %s goal can be completed or canceled, not reopened", async (status) => {
  renderGoal(status);
  expect(await screen.findByRole("button", { name: "Complete goal" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Cancel goal" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Reopen goal" })).toBeNull();
});

test.each(["met", "canceled"])("a %s goal can be reopened, not completed or canceled", async (status) => {
  renderGoal(status);
  expect(await screen.findByRole("button", { name: "Reopen goal" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Complete goal" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel goal" })).toBeNull();
});

test("a budget-exhausted goal offers no Complete goal", async () => {
  renderGoal("budget_exhausted");
  expect(await screen.findByRole("button", { name: "Cancel goal" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Complete goal" })).toBeNull();
});

test("Complete goal sets the goal met, and Reopen goal makes it active again", async () => {
  const posted = renderGoal("active", { complete: "met", reopen: "active" });

  fireEvent.click(await screen.findByRole("button", { name: "Complete goal" }));
  fireEvent.click(await screen.findByRole("button", { name: "Reopen goal" }));

  await waitFor(() => expect(screen.getByRole("button", { name: "Complete goal" })).toBeTruthy());
  expect(posted).toEqual(["complete", "reopen"]);
});

test("a row with a task opens the task, and its links open GitHub instead", async () => {
  const running = { ...goal("active"), started_at: "2026-11-08T20:41:00Z", issues: [
    { ref: "github:o/r#7", repo: "o/r", task_id: "t-7", state: "open", depends_on: [], refused_reason: null },
    { ref: "github:o/r#8", repo: "o/r", task_id: null, state: "open", depends_on: [], refused_reason: "no checks" },
  ] };
  const task = { id: "t-7", external_ref: "github:o/r#7", repo: "o/r", task_type: "bug", title: "Fix the crash", status: "running",
    status_reason: null, cancel_requested: false, goal_id: "g-1", pr_url: "https://github.com/o/r/pull/9", acceptance_checks: [],
    created_at: "2026-11-08T20:41:00Z", updated_at: "2026-11-08T20:42:00Z" };
  vi.stubGlobal("fetch", (url: string) => {
    if (url.startsWith("/v1/tasks")) return respond({ tasks: [task], total: 1 });
    return respond(running);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/g-1"]}>
        <Routes>
          <Route path="/goals/:goalId" element={<GoalPage />} />
          <Route path="/tasks/:taskId" element={<p>task page</p>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

  expect(await screen.findByText("Fix the crash")).toBeTruthy();
  expect(screen.getByTestId("issue-row-github:o/r#8").getAttribute("role")).toBeNull();
  fireEvent.click(screen.getByText("Open"));
  expect(screen.queryByText("task page")).toBeNull();

  fireEvent.keyDown(screen.getByRole("link", { name: "Open task Fix the crash" }), { key: "Enter" });

  expect(await screen.findByText("task page")).toBeTruthy();
});
