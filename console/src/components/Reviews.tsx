import { Anchor, Badge, Card, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { ReviewRound, Task } from "../api";
import { Empty } from "./ui";

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
  if (n.kind === "reviewed_again") return <Text size="sm">Reviewed again</Text>;
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
export function Reviews({ task, rounds }: { task: Task; rounds: ReviewRound[] }) {
  if (!rounds.length) return <Empty>{noReviewYet(task)}</Empty>;
  const repo = repoOf(task.pr_url);
  return (
    <Stack gap="md">
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
                  <Group key={i} gap="xs" align="flex-start" wrap="nowrap">
                    {f.path && repo && r.head_sha ? (
                      <Anchor href={`https://github.com/${repo}/blob/${r.head_sha}/${f.path}${f.line ? `#L${f.line}` : ""}`} target="_blank" ff="monospace" size="xs" style={{ whiteSpace: "nowrap" }}>
                        {f.path}{f.line ? `:${f.line}` : ""}
                      </Anchor>
                    ) : f.path ? <Text ff="monospace" size="xs">{f.path}</Text> : null}
                    <Text size="sm">{f.body}</Text>
                  </Group>
                ))}
              </Stack>
            )}
            <Group justify="space-between">
              {nextLine(r, task.id)}
              {r.review_url && <Anchor href={r.review_url} target="_blank" size="xs">The review on GitHub</Anchor>}
            </Group>
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}
