import { useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import type { Task } from "../../api";

// Canceling a goal ends what it has not started, so the button asks first and says what will happen to each
// of the goal's tasks: those not started are canceled, those running with no pull request are stopped, and
// those with a pull request open are left for the operator, linked here.

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

// What canceling the goal does to its tasks, by the same rule the planner applies.
export function cancelEffect(tasks: Pick<Task, "id" | "status" | "pr_url">[]): { canceled: string[]; stopped: string[]; left: string[] } {
  const effect = { canceled: [] as string[], stopped: [] as string[], left: [] as string[] };
  for (const task of tasks) {
    if (task.status === "queued" || task.status === "blocked") effect.canceled.push(task.id);
    else if (!["running", "ready", "review_unresolved"].includes(task.status)) continue;
    else if (task.pr_url) effect.left.push(task.id);
    else if (task.status === "running") effect.stopped.push(task.id);
  }
  return effect;
}

export function CancelGoal({ tasks, onCancel }: { tasks: Pick<Task, "id" | "status" | "pr_url">[]; onCancel: () => Promise<unknown> }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const effect = cancelEffect(tasks);
  async function confirm() {
    setBusy(true);
    try {
      await onCancel();
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
            The goal takes in no more issues. {count(effect.canceled.length, "task that has not started is", "tasks that have not started are")} canceled
            {effect.stopped.length > 0 && `, and ${count(effect.stopped.length, "running task is", "running tasks are")} stopped`}. Reopening the goal
            later does not bring them back: it makes new tasks for its open issues.
          </Text>
          {effect.left.length > 0 && (
            <Stack gap={4}>
              <Text size="sm">
                {count(effect.left.length, "task has a pull request open and is", "tasks have a pull request open and are")} left as {effect.left.length === 1 ? "it is" : "they are"}, for you to merge, close or cancel:
              </Text>
              <Group gap="xs">
                {effect.left.map((id) => (
                  <Anchor key={id} component={Link} to={`/tasks/${id}`} ff="monospace" size="sm">{id}</Anchor>
                ))}
              </Group>
            </Stack>
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
