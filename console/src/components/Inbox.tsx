import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ActionIcon, Anchor, Indicator, Popover, Stack, Text, Tooltip } from "@mantine/core";
import { IconInbox } from "@tabler/icons-react";
import { api, goalTitle, type Goal, type KillSwitch, type Task } from "../api";
import { reviewLink, reviewsInProgress, type ReviewRun } from "../features/tasks/reviewing";
import { diagnose, oneLine } from "../features/tasks/diagnosis";

// One thing waiting on the operator, with where it is resolved.
export interface InboxItem {
  key: string;
  text: string;
  to: string;
  // What to do about it, and why it is here, under the item.
  detail?: string;
  // A page outside the console that settles it, such as the pull request to merge: opened in a new tab.
  href?: string;
  hrefLabel?: string;
}

const POLL_MS = 30_000;
// A failed task stays in the inbox this long: nothing resolves a failure, so it ages out.
const FAILED_DAYS = 3;

// What the operator should know of: an agent's review in progress, which they may join; a pull request ready to merge, a review that ended unresolved, a failed task,
// a goal past its budget or stalled, and the kill switch. An item leaves when what it names is resolved.
export function inboxItems(
  tasks: Task[],
  goals: Goal[],
  kill: KillSwitch | null,
  now = Date.now(),
  reviews: { review: ReviewRun; task: Task }[] = [],
): InboxItem[] {
  const items: InboxItem[] = [];
  // An agent's review in progress: the operator may review the pull request too. Keyed by the run, so each
  // review is announced once.
  for (const { review, task } of reviews) {
    items.push({ key: `review-${review.run_id}`, text: `An agent is reviewing: ${task.title}`, to: reviewLink(task.id) });
  }
  if (kill?.engaged) {
    items.push({ key: "kill", text: `The kill switch is on: ${kill.reason ?? "nothing runs"}`, to: "/admin/kill-switch", detail: "No task starts or runs. Lift it once it is safe to run again." });
  }
  for (const g of goals) {
    // A goal past its budget, once: its key is the same whether it is over or stopped.
    if (g.status === "budget_exhausted" || g.budget_state === "blocked") items.push({ key: `goal-${g.id}`, text: `Stopped by its budget: ${goalTitle(g)}`, to: `/goals/${g.id}`, detail: "None of its tasks starts until it has more to spend. Raise the budget on the goal's page." });
    else if (g.budget_state === "over") items.push({ key: `goal-${g.id}`, text: `Over budget: ${goalTitle(g)}`, to: `/goals/${g.id}`, detail: "It keeps running. Raise the budget on the goal's page, or pause the goal." });
    if (g.status === "stalled") items.push({ key: `goal-${g.id}`, text: `Stalled: ${goalTitle(g)}`, to: `/goals/${g.id}`, detail: "Every open issue waits on a failed one or on you. Its page says which." });
  }
  for (const t of tasks) {
    if (t.status === "ready") {
      items.push({
        key: `task-${t.id}`,
        text: `Ready to merge: ${t.title}`,
        to: `/tasks/${t.id}`,
        detail: "Its review is clean and its pull request is open. Merge it on GitHub, or with Merge on the task's page.",
        href: t.pr_url && /^https?:/.test(t.pr_url) ? t.pr_url : undefined,
        hrefLabel: "Open the pull request",
      });
    }
    const failed = t.status === "failed" && now - Date.parse(t.updated_at) < FAILED_DAYS * 86_400_000;
    if (t.status === "review_unresolved" || failed) {
      const diagnosis = diagnose({ status: t.status, reason: t.status_reason });
      items.push({
        key: `task-${t.id}`,
        text: `${failed ? "Failed" : "Review unresolved"}: ${t.title}`,
        to: `/tasks/${t.id}`,
        detail: diagnosis ? oneLine(diagnosis) : undefined,
      });
    }
  }
  return items;
}

// The header's inbox: its count, the list on a click, and a browser notification for each new item when the
// operator turned notifications on in Settings and the browser allows them.
export function Inbox() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const seen = useRef<Set<string> | null>(null);
  const notify = useRef(false);

  const load = useCallback(async () => {
    try {
      const [tasks, goals, kill, events, running] = await Promise.all([
        api.tasks({ statuses: ["ready", "review_unresolved", "failed"] }, 0, 200),
        api.goals(["active", "budget_exhausted", "stalled"], 0, 100),
        api.killSwitch().catch(() => null),
        api.recentEvents(["run_prompt", "attempt_finished"], 500).catch(() => []),
        api.tasks({ statuses: ["running"] }, 0, 200).catch(() => ({ items: [] as Task[], total: 0 })),
      ]);
      const byId = new Map(running.items.map((t) => [t.id, t]));
      const reviews = reviewsInProgress(events).flatMap((review) => (byId.has(review.task_id) ? [{ review, task: byId.get(review.task_id)! }] : []));
      const found = inboxItems(tasks.items, goals.items, kill, Date.now(), reviews);
      if (seen.current && notify.current && typeof Notification !== "undefined" && Notification.permission === "granted") {
        for (const item of found) if (!seen.current.has(item.key)) new Notification("Meta-harness", { body: item.text, tag: item.key });
      }
      seen.current = new Set(found.map((i) => i.key));
      setItems(found);
    } catch {
      // The inbox is a convenience: the pages it links to show the same things.
    }
  }, []);

  useEffect(() => {
    api.settings().then((s) => (notify.current = s.notifications), () => (notify.current = false));
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <Popover position="bottom-end" width={440} shadow="md">
      <Popover.Target>
        <Tooltip label="What needs you">
          <Indicator label={items.length} size={16} disabled={!items.length} color="red" offset={4}>
            <ActionIcon variant="default" size="lg" aria-label={`Inbox, ${items.length} items`}>
              <IconInbox size={18} />
            </ActionIcon>
          </Indicator>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown>
        {items.length === 0 ? (
          <Text size="sm" c="dimmed">Nothing needs you.</Text>
        ) : (
          <Stack gap="sm" mah="70vh" style={{ overflowY: "auto" }}>
            {items.map((i) => (
              <Stack key={i.key} gap={2} data-testid={`inbox-${i.key}`}>
                <Anchor component={Link} to={i.to} size="sm" fw={500}>{i.text}</Anchor>
                {i.detail && <Text size="xs" c="dimmed">{i.detail}</Text>}
                {i.href && <Anchor href={i.href} target="_blank" size="xs">{i.hrefLabel ?? "Open"}</Anchor>}
              </Stack>
            ))}
          </Stack>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}
