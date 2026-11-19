import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Goal, GoalPlan as Plan, PlanDraft, PlanItem } from "../../api";
import { planSentence, tasksOf } from "./PlanReview";
import { theme } from "../../theme";
import { ApprovedPlan, GoalPlan, inOrder, splitBody } from "./GoalPlan";

afterEach(() => vi.unstubAllGlobals());

const GOAL = { id: "g-1", title: "Add exports", statement: "Let users export.", repos: ["o/a"], label: "meta-harness:goal-g-1", status: "planning" } as Goal;

function draft(draft_id: string, depends_on: string[] = [], body = `Build ${draft_id}.\n\n## Acceptance checks\n\n- make test`): PlanDraft {
  return { draft_id, repo: "o/a", title: `Build ${draft_id}`, body, labels: [], depends_on };
}

function planned(fields: Partial<NonNullable<Plan["plan"]>> = {}, top: Partial<Plan> = {}): Plan {
  return {
    goal_id: "g-1", status: "planning", planning_task_id: null, approved_at: null, label: "meta-harness:goal-g-1",
    plan: { version: 1, drafts: [draft("ui", ["api"]), draft("api", ["store"]), draft("store")], invalid: [], source: "planned", task_id: "t-plan", note: null, created: {}, created_by: "planner", created_at: "2026-11-10T10:00:00Z", ...fields },
    ...top,
  };
}

function show(first: Plan, goal: Goal = GOAL) {
  const asked: { url: string; method: string; body: any }[] = [];
  const approved: number[] = [];
  let now = first;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    asked.push({ url, method, body });
    if (method === "PUT") now = planned({ ...now.plan, version: now.plan!.version + 1, drafts: body.drafts, items: body.items ?? now.plan!.items, source: "edited", created_by: "dave" });
    if (url.endsWith("/replan")) now = { ...now, planning_task_id: "t-plan-2" };
    if (url.endsWith("/approve")) now = { ...now, status: "active", approved_at: "2026-11-10T11:00:00Z" };
    return Promise.resolve(new Response(JSON.stringify(now), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <GoalPlan goal={goal} onApproved={() => approved.push(1)} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return { asked, approved };
}

test("while the planning task drafts, the page says so and links to its run", async () => {
  show({ ...planned(), plan: null, planning_task_id: "t-plan" });
  expect(await screen.findByText("The planner is working on the plan")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Watch its run" }).getAttribute("href")).toBe("/tasks/t-plan");
  expect(screen.queryByRole("button", { name: "Approve the plan" })).toBeNull();
});

test("the plan shows each draft in the order the work can be done, with its checks set apart", async () => {
  show(planned());
  await screen.findByTestId("draft-store");
  const cards = screen.getAllByTestId(/^draft-/).map((c) => c.getAttribute("data-testid"));
  expect(cards).toEqual(["draft-store", "draft-api", "draft-ui"]);
  const api = screen.getByTestId("draft-api");
  // What the agent is asked to build stays folded until it is asked for: the plan reads as a short list.
  expect(within(api).queryByText("Acceptance checks")).toBeNull();
  fireEvent.click(within(api).getByRole("button", { name: "Show the details of api" }));
  expect(within(api).getByText("Acceptance checks")).toBeTruthy();
  expect(api.textContent).toContain("- make test");
  expect(api.textContent).toContain("Afterstore");
  expect(screen.getByTestId("plan-order").textContent).toContain("store");
  expect(screen.getByText(/Version 1, made by the planner/)).toBeTruthy();
  // The order of work says how much can run side by side.
  expect(screen.getByTestId("plan-order").textContent).toContain("3 tasks. 1 can start at once");
  expect(screen.getByRole("link", { name: "The planning task's run" }).getAttribute("href")).toBe("/tasks/t-plan");
});

test("an invalid plan lists its reasons and can't be approved", async () => {
  show(planned({ invalid: ["api: depends on ghost, which is no draft and no issue (owner/repo#number)"] }));
  expect(await screen.findByText("This plan can't be approved yet")).toBeTruthy();
  expect(screen.getByText(/depends on ghost/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Approve the plan" }) as HTMLButtonElement).disabled).toBe(true);
});

test("an edit is held until saved as the next version, and the plan can't be approved before that", async () => {
  const { asked } = show(planned());
  fireEvent.click(await screen.findByRole("button", { name: "Edit store" }));
  const dialog = await screen.findByRole("dialog", { name: "Edit store" });
  fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Build the store, with indexes" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Keep the change" }));

  expect(await screen.findByText("Edits not saved yet")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Approve the plan" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Remove ui" }));
  fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));

  await waitFor(() => expect(asked.some((a) => a.method === "PUT")).toBe(true));
  const put = asked.find((a) => a.method === "PUT")!;
  expect(put.url).toBe("/v1/goals/g-1/plan");
  expect(put.body.drafts.map((d: PlanDraft) => [d.draft_id, d.title])).toEqual([["api", "Build api"], ["store", "Build the store, with indexes"]]);
  expect(await screen.findByText(/Version 2, edited by dave/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Approve the plan" }) as HTMLButtonElement).disabled).toBe(false);
});

