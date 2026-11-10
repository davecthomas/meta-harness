import { call, query } from "../../client";

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

// The activity feature's calls; src/api.ts spreads them into `api`.
export const activityApi = {
  activity: (groupBy: "goal" | "repo" | "label", days = 91) => call<Activity>("/v1/activity" + query({ group_by: groupBy, days })),
};
