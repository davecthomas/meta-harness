import { Alert } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import type { IssueProgress, Task } from "../../api";
import { FailureGroups, groupFailures, type FailureGroup } from "../tasks/FailureGroups";

const FAILED = ["failed", "review_unresolved"];

// The goal's issues whose task failed, ended unresolved or is held for budget, or that the planner refused.
export function failures(issues: IssueProgress[]): IssueProgress[] {
  return issues.filter((i) => i.refused_reason || i.task_waiting_on || (i.task_status !== null && FAILED.includes(i.task_status)));
}

// The goal's failed issues by what went wrong: those that failed the same way are one group.
export function groups(issues: IssueProgress[], tasks: Record<string, Task> = {}): FailureGroup[] {
  return groupFailures(
    failures(issues).map((i) => ({
      key: i.ref,
      name: (i.task_id && tasks[i.task_id]?.title) || i.title || i.ref.replace(/^[a-z]+:/, ""),
      taskId: i.task_id,
      issueRef: i.ref,
      status: i.task_status,
      reason: i.task_status_reason,
      waitingOn: i.task_waiting_on,
      refused: i.refused_reason,
      max_usd: i.task_id ? tasks[i.task_id]?.max_usd : undefined,
    })),
  );
}

// A goal with failed work says so above its board: what went wrong, why, and what to do, once for the issues
// that failed the same way, with the action that fixes them. Nothing when there is none.
// ended: the goal is met or canceled. Its failed work is then said as what was left, with no button: an ended
// goal asks nothing of anyone, and a retry would start work the goal no longer wants.
export function GoalFailures({ issues, tasks, onChanged, ended = false }: { issues: IssueProgress[]; tasks: Record<string, Task>; onChanged?: () => void; ended?: boolean }) {
  const found = groups(issues, tasks);
  const total = found.reduce((n, g) => n + g.items.length, 0);
  if (!total) return null;
  if (ended) {
    return (
      <Alert color="gray" variant="light" title={`${total} ${total === 1 ? "issue was" : "issues were"} left unfinished when the goal ended`} mb="md" data-testid="goal-left-unfinished">
        Nothing here needs you. To take {total === 1 ? "it" : "them"} up again, reopen the goal, and each can be retried from here.
      </Alert>
    );
  }
  const count = `${total} ${total === 1 ? "issue needs" : "issues need"} you`;
  return (
    <Alert color="red" title={count} icon={<IconAlertTriangle size={18} />} mb="md" role="alert">
      <FailureGroups groups={found} onChanged={onChanged} />
    </Alert>
  );
}
