import type { MhEvent } from "../../api";
import { moneyText, usd } from "../../money";
import { writingLine } from "./writing";

// The stages a task passes through, left to right. A step sits in its stage's column, and a step that
// belongs to an earlier stage than the one before it starts a new row: that is how a loop back shows.
export const COLUMNS = ["Waiting", "Run", "Pull request", "Review", "Outcome"] as const;

export type StepKind = "wait" | "run" | "review" | "pull_request" | "checks" | "merged" | "merge_checks" | "parked" | "end";
export type Tone = "ok" | "bad" | "warn" | "plain";

// One step of a task's path: what it was, how it ended, how long it took and what it cost.
export interface Step {
  key: string;
  kind: StepKind;
  column: number;
  title: string;
  // Why, or how it ended, in a sentence. Empty when the title says it all.
  detail: string;
  tone: Tone;
  start: string;
  // Null while the step is still going.
  end: string | null;
  runId?: string;
  // What the run was for: task, fix or review.
  runKind?: string;
  candidate?: string;
  usd?: number;
  turns?: number;
  round?: number;
  verdict?: string;
  findings?: number;
  url?: string;
  chosen?: boolean;
  // What an operator, the supervisor or the writing pass did during the step.
  notes: string[];
  // The task is here now.
  now: boolean;
}

// One pass through the stages, and why the task came back to start it.
export interface Row {
  steps: Step[];
  back: string;
}

// Every event type the path is drawn from. The task page reads all of them for the task, however old.
export const STEP_EVENTS = [
  "task_created", "task_status_changed", "task_cancel_requested", "attempt_finished", "run_prompt", "run_finished",
  "pull_request_opened", "pull_request_checks", "pull_request_reviewed", "pull_request_merged", "pull_request_closed",
  "merge_checks", "task_retried", "task_driven", "task_recovered", "task_standing", "task_held", "task_admitted",
  "task_unblocked", "budget_hold", "budget_decision", "review_hold", "review_proceeded", "finding_dismissed",
  "operator_review_claimed", "operator_review_released", "operator_review_submitted", "operator_message", "attempt_chosen", "writing_checked",
];

const PARKED: Record<string, string> = {
  ready: "Ready to merge",
  review_unresolved: "Review unresolved",
  taken_over: "Taken over by an operator",
};
const ENDS: Record<string, [string, Tone]> = { closed: ["Closed", "ok"], failed: ["Failed", "bad"], canceled: ["Canceled", "plain"] };
const RUN_TITLES: Record<string, string> = { task: "Task run", fix: "Fix run", review: "Review" };

// "harness:pi-careful/claude:claude-opus-5-5" as "pi-careful · claude-opus-5-5".
export function candidateName(candidate: string): string {
  const [harness, model] = candidate.split("/");
  if (!model) return candidate;
  return `${harness.replace(/^harness:/, "")} · ${model.split(":").at(-1)}`;
}

// A length of time in the two largest units that matter: "4s", "6m 12s", "1h 2m", "2d 3h".
export function span(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m}m ${s % 60}s` : `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}

// How long a step took, or has taken so far.
export function took(step: Step, now = Date.now()): number {
  return (step.end ? Date.parse(step.end) : now) - Date.parse(step.start);
}

const pullNumber = (url: string) => url.match(/\/pull\/(\d+)/)?.[1];
const runNumber = (runId: string) => runId.match(/run-(\d+)$/)?.[1];

