import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme";
import { PlanningView } from "./PlanningView";
import { reasonTarget } from "./staging";

const DOC = { candidate: "harness:pi-plan/claude:claude-opus-5-5", max_usd: "3.00", goals: "all", max_issues: 60, issue_chars: 4000 };

function show(document: Record<string, any> = DOC, reasons: string[] = []) {
  const staged: [string[], unknown][] = [];
  render(
    <MantineProvider theme={theme}>
      <PlanningView
        profiles={["pi-plan", "pi-plan-deep"]}
        models={["claude:claude-opus-5-5", "claude:claude-sonnet-5-5"]}
        staging={{
          document,
          original: document,
          stage: (path, value) => staged.push([path as string[], value]),
          reasons: (target) => reasons.filter((r) => reasonTarget("planning", document, r) === target),
        }}
      />
    </MantineProvider>,
  );
  return staged;
}

test("the planning tab says who the planner is, which goals are planned and where new tasks go", () => {
  show();
  expect(screen.getByText("pi-plan")).toBeTruthy();
  expect(screen.getByText("claude:claude-opus-5-5")).toBeTruthy();
  expect(screen.getByText("Every goal")).toBeTruthy();
  expect(screen.getByText(/none of its tasks starts until its plan is approved/)).toBeTruthy();
  // Left out of the document, the planner is casual and shares little detail.
  expect(screen.getByText("Casual and friendly")).toBeTruthy();
  expect(screen.getByText("Low detail: what you need to know")).toBeTruthy();
  // Left out of the document, new tasks go to the tracker.
  expect((screen.getByRole("switch", { name: "A plan's new tasks go to the issue tracker" }) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByText("Added to the goal's issue tracker")).toBeTruthy();
});

test("each setting is changed in place: the planner's model, where new tasks go, the spend limit", () => {
  const staged = show();

  fireEvent.click(screen.getByRole("switch", { name: "A plan's new tasks go to the issue tracker" }));
  expect(staged).toEqual([[["track_new_tasks"], false]]);

  fireEvent.click(screen.getByRole("button", { name: /^Edit a planning task's spend limit$/i }));
  const limit = screen.getByLabelText("A planning task's spend limit");
  fireEvent.change(limit, { target: { value: "5" } });
  fireEvent.keyDown(limit, { key: "Enter" });
  expect(staged.at(-1)).toEqual([["max_usd"], "5.00"]);
});

test("a refused save's reason shows on the setting it is about", () => {
  const reason = "candidate names the harness profile pi-lean, which has no create_issue tool: a planning run records its plan with it. Pick a planning profile, or add the tool to this one";
  show({ ...DOC, candidate: "harness:pi-lean/claude:claude-opus-5-5", goals: "from_description" }, [reason, "max_usd must be an amount above 0: the most a planning task may spend"]);

  expect(screen.getByText(reason)).toBeTruthy();
  expect(screen.getByText(/max_usd must be an amount above 0/)).toBeTruthy();
  expect(screen.getByText(/runs them as they are, with no plan/)).toBeTruthy();
  expect(reasonTarget("planning", DOC, "goals must be one of all, from_description")).toBe("goals");
});
