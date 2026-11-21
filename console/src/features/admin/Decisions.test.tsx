import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { Decisions, describeDecision } from "./Decisions";

afterEach(() => vi.unstubAllGlobals());

const ev = (event_type: string, payload: Record<string, unknown>, actor: string | null = null): MhEvent =>
  ({ position: 1, event_id: event_type, event_type, time_utc: "2026-11-21T20:00:00Z", source: "", actor, run_id: null, task_id: "t-1", payload }) as MhEvent;

test("each decision reads as a sentence: the model picked, how a run ended, a verdict, a merge, an unsticking", () => {
  expect(describeDecision(ev("task_routed", { tier: "complex", method: "classifier", confidence: 0.82, candidates: ["harness:pi-careful/claude:claude-opus-5-5"] })))
    .toBe("Routed to the complex tier by the classifier, 82% sure. First choice: pi-careful · claude-opus-5-5");
  expect(describeDecision(ev("attempt_finished", { tier: "fix", candidate: "harness:pi-careful/claude:claude-opus-5-5", decision: "finished", spent_usd: "0.42" })))
    .toBe("A fix run on pi-careful · claude-opus-5-5 finished, $0.42");
  expect(describeDecision(ev("pull_request_reviewed", { round: 3, verdict: "changes", findings: 1, reviewer: "harness:pi-review/openai-responses:gpt-6-sol" })))
    .toBe("Review round 3 by pi-review · gpt-6-sol asked for changes (1 finding)");
  expect(describeDecision(ev("pull_request_merged", { pr_url: "https://github.com/o/r/pull/9" }))).toBe("Merged o/r/pull/9");
  expect(describeDecision(ev("remedy_applied", { did: "split it into 2 smaller issues", because: "the issue is too large for one pull request" }, "planner")))
    .toBe("The Goal Planner: split it into 2 smaller issues, because the issue is too large for one pull request");
});

test("the list reads every kind of decision at once, and a kind picked reads only that kind", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(decodeURIComponent(url));
    return Promise.resolve(new Response(JSON.stringify({ events: [ev("pull_request_merged", { pr_url: "https://github.com/o/r/pull/9" })] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(<MantineProvider theme={theme}><MemoryRouter><Decisions /></MemoryRouter></MantineProvider>);

  expect(await screen.findByText("Merged o/r/pull/9")).toBeTruthy();
  expect(asked[0]).toContain("event_type=task_routed,attempt_finished,pull_request_reviewed,pull_request_merged,remedy_applied");
  fireEvent.click(screen.getByText("Routing"));
  await waitFor(() => expect(asked.at(-1)).toContain("event_type=task_routed&"));
});
