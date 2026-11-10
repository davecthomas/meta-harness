import type { ReactElement } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { GoalPage } from "./GoalPage";
import { GoalsPage } from "./GoalsPage";
import { theme } from "../../theme";

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
  fireEvent.click(screen.getByText("#9"));
  expect(screen.queryByText("task page")).toBeNull();

  fireEvent.keyDown(screen.getByRole("link", { name: "Open task Fix the crash" }), { key: "Enter" });

  expect(await screen.findByText("task page")).toBeTruthy();
});

test("a goal shows its title over its description, and its title is edited in place", async () => {
  let current = { ...goal("active"), started_at: "2026-11-08T20:41:00Z", title: "Fix issue 55", statement: "Close #55: the save loses the last edit.", max_usd: "10" };
  const patched: unknown[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      patched.push(body);
      current = { ...current, ...body };
      return respond(current);
    }
    if (url.startsWith("/v1/tasks")) return respond({ tasks: [], total: 0 });
    return respond(current);
  });
  renderAt("/goals/g-1", <GoalPage />);

  expect(await screen.findByRole("heading", { name: "Fix issue 55" })).toBeTruthy();
  expect(screen.getByText("Close #55: the save loses the last edit.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit goal" })).toBeNull();
  fireEvent.click(screen.getByText("Fix issue 55"));
  const input = await screen.findByLabelText("Title");
  fireEvent.change(input, { target: { value: "Save keeps every edit" } });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() => expect(patched).toEqual([{ title: "Save keeps every edit" }]));
  expect(await screen.findByRole("heading", { name: "Save keeps every edit" })).toBeTruthy();
});

test("the Goals list shows each goal's title, or the start of its description when it has none", async () => {
  vi.stubGlobal("fetch", () =>
    respond({
      goals: [
        { ...SAVED, title: "Save bugs" },
        { ...SAVED, id: "g-2", title: null, statement: "A description long enough that a list shows only its start, cut short to keep every row on one line." },
      ],
      total: 2,
    }),
  );

  renderAt("/goals", <GoalsPage />);

  expect(await screen.findByText("Save bugs")).toBeTruthy();
  expect(screen.getByText(/^A description long enough .*…$/)).toBeTruthy();
});

test("the board refreshes by itself: a card with no task shows its issue's title, then moves once its task runs", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.stubGlobal("EventSource", class { static CLOSED = 2; addEventListener() {} close() {} });
  const active = { ...SAVED, status: "active", started_at: "2026-11-08T20:45:00Z" };
  const link = { ref: "github:o/a#1", repo: "o/a", state: "open", depends_on: [], refused_reason: null, picked: false };
  const bare = { ...link, title: "Crash on save", task_id: null, task_status: null, task_status_reason: null, pr_url: null, pr_state: null, checks: null };
  let started = false;
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url === "/v1/goals/g-1") return respond({ ...active, issues: [{ ...link, task_id: started ? "t-1" : null }] });
    if (url === "/v1/goals/g-1/issues") return respond({ issues: [started ? { ...bare, task_id: "t-1", task_status: "running" } : bare] });
    if (url.startsWith("/v1/tasks")) return respond({ tasks: started ? [{ id: "t-1", title: "Crash on save", status: "running" }] : [], total: started ? 1 : 0 });
    if (url.startsWith("/v1/events")) return respond({ events: [], latest_position: 0 });
    if (url === "/v1/limits") return respond({ max_parallel: 8, max_sandboxes: 8 });
    return respond({ base: "", ticket: null });
  });

  try {
    renderAt("/goals/g-1", <GoalPage />);

    const queued = await screen.findByTestId("column-queued");
    await waitFor(() => expect(queued.textContent).toContain("Crash on save"));
    expect(screen.getByTestId("column-running").textContent).not.toContain("Crash on save");
    expect(screen.getByTestId("refreshed").textContent).toContain("updated");

    // The planner makes the task and the scheduler starts it; nothing tells the page but its own reread.
    started = true;
    const before = asked.filter((u) => u === "/v1/goals/g-1/issues").length;
    await vi.advanceTimersByTimeAsync(5100);
    await waitFor(() => expect(screen.getByTestId("column-running").textContent).toContain("Crash on save"));
    expect(asked.filter((u) => u === "/v1/goals/g-1/issues").length).toBeGreaterThan(before);
    expect(asked.some((u) => u.includes("/v1/repos/"))).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});
