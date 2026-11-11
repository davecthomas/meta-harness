import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ActionIcon, Anchor, Indicator, Popover, Stack, Text, Tooltip } from "@mantine/core";
import { IconInbox } from "@tabler/icons-react";
import { api, goalTitle, type Goal, type KillSwitch, type Task } from "../api";

// One thing waiting on the operator, with where it is resolved.
export interface InboxItem {
  key: string;
  text: string;
  to: string;
}

const POLL_MS = 30_000;
// A failed task stays in the inbox this long: nothing resolves a failure, so it ages out.
const FAILED_DAYS = 3;

// What waits on the operator: a pull request ready to merge, a review that ended unresolved, a failed task,
// a goal past its budget or stalled, and the kill switch. An item leaves when what it names is resolved.
export function inboxItems(tasks: Task[], goals: Goal[], kill: KillSwitch | null, now = Date.now()): InboxItem[] {
  const items: InboxItem[] = [];
  if (kill?.engaged) items.push({ key: "kill", text: `The kill switch is on: ${kill.reason ?? "nothing runs"}`, to: "/admin/kill-switch" });
  for (const g of goals) {
    // A goal past its budget, once: its key is the same whether it is over or stopped.
    if (g.status === "budget_exhausted" || g.budget_state === "blocked") items.push({ key: `goal-${g.id}`, text: `Stopped by its budget: ${goalTitle(g)}`, to: `/goals/${g.id}` });
    else if (g.budget_state === "over") items.push({ key: `goal-${g.id}`, text: `Over budget: ${goalTitle(g)}`, to: `/goals/${g.id}` });
    if (g.status === "stalled") items.push({ key: `goal-${g.id}`, text: `Stalled: ${goalTitle(g)}`, to: `/goals/${g.id}` });
  }
  for (const t of tasks) {
    if (t.status === "ready") items.push({ key: `task-${t.id}`, text: `Ready for you to merge: ${t.title}`, to: `/tasks/${t.id}` });
    if (t.status === "review_unresolved") items.push({ key: `task-${t.id}`, text: `Review unresolved: ${t.title}`, to: `/tasks/${t.id}` });
    if (t.status === "failed" && now - Date.parse(t.updated_at) < FAILED_DAYS * 86_400_000) {
      items.push({ key: `task-${t.id}`, text: `Failed: ${t.title}`, to: `/tasks/${t.id}` });
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
      const [tasks, goals, kill] = await Promise.all([
        api.tasks({ statuses: ["ready", "review_unresolved", "failed"] }, 0, 200),
        api.goals(["active", "budget_exhausted", "stalled"], 0, 100),
        api.killSwitch().catch(() => null),
      ]);
      const found = inboxItems(tasks.items, goals.items, kill);
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
    <Popover position="bottom-end" width={360} shadow="md">
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
          <Stack gap={6}>
            {items.map((i) => (
              <Anchor key={i.key} component={Link} to={i.to} size="sm">{i.text}</Anchor>
            ))}
          </Stack>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}
