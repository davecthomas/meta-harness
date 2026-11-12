import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { MhEvent } from "../../api";
import { Terminal } from "../../components/Terminal";
import { describe, type Line } from "../../terminal";
import { theme } from "../../theme";
import { Timeline, entries } from "./Timeline";
import { TaskPage, merge } from "./TaskPage";

afterEach(() => vi.unstubAllGlobals());

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

test("merging events keeps each once, in position order", () => {
  const [a, b, c] = EVENTS;
  expect(merge([b, c], [a, b]).map((e) => e.position)).toEqual([a.position, b.position, c.position]);
  const held = [a, b];
  expect(merge(held, [b])).toBe(held);
});

const TASK = {
  id: "t", title: "Fix the bug", repo: "o/r", status: "running", pr_url: null, external_ref: "github:o/r#1", status_reason: null,
  acceptance_checks: ["pytest -q"], created_at: "2026-11-10T09:00:00Z", updated_at: "2026-11-10T10:00:00Z",
};

// Serves the task page with run-2's events alone as the first page; run-1's prompt is only found by
// asking for every run_prompt. Returns the URLs asked for.
function serveNewestOnly(): string[] {
  const newest = EVENTS.slice(4);
  const asked: string[] = [];
  vi.stubGlobal("EventSource", class { static CLOSED = 2; addEventListener() {} close() {} });
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    const body = url === "/v1/tasks/t" ? TASK
      : url.includes("event_type=run_prompt") ? { events: EVENTS.filter((e) => e.event_type === "run_prompt"), next_position: 99 }
      : url.startsWith("/v1/events") ? { events: newest, latest_position: newest.at(-1)!.position }
      : { base: "", ticket: null };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  return asked;
}

function renderTaskPage(path: string) {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/tasks/:taskId" element={<TaskPage />} /></Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the task page shows an older run's prompt the first page of events does not reach", async () => {
  const asked = serveNewestOnly();
  renderTaskPage("/tasks/t?run=t:run-1&tab=timeline");

  await waitFor(() => expect(screen.getAllByTestId("prompt")).toHaveLength(1));
  expect(screen.getByTestId("prompt").textContent).toContain("repo-task-v1");
  expect(asked.some((u) => u.includes("task_id=t") && u.includes("event_type=run_prompt"))).toBe(true);

  // The run's Terminal opens on the same prompt, though none of its other lines were loaded.
  fireEvent.click(screen.getByText("Terminal"));
  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("prompt (repo-task-v1)"));
  expect(log.querySelector("details pre")!.textContent).toBe(PROMPT);
  expect(log.textContent).not.toContain("review-v1");
});

test("the task page's Terminal merges an older prompt in position order, each prompt once", async () => {
  serveNewestOnly();
  renderTaskPage("/tasks/t");

  // The page opens on the task's path; a prompt shows there once its run's step is picked.
  await screen.findByTestId("task-path");
  fireEvent.click(screen.getByText("Terminal"));
  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("prompt (repo-task-v1)"));
  // run-1's prompt, read with every prompt, sits above run-2's, which came in both reads but shows once.
  const summaries = [...log.querySelectorAll("summary")].map((s) => s.textContent);
  expect(summaries).toEqual(["10:00:00 prompt (repo-task-v1)", "10:00:00 prompt (review-v1)"]);
  expect([...log.children].map((c) => c.textContent).at(-1)).toContain("turn 1");
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
