import { useEffect, useState, type ReactNode } from "react";
import { Button, Group, List, Stack, Text } from "@mantine/core";
import { Link, useNavigate } from "react-router-dom";
import { IconCheck, IconGitMerge, IconRefresh } from "@tabler/icons-react";
import { api, type IssueProgress, type Task } from "../../api";
import { ConfirmButton } from "../../components/ConfirmButton";
import { tasksApi } from "../tasks/api";
import { retryLimit } from "../tasks/Remedies";
import { issueUrl } from "../trackers/refs";
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

// What resolves a need: the page's own button for it. Every need has one.
export type NeedAction =
  | "approve_plan" | "accept_fix" | "merge" | "review" | "open_task" | "raise_budget" | "open_issue"
  | "settle_review" | "retry" | "split_or_run" | "fix_plan";
// changes: for accept_fix, what the planner's fix changes in the plan, a line each.
export type Need = { what: string; ref: string; action?: NeedAction | ""; task_id?: string; pr_url?: string; changes?: string[] };

// Events the goal page listens for, so a need is resolved where it is shown: the plan's approval, and the
// goal's settings (its budget).
export const APPROVE_PLAN = "mh:approve-plan";
// Opens the plan's Plan again, for a plan that cannot be approved as it is.
export const REPLAN = "mh:replan";
export const OPEN_SETTINGS = "mh:open-settings";
export const SHOW_ISSUES = "mh:show-issues";
// Sent once the plan changed from here (a fix accepted or dismissed), so the plan's section reads it again.
export const PLAN_CHANGED = "mh:plan-changed";
// What a link to a goal's page asks it to do on arrival, `?do=`, and the event that does it.
export const DO_BY_EVENT: Record<string, string> = { [APPROVE_PLAN]: "approve", [REPLAN]: "replan", [OPEN_SETTINGS]: "budget", [SHOW_ISSUES]: "issues" };
export const EVENT_BY_DO: Record<string, string> = Object.fromEntries(Object.entries(DO_BY_EVENT).map(([event, name]) => [name, event]));

// The button that resolves one need, or takes the operator to where they do. Every place the planner asks or
// suggests something shows this one, so a need has the same button wherever it is read.
// planning: the goal is being planned, so a need written before needs had actions, about no issue, is the
// plan's approval.
// away: the button is shown somewhere other than its goal's page (the Overview). What the goal's page does in
// place (its plan's approval, its Plan again, its budget, its issues) is then a link there that opens it.
export function NeedButton({ need, planning, goalId, label, onDone, away = false }: { need: Need; planning: boolean; goalId: string; label: string | null; onDone: () => void; away?: boolean }) {
  const changed = () => {
    window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
    onDone();
  };
  const navigate = useNavigate();
  // On the goal's page a press sends its event; away from it, the same press opens the page with that event
  // sent on arrival (GoalPage reads `do`).
  const send = (name: string) => () =>
    void (away ? navigate(`/goals/${goalId}?do=${DO_BY_EVENT[name]}`) : window.dispatchEvent(new CustomEvent(name)));
  const task = need.task_id ?? "";
  const action = need.action || (planning && !need.ref ? "approve_plan" : "");
  switch (action) {
    case "approve_plan":
      return <Button size="xs" color="teal" leftSection={<IconCheck size={14} />} onClick={send(APPROVE_PLAN)}>Approve the plan</Button>;
    case "fix_plan":
      return <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={send(REPLAN)}>Plan again</Button>;
    case "accept_fix":
      return (
        <Group gap="xs" wrap="nowrap">
          <ConfirmButton
            size="xs"
            color="teal"
            label="Accept the fix"
            title="Accept the planner's fix?"
            says={
              <Stack gap={4}>
                <Text size="sm">It becomes the plan. You still approve the plan before anything runs.</Text>
                <List size="sm" spacing={2}>{(need.changes ?? []).map((line) => <List.Item key={line}>{line}</List.Item>)}</List>
              </Stack>
            }
            confirm="Accept"
            leftSection={<IconCheck size={14} />}
            onConfirm={async () => {
              await api.acceptFix(goalId);
              changed();
            }}
          />
          <Button
            size="xs"
            variant="subtle"
            color="gray"
            onClick={async () => {
              await api.dismissFix(goalId);
              changed();
            }}
          >
            Dismiss
          </Button>
        </Group>
      );
    // A need about a task gets the buttons its task's state calls for now (needActions.tsx): merge or try
    // another fix on a review no fix settled, retry a failed task, and none once it is under way again.
    case "merge":
    case "review":
    case "settle_review":
    case "retry":
    case "open_task":
      return task ? <TaskNeedAction taskId={task} onDone={onDone} /> : <Button size="xs" variant="light" onClick={send(SHOW_ISSUES)}>Show the goal's issues</Button>;
    case "split_or_run": {
      const issues = <Button size="xs" variant="light" onClick={send(SHOW_ISSUES)}>Show the goal's issues</Button>;
      return need.ref ? <HeldIssueAction goalId={goalId} issueRef={need.ref} label={label} fallback={issues} onDone={onDone} /> : issues;
    }
    case "raise_budget":
      return <Button size="xs" variant="light" onClick={send(OPEN_SETTINGS)}>Raise the budget</Button>;
    default: {
      const url = need.ref ? issueUrl(need.ref) : null;
      const open = url ? (
        <Button size="xs" variant="light" component="a" href={url} target="_blank" rel="noreferrer">Open the issue</Button>
      ) : (
        <Button size="xs" variant="light" onClick={send(SHOW_ISSUES)}>Show the goal's issues</Button>
      );
      // A need about an issue reads the issue as it is now, whatever the update said when it was written: a
      // held one is split or run as one right here, one with a task gets its task's buttons.
      return need.ref ? <HeldIssueAction goalId={goalId} issueRef={need.ref} label={label} fallback={open} onDone={onDone} /> : open;
    }
  }
}
