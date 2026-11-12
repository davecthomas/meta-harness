import { useState } from "react";
import { Anchor, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { api, type Task } from "../../api";

// Remove an issue from its goal, after saying what that stops. The goal lets the issue go for good: it is not
// taken in again by the goal's label, it no longer counts toward the goal being met, and another goal may
// take it. Nothing changes on GitHub. A task that has not ended is canceled; one with a pull request open
// has work done and waiting, so the dialog says the pull request stays open and asks for that plainly.

const LIVE = ["queued", "blocked", "running", "taken_over", "ready", "review_unresolved"];

// What removing the issue does to its task, as a sentence, and whether its pull request is left open.
export function removeEffect(task: Pick<Task, "status" | "pr_url"> | undefined): { text: string; leavesPullRequest: boolean } {
  if (!task) return { text: "It has no task yet, so nothing is stopped.", leavesPullRequest: false };
  if (!LIVE.includes(task.status)) return { text: `Its task already ended (${task.status.replace(/_/g, " ")}), so nothing is stopped.`, leavesPullRequest: false };
  if (task.pr_url) return { text: "Its task is canceled. Its pull request is open with work done: it stays open on GitHub, for you to merge or close there.", leavesPullRequest: true };
  if (task.status === "running") return { text: "Its task is running: the run is stopped and the task canceled. No pull request was opened.", leavesPullRequest: false };
  return { text: "Its task has not started: it is canceled.", leavesPullRequest: false };
}

export function RemoveIssue({ goalId, issueRef, task, onRemoved }: { goalId: string; issueRef: string; task?: Pick<Task, "status" | "pr_url">; onRemoved: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const effect = removeEffect(task);
  const name = issueRef.replace(/^github:/, "");
  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.removeIssue(goalId, issueRef, effect.leavesPullRequest);
      setAsking(false);
      onRemoved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <span onClick={(e) => e.stopPropagation()}>
      <Anchor component="button" type="button" size="xs" c="dimmed" onClick={() => setAsking(true)} aria-label={`Remove ${name} from the goal`}>
        Remove from goal
      </Anchor>
      <Modal opened={asking} onClose={() => setAsking(false)} title={`Remove ${name} from the goal`}>
        <Stack gap="md" data-testid="remove-issue">
          <Text size="sm">
            The goal lets this issue go: it is not taken in again by the goal's label, it no longer counts toward the goal being met, and another goal may take it. Nothing changes on GitHub. Add issues brings it back.
          </Text>
          <Text size="sm">{effect.text}</Text>
          {error && <Text size="sm" c="red">{error}</Text>}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(false)}>Not now</Button>
            <Button color="red" loading={busy} onClick={() => void remove()}>Remove from goal</Button>
          </Group>
        </Stack>
      </Modal>
    </span>
  );
}
