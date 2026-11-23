import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { PullRequestsRow } from "../features/overview/PullRequestsRow";
import { SupervisorHealth } from "../features/overview/SupervisorHealth";
import { TodaySpend } from "../features/overview/Overview";
import { theme } from "../theme";

// A section that could not read what it lists says so. It never shows nothing, or its empty state, in place of
// a failure: "none" must mean there are none.

afterEach(() => vi.unstubAllGlobals());

test.each([
  ["the Task Manager's state", <SupervisorHealth key="s" />, /Could not read how the Task Manager is doing, so the list below may be missing something: the planner did not answer/],
  ["the pull requests", <PullRequestsRow key="p" />, /Could not read the pull requests: the planner did not answer\. Reload the page to try again\./],
  ["today's spend", <TodaySpend key="t" />, /Could not read today's spend: the planner did not answer/],
])("a failed read of %s is said, with what to do", async (_, section, said) => {
  vi.stubGlobal("fetch", () =>
    Promise.resolve(new Response(JSON.stringify({ detail: "the planner did not answer" }), { status: 502, headers: { "Content-Type": "application/json" } })),
  );
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>{section}</MemoryRouter>
    </MantineProvider>,
  );
  expect((await screen.findByTestId("read-failed")).textContent).toMatch(said);
});
