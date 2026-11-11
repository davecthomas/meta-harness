import { call, query } from "../../client";
import type { Goal, IssueLink, Page } from "../../api";

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

// A feature goal's plan as the planner shows it: its newest version, or none while the first is drafted.
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
    drafts: PlanDraft[];
    // Why the plan cannot be approved; empty when it can.
    invalid: string[];
    source: "planned" | "edited";
    task_id: string | null;
    note: string | null;
    created: Record<string, string>;
    created_by: string;
    created_at: string;
  } | null;
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
  limits: () => call<{ max_parallel: number | null; max_sandboxes: number | null }>("/v1/limits"),
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
  // Where the goal's money went: each task's spend, and the goal's by kind of run.
  goalSpend: (id: string) => call<GoalSpend>(`/v1/goals/${id}/spend`),
  // A feature goal's plan: read it, save edited drafts as its next version, ask for a new one, approve it.
  goalPlan: (id: string) => call<GoalPlan>(`/v1/goals/${id}/plan`),
  savePlan: (id: string, drafts: PlanDraft[]) =>
    call<GoalPlan>(`/v1/goals/${id}/plan`, { method: "PUT", body: JSON.stringify({ drafts }) }),
  replan: (id: string, note: string) =>
    call<GoalPlan>(`/v1/goals/${id}/replan`, { method: "POST", body: JSON.stringify({ note }) }),
  approvePlan: (id: string) => call<GoalPlan>(`/v1/goals/${id}/approve`, { method: "POST" }),
  // Each of a goal's issues with its task's status and its pull request's state and checks.
  goalIssues: (id: string) => call<{ issues: IssueProgress[] }>(`/v1/goals/${id}/issues`).then((r) => r.issues),
};
