import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { Activity } from "../../api";
import { theme } from "../../theme";
import { ActivityPage, costColor, costGradient } from "./ActivityPage";

afterEach(() => vi.unstubAllGlobals());

const pr = (task: string, state: string, usd: number, run: string | null) => ({
  task_id: task, latest_run_id: run, title: `Task ${task}`, repo: "o/r", pr_url: `https://github.com/o/r/pull/${task.slice(2)}`,
  opened_at: "2026-11-09T10:00:00Z", state, state_at: "2026-11-09T11:00:00Z", fix_rounds: 1, usd,
});

const ACTIVITY: Activity = {
  group_by: "goal",
  days: 91,
  groups: [
    { key: "g-1", name: "Saves", total: 2, left_out: 0, prs: [pr("t-1", "merged", 1, "r-1"), pr("t-2", "blocked", 4, "r-9")] as never,
      spent_without_pr: [{ task_id: "t-3", latest_run_id: "r-3", title: "Task t-3", started_at: "2026-11-09T09:00:00Z", usd: 0.5 }] },
  ],
  cost_scale: { low: 1, high: 4 },
};

function renderAt(path: string) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return Promise.resolve(new Response(JSON.stringify(ACTIVITY), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/tasks/:taskId" element={<p>task page</p>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  return asked;
}

test("each pull request is a cell colored by its state, and a click opens its task's latest run", async () => {
  const asked = renderAt("/activity");
  expect((await screen.findByTestId("cell-t-2")).getAttribute("data-state")).toBe("blocked");
  expect(screen.getByText("1 blocked · 1 merged")).toBeTruthy();
  expect(screen.queryByTestId("cell-t-3")).toBeNull();
  expect(asked[0]).toContain("group_by=goal");

  fireEvent.click(screen.getByTestId("cell-t-2"));
  expect(await screen.findByText("task page")).toBeTruthy();
});

test("the cost view adds the tasks that spent without a pull request and totals each row", async () => {
  renderAt("/activity?view=cost&by=repo");
  expect(await screen.findByTestId("cell-t-3")).toBeTruthy();
  expect(screen.getByText("$5.50")).toBeTruthy();
});

test("the cost gradient runs cool to warm between the scale's ends", () => {
  expect(costColor(1, { low: 1, high: 4 })).toBe("hsl(210 70% 50%)");
  expect(costColor(9, { low: 1, high: 4 })).toBe("hsl(0 70% 50%)");
});

test("the cost legend passes through the same colors as the cells, not straight from blue to red", () => {
  const scale = { low: 0.13, high: 10.01 };
  const legend = costGradient(scale);
  // A cost in the middle of the scale: its cell's color is one of the legend's stops.
  expect(legend).toContain(costColor((scale.low + scale.high) / 2, scale));
  expect(legend.startsWith(`linear-gradient(90deg, ${costColor(scale.low, scale)}, `)).toBe(true);
  expect(legend.endsWith(`, ${costColor(scale.high, scale)})`)).toBe(true);
  expect(legend.split("hsl(").length - 1).toBe(7);
});
