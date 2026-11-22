import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { PlannerUpdates, since } from "./PlannerUpdates";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

const NOW = "2026-11-19T12:00:00Z";
const LATEST = {
  number: 2,
  headline: "Two of three tasks are in review.",
  body: "The store and API tasks opened pull requests. The UI task waits on the API.",
  needs_you: [{ what: "Review the API pull request.", ref: "github:o/r#12" }],
  next: "The UI task starts when the API merges.",
  created_at: NOW,
};
const pr = (n: number) => `https://github.com/o/r/pull/${n}`;
const TASKS: Record<string, unknown> = {
  "t-1": { id: "t-1", title: "CLI", status: "ready", pr_url: pr(9), max_usd: "5" },
  "t-2": { id: "t-2", title: "Export", status: "review_unresolved", pr_url: pr(10), max_usd: "5" },
  "t-3": { id: "t-3", title: "Question", status: "running", pr_url: null, max_usd: "5" },
  "t-4": { id: "t-4", title: "Broken", status: "failed", pr_url: null, max_usd: "5" },
};
const ISSUES = [{ ref: "jira:VL-9", repo: "o/r", state: "open", depends_on: [], refused_reason: null, held_reason: "it is large: 5 areas", task_id: null, task_status: null, task_status_reason: null, pr_url: null, pr_state: null, checks: null, split: [] }, { ref: "jira:VL-8", repo: "o/r", state: "open", depends_on: [], refused_reason: null, held_reason: "it is large: 4 areas", task_id: null, task_status: null, task_status_reason: null, pr_url: null, pr_state: null, checks: null, split: [] }];
const FIRST = { number: 1, headline: "The plan is ready for you.", body: "Three tasks, in order.", needs_you: [], next: "", created_at: "2026-11-19T11:00:00Z" };

// The panel over a fake API. reads: what each GET answers, the last one repeating. Returns the POSTs made.
function show(reads: unknown[], open = true, ask: () => Promise<Response> = () => respond({ update: LATEST }), planning = false) {
  const posts: string[] = [];
  let read = 0;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push(url);
      return ask();
    }
    // Each need's task, read for its state now, and the goal's issues, read for a held one's choices.
    const task = String(url).match(/^\/v1\/tasks\/(t-[\w-]+)$/);
    if (task) return respond(TASKS[task[1]] ?? { detail: "no such task" }, TASKS[task[1]] ? 200 : 404);
    if (String(url).startsWith("/v1/goals/g-1/issues")) return respond({ issues: ISSUES });
    return respond(reads[Math.min(read++, reads.length - 1)]);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <PlannerUpdates goalId="g-1" open={open} planning={planning} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return posts;
}

test("the latest update leads with its headline, what needs the operator and what is next", async () => {
  show([{ updates: [LATEST, FIRST], enabled: true, error: null }]);

  expect((await screen.findByTestId("planner-headline")).textContent).toBe("Two of three tasks are in review.");
  expect(screen.getByText(/The store and API tasks opened pull requests/)).toBeTruthy();
  const needs = screen.getByTestId("planner-needs-you");
  expect(needs.textContent).toContain("Needs you");
  expect(needs.textContent).toContain("Review the API pull request.");
  expect(needs.querySelector("a")?.getAttribute("href")).toBe("https://github.com/o/r/issues/12");
  expect(screen.getByText("The UI task starts when the API merges.")).toBeTruthy();

  expect(screen.getByRole("button", { name: "Ask for an update" }).style.flexShrink).toBe("0");

  // The ones before it are a click away.
  fireEvent.click(screen.getByRole("button", { name: "1 earlier update" }));
  expect(await screen.findByText("The plan is ready for you.")).toBeTruthy();
});

