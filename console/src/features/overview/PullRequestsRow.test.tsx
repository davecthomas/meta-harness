import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { PullRequests } from "../../api";
import { theme } from "../../theme";
import { PullRequestsRow } from "./PullRequestsRow";

afterEach(() => vi.unstubAllGlobals());

const pr = (task: string, state: PullRequests["prs"][number]["state"], run: string | null) => ({
  task_id: task, latest_run_id: run, title: `Task ${task}`, repo: "o/r", pr_url: `https://github.com/o/r/pull/${task.slice(2)}`,
  opened_at: "2026-11-09T10:00:00Z", state, state_at: "2026-11-09T11:00:00Z", fix_rounds: 1,
});

function renderRow(found: PullRequests) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return Promise.resolve(new Response(JSON.stringify(found), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<PullRequestsRow />} />
          <Route path="/tasks/:taskId" element={<p>task page</p>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  return asked;
}

test("each pull request is a cell colored by its state, with the blocked and waiting counts, and a click opens its latest run", async () => {
  const asked = renderRow({
    days: 91, total: 3, left_out: 0,
    prs: [pr("t-1", "merged", "r-1"), pr("t-2", "blocked", "r-9"), pr("t-3", "waiting", null)],
  });
  expect((await screen.findByTestId("cell-t-2")).getAttribute("data-state")).toBe("blocked");
  expect(screen.getByText("1 blocked · 1 waiting for you")).toBeTruthy();
  expect(asked[0]).toContain("/v1/activity");
  expect(screen.queryByText("Activity")).toBeNull();

  fireEvent.click(screen.getByTestId("cell-t-2"));
  expect(await screen.findByText("task page")).toBeTruthy();
});

test("with no pull request the row is not shown", async () => {
  const asked = renderRow({ days: 91, total: 0, left_out: 0, prs: [] });
  await vi.waitFor(() => expect(asked.length).toBe(1));
  expect(screen.queryByText("Pull requests")).toBeNull();
});
