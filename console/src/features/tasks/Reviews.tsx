import { useState } from "react";
import { Alert, Anchor, Badge, Button, Card, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { Link } from "react-router-dom";
import { api, type ReviewRound, type Task } from "../../api";
import { Empty } from "../../components/ui";

export function VerdictBadge({ verdict }: { verdict: string }) {
  return <Badge color={verdict === "clean" ? "teal" : "orange"} variant="light">{verdict}</Badge>;
}

function repoOf(prUrl: string | null): string | null {
  const m = /github\.com\/([^/]+\/[^/]+)\/pull\//.exec(prUrl ?? "");
  return m ? m[1] : null;
}

function nextLine(round: ReviewRound, taskId: string) {
  const n = round.next;
  if (n.kind === "fix") return <Text size="sm">Sent to <Anchor component={Link} to={`/tasks/${taskId}?run=${encodeURIComponent(n.run_id ?? "")}`}>fix run {n.attempt ?? ""}</Anchor></Text>;
  if (n.kind === "merged") return <Text size="sm">Merged</Text>;
  if (n.kind === "reviewed_again") return <Text size="sm">Reviewed again: the pull request's head changed before the next step</Text>;
  if (n.kind === "waiting") return <Text size="sm">Waiting for an operator{n.reason ? `: ${n.reason}` : ""}</Text>;
  if (n.kind === "ended") return <Text size="sm">The loop stopped{n.reason ? `: ${n.reason}` : ""}</Text>;
  return <Text size="sm" c="dimmed">The loop goes on</Text>;
}

// Why a pull request has no review yet, from its task's state.
function noReviewYet(task: Task): string {
  if (task.status === "running") return "No review yet: the checks or the review run are under way.";
  return `No review: ${task.status_reason ?? task.status}.`;
}

// Every review round on a task's pull request: the verdict, who reviewed which commit, the summary, the
// findings linked to their lines at the reviewed commit, and what came next.
//
// While the task holds its newest review for an operator (waiting_on: review), each of that round's findings
// can be dismissed, with why, and Send to fix now ends the wait: the fix run is asked only for what is left.
export function Reviews({ task, rounds, onAnswered }: { task: Task; rounds: ReviewRound[]; onAnswered?: () => void }) {
  const [dismissing, setDismissing] = useState<{ round: number; n: number; body: string } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!rounds.length) return <Empty>{noReviewYet(task)}</Empty>;
  const repo = repoOf(task.pr_url);
  const held = task.status === "running" && task.waiting_on === "review" ? rounds[rounds.length - 1].round : null;

  async function answer(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setDismissing(null);
      setReason("");
      onAnswered?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="md">
      {held !== null && (
        <Alert color="orange" title={`Round ${held} waits on you before its fix run`} role="alert">
          <Stack gap="xs">
            <Text size="sm">{task.status_reason}</Text>
            <Text size="sm">Dismiss any finding that is wrong, with why. The fix run is asked only for the rest; with none left, the round counts as clean.</Text>
            <Group gap="sm">
              <Button size="xs" loading={busy} onClick={() => void answer(() => api.proceedReview(task.id, held))}>Send to fix now</Button>
              {error && <Text size="sm" c="red">{error}</Text>}
            </Group>
          </Stack>
        </Alert>
      )}
      {rounds.map((r) => (
        <Card key={r.round} withBorder padding="md" data-testid={`review-${r.round}`}>
          <Stack gap="xs">
            <Group justify="space-between">
              <Group gap="xs">
                <Text fw={650}>Round {r.round}</Text>
                <VerdictBadge verdict={r.verdict} />
                <Text size="xs" c="dimmed">{(r.reviewer ?? "").replace(/^harness:/, "")}</Text>
              </Group>
              {r.head_sha && repo && (
                <Anchor href={`https://github.com/${repo}/commit/${r.head_sha}`} target="_blank" ff="monospace" size="xs">{r.head_sha.slice(0, 7)}</Anchor>
              )}
            </Group>
            {r.summary && (
              <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{r.summary}</Text>
            )}
            {r.findings.length > 0 && (
              <Stack gap={4}>
                {r.findings.map((f, i) => (
                  <Group key={i} gap="xs" align="flex-start" wrap="nowrap" data-testid={`finding-${r.round}-${f.n ?? i + 1}`} opacity={f.dismissed ? 0.6 : 1}>
                    {f.path && repo && r.head_sha ? (
                      <Anchor href={`https://github.com/${repo}/blob/${r.head_sha}/${f.path}${f.line ? `#L${f.line}` : ""}`} target="_blank" ff="monospace" size="xs" style={{ whiteSpace: "nowrap" }}>
                        {f.path}{f.line ? `:${f.line}` : ""}
                      </Anchor>
                    ) : f.path ? <Text ff="monospace" size="xs">{f.path}</Text> : null}
                    <Text size="sm" td={f.dismissed ? "line-through" : undefined}>{f.body}</Text>
                    {f.dismissed && (
                      <Badge color="gray" variant="light" tt="none" style={{ flexShrink: 0 }} title={f.dismissed.reason}>
                        dismissed{f.dismissed.by ? ` by ${f.dismissed.by}` : ""}: {f.dismissed.reason}
                      </Badge>
                    )}
                    {held === r.round && !f.dismissed && (
                      <Button
                        size="compact-xs"
                        variant="light"
                        color="orange"
                        style={{ flexShrink: 0 }}
                        aria-label={`Dismiss finding ${f.n ?? i + 1}`}
                        onClick={() => setDismissing({ round: r.round, n: f.n ?? i + 1, body: f.body })}
                      >
                        Dismiss
                      </Button>
                    )}
                  </Group>
                ))}
              </Stack>
            )}
            <Group justify="space-between">
              {nextLine(r, task.id)}
              <Group gap="md">
                {r.run_id && (
                  <Anchor component={Link} to={`/tasks/${task.id}?run=${encodeURIComponent(r.run_id)}`} size="xs">Show review run</Anchor>
                )}
                {r.review_url && <Anchor href={r.review_url} target="_blank" size="xs">The review on GitHub</Anchor>}
              </Group>
            </Group>
          </Stack>
        </Card>
      ))}
      <Modal opened={dismissing !== null} onClose={() => setDismissing(null)} title={`Dismiss finding ${dismissing?.n ?? ""}`}>
        <Stack gap="md">
          <Text size="sm" c="dimmed">{dismissing?.body}</Text>
          <Textarea
            label="Why"
            description="Shown on this review and posted on the pull request."
            autosize
            minRows={2}
            value={reason}
            onChange={(e) => setReason(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDismissing(null)}>Cancel</Button>
            <Button
              color="orange"
              loading={busy}
              disabled={!reason.trim()}
              onClick={() => dismissing && void answer(() => api.dismissFinding(task.id, dismissing.round, dismissing.n, reason.trim()))}
            >
              Dismiss the finding
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
