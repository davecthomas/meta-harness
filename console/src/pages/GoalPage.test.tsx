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
