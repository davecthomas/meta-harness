import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { GoalSpend } from "./GoalSpend";

afterEach(() => vi.unstubAllGlobals());

test("a goal's spend shows how much went to own runs, reviews and fixes, and each task dearest first", async () => {
  const spend = {
    goal_id: "g-1", max_usd: "10.00", usd: "26.79", by_kind: { task: "16.00", review: "2.79", fix: "8.00" },
    tasks: [
      { task_id: "t-2", title: "Record prompts", status: "failed", usd: "10.15", by_kind: { task: "4.00", review: "1.20", fix: "4.95" }, runs: 9, turns: 240 },
      { task_id: "t-1", title: "Inline editing", status: "closed", usd: "6.50", by_kind: { task: "5.00", review: "0.50", fix: "1.00" }, runs: 4, turns: 90 },
    ],
  };
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return Promise.resolve(new Response(JSON.stringify(spend), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <GoalSpend goalId="g-1" goalName="Console UX" opened onClose={() => undefined} />
      </MemoryRouter>
    </MantineProvider>,
  );

  expect(await screen.findByText(/across 2 tasks/)).toBeTruthy();
  expect(asked).toEqual(["/v1/goals/g-1/spend"]);
  const kinds = screen.getByTestId("goal-spend-kinds").textContent ?? "";
  expect(kinds).toContain("Tasks' own runs: $16.00 (60%)");
  expect(kinds).toContain("Fixes: $8.00 (30%)");
  const dear = screen.getByTestId("goal-spend-t-2");
  expect(dear.textContent).toContain("$10.15");
  expect(dear.textContent).toContain("240");
  expect(screen.getByRole("link", { name: "Record prompts" }).getAttribute("href")).toBe("/tasks/t-2?tab=spend");
  // The window names its goal and links back to it, and the table ends with its totals.
  expect(screen.getByRole("link", { name: "Console UX" }).getAttribute("href")).toBe("/goals/g-1");
  const totals = screen.getByTestId("goal-spend-total").textContent ?? "";
  expect(totals).toContain("Total, 2 tasks");
  expect(totals).toContain("13");
  expect(totals).toContain("330");
  expect(totals).toContain("$16.00");
  expect(totals).toContain("$26.79");
});
