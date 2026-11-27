import { render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { TaskPage } from "./TaskPage";

afterEach(() => vi.unstubAllGlobals());

const TASK = {
  id: "t", title: "Question answer buttons", repo: "o/r", status: "blocked", pr_url: null, external_ref: "github:o/r#7",
  depends_on: ["github:o/r#5", "github:o/r#6"], goal_id: null,
  status_reason: null, acceptance_checks: [], created_at: "2026-11-26T09:00:00Z", updated_at: "2026-11-26T10:00:00Z",
};

test("a blocked task says beside its badge which issues it waits for", async () => {
  vi.stubGlobal("EventSource", class { static CLOSED = 2; addEventListener() {} close() {} });
  vi.stubGlobal("fetch", (url: string) => {
    const body = url === "/v1/tasks/t" ? TASK : url.startsWith("/v1/events") ? { events: [], latest_position: 0 } : { base: "", ticket: null, rounds: [], events: [] };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/tasks/t"]}>
        <Routes><Route path="/tasks/:taskId" element={<TaskPage />} /></Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  const why = await screen.findByTestId("blocked-why");
  expect(why.textContent).toMatch(/^until.*#5.*and.*#6.*are closed$/);
  expect(within(why).getAllByRole("link")).toHaveLength(2);
});
