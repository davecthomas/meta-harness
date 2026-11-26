import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Goal, GoalPlan as Plan, PlanAsk } from "../../api";
import { theme } from "../../theme";
import { DecisionBar, Glance, History, ReviewPart, OpenQuestions, SHOWN_BEFORE_COUNTING, WhatChanged } from "./PlanPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const shown = (ui: React.ReactNode) => render(<MantineProvider theme={theme}><MemoryRouter>{ui}</MemoryRouter></MantineProvider>);

const READY: PlanAsk = {
  state: "ready",
  says: "44 tasks in 1 repo. Running them may spend up to $62 of your $100 budget.",
  buttons: [{ action: "approve_plan", label: "Approve and start" }, { action: "replan", label: "Plan again (up to $4)" }],
};

test("the decision bar says what is asked and offers its buttons, the recommended one first", () => {
  const acted: string[] = [];
  shown(<DecisionBar decision={READY} busy={false} onAct={(action) => acted.push(action)} />);

  const bar = screen.getByTestId("plan-decision");
  expect(within(bar).getByTestId("plan-decision-says").textContent).toBe(READY.says);
  const buttons = within(bar).getAllByRole("button").map((b) => b.textContent);
  expect(buttons).toEqual(["Approve and start", "Plan again (up to $4)"]);
  fireEvent.click(within(bar).getByRole("button", { name: "Approve and start" }));
  expect(acted).toEqual(["approve_plan"]);
});

// Reduce decision drag: before its first button an ask is two sentences and 280 characters at most, and no list.
test("no ask is more than two sentences and 280 characters, or a list, before its first button", () => {
  const asks: PlanAsk[] = [
    READY,
    { state: "fix_waiting", says: "The planner drafted a fix for 2 gaps in this plan.", buttons: [{ action: "accept_fix", label: "Accept the fix" }, { action: "dismiss_fix", label: "Keep the plan as it is" }] },
    { state: "incomplete", says: "Planning stopped at its turns limit with 44 tasks drafted.", buttons: [{ action: "replan", label: "Carry on planning (up to $4)" }] },
    { state: "cannot_approve", says: "This plan cannot be approved yet: the task api is too large for one pull request. And 2 more.", buttons: [{ action: "replan", label: "Plan again (up to $4)" }] },
  ];
  for (const ask of asks) {
    const { container, unmount } = shown(<DecisionBar decision={ask} busy={false} onAct={() => undefined} />);
    const first = container.querySelector("button")!;
    const bar = screen.getByTestId("plan-decision");
    // Everything in the bar that comes before its first button, as the reader meets it.
    const walker = document.createTreeWalker(bar, NodeFilter.SHOW_TEXT);
    let before = "";
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (first.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING) before += node.textContent;
    }
    expect(before.length, ask.state).toBeLessThanOrEqual(280);
    expect(before.split(/[.!?](?:\s|$)/).filter((part) => part.trim()).length, ask.state).toBeLessThanOrEqual(2);
    expect(bar.querySelectorAll("li").length, ask.state).toBe(0);
    unmount();
  }
});

test("the review button shows its spend limit as given, and the plain label without one", () => {
  const goal = { id: "g-1", status: "planning" } as unknown as Goal;
  const plan = { goal_id: "g-1", status: "planning", plan: { version: 2 } };
  const { unmount } = shown(<ReviewPart plan={{ ...plan, review_usd: "4.00" } as unknown as Plan} goal={goal} onChanged={() => undefined} />);
  expect(screen.getByRole("button", { name: "Review this plan (up to $4.00)" })).toBeTruthy();
  unmount();
  shown(<ReviewPart plan={plan as unknown as Plan} goal={goal} onChanged={() => undefined} />);
  expect(screen.getByRole("button", { name: "Review this plan" })).toBeTruthy();
});

