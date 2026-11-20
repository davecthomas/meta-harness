import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { originText, provenanceOf, RunProvenance, runNumber } from "./Provenance";

const ev = (position: number, payload: Record<string, unknown>, eventId = `e-${position}`): MhEvent => ({
  position,
  event_id: eventId,
  event_type: "run_provenance",
  time_utc: "2026-11-20T00:00:00Z",
  source: "run",
  actor: null,
  run_id: String(payload.run_id),
  task_id: "t",
  payload,
});

const EVENTS: MhEvent[] = [
  ev(30, { run_id: "t:run-2", attempt: 2, kind: "review", candidate: "harness:pi-review/openai-responses:gpt-6-sol", follows: "t:run-1", workflow: 1, parent_task_id: "t-plan" }),
  ev(10, { run_id: "t:run-1", attempt: 1, kind: "task", candidate: "harness:pi-careful/claude:claude-opus-5-5", follows: null, workflow: 1, parent_task_id: "t-plan" }),
  // The same event read twice, as the first page and the full read both bring it.
  ev(10, { run_id: "t:run-1", attempt: 1, kind: "task", candidate: "harness:pi-careful/claude:claude-opus-5-5", follows: null, workflow: 1 }, "e-10"),
  ev(50, { run_id: "t:run-3", attempt: 3, kind: "fix", candidate: "harness:pi-careful/claude:claude-opus-5-5", follows: "t:run-2", workflow: 3 }),
  { ...ev(60, { run_id: "t:run-3" }), event_type: "run_prompt" },
];

test("each run's provenance, oldest first and once each", () => {
  const origins = provenanceOf(EVENTS);
  expect(origins.map((o) => [o.runId, o.kind, o.follows])).toEqual([
    ["t:run-1", "task", null],
    ["t:run-2", "review", "t:run-1"],
    ["t:run-3", "fix", "t:run-2"],
  ]);
  expect(runNumber("t:run-12")).toBe(12);
  expect(runNumber("nope")).toBeNull();
});

test("a run's provenance reads as a sentence", () => {
  const [first, review, fix] = provenanceOf(EVENTS);
  expect(originText(first)).toBe("Run 1, the task's own run, following no earlier run");
  expect(originText(review)).toBe("Run 2, a review run, after run 1");
  expect(originText(fix)).toBe("Run 3, a fix run, after run 2, in workflow 3, after 2 retries");
  expect(originText({ ...fix, workflow: 2 })).toBe("Run 3, a fix run, after run 2, in workflow 2, after one retry");
});

test("the task page lists each run and links the task that made this one", () => {
  render(<MantineProvider theme={theme}><MemoryRouter><RunProvenance events={EVENTS} /></MemoryRouter></MantineProvider>);
  expect(screen.getAllByTestId("run-origin")).toHaveLength(3);
  expect(screen.getByRole("link", { name: "t-plan" }).getAttribute("href")).toBe("/tasks/t-plan");
  expect(screen.getAllByTestId("candidate-name")[1].getAttribute("title")).toBe("harness:pi-review/openai-responses:gpt-6-sol");
});

test("a task with no recorded provenance says so", () => {
  render(<MantineProvider theme={theme}><MemoryRouter><RunProvenance events={[]} /></MemoryRouter></MantineProvider>);
  expect(screen.getByText(/None recorded yet/)).toBeTruthy();
});
