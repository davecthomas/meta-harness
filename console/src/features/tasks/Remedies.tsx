import { useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Button, Group, Text } from "@mantine/core";
import { IconArrowBackUp } from "@tabler/icons-react";
import { api, issueUrl, trackerTitle, type Task } from "../../api";
import { ConfirmButton } from "../../components/ConfirmButton";
import { usd } from "../../money";
import type { Diagnosis } from "./diagnosis";

// The actions a diagnosis offers, where its message is shown: Retry for the tasks it covers (all of them at
// once when several failed the same way), and links to the task, the issue and Configuration.

const DEFAULT_LIMIT = 10;

// The spend limit a retry gets: the task's own, doubled when the last one was used up.
// Which Configuration tab holds what a failure's fix changes, and the tab's name: a link goes to the setting,
// not to the first tab.
const CONFIG_TAB: Record<string, [string, string]> = {
  grant_refused: ["tiers", "Router tiers"],
  config: ["profiles", "Harness profiles"],
  limit: ["profiles", "Harness profiles"],
  refused_no_checks: ["repos", "Repositories"],
};

export function retryLimit(task: Pick<Task, "max_usd">, more: boolean): number {
  const limit = Number(task.max_usd ?? DEFAULT_LIMIT) || DEFAULT_LIMIT;
  return more ? limit * 2 : limit;
}

// Every task the groups can retry from here, each with the spend limit its own Retry would give it. What the
// Overview's Retry all sends: the one fix that needs no reading first. A link to Configuration or a task
// stays a link.
export function retryAllPlan(groups: { diagnosis: Diagnosis; items: { taskId?: string | null; status: string | null; waitingOn?: string | null; max_usd?: string | null }[] }[]): { id: string; limit: number }[] {
  return groups.flatMap(({ diagnosis, items }) => {
    const more = diagnosis.remedies.includes("retry_more");
    if (!more && !diagnosis.remedies.includes("retry")) return [];
    return items.flatMap((i) => (i.taskId && i.status && ["failed", "review_unresolved"].includes(i.status) && !i.waitingOn
      ? [{ id: i.taskId, limit: retryLimit({ max_usd: i.max_usd ?? null }, more) }]
      : []));
  });
}

// One button for every retry the groups offer, with a dialog that says how many tasks and the most they can spend.
export function RetryAll({ plan, onChanged }: { plan: { id: string; limit: number }[]; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  if (plan.length < 2) return null;
  const total = plan.reduce((sum, p) => sum + p.limit, 0);
  async function go() {
    setBusy(true);
    setError(null);
    let retried = 0;
    try {
      for (const p of plan) {
        await api.retryTask(p.id, p.limit);
        retried += 1;
      }
      setDone(true);
    } catch (err) {
      setError(`${retried} of ${plan.length} retried, then: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
      onChanged?.();
    }
  }
  const label = `Retry all ${plan.length} tasks`;
  return (
    <Group gap="sm">
      <ConfirmButton
        size="compact-sm"
        variant="filled"
        loading={busy}
        disabled={done}
        leftSection={<IconArrowBackUp size={14} />}
        label={done ? "Sent back to the queue" : label}
        title={`${label}?`}
        says={`Each task runs again with the spend limit its own Retry gives it. Together they can spend at most ${usd(total)}.`}
        confirm={label}
        onConfirm={() => go()}
      />
      {error && <Text size="xs" c="red">{error}</Text>}
    </Group>
  );
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
  const raisesManyLimits = more && tasks.length > 1;
  const total = raisesManyLimits ? tasks.reduce((sum, task) => sum + retryLimit(task, true), 0) : 0;
  return (
    <Group gap="sm" onClick={(e) => e.stopPropagation()}>
      {retries && raisesManyLimits && (
        <ConfirmButton
          size="compact-sm"
          variant="filled"
          loading={busy}
          disabled={done === tasks.length}
          leftSection={<IconArrowBackUp size={14} />}
          label={done === tasks.length ? "Sent back to the queue" : label}
          title="Retry with higher spend limits?"
          says={`Retry ${tasks.length} tasks with spend limits that add up to ${usd(total)}, double what they had.`}
          confirm={label}
          onConfirm={() => retry()}
        />
      )}
      {retries && !raisesManyLimits && (
        <Button size="compact-sm" variant="filled" loading={busy} disabled={done === tasks.length} leftSection={<IconArrowBackUp size={14} />} onClick={() => void retry()}>
          {done === tasks.length ? "Sent back to the queue" : label}
        </Button>
      )}
      {diagnosis.remedies.includes("task") && tasks.length === 1 && (
        <Anchor component={Link} to={`/tasks/${tasks[0].id}`} size="sm">Open the task</Anchor>
      )}
      {diagnosis.remedies.includes("issue") && url && (
        <Anchor href={url} target="_blank" size="sm">Edit the issue in {trackerTitle(issueRef ? issueRef.split(":")[0] : "github")}</Anchor>
      )}
      {diagnosis.remedies.includes("config") && (
        <Anchor component={Link} to={`/config?kind=${CONFIG_TAB[diagnosis.kind]?.[0] ?? "tiers"}`} size="sm">
          Configuration, {CONFIG_TAB[diagnosis.kind]?.[1] ?? "Router tiers"}
        </Anchor>
      )}
      {error && <Text size="xs" c="red">{error}</Text>}
    </Group>
  );
}