test("a plan that is being written asks nothing", () => {
  shown(<DecisionBar decision={{ state: "improving", says: "The planner is revising the plan. It has 12 tasks so far.", buttons: [] }} busy={false} onAct={() => undefined} />);
  expect(within(screen.getByTestId("plan-decision")).queryAllByRole("button")).toHaveLength(0);
});

test("the plan's figures are tiles, with what it may spend said as an upper bound", () => {
  shown(<Glance glance={{ tasks: 44, repos: ["davecthomas/meta-harness"], may_spend_usd: "62.00", budget_usd: "100.00", can_start: 3, open_questions: 2, unsized: 1 }} />);
  const tiles = screen.getAllByTestId("plan-tile").map((t) => t.textContent);
  expect(tiles).toEqual(["Tasks441 not sized yet", "Repometa-harness", "May spend up to$62of your $100 budget", "Can start at once3", "To weigh first2"]);
});

const PLAN = {
  goal_id: "g-1", status: "planning", planning_task_id: null, approved_at: null, label: "l",
  plan: { version: 2 },
  applied_fix: { from_version: 1, fixes: [{ gap: "The api and the store edit one file.", change: "Run api after store." }], changes: ["1 task reordered", "38 tasks reworded"] },
  changes: {
    from_version: 1,
    lines: ["1 task reordered", "38 tasks reworded"],
    diff: { added: [], removed: [], reordered: [{ id: "api", title: "Build api" }], changed: [], reworded: Array.from({ length: 38 }, (_, i) => ({ id: `t${i}`, title: `Task ${i}` })) },
  },
} as unknown as Plan;

test("what changed leads with why, then counts each kind, and lists a kind's tasks only when its count is opened", () => {
  shown(<WhatChanged plan={PLAN} />);

  const part = screen.getByTestId("plan-what-changed");
  expect(part.textContent).toContain("What changed since version 1");
  expect(within(part).getByTestId("planner-fix-reason").textContent).toBe("Run api after store. The api and the store edit one file.");
  expect(within(part).getByTestId("plan-change-counts").textContent).toBe("1 reordered38 reworded");
  // No task is named until a count is opened: thirty-eight reworded tasks are a number, not a paragraph.
  expect(within(part).queryByText("Task 7")).toBeNull();
  fireEvent.click(within(part).getByTestId("plan-change-reworded"));
  expect(within(part).getByTestId("plan-change-list").querySelectorAll("li")).toHaveLength(38);
  expect(within(part).getByRole("button", { name: "Undo the fix" })).toBeTruthy();
});

test("a plan with tasks not sized yet names no figure it would have to guess", () => {
  shown(<Glance glance={{ tasks: 60, repos: ["davecthomas/meta-harness"], may_spend_usd: null, budget_usd: "100.00", can_start: 8, open_questions: 3, unsized: 60 }} />);
  const tiles = screen.getAllByTestId("plan-tile").map((t) => t.textContent);
  expect(tiles[0]).toBe("Tasks6060 not sized yet");
  expect(tiles[2]).toBe("Budget$100what the tasks may spend is known once they are sized");
});

test("a fix that waits is shown by what accepting it would change, apart from older changes", () => {
  const waiting = { ...PLAN, applied_fix: null, suggestion: { for_version: 3, fixes: [{ gap: "Gap.", change: "Fix." }], changes: ["2 tasks added"], diff: { added: [{ id: "a", title: "Task a" }, { id: "b", title: "Task b" }] } } } as unknown as Plan;
  shown(<WhatChanged plan={waiting} />);
  const part = screen.getByTestId("plan-what-changed");
  expect(part.textContent).toContain("What the fix would change");
  expect(part.textContent).toContain("The planner's fix would settle");
  expect(within(part).getByTestId("plan-change-counts").textContent).toBe("2 added");
  expect(within(part).queryByRole("button", { name: "Undo the fix" })).toBeNull();
});

