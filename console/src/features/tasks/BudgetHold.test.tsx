import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Task } from "../../api";
import { theme } from "../../theme";
import { BudgetHold } from "./BudgetHold";
import { LOOKED_AGAIN_MS, REREAD_MS } from "./budgetAnswer";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

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

test("a task held for its goal's budget has a field for the goal's new budget, links to the goal, and can look again or end", async () => {
  const { asked } = show({ ...HELD, status_reason: "review run 2 needs about $0.30, and goal g-1 has spent $9.95 of its $10 budget. Raise the goal's budget, or end the task" });
  expect(await screen.findByLabelText(/New goal budget/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Raise the goal's budget and carry on" })).toBeTruthy();
  asked.length = 0;
  expect(screen.getByRole("link", { name: "The goal's page" }).getAttribute("href")).toBe("/goals/g-1");
  expect(screen.queryByLabelText(/New spend limit/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "End the task" }));
  await waitFor(() => expect(asked).toHaveLength(1));
  expect(asked[0].body).toEqual({ action: "end" });
});

test("a task that is not held shows nothing", () => {
  show({ ...HELD, waiting_on: null });
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a hold that says what it asks for is granted in one click, and another limit can still be set", async () => {
  const request = { for: "task" as const, what: "fix run 4", needs_usd: "1.00", left_usd: "0.20", limit_usd: "10.00", add_usd: "1", to_usd: "11.00" };
  const { asked, answered } = show({ ...HELD, budget_request: request });
  expect(screen.getByRole("alert").textContent).toContain("this task asks for $1.00 more");
  // The other limit starts at what the request asks for, not at a guess.
  expect((screen.getByLabelText(/Or set another limit/) as HTMLInputElement).value).toBe("$11");

  fireEvent.click(screen.getByRole("button", { name: "Grant $1.00: its limit becomes $11.00" }));
  await waitFor(() => expect(answered).toHaveLength(1));
  expect(asked[0]).toEqual({ url: "/v1/tasks/t-1/budget", body: { action: "raise", max_usd: 11 } });
});

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

// The task's page as far as the hold goes: it keeps the task, and takes every answer and read of it.
function Page({ first }: { first: Task }) {
  const [task, setTask] = useState(first);
  return <BudgetHold task={task} onAnswered={setTask} />;
}

// Serves the answer (the task as the Task API returns it: still held, with its old reason, and any new limit)
// and each later read of the task: the next of reads each time, the last again once they run out.
function served(task: Task, reads: Partial<Task>[], other: (url: string) => Promise<Response> | null = () => null) {
  const asked: { url: string; body: any }[] = [];
  let read = 0;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    asked.push({ url, body });
    const elsewhere = other(url);
    if (elsewhere) return elsewhere;
    if (url === "/v1/tasks/t-1/budget") return json({ ...task, ...(body.max_usd ? { max_usd: String(body.max_usd) } : {}) }, 202);
    if (url === "/v1/tasks/t-1") return json({ ...task, ...reads[Math.min(read++, reads.length - 1)] });
    return json({ detail: "not here" }, 404);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <Page first={task} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return asked;
}

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const carriedOn = { max_usd: "15.00", waiting_on: null, status_reason: "the budget was raised; carrying on with fix run 4" };

test("once a raised limit is accepted, the alert gives way to what was sent, which clears when the task waits no more", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const asked = served(HELD, [{ max_usd: "15.00" }, carriedOn]);
  fireEvent.click(screen.getByRole("button", { name: "Raise the limit and carry on" }));
  expect((await screen.findByRole("status")).textContent).toBe("Limit raised to $15.00. The task continues at its next step.");
  expect(screen.queryByRole("alert")).toBeNull();
  // The scheduler has yet to hand the answer over: the task still reads as held, and the line stays.
  await pass(REREAD_MS);
  expect(screen.getByRole("status")).toBeTruthy();
  // The workflow took it: the task waits no more.
  await pass(REREAD_MS);
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  expect(screen.queryByRole("alert")).toBeNull();
  expect(asked.filter((a) => a.url === "/v1/tasks/t-1")).toHaveLength(2);
});

