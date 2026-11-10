// The console's only way to the meta-harness: the /v1 REST API, through the edge (src/meta_harness/edge).
// In the cloud the edge needs a Google sign-in session (/auth); locally it names the operator itself.

export class ApiError extends Error {
  // `reasons`: every reason a configuration save was refused, when the service listed them.
  constructor(public status: number, public detail: string, public reasons: string[] = []) {
    super(detail);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const reasons: string[] = Array.isArray(body?.detail?.reasons) ? body.detail.reasons : [];
    const detail = body && typeof body.detail === "string" ? body.detail : reasons.join("; ") || JSON.stringify(body?.detail ?? body);
    throw new ApiError(response.status, detail || response.statusText, reasons);
  }
  return body as T;
}

export type ConfigKind = "tiers" | "profiles" | "repos";

export interface ConfigSummary {
  kind: ConfigKind;
  title: string;
  description: string;
  version: number;
  edited_by: string;
  created_at: string;
}

export interface ConfigVersion {
  kind: ConfigKind;
  version: number;
  document: Record<string, any>;
  edited_by: string;
  note: string | null;
  origin: "seed" | "edit" | "rollback";
  rolled_back_from: number | null;
  created_at: string;
}

function query(params: Record<string, string | number | undefined | null>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return pairs.length ? "?" + new URLSearchParams(pairs.map(([k, v]) => [k, String(v)])).toString() : "";
}

export interface LabelCount {
  label: string;
  total: number;
  repos: Record<string, number>;
  // How many of the label's open issues an open goal holds, and those goals.
  held: number;
  held_by: string[];
}

export interface LabelIssue {
  ref: string;
  number: number;
  title: string;
  labels: string[];
  updated_at: string;
  held_by: string | null;
}

export interface GoalDraft {
  title: string;
  statement: string;
  theme: string;
  modality: string;
  model: string;
  usd_cost: string;
  label: string;
  repos: string[];
  refs: string[];
}

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

// An issue of a goal with its task's progress, from GET /v1/goals/{id}/issues.
export interface IssueProgress {
  ref: string;
  repo: string;
  state: string;
  depends_on: string[];
  refused_reason: string | null;
  task_id: string | null;
  task_status: string | null;
  task_status_reason: string | null;
  pr_url: string | null;
  pr_state: "open" | "merged" | "closed" | null;
  checks: { head_sha: string | null; state: "passed" | "failed" | "pending" | "none"; failed: string[] } | null;
}

// A pull request on the activity map: the one a task opened, with its review and fix runs.
export interface ActivityPr {
  task_id: string;
  latest_run_id: string | null;
  title: string;
  repo: string;
  pr_url: string;
  opened_at: string;
  state: "in_review" | "waiting" | "blocked" | "merged" | "closed";
  state_at: string;
  fix_rounds: number;
  usd: number;
}

// A task that spent without opening a pull request.
export interface ActivitySpend {
  task_id: string;
  latest_run_id: string | null;
  title: string;
  started_at: string;
  usd: number;
}

export interface Activity {
  group_by: string;
  days: number;
  groups: { key: string; name: string; total: number; left_out: number; prs: ActivityPr[]; spent_without_pr: ActivitySpend[] }[];
  cost_scale: { low: number; high: number };
}

// One log entry, as the Logs page shows it, with the whole Cloud Logging entry.
export interface LogEntry {
  time: string;
  service: string;
  severity: string;
  message: string;
  entry: Record<string, unknown>;
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
  changed_by: string;
  reason: string | null;
  changed_at: string;
}

