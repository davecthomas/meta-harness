import { expect, test } from "vitest";
import type { MhEvent } from "../api";
import { projection, spendOf } from "./Spend";

let n = 0;
const ev = (event_type: string, payload: Record<string, unknown>): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: "", source: "", actor: null, run_id: null, task_id: "t", payload,
});

test("a task's spend adds its model calls by model, and lists each attempt's cost", () => {
  const spend = spendOf([
    ev("model_call", { engine: "claude", model: "claude-sonnet-5", usd: 0.5, input_tokens: 1000, output_tokens: 10 }),
    ev("model_call", { latency_ms: 4, error: "refused" }),
    ev("model_call", { engine: "openai-responses", model: "gpt-6-sol", usd: 0.25, input_tokens: 500, output_tokens: 5 }),
    ev("attempt_finished", { attempt: 1, candidate: "a", spent_usd: "0.50", decision: "next_candidate" }),
    ev("attempt_finished", { attempt: 2, candidate: "b", spent_usd: "0.25", decision: "finished" }),
  ]);
  expect(spend.usd).toBeCloseTo(0.75);
  expect(spend.input).toBe(1500);
  expect(spend.byModel).toEqual({ "claude:claude-sonnet-5": 0.5, "openai-responses:gpt-6-sol": 0.25 });
  expect(spend.byAttempt.map((a) => a.usd)).toEqual([0.5, 0.25]);
});

test("a goal's projection spreads the average cost per closed issue over the rest", () => {
  expect(projection(6, 2, 5)).toBe(15);
  expect(projection(6, 0, 5)).toBeNull();
});
