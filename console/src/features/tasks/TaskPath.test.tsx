import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { About, TaskPath } from "./TaskPath";
import { steps } from "./path";

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

test("a run's box opens its terminal at once", () => {
  const props = show();
  fireEvent.click(screen.getByTestId("step-t:run-1"));
  expect(props.onOpenRun).toHaveBeenCalledWith("t:run-1");
  expect(screen.queryByTestId("picked")).toBeNull();
});

test("a review's box opens its round, checks open the Checks tab, and a pull request opens its changes", () => {
  const onOpenTab = vi.fn();
  const props = show({ onOpenTab });
  fireEvent.click(screen.getByTestId("step-t:run-2"));
  expect(props.onOpenReviews).toHaveBeenCalledWith(1);
  fireEvent.click(screen.getAllByText("Checks passed")[0]);
  expect(onOpenTab).toHaveBeenLastCalledWith("checks");
  fireEvent.click(screen.getByText("Pull request #9 opened"));
  expect(onOpenTab).toHaveBeenLastCalledWith("changes");
  expect(screen.queryByTestId("picked")).toBeNull();
});

test("a wait's box, which has nowhere else to go, opens below the path", () => {
  show();
  const wait = screen.getAllByRole("button").find((b) => b.getAttribute("data-testid")?.startsWith("step-") && /Queued|Waiting/.test(b.textContent ?? ""));
  fireEvent.click(wait!);
  expect(screen.getByTestId("picked")).toBeTruthy();
});

test("a link that names a run opens on that run's step, and no events is said plainly", () => {
  show({ pick: "t:run-1" });
  expect(within(screen.getByTestId("picked")).getByText("I changed the file.")).toBeTruthy();
  show({ events: [] });
  expect(screen.getByText(/Nothing has happened yet: the task has not started/)).toBeTruthy();
});

test("the path's cost is the task's spend total when the page gives it one", () => {
  show({ usd: 12.34 });
  expect(screen.getByTestId("path-totals").textContent).toContain("$12.34");
});

afterEach(() => vi.useRealTimers());

test("a step that is still going counts its time by the second", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-11T18:16:05Z"));
  show();
  const going = () => screen.getAllByText(/so far$/).map((e) => e.textContent);
  expect(going()).toContain("5s so far");

  act(() => void vi.advanceTimersByTime(3000));
  expect(going()).toContain("8s so far");
  act(() => void vi.advanceTimersByTime(60_000));
  expect(going()).toContain("1m 8s so far");
});


test("a box's hover adds what the box does not show: a review's summary and findings, a run's plan, never its run id", () => {
  const events = [
    ...EVENTS.slice(0, 3),
    ev("plan_updated", 4, { steps: [{ step: "List the refs", status: "done" }, { step: "Wire the picker", status: "in_progress" }] }, "t:run-1"),
    ...EVENTS.slice(3, 9),
    ev("review_submitted", 16, { summary: "The picker works, but a typed name is accepted unchecked.", findings: [{ path: "console/src/x.tsx", line: 42, body: "Refuse a name the list lacks." }] }, "t:run-2"),
    ...EVENTS.slice(9),
  ];
  const path = steps(events);
  const text = (key: string) => {
    const { container, unmount } = render(<MantineProvider theme={theme}><About step={path.find((s) => s.key === key)!} /></MantineProvider>);
    const found = container.textContent ?? "";
    unmount();
    return found;
  };
  const review = text("t:run-2");
  expect(review).toContain("The picker works, but a typed name is accepted unchecked.");
  expect(review).toContain("console/src/x.tsx:42: Refuse a name the list lacks.");
  expect(review).toContain("Click to open round 1 in Reviews.");
  expect(review).not.toContain("t:run-2");
  expect(review).not.toContain("harness:pi-review");
  const run = text("t:run-1");
  expect(run).toContain("Its plan, 1 of 2 done:");
  expect(run).toContain("✓ List the refs");
  expect(run).not.toContain("$3.50");
});

test("a title with a word longer than its box, such as an email address, breaks inside the box", () => {
  show({ events: [...EVENTS, ev("task_retried", 20, { max_usd: "20.00" }), { ...ev("task_status_changed", 20, { from: "failed", to: "queued", reason: "a.very.long.name@a-long-company-name.example retried it" }), actor: "a.very.long.name@a-long-company-name.example" }] });

  for (const title of screen.getAllByTestId("step-title")) {
    expect(title.style.overflowWrap).toBe("anywhere");
    expect(title.style.minWidth).toBe("0");
  }
});
