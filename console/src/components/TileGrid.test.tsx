import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MhEvent, Task } from "../api";
import { theme } from "../theme";
import { TileGrid } from "./TileGrid";

const TASK = {
  id: "t", title: "Fix the bug", repo: "o/r", status: "running", pr_url: null, external_ref: "github:o/r#1", status_reason: null,
  acceptance_checks: [], created_at: "2026-11-10T09:00:00Z", updated_at: "2026-11-10T10:00:00Z",
} as unknown as Task;

const ev = (position: number, event_type: string, payload: Record<string, unknown>, run_id = "t:run-2"): MhEvent => ({
  position, event_id: String(position), event_type, time_utc: "2026-11-10T10:00:00Z", source: "run", actor: null, run_id, task_id: "t", payload,
});

const PROMPT = ev(10, "run_prompt", { run_id: "t:run-2", kind: "fix", template: "fix-v1", prompt: "Fix the review's findings." });
// The tile's backlog: the newest events of a long run, which no longer reach its prompt.
const BACKLOG = [ev(50, "turn_started", { turn: 7 }), ev(51, "turn_finished", { turn: 7, text: "Still working." })];

// The browser's EventSource as the grid's stream sees it: the test plays the server.
class Stream {
  static CLOSED = 2;
  static opened: Stream[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: ((m: MessageEvent) => void)[] = [];
  constructor() {
    Stream.opened.push(this);
  }
  addEventListener(_type: string, listener: (m: MessageEvent) => void) {
    this.listeners.push(listener);
  }
  emit(e: MhEvent) {
    const message = new MessageEvent("event", { data: JSON.stringify(e), lastEventId: String(e.position) });
    this.listeners.forEach((l) => l(message));
  }
  close() {
    this.readyState = Stream.CLOSED;
  }
}

class Visible {
  constructor(private callback: IntersectionObserverCallback) {}
  observe(node: Element) {
    this.callback([{ target: node, isIntersecting: true } as unknown as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("IntersectionObserver", Visible);
  Stream.opened = [];
  vi.stubGlobal("EventSource", Stream);
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function serve(prompts: MhEvent[] | null, backlog: MhEvent[] = BACKLOG): string[] {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url.includes("event_type=run_prompt")) {
      if (prompts === null) return Promise.resolve(new Response("{}", { status: 400 }));
      return Promise.resolve(new Response(JSON.stringify({ events: prompts, next_position: 99 }), { status: 200 }));
    }
    const body = url.startsWith("/v1/events") ? { events: backlog, latest_position: backlog[backlog.length - 1].position } : { base: "", ticket: null };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  return asked;
}

function renderGrid() {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <TileGrid tasks={[TASK]} size={4} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("a tile shows its run's prompt expanded above the run's output, though its backlog does not reach it", async () => {
  const asked = serve([ev(3, "run_prompt", { template: "repo-task-v1", prompt: "Old." }, "t:run-1"), PROMPT]);
  renderGrid();
  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("prompt (fix-v1)"), { timeout: 2000 });
  const details = log.querySelector("details")!;
  expect(details.open).toBe(true);
  expect(details.querySelector("pre")!.textContent).toBe("Fix the review's findings.");
  // The prompt heads the tile, and a run the tile no longer shows has none.
  expect(log.firstElementChild).toBe(details);
  expect(log.textContent).not.toContain("repo-task-v1");
  expect(log.textContent).toContain("Still working.");
  expect(asked.some((u) => u.includes("task_id=t") && u.includes("event_type=run_prompt"))).toBe(true);
});

test("a tile whose prompt cannot be read still shows its output", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
  serve(null);
  renderGrid();
  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("Still working."), { timeout: 2000 });
  expect(log.querySelector("details")).toBeNull();
});

test("a tile whose output spans a task run, a review and a fix shows each run's prompt above its output", async () => {
  const backlog = [
    ev(20, "turn_finished", { turn: 9, text: "Opened the pull request." }, "t:run-1"),
    ev(26, "turn_finished", { turn: 1, text: "Two findings." }, "t:run-x"),
    ev(31, "turn_started", { turn: 1 }),
    ev(32, "turn_finished", { turn: 1, text: "Fixing." }),
  ];
  const task = ev(3, "run_prompt", { run_id: "t:run-1", kind: "task", template: "repo-task-v1", prompt: "Do the task." }, "t:run-1");
  const review = ev(25, "run_prompt", { run_id: "t:run-x", kind: "review", template: "review-v1", prompt: "Review it." }, "t:run-x");
  serve([task, review, ev(30, "run_prompt", { run_id: "t:run-2", kind: "fix", template: "fix-v1", prompt: "Fix it." })], backlog);
  renderGrid();
  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.querySelectorAll("details").length).toBe(3), { timeout: 2000 });
  const shown = [...log.children].map((c) => c.textContent ?? "");
  // The task run's prompt is older than the backlog, and still heads that run's output.
  expect(shown[0]).toContain("prompt (repo-task-v1)");
  expect(shown[1]).toContain("Opened the pull request.");
  expect(shown[2]).toContain("prompt (review-v1)");
  expect(shown[3]).toContain("Two findings.");
  expect(shown[4]).toContain("prompt (fix-v1)");
  expect([...log.querySelectorAll("details")].every((d) => d.open)).toBe(true);
});

test("a tile keeps its run's prompt at the top however long the run streams", async () => {
  serve([PROMPT]);
  renderGrid();
  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("prompt (fix-v1)"), { timeout: 2000 });
  await waitFor(() => expect(Stream.opened.length).toBe(1));
  const stream = Stream.opened[0];
  act(() => {
    for (let i = 0; i < 400; i++) stream.emit(ev(100 + i, "turn_finished", { turn: 8 + i, text: `Step ${i}.` }));
  });
  await waitFor(() => expect(log.textContent).toContain("Step 399."));
  expect(log.firstElementChild!.tagName).toBe("DETAILS");
  expect(log.firstElementChild!.textContent).toContain("Fix the review's findings.");
  expect(log.textContent).not.toContain("Still working.");
  // A new run streams in with its prompt; the old run's prompt goes once its output has scrolled out.
  act(() => {
    stream.emit(ev(600, "run_prompt", { run_id: "t:run-3", kind: "review", template: "review-v1", prompt: "Review." }, "t:run-3"));
    for (let i = 0; i < 300; i++) stream.emit(ev(601 + i, "turn_finished", { turn: i, text: `Review ${i}.` }, "t:run-3"));
  });
  await waitFor(() => expect(log.textContent).toContain("Review 299."));
  expect(log.querySelectorAll("details").length).toBe(1);
  expect(log.firstElementChild!.textContent).toContain("prompt (review-v1)");
});
