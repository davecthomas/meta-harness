import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { Plan, latestPlan, planStanding } from "./Plan";

const plan = (position: number, steps: { step: string; status: string }[], run = "r", time = ""): MhEvent => ({
  position, event_id: String(position), event_type: "plan_updated", time_utc: time, source: "", actor: null, run_id: run, task_id: "t", payload: { steps },
});
const show = (events: MhEvent[], running: boolean) =>
  render(
    <MantineProvider theme={theme}>
      <Plan events={events} running={running} />
    </MantineProvider>,
  );

test("the plan shown is the newest one, with the step in progress marked", () => {
  const events = [
    plan(1, [{ step: "Read", status: "in_progress" }]),
    plan(5, [{ step: "Read", status: "done" }, { step: "Fix", status: "in_progress" }, { step: "Test", status: "pending" }]),
  ];
  expect(latestPlan(events)?.length).toBe(3);
  render(
    <MantineProvider theme={theme}>
      <Plan events={events} />
    </MantineProvider>,
  );
  expect(screen.getByText("1 of 3 done")).toBeTruthy();
  expect(screen.getByText("Fix").closest("[data-status]")?.getAttribute("data-status")).toBe("in_progress");
});

test("no plan recorded shows nothing", () => {
  expect(latestPlan([])).toBeNull();
});

test("while its run is under way the plan says it is live, and the step in progress reads as going on", () => {
  const now = Date.parse("2026-11-18T10:05:00Z");
  const events = [plan(3, [{ step: "Read", status: "done" }, { step: "Fix", status: "in_progress" }], "r", "2026-11-18T10:03:00Z")];
  expect(planStanding(events, true, now)).toMatch(/^Live: the agent last updated it .*2/);
  show(events, true);
  expect(screen.getByText("Fix").textContent).toContain("in progress");
  expect(screen.getByRole("progressbar", { name: "1 of 2 steps done" })).toBeTruthy();
});

test("with no run under way the plan is as the last run left it, and its step in progress is where it stopped", () => {
  const events = [plan(3, [{ step: "Read", status: "done" }, { step: "Fix", status: "in_progress" }])];
  show(events, false);
  expect(screen.getByTestId("plan-standing").textContent).toBe("As the last run left it. No run is under way.");
  expect(screen.getByText("Fix").textContent).toContain("stopped here");
});

test("a new run that has recorded no plan shows the earlier run's as an earlier run's", () => {
  const started: MhEvent = { ...plan(9, []), event_type: "turn_started", run_id: "r2", payload: {} };
  const events = [plan(3, [{ step: "Fix", status: "in_progress" }], "r1"), started];
  expect(planStanding(events, true)).toBe("From an earlier run. The run under way has not recorded a plan yet.");
  show(events, true);
  expect(screen.getByText("Fix").textContent).toContain("stopped here");
});

test("a running task with no plan says one may come, and an ended one says nothing", () => {
  const { unmount } = show([], true);
  expect(screen.getByTestId("no-plan").textContent).toContain("No plan yet");
  unmount();
  const { container } = show([], false);
  expect(container.querySelector("[data-testid]")).toBeNull();
});
