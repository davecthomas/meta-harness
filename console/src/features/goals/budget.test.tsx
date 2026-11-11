import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Goal } from "../../api";
import { theme } from "../../theme";
import { BudgetAlert, BudgetMark, BudgetRow, ResumeBudget, budgetStanding, budgetWords } from "./budget";
import { GoalProgress } from "./GoalBoard";

afterEach(() => vi.unstubAllGlobals());

const goal = (fields: Partial<Goal> = {}) =>
  ({
    id: "g-1", title: "Saves", statement: "x", status: "active", max_usd: "10.0000", budget_mode: "block",
    budget_overage_percent: 10, budget_block_at: "11.00", budget_state: "ok", spent_usd: "0.0000", budget_resume_to: null,
    ...fields,
  }) as Goal;

const inPage = (ui: React.ReactNode) => render(<MantineProvider theme={theme}><MemoryRouter>{ui}</MemoryRouter></MantineProvider>);

test("a goal stands ok, over or blocked against its budget, by what its runs have spent", () => {
  expect(budgetStanding(goal(), 9.99)).toBe("ok");
  expect(budgetStanding(goal(), 10.4)).toBe("over");
  expect(budgetWords(goal(), 10.4)).toBe("over budget, stops at $11.00");
  expect(budgetStanding(goal(), 11)).toBe("blocked");
  expect(budgetWords(goal({ status: "budget_exhausted" }), 3)).toBe("stopped by its budget");
  const warns = goal({ budget_mode: "warn", budget_block_at: null });
  expect(budgetStanding(warns, 500)).toBe("over");
  expect(budgetWords(warns, 500)).toBe("over budget, and it keeps going");
  expect(budgetStanding(goal({ max_usd: null }), 500)).toBe("ok");
  // With no spend added up on the page, the planner's own answer.
  expect(budgetStanding(goal({ budget_state: "over" }))).toBe("over");
});

test("the spend line says where a goal past its budget stands", () => {
  const issues = [{ ref: "github:o/r#1", repo: "o/r", state: "open", depends_on: [], refused_reason: null, task_id: "t-1", task_status: "running", task_status_reason: null, pr_url: null, pr_state: null, checks: null }];
  inPage(<GoalProgress issues={issues} spend={{ "t-1": 10.4 }} budget="10" goal={goal()} />);
  expect(screen.getByTestId("goal-progress").textContent).toContain("$10.40 spent of $10.00: over budget, stops at $11.00");
});

test("a list marks a goal that is over its budget or stopped by it, and no other", () => {
  inPage(
    <>
      <BudgetMark goal={goal()} />
      <BudgetMark goal={goal({ budget_state: "over" })} />
      <BudgetMark goal={goal({ status: "budget_exhausted", budget_state: "blocked" })} />
    </>,
  );
  expect(screen.getAllByText(/budget$/).map((e) => e.textContent)).toEqual(["over budget", "stopped by budget"]);
});

test("a stopped goal's alert resumes it at the worked-out budget in one step", async () => {
  const sent: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    sent.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const answer = { goal: goal({ max_usd: "45" }), from: "10.0000", to: "45", spent_usd: "26.79", how: "worked out", tasks_told: [] };
    return Promise.resolve(new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  const resumed = vi.fn();
  const stopped = goal({ status: "budget_exhausted", budget_state: "blocked", spent_usd: "26.7900", budget_resume_to: "45.0000" });
  inPage(<BudgetAlert goal={stopped} onResumed={resumed} />);

  expect(screen.getByTestId("budget-alert").textContent).toContain("$26.79 spent of $10.00: stopped by its budget");
  fireEvent.click(screen.getByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", { name: "Raise to $45.00 and carry on" }));

  await waitFor(() => expect(resumed).toHaveBeenCalled());
  // The suggested budget left as it was: the planner works the amount out, and records how.
  expect(sent).toEqual([{ url: "/v1/goals/g-1/resume-budget", body: {} }]);
});

test("resume sends the operator's own amount when they change it, and reads the goal when given only its id", async () => {
  const sent: { url: string; body: unknown }[] = [];
  const stopped = goal({ status: "budget_exhausted", budget_state: "blocked", spent_usd: "26.7900", budget_resume_to: "45.0000" });
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    sent.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const answer = url.endsWith("/resume-budget") ? { goal: stopped, from: "10", to: "60", spent_usd: "26.79", how: "", tasks_told: [] } : { ...stopped, issues: [] };
    return Promise.resolve(new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  inPage(<ResumeBudget goalId="g-1" />);

  fireEvent.click(screen.getByRole("button", { name: "Resume" }));
  const input = await screen.findByLabelText("Raise the budget from $10.00 to");
  fireEvent.change(input, { target: { value: "60" } });
  fireEvent.click(await screen.findByRole("button", { name: "Raise to $60.00 and carry on" }));

  await waitFor(() => expect(sent.length).toBe(2));
  expect(sent).toEqual([{ url: "/v1/goals/g-1", body: null }, { url: "/v1/goals/g-1/resume-budget", body: { add_usd: "50.00" } }]);
});

test("a goal whose budget only warns says it keeps going, and offers nothing to resume", () => {
  inPage(<BudgetAlert goal={goal({ budget_mode: "warn", budget_block_at: null, budget_state: "over", spent_usd: "12.5" })} onResumed={() => undefined} />);
  expect(screen.getByTestId("budget-alert").textContent).toContain("over budget, and it keeps going");
  expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
});

test("the budget, what happens when it is reached, and the overage share one row", () => {
  const { container, rerender } = inPage(<BudgetRow goal={goal()} readOnly={false} save={async () => undefined} />);
  const row = container.querySelector('[data-testid="budget-row"]')!;
  expect(row.textContent).toContain("$10.00");
  expect(row.textContent).toContain("Stop");
  expect(row.textContent).toContain("10% over, stops at $11.00");
  rerender(
    <MantineProvider theme={theme}><MemoryRouter><BudgetRow goal={goal({ budget_mode: "warn", budget_block_at: null })} readOnly={false} save={async () => undefined} /></MemoryRouter></MantineProvider>,
  );
  expect(container.textContent).toContain("Warn and keep going");
  expect(container.textContent).not.toContain("Overage");
});
