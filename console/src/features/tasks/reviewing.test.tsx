import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import type { MhEvent, Task } from "../../api";
import { inboxItems } from "../../components/Inbox";
import { theme } from "../../theme";
import { ReviewBanner, ago, reviewsInProgress } from "./reviewing";

let n = 0;
const ev = (event_type: string, run_id: string, payload: Record<string, unknown> = {}, task_id = "t-1"): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: "2026-11-11T14:00:00Z", source: "", actor: null, run_id, task_id, payload,
});
const TASK = { id: "t-1", title: "Fix it", status: "running", pr_url: "https://github.com/o/r/pull/9" } as Task;

test("a review run that has started and not ended is in progress, and only the task's newest", () => {
  const events = [
    ev("run_prompt", "t-1:run-1", { kind: "task" }),
    ev("attempt_finished", "t-1:run-1"),
    ev("run_prompt", "t-1:run-2", { kind: "review" }),
    ev("attempt_finished", "t-1:run-2"),
    ev("run_prompt", "t-1:run-3", { kind: "fix" }),
    ev("attempt_finished", "t-1:run-3"),
    ev("run_prompt", "t-1:run-4", { kind: "review" }),
    ev("run_prompt", "t-2:run-2", { kind: "review" }, "t-2"),
    ev("attempt_finished", "t-2:run-2", {}, "t-2"),
  ];
  expect(reviewsInProgress(events)).toEqual([{ task_id: "t-1", run_id: "t-1:run-4", since: "2026-11-11T14:00:00Z" }]);
  expect(ago("2026-11-11T14:00:00Z", Date.parse("2026-11-11T14:07:10Z"))).toBe("7 minutes ago");
});

test("a task's page says an agent is reviewing its pull request, and offers the diff to review it too", () => {
  const events = [ev("run_prompt", "t-1:run-2", { kind: "review" })];
  const { rerender } = render(<MantineProvider theme={theme}><MemoryRouter><ReviewBanner task={TASK} events={events} /></MemoryRouter></MantineProvider>);
  expect(screen.getByTestId("review-banner").textContent).toContain("An agent is reviewing this pull request now");
  expect(screen.getByRole("link", { name: "Open the diff" }).getAttribute("href")).toBe("/tasks/t-1?tab=changes");
  expect(screen.queryByRole("button", { name: "Take over this review" })).toBeNull();

  rerender(<MantineProvider theme={theme}><MemoryRouter><ReviewBanner task={TASK} events={events} onChanged={() => undefined} /></MemoryRouter></MantineProvider>);
  expect(screen.getByRole("button", { name: "Take over this review" })).toBeTruthy();

  rerender(<MantineProvider theme={theme}><MemoryRouter><ReviewBanner task={TASK} events={[...events, ev("attempt_finished", "t-1:run-2")]} /></MemoryRouter></MantineProvider>);
  expect(screen.queryByTestId("review-banner")).toBeNull();
});

test("the inbox announces each review in progress once, with the way into it", () => {
  const review = { task_id: "t-1", run_id: "t-1:run-2", since: "2026-11-11T14:00:00Z" };
  const items = inboxItems([], [], null, Date.now(), [{ review, task: TASK }]);
  expect(items).toEqual([{ key: "review-t-1:run-2", text: "An agent is reviewing: Fix it", to: "/tasks/t-1?tab=changes" }]);
});
