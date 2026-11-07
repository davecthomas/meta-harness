import { describe as suite, expect, it } from "vitest";
import type { MhEvent } from "./api";
import { describe, fold, taskOf } from "./terminal";

function event(event_type: string, payload: Record<string, unknown>, extra: Partial<MhEvent> = {}): MhEvent {
  return {
    position: 1,
    event_id: "e",
    event_type,
    time_utc: "2026-11-07T20:00:01.000+00:00",
    source: "run",
    actor: null,
    run_id: null,
    task_id: "t-1",
    payload,
    ...extra,
  };
}

suite("terminal lines", () => {
  it("shows a tool call as a command", () => {
    const line = describe(event("tool_call", { tool: "write", args: { path: "hello.txt" } }));
    expect(line).toEqual({ position: 1, kind: "tool", text: '20:00:01 $ write {"path":"hello.txt"}' });
  });

  it("marks a failed tool result as an error", () => {
    expect(describe(event("tool_result", { output: "boom", is_error: true }))?.kind).toBe("error");
  });

  it("leaves out events a terminal does not show", () => {
    expect(describe(event("debug_ping", { n: 1 }))).toBeNull();
    expect(describe(event("turn_finished", { text: "" }))).toBeNull();
  });

  it("finds the task from a run id", () => {
    expect(taskOf(event("model_call", {}, { task_id: null, run_id: "t-9:run-2" }))).toBe("t-9");
  });

  it("folds turn, spend, model and status", () => {
    const facts = [
      event("turn_started", { turn: 3 }),
      event("model_call", { usd: 0.002, engine: "claude", model: "haiku" }),
      event("model_call", { usd: 0.003, engine: "claude", model: "haiku" }),
      event("task_status_changed", { from: "running", to: "ready" }),
    ].reduce(fold, { turn: 0, usd: 0, model: "", status: null });
    expect(facts.turn).toBe(3);
    expect(facts.usd).toBeCloseTo(0.005);
    expect(facts.model).toBe("claude:haiku");
    expect(facts.status).toBe("ready");
  });
});
