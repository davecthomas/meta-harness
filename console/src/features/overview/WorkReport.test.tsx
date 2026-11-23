// Created by the meta-harness in run t-33d72d7e8075:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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

// Stands in for a goal's page: it says where the link led.
function Arrived() {
  const at = useLocation();
  return <div data-testid="arrived">{at.pathname + at.search}</div>;
}

beforeEach(() => { window.localStorage.clear(); retries.baseMs = 0; });
afterEach(() => { vi.unstubAllGlobals(); retries.baseMs = 400; });

function renderPanel(handler: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => { calls.push({ url, init }); return Promise.resolve(handler(url, init)); });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<WorkReport />} />
          <Route path="/goals/:id" element={<Arrived />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  return calls;
}

test("the panel shows the report and sends since from the last visit", async () => {
  window.localStorage.setItem("mh:overview:last-visit", "2026-11-08T09:00:00.000Z");
  const calls = renderPanel(() => json({ report, written: true }));
  expect(await screen.findByText("Two goals are moving.")).toBeTruthy();
  expect(screen.getByTestId("report-row").textContent).toBe("Add exports: A pull request opened.");
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

test("a row that needs the operator carries the button that does it, read from the goal's records", async () => {
  const needing = {
    ...report,
    needs_you: [
      { goal_id: "g-2", goal: "Keep tasks through compaction", what: "Check the context limits in the plan.", why: "Nothing starts until you sign off." },
      { goal_id: "g-3", goal: "Add imports", what: "Its pull request is ready.", why: "" },
    ],
    not_moving: [{ goal_id: "g-2", goal: "Keep tasks through compaction", why: "Its plan waits for you." }, { goal_id: "g-4", goal: "Add search", why: "It waits its turn." }],
  };
  const actions = {
    "g-2": [{ what: "", ref: "", action: "approve_plan", task_id: "", pr_url: "", goal_id: "g-2", label: "mh:g-2" }],
    "g-3": [{ what: "", ref: "mh:i-1", action: "merge", task_id: "t-1", pr_url: "https://github.com/o/r/pull/9", goal_id: "g-3", label: "mh:g-3" }],
    "g-4": [],
  };
  const calls = renderPanel((url) =>
    url.startsWith("/v1/tasks/t-1") ? json({ id: "t-1", title: "Imports", status: "ready", pr_url: "https://github.com/o/r/pull/9", max_usd: "5" }) : json({ report: needing, written: true, actions }));

  const rows = await screen.findAllByTestId("report-row");
  // The goal's name is a link to its page, and its first row has its button.
  expect(within(rows[0]).getByRole("link", { name: "Keep tasks through compaction" }).getAttribute("href")).toBe("/goals/g-2");
  expect(within(rows[0]).getByRole("button", { name: "Approve the plan" })).toBeTruthy();
  // A task's button acts where it is read, from the task as it is now.
  expect(await within(rows[1]).findByRole("button", { name: "Merge" })).toBeTruthy();
  // A goal named again gets its button once, and one that needs nothing gets the way to its page.
  expect(within(rows[3]).queryByRole("button")).toBeNull();
  expect(within(rows[4]).getByRole("link", { name: "Open the goal" }).getAttribute("href")).toBe("/goals/g-4");
  // The moving rows ask nothing, so they carry no button.
  expect(within(rows[2]).queryByTestId("report-actions")).toBeNull();

  // Approving is the goal page's to do: the button opens it there, with the approval asked for.
  fireEvent.click(within(rows[0]).getByRole("button", { name: "Approve the plan" }));
  expect((await screen.findByTestId("arrived")).textContent).toBe("/goals/g-2?do=approve");
  expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
});

test("an answer carries the buttons for what it suggests, each beside the goal it is about", async () => {
  const actions = [
    { what: "", ref: "", action: "open_goal", says: "plan", task_id: "", pr_url: "", goal_id: "g-2", label: "mh:g-2", goal: "Keep tasks through compaction" },
    { what: "", ref: "", action: "approve_plan", task_id: "", pr_url: "", goal_id: "g-2", label: "mh:g-2", goal: "Keep tasks through compaction" },
  ];
  renderPanel((_url, init) =>
    init?.method === "POST" ? json({ answer: "Check the context limits in the plan, then approve it.", in_record: true, actions }) : json({ report, written: true }));
  await screen.findByText("Two goals are moving.");
  fireEvent.change(screen.getByLabelText("Ask about the work"), { target: { value: "What should I follow up on?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  const [open, approve] = await screen.findAllByTestId("answer-action");
  expect(open.textContent).toContain("Keep tasks through compaction");
  expect(within(open).getByRole("link", { name: "Open the plan" }).getAttribute("href")).toBe("/goals/g-2");
  fireEvent.click(within(approve).getByRole("button", { name: "Approve the plan" }));
  expect((await screen.findByTestId("arrived")).textContent).toBe("/goals/g-2?do=approve");
});

test("an answer that suggests no step has no button", async () => {
  renderPanel((_url, init) => (init?.method === "POST" ? json({ answer: "Nothing is waiting.", in_record: true, actions: [] }) : json({ report, written: true })));
  await screen.findByText("Two goals are moving.");
  fireEvent.change(screen.getByLabelText("Ask about the work"), { target: { value: "Anything waiting?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  await screen.findByText("Nothing is waiting.");
  expect(screen.queryByTestId("answer-actions")).toBeNull();
});
