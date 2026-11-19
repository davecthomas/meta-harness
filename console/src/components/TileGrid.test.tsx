import { render, screen, waitFor } from "@testing-library/react";
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

const ev = (position: number, event_type: string, payload: Record<string, unknown>): MhEvent => ({
  position, event_id: String(position), event_type, time_utc: "2026-11-10T10:00:00Z", source: "run", actor: null, run_id: "t:run-2", task_id: "t", payload,
});

const PROMPT = ev(10, "run_prompt", { run_id: "t:run-2", kind: "fix", template: "fix-v1", prompt: "Fix the review's findings." });
// The tile's backlog: the newest events of a long run, which no longer reach its prompt.
const BACKLOG = [ev(50, "turn_started", { turn: 7 }), ev(51, "turn_finished", { turn: 7, text: "Still working." })];

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
  vi.stubGlobal("EventSource", class { static CLOSED = 2; addEventListener() {} close() {} });
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function serve(prompts: MhEvent[] | null): string[] {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url.includes("event_type=run_prompt")) {
      if (prompts === null) return Promise.resolve(new Response("{}", { status: 400 }));
      return Promise.resolve(new Response(JSON.stringify({ events: prompts, next_position: 99 }), { status: 200 }));
    }
    const body = url.startsWith("/v1/events") ? { events: BACKLOG, latest_position: 51 } : { base: "", ticket: null };
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
  const asked = serve([ev(3, "run_prompt", { template: "repo-task-v1", prompt: "Old." }), PROMPT]);
  renderGrid();
  const log = await screen.findByRole("log");
  await waitFor(() => expect(log.textContent).toContain("prompt (fix-v1)"), { timeout: 2000 });
  const details = log.querySelector("details")!;
  expect(details.open).toBe(true);
  expect(details.querySelector("pre")!.textContent).toBe("Fix the review's findings.");
  // The prompt heads the tile, and only the run it is showing has one.
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
