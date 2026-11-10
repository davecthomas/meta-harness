import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { Terminal } from "../../components/Terminal";
import { describe, type Line } from "../../terminal";
import { theme } from "../../theme";
import { Timeline, entries } from "./Timeline";

let n = 0;
const ev = (event_type: string, payload: Record<string, unknown>, run_id = "t:run-1"): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: "2026-11-10T10:00:00Z", source: "run", actor: null, run_id, task_id: "t", payload,
});

const PROMPT = "You are working in owner/repo.\n\nFix the bug in app.py.\n\nAcceptance checks:\n- pytest -q";
const EVENTS = [
  ev("run_prompt", { run_id: "t:run-1", kind: "task", template: "repo-task-v1", prompt: PROMPT }),
  ev("turn_started", { turn: 1 }),
  ev("turn_finished", { turn: 1, text: "Fixed it." }),
  ev("run_finished", { status: "FINISHED", end_reason: "done" }),
  ev("run_prompt", { run_id: "t:run-2", kind: "review", template: "review-v1", prompt: "Review the pull request." }, "t:run-2"),
  ev("turn_started", { turn: 1 }, "t:run-2"),
];

test("each run's prompt heads its run on the timeline, before its turns", () => {
  const list = entries(EVENTS);
  expect(list.map((e) => e.kind)).toEqual(["prompt", "turn", "status", "prompt", "turn"]);
  expect(list[0]).toMatchObject({ kind: "prompt", template: "repo-task-v1", prompt: PROMPT, runKind: "task" });
  expect(list[3]).toMatchObject({ template: "review-v1", runKind: "review" });
});

test("a run started before prompts were recorded shows no prompt", () => {
  const list = entries([ev("turn_started", { turn: 1 }), ev("run_finished", { status: "FINISHED" })]);
  expect(list.some((e) => e.kind === "prompt")).toBe(false);
});

test("the timeline's Prompt item opens to the full prompt", () => {
  render(
    <MantineProvider theme={theme}>
      <Timeline events={EVENTS.slice(0, 4)} />
    </MantineProvider>,
  );
  const item = screen.getByTestId("prompt");
  expect(item.textContent).toContain("Prompt");
  expect(item.textContent).toContain("repo-task-v1");
  const toggle = item.querySelector("button")!;
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(item.textContent).toContain("Fix the bug in app.py.");
});

test("the terminal shows the prompt as one collapsed line that expands to the full text", () => {
  const lines = EVENTS.map(describe).filter((l): l is Line => l !== null);
  render(<Terminal lines={lines} />);
  const summary = screen.getByText("10:00:00 prompt (repo-task-v1)");
  const details = summary.closest("details")!;
  expect(details.open).toBe(false);
  expect(details.querySelector("pre")!.textContent).toBe(PROMPT);
  fireEvent.click(summary);
  expect(details.open).toBe(true);
  expect(screen.getByText("10:00:00 prompt (review-v1)")).toBeTruthy();
});
