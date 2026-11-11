// The console's only way to the meta-harness: the /v1 REST API, through the edge (src/meta_harness/edge).
// In the cloud the edge needs a Google sign-in session (/auth); locally it names the operator itself.

import { ApiError, call, query } from "./client";
import { adminApi } from "./features/admin/api";
import { performanceApi } from "./features/performance/api";
import { configApi } from "./features/config/api";
import { goalsApi } from "./features/goals/api";
import { overviewApi } from "./features/overview/api";
import { settingsApi } from "./features/settings/api";
import { tasksApi } from "./features/tasks/api";

export { ApiError, call, query };
// Each feature's own types, so `import { ... } from "../api"` finds them too.
export * from "./features/admin/api";
export * from "./features/config/api";
export * from "./features/goals/api";
export * from "./features/overview/api";
export * from "./features/performance/api";
export * from "./features/settings/api";
export * from "./features/tasks/api";

export interface Goal {
  id: string;
  // The goal's short name. Null until the operator or the planner's model gives it one: goalTitle covers that.
  title: string | null;
  // The goal's description: what it is, which the planner reads.
  statement: string;
  repos: string[];
  // The label that queues an issue for the goal; null for a goal that takes only issues picked by ref.
  label: string | null;
  modality: string;
  status: string;
  auto_merge: boolean;
  max_usd: string | null;
  // What the budget does when it is reached: stop the goal (block) or only warn; and how far past it a block
  // goal may go, in percent.
  budget_mode?: "block" | "warn";
  // The deployment's budget policy, from the task policy: under "track" no budget stops anything.
  budget_policy?: "block" | "track";
  budget_overage_percent?: number;
  // What the goal's runs had spent at the planner's last look, the spend a block goal stops at, where the goal
  // stands, and the budget a resume would set for a goal past its budget.
  spent_usd?: string;
  budget_block_at?: string | null;
  budget_state?: "ok" | "over" | "blocked";
  budget_resume_to?: string | null;
  // The most of its tasks that run at once; null: the system's, the task policy's max_parallel.
  max_parallel?: number | null;
  priority: number;
  created_by: string;
  created_at: string;
  // When the goal first became active: null for a goal saved without starting.
  started_at: string | null;
  // When the goal was met or canceled; null while it is open.
  closed_at: string | null;
}

// The name a goal shows in lists and headers: its title, or until it has one, the start of its description.
export function goalTitle(goal: Pick<Goal, "title" | "statement">): string {
  if (goal.title) return goal.title;
  const text = goal.statement.trim().replace(/\s+/g, " ");
  return text.length > 80 ? `${text.slice(0, 79)}…` : text;
}

// The status a goal shows: a paused goal that never started is "not_started", told apart from one paused after running.
export function goalStatus(goal: Pick<Goal, "status" | "started_at">): string {
  return goal.status === "paused" && !goal.started_at ? "not_started" : goal.status;
}

export interface IssueLink {
  ref: string;
  task_id: string | null;
  state: string;
  depends_on: string[];
  refused_reason: string | null;
}

export interface Task {
  id: string;
  external_ref: string;
  repo: string;
  task_type: string;
  title: string;
  status: string;
  status_reason: string | null;
  cancel_requested: boolean;
  goal_id: string | null;
  pr_url: string | null;
  acceptance_checks: string[];
  created_at: string;
  updated_at: string;
  // Attempts the task's first run makes at once, for an operator to pick one.
  attempts?: number;
  // The task's spend limit for all its runs, when it has its own.
  max_usd?: string | null;
  // What the running task waits on an operator for: "budget" while it is held for budget.
  waiting_on?: string | null;
  // The operator who took the task's review over; the round waits for their verdict. Null: no one has.
  review_claimed_by?: string | null;
  // The issues a blocked task waits on, as refs.
  depends_on?: string[];
  // Why the scheduler holds a queued task; null for one it does not hold, and for a task in any other status.
  held?: TaskHold | null;
  // Who has the task, from the supervisor: null for a closed task, or one it has not looked at yet.
  standing?: TaskStanding | null;
}

// A queued task's hold, from the scheduler. kind says what to do about it without reading the reason's words;
// detail names what the reason is about: goal_id, the tasks holding the places (holders), shared paths.
export interface TaskHold {
  reason: string;
  kind: "goal_budget" | "goal_status" | "goal_parallel" | "system_parallel" | "repo_area" | "kill_switch" | "draining" | "start_failed";
  since: string;
  last_checked: string;
  detail: Record<string, unknown>;
}

