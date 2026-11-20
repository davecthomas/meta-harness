import { useEffect, useState, type ReactNode } from "react";
import { Button, Group, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { IconGitMerge, IconRefresh } from "@tabler/icons-react";
import { api, type IssueProgress, type Task } from "../../api";
import { ConfirmButton } from "../../components/ConfirmButton";
import { tasksApi } from "../tasks/api";
import { retryLimit } from "../tasks/Remedies";
import { reviewLink } from "../tasks/reviewing";
import { HeldActions } from "./sizing";

// The buttons for a need about a task or an issue, from what it is now, not from what the update said when it
// was written: a review no fix settled is merged as is or sent back for another fix right here, a failed task
// is retried, an issue held for its size is split or run as one. A need already settled says so.

const findings = (taskId: string) => `/tasks/${taskId}?tab=reviews`;

export function TaskNeedAction({ taskId, onDone }: { taskId: string; onDone: () => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    tasksApi.task(taskId).then(setTask, () => setTask(null));
  }, [taskId]);
  const open = <Button size="xs" variant="subtle" component={Link} to={`/tasks/${taskId}`}>Open the task</Button>;
  if (!task) return open;
  const run = (act: () => Promise<unknown>) => async () => {
    try {
      await act();
      setError(null);
      onDone();
      setTask(await tasksApi.task(taskId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const merge = (label: string) => (
    <ConfirmButton
      size="xs"
      color="teal"
      label={label}
      title="Merge this pull request?"
      says="It merges once its checks pass. A merge to main cannot be taken back from here."
      confirm="Merge"
      leftSection={<IconGitMerge size={14} />}
      onConfirm={run(() => tasksApi.mergeTask(taskId))}
    />
  );
  const retry = (label: string) => (
    <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={run(() => tasksApi.retryTask(taskId, retryLimit(task, false)))}>
      {label}
    </Button>
  );
  let buttons;
  switch (task.status) {
    case "review_unresolved":
      buttons = task.pr_url ? (
        <>
          {merge("Merge as is")}
          {retry("Try another fix")}
          <Button size="xs" variant="subtle" component={Link} to={findings(taskId)}>See the findings</Button>
        </>
      ) : (
        <>{retry("Try again")}{open}</>
      );
      break;
    case "ready":
      buttons = <>{merge("Merge")}<Button size="xs" variant="subtle" component={Link} to={reviewLink(taskId)}>See the changes</Button></>;
      break;
    case "failed":
    case "canceled":
      buttons = <>{retry("Retry")}{open}</>;
      break;
    case "closed":
      return <Text size="sm" c="dimmed">Done already.</Text>;
    default:
      // Running or waiting again: the agent has it, and its page shows where it is.
      return <Group gap="xs"><Text size="sm" c="dimmed">Under way again.</Text>{open}</Group>;
  }
  return (
    <Group gap="xs" wrap="wrap">
      {buttons}
      {error && <Text size="xs" c="red">{error}</Text>}
    </Group>
  );
}

// Split or run as one, for an issue the planner held for its size.
// fallback: shown until the issue is read, and when it cannot be, so the need always has a button.
export function HeldIssueAction({ goalId, issueRef, label, fallback, onDone }: { goalId: string; issueRef: string; label: string | null; fallback: ReactNode; onDone: () => void }) {
  const [issue, setIssue] = useState<IssueProgress | null>(null);
  useEffect(() => {
    api.goalIssuesChecked(goalId).then((found) => setIssue(found.issues.find((i) => i.ref === issueRef) ?? null), () => setIssue(null));
  }, [goalId, issueRef]);
  if (!issue) return <>{fallback}</>;
  if (issue.task_id) return <TaskNeedAction taskId={issue.task_id} onDone={onDone} />;
  // Not held, or held for nothing there is a choice about: what the update offered.
  if (!issue.held_reason) return <>{fallback}</>;
  return <HeldActions goalId={goalId} label={label} issue={issue} onChanged={onDone} />;
}
