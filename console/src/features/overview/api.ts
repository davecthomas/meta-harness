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

// The overview feature's calls; src/api.ts spreads them into `api`.
export const overviewApi = {
  pullRequests: (days = 91) => call<PullRequests>("/v1/activity" + query({ days })),
};
