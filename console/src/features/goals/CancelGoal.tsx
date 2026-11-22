import { useState } from "react";
import { TaskName } from "../../components/TaskName";
import { Button, Checkbox, Group, Modal, Stack, Text } from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import type { Task } from "../../api";

// Canceling a goal ends what it has not started, so the button asks first and says what will happen to each
// of the goal's tasks: those not started are canceled, those running with no pull request are stopped, and
// finished ones with a pull request open are left as they are, linked here.

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

// What canceling the goal does to its tasks, by the same rule the planner applies.
export function cancelEffect(tasks: Pick<Task, "id" | "status" | "pr_url">[]): { canceled: string[]; stopped: string[]; left: string[] } {
  const effect = { canceled: [] as string[], stopped: [] as string[], left: [] as string[] };
  for (const task of tasks) {
    if (task.status === "queued" || task.status === "blocked") effect.canceled.push(task.id);
    else if (!["running", "ready", "review_unresolved"].includes(task.status)) continue;
    // A running task is stopped whether it has a pull request or not: a canceled goal spends no more.
    else if (task.status === "running") effect.stopped.push(task.id);
    else if (task.pr_url) effect.left.push(task.id);
  }
  return effect;
}

export function CancelGoal({ tasks, createdIssues = false, tracker = "its tracker", onCancel }: {
  tasks: Pick<Task, "id" | "status" | "pr_url">[];
  // Whether the goal's plan created issues in its tracker: canceling can close the open ones too.
  createdIssues?: boolean;
  // What the goal's tracker is called, for the sentence that says where they are closed.
  tracker?: string;
  onCancel: (closeIssues: boolean) => Promise<unknown>;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  // On unless turned off: issues made for a goal that is canceled are work nobody will do.
  const [closeIssues, setCloseIssues] = useState(true);
  const effect = cancelEffect(tasks);
  async function confirm() {
    setBusy(true);
    try {
      await onCancel(createdIssues && closeIssues);
      setAsking(false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Button color="red" variant="light" leftSection={<IconX size={16} />} onClick={() => setAsking(true)}>Cancel goal</Button>
      <Modal opened={asking} onClose={() => setAsking(false)} title="Cancel the goal">
        <Stack gap="md" data-testid="cancel-goal">
          <Text size="sm">
            The goal takes in no more issues.
            {effect.canceled.length > 0 && ` ${count(effect.canceled.length, "task that has not started is", "tasks that have not started are")} canceled.`}
            {effect.stopped.length > 0 && ` ${count(effect.stopped.length, "running task is", "running tasks are")} stopped.`}
            {(effect.canceled.length > 0 || effect.stopped.length > 0) && " Reopening the goal later does not bring them back: it makes new tasks for its open issues."}
          </Text>
          {effect.left.length > 0 && (
            <Stack gap={4}>
              <Text size="sm">
                {count(effect.left.length, "task has a pull request open and is", "tasks have a pull request open and are")} left as {effect.left.length === 1 ? "it is" : "they are"}. Nothing more is asked of you for {effect.left.length === 1 ? "it" : "them"}; merge or close {effect.left.length === 1 ? "it" : "them"} on the code host if you want to:
              </Text>
              <Group gap="xs">
                {effect.left.map((id) => (
                  <TaskName key={id} id={id} />
                ))}
              </Group>
            </Stack>
          )}
          {createdIssues && (
            <Checkbox
              label="Also close the open issues this goal's plan created"
              description={`They are closed in ${tracker} as not planned, each with a note that the goal was canceled. Issues that existed before the goal, and any with a pull request open, are left as they are.`}
              checked={closeIssues}
              onChange={(e) => setCloseIssues(e.currentTarget.checked)}
            />
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(false)}>Not now</Button>
            <Button color="red" loading={busy} onClick={() => void confirm()}>Cancel goal</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
