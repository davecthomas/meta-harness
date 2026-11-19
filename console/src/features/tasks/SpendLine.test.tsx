import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { SpendLine, spendOf } from "./Spend";

let n = 0;
const ev = (event_type: string, payload: Record<string, unknown>): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: "", source: "", actor: null, run_id: null, task_id: "t", payload,
});

// One finished run, then a second under way.
const EVENTS = [
  ev("model_call", { engine: "claude", model: "m", usd: 0.5 }),
  ev("attempt_finished", { attempt: 1, candidate: "a", spent_usd: "0.55", decision: "next_candidate" }),
  ev("model_call", { engine: "claude", model: "m", usd: 0.2 }),
  ev("model_call", { engine: "claude", model: "m", usd: 0.1 }),
];

const show = (props: { total?: number; running?: boolean }) =>
  render(<MantineProvider theme={theme}><SpendLine spend={spendOf(EVENTS)} turn={3} {...props} /></MantineProvider>);

test("what the run under way has cost is the model calls since the last attempt finished", () => {
  expect(spendOf(EVENTS).underWay).toBeCloseTo(0.3);
  expect(spendOf(EVENTS.slice(0, 2)).underWay).toBe(0);
});

test("the header's figure is the spend report's total, with the run under way named beside it", () => {
  show({ total: 0.55, running: true });
  expect(screen.getByTestId("task-spend").textContent).toBe("$0.55");
  expect(screen.getByTestId("task-spend-under-way").textContent).toBe("+ $0.30 in the run under way");
});

test("a task that is not running shows the total alone, and the live sum until the report is read", () => {
  const { unmount } = show({ total: 0.55, running: false });
  expect(screen.queryByTestId("task-spend-under-way")).toBeNull();
  unmount();
  show({ running: true });
  expect(screen.getByTestId("task-spend").textContent).toBe("$0.80");
  expect(screen.queryByTestId("task-spend-under-way")).toBeNull();
});

test("an ended task shows its totals and no single model; a running one names its run's turn and model", () => {
  const evs = [
    ev("turn_started", { turn: 1 }), ev("turn_started", { turn: 2 }),
    ev("turn_started", { turn: 1 }), ev("turn_started", { turn: 2 }), ev("turn_started", { turn: 3 }),
  ].map((e, i) => ({ ...e, run_id: i < 2 ? "t:1" : "t:2" }));
  const { unmount } = render(<MantineProvider theme={theme}><SpendLine spend={spendOf(evs)} turn={3} model="claude:m" running={false} /></MantineProvider>);
  expect(screen.getByTestId("task-turns").textContent).toBe("5 turns in 2 runs");
  expect(screen.queryByText("claude:m")).toBeNull();
  unmount();
  render(<MantineProvider theme={theme}><SpendLine spend={spendOf(evs)} turn={3} model="claude:m" running /></MantineProvider>);
  expect(screen.getByTestId("task-turns").textContent).toBe("Turn 3 of the run under way");
  expect(screen.getByText("claude:m")).toBeTruthy();
});
