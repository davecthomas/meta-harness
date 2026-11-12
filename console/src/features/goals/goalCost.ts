import type { Goal } from "../../api";
import { budgetStanding } from "./budget";

// A goal's cost against its budget, for the Goals page: what it has cost, how much of its budget that is, and
// the words for where it stands, as its goal page would say them.

export type CostRow = Pick<
  Goal,
  "id" | "status" | "max_usd" | "budget_mode" | "budget_block_at" | "budget_state" | "spent_usd" | "cost_usd" | "cost_unknown" | "created_at"
>;

export const money = (value: number) => `$${value.toFixed(2)}`;

export const costOf = (goal: CostRow): number | null => (goal.cost_usd == null ? null : Number(goal.cost_usd));

// How much of the budget the goal has used, in percent (past 100 when over), or null with no budget or no cost.
export function usedPercent(goal: CostRow): number | null {
  const cost = costOf(goal);
  if (!goal.max_usd || cost === null || Number(goal.max_usd) <= 0) return null;
  return (100 * cost) / Number(goal.max_usd);
}

// The words under the budget: "12% used", "over by $4.10", "stopped at its budget", or "no budget".
export function budgetLine(goal: CostRow): string {
  if (!goal.max_usd) return "no budget";
  const cost = costOf(goal);
  if (cost === null) return "used: unknown";
  if (budgetStanding(goal, cost) === "blocked") return "stopped at its budget";
  const over = cost - Number(goal.max_usd);
  if (over > 0) return `over by ${money(over)}`;
  return `${Math.round(usedPercent(goal) ?? 0)}% used`;
}

export type SortKey = "newest" | "cost" | "used";

// The goals in the order asked for: newest first (as the API lists them), most expensive first, or most of
// their budget used first. A goal whose cost is unknown, or with no budget when sorting by use, goes last.
export function sortGoals<T extends CostRow>(goals: T[], key: SortKey): T[] {
  if (key === "newest") return goals;
  const value = (g: T) => (key === "cost" ? costOf(g) : usedPercent(g));
  return [...goals].sort((a, b) => (value(b) ?? -1) - (value(a) ?? -1));
}

// What the listed goals cost together, how many costs could not be read, and their budgets together when
// every one has a budget.
export function totals(goals: CostRow[]): { cost: number; unknown: number; budget: number | null } {
  const known = goals.filter((g) => costOf(g) !== null);
  return {
    cost: known.reduce((sum, g) => sum + (costOf(g) ?? 0), 0),
    unknown: goals.length - known.length,
    budget: goals.length && goals.every((g) => g.max_usd) ? goals.reduce((sum, g) => sum + Number(g.max_usd), 0) : null,
  };
}
