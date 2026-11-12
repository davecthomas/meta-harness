import { fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test, vi } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { TaskPath } from "./TaskPath";

let n = 0;
const ev = (event_type: string, minute: number, payload: Record<string, unknown> = {}, run_id: string | null = null): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: new Date(Date.parse("2026-11-11T18:00:00Z") + minute * 60_000).toISOString(),
  source: "", actor: null, run_id, task_id: "t", payload,
});

const EVENTS = [
  ev("task_created", 0, { status: "queued" }),
  ev("task_admitted", 1),
  ev("run_prompt", 1, { kind: "task", template: "repo-task-v1", prompt: "Do the work" }, "t:run-1"),
  ev("turn_started", 2, { turn: 1 }, "t:run-1"),
  ev("turn_finished", 3, { turn: 1, text: "I changed the file." }, "t:run-1"),
  ev("attempt_finished", 11, { attempt: 1, tier: "complex", candidate: "harness:pi-careful/claude:claude-opus-5-5", decision: "finished", spent_usd: "3.5" }, "t:run-1"),
  ev("pull_request_opened", 11, { pr_url: "https://github.com/o/r/pull/9", files: ["a"] }),
  ev("pull_request_checks", 15, { state: "passed", failed: [] }),
  ev("run_prompt", 15, { kind: "review" }, "t:run-2"),
  ev("attempt_finished", 16, { attempt: 2, tier: "review", candidate: "harness:pi-review/openai-responses:gpt-6-sol", decision: "finished", spent_usd: "0.2" }, "t:run-2"),
  ev("pull_request_reviewed", 16, { round: 1, verdict: "changes", findings: 2 }),
  ev("run_prompt", 16, { kind: "fix" }, "t:run-3"),
];

function show(more: Partial<Parameters<typeof TaskPath>[0]> = {}) {
  const props = { events: EVENTS, onOpenRun: vi.fn(), onOpenReviews: vi.fn(), onPickRun: vi.fn(), ...more };
  render(
    <MantineProvider theme={theme}>
      <TaskPath {...props} />
    </MantineProvider>,
  );
  return props;
}

test("the path is drawn in rows, with the return a review's findings caused and where the task is now", () => {
  show();
  expect(within(screen.getByTestId("path-row-0")).getByText("Review round 1")).toBeTruthy();
  const second = screen.getByTestId("path-row-1");
  expect(within(second).getByText("Fix run")).toBeTruthy();
  expect(within(second).getByText("Now")).toBeTruthy();
  expect(screen.getByTestId("path-back").textContent).toContain("2 findings to fix");
  expect(screen.getByTestId("path-totals").textContent).toMatch(/2 runs|3 runs/);
  expect(screen.getByText(/10m · \$3\.50/)).toBeTruthy();
});

test("a run's step opens to its turns, and to its terminal", () => {
  const props = show();
  fireEvent.click(screen.getByTestId("step-t:run-1"));
  expect(props.onPickRun).toHaveBeenCalledWith("t:run-1");
  const picked = screen.getByTestId("picked");
  expect(within(picked).getByText("I changed the file.")).toBeTruthy();
  fireEvent.click(within(picked).getByText("Open its terminal"));
  expect(props.onOpenRun).toHaveBeenCalledWith("t:run-1");
});

test("a review's step opens its round, and a pull request's step links to it", () => {
  const props = show();
  fireEvent.click(screen.getByTestId("step-t:run-2"));
  fireEvent.click(screen.getByText("Open its review round"));
  expect(props.onOpenReviews).toHaveBeenCalled();
  fireEvent.click(screen.getByText("Pull request #9 opened"));
  expect((screen.getByText("Open the pull request") as HTMLAnchorElement).href).toBe("https://github.com/o/r/pull/9");
});

test("a link that names a run opens on that run's step, and no events is said plainly", () => {
  show({ pick: "t:run-1" });
  expect(within(screen.getByTestId("picked")).getByText("I changed the file.")).toBeTruthy();
  show({ events: [] });
  expect(screen.getByText("Nothing recorded yet.")).toBeTruthy();
});
