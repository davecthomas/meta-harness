import type { Task } from "../../api";

// The line under the Overview's Needs you number: what the number is made of, so it is never a bare count.
// tasks are the waiting tasks loaded so far; held and pastBudget are counts; total is the tile's number.
export function needsYouNote(tasks: Pick<Task, "status">[], held: number, pastBudget: number, total: number | null): string {
  if (total === null) return "";
  if (total === 0) return "Nothing waits on you.";
  const count = (status: string) => tasks.filter((t) => t.status === status).length;
  const parts = [
    [count("failed"), "failed"],
    [count("review_unresolved"), "with an unresolved review"],
    [count("ready"), "ready to merge"],
    [held, "held for budget or review"],
    [pastBudget, pastBudget === 1 ? "goal past its budget" : "goals past their budget"],
  ] as const;
  const said = parts.filter(([n]) => n > 0).map(([n, what]) => `${n} ${what}`);
  const named = parts.reduce((sum, [n]) => sum + n, 0);
  if (named < total) said.push(`${total - named} more`);
  return `${said.join(", ")}. Each says what to do.`;
}