test("a task held for a new reason after its workflow looked again shows the alert again, with that reason", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const again = "fix run 4 needs about $6.00, and the task has $5.20 left of its $15.00 spend limit.";
  served(HELD, [{ max_usd: "15.00", status_reason: again }]);
  fireEvent.click(screen.getByRole("button", { name: "Raise the limit and carry on" }));
  await screen.findByRole("status");
  await pass(REREAD_MS);
  expect((await screen.findByRole("alert")).textContent).toContain("$5.20 left of its $15.00 spend limit");
  expect(screen.queryByRole("status")).toBeNull();
  // The field starts again above the limit now set.
  expect((screen.getByLabelText(/New spend limit/) as HTMLInputElement).value).toBe("$23");
});

test("a task held as before once its workflow has had time to look again shows the alert again", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  served(HELD, [{ max_usd: "15.00" }]);
  fireEvent.click(screen.getByRole("button", { name: "Raise the limit and carry on" }));
  await screen.findByRole("status");
  await pass(LOOKED_AGAIN_MS - REREAD_MS);
  expect(screen.getByRole("status")).toBeTruthy();
  await pass(REREAD_MS);
  expect((await screen.findByRole("alert")).textContent).toContain("fix run 4 needs about $1.00");
  expect(screen.queryByRole("status")).toBeNull();
});

test("Grant says what was sent in place of the alert", async () => {
  const request = { for: "task" as const, what: "fix run 4", needs_usd: "1.00", left_usd: "0.20", limit_usd: "10.00", add_usd: "1", to_usd: "11.00" };
  served({ ...HELD, budget_request: request }, [{}]);
  fireEvent.click(screen.getByRole("button", { name: "Grant $1.00: its limit becomes $11.00" }));
  expect((await screen.findByRole("status")).textContent).toBe("Limit raised to $11.00. The task continues at its next step.");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("End the task says what was sent in place of the alert", async () => {
  served(HELD, [{}]);
  fireEvent.click(screen.getByRole("button", { name: "End the task" }));
  expect((await screen.findByRole("status")).textContent).toBe("Sent: end the task. It ends at its next step.");
  expect(screen.queryByRole("alert")).toBeNull();
});

const FOR_GOAL = { ...HELD, status_reason: "review run 2 needs about $0.30, and goal g-1 has spent $9.95 of its $10 budget. Raise the goal's budget, or end the task" } as Task;
const GOAL = { id: "g-1", title: "Saves", status: "budget_exhausted", max_usd: "10.00", budget_mode: "block", spent_usd: "9.95", budget_resume_to: "20.00" };
const goalServed = (url: string) =>
  url === "/v1/goals/g-1"
    ? json(GOAL)
    : url === "/v1/goals/g-1/resume-budget"
      ? json({ goal: { ...GOAL, max_usd: "20.00" }, from: "10.00", to: "20.00", spent_usd: "9.95", how: "worked out", tasks_told: ["t-1"] })
      : null;

test("Look again now, on a task held for its goal's budget, says what was sent in place of the alert", async () => {
  const asked = served(FOR_GOAL, [{}], goalServed);
  await screen.findByLabelText(/New goal budget/);
  fireEvent.click(screen.getByRole("button", { name: "Look again now" }));
  expect((await screen.findByRole("status")).textContent).toBe("Sent: look again at the goal's budget. The task continues at its next step if the budget allows it.");
  expect(asked.find((a) => a.url === "/v1/tasks/t-1/budget")!.body).toEqual({ action: "raise" });
});

test("raising the goal's budget from the task's page says what was sent in place of the alert", async () => {
  served(FOR_GOAL, [{}], goalServed);
  await screen.findByLabelText(/New goal budget/);
  fireEvent.click(screen.getByRole("button", { name: "Raise the goal's budget and carry on" }));
  expect((await screen.findByRole("status")).textContent).toBe("The goal's budget raised to $20.00. The task continues at its next step.");
  expect(screen.queryByRole("alert")).toBeNull();
});