// The task's events as the steps of its path, in the order they happened.
export function steps(events: MhEvent[]): Step[] {
  const out: Step[] = [];
  const runs = new Map<string, Step>();
  // The wait the task is in, if any: the next step ends it.
  let wait = null as Step | null;
  let pull = "";
  const endWait = (time: string) => {
    if (wait && !wait.end) wait.end = time;
    wait = null;
  };
  const lastEnd = (time: string) => out.at(-1)?.end ?? out.at(-1)?.start ?? time;
  const add = (step: Omit<Step, "notes" | "now">): Step => {
    endWait(step.start);
    const made: Step = { ...step, notes: [], now: false };
    out.push(made);
    return made;
  };
  const waitFor = (step: Omit<Step, "notes" | "now" | "end" | "kind">, kind: StepKind = "wait"): Step => {
    const made = add({ ...step, kind, end: null });
    wait = made;
    return made;
  };
  // A wait to start again that the event before this one opened is this event's to name.
  const queuedAgain = (time: string, key: string, title: string, detail: string): void => {
    if (wait && wait.kind === "wait" && wait.title === "Queued again") Object.assign(wait, { title, detail: detail || wait.detail });
    else waitFor({ key, column: 0, title, detail, tone: "plain", start: time });
  };
  const run = (runId: string, time: string, kind?: string): Step => {
    const held = runs.get(runId);
    if (held) return held;
    const made = add({
      key: runId, kind: kind === "review" ? "review" : "run", column: kind === "review" ? 3 : 1, title: RUN_TITLES[kind ?? "task"] ?? `${kind} run`,
      detail: "", tone: "plain", start: kind ? time : lastEnd(time), end: null, runId, runKind: kind ?? "task",
    });
    runs.set(runId, made);
    return made;
  };
  const note = (text: string): void => {
    out.at(-1)?.notes.push(text);
  };

  for (const e of [...events].sort((a, b) => a.position - b.position)) {
    const p = e.payload ?? {};
    const time = e.time_utc;
    const who = e.actor ?? "An operator";
    const runId = e.run_id ?? (p.run_id as string | undefined) ?? "";
    switch (e.event_type) {
      case "task_created":
        waitFor({ key: `p${e.position}`, column: 0, title: "Queued", detail: "", tone: "plain", start: time });
        break;
      case "task_held": {
        const reason = moneyText(String(p.reason ?? ""));
        if (wait && wait.kind === "wait" && wait.column === 0) Object.assign(wait, { title: "Held by the scheduler", detail: reason });
        else waitFor({ key: `p${e.position}`, column: 0, title: "Held by the scheduler", detail: reason, tone: "plain", start: time });
        break;
      }
      case "task_admitted":
        endWait(time);
        break;
      case "task_unblocked":
        waitFor({ key: `p${e.position}`, column: 0, title: "Queued", detail: "What it was blocked on is done", tone: "plain", start: time });
        break;
      case "task_status_changed": {
        const to = String(p.to);
        const reason = moneyText(String(p.reason ?? ""));
        if (to === "running") endWait(time);
        else if (to === "queued") queuedAgain(time, `p${e.position}`, "Queued again", reason);
        else if (to === "blocked") waitFor({ key: `p${e.position}`, column: 0, title: "Blocked", detail: reason, tone: "plain", start: time });
        else if (ENDS[to]) add({ key: `p${e.position}`, kind: "end", column: 4, title: ENDS[to][0], detail: reason, tone: ENDS[to][1], start: time, end: time });
        else waitFor({ key: `p${e.position}`, column: 4, title: PARKED[to] ?? to, detail: reason, tone: "warn", start: time }, "parked");
        break;
      }
      case "task_retried":
        queuedAgain(time, `p${e.position}`, `${p.action === "merge" ? "Merge asked" : "Retried"} by ${who}`, p.max_usd ? `with a spend limit of ${usd(p.max_usd)}` : "");
        break;
      case "task_driven":
        queuedAgain(time, `p${e.position}`, `Sent back for its ${p.action} by the supervisor`, moneyText(String(p.why ?? "")));
        break;
      case "task_recovered":
        queuedAgain(time, `p${e.position}`, "Recovered by the supervisor", moneyText(String(p.why ?? "")));
        break;
      case "task_standing":
        if (p.owner === "operator" && p.ask && out.at(-1)?.column === 4) note(`Needs you: ${p.ask}`);
        break;
      case "task_cancel_requested":
        note(`Cancel asked${e.actor ? ` by ${e.actor}` : ""}${p.reason ? `: ${p.reason}` : ""}`);
        break;
      case "run_prompt":
        if (runId) run(runId, time, String(p.kind ?? "task"));
        break;
      case "run_finished": {
        if (!runId) break;
        const step = run(runId, time);
        step.turns = Number(p.turns) || step.turns;
        if (p.usage?.usd !== undefined) step.usd = Number(p.usage.usd);
        step.end = time;
        break;
      }
      case "attempt_finished": {
        if (!runId) break;
        const step = run(runId, time);
        // A run from before prompts were recorded is known for what it was only by its tier.
        if (p.tier === "review" && step.kind !== "review") Object.assign(step, { kind: "review", column: 3, title: RUN_TITLES.review, runKind: "review" });
        if (p.tier === "fix" && step.runKind === "task") Object.assign(step, { title: RUN_TITLES.fix, runKind: "fix" });
        step.candidate = String(p.candidate ?? "");
        step.usd = Number(p.spent_usd) || step.usd;
        step.end = time;
        const finished = p.decision === "finished";
        if (step.tone === "plain") step.tone = finished ? "ok" : "bad";
        if (!finished) step.detail = moneyText(String(p.detail ?? "")) + (p.decision === "next_candidate" ? ". The next candidate takes it" : "");
        break;
      }
      case "attempt_chosen": {
        const chosen = [...runs.values()].find((s) => runNumber(s.runId ?? "") === String(p.attempt));
        if (chosen) chosen.chosen = true;
        break;
      }
      case "pull_request_opened":
        pull = String(p.pr_url ?? "");
        add({
          key: `p${e.position}`, kind: "pull_request", column: 2, title: `Pull request ${pullNumber(pull) ? `#${pullNumber(pull)} ` : ""}opened`,
          detail: Array.isArray(p.files) ? `${p.files.length} file${p.files.length === 1 ? "" : "s"} changed` : "", tone: "plain", start: time, end: time, url: pull,
        });
        break;
      case "pull_request_checks":
        add({
          key: `p${e.position}`, kind: "checks", column: 2, title: `Checks ${p.state}`, detail: (p.failed ?? []).join(", "),
          tone: p.state === "passed" ? "ok" : p.state === "failed" ? "bad" : "plain", start: lastEnd(time), end: time, url: pull ? `${pull}/checks` : undefined,
        });
        break;
      case "pull_request_reviewed": {
        const review = [...out].reverse().find((s) => s.kind === "review" && s.verdict === undefined)
          ?? add({ key: `p${e.position}`, kind: "review", column: 3, title: RUN_TITLES.review, detail: "", tone: "plain", start: lastEnd(time), end: time, candidate: String(p.reviewer ?? "") });
        Object.assign(review, { round: Number(p.round), verdict: String(p.verdict), findings: Number(p.findings) || 0, tone: p.verdict === "clean" ? "ok" : "warn" });
        review.title = `Review round ${p.round}`;
        review.detail = p.verdict === "clean" ? "Clean" : `${review.findings} finding${review.findings === 1 ? "" : "s"}: changes asked`;
        break;
      }
      case "review_hold":
        waitFor({ key: `p${e.position}`, column: 3, title: "Held for an operator", detail: moneyText(String(p.reason ?? "")), tone: "warn", start: time });
        break;
      case "operator_review_claimed":
        waitFor({ key: `p${e.position}`, column: 3, title: `Review taken by ${who}`, detail: "", tone: "warn", start: time });
        break;
      case "operator_review_released":
        note(`${who} gave the review back`);
        endWait(time);
        break;
      case "operator_review_submitted":
        note(`${who} reviewed it: ${p.verdict === "clean" ? "clean" : `${p.findings ?? 0} findings`}`);
        endWait(time);
        break;
      case "review_proceeded":
        note(`${who} sent round ${p.round} on to its fix run`);
        endWait(time);
        break;
      case "finding_dismissed":
        note(`${who} dismissed finding ${p.finding} of round ${p.round}: ${p.reason}`);
        break;
      case "budget_hold":
        waitFor({ key: `p${e.position}`, column: 0, title: "Held for budget", detail: moneyText(String(p.reason ?? "")), tone: "warn", start: time });
        break;
      case "budget_decision":
        note(p.action === "raise" ? `${who} raised the spend limit to ${usd(p.max_usd)}` : `${who} ended the task`);
        break;
      case "operator_message":
        note(`${who}: ${p.when === "stop" ? "stop after this turn" : String(p.text ?? "")}`);
        break;
      case "writing_checked":
        note(writingLine(p));
        break;
      case "pull_request_merged":
        add({ key: `p${e.position}`, kind: "merged", column: 4, title: "Merged", detail: `as ${String(p.merge_sha ?? "").slice(0, 12)}${e.actor ? ` by ${e.actor}` : ""}`, tone: "ok", start: time, end: time, url: String(p.pr_url ?? pull) });
        break;
      case "merge_checks":
        add({
          key: `p${e.position}`, kind: "merge_checks", column: 4, title: `Merge commit's checks ${p.state}`, detail: (p.failed ?? []).join(", "),
          tone: p.state === "passed" ? "ok" : p.state === "failed" ? "bad" : "plain", start: lastEnd(time), end: time,
        });
        break;
      case "pull_request_closed":
        add({ key: `p${e.position}`, kind: "end", column: 4, title: "Pull request closed", detail: "not merged", tone: "plain", start: time, end: time, url: String(p.pr_url ?? pull) });
        break;
    }
  }
  // A run nothing ended is still going only when it is the newest thing on the path.
  out.forEach((step, i) => {
    if (!step.end && i < out.length - 1) step.end = out[i + 1].start;
  });
  const last = out.at(-1);
  if (last && !(last.kind === "end" && last.title !== "Pull request closed")) last.now = true;
  return out;
}

