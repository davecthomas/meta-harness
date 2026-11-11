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

export type CostFilter = "source" | "kind" | "goal" | "repo" | "model";
export type CostQuery = { from: string; to: string; bucket: string; group_by: string } & Partial<Record<CostFilter, string>>;

// The performance feature's calls; src/api.ts spreads them into `api`.
export const performanceApi = {
  costs: (params: CostQuery) => call<Costs>("/v1/costs" + query(params)),
};
