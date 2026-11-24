import { call, query } from "../../client";

// A cost time series, from GET /v1/costs: the buckets, one series per value of the dimension grouped by, the
// totals, and the trend the server fitted through them.
export interface Costs {
  from: string;
  to: string;
  bucket: "day" | "week" | "month";
  group_by: string;
  buckets: string[];
  series: { key: string; usd: number[]; total: number }[];
  totals: number[];
  // The least-squares line through the buckets' totals; null with fewer than two buckets.
  trend: { slope: number; start: number; end: number } | null;
  total: number;
  daily_average: number;
  previous_total: number;
  change_percent: number | null;
  projected_month: number | null;
  top_tasks: { task_id: string; title: string | null; usd: number }[];
  // Names for keys that are ids, such as a goal's title.
  labels: Record<string, string>;
  // Every value a filter may take.
  choices: Record<string, string[]>;
  sources: Record<string, { configured: boolean; harvested_at?: string; error?: string | null; note: string }>;
  // all: an admin's view of every cost. own: the model cost of this user's own work.
  scope: "all" | "own";
}

// label: the labels of the issue a task worked on. outcome: what became of its pull request (merged, closed,
// open, or none opened).
export type CostFilter = "source" | "kind" | "goal" | "repo" | "model" | "label" | "outcome";
export type CostQuery = { from: string; to: string; bucket: string; group_by: string } & Partial<Record<CostFilter, string>>;

// An average and a median over `count` things; both null with nothing to count.
export interface Both {
  average: number | null;
  median: number | null;
  count: number;
}

// The key numbers, from GET /v1/performance, for the tasks whose last run finished in the days asked.
export interface Performance {
  since: string;
  until: string;
  tasks: number;
  merged: number;
  cost_per_task: Both;
  cost_per_pull_request: Both;
  cost_per_goal: Both;
  dollar_hours_per_completed_task: Both;
  // How many tries a merged task took to get to green: its own runs and its fix runs.
  tries_to_green?: Both;
  // The merged tasks that got to green with one run and no fix: how many, of how many, and the share.
  first_try_green?: { rate: number | null; count: number; of: number };
  total_usd: string;
  wasted_usd: string;
  review_loop_usd: string;
  // What the goals' planning tasks cost: in the total and in each goal's cost, and not among the tasks.
  planning_usd?: string;
  hours: number;
}

// What each goal's model cost is made of, from GET /v1/costs/goals, most expensive goal first.
export interface GoalCosts {
  from: string;
  to: string;
  // The kinds of cost, in the order to show them.
  kinds: string[];
  goals: { goal_id: string; title: string; status: string; total: number; parts: Record<string, number> }[];
}

// The performance feature's calls; src/api.ts spreads them into `api`.
export const performanceApi = {
  performance: (span: { from: string; to: string }) => call<Performance>("/v1/performance" + query(span)),
  goalCosts: (span: { from: string; to: string }) => call<GoalCosts>("/v1/costs/goals" + query(span)),
  costs: (params: CostQuery) => call<Costs>("/v1/costs" + query(params)),
};
