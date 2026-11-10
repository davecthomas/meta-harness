import type { MhEvent } from "./api";

export type LineKind =
  | "turn" | "model" | "tool" | "result" | "file" | "text" | "status" | "warn" | "error" | "info";

export interface Line {
  position: number;
  kind: LineKind;
  text: string;
  // The run the event belongs to, if any.
  run?: string | null;
}

function short(value: unknown, n: number): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > n ? text.slice(0, n) + "…" : text;
}

// How one event reads in a terminal, or null for events a terminal leaves out.
export function describe(e: MhEvent): Line | null {
  const p = e.payload ?? {};
  const line = (kind: LineKind, text: string): Line => ({ position: e.position, kind, text: `${e.time_utc.slice(11, 19)} ${text}` });
  switch (e.event_type) {
    case "task_created": return line("info", `task created from ${p.external_ref}`);
    case "task_status_changed": return line("status", `status ${p.from} → ${p.to}${p.reason ? ": " + p.reason : ""}`);
    case "task_admitted": return line("info", "admitted by the scheduler");
    case "task_held": return line("warn", `held: ${p.reason}`);
    case "task_routed": return line("info", `routed to tier ${p.tier} (${p.method}): ${(p.candidates ?? []).join(", ")}`);
    case "turn_started": return line("turn", `── turn ${p.turn} ──`);
    case "model_call":
      // A call that failed carries its error, and no model, tokens or cost.
      if (!p.model) return line("error", `model call failed after ${p.latency_ms ?? "?"}ms: ${short(p.error ?? p.error_code ?? "no answer", 240)}`);
      return line("model", `model ${modelName(p)}  in ${p.input_tokens}  out ${p.output_tokens}  $${p.usd}  ${p.latency_ms}ms`);
    case "tool_call": return line("tool", `$ ${p.tool} ${short(p.args ?? {}, 160)}`);
    case "tool_result": return line(p.is_error ? "error" : "result", `  ${short(p.output ?? "", 240)}`);
    case "file_changed": return line("file", `M ${p.path}`);
    case "tests_run": return line(p.passed === false ? "error" : "result", `tests ${short(p, 200)}`);
    case "guardrail_blocked": return line("error", `guardrail blocked ${short(p, 200)}`);
    case "compacted": return line("info", "context compacted");
    case "turn_finished": return p.text ? line("text", String(p.text)) : null;
    case "run_finished": return line(p.status === "FINISHED" ? "status" : "error", `run ${p.status} (${p.end_reason ?? ""})${p.error ? ": " + p.error : ""}`);
    case "attempt_finished": return line("status", `attempt ${p.attempt} on ${p.candidate}: ${p.decision}, $${p.spent_usd}`);
    case "pull_request_opened": return line("status", `pull request ${p.pr_url}`);
    case "task_cancel_requested": return line("warn", `cancel requested: ${p.reason}`);
    default: return null;
  }
}

// A model call's model, as <engine>:<model> when the event names the engine.
function modelName(p: Record<string, any>): string {
  return p.engine ? `${p.engine}:${p.model}` : String(p.model);
}

// The task an event belongs to: its task_id, or the task part of its run id (<task id>:run-<n>).
export function taskOf(e: MhEvent): string | null {
  if (e.task_id) return e.task_id;
  if (e.run_id) return e.run_id.split(":")[0];
  return null;
}

// What a tile shows above its terminal, folded from the task's events.
export interface TileFacts {
  turn: number;
  usd: number;
  model: string;
  status: string | null;
}

export function fold(facts: TileFacts, e: MhEvent): TileFacts {
  const p = e.payload ?? {};
  switch (e.event_type) {
    case "turn_started": return { ...facts, turn: Number(p.turn) || facts.turn };
    case "model_call": return { ...facts, usd: facts.usd + (Number(p.usd) || 0), model: p.model ? modelName(p) : facts.model };
    case "task_status_changed": return { ...facts, status: p.to };
    default: return facts;
  }
}
