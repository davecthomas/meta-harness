import { useState } from "react";
import { Alert, Button, Checkbox, Group, NumberInput, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { IconUserCheck } from "@tabler/icons-react";
import { api, type ReviewRound, type Task } from "../../api";

// An operator's own review of a task's pull request, in place of leaving the round to the agent.
//
// Take over this review claims the round: the agent's review still finishes, and the round then waits for the
// operator's verdict. They start from the agent's findings, keep the ones that are right, add their own, and
// submit. With nothing kept or added the round is clean; otherwise the fix run is asked for exactly those. Their
// review is posted on the pull request under their name. Give it back returns the round to the agent's verdict.

interface Own {
  path: string;
  line: number | string;
  body: string;
}

// Whether a review can be taken over now: the task is running, has a pull request, and no one holds its review.
export const canTakeOver = (task: Pick<Task, "status" | "pr_url" | "review_claimed_by">) =>
  task.status === "running" && Boolean(task.pr_url) && !task.review_claimed_by;

export function TakeOverReview(props: { task: Task; onChanged: (task: Task) => void; size?: "xs" | "sm"; variant?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!canTakeOver(props.task)) return null;
  async function claim() {
    setBusy(true);
    setError(null);
    try {
      props.onChanged(await api.claimReview(props.task.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Group gap="xs">
      <Button size={props.size ?? "xs"} variant={props.variant} color="violet" loading={busy} leftSection={<IconUserCheck size={14} />} onClick={() => void claim()}>
        Take over this review
      </Button>
      {error && <Text size="xs" c="red" role="alert">{error}</Text>}
    </Group>
  );
}

// At the top of the task's page while an operator holds its review: what is happening, and once the agent's
// review has finished, the form the operator gives their verdict with.
export function OperatorReview({ task, rounds, onChanged }: { task: Task; rounds: ReviewRound[]; onChanged: (task: Task) => void }) {
  const waiting = task.status === "running" && task.waiting_on === "operator_review";
  const agent = rounds.length ? rounds[rounds.length - 1] : null;
  const [dropped, setDropped] = useState<Set<number>>(new Set());
  const [own, setOwn] = useState<Own[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (task.status !== "running" || !task.review_claimed_by) return null;
  const who = task.review_claimed_by;
  const findings = waiting && agent ? agent.findings : [];
  const keep = findings.map((_, i) => i + 1).filter((n) => !dropped.has(n));
  const added = own.filter((f) => f.body.trim());
  const asks = keep.length + added.length;

  async function send(action: () => Promise<Task>) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  const verdict = (clean: boolean) =>
    api.reviewVerdict(task.id, {
      verdict: clean ? "clean" : "changes",
      keep: clean ? [] : keep,
      findings: clean ? [] : added.map((f) => ({ body: f.body.trim(), ...(f.path.trim() ? { path: f.path.trim() } : {}), ...(Number(f.line) >= 1 ? { line: Number(f.line) } : {}) })),
    });
  const edit = (i: number, change: Partial<Own>) => setOwn((now) => now.map((f, n) => (n === i ? { ...f, ...change } : f)));

  return (
    <Alert color="violet" title={waiting ? `This review waits for ${who}'s verdict` : `${who} took this review over`} icon={<IconUserCheck size={18} />} mb="md" role="alert" data-testid="operator-review">
      <Stack gap="sm">
        {!waiting ? (
          <Text size="sm">The agent's review is still running. When it finishes, the round waits for {who}'s verdict, starting from the agent's findings.</Text>
        ) : (
          <>
            <Text size="sm">
              The agent's review {agent ? `came back ${agent.verdict === "clean" ? "clean" : `with ${findings.length} ${findings.length === 1 ? "finding" : "findings"}`}` : "has finished"}.
              Keep the findings that are right, add your own, and submit. The fix run is asked for exactly what you submit; with nothing, the round is clean.
            </Text>
            {findings.map((f, i) => (
              <Checkbox
                key={i}
                checked={!dropped.has(i + 1)}
                onChange={(e) => {
                  const on = e.currentTarget.checked;
                  setDropped((now) => {
                    const next = new Set(now);
                    if (on) next.delete(i + 1);
                    else next.add(i + 1);
                    return next;
                  });
                }}
                label={<Text size="sm">{f.path && <Text span ff="monospace" size="xs">{f.path}{f.line ? `:${f.line}` : ""} </Text>}{f.body}</Text>}
                aria-label={`Keep the agent's finding ${i + 1}`}
              />
            ))}
            {own.map((f, i) => (
              <Group key={i} gap="xs" align="flex-start" wrap="nowrap">
                <TextInput aria-label={`Path of your finding ${i + 1}`} placeholder="path (optional)" value={f.path} onChange={(e) => edit(i, { path: e.currentTarget.value })} w={220} styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }} />
                <NumberInput aria-label={`Line of your finding ${i + 1}`} placeholder="line" min={1} allowDecimal={false} value={f.line} onChange={(v) => edit(i, { line: v })} w={90} />
                <Textarea aria-label={`Your finding ${i + 1}`} placeholder="What needs to change, and why" autosize minRows={1} value={f.body} onChange={(e) => edit(i, { body: e.currentTarget.value })} style={{ flex: 1 }} />
                <Button size="compact-xs" variant="subtle" color="gray" mt={6} onClick={() => setOwn((now) => now.filter((_, n) => n !== i))}>Remove</Button>
              </Group>
            ))}
            <Group gap="sm">
              <Button size="xs" variant="default" onClick={() => setOwn((now) => [...now, { path: "", line: "", body: "" }])}>Add a finding</Button>
              <Button size="xs" color="orange" loading={busy} disabled={!asks} onClick={() => void send(() => verdict(false))}>
                Submit: changes needed ({asks})
              </Button>
              <Button size="xs" color="teal" loading={busy} disabled={added.length > 0} onClick={() => void send(() => verdict(true))}>
                Submit: no change needed
              </Button>
            </Group>
          </>
        )}
        <Group gap="sm">
          <Button size="xs" variant="subtle" color="gray" loading={busy} onClick={() => void send(() => api.releaseReview(task.id))}>
            Give the review back to the agent
          </Button>
          {error && <Text size="sm" c="red" role="alert">{error}</Text>}
        </Group>
      </Stack>
    </Alert>
  );
}
