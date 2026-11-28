import { fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import type { RepoHealth } from "../../api";
import { ReposPage } from "./ReposPage";
import { FailingCount, RepoMarker } from "./RepoMarker";
import { forgetRepoHealth } from "./health";

const RED: RepoHealth = {
  repo: "acme/api", registered: false, default_checks: [], deploy_check: "",
  base: { state: "failing", commit: "4c1e9d0aaaa", checks: [{ command: "npm test", passed: false, on: "sandbox" }], failing: ["npm test"], goal_id: "g-1", found_by: "Add CSV export" },
  fix: { asked: false, goal_id: "g-1", ref: "mh:1", task_id: "t-fix", open: true },
  usual_seconds: 500, reruns: [{ check: "integration tests", times: 4 }],
  goals: [{ id: "g-1", title: "Add CSV export" }], tasks: 2, pulls: 1, live: null,
};
const GREEN: RepoHealth = {
  repo: "acme/web", registered: true, default_checks: ["uv run ruff check ."], deploy_check: "deploy",
  base: { state: "passing", commit: "49dcecd0000", checks: [{ command: "uv run ruff check .", passed: true, on: "code_host" }], failing: [], goal_id: "", found_by: "" },
  fix: null, usual_seconds: 315, reruns: [], goals: [{ id: "g-2", title: "Tidy the docs" }], tasks: 3, pulls: 2,
  live: { task_id: "t-9", title: "Plan page: an answer field", at: "2026-11-28T12:00:00Z", deploy_check: "deploy" },
};
const IDLE: RepoHealth = { ...GREEN, repo: "acme/idle", base: null, goals: [], tasks: 0, pulls: 0, live: null, deploy_check: "" };

function Where() {
  const at = useLocation();
  return <div data-testid="where">{at.pathname + at.search}</div>;
}

function serve(repos: RepoHealth[]) {
  vi.stubGlobal("fetch", (url: string) => {
    const body = url.startsWith("/v1/repos/health")
      ? { repos, failing: repos.filter((r) => r.base?.state === "failing").length }
      : url.startsWith("/v1/config")
        ? { kinds: [], source: "db" }
        : {};
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
}

function show(at = "/repos") {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="*" element={<><ReposPage /><FailingCount /><RepoMarker repo="ACME/api" /><Where /></>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

beforeEach(forgetRepoHealth);
afterEach(() => vi.unstubAllGlobals());

test("each repo says how its base stands, its checks, the work there and what went live", async () => {
  serve([RED, GREEN, IDLE]);
  show();

  const red = within(await screen.findByTestId("repo-health-acme/api"));
  expect(red.getByTestId("repo-base").textContent).toBe("Base fails 1 check, a fix is running");
  expect(red.getByText("npm test")).toBeTruthy();
  expect(red.getByText('at 4c1e9d0, found by "Add CSV export"')).toBeTruthy();
  expect(red.getByText("Usually 8m 20s")).toBeTruthy();
  expect(red.getByText("integration tests: rerun 4 times this week")).toBeTruthy();
  expect(red.getByText("2 tasks running, 1 open pull request")).toBeTruthy();
  expect(red.getByRole("link", { name: "Open the fix task" }).getAttribute("href")).toBe("/tasks/t-fix");
  expect(red.getByRole("link", { name: "Name one" }).getAttribute("href")).toBe("/repos?tab=settings&repo=acme%2Fapi");

  const green = within(screen.getByTestId("repo-health-acme/web"));
  expect(green.getByTestId("repo-base").textContent).toBe("Base passes");
  expect(green.getByText("Passes on the code host, so not run in the sandbox")).toBeTruthy();
  expect(green.getByRole("link", { name: "Plan page: an answer field" }).getAttribute("href")).toBe("/tasks/t-9");
  expect(green.getByText("uv run ruff check .")).toBeTruthy();
  expect(green.getByRole("link", { name: "Edit settings" })).toBeTruthy();

  const idle = within(screen.getByTestId("repo-health-acme/idle"));
  expect(idle.getByText("No goal is working here. A task's next start checks its base.")).toBeTruthy();
});

test("the sidebar counts the failing repos, and the marker beside a repo reads the same health", async () => {
  serve([RED, GREEN]);
  show();

  expect((await screen.findByTestId("repos-failing")).textContent).toBe("1 failing");
  // The marker finds the repo whatever the case of its name, and leads to the Repos page.
  const markers = await screen.findAllByTestId("repo-base");
  expect(markers.at(-1)?.textContent).toBe("Base fails 1 check, a fix is running");
  expect(markers.at(-1)?.closest("a")?.getAttribute("href")).toBe("/repos");
});

test("Settings is a tab of the page, and Add repository opens it with its dialog", async () => {
  serve([GREEN]);
  show();

  await screen.findByTestId("repo-health-acme/web");
  fireEvent.click(screen.getByRole("link", { name: "Add repository" }));
  expect(screen.getByTestId("where").textContent).toBe("/repos?tab=settings&add=1");
  expect(screen.getByRole("tab", { name: "Settings" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("tab", { name: "Health" }));
  expect(screen.getByTestId("where").textContent).toBe("/repos");
});

test("with no repo it says when one shows here", async () => {
  serve([]);
  show();
  expect((await screen.findByTestId("no-repo-health")).textContent).toContain("once a goal works in it, or once it has settings");
});

test("under a repo picker only the chosen repos whose base fails are named, with their marker", async () => {
  const { FailingBases } = await import("./RepoMarker");
  serve([RED, GREEN]);
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <FailingBases repos={["acme/api", "acme/web", "acme/unknown"]} />
      </MemoryRouter>
    </MantineProvider>,
  );
  const lines = await screen.findByTestId("failing-bases");
  expect(lines.textContent).toContain("acme/api");
  expect(lines.textContent).not.toContain("acme/web");
  expect(lines.textContent).toContain("its tasks are told what already fails");
});
