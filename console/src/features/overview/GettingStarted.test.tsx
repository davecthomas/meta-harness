import { render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { GettingStarted } from "./GettingStarted";
import { AdminProvider } from "../../components/Role";
import { theme } from "../../theme";

// A new deployment says what it needs before any work runs, in order, and stops saying it once it has it all.

afterEach(() => vi.unstubAllGlobals());

const tracker = (more: object) => ({ scheme: "github", title: "GitHub Issues", configured: true, signed_in: true, settings: [], goal_settings: [], credentials: [], missing_credentials: [], ...more });

function show(have: { repos?: number; goals?: number; tasks?: number; signedIn?: boolean }, admin = true) {
  vi.stubGlobal("fetch", (url: string) => {
    const body = url.startsWith("/v1/repos")
      ? { repos: Array.from({ length: have.repos ?? 0 }, (_, i) => ({ name: `o/r${i}` })) }
      : url.startsWith("/v1/trackers")
        ? { default: "github", trackers: [tracker({ signed_in: have.signedIn ?? true })] }
        : url.startsWith("/v1/goals")
          ? { goals: [], total: have.goals ?? 0 }
          : { tasks: [], total: have.tasks ?? 0 };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <AdminProvider value={admin}>
          <GettingStarted />
        </AdminProvider>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("a deployment with nothing yet lists the steps in order, the first one to do with its button", async () => {
  show({});

  const list = await screen.findByTestId("getting-started");
  expect(list.textContent).toContain("1 of 4 done");
  expect(within(screen.getByTestId("step-repo")).getByRole("link", { name: "Add a repository" }).getAttribute("href")).toBe("/config?kind=repos");
  expect(screen.getByTestId("step-tracker").getAttribute("data-done")).toBe("true");
  expect(within(screen.getByTestId("step-goal")).getByRole("link", { name: "New goal" }).getAttribute("href")).toBe("/goals/new");
  expect(screen.getByTestId("step-issue").textContent).toContain("Put the goal's label on an open issue");
});

test("the next step moves on as each is done, and someone who is not an admin is told to ask one", async () => {
  const first = show({ repos: 1 });
  expect((await screen.findByTestId("getting-started")).textContent).toContain("2 of 4 done");
  expect(within(screen.getByTestId("step-goal")).getByRole("link", { name: "New goal" })).toBeTruthy();
  first.unmount();

  show({ signedIn: false }, false);
  const repo = await screen.findByTestId("step-repo");
  expect(repo.textContent).toContain("Only an admin can do this: ask one to.");
  expect(within(repo).queryByRole("link")).toBeNull();
  expect(screen.getByTestId("step-tracker").textContent).toContain("its sign-in is missing");
});

test("once every step is done the checklist is gone", async () => {
  const { container } = show({ repos: 1, goals: 1, tasks: 3 });
  await new Promise((r) => setTimeout(r, 50));
  expect(container.querySelector('[data-testid="getting-started"]')).toBeNull();
});
