import { call, query } from "../../client";

// A pull request on the Overview's Pull requests row: the one a task opened, with its review and fix runs.
export interface PullRequestCell {
  task_id: string;
  latest_run_id: string | null;
  title: string;
  repo: string;
  pr_url: string;
  opened_at: string;
  state: "in_review" | "waiting" | "blocked" | "merged" | "closed";
  state_at: string;
  fix_rounds: number;
}

// GET /v1/activity: every pull request of the last days, oldest first, the newest few hundred of them.
export interface PullRequests {
  days: number;
  total: number;
  left_out: number;
  prs: PullRequestCell[];
}

// One line of the Overview report: a goal and what is said of it.
export interface ReportItem {
  goal_id: string;
  goal: string;
  what?: string;
  why?: string;
}

// The planner's report on all running goals.
export interface WorkReportBody {
  headline: string;
  moving: ReportItem[];
  not_moving: ReportItem[];
  needs_you: ReportItem[];
  spend: string;
  finished: string;
  goals: number;
  written_at: string;
}

// GET /v1/overview/report: report is null before the first is written.
export interface WorkReportAnswer {
  report: WorkReportBody | null;
  written: boolean;
  note?: string | null;
  error?: string | null;
}

// POST /v1/overview/ask
export interface WorkAnswer {
  answer: string;
  in_record: boolean;
}

// The overview feature's calls; src/api.ts spreads them into `api`.
export const overviewApi = {
  pullRequests: (days = 91) => call<PullRequests>("/v1/activity" + query({ days })),
  overviewReport: (since?: string | null) => call<WorkReportAnswer>("/v1/overview/report" + query({ since })),
  overviewAsk: (question: string, since?: string | null) =>
    call<WorkAnswer>("/v1/overview/ask", { method: "POST", body: JSON.stringify({ question, since: since ?? undefined }) }),
};