test("a plan that changed nothing shows no such part", () => {
  shown(<WhatChanged plan={{ ...PLAN, applied_fix: null, changes: null } as unknown as Plan} />);
  expect(screen.queryByTestId("plan-what-changed")).toBeNull();
});

test("open questions show a few and count the rest", () => {
  const gaps = Array.from({ length: 8 }, (_, i) => `Question ${i + 1}?`);
  shown(<OpenQuestions gaps={gaps} />);
  const part = screen.getByTestId("plan-questions");
  expect(part.textContent).toContain("Weigh this first");
  expect(within(part).queryByText(`Question ${SHOWN_BEFORE_COUNTING + 1}?`)).toBeNull();
  fireEvent.click(within(part).getByRole("button", { name: "Show the other 3" }));
  expect(within(part).getByText("Question 8?")).toBeTruthy();
});

test("the history lists the plan's versions, newest first, and is left out for a single one", () => {
  const v = (version: number, source: string, by: string, tasks: number) => ({ version, source, created_by: by, created_at: "2026-11-25T10:00:00Z", note: null, stopped_at: version === 1 ? "turns" : null, tasks, problems: 0 });
  const { unmount } = shown(<History versions={[v(1, "planned", "planner", 44), v(2, "edited", "dave", 45)]} />);
  const items = screen.getByTestId("plan-history").textContent ?? "";
  expect(items.indexOf("Version 2")).toBeLessThan(items.indexOf("Version 1"));
  expect(items).toContain("Edited by dave · 45 tasks");
  expect(items).toContain("stopped at its turns limit");
  unmount();
  shown(<History versions={[v(1, "planned", "planner", 44)]} />);
  expect(screen.queryByTestId("plan-history")).toBeNull();
});

const GOAL = { id: "g-1", status: "planning", adversarial_review: true } as unknown as import("../../api").Goal;
const finding = (id: string, more: Partial<import("../../api").PlanFinding> = {}) => ({ id, kind: "order", severity: "blocking", says: `Problem ${id}.`, change: `Change ${id}.`, tasks: ["api"], status: "open", ...more }) as import("../../api").PlanFinding;
const reviewed = (findings: import("../../api").PlanFinding[], more: object = {}) => ({
  ...PLAN,
  review: {
    model: "gpt-6-sol", rounds: 2, state: "done", version: 2, task_id: "t-r", summary: "", findings,
    counts: { found: findings.length, fixed: findings.filter((f) => f.status === "fixed").length, open: findings.filter((f) => f.status === "open" && f.severity === "blocking").length, advice: 0, dismissed: 0 },
    ...more,
  },
}) as unknown as Plan;