test("Approve says what it will create, creates the issues and tells the page", async () => {
  const { asked, approved } = show(planned());
  fireEvent.click(await screen.findByRole("button", { name: "Approve the plan" }));
  const dialog = await screen.findByRole("dialog", { name: "Approve the plan" });
  expect(within(dialog).getByTestId("approve-says").textContent).toContain("Approving this plan adds 3 new tasks. Then the goal starts");
  // The new tasks go to the goal's tracker unless the operator says to keep them in the meta-harness.
  const toTracker = within(dialog).getByRole("switch") as HTMLInputElement;
  expect(toTracker.checked).toBe(true);
  expect(dialog.textContent).toContain("Add the 3 new tasks to GitHub as issues");
  expect(dialog.textContent).toContain("with the label meta-harness:goal-g-1");
  fireEvent.click(within(dialog).getByRole("button", { name: "Approve and start" }));
  await waitFor(() => expect(approved).toHaveLength(1));
  expect(asked.at(-1)).toMatchObject({ url: "/v1/goals/g-1/approve", method: "POST", body: { track: true } });
});

test("Replan takes a note and starts a new planning task", async () => {
  const { asked } = show(planned());
  fireEvent.click(await screen.findByRole("button", { name: "Plan again" }));
  const dialog = await screen.findByRole("dialog", { name: "Ask for a new plan" });
  fireEvent.change(within(dialog).getByLabelText("Note for the planner"), { target: { value: "Smaller issues." } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Plan again" }));
  await waitFor(() => expect(asked.some((a) => a.url.endsWith("/replan"))).toBe(true));
  expect(asked.find((a) => a.url.endsWith("/replan"))!.body).toEqual({ note: "Smaller issues." });
  expect(await screen.findByText("The planner is working on the plan")).toBeTruthy();
});

// --- A plan over the issues a goal already has ---------------------------------------------------------

const ISSUES = [
  { ref: "jira:VL-1", repo: "o/a", title: "Crash on save" },
  { ref: "jira:VL-2", repo: "o/a", title: "Too big" },
  { ref: "jira:VL-3", repo: "o/a", title: "Duplicate of the first" },
];
const item = (ref: string, decision: PlanItem["decision"], more: Partial<PlanItem> = {}): PlanItem => ({ ref, decision, reason: "Because.", depends_on: [], replaced_by: [], ...more });
const DECIDED = [item("jira:VL-1", "keep", { depends_on: ["api"] }), item("jira:VL-2", "split", { replaced_by: ["store", "api"], reason: "Two changes." }), item("jira:VL-3", "leave_out")];
const OVER_ISSUES = () =>
  planned({ drafts: [draft("store"), draft("api", ["store"])], items: DECIDED, issues: ISSUES, summary: "Store first, then the API, then the crash.", gaps: ["Nothing covers export."] });
const JIRA_GOAL = { ...GOAL, tracker: "jira" } as Goal;

test("the plan shows the order across issues and new tasks, then each decision; the planner speaks only in its panel", async () => {
  show(OVER_ISSUES(), JIRA_GOAL);

  await screen.findByTestId("plan-order");
  // What the planner says of the plan is in its updates at the head of the page, not repeated here.
  expect(screen.queryByText("Store first, then the API, then the crash.")).toBeNull();
  // The kept issue waits on a new task: one order of work for both kinds, and only what runs is in it.
  const order = screen.getByTestId("plan-order").textContent ?? "";
  expect(order).toContain("3 tasks. 1 can start at once");
  expect(order.indexOf("store")).toBeLessThan(order.indexOf("VL-1"));
  expect(order).not.toContain("VL-2");
  expect(order).not.toContain("VL-3");
  const big = screen.getByTestId("plan-issue-jira:VL-2");
  expect(big.textContent).toContain("Too big");
  expect((within(big).getByRole("radio", { name: "Split" }) as HTMLInputElement).checked).toBe(true);
  expect((within(big).getByRole("textbox", { name: "Why, for VL-2" }) as HTMLInputElement).value).toBe("Two changes.");
  expect((within(screen.getByTestId("plan-issue-jira:VL-3")).getByRole("radio", { name: "Leave out" }) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByText("Written by the planner. Created when you approve.")).toBeTruthy();
});

test("an operator changes a decision, which is saved with the plan as its next version", async () => {
  const { asked } = show(OVER_ISSUES(), JIRA_GOAL);
  const dup = await screen.findByTestId("plan-issue-jira:VL-3");

  fireEvent.click(within(dup).getByRole("radio", { name: "Run as it is" }));

  expect(await screen.findByText("Edits not saved yet")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Approve the plan" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByTestId("plan-order").textContent).toContain("4 tasks");
  fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));
  await waitFor(() => expect(asked.some((a) => a.method === "PUT")).toBe(true));
  const put = asked.find((a) => a.method === "PUT")!;
  expect(put.body.items.map((i: PlanItem) => [i.ref, i.decision])).toEqual([["jira:VL-1", "keep"], ["jira:VL-2", "split"], ["jira:VL-3", "keep"]]);
  expect(put.body.drafts).toHaveLength(2);
});

test("approving says what the plan does in numbers, and new tasks can be kept out of the tracker", async () => {
  const { asked, approved } = show(OVER_ISSUES(), JIRA_GOAL);
  fireEvent.click(await screen.findByRole("button", { name: "Approve the plan" }));
  const dialog = await screen.findByRole("dialog", { name: "Approve the plan" });
  expect(within(dialog).getByTestId("approve-says").textContent).toContain(
    "Approving this plan runs 1 issue of the goal's as it is, replaces 1 issue with new tasks, leaves 1 issue out and adds 2 new tasks.",
  );

  expect(dialog.textContent).toContain("Add the 2 new tasks to jira as issues");
  fireEvent.click(within(dialog).getByRole("switch"));
  expect(dialog.textContent).toContain("kept in the meta-harness only");
  fireEvent.click(within(dialog).getByRole("button", { name: "Approve and start" }));

  await waitFor(() => expect(approved).toHaveLength(1));
  expect(asked.at(-1)).toMatchObject({ url: "/v1/goals/g-1/approve", body: { track: false } });
});

test("a goal with no tracker is told its tasks stay in the meta-harness, with no choice to make", async () => {
  const { asked, approved } = show(planned(), { ...GOAL, tracker: "mh" } as Goal);
  fireEvent.click(await screen.findByRole("button", { name: "Approve the plan" }));
  const dialog = await screen.findByRole("dialog", { name: "Approve the plan" });

  expect(within(dialog).queryByRole("switch")).toBeNull();
  expect(dialog.textContent).toContain("This goal has no issue tracker: its tasks are kept in the meta-harness only.");
  fireEvent.click(within(dialog).getByRole("button", { name: "Approve and start" }));
  await waitFor(() => expect(approved).toHaveLength(1));
  expect(asked.at(-1)!.body).toEqual({ track: false });
});

test("a plan's tasks are the issues it keeps and its new tasks, and its sentence counts each decision", () => {
  const tasks = tasksOf([draft("store"), draft("api", ["store"])], DECIDED, ISSUES);
  expect(tasks.map((t) => [t.key, t.kind])).toEqual([["store", "new"], ["api", "new"], ["jira:VL-1", "issue"]]);
  expect(planSentence([], [item("jira:VL-1", "keep"), item("jira:VL-2", "keep")])).toBe("Approving this plan runs 2 issues of the goal's as they are.");
  expect(planSentence([], [])).toBe("Approving this plan runs nothing.");
});

test("drafts are ordered after what they wait on, and a body splits at its acceptance checks", () => {
  expect(inOrder([draft("ui", ["api"]), draft("api", ["store", "o/b#12"]), draft("docs"), draft("store")]).map((d) => d.draft_id)).toEqual(["store", "api", "ui", "docs"]);
  expect(inOrder([draft("a", ["b"]), draft("b", ["a"])]).map((d) => d.draft_id)).toEqual(["b", "a"]);
  expect(splitBody("Build it.\n\n## Acceptance checks\n\n- make test\n")).toEqual({ text: "Build it.", checks: "- make test" });
  expect(splitBody("No checks here.")).toEqual({ text: "No checks here.", checks: null });
});

test("an approved plan stays readable on the goal's page: what the planner said, and the plan when asked for", async () => {
  const approved = { ...OVER_ISSUES(), status: "active", approved_at: "2026-11-19T10:00:00Z" };
  approved.plan = { ...approved.plan!, tracked: false, created: { store: "mh:i-000000000001", api: "mh:i-000000000002" } };
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify(approved), { status: 200 })));
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <ApprovedPlan goal={{ ...JIRA_GOAL, status: "active" } as Goal} />
      </MemoryRouter>
    </MantineProvider>,
  );

  const section = await screen.findByTestId("approved-plan");
  expect(section.textContent).toContain("Plan version 1, approved");
  expect(section.textContent).toContain("It runs 1 issue of the goal's as it is, replaces 1 issue with new tasks, leaves 1 issue out and adds 2 new tasks.");
  expect(section.textContent).toContain("The new tasks are kept in the meta-harness only.");
  expect(screen.queryByTestId("plan-issues")).toBeNull();

  fireEvent.click(within(section).getByRole("button", { name: "Show the plan" }));
  // Read only: a decision shows as what was decided, with no way to change it.
  const big = screen.getByTestId("plan-issue-jira:VL-2");
  expect(big.textContent).toContain("Split");
  expect(within(big).queryByRole("radio")).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve the plan" })).toBeNull();
});

