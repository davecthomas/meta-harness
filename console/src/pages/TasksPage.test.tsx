import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { TasksPage } from "./TasksPage";
import { theme } from "../theme";

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

const IN_REVIEW = {
  id: "t-1",
  title: "Add a root README",
  repo: "o/r",
  status: "running",
  pr_url: "https://github.com/o/r/pull/104",
  external_ref: "github:o/r#55",
  status_reason: "its pull request is open, in the review loop",
  created_at: "2026-11-08T20:40:00Z",
  updated_at: "2026-11-08T20:50:00Z",
};

function renderAt(path: string, asked: string[]) {
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return respond({ tasks: url.includes("pr=true") ? [IN_REVIEW] : [], total: url.includes("pr=true") ? 1 : 0 });
  });
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <TasksPage />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("Pull request open lists a running task whose pull request is open, as in review", async () => {
  const asked: string[] = [];
  renderAt("/tasks?show=ready&view=list", asked);

  expect(await screen.findByText("Add a root README")).toBeTruthy();
  expect(screen.getByText("in review")).toBeTruthy();
  expect(asked[0]).toContain("status=running%2Cready%2Creview_unresolved");
  expect(asked[0]).toContain("pr=true");
});

test("Running lists only tasks still on their first run, with no pull request", async () => {
  const asked: string[] = [];
  renderAt("/tasks?show=running&view=list", asked);

  expect(await screen.findByText(/No running tasks/i)).toBeTruthy();
  expect(asked[0]).toContain("status=running");
  expect(asked[0]).toContain("pr=false");
});