export const api = {
  me: () => call<{ identity: string; roles?: string[] }>("/v1/me"),
  logs: (params: { unit?: string; severity?: string; minutes?: number; q?: string }) =>
    call<{ entries: LogEntry[]; units: string[] }>("/v1/admin/logs" + query(params)),
  deploys: () => call<{ version: string; commit: string | null; revision: string | null; project?: string | null }>("/v1/admin/deploys"),
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

  goals: async (statuses: string[], offset: number, limit: number): Promise<Page<Goal>> => {
    const r = await call<{ goals: Goal[]; total: number }>(
      "/v1/goals" + query({ status: statuses.join(","), offset, limit }),
    );
    return { items: r.goals, total: r.total };
  },
  goal: (id: string) => call<Goal & { issues: IssueLink[] }>(`/v1/goals/${id}`),
  createGoal: (body: Record<string, unknown>) => call<Goal>("/v1/goals", { method: "POST", body: JSON.stringify(body) }),
  // Goals from labels: every label on the registered repos' open issues, a label's issues, and a drafted goal.
  labels: (repo?: string) => call<{ repos: string[]; labels: LabelCount[] }>("/v1/labels" + query({ repo })),
  // A registered repo's open issues, newest first, with the label and title text given, if any.
  repoIssues: (repo: string, params: { label?: string; q?: string; cursor?: number; limit?: number }) =>
    call<{ issues: LabelIssue[]; total: number; next_cursor: number | null }>(`/v1/repos/${repo}/issues` + query(params)),
  addIssues: (goalId: string, refs: string[]) =>
    call<{ added: string[] }>(`/v1/goals/${goalId}/issues`, { method: "POST", body: JSON.stringify({ refs }) }),
  labelIssues: (repo: string, label: string) =>
    call<{ issues: LabelIssue[] }>(`/v1/repos/${repo}/issues` + query({ label })).then((r) => r.issues),
  draftGoal: (body: { label: string; repos: string[]; refs: string[] }) =>
    call<GoalDraft>("/v1/goals/draft", { method: "POST", body: JSON.stringify(body) }),
  goalAction: (id: string, action: "pause" | "resume" | "cancel" | "complete" | "reopen" | "sweep") =>
    call<unknown>(`/v1/goals/${id}/${action}`, { method: "POST" }),
  patchGoal: (id: string, fields: Record<string, unknown>) =>
    call<Goal>(`/v1/goals/${id}`, { method: "PATCH", body: JSON.stringify(fields) }),

  tasks: async (
    // pr: true for tasks with a pull request, false for tasks without one.
    params: { statuses?: string[]; goalId?: string; pr?: boolean },
    offset: number,
    limit: number,
  ): Promise<Page<Task>> => {
    const pr = params.pr === undefined ? undefined : String(params.pr);
    const r = await call<{ tasks: Task[]; total: number }>(
      "/v1/tasks" + query({ status: params.statuses?.join(","), goal_id: params.goalId, pr, offset, limit }),
    );
    return { items: r.tasks, total: r.total };
  },
  task: (id: string) => call<Task>(`/v1/tasks/${id}`),
  cancelTask: (id: string) =>
    call<Task>(`/v1/tasks/${id}/cancel`, { method: "POST", body: JSON.stringify({ reason: "canceled from the console" }) }),

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
  // Each of a goal's issues with its task's status and its pull request's state and checks.
  goalIssues: (id: string) => call<{ issues: IssueProgress[] }>(`/v1/goals/${id}/issues`).then((r) => r.issues),
  activity: (groupBy: "goal" | "repo" | "label", days = 91) => call<Activity>("/v1/activity" + query({ group_by: groupBy, days })),
  latestPosition: () => call<{ latest_position: number }>("/v1/events" + query({ limit: 1, from_position: 0 })),

  configKinds: () => call<{ kinds: ConfigSummary[]; source: "db" | "file" }>("/v1/config"),
  configModels: () =>
    call<{ models: { name: string; engine: string; model: string; status: string }[] }>("/v1/config/models").then((r) => r.models),
  config: (kind: ConfigKind) => call<ConfigVersion>(`/v1/config/${kind}`),
  configVersions: (kind: ConfigKind) =>
    call<{ versions: ConfigVersion[] }>(`/v1/config/${kind}/versions`).then((r) => r.versions),
  saveConfig: (kind: ConfigKind, document: unknown, note: string) =>
    call<ConfigVersion>(`/v1/config/${kind}`, { method: "PUT", body: JSON.stringify({ document, note: note || null }) }),
  rollbackConfig: (kind: ConfigKind, version: number) =>
    call<ConfigVersion>(`/v1/config/${kind}/rollback`, { method: "POST", body: JSON.stringify({ version }) }),

  killSwitch: () => call<KillSwitch>("/v1/kill-switch"),
  setKillSwitch: (engaged: boolean, reason: string) =>
    call<KillSwitch>("/v1/kill-switch", { method: "POST", body: JSON.stringify({ engaged, reason }) }),
};

// One server-sent-event stream for many tasks: the event service's tail, filtered by task ids.
export function tailUrl(taskIds: string[], fromPosition: number, base = "", ticket: string | null = null): string {
  return base + "/v1/events/tail" + query({ task_id: taskIds.join(","), from_position: fromPosition, ticket: ticket ?? undefined });
}

export function issueUrl(ref: string): string | null {
  const m = /^github:([\w.-]+\/[\w.-]+)#(\d+)$/.exec(ref);
  return m ? `https://github.com/${m[1]}/issues/${m[2]}` : null;
}
