import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { Plan, latestPlan } from "./Plan";

const plan = (position: number, steps: { step: string; status: string }[]): MhEvent => ({
  position, event_id: String(position), event_type: "plan_updated", time_utc: "", source: "", actor: null, run_id: "r", task_id: "t", payload: { steps },
});

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
