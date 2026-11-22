// Created by the meta-harness in run t-33d72d7e8075:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { retries } from "../../client";
import { theme } from "../../theme";
import { Overview } from "./Overview";
import { FIRST_LOOK_MS, WorkReport } from "./WorkReport";

const report = {
  headline: "Two goals are moving.", moving: [{ goal_id: "g-1", goal: "Add exports", what: "A pull request opened." }],
  not_moving: [], needs_you: [], spend: "Spend is within budgets.", finished: "", goals: 2, written_at: "2026-11-09T10:00:00Z",
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => { window.localStorage.clear(); retries.baseMs = 0; });
afterEach(() => { vi.unstubAllGlobals(); retries.baseMs = 400; });

function renderPanel(handler: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => { calls.push({ url, init }); return Promise.resolve(handler(url, init)); });
  render(<MantineProvider theme={theme}><WorkReport /></MantineProvider>);
  return calls;
}

test("the panel shows the report and sends since from the last visit", async () => {
  window.localStorage.setItem("mh:overview:last-visit", "2026-11-08T09:00:00.000Z");
  const calls = renderPanel(() => json({ report, written: true }));
  expect(await screen.findByText("Two goals are moving.")).toBeTruthy();
  expect(screen.getByText(/Add exports: A pull request opened\./)).toBeTruthy();
  expect(screen.getByTestId("work-report-written").textContent).toContain("Written");
  const url = new URL(calls[0].url, "http://x");
  expect(url.pathname).toBe("/v1/overview/report");
  expect(url.searchParams.get("since")).toBe("2026-11-08T09:00:00.000Z");
  expect(Date.parse(window.localStorage.getItem("mh:overview:last-visit")!)).toBeGreaterThan(Date.parse("2026-11-08T09:00:00.000Z"));
});

test("a first visit asks for a report covering the last day", async () => {
  const calls = renderPanel(() => json({ report, written: true }));
  await screen.findByText("Two goals are moving.");
  const since = new URL(calls[0].url, "http://x").searchParams.get("since");
  expect(since).toBeTruthy();
  const age = Date.now() - Date.parse(since!);
  expect(age).toBeGreaterThanOrEqual(FIRST_LOOK_MS);
  expect(age).toBeLessThan(FIRST_LOOK_MS + 60_000);
});

test("the report is the first thing on the Overview", async () => {
  vi.stubGlobal("fetch", (url: string) =>
    Promise.resolve(url.startsWith("/v1/overview/report") ? json({ report, written: false }) : json({ detail: "not here" }, 404)));
  render(<MantineProvider theme={theme}><MemoryRouter><Overview /></MemoryRouter></MantineProvider>);
  await screen.findByText("Two goals are moving.");
  const panel = screen.getByTestId("work-report");
  const follows = (a: Node, b: Node) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  // The page's title, then the report, then everything else.
  expect(follows(screen.getByRole("heading", { name: "Overview" }), panel)).toBe(true);
  for (const label of ["Needs you", "Running", "Open goals"]) {
    for (const el of screen.getAllByText(label)) expect(follows(panel, el)).toBe(true);
  }
});

test("a failed refresh keeps the last report and says so", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    let n = 0;
    renderPanel(() => (n++ === 0 ? json({ report, written: false }) : json({ detail: "down" }, 400)));
    await screen.findByText("Two goals are moving.");
    await vi.advanceTimersByTimeAsync(61_000);
    expect((await screen.findByRole("alert")).textContent).toContain("Could not refresh the report");
    expect(screen.getByText("Two goals are moving.")).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});

test("a question shows its answer, and a 429 shows its message", async () => {
  let asked = 0;
  const calls = renderPanel((url) => {
    if (url.startsWith("/v1/overview/report")) return json({ report, written: false });
    return ++asked === 1
      ? json({ answer: "Exports merged today.", in_record: true })
      : json({ detail: "No more questions until the limit resets at 2026-11-10T00:00:00+00:00 (midnight UTC)." }, 429);
  });
  await screen.findByText("Two goals are moving.");
  const box = screen.getByLabelText("Ask about the work");
  fireEvent.change(box, { target: { value: "What merged today?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText("Exports merged today.")).toBeTruthy();
  const post = calls.find((c) => c.init?.method === "POST")!;
  expect(JSON.parse(String(post.init!.body)).question).toBe("What merged today?");

  fireEvent.change(box, { target: { value: "And yesterday?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByTestId("work-ask-error").textContent).toContain("limit resets at 2026-11-10"));
  expect(screen.queryByText("Exports merged today.")).toBeNull();
});
