import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { GoalPlan as Plan, PlanAsk } from "../../api";
import { theme } from "../../theme";
import { DecisionBar, Glance, History, OpenQuestions, SHOWN_BEFORE_COUNTING, WhatChanged } from "./PlanPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const shown = (ui: React.ReactNode) => render(<MantineProvider theme={theme}><MemoryRouter>{ui}</MemoryRouter></MantineProvider>);

const READY: PlanAsk = {
  state: "ready",
  says: "44 tasks in 1 repo. Running them may spend up to $62 of your $100 budget.",
  buttons: [{ action: "approve_plan", label: "Approve and start (up to $62)" }, { action: "replan", label: "Plan again (up to $4)" }],
};

test("the decision bar says what is asked and offers its buttons, the recommended one first", () => {
  const acted: string[] = [];
  shown(<DecisionBar decision={READY} busy={false} onAct={(action) => acted.push(action)} />);

  const bar = screen.getByTestId("plan-decision");
  expect(within(bar).getByTestId("plan-decision-says").textContent).toBe(READY.says);
  const buttons = within(bar).getAllByRole("button").map((b) => b.textContent);
  expect(buttons).toEqual(["Approve and start (up to $62)", "Plan again (up to $4)"]);
  fireEvent.click(within(bar).getByRole("button", { name: "Approve and start (up to $62)" }));
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

test("a plan that is being written asks nothing", () => {
  shown(<DecisionBar decision={{ state: "improving", says: "The planner is revising the plan. It has 12 tasks so far.", buttons: [] }} busy={false} onAct={() => undefined} />);
  expect(within(screen.getByTestId("plan-decision")).queryAllByRole("button")).toHaveLength(0);
});

test("the plan's figures are tiles, with what it may spend said as an upper bound", () => {
  shown(<Glance glance={{ tasks: 44, repos: ["davecthomas/meta-harness"], may_spend_usd: "62.00", budget_usd: "100.00", can_start: 3, open_questions: 2, unsized: 1 }} />);
  const tiles = screen.getAllByTestId("plan-tile").map((t) => t.textContent);
  expect(tiles).toEqual(["Tasks441 not sized yet", "Repometa-harness", "May spend up to$62of your $100 budget", "Can start at once3", "Open questions2"]);
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
  expect(part.textContent).toContain("8 open questions");
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
