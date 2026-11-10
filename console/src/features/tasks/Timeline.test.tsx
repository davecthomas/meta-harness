import { fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { Timeline, entries, summarize } from "./Timeline";

let n = 0;
const ev = (event_type: string, payload: Record<string, unknown>): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: "2026-11-10T10:00:00Z", source: "", actor: null, run_id: "t:run-1", task_id: "t", payload,
});

const EVENTS = [
  ev("task_routed", { tier: "standard", candidates: ["harness:pi-lean/claude:claude-sonnet-5"] }),
  ev("turn_started", { turn: 1 }),
  ev("model_call", { engine: "claude", model: "claude-sonnet-5", input_tokens: 900, output_tokens: 40, usd: 0.012, latency_ms: 800 }),
  ev("tool_call", { tool_call_id: "c1", tool: "bash", args: { command: "uv run pytest -q" } }),
  ev("tool_result", { tool_call_id: "c1", tool: "bash", is_error: false, output: "12 passed", duration_ms: 3100 }),
  ev("tool_call", { tool_call_id: "c2", tool: "edit", args: { path: "src/app.py" } }),
  ev("tool_result", { tool_call_id: "c2", tool: "edit", is_error: true, output: "no match", duration_ms: 4 }),
  ev("file_changed", { path: "src/app.py" }),
  ev("turn_finished", { turn: 1, text: "I ran the tests; they pass." }),
  ev("run_finished", { status: "FINISHED", end_reason: "done" }),
];

test("events become turns with their model calls, tool calls and files, between status lines", () => {
  const list = entries(EVENTS);
  expect(list.map((e) => e.kind)).toEqual(["status", "turn", "status"]);
  const turn = list[1] as Extract<ReturnType<typeof entries>[number], { kind: "turn" }>;
  expect(turn.text).toBe("I ran the tests; they pass.");
  expect(turn.tools.map((t) => [t.tool, t.ok])).toEqual([["bash", true], ["edit", false]]);
  expect(turn.files).toEqual(["src/app.py"]);
  expect(summarize({ command: "uv run pytest -q" })).toBe("uv run pytest -q");
});

test("a tool row opens to its arguments and output", () => {
  render(
    <MantineProvider theme={theme}>
      <Timeline events={EVENTS} />
    </MantineProvider>,
  );
  const turn = screen.getByTestId("turn-1");
  expect(within(turn).getByText("I ran the tests; they pass.")).toBeTruthy();
  expect(within(turn).getByText(/900 in · 40 out/)).toBeTruthy();
  fireEvent.click(within(turn).getByText("uv run pytest -q"));
  expect(within(turn).getByText("12 passed")).toBeTruthy();
});
