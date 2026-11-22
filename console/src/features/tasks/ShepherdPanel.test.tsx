import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Task, TaskStanding } from "../../api";
import { theme } from "../../theme";
import { ShepherdPanel } from "./ShepherdPanel";
import { TaskFailure } from "./TaskFailure";

afterEach(() => vi.unstubAllGlobals());

const PR = "https://github.com/o/r/pull/437";

function standing(over: Partial<TaskStanding>): TaskStanding {
  return {
    owner: "operator", situation: "review_unresolved_conflict",
    reason: "its review loop ended without a clean review, and its pull request conflicts with its base",
    ask: "Resolve the conflict and merge: a fix run resolves it, and it merges once a review is clean.",
    since: "2026-11-20T22:55:44Z", checked_at: "2026-11-21T10:00:00Z", attempts: 0, last_error: null,
    moves: ["resolve_and_merge", "retry_stronger", "cancel"], saw: "4 of its 12 reviews passed, and the last left 2 findings open",
    ...over,
  };
}

function show(over: Partial<TaskStanding>, status = "review_unresolved", failure = false) {
  const task = { id: "t-1", status, status_reason: "the review loop used its 3 fix rounds", pr_url: PR, goal_id: "g-1", complexity: "high", max_usd: "20.00", external_ref: "jira:ABC-1", standing: standing(over) } as unknown as Task;
  const sent: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    sent.push(`${init?.method ?? "GET"} ${url} ${init?.body ?? ""}`.trim());
    return new Response(JSON.stringify(task), { status: 202, headers: { "content-type": "application/json" } });
  }));
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <ShepherdPanel task={task} status={status}>
          {failure && <TaskFailure task={task} status={status} events={[]} />}
        </ShepherdPanel>
      </MemoryRouter>
    </MantineProvider>,
  );
  return sent;
}

test("a task that waits on a person says so under the Task Shepherd's mark, with what it saw", () => {
  show({});

  const panel = screen.getByTestId("shepherd-panel");
  expect(screen.getByTestId("voice-mark-shepherd").getAttribute("aria-label")).toBe("Task Shepherd");
  expect(panel.textContent).toContain("I need you on this one.");
  expect(panel.textContent).toContain("Its review loop ended without a clean review, and its pull request conflicts with its base.");
  expect(panel.textContent).toContain("4 of its 12 reviews passed, and the last left 2 findings open.");
});

test("a task nobody has is said to be over, not to be the Goal Planner's", () => {
  // A canceled goal's task: its record names the planner, and "the Goal Planner has it" would be false.
  show({ owner: "planner", situation: "canceled", reason: "the task and its goal were canceled, and its pull request stays open on the code host", over: true }, "canceled");
  expect(screen.getByText("Nothing for you to do: this task is over.")).toBeTruthy();
  expect(screen.queryByText(/Goal Planner has it/)).toBeNull();
});

test("a task the Goal Planner's sweep will move still says the Goal Planner has it", () => {
  show({ owner: "planner", situation: "blocked", reason: "it waits on o/r#7, which is being worked on" }, "blocked");
  expect(screen.getByText("Nothing for you to do: the Goal Planner has it.")).toBeTruthy();
});

test("the moves are buttons in the supervisor's order, the first one filled and marked as recommended", () => {
  show({});

  const buttons = Array.from(screen.getByTestId("shepherd-moves").querySelectorAll("button, a")).map((b) => b.textContent);
  expect(buttons).toEqual(["Resolve the conflict and merge (recommended)", "Retry with a stronger model", "Cancel the task"]);
  expect(screen.getByRole("button", { name: "Resolve the conflict and merge (recommended)" }).getAttribute("data-variant")).toBe("filled");
  expect(screen.queryByText(/Read the last review/)).toBeNull();
  expect(screen.queryByText(/Merge as it is/)).toBeNull();
});

test("Resolve the conflict and merge asks first, says what will run and what it may spend, then asks for the merge", async () => {
  const sent = show({});

  fireEvent.click(screen.getByRole("button", { name: "Resolve the conflict and merge (recommended)" }));
  expect(await screen.findByText(/A fix run resolves the conflict with the base first.*can spend up to \$20\.00/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Resolve the conflict and merge" }));

  await waitFor(() => expect(sent.some((s) => s.startsWith("POST /api/v1/tasks/t-1/merge") || s.includes("/v1/tasks/t-1/merge"))).toBe(true));
});

test("Retry with a stronger model sends the task back on the stronger tier", async () => {
  const sent = show({});

  fireEvent.click(screen.getByRole("button", { name: "Retry with a stronger model" }));

  await waitFor(() => expect(sent.some((s) => s.includes("/v1/tasks/t-1/retry") && s.includes('"complexity":"high"'))).toBe(true));
});

test("the failure's own message sits inside the panel and offers no second set of buttons", () => {
  show({}, "review_unresolved", true);

  const panel = screen.getByTestId("shepherd-panel");
  expect(panel.textContent).toContain("The review loop ended without a clean review");
  expect(screen.queryByText("What to do:")).toBeNull();
  expect(screen.getAllByRole("button", { name: /Retry with a stronger model/ })).toHaveLength(1);
});

test("a task the Shepherd is moving says what it is doing, in the first person, with no buttons", () => {
  show({ owner: "supervisor", situation: "merge_due", reason: "the code host would take its merge now", ask: null, attempts: 2, moves: [], saw: "" }, "ready");

  const panel = screen.getByTestId("shepherd-panel");
  expect(panel.textContent).toContain("I have this one.");
  expect(panel.textContent).toContain("The code host would take its merge now. I have acted on it 2 times. I look again within a minute.");
  expect(screen.queryByTestId("shepherd-moves")).toBeNull();
});

test("a running task gets one quiet line, and an ask with no ranked moves still says what to do", () => {
  show({ owner: "workflow", situation: "working", reason: "its RunTask workflow is running", ask: null, moves: [], saw: "" }, "running");
  expect(screen.getByTestId("shepherd-panel").textContent).toContain("Nothing for you to do: the task is running.");

  show({ situation: "budget_hold", reason: "its workflow is held: the task reached its spend limit, or its goal's budget", ask: "Raise its spend limit or its goal's budget to carry on, or end the task.", moves: [], saw: "" }, "running");
  expect(screen.getAllByText(/Raise its spend limit or its goal's budget/)).toHaveLength(1);
});

test("a closed task has no panel", () => {
  show({}, "closed");

  expect(screen.queryByTestId("shepherd-panel")).toBeNull();
});