// Who has an open task, from the supervisor's last pass: its workflow, the scheduler, the planner's sweep,
// the supervisor itself, or an operator, who is told what to do in `ask`.
export interface TaskStanding {
  owner: "workflow" | "scheduler" | "planner" | "supervisor" | "operator";
  situation: string;
  reason: string;
  ask: string | null;
  since: string;
  checked_at: string;
  attempts: number;
  last_error: string | null;
}

export interface MhEvent {
  position: number;
  event_id: string;
  event_type: string;
  time_utc: string;
  source: string;
  actor: string | null;
  run_id: string | null;
  task_id: string | null;
  payload: Record<string, any>;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface KillSwitch {
  engaged: boolean;
  // A deploy that replaces the VM drains: no new task starts, and running ones finish.
  draining?: boolean;
  changed_by: string;
  reason: string | null;
  changed_at: string;
}

// The calls every feature shares; each feature's own calls are in features/<feature>/api.ts.
export const api = {
  version: () => call<{ version: string; commit: string | null }>("/v1/version"),
  me: () => call<{ identity: string; roles?: string[] }>("/v1/me"),
  // The newest `limit` events of these types, oldest first.
  recentEvents: (eventTypes: string[], limit: number) =>
    call<{ events: MhEvent[] }>("/v1/events" + query({ event_type: eventTypes.join(","), limit, before_position: Number.MAX_SAFE_INTEGER })).then((r) => r.events),
  authConfig: () => call<{ google_client_id: string | null }>("/auth/config"),
  signIn: (credential: string) =>
    call<{ identity: string }>("/auth/google", { method: "POST", body: JSON.stringify({ credential }) }),
  signOut: () => call<{ signed_out: boolean }>("/auth/logout", { method: "POST" }),
  // Where to open the live event stream, and the ticket that opens it (null locally).
  streamTicket: () => call<{ base: string; ticket: string | null }>("/auth/stream-ticket", { method: "POST" }),
  repos: () => call<{ repos: { name: string }[] }>("/v1/repos").then((r) => r.repos),
  // The last `limit` events before `before`, or the newest `limit`, for one task or several.
  events: (taskIds: string[], limit: number, before?: number) =>
    call<{ events: MhEvent[]; latest_position: number }>(
      "/v1/events" + query({ task_id: taskIds.join(","), limit, before_position: before ?? Number.MAX_SAFE_INTEGER }),
    ),
  // Every event of these types on these tasks, oldest first: a goal's attempts, for what its tasks spent.
  taskEvents: async (taskIds: string[], eventTypes: string[]) => {
    const found: MhEvent[] = [];
    for (let start = 0; start < taskIds.length; start += 100) {
      let from = 0;
      for (;;) {
        const page = await call<{ events: MhEvent[]; next_position: number }>(
          "/v1/events" +
            query({ task_id: taskIds.slice(start, start + 100).join(","), event_type: eventTypes.join(","), from_position: from, limit: 5000 }),
        );
        found.push(...page.events);
        if (page.events.length < 5000) break;
        from = page.next_position;
      }
    }
    return found.sort((a, b) => a.position - b.position);
  },
  latestPosition: () => call<{ latest_position: number }>("/v1/events" + query({ limit: 1, from_position: 0 })),
  search: (q: string) =>
    call<{ goals: { id: string; title: string; status: string }[]; tasks: { id: string; title: string; status: string; ref: string | null }[] }>(
      "/v1/search" + query({ q }),
    ),
  killSwitch: () => call<KillSwitch>("/v1/kill-switch"),
  setKillSwitch: (engaged: boolean, reason: string) =>
    call<KillSwitch>("/v1/kill-switch", { method: "POST", body: JSON.stringify({ engaged, reason }) }),
  ...adminApi,
  ...performanceApi,
  ...configApi,
  ...goalsApi,
  ...overviewApi,
  ...settingsApi,
  ...tasksApi,
};

// One server-sent-event stream for many tasks: the event service's tail, filtered by task ids.
export function tailUrl(taskIds: string[], fromPosition: number, base = "", ticket: string | null = null): string {
  return base + "/v1/events/tail" + query({ task_id: taskIds.join(","), from_position: fromPosition, ticket: ticket ?? undefined });
}

export function issueUrl(ref: string): string | null {
  const m = /^github:([\w.-]+\/[\w.-]+)#(\d+)$/.exec(ref);
  return m ? `https://github.com/${m[1]}/issues/${m[2]}` : null;
}
