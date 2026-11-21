import { useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Button, Group, Text } from "@mantine/core";
import { IconArrowBackUp, IconGitMerge } from "@tabler/icons-react";
import { api, issueUrl, trackerTitle, type Task } from "../../api";
import { ConfirmButton } from "../../components/ConfirmButton";
import { usd } from "../../money";
import type { Diagnosis } from "./diagnosis";
import { nextSteps } from "./nextSteps";

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

export function Remedies({ diagnosis, tasks, retry: mayRetry = true, issueRef, steps, onChanged }: {
  diagnosis: Diagnosis;
  // The tasks the diagnosis covers, which Retry sends back to the queue.
  tasks: Pick<Task, "id" | "max_usd">[];
  // False when the tasks cannot be retried from here (one held for budget is answered on its page).
  retry?: boolean;
  issueRef?: string;
  // One task's facts, for its recommended way out (nextSteps.ts): a stronger model, a merge as it is, or the
  // last review. Left out where the message covers several tasks.
  steps?: { status: string | null; complexity?: string | null; pr_url?: string | null };
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [merged, setMerged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(0);
  const more = diagnosis.remedies.includes("retry_more");
  const retries = mayRetry && (more || diagnosis.remedies.includes("retry")) && tasks.length > 0;
  const url = issueRef ? issueUrl(issueRef) : null;

  const single = steps && tasks.length === 1 ? nextSteps(diagnosis, steps) : null;
  const recommended = single?.recommended ?? null;
  const mark = (step: string) => (recommended === step ? " (recommended)" : "");

  async function retry(stronger = false) {
    setBusy(true);
    setError(null);
    let retried = 0;
    try {
      for (const task of tasks) {
        await api.retryTask(task.id, retryLimit(task, more), stronger);
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

  async function merge() {
    setBusy(true);
    setError(null);
    try {
      await api.mergeTask(tasks[0].id);
      setMerged(true);
    } catch (err) {
      setError(`The merge was not asked for: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
      onChanged?.();
    }
  }

  const plain = tasks.length > 1 ? `Retry all ${tasks.length}` : more ? `Retry with $${retryLimit(tasks[0] ?? {}, true).toFixed(2)}` : "Retry";
  const label = single?.stronger ? "Retry with the same model" : plain;
  const sent = done === tasks.length;
  const raisesManyLimits = more && tasks.length > 1;
  const total = raisesManyLimits ? tasks.reduce((sum, task) => sum + retryLimit(task, true), 0) : 0;
  const fill = (step: string) => (recommended === step ? "filled" : "default");
  const clean = diagnosis.kind === "clean_conflict";
  const reviewUrl = single?.merge && steps?.pr_url && /^https?:/.test(steps.pr_url) ? steps.pr_url : null;
  // The ways out, each under the step it is, so the recommended one comes first: the first button on the left
  // is the one to press.
  const offered: { step: string; node: React.ReactNode }[] = [];
  if (retries && raisesManyLimits) {
    offered.push({ step: "retry", node: (
      <ConfirmButton
        key="many"
        size="compact-sm"
        variant="filled"
        loading={busy}
        disabled={sent}
        leftSection={<IconArrowBackUp size={14} />}
        label={sent ? "Sent back to the queue" : label}
        title="Retry with higher spend limits?"
        says={`Retry ${tasks.length} tasks with spend limits that add up to ${usd(total)}, double what they had.`}
        confirm={label}
        onConfirm={() => retry()}
      />
    ) });
  }
  if (single?.merge) {
    offered.push({ step: "merge", node: (
      <ConfirmButton
        key="merge"
        size="compact-sm"
        variant={fill("merge")}
        loading={busy}
        disabled={merged || sent}
        leftSection={<IconGitMerge size={14} />}
        label={merged ? "Merge asked for" : clean ? `Merge${mark("merge")}` : `Merge as it is${mark("merge")}`}
        title={clean ? "Merge the pull request?" : "Merge as it is?"}
        says={clean
          ? "The review passed. A fix run resolves the conflict with the base first, then its checks and a review run, and it merges once that review is clean."
          : "The last review's findings stay unfixed. If the pull request conflicts with its base, a fix run resolves that first, and the merge waits for a clean review."}
        confirm={clean ? "Merge" : "Merge as it is"}
        onConfirm={() => merge()}
      />
    ) });
  }
  if (retries && single?.stronger) {
    offered.push({ step: "stronger", node: (
      <Button key="stronger" size="compact-sm" variant={fill("stronger")} loading={busy} disabled={sent} leftSection={<IconArrowBackUp size={14} />} onClick={() => void retry(true)}>
        {sent ? "Sent back to the queue" : `Retry with a stronger model${mark("stronger")}`}
      </Button>
    ) });
  }
  if (retries && !raisesManyLimits && !(single?.stronger && sent)) {
    offered.push({ step: "retry", node: (
      <Button key="retry" size="compact-sm" variant={recommended ? "default" : "filled"} loading={busy} disabled={sent} leftSection={<IconArrowBackUp size={14} />} onClick={() => void retry()}>
        {sent ? "Sent back to the queue" : label}
      </Button>
    ) });
  }
  if (reviewUrl) {
    offered.push({ step: "review", node: recommended === "review"
      ? <Button key="review" component="a" href={reviewUrl} target="_blank" size="compact-sm" variant="filled">Read the last review (recommended)</Button>
      : <Anchor key="review" href={reviewUrl} target="_blank" size="sm">Read the last review</Anchor> });
  }
  if (diagnosis.remedies.includes("config")) {
    const to = `/config?kind=${CONFIG_TAB[diagnosis.kind]?.[0] ?? "tiers"}`;
    const name = `Configuration, ${CONFIG_TAB[diagnosis.kind]?.[1] ?? "Router tiers"}`;
    offered.push({ step: "config", node: recommended === "config"
      ? <Button key="config" component={Link} to={to} size="compact-sm" variant="filled">{name} (recommended)</Button>
      : <Anchor key="config" component={Link} to={to} size="sm">{name}</Anchor> });
  }
  const ordered = [...offered.filter((o) => o.step === recommended), ...offered.filter((o) => o.step !== recommended)];
  return (
    <Group gap="sm" onClick={(e) => e.stopPropagation()}>
      {ordered.map((o) => o.node)}
      {diagnosis.remedies.includes("task") && tasks.length === 1 && (
        <Anchor component={Link} to={`/tasks/${tasks[0].id}`} size="sm">Open the task</Anchor>
      )}
      {diagnosis.remedies.includes("issue") && url && (
        <Anchor href={url} target="_blank" size="sm">Edit the issue in {trackerTitle(issueRef ? issueRef.split(":")[0] : "github")}</Anchor>
      )}
      {retries && single && !sent && <Text size="xs" c="dimmed">Each retry can spend up to {usd(retryLimit(tasks[0], more))}.</Text>}
      {error && <Text size="xs" c="red">{error}</Text>}
    </Group>
  );
}

