import { call, query } from "../../client";
import { sourceQuery, type IssueSource } from "../trackers/api";
import type { Goal, IssueLink, Page } from "../../api";
import type { PlannerUpdate, PlannerUpdatesRead } from "./PlannerUpdates";

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
  // The label drafted from, when it was one: the goal can take it. Null for a draft from several (labels):
  // a goal takes one label, so it holds the issues read (refs) as picked issues.
  label: string | null;
  labels?: string[];
  repos: string[];
  refs: string[];
}

// An issue of a goal with its task's progress, from GET /v1/goals/{id}/issues.
export interface GoalSpend {
  goal_id: string;
  max_usd: string | null;
  usd: string;
  by_kind: Record<string, string>;
  tasks: { task_id: string; title: string | null; status: string; usd: string; by_kind: Record<string, string>; runs: number; turns: number }[];
}

// One issue a planning task proposed. depends_on names other drafts by draft_id, or issues as owner/repo#12.
export interface PlanDraft {
  draft_id: string;
  repo: string;
  title: string;
  body: string;
  labels: string[];
  depends_on: string[];
}

// A plan's decision on an issue the goal already had: run it as it is, replace it with new tasks, or leave it
// out, with the planner's reason. depends_on names what a kept issue waits on: other issues' refs, or new
// tasks' draft_id. replaced_by names the new tasks a split issue is replaced by.
export type PlanDecision = "keep" | "split" | "leave_out";
export interface PlanItem {
  ref: string;
  decision: PlanDecision;
  reason: string;
  depends_on: string[];
  replaced_by: string[];
}

// A goal's plan as the planner shows it: its newest version, or none while the first is drafted.
export interface GoalPlan {
  goal_id: string;
  status: string;
  // The planning task still drafting, if one is.
  planning_task_id: string | null;
  approved_at: string | null;
  // The label the plan's issues carry.
  label: string;
  plan: {
    version: number;
    // The new tasks the planner wrote: they exist only in the plan until it is approved.
    drafts: PlanDraft[];
    // The decision on each issue the goal already had, and those issues as they were when it was planned.
    items?: PlanItem[];
    issues?: { ref: string; repo: string; title: string }[];
    // How the plan reaches the goal, and what it does not cover.
    summary?: string;
    gaps?: string[];
    // Where an approval put the new tasks: the goal's tracker (true) or the meta-harness alone.
    tracked?: boolean | null;
    // Why the plan cannot be approved; empty when it can.
    invalid: string[];
    source: "planned" | "edited" | "suggested";
    task_id: string | null;
    note: string | null;
    created: Record<string, string>;
    created_by: string;
    created_at: string;
  } | null;
}

// The answer to a budget resume: the goal as it now is, the old and new budgets, and how the new one was worked out.
export interface BudgetResume {
  goal: Goal;
  from: string;
  to: string;
  spent_usd: string;
  how: string;
  tasks_told: string[];
}

export interface IssueProgress {
  ref: string;
  // The issue's title, once a sweep has read it: what a card with no task yet shows.
  title?: string | null;
  repo: string;
  state: string;
  depends_on: string[];
  refused_reason: string | null;
  task_id: string | null;
  task_status: string | null;
  task_status_reason: string | null;
  task_waiting_on?: string | null;
  pr_url: string | null;
  pr_state: "open" | "merged" | "closed" | null;
  checks: { head_sha: string | null; state: "passed" | "failed" | "pending" | "none"; failed: string[] } | null;
  // Whose words the pull request description's opening summary is: the run's own, or a fallback the
  // meta-harness wrote from its record of the run or from the branch's files. Null when none was recorded.
  summary_source?: "run" | "recorded" | "files" | null;
  // How many fix rounds' summaries the meta-harness wrote because their runs left none usable.
  fix_summaries_written?: number;
  // What the planner's sizing found before the issue became a task; null and empty for an issue not sized.
  size?: IssueSize | null;
  size_reasons?: string[];
  // What the issue lacks: shown on its card, and what holds it if the goal is strict.
  missing?: IssueGap[];
  // For a large issue, the smaller issues it splits into, and its own paths and checks, for the split drafts.
  split?: string[];
  paths?: string[];
  checks_named?: string[];
  // Why the issue waits for an operator instead of becoming a task: Run anyway or Split answers it.
  held_reason?: string | null;
  // The issues an operator split it into: a split issue is not split again.
  split_into?: string[];
}

