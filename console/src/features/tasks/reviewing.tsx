import { useEffect, useState } from "react";
import { Alert, Button, Group, Paper, Stack, Text } from "@mantine/core";
import { IconEye } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type MhEvent, type Task } from "../../api";
import { PrLink, TitleLink } from "../../components/ui";

// An agent's review of a pull request, while it runs. The operator stays in the loop: the console says a review
// is under way wherever they are (the Overview, the task's page, the inbox), and one click opens the diff, where
// a comment on its lines posts to the pull request under their name.
//
// A review run records `run_prompt` (kind review) before it starts and `attempt_finished` when it ends, both
// with its run id. One with a start and no end is in progress.

export interface ReviewRun {
  task_id: string;
  run_id: string;
  // When the review run started.
  since: string;
}

const POLL_MS = 15_000;
// How far back the Overview looks for a review still running. A review run is minutes long.
const RECENT_EVENTS = 500;

export function reviewsInProgress(events: MhEvent[]): ReviewRun[] {
  const ended = new Set(events.filter((e) => e.event_type === "attempt_finished" || e.event_type === "run_finished").map((e) => e.run_id));
  const open = new Map<string, ReviewRun>();
  for (const e of [...events].sort((a, b) => a.position - b.position)) {
    const runId = e.run_id ?? (e.payload?.run_id as string | undefined);
    if (e.event_type !== "run_prompt" || e.payload?.kind !== "review" || !e.task_id || !runId || ended.has(runId)) continue;
    // A task's newest review run is the one in progress.
    open.set(e.task_id, { task_id: e.task_id, run_id: runId, since: e.time_utc });
  }
  return [...open.values()];
}

export function ago(iso: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000));
  return minutes < 1 ? "just now" : minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
}

// The link that puts the operator in the review: the task's diff, where line comments post to the pull request.
export const reviewLink = (taskId: string) => `/tasks/${taskId}?tab=changes`;

// The reviews running now across every task, each with its task, reread every few seconds.
export function useReviewsInProgress(): { review: ReviewRun; task: Task }[] {
  const [found, setFound] = useState<{ review: ReviewRun; task: Task }[]>([]);
  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const [events, running] = await Promise.all([
          api.recentEvents(["run_prompt", "attempt_finished"], RECENT_EVENTS),
          api.tasks({ statuses: ["running"] }, 0, 200),
        ]);
        const tasks = new Map(running.items.map((t) => [t.id, t]));
        // Only a task still running: one canceled in the middle of its review recorded no end.
        const reviews = reviewsInProgress(events).flatMap((review) => (tasks.has(review.task_id) ? [{ review, task: tasks.get(review.task_id)! }] : []));
        if (live) setFound(reviews);
      } catch {
        // The panel is a convenience: each task's page says the same.
      }
    };
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, []);
  return found;
}

// On the Overview, above everything that waits: each pull request an agent is reviewing now.
export function ReviewsInProgress() {
  const reviews = useReviewsInProgress();
  if (!reviews.length) return null;
  return (
    <Paper p="md" mb="lg" withBorder data-testid="reviews-in-progress" style={{ borderLeft: "4px solid var(--mantine-color-violet-6)" }}>
      <Group gap="xs" mb={4}>
        <IconEye size={18} color="var(--mantine-color-violet-6)" />
        <Text fw={650}>{reviews.length === 1 ? "An agent is reviewing a pull request now" : `Agents are reviewing ${reviews.length} pull requests now`}</Text>
      </Group>
      <Text size="sm" c="dimmed" mb="sm">
        You can review any of them too: open the diff and comment on its lines, and your comments post to the pull request under your name.
      </Text>
      <Stack gap="xs">
        {reviews.map(({ review, task }) => (
          <Group key={review.run_id} justify="space-between" wrap="wrap" gap="sm">
            <Group gap="sm" wrap="wrap">
              <TitleLink to={`/tasks/${task.id}`} title={task.title} id={task.id} />
              {task.pr_url && <PrLink url={task.pr_url} />}
              <Text size="xs" c="dimmed">review started {ago(review.since)}</Text>
            </Group>
            <Button component={Link} to={reviewLink(task.id)} size="xs" color="violet" leftSection={<IconEye size={14} />}>
              Review it yourself
            </Button>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}

// At the top of a task's page while an agent reviews its pull request.
export function ReviewBanner({ task, events }: { task: Task; events: MhEvent[] }) {
  if (task.status !== "running") return null;
  const review = reviewsInProgress(events).find((r) => r.task_id === task.id);
  if (!review) return null;
  return (
    <Alert color="violet" title="An agent is reviewing this pull request now" icon={<IconEye size={18} />} mb="md" data-testid="review-banner">
      <Stack gap="sm" align="flex-start">
        <Text size="sm">
          Its review started {ago(review.since)}. You can review it too: comment on the diff's lines, and your comments post to the pull request under your name.
        </Text>
        <Group gap="sm">
          <Button component={Link} to={reviewLink(task.id)} size="xs" color="violet" leftSection={<IconEye size={14} />}>Review it yourself</Button>
          {task.pr_url && <PrLink url={task.pr_url} />}
        </Group>
      </Stack>
    </Alert>
  );
}