test("an open goal with no update yet says one is coming, and asking writes one now", async () => {
  const posts = show([{ updates: [], enabled: true, error: null }, { updates: [LATEST], enabled: true, error: null }]);

  expect(await screen.findByText(/Its first update is a minute or two away/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ask for an update" }));

  await waitFor(() => expect(posts).toEqual(["/v1/goals/g-1/updates"]));
  expect((await screen.findByTestId("planner-headline")).textContent).toBe("Two of three tasks are in review.");
});

test("an update that could not be written says why and what to do, and keeps the last one", async () => {
  show([{ updates: [LATEST], enabled: true, error: "UpdateError: the HTTP Gateway answered 503" }], true, () =>
    respond({ detail: "the update could not be written: the HTTP Gateway answered 503. Ask again in a moment" }, 502),
  );

  expect(await screen.findByText(/could not write its latest update \(UpdateError: the HTTP Gateway answered 503\)/)).toBeTruthy();
  expect(screen.getByTestId("planner-headline")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ask for an update" }));
  expect((await screen.findByTestId("planner-update-refused", {}, { timeout: 60000 })).textContent).toContain("Ask again in a moment");
}, 70000);

test("every need comes with the button that resolves it, from its task's state now", async () => {
  const needs = [
    { what: "Approve the plan.", ref: "", action: "approve_plan" },
    { what: "Merge the CLI change.", ref: "mh:i-1", action: "merge", task_id: "t-1", pr_url: pr(9) },
    // The model said "review", but the task's review ended with findings no fix settled: settle it here.
    { what: "Look at the export change.", ref: "mh:i-2", action: "review", task_id: "t-2" },
    { what: "Answer the agent's question.", ref: "mh:i-3", action: "open_task", task_id: "t-3" },
    { what: "Raise the budget.", ref: "", action: "raise_budget" },
    { what: "Decide on the held issue.", ref: "jira:VL-7", action: "open_issue" },
    { what: "The broken task failed.", ref: "mh:i-4", action: "retry", task_id: "t-4" },
    { what: "Split the big issue or run it as one.", ref: "jira:VL-8", action: "split_or_run" },
  ];
  const seen: string[] = [];
  const approve = () => seen.push("approve");
  const settings = () => seen.push("settings");
  window.addEventListener("mh:approve-plan", approve);
  window.addEventListener("mh:open-settings", settings);
  const posts = show([{ updates: [{ ...LATEST, needs_you: needs }], enabled: true, error: null }]);

  const rows = await screen.findAllByTestId("planner-need");
  expect(rows).toHaveLength(8);
  for (const row of rows) expect(row.querySelectorAll("a, button").length).toBeGreaterThan(0);

  fireEvent.click(within(rows[0]).getByRole("button", { name: "Approve the plan" }));
  fireEvent.click(within(rows[4]).getByRole("button", { name: "Raise the budget" }));
  expect(seen).toEqual(["approve", "settings"]);
  expect((await within(rows[1]).findByRole("link", { name: "See the changes" })).getAttribute("href")).toBe("/tasks/t-1?tab=changes");
  expect(await within(rows[2]).findByRole("button", { name: "Merge as is" })).toBeTruthy();
  expect(within(rows[2]).getByRole("link", { name: "See the findings" }).getAttribute("href")).toBe("/tasks/t-2?tab=reviews");
  expect(await within(rows[3]).findByText("Under way again.")).toBeTruthy();
  expect(within(rows[3]).getByRole("link", { name: "Open the task" }).getAttribute("href")).toBe("/tasks/t-3");
  expect(within(rows[5]).getByRole("button", { name: "Show the goal's issues" })).toBeTruthy();
  expect(await within(rows[7]).findByRole("button", { name: "Run anyway" })).toBeTruthy();
  expect(within(rows[7]).getByRole("button", { name: "Split" })).toBeTruthy();

  // Each resolves it on the spot, and the planner is asked for a fresh update at once.
  fireEvent.click(within(rows[2]).getByRole("button", { name: "Try another fix" }));
  await waitFor(() => expect(posts).toContain("/v1/tasks/t-2/retry"));
  fireEvent.click(await within(rows[6]).findByRole("button", { name: "Retry" }));
  await waitFor(() => expect(posts).toContain("/v1/tasks/t-4/retry"));
  fireEvent.click(within(rows[1]).getByRole("button", { name: "Merge" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Merge" }));
  await waitFor(() => expect(posts).toContain("/v1/tasks/t-1/merge"));
  expect(posts).toContain("/v1/goals/g-1/updates");

  // An update written before needs carried state-based actions still offers the choice for a held issue.
  cleanup();
  show([{ updates: [{ ...LATEST, needs_you: [{ what: "Say whether to split VL-9.", ref: "jira:VL-9", action: "open_issue" }] }], enabled: true, error: null }]);
  const [old] = await screen.findAllByTestId("planner-need");
  expect(await within(old).findByRole("button", { name: "Run anyway" })).toBeTruthy();
  expect(within(old).getByRole("button", { name: "Split" })).toBeTruthy();
  window.removeEventListener("mh:approve-plan", approve);
  window.removeEventListener("mh:open-settings", settings);
});

test("a fix the planner drafted is accepted or dismissed from the panel, with what it changes", async () => {
  const fix = { what: "I can run the api after the store so they don't clash. Accept?", ref: "", action: "accept_fix", changes: ["Build api now waits for Build store"] };
  let changed = 0;
  const onChanged = () => (changed += 1);
  window.addEventListener("mh:plan-changed", onChanged);
  const posts = show([{ updates: [{ ...LATEST, needs_you: [fix] }], enabled: true, error: null }], true, () => respond({}), true);

  const [row] = await screen.findAllByTestId("planner-need");
  expect(row.textContent).toContain("Build api now waits for Build store");
  fireEvent.click(within(row).getByRole("button", { name: "Accept the fix" }));
  const dialog = await screen.findByRole("dialog");
  expect(dialog.textContent).toContain("You still approve the plan before anything runs.");
  fireEvent.click(within(dialog).getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(posts).toContain("/v1/goals/g-1/suggestion/accept"));
  await waitFor(() => expect(changed).toBe(1));

  // Once the update asked for after the accept has arrived, the needs are shown again.
  const [again] = await screen.findAllByTestId("planner-need");
  fireEvent.click(within(again).getByRole("button", { name: "Dismiss" }));
  await waitFor(() => expect(posts).toContain("/v1/goals/g-1/suggestion/dismiss"));
  window.removeEventListener("mh:plan-changed", onChanged);
});

test("after a fix is accepted the panel says what is happening, in place of the button, until the new update arrives", async () => {
  const fix = { what: "I can run the api after the store. Accept?", ref: "", action: "accept_fix", changes: ["Build api now waits for Build store"] };
  // The update asked for after the accept is a model call: it is held here until the test lets it answer.
  let answer: (value: Response | PromiseLike<Response>) => void = () => undefined;
  let posted = 0;
  // The accept itself answers at once; the request for an update after it is the slow one.
  const slow = () => (posted++ === 0 ? respond({}) : new Promise<Response>((resolve) => { answer = resolve; }));
  show([{ updates: [{ ...LATEST, needs_you: [fix] }], enabled: true, error: null }], true, slow, true);

  const [row] = await screen.findAllByTestId("planner-need");
  fireEvent.click(within(row).getByRole("button", { name: "Accept the fix" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Accept" }));

  const told = await screen.findByTestId("planner-acted");
  expect(told.textContent).toContain("Done. I'm writing what changed");
  expect(screen.queryByRole("button", { name: "Accept the fix" })).toBeNull();
  answer(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
  await waitFor(() => expect(screen.queryByTestId("planner-acted")).toBeNull());
});

test("a need written before needs had actions still gets a button: on a goal being planned, the approval", async () => {
  show([{ updates: [{ ...LATEST, needs_you: [{ what: "Review and approve the plan.", ref: "" }] }], enabled: true, error: null }], true, undefined, true);

  const [row] = await screen.findAllByTestId("planner-need");
  expect(within(row).getByRole("button", { name: "Approve the plan" })).toBeTruthy();
});

test("nothing is shown with updates off, or on a closed goal that never had one", async () => {
  show([{ updates: [LATEST], enabled: false, error: null }]);
  await waitFor(() => expect(screen.queryByTestId("planner-updates")).toBeNull());
});

test("a need about a task kept in the meta-harness shows no ref beside it", async () => {
  show([{ updates: [{ ...LATEST, needs_you: [{ what: "Merge pull request #382.", ref: "mh:i-abdbf8c1cc27" }] }], enabled: true, error: null }]);

  const needs = await screen.findByTestId("planner-needs-you");
  expect(needs.textContent).toContain("Merge pull request #382.");
  expect(needs.textContent).not.toContain("Kept here");
});

test("a closed goal keeps its last update and offers no new one", async () => {
  show([{ updates: [LATEST], enabled: true, error: null }], false);

  expect(await screen.findByTestId("planner-headline")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Ask for an update" })).toBeNull();
});

test("an update's age reads as a person would say it", () => {
  const now = Date.parse(NOW);
  expect(since(NOW, now)).toBe("just now");
  expect(since("2026-11-19T11:59:00Z", now)).toBe("1 minute ago");
  expect(since("2026-11-19T11:15:00Z", now)).toBe("45 minutes ago");
  expect(since("2026-11-19T09:00:00Z", now)).toBe("3 hours ago");
});

test("an ended goal's last update asks for nothing: no need, no button, no next step", async () => {
  const need = { what: "Retry or drop the failed deploy check task.", ref: "", action: "retry", task_id: "t-1" };
  show([{ updates: [{ ...LATEST, needs_you: [need], next: "It will merge once its checks pass." }], enabled: true, error: null }], false);

  expect((await screen.findByTestId("planner-goal-over")).textContent).toContain("Nothing here needs you now");
  expect(screen.queryByTestId("planner-need")).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  expect(screen.queryByText(/It will merge once its checks pass/)).toBeNull();
});
