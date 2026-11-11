import { useEffect, useState } from "react";
import { Alert, Anchor, Group, Stack, Text } from "@mantine/core";
import { IconHourglass } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type IssueProgress, type Task } from "../../api";
import { IssueLink, StatusBadge, when } from "../../components/ui";
import { ResumeBudget } from "../goals/budget";

// A task that has not started says why at the top of its page, with what would start it. A queued task shows
// the scheduler's reason for holding it (the task's `held`) and the action that fits its kind; a blocked one
// shows each issue it waits on.

const TaskLinks = ({ ids }: { ids: string[] }) => (
  <Group gap="xs">
    {ids.map((id) => (
      <Anchor key={id} component={Link} to={`/tasks/${id}`} ff="monospace" size="sm">{id}</Anchor>
    ))}
  </Group>
);

// In a list or on a card: why a queued or blocked task waits, in a line. Null for a task that does not wait.
export function waitReason(task: Pick<Task, "status" | "held" | "depends_on">): string | null {
  if (task.status === "blocked") {
    const n = task.depends_on?.length ?? 0;
    return n ? `waits on ${n} ${n === 1 ? "issue" : "issues"}` : "waits on other issues";
  }
  if (task.status !== "queued") return null;
  return task.held?.reason ?? "starts at the scheduler's next pass";
}

function Blocked({ task }: { task: Task }) {
  const [issues, setIssues] = useState<Record<string, IssueProgress>>({});
  useEffect(() => {
    if (!task.goal_id) return;
    api.goalIssues(task.goal_id).then((found) => setIssues(Object.fromEntries(found.map((i) => [i.ref, i]))), () => setIssues({}));
  }, [task.goal_id]);
  const refs = task.depends_on ?? [];
  return (
    <Stack gap={6}>
      <Text size="sm">It waits on {refs.length === 1 ? "another issue" : "other issues"}, and is queued once {refs.length === 1 ? "it is" : "each is"} closed.</Text>
      {refs.map((ref) => {
        const issue = issues[ref];
        return (
          <Group key={ref} gap="xs" data-testid={`waits-on-${ref}`}>
            <IssueLink issueRef={ref} />
            {issue && <Text size="sm">{issue.state}</Text>}
            {issue?.task_id && (
              <>
                <StatusBadge status={issue.task_status ?? "queued"} />
                <Anchor component={Link} to={`/tasks/${issue.task_id}`} ff="monospace" size="xs">{issue.task_id}</Anchor>
              </>
            )}
          </Group>
        );
      })}
    </Stack>
  );
}

function Action({ task, onChanged }: { task: Task; onChanged?: () => void }) {
  const held = task.held!;
  const detail = held.detail ?? {};
  const goalId = (detail.goal_id as string | undefined) ?? task.goal_id;
  const holders = (detail.holders as string[] | undefined) ?? [];
  switch (held.kind) {
    case "goal_budget":
      return goalId ? (
        <Group gap="sm">
          <ResumeBudget goalId={goalId} size="xs" onResumed={onChanged} />
          <Anchor component={Link} to={`/goals/${goalId}`} size="sm">Raise the goal's budget on its page</Anchor>
        </Group>
      ) : null;
    case "goal_status":
      return goalId ? (
        <Group gap="xs">
          <Text size="sm">Its goal is</Text>
          <StatusBadge status={String(detail.status ?? "not active")} />
          <Anchor component={Link} to={`/goals/${goalId}`} size="sm">Open the goal</Anchor>
        </Group>
      ) : null;
    case "goal_parallel":
    case "system_parallel":
      return (
        <Group gap="xs">
          <Text size="sm">These tasks hold the places:</Text>
          <TaskLinks ids={holders} />
        </Group>
      );
    case "repo_area":
      return (
        <Group gap="xs">
          <Text size="sm">Held by</Text>
          <TaskLinks ids={holders} />
          <Text size="sm">
            on <Text span ff="monospace" size="xs">{((detail.held_paths as string[] | undefined) ?? []).join(", ") || "the whole repo"}</Text>, which this task's{" "}
            <Text span ff="monospace" size="xs">{((detail.paths as string[] | undefined) ?? []).join(", ")}</Text> shares.
          </Text>
        </Group>
      );
    case "kill_switch":
    case "draining":
      return <Anchor component={Link} to="/" size="sm">See the Overview</Anchor>;
    default:
      return null;
  }
}

export function WaitingToStart({ task, onChanged }: { task: Task; onChanged?: () => void }) {
  if (task.status !== "queued" && task.status !== "blocked") return null;
  return (
    <Alert color="blue" title="Waiting to start" icon={<IconHourglass size={18} />} mb="md" data-testid="waiting-to-start">
      {task.status === "blocked" ? (
        <Blocked task={task} />
      ) : task.held ? (
        <Stack gap="sm" align="flex-start">
          <Text size="sm">The scheduler holds it: {task.held.reason}.</Text>
          <Action task={task} onChanged={onChanged} />
          <Text size="xs" c="dimmed">Waiting for this since {when(task.held.since)}. The scheduler last looked {when(task.held.last_checked)}.</Text>
        </Stack>
      ) : (
        <Text size="sm">Nothing holds it: it starts at the scheduler's next pass.</Text>
      )}
    </Alert>
  );
}
