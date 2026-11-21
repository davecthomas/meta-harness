import type { TaskHold } from "../../api";
import { moneyText } from "../../money";

// Why the scheduler holds a task, in words a person uses: what it waits for, and whether anyone needs to act.
// The scheduler's own reason names paths and IDs; it stays in the event for whoever debugs the scheduler.

export type HoldWords = { line: string; short: string; yours: boolean };

export function holdWords(held: Pick<TaskHold, "kind" | "reason" | "detail">): HoldWords {
  const detail = held.detail ?? {};
  switch (held.kind) {
    case "repo_area":
      return { line: "Another task is changing the same files. This one starts when that task finishes.", short: "waits for a task on the same files", yours: false };
    case "goal_parallel":
      return { line: "Its goal already has as many tasks running as it allows. This one starts when one finishes.", short: "waits for a free slot", yours: false };
    case "system_parallel":
      return { line: "The system is running as many tasks as it allows. This one starts when one finishes.", short: "waits for a free slot", yours: false };
    case "goal_budget":
      return { line: "Its goal is out of budget. Raise the budget to carry on.", short: "goal is out of budget", yours: true };
    case "goal_status":
      return detail.status === "active"
        ? { line: "Its goal is starting again. This one starts in a moment.", short: "goal is restarting", yours: false }
        : { line: `Its goal is ${String(detail.status ?? "not active")}. Resume the goal to start it.`, short: `goal is ${String(detail.status ?? "not active")}`, yours: true };
    case "kill_switch":
      return { line: "All work is stopped. The banner at the top says why.", short: "all work is stopped", yours: true };
    case "draining":
      return { line: "A deploy is in progress. This one starts when it ends.", short: "waits for a deploy", yours: false };
    case "start_failed":
      return { line: "It failed to start. The scheduler tries again on its own.", short: "retrying its start", yours: false };
    default:
      return { line: `${moneyText(held.reason)}.`, short: moneyText(held.reason), yours: false };
  }
}

// A task_held event's line: the plain sentence for a kind the console knows, else the scheduler's reason.
export function heldLine(payload: Record<string, unknown>): string {
  const reason = String(payload.reason ?? "");
  const kind = payload.kind as TaskHold["kind"] | undefined;
  if (!kind || kind === "goal_status" || kind === "goal_budget") return moneyText(reason);
  return holdWords({ kind, reason, detail: {} }).line;
}
