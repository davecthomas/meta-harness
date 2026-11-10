import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const UNRESOLVED = { ...IN_REVIEW, id: "t-2", title: "Fix the crash", status: "review_unresolved", pr_url: "https://github.com/o/r/pull/105" };

function renderAt(path: string, asked: string[], posted: string[] = []) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push(url);
    if (init?.method === "POST") {
      posted.push(url);
      return respond({ ...UNRESOLVED, status: "canceled" });
    }
    const running = url.includes("status=running");
    const found = running ? [IN_REVIEW] : url.includes("review_unresolved") ? [UNRESOLVED] : [];
    return respond({ tasks: found, total: found.length });
  });
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <TasksPage />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("Running lists every task a workflow is working on, a running one with a pull request as in review", async () => {
  const asked: string[] = [];
  renderAt("/tasks?show=running&view=list", asked);

  expect(await screen.findByText("Add a root README")).toBeTruthy();
  expect(screen.getByText("in review")).toBeTruthy();
  expect(asked[0]).toContain("status=running%2Ctaken_over");
  expect(asked[0]).not.toContain("pr=");
  expect(screen.getByRole("link", { name: "#104" }).getAttribute("href")).toBe("https://github.com/o/r/pull/104");
});

test("Pull request open lists the tasks waiting on someone, and clears the ones picked", async () => {
  const asked: string[] = [];
  const posted: string[] = [];
  renderAt("/tasks?show=ready&view=list", asked, posted);

  expect(await screen.findByText("Fix the crash")).toBeTruthy();
  expect(asked[0]).toContain("status=ready%2Creview_unresolved");
  fireEvent.click(screen.getByRole("checkbox", { name: "Clear Fix the crash" }));
  fireEvent.click(screen.getByRole("button", { name: "Clear selected (1)" }));

  await waitFor(() => expect(posted).toEqual(["/v1/tasks/t-2/clear"]));
});