test("a goal under way is planned again from its page, with a note, and a goal that has ended is not", async () => {
  const approved = { ...OVER_ISSUES(), status: "active", approved_at: "2026-11-19T10:00:00Z" };
  const asked: { url: string; body: any }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return Promise.resolve(new Response(JSON.stringify(approved), { status: 200 }));
  });
  const again: number[] = [];
  const view = (onPlanAgain?: () => void) => (
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <ApprovedPlan goal={{ ...JIRA_GOAL, status: "active" } as Goal} onPlanAgain={onPlanAgain} />
      </MemoryRouter>
    </MantineProvider>
  );
  const { rerender } = render(view(() => again.push(1)));

  fireEvent.click(await screen.findByRole("button", { name: "Plan again" }));
  const dialog = await screen.findByRole("dialog", { name: "Plan the goal again" });
  expect(dialog.textContent).toContain("Running tasks carry on.");
  fireEvent.change(within(dialog).getByLabelText("Note for the planner"), { target: { value: "Fit the new issue in." } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Plan again" }));

  await waitFor(() => expect(again).toHaveLength(1));
  expect(asked.at(-1)).toEqual({ url: "/v1/goals/g-1/replan", body: { note: "Fit the new issue in." } });

  rerender(view(undefined));
  await screen.findByTestId("approved-plan");
  // The dialog's own button goes as it closes; the page's is not offered at all.
  await waitFor(() => expect(screen.queryByRole("button", { name: "Plan again" })).toBeNull());
});
