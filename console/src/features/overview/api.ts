import type { Need } from "../goals/needActions";
import { call, query } from "../../client";

// A pull request on the Overview's Pull requests row: the one a task opened, with its review and fix runs.
export interface PullRequestCell {
  task_id: string;
  latest_run_id: string | null;
  title: string;
  repo: string;
  pr_url: string;
  opened_at: string;
  state: "in_review" | "waiting" | "blocked" | "left_open" | "merged" | "closed";
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
  // What each goal named in the report needs now, by goal id, read from its records as the report is read:
  // the buttons for its rows.
  actions?: Record<string, GoalAction[]>;
}

// One thing a goal needs from the operator, as the action a page offers (planner/actions.py).
export interface GoalAction extends Need {
  goal_id: string;
  label: string;
}

// POST /v1/overview/ask
export interface WorkAnswer {
  answer: string;
  in_record: boolean;
  // The buttons for the steps the answer suggests, each checked against its goal's records as it was given.
  actions?: SuggestedAction[];
}

// A step an answer suggests. says: for one that opens the goal's page, what the answer said to look at there.
export interface SuggestedAction extends Omit<GoalAction, "action"> {
  action: Need["action"] | "open_goal";
  goal: string;
  says?: "plan" | "goal" | "next_steps";
}

// The overview feature's calls; src/api.ts spreads them into `api`.
export const overviewApi = {
  pullRequests: (days = 91) => call<PullRequests>("/v1/activity" + query({ days })),
  overviewReport: (since?: string | null) => call<WorkReportAnswer>("/v1/overview/report" + query({ since })),
  overviewAsk: (question: string, since?: string | null) =>
    call<WorkAnswer>("/v1/overview/ask", { method: "POST", body: JSON.stringify({ question, since: since ?? undefined }) }),
};