// Why the task came back to an earlier stage: what the step it left says, or what the one it starts with does.
function backBecause(left: Step, first: Step): string {
  if (left.kind === "review" && left.verdict === "changes") return `${left.findings} finding${left.findings === 1 ? "" : "s"} to fix`;
  if (first.kind === "wait") return first.title;
  if (left.tone === "bad") return `${left.title.toLowerCase()}: back to fix it`;
  return first.runKind === "fix" ? "back for a fix" : "back again";
}

// The steps as rows: each row runs left to right through the stages, and a step in an earlier stage than
// the one before it starts the next row.
export function rows(path: Step[]): Row[] {
  const out: Row[] = [];
  for (const step of path) {
    const row = out.at(-1);
    const left = row?.steps.at(-1);
    if (!row || !left || step.column < left.column) out.push({ steps: [step], back: left ? backBecause(left, step) : "" });
    else row.steps.push(step);
  }
  return out;
}

// The whole path in numbers: how long, how much of that was waiting, what it cost, and how often it looped.
export function totals(path: Step[], now = Date.now()): { elapsed: number; waited: number; usd: number; runs: number; rounds: number } {
  const first = path[0];
  const last = path.at(-1);
  const until = last ? (last.now && !last.end ? now : Date.parse(last.end ?? last.start)) : now;
  return {
    elapsed: first ? until - Date.parse(first.start) : 0,
    waited: path.filter((s) => s.kind === "wait" || s.kind === "parked").reduce((sum, s) => sum + took(s, now), 0),
    usd: path.reduce((sum, s) => sum + (s.usd ?? 0), 0),
    runs: path.filter((s) => s.runId).length,
    rounds: Math.max(0, ...path.map((s) => s.round ?? 0)),
  };
}
