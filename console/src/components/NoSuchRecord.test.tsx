import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { GoalPage } from "../features/goals/GoalPage";
import { TaskPage } from "../features/tasks/TaskPage";
import { theme } from "../theme";

afterEach(() => vi.unstubAllGlobals());

function answer(status: number, detail: string) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json" } }))));
}

function show(path: string) {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/goals/:goalId" element={<GoalPage />} />
          <Route path="/tasks/:taskId" element={<TaskPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test.each([
  ["/goals/g-x", "no such goal", "goal"],
  ["/tasks/t-x", "no such task", "task"],
])("%s: an unknown id is No such page with links, not a failure", async (path, detail, kind) => {
  answer(404, detail);
  show(path);
  await waitFor(() => expect(screen.getByTestId("no-such-page")).toBeTruthy());
  expect(screen.getByText(new RegExp(`No ${kind} has the id`))).toBeTruthy();
  expect(screen.getByRole("link", { name: "Tasks" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Goals" })).toBeTruthy();
  expect(screen.queryByTestId("error-alert")).toBeNull();
});

test.each(["/goals/g-x", "/tasks/t-x"])("%s: a read that really failed keeps the reload message", async (path) => {
  answer(500, "boom");
  show(path);
  await waitFor(() => expect(screen.getByTestId("error-alert")).toBeTruthy());
  expect(screen.getByText("Reload to try again")).toBeTruthy();
  expect(screen.queryByTestId("no-such-page")).toBeNull();
});