export type IssueSize = "small" | "medium" | "large";
export type IssueGap = "acceptance_checks" | "paths" | "testable_outcome";

// One issue a held issue splits into, as the operator edited it before it is created.
export interface SplitDraft {
  title: string;
  body: string;
}

// A goal's issues with what the planner knows of their states. A goal no sweep covers (met, canceled, paused,
// stalled) has its issues' states read from GitHub when this is asked for: states_checked_at says when, and
// states_complete is false when one could not be read and kept the state it had.
export interface GoalIssues {
  goal_id: string;
  goal_status?: string;
  states_checked_at?: string | null;
  states_complete?: boolean;
  issues: IssueProgress[];
}

// The goals feature's calls; src/api.ts spreads them into `api`.
export const goalsApi = {
  goals: async (statuses: string[], offset: number, limit: number): Promise<Page<Goal>> => {
    const r = await call<{ goals: Goal[]; total: number }>(
      "/v1/goals" + query({ status: statuses.join(","), offset, limit }),
    );
    return { items: r.goals, total: r.total };
  },
  goal: (id: string) => call<Goal & { issues: IssueLink[] }>(`/v1/goals/${id}`),
  // The system's parallel limit, which a goal inherits and may lower.
  limits: () => call<{ max_parallel: number | null; max_sandboxes: number | null; plans_every_goal?: boolean; track_new_tasks?: boolean }>("/v1/limits"),
  createGoal: (body: Record<string, unknown>) => call<Goal>("/v1/goals", { method: "POST", body: JSON.stringify(body) }),
  // Goals from labels: every label on the registered repos' open issues, a label's issues, and a drafted goal.
  // source, here and below: which tracker's issues, and what it asks of a goal; the default tracker's when left out.
  labels: (repo?: string, source?: IssueSource) =>
    call<{ repos: string[]; labels: LabelCount[] }>("/v1/labels" + query({ repo, ...sourceQuery(source) })),
  // A registered repo's open issues, newest first, with the label and title text given, if any.
  repoIssues: (repo: string, params: { label?: string; q?: string; cursor?: number; limit?: number }, source?: IssueSource) =>
    call<{ issues: LabelIssue[]; total: number; next_cursor: number | null }>(
      `/v1/repos/${repo}/issues` + query({ ...params, ...sourceQuery(source) }),
    ),
  // Remove an issue from its goal. force: also when its task has a pull request open, which is left open.
  removeIssue: (goalId: string, ref: string, force = false) =>
    call<{ dropped: string; task_canceled: string | null }>(
      `/v1/goals/${goalId}/issues/${ref.replace("#", "%23")}${force ? "?force=true" : ""}`,
      { method: "DELETE" },
    ),
  addIssues: (goalId: string, refs: string[]) =>
    call<{ added: string[] }>(`/v1/goals/${goalId}/issues`, { method: "POST", body: JSON.stringify({ refs }) }),
  labelIssues: (repo: string, label: string, source?: IssueSource) =>
    call<{ issues: LabelIssue[] }>(`/v1/repos/${repo}/issues` + query({ label, ...sourceQuery(source) })).then((r) => r.issues),
  draftGoal: (body: { labels: string[]; repos: string[]; refs: string[]; tracker?: string; tracker_settings?: Record<string, string> }) =>
    call<GoalDraft>("/v1/goals/draft", { method: "POST", body: JSON.stringify(body) }),
  goalAction: (id: string, action: "pause" | "resume" | "cancel" | "complete" | "reopen" | "sweep") =>
    call<unknown>(`/v1/goals/${id}/${action}`, { method: "POST" }),
  patchGoal: (id: string, fields: Record<string, unknown>) =>
    call<Goal>(`/v1/goals/${id}`, { method: "PATCH", body: JSON.stringify(fields) }),
  // Raise the budget of a goal over it or stopped by it: by addUsd, or by an amount the planner works out.
  resumeBudget: (id: string, addUsd?: number) =>
    call<BudgetResume>(`/v1/goals/${id}/resume-budget`, {
      method: "POST",
      body: JSON.stringify(addUsd === undefined ? {} : { add_usd: addUsd.toFixed(2) }),
    }),
  // Where the goal's money went: each task's spend, and the goal's by kind of run.
  goalSpend: (id: string) => call<GoalSpend>(`/v1/goals/${id}/spend`),
  // The planner's updates on the goal, newest first; and one written now.
  goalUpdates: (id: string) => call<PlannerUpdatesRead>(`/v1/goals/${id}/updates`),
  askForGoalUpdate: (id: string) => call<{ update: PlannerUpdate | null }>(`/v1/goals/${id}/updates`, { method: "POST" }),
  // A feature goal's plan: read it, save edited drafts as its next version, ask for a new one, approve it.
  goalPlan: (id: string) => call<GoalPlan>(`/v1/goals/${id}/plan`),
  // items: the decisions on the goal's issues; left out, those of the version before stay.
  savePlan: (id: string, drafts: PlanDraft[], items?: PlanItem[]) =>
    call<GoalPlan>(`/v1/goals/${id}/plan`, { method: "PUT", body: JSON.stringify(items ? { drafts, items } : { drafts }) }),
  // The planner's suggested fix for the plan: make it the plan's next version, or leave it behind.
  acceptFix: (id: string) => call<unknown>(`/v1/goals/${id}/suggestion/accept`, { method: "POST" }),
  dismissFix: (id: string) => call<unknown>(`/v1/goals/${id}/suggestion/dismiss`, { method: "POST" }),
  replan: (id: string, note: string) =>
    call<GoalPlan>(`/v1/goals/${id}/replan`, { method: "POST", body: JSON.stringify({ note }) }),
  // track: whether the plan's new tasks become issues in the goal's tracker, or are kept in the meta-harness.
  approvePlan: (id: string, track = true) => call<GoalPlan>(`/v1/goals/${id}/approve`, { method: "POST", body: JSON.stringify({ track }) }),
  // A held issue: make it a task whatever its size and gaps, or create the smaller issues it splits into.
  runAnyway: (id: string, ref: string) =>
    call<{ goal_id: string; ref: string; run_anyway: boolean }>(`/v1/goals/${id}/issues/run-anyway`, {
      method: "POST",
      body: JSON.stringify({ ref }),
    }),
  // The parts of a held issue as the planner drafts them: each a complete issue, to edit before splitting.
  splitDrafts: (id: string, ref: string) =>
    call<{ ref: string; drafts: SplitDraft[] }>(`/v1/goals/${id}/issues/split-drafts`, { method: "POST", body: JSON.stringify({ ref }) }),
  splitIssue: (id: string, ref: string, issues: SplitDraft[]) =>
    call<{ goal_id: string; ref: string; created: string[] }>(`/v1/goals/${id}/issues/split`, {
      method: "POST",
      body: JSON.stringify({ ref, issues }),
    }),
  // Each of a goal's issues with its task's status and its pull request's state and checks.
  goalIssues: (id: string) => call<{ issues: IssueProgress[] }>(`/v1/goals/${id}/issues`).then((r) => r.issues),
  // The same, with the goal's status and when its issues' states were read from GitHub.
  goalIssuesChecked: (id: string) => call<GoalIssues>(`/v1/goals/${id}/issues`),
};
