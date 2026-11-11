import { useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Button, Group, Text } from "@mantine/core";
import { IconArrowBackUp } from "@tabler/icons-react";
import { api, issueUrl, type Task } from "../../api";
import type { Diagnosis } from "./diagnosis";

// The actions a diagnosis offers, where its message is shown: Retry for the tasks it covers (all of them at
// once when several failed the same way), and links to the task, the issue and Configuration.

const DEFAULT_LIMIT = 10;

// The spend limit a retry gets: the task's own, doubled when the last one was used up.
export function retryLimit(task: Pick<Task, "max_usd">, more: boolean): number {
  const limit = Number(task.max_usd ?? DEFAULT_LIMIT) || DEFAULT_LIMIT;
  return more ? limit * 2 : limit;
}

export function Remedies({ diagnosis, tasks, retry: mayRetry = true, issueRef, onChanged }: {
  diagnosis: Diagnosis;
  // The tasks the diagnosis covers, which Retry sends back to the queue.
  tasks: Pick<Task, "id" | "max_usd">[];
  // False when the tasks cannot be retried from here (one held for budget is answered on its page).
  retry?: boolean;
  issueRef?: string;
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(0);
  const more = diagnosis.remedies.includes("retry_more");
  const retries = mayRetry && (more || diagnosis.remedies.includes("retry")) && tasks.length > 0;
  const url = issueRef ? issueUrl(issueRef) : null;

  async function retry() {
    setBusy(true);
    setError(null);
    let retried = 0;
    try {
      for (const task of tasks) {
        await api.retryTask(task.id, retryLimit(task, more));
        retried += 1;
      }
    } catch (err) {
      setError(`${retried} of ${tasks.length} retried, then: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDone(retried);
      setBusy(false);
      onChanged?.();
    }
  }

  const label = tasks.length > 1 ? `Retry all ${tasks.length}` : more ? `Retry with $${retryLimit(tasks[0] ?? {}, true).toFixed(2)}` : "Retry";
  return (
    <Group gap="sm" onClick={(e) => e.stopPropagation()}>
      {retries && (
        <Button size="compact-sm" variant="filled" loading={busy} disabled={done === tasks.length} leftSection={<IconArrowBackUp size={14} />} onClick={() => void retry()}>
          {done === tasks.length ? "Sent back to the queue" : label}
        </Button>
      )}
      {diagnosis.remedies.includes("task") && tasks.length === 1 && (
        <Anchor component={Link} to={`/tasks/${tasks[0].id}`} size="sm">Open the task</Anchor>
      )}
      {diagnosis.remedies.includes("issue") && url && (
        <Anchor href={url} target="_blank" size="sm">Edit the issue on GitHub</Anchor>
      )}
      {diagnosis.remedies.includes("config") && <Anchor component={Link} to="/config" size="sm">Configuration</Anchor>}
      {error && <Text size="xs" c="red">{error}</Text>}
    </Group>
  );
}
