import type { Task } from "../../api";

// The status a task shows: a working task whose pull request is open is in its review loop (checks, review,
// fixes), on every page that shows it.
export function shownStatus(task: Pick<Task, "status" | "pr_url">): string {
  return task.status === "running" && task.pr_url ? "in_review" : task.status;
}
