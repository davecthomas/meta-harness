import { call, query } from "../../client";
import type { Page, Task } from "../../api";

// One review round on a task's pull request, from GET /v1/tasks/{id}/reviews.
export interface ReviewRound {
  round: number;
  verdict: "clean" | "changes";
  reviewer: string | null;
  head_sha: string | null;
  summary: string;
  // n numbers a round's findings from 1. dismissed: who dismissed the finding and why, when an operator did.
  findings: { n?: number; path: string | null; line: number | null; body: string; dismissed?: { by: string | null; reason: string } | null }[];
  review_url: string | null;
  reviewed_at: string | null;
  run_id: string | null;
  next: { kind: "fix" | "merged" | "reviewed_again" | "waiting" | "ended" | "under_way"; run_id?: string | null; attempt?: number; reason?: string | null };
}

// A task's pull request: the files it changes with their patches, from GET /v1/pulls/{task_id}/diff.
export interface PullDiff {
  repo: string;
  number: number;
  head_sha: string;
  state: string;
  files: { path: string; status: string; additions: number; deletions: number; patch: string | null }[];
}

// A task's attempts side by side, from GET /v1/pulls/{task_id}/attempts.
export interface AttemptsCompare {
  task_id: string;
  waiting: boolean;
  attempts: {
    attempt: number;
    run_id: string | null;
    candidate: string | null;
    decision: string;
    detail: string | null;
    spent_usd: string | null;
    files: { path: string; additions: number; deletions: number }[];
    patch: string;
  }[];
}

// What runs spent, in all and by kind (the task's own runs, reviews, fixes), with their turns and tokens.
export interface SpendTotals {
  usd: string;
  by_kind: Record<string, string>;
  runs: number;
  turns: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
}

export interface TaskSpend {
  task_id: string;
  max_usd: string | null;
  runs: {
    run_id: string | null;
    attempt: number;
    kind: string;
    tier: string | null;
    candidate: string | null;
    turns: number | null;
    model_calls: number | null;
    input_tokens: number | null;
    cached_input_tokens: number | null;
    output_tokens: number | null;
    usd: string;
    status: string | null;
    end_reason: string | null;
  }[];
  totals: SpendTotals;
}

// What the supervisor last did, from GET /v1/supervisor. every_seconds 0: it is turned off.
export interface SupervisorState {
  every_seconds: number;
  last_pass: { at?: string; error?: string | null; failed_at?: string; looked_at?: number; errors?: string[]; owners?: Record<string, number> } | null;
  sweeps_failing?: Record<string, { at: string; errors: string[] }>;
}

// The tasks feature's calls; src/api.ts spreads them into `api`.
export const tasksApi = {
  tasks: async (
    // pr: true for tasks with a pull request, false for tasks without one.
    // waiting: true for tasks waiting on an operator (held for budget).
    // hasIt: only the tasks the supervisor found this owner to have, longest in their situation first.
    params: { statuses?: string[]; goalId?: string; pr?: boolean; waiting?: boolean; hasIt?: "operator" | "supervisor" | "workflow" | "scheduler" | "planner" },
    offset: number,
    limit: number,
  ): Promise<Page<Task>> => {
    const pr = params.pr === undefined ? undefined : String(params.pr);
    const r = await call<{ tasks: Task[]; total: number }>(
      "/v1/tasks" + query({ status: params.statuses?.join(","), goal_id: params.goalId, pr, waiting: params.waiting ? "true" : undefined, has_it: params.hasIt, offset, limit }),
    );
    return { items: r.tasks, total: r.total };
  },
  task: (id: string) => call<Task>(`/v1/tasks/${id}`),
  // The supervisor's last pass, how often it runs, and the goals whose latest sweep could not finish.
  supervisor: () => call<SupervisorState>("/v1/supervisor"),
  reviews: (id: string) => call<{ rounds: ReviewRound[] }>(`/v1/tasks/${id}/reviews`).then((r) => r.rounds),
  pullDiff: (taskId: string) => call<PullDiff>(`/v1/pulls/${taskId}/diff`),
  commentOnLines: (taskId: string, body: { path: string; line: number; start_line: number | null; body: string }) =>
    call<{ url: string }>(`/v1/pulls/${taskId}/comments`, { method: "POST", body: JSON.stringify(body) }),
  messageTask: (id: string, text: string, when: "next_turn" | "after_stop" | "stop" | "choose") =>
    call<{ message_id: number }>(`/v1/tasks/${id}/messages`, { method: "POST", body: JSON.stringify({ text, when }) }),
  setAttempts: (id: string, count: number) => call<Task>(`/v1/tasks/${id}/attempts`, { method: "POST", body: JSON.stringify({ count }) }),
  attempts: (taskId: string) => call<AttemptsCompare>(`/v1/pulls/${taskId}/attempts`),
  // An operator's word on a review its task holds for them: dismiss a finding with why, or go on to the fix now.
  dismissFinding: (id: string, round: number, finding: number, reason: string) =>
    call<Task>(`/v1/tasks/${id}/reviews/${round}/findings/${finding}/dismiss`, { method: "POST", body: JSON.stringify({ reason }) }),
  // An operator's own review: take the round over, give it back, or give the verdict it waits for.
  claimReview: (id: string) => call<Task>(`/v1/tasks/${id}/review/claim`, { method: "POST" }),
  releaseReview: (id: string) => call<Task>(`/v1/tasks/${id}/review/release`, { method: "POST" }),
  reviewVerdict: (id: string, body: { verdict: "clean" | "changes"; keep: number[]; findings: { body: string; path?: string; line?: number }[] }) =>
    call<Task>(`/v1/tasks/${id}/review/verdict`, { method: "POST", body: JSON.stringify(body) }),
  proceedReview: (id: string, round: number) => call<Task>(`/v1/tasks/${id}/reviews/${round}/proceed`, { method: "POST" }),
  // Where the task's money went: its runs, with their turns, tokens and cost.
  taskSpend: (id: string) => call<TaskSpend>(`/v1/tasks/${id}/spend`),
  clearTask: (id: string) => call<Task>(`/v1/tasks/${id}/clear`, { method: "POST" }),
  // Sends an ended task back to the queue, with a spend limit for the retry's runs.
  retryTask: (id: string, max_usd: number) => call<Task>(`/v1/tasks/${id}/retry`, { method: "POST", body: JSON.stringify({ max_usd }) }),
  // Asks for the task's pull request to be merged through the merge queue.
  mergeTask: (id: string) => call<Task>(`/v1/tasks/${id}/merge`, { method: "POST" }),
  // An operator's answer to a task held for budget: raise (with a new limit, or none to look again) or end.
  decideBudget: (id: string, action: "raise" | "end", max_usd?: number) =>
    call<Task>(`/v1/tasks/${id}/budget`, { method: "POST", body: JSON.stringify({ action, ...(max_usd ? { max_usd } : {}) }) }),
  cancelTask: (id: string) =>
    call<Task>(`/v1/tasks/${id}/cancel`, { method: "POST", body: JSON.stringify({ reason: "canceled from the console" }) }),
};
