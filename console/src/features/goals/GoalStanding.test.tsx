import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import type { GoalStanding as Standing } from "../../api";
import { theme } from "../../theme";
import { GoalStanding, standingNote } from "./GoalStanding";

const waiting: Standing = {
  state: "needs_you",
  reason: "nothing in it is moving: 2 tasks wait on you",
  ask: "Each one below says what to do. The goal goes on as soon as one of them is unstuck.",
  yours: [
    { task_id: "t-1", title: "Record prompts", reason: "the task failed", ask: "Read why it failed, then retry it.", since: "2026-11-12T09:00:00Z" },
    { task_id: "t-2", title: "Size issues", reason: "its review is clean", ask: "Merge the pull request.", since: "2026-11-12T10:00:00Z" },
  ],
  moving: 0,
};

function show(standing: Standing | null) {
  return render(<MantineProvider theme={theme}><MemoryRouter><GoalStanding standing={standing} /></MemoryRouter></MantineProvider>);
}

test("a goal with nothing moving says it waits on you, and what each task needs", () => {
  show(waiting);
  const shown = screen.getByTestId("goal-standing").textContent ?? "";
  expect(shown).toContain("This goal waits on you");
  expect(shown).toContain("Nothing in it is moving: 2 tasks wait on you.");
  expect(shown).toContain("Record prompts: Read why it failed, then retry it.");
  expect(screen.getByRole("link", { name: "Size issues" }).getAttribute("href")).toBe("/tasks/t-2");
  expect(standingNote(waiting)).toBe("Waits on you: nothing in it is moving: 2 tasks wait on you");
});

test("a goal that is moving, held or done shows nothing", () => {
  for (const state of ["moving", "held", "idle", "done"] as const) {
    const { unmount } = show({ ...waiting, state });
    expect(screen.queryByTestId("goal-standing")).toBeNull();
    expect(standingNote({ ...waiting, state })).toBe("");
    unmount();
  }
  show(null);
  expect(screen.queryByTestId("goal-standing")).toBeNull();
});
