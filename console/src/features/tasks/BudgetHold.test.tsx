import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Task } from "../../api";
import { theme } from "../../theme";
import { BudgetHold } from "./BudgetHold";

afterEach(() => vi.unstubAllGlobals());

const HELD = { id: "t-1", title: "Fix it", status: "running", waiting_on: "budget", max_usd: "10.00", goal_id: "g-1", status_reason: "fix run 4 needs about $1.00, and the task has $0.20 left of its $10.00 spend limit." } as Task;

function show(task: Task) {
  const asked: { url: string; body: any }[] = [];
  const answered: Task[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
    return Promise.resolve(new Response(JSON.stringify({ ...task, waiting_on: "budget" }), { status: 202, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <BudgetHold task={task} onAnswered={(t) => answered.push(t)} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return { asked, answered };
}

test("a task held for its own limit asks for a new limit, above the old one, and sends it", async () => {
  const { asked, answered } = show(HELD);
  expect(screen.getByRole("alert").textContent).toContain("fix run 4 needs about $1.00");
  const input = screen.getByLabelText(/New spend limit/) as HTMLInputElement;
  expect(input.value).toBe("$15");
  fireEvent.change(input, { target: { value: "8" } });
  expect((screen.getByRole("button", { name: "Raise the limit and carry on" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(input, { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "Raise the limit and carry on" }));
  await waitFor(() => expect(answered).toHaveLength(1));
  expect(asked[0]).toEqual({ url: "/v1/tasks/t-1/budget", body: { action: "raise", max_usd: 20 } });
});

test("a task held for its goal's budget links to the goal, and can look again or end", async () => {
  const { asked } = show({ ...HELD, status_reason: "review run 2 needs about $0.30, and goal g-1 has spent $9.95 of its $10 budget. Raise the goal's budget, or end the task" });
  expect(screen.getByRole("link", { name: /Raise the goal's budget/ }).getAttribute("href")).toBe("/goals/g-1");
  expect(screen.queryByLabelText(/New spend limit/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "End the task" }));
  await waitFor(() => expect(asked).toHaveLength(1));
  expect(asked[0].body).toEqual({ action: "end" });
});

test("a task that is not held shows nothing", () => {
  show({ ...HELD, waiting_on: null });
  expect(screen.queryByRole("alert")).toBeNull();
});