test("a review says what it found in one line, shows only what is open in full, and counts the rest", async () => {
  const posts: string[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") posts.push(url);
    return Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  const { ReviewPart } = await import("./PlanPage");
  const findings = [finding("r1-1", { status: "fixed" }), finding("r1-2", { status: "fixed" }), finding("r2-1"), finding("r2-2", { severity: "advice" })];
  shown(<ReviewPart plan={reviewed(findings)} goal={GOAL} onChanged={() => undefined} />);

  const part = screen.getByTestId("plan-review");
  expect(within(part).getByTestId("plan-review-says").textContent).toBe("gpt-6-sol found 4 problems: 2 fixed, 1 open.");
  const cards = within(part).getAllByTestId("plan-finding");
  expect(cards).toHaveLength(1);
  expect(cards[0].textContent).toContain("Problem r2-1.");
  expect(cards[0].textContent).toContain("To settle it: Change r2-1.");
  // What the planner fixed is a count until it is opened.
  expect(within(part).queryByText("Problem r1-1.")).toBeNull();
  fireEvent.click(within(part).getByTestId("plan-review-fixed"));
  expect(within(part).getByTestId("plan-review-list").querySelectorAll("li")).toHaveLength(2);
  fireEvent.click(within(cards[0]).getByRole("button", { name: "Not a problem" }));
  await vi.waitFor(() => expect(posts).toContain("/v1/goals/g-1/plan/review/findings/r2-1/dismiss"));
});

test("more open findings than a person reads are grouped by kind, each with its count", async () => {
  const { ReviewPart } = await import("./PlanPage");
  const many = [...Array.from({ length: 4 }, (_, i) => finding(`o${i}`)), ...Array.from({ length: 3 }, (_, i) => finding(`c${i}`, { kind: "check" }))];
  shown(<ReviewPart plan={reviewed(many)} goal={GOAL} onChanged={() => undefined} />);

  const part = screen.getByTestId("plan-review");
  expect(within(part).queryAllByTestId("plan-finding")).toHaveLength(0);
  expect(within(part).getByTestId("plan-finding-kinds").textContent).toBe("Order: 4Checks: 3");
  fireEvent.click(within(part).getByText("Checks: 3"));
  expect(within(part).getAllByTestId("plan-finding")).toHaveLength(3);
});

test("a review that is running, failed or found nothing says so, and one that failed is never read as clean", async () => {
  const { ReviewPart } = await import("./PlanPage");
  const said = (more: object, findings: import("../../api").PlanFinding[] = []) => {
    const { unmount } = shown(<ReviewPart plan={reviewed(findings, more)} goal={GOAL} onChanged={() => undefined} />);
    const text = screen.getByTestId("plan-review-says").textContent;
    unmount();
    return text;
  };
  expect(said({ state: "running" })).toBe("gpt-6-sol is reading the plan now.");
  expect(said({ state: "failed" })).toBe("The review by gpt-6-sol did not finish, so it says nothing about the plan.");
  expect(said({})).toBe("gpt-6-sol looked for problems and found none.");
  expect(said({ state: "partial" }, [finding("r1-1")])).toBe("gpt-6-sol found 1 problem: 0 fixed, 1 open. It stopped before it finished.");
});

test("what to weigh before approving is in the decision's own box, over its buttons", () => {
  shown(<DecisionBar decision={READY} busy={false} onAct={() => undefined} gaps={["No task covers the export."]} />);
  const bar = screen.getByTestId("plan-decision");
  const weigh = within(bar).getByTestId("plan-questions");
  const approve = within(bar).getAllByRole("button")[0];
  expect(weigh.textContent).toContain("No task covers the export.");
  // Read before the button is reached: it comes first in the box.
  expect(weigh.compareDocumentPosition(approve) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // A box with a list in it is too tall to keep in view over the page, and its sentence takes no stretch.
  expect(bar.style.position).toBe("");
  expect((within(bar).getByTestId("plan-decision-says").closest(".mantine-Group-root") as HTMLElement).style.flex).toBe("");
});

test("a plan still being worked on shows nothing to weigh: there is nothing to press yet", () => {
  shown(<DecisionBar decision={{ state: "improving", says: "gpt-6-sol is checking the plan for problems.", buttons: [] }} busy={false} onAct={() => undefined} gaps={["No task covers the export."]} />);
  expect(screen.queryByTestId("plan-questions")).toBeNull();
});

test("when the plan's changes are the review's own fixes, the review says them and nothing repeats it", async () => {
  const { changedByReview, reviewMadeTheChanges } = await import("./PlanPage");
  const review = { counts: { found: 1, fixed: 1, open: 0 } };
  const changes = { from_version: 1, lines: [], diff: { reworded: [{ id: "a", title: "A" }], added: [] } };
  const plan = { review, changes, suggestion: null, applied_fix: null } as never;
  expect(reviewMadeTheChanges(plan)).toBe(true);
  expect(changedByReview(plan)).toBe(" The fix reworded 1 task.");
  // A replan with no review, or a fix the planner applied itself, is not the review's: its own part says it.
  expect(reviewMadeTheChanges({ review: null, changes } as never)).toBe(false);
  expect(changedByReview({ review, changes, applied_fix: { from_version: 1, fixes: [], changes: [] } } as never)).toBe("");
});
