import { useEffect, useState } from "react";
import { Alert, Anchor, Group, Stack, Text } from "@mantine/core";
import { IconHourglass } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type IssueProgress, type Task } from "../../api";
import { IssueLink, StatusBadge, when } from "../../components/ui";
import { RaiseGoalBudget } from "../goals/budget";
import { holdWords } from "./hold";

// A task that has not started says why at the top of its page in a sentence, and what would start it: the action
// when it is the operator's to take, or that nothing is needed. A blocked task shows each issue it waits on.

// The tasks a hold waits on, each by its title: a title says what the task is, an ID does not.
function TaskLinks({ ids }: { ids: string[] }) {
  const [titles, setTitles] = useState<Record<string, string>>({});
  useEffect(() => {
    ids.forEach((id) => api.task(id).then((t) => setTitles((was) => ({ ...was, [id]: t.title })), () => undefined));
  }, [ids.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Stack gap={2}>
      {ids.map((id) => (
        <Anchor key={id} component={Link} to={`/tasks/${id}`} size="sm">{titles[id] ?? "the other task"}</Anchor>
      ))}
    </Stack>
  );
}

// In a list or on a card: why a queued or blocked task waits, in a line. Null for a task that does not wait.
export function waitReason(task: Pick<Task, "status" | "held" | "depends_on">): string | null {
  if (task.status === "blocked") {
    const n = task.depends_on?.length ?? 0;
    return n ? `waits on ${n} ${n === 1 ? "issue" : "issues"}` : "waits on other issues";
  }
  if (task.status !== "queued") return null;
  return task.held ? holdWords(task.held).short : "starts in a moment";
}

function Blocked({ task }: { task: Task }) {
  const [issues, setIssues] = useState<Record<string, IssueProgress>>({});
  const [goalStatus, setGoalStatus] = useState<string | null>(null);
  useEffect(() => {
    if (!task.goal_id) return;
    api.goalIssuesChecked(task.goal_id).then(
      (found) => {
        setIssues(Object.fromEntries(found.issues.map((i) => [i.ref, i])));
        setGoalStatus(found.goal_status ?? null);
      },
      () => setIssues({}),
    );
  }, [task.goal_id]);
  const refs = task.depends_on ?? [];
  // A goal that has ended starts none of its tasks, whatever they wait on: that comes first.
  if (task.goal_id && (goalStatus === "canceled" || goalStatus === "met")) {
    return (
      <Stack gap={6} data-testid="goal-ended">
        <Group gap="xs">
          <Text size="sm">Its goal is</Text>
          <StatusBadge status={goalStatus} />
          <Text size="sm">so it will not start. Cancel the task, or reopen the goal.</Text>
          <Anchor component={Link} to={`/goals/${task.goal_id}`} size="sm">Open the goal</Anchor>
        </Group>
      </Stack>
    );
  }
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
                <Anchor component={Link} to={`/tasks/${issue.task_id}`} size="sm">Open its task</Anchor>
              </>
            )}
          </Group>
        );
      })}
    </Stack>
  );
}

// What to do about a hold, when it is the operator's to act on, or what it waits for.
function Action({ task, onChanged }: { task: Task; onChanged?: () => void }) {
  const held = task.held!;
  const detail = held.detail ?? {};
  const goalId = (detail.goal_id as string | undefined) ?? task.goal_id;
  const holders = (detail.holders as string[] | undefined) ?? [];
  switch (held.kind) {
    case "goal_budget":
      return goalId ? <RaiseGoalBudget goalId={goalId} onResumed={onChanged} /> : null;
    case "goal_status":
      return goalId ? <Anchor component={Link} to={`/goals/${goalId}`} size="sm">Open the goal</Anchor> : null;
    case "repo_area":
      return holders.length ? (
        <Group gap="xs" align="flex-start">
          <Text size="sm">Waiting for:</Text>
          <TaskLinks ids={holders} />
        </Group>
      ) : null;
    default:
      return null;
  }
}

export function WaitingToStart({ task, onChanged }: { task: Task; onChanged?: () => void }) {
  if (task.status !== "queued" && task.status !== "blocked") return null;
  // A task with a pull request has run before: it waits to carry on, not to start.
  const title = task.pr_url ? "Waiting its turn" : "Waiting to start";
  const words = task.held ? holdWords(task.held) : null;
  return (
    <Alert color="blue" title={title} icon={<IconHourglass size={18} />} mb="md" data-testid="waiting-to-start">
      {task.status === "blocked" ? (
        <Blocked task={task} />
      ) : words ? (
        <Stack gap="xs" align="flex-start">
          <Text size="sm">{words.line}</Text>
          <Action task={task} onChanged={onChanged} />
          <Text size="xs" c="dimmed">{words.yours ? "" : "Nothing for you to do. "}Waiting since {when(task.held!.since)}.</Text>
        </Stack>
      ) : (
        <Text size="sm">It starts in a moment. Nothing for you to do.</Text>
      )}
    </Alert>
  );
}
