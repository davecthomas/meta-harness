import { call, query } from "../../client";
import type { Page, Task } from "../../api";

// One review round on a task's pull request, from GET /v1/tasks/{id}/reviews.
export interface ReviewRound {
  round: number;
  verdict: "clean" | "changes";
  reviewer: string | null;
  head_sha: string | null;
  summary: string;
  findings: { path: string | null; line: number | null; body: string }[];
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

// The tasks feature's calls; src/api.ts spreads them into `api`.
export const tasksApi = {
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
  reviews: (id: string) => call<{ rounds: ReviewRound[] }>(`/v1/tasks/${id}/reviews`).then((r) => r.rounds),
  pullDiff: (taskId: string) => call<PullDiff>(`/v1/pulls/${taskId}/diff`),
  commentOnLines: (taskId: string, body: { path: string; line: number; start_line: number | null; body: string }) =>
    call<{ url: string }>(`/v1/pulls/${taskId}/comments`, { method: "POST", body: JSON.stringify(body) }),
  messageTask: (id: string, text: string, when: "next_turn" | "after_stop" | "stop" | "choose") =>
    call<{ message_id: number }>(`/v1/tasks/${id}/messages`, { method: "POST", body: JSON.stringify({ text, when }) }),
  setAttempts: (id: string, count: number) => call<Task>(`/v1/tasks/${id}/attempts`, { method: "POST", body: JSON.stringify({ count }) }),
  attempts: (taskId: string) => call<AttemptsCompare>(`/v1/pulls/${taskId}/attempts`),
  clearTask: (id: string) => call<Task>(`/v1/tasks/${id}/clear`, { method: "POST" }),
  cancelTask: (id: string) =>
    call<Task>(`/v1/tasks/${id}/cancel`, { method: "POST", body: JSON.stringify({ reason: "canceled from the console" }) }),
};
