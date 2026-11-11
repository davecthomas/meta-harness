import { Alert, Anchor, List, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import type { IssueProgress, Task } from "../../api";
import { moneyText } from "../../money";

const FAILED = ["failed", "review_unresolved"];

// The goal's issues whose task failed, ended unresolved or is held for budget, or that the planner refused.
export function failures(issues: IssueProgress[]): IssueProgress[] {
  return issues.filter((i) => i.refused_reason || i.task_waiting_on || (i.task_status !== null && FAILED.includes(i.task_status)));
}

// A goal with failed work says so above its board: each failed or unresolved task with why, linked to its page,
// and each issue the planner refused. Nothing when there is none.
export function GoalFailures({ issues, tasks }: { issues: IssueProgress[]; tasks: Record<string, Task> }) {
  const failed = failures(issues);
  if (!failed.length) return null;
  const count = `${failed.length} ${failed.length === 1 ? "issue needs" : "issues need"} you`;
  return (
    <Alert color="red" title={count} icon={<IconAlertTriangle size={18} />} mb="md" role="alert">
      <List size="sm" spacing={4}>
        {failed.map((i) => {
          const name = (i.task_id && tasks[i.task_id]?.title) || i.title || i.ref.replace(/^github:/, "");
          const state = i.task_waiting_on === "budget" ? "out of budget" : i.task_status;
          const why = i.refused_reason ? `refused: ${i.refused_reason}` : `${state}: ${moneyText(i.task_status_reason) ?? "no reason was recorded"}`;
          return (
            <List.Item key={i.ref}>
              {i.task_id ? <Anchor component={Link} to={`/tasks/${i.task_id}`} size="sm">{name}</Anchor> : <Text span size="sm">{name}</Text>}
              <Text span size="sm"> ({why})</Text>
            </List.Item>
          );
        })}
      </List>
    </Alert>
  );
}
