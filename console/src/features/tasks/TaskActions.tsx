import { useState } from "react";
import { Button, Group, Modal, NumberInput, Stack, Text, Tooltip } from "@mantine/core";
import { IconArrowBackUp, IconCheck, IconGitMerge, IconX } from "@tabler/icons-react";
import { api, type Task } from "../../api";

const RETRYABLE = ["review_unresolved", "failed", "canceled"];
const MERGEABLE = ["ready", "review_unresolved"];
const WAITING = ["ready", "review_unresolved"];

// What each action does, said on the button's tooltip and in its dialog, so none has to be guessed.
export function describe(action: "retry" | "merge" | "cancel" | "done", task: Task): string {
  if (action === "retry")
    return task.pr_url
      ? "Sends the task back to the queue. Its pull request goes through checks, review and fixes again, with its fix rounds and spend counted from nothing. If the pull request has merged, the task closes."
      : "Sends the task back to the queue for a new first run, with its spend counted from nothing.";
  if (action === "merge")
    return "Merges the pull request through the merge queue, whatever the goal's auto-merge says, then waits for the merge commit's checks.";
  if (action === "done") return "Marks the task closed: its pull request merged. Nothing changes on GitHub.";
  return "Marks the task canceled. Its pull request stays open on GitHub; close it there if you don't want it.";
}

// The actions an ended task offers: Retry, Merge, and Mark done or Cancel, each saying what it does. A task
// still working offers none of these; the page's Cancel task stops it.
export function TaskActions({ task, status, merged, onChanged, onError }: {
  task: Task;
  status: string;
  // Whether the task's pull request merged, as its events record.
  merged: boolean;
  onChanged: (task: Task) => void;
  onError: (message: string) => void;
}) {
  const [retrying, setRetrying] = useState(false);
  const [limit, setLimit] = useState<number | string>(Number(task.max_usd ?? 10) || 10);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<Task>) {
    setBusy(true);
    try {
      onChanged(await action());
      setRetrying(false);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const canMerge = MERGEABLE.includes(status) && !!task.pr_url && !merged;
  return (
    <>
      {RETRYABLE.includes(status) && (
        <Tooltip label={describe("retry", task)} multiline w={320}>
          <Button variant="light" leftSection={<IconArrowBackUp size={16} />} onClick={() => setRetrying(true)}>Retry</Button>
        </Tooltip>
      )}
      {canMerge && (
        <Tooltip label={describe("merge", task)} multiline w={320}>
          <Button color="teal" variant="light" loading={busy} leftSection={<IconGitMerge size={16} />} onClick={() => void run(() => api.mergeTask(task.id))}>Merge</Button>
        </Tooltip>
      )}
      {WAITING.includes(status) && (
        <Tooltip label={describe(merged ? "done" : "cancel", task)} multiline w={320}>
          <Button
            color={merged ? undefined : "red"}
            variant="light"
            loading={busy}
            leftSection={merged ? <IconCheck size={16} /> : <IconX size={16} />}
            onClick={() => void run(() => api.clearTask(task.id))}
          >
            {merged ? "Mark done" : "Cancel task"}
          </Button>
        </Tooltip>
      )}
      <Modal opened={retrying} onClose={() => setRetrying(false)} title="Retry the task">
        <Stack gap="md">
          <Text size="sm">{describe("retry", task)}</Text>
          <NumberInput
            label="Spend limit"
            description="For all the runs of this retry."
            prefix="$"
            min={0.1}
            decimalScale={2}
            value={limit}
            onChange={setLimit}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRetrying(false)}>Not now</Button>
            <Button loading={busy} disabled={!(Number(limit) > 0)} onClick={() => void run(() => api.retryTask(task.id, Number(limit)))}>Retry</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
