import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { GoalsPage } from "./GoalsPage";
import { budgetLine, sortGoals, totals, usedPercent, type CostRow } from "./goalCost";
import { theme } from "../../theme";

// Issue #296: each goal's cost beside its budget on the Goals page, with how much of the budget is used.

afterEach(() => vi.unstubAllGlobals());

const row = (id: string, cost: string | null, max: string | null, more: Partial<CostRow> = {}) => ({
  id,
  statement: `Goal ${id}`,
  title: `Goal ${id}`,
  repos: ["o/r"],
  label: "meta-harness:console-ux-and-a-long-label",
  modality: "fix",
  status: "canceled",
  auto_merge: false,
  max_usd: max,
  priority: 0,
  created_by: "dave",
  created_at: "2026-11-10T13:53:00Z",
  started_at: "2026-11-10T13:54:00Z",
  cost_usd: cost,
  cost_unknown: cost === null ? "ConnectionError: the event service did not answer" : null,
  ...more,
});

test("the words under a budget say how much is used, how far over, or that it stopped", () => {
  expect(budgetLine(row("a", "3.00", "30.00"))).toBe("10% used");
  expect(budgetLine(row("b", "34.10", "30.00"))).toBe("over by $4.10");
  expect(budgetLine(row("c", "5.00", "5.00", { status: "budget_exhausted" }))).toBe("stopped at its budget");
  expect(budgetLine(row("d", "2.00", null))).toBe("no budget");
  expect(budgetLine(row("e", null, "10.00"))).toBe("used: unknown");
  expect(usedPercent(row("f", "15.00", "10.00"))).toBe(150);
});

test("goals sort by cost or by budget used, with unknown costs and no budgets last", () => {
  const goals = [row("a", "1.00", "100.00"), row("b", null, "5.00"), row("c", "8.00", null), row("d", "4.00", "5.00")];
  expect(sortGoals(goals, "cost").map((g) => g.id)).toEqual(["c", "d", "a", "b"]);
  expect(sortGoals(goals, "used").map((g) => g.id)).toEqual(["d", "a", "b", "c"]);
  expect(sortGoals(goals, "newest").map((g) => g.id)).toEqual(["a", "b", "c", "d"]);
});

test("the totals add the known costs, count the unknown, and add budgets only when every goal has one", () => {
  expect(totals([row("a", "1.50", "10.00"), row("b", "2.25", "5.00")])).toEqual({ cost: 3.75, unknown: 0, budget: 15 });
  expect(totals([row("a", "1.50", "10.00"), row("b", null, null)])).toEqual({ cost: 1.5, unknown: 1, budget: null });
});

test("the Goals page shows each goal's cost and budget used, an unknown cost with why, and a total", async () => {
  vi.stubGlobal("fetch", () =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          goals: [row("g-1", "34.10", "30.00"), row("g-2", "0.42", null), row("g-3", null, "1.00")],
          total: 3,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals?show=all"]}>
        <Routes>
          <Route path="/goals" element={<GoalsPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

  expect(within(await screen.findByTestId("cost-g-1")).getByText("$34.10")).toBeTruthy();
  expect(within(screen.getByTestId("budget-g-1")).getByText("over by $4.10")).toBeTruthy();
  expect(within(screen.getByTestId("budget-g-2")).getByText("no budget")).toBeTruthy();
  expect(within(screen.getByTestId("cost-g-3")).getByText("unknown")).toBeTruthy();
  expect(screen.getByTestId("goal-totals").textContent).toBe(
    "3 goals listed: $34.52 spent, and 1 whose cost could not be read.",
  );
  // The full label shows: a badge grows with its text instead of cutting it off.
  expect(screen.getAllByText("meta-harness:console-ux-and-a-long-label").length).toBe(3);
  fireEvent.click(screen.getByRole("combobox", { name: "Sort goals" }));
  fireEvent.click(await screen.findByRole("option", { name: "Most expensive first", hidden: true }));
  const costs = screen.getAllByTestId(/^cost-/).map((cell) => cell.getAttribute("data-testid"));
  expect(costs).toEqual(["cost-g-1", "cost-g-2", "cost-g-3"]);
});
