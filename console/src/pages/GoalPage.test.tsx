import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { GoalPage } from "./GoalPage";
import { theme } from "../theme";

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

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
