import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ActionIcon, Anchor, Divider, Indicator, Popover, Stack, Text, Tooltip } from "@mantine/core";
import { IconInbox } from "@tabler/icons-react";
import { api, type Task } from "../api";
import { NEEDS_YOU_POLL_MS, useNeedsYou, type NeedsYouItem } from "../features/overview/needsYou";
import { reviewLink, reviewsInProgress, type ReviewRun } from "../features/tasks/reviewing";

// The header's inbox: the count of what needs the operator and the first few, from the same list the
// Overview shows in full (features/overview/needsYou.ts), so the two never disagree. Below it, and not
// counted, the reviews agents have in progress: the operator may join one, and need not.

// How many of the list the popover shows before it sends the reader to all of it.
const SHOWN = 6;

export interface ReviewNote {
  key: string;
  text: string;
  to: string;
}

// Each review an agent has in progress, with the way into it. Keyed by the run, so each is one line.
export function reviewNotes(reviews: { review: ReviewRun; task: Task }[]): ReviewNote[] {
  return reviews.map(({ review, task }) => ({ key: `review-${review.run_id}`, text: `An agent is reviewing: ${task.title}`, to: reviewLink(task.id) }));
}

export function Inbox() {
  const { items, error } = useNeedsYou();
  const [reviews, setReviews] = useState<ReviewNote[]>([]);
  const seen = useRef<Set<string> | null>(null);
  const notify = useRef(false);

  useEffect(() => {
    api.settings().then((s) => (notify.current = s.notifications), () => (notify.current = false));
    const read = async () => {
      try {
        const [events, running] = await Promise.all([api.recentEvents(["run_prompt", "attempt_finished"], 500), api.tasks({ statuses: ["running"] }, 0, 200)]);
        const byId = new Map(running.items.map((t) => [t.id, t]));
        setReviews(reviewNotes(reviewsInProgress(events).flatMap((review) => (byId.has(review.task_id) ? [{ review, task: byId.get(review.task_id)! }] : []))));
      } catch {
        // The reviews are an extra: each task's page shows its own. What needs the operator says when it fails.
      }
    };
    void read();
    const timer = setInterval(() => void read(), NEEDS_YOU_POLL_MS);
    return () => clearInterval(timer);
  }, []);

  // A browser notification for each new item, when the operator turned them on and the browser allows them:
  // only for what needs them, never for a review an agent started.
  useEffect(() => {
    if (!items) return;
    if (seen.current && notify.current && typeof Notification !== "undefined" && Notification.permission === "granted") {
      for (const item of items) if (!seen.current.has(item.key)) new Notification("Muxxy", { body: `${item.action}: ${item.title}`, tag: item.key });
    }
    seen.current = new Set(items.map((i) => i.key));
  }, [items]);

  const count = items?.length ?? 0;
  return (
    <Popover position="bottom-end" width={440} shadow="md">
      <Popover.Target>
        <Tooltip label={count ? `${count} need you` : "Nothing needs you"}>
          <Indicator label={count} size={16} disabled={!count} color="red" offset={4}>
            <ActionIcon variant="default" size="lg" aria-label={`Needs you, ${count} items`}>
              <IconInbox size={18} />
            </ActionIcon>
          </Indicator>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown maw="calc(100vw - 32px)">
        <Stack gap="sm" mah="70vh" style={{ overflowY: "auto" }}>
          {error && (
            <Text size="sm" c="red" data-testid="inbox-error">
              Could not read what needs you: {error}. It tries again in 30 seconds; the list below is as it was last read.
            </Text>
          )}
          {items === null && !error && <Text size="sm" c="dimmed">Loading…</Text>}
          {items !== null && count === 0 && <Text size="sm" c="dimmed">Nothing needs you.</Text>}
          {(items ?? []).slice(0, SHOWN).map((item) => <Item key={item.key} item={item} />)}
          {count > SHOWN && (
            <Anchor component={Link} to="/" size="sm" data-testid="inbox-all">
              See all {count} on the Overview
            </Anchor>
          )}
          {reviews.length > 0 && (
            <>
              <Divider label="Also happening, nothing to do" labelPosition="left" />
              {reviews.map((r) => (
                <Anchor key={r.key} component={Link} to={r.to} size="sm" c="dimmed" data-testid={`inbox-${r.key}`}>{r.text}</Anchor>
              ))}
            </>
          )}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

function Item({ item }: { item: NeedsYouItem }) {
  return (
    <Stack gap={2} data-testid={`inbox-${item.key}`}>
      <Anchor component={Link} to={item.to} size="sm" fw={500}>
        {item.action}: {item.title}
      </Anchor>
      <Text size="sm" c="dimmed">{item.detail}</Text>
      {item.href && <Anchor href={item.href} target="_blank" size="xs">{item.hrefLabel ?? "Open"}</Anchor>}
    </Stack>
  );
}
