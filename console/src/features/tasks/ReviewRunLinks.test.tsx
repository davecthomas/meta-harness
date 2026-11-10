import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { TaskPage, tabOf } from "./TaskPage";

afterEach(() => vi.unstubAllGlobals());

const TASK = {
  id: "t", title: "Fix the bug", repo: "o/r", status: "ready", pr_url: "https://github.com/o/r/pull/9", external_ref: "github:o/r#1",
  status_reason: null, acceptance_checks: [], created_at: "2026-11-10T09:00:00Z", updated_at: "2026-11-10T10:00:00Z",
};
const at = "2026-11-10T10:00:00Z";
const tool = (position: number, run: string, command: string) =>
  ({ position, event_type: "tool_call", task_id: "t", run_id: run, time_utc: at, payload: { tool: "bash", args: { command } } });
const EVENTS = [tool(1, "t:run-1", "write the change"), tool(2, "t:run-2", "read the diff to review"), tool(3, "t:run-3", "apply the fix")];
const ROUNDS = [{
  round: 1, verdict: "changes", reviewer: "harness:pi-review/x:y", head_sha: "abc1234ff", summary: "One gap.", findings: [],
  review_url: null, reviewed_at: null, run_id: "t:run-2", next: { kind: "fix", run_id: "t:run-3", attempt: 3 },
}];

function serve() {
  vi.stubGlobal("EventSource", class { static CLOSED = 2; addEventListener() {} close() {} });
  vi.stubGlobal("fetch", (url: string) => {
    const body = url === "/v1/tasks/t" ? TASK
      : url === "/v1/tasks/t/reviews" ? { rounds: ROUNDS }
      : url.includes("event_type=run_prompt") ? { events: [], next_position: 0 }
      : url.startsWith("/v1/events") ? { events: EVENTS, latest_position: 3 }
      : { base: "", ticket: null };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/tasks/t?tab=reviews"]}>
        <Routes><Route path="/tasks/:taskId" element={<TaskPage />} /></Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("Sent to fix run N opens the Terminal on that fix run alone", async () => {
  serve();
  const card = await screen.findByTestId("review-1");
  fireEvent.click(within(card).getByRole("link", { name: /fix run 3/ }));

  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("apply the fix"));
  expect(log.textContent).not.toContain("read the diff to review");
  expect(log.textContent).not.toContain("write the change");
  expect(screen.getByText("t:run-3")).toBeTruthy();
});

test("Show review run opens the Terminal on the review's own run, and Show every run brings the rest back", async () => {
  serve();
  const card = await screen.findByTestId("review-1");
  fireEvent.click(within(card).getByRole("link", { name: "Show review run" }));

  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("read the diff to review"));
  expect(log.textContent).not.toContain("apply the fix");

  fireEvent.click(screen.getByRole("button", { name: "Show every run" }));
  await waitFor(() => expect(screen.getByRole("log").textContent).toContain("apply the fix"));
  expect(screen.getByRole("log").textContent).toContain("write the change");
});

test("a URL names its tab; one that names only a run shows the Terminal; otherwise the Timeline", () => {
  expect(tabOf(new URLSearchParams("tab=reviews"))).toBe("reviews");
  expect(tabOf(new URLSearchParams("run=t:run-3"))).toBe("terminal");
  expect(tabOf(new URLSearchParams("run=t:run-3&tab=timeline"))).toBe("timeline");
  expect(tabOf(new URLSearchParams("tab=nonsense"))).toBe("timeline");
  expect(tabOf(new URLSearchParams(""))).toBe("timeline");
});
