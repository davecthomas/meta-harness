import { useState } from "react";
import { useLocation } from "react-router-dom";
import { api, goalStatus, goalTitle, type Goal } from "../../api";
import { LeafItem, PastGroup, readFailed, StatusDot, TreeItem, TreeNote, useRows } from "../../components/NavTree";

// Every goal, current and past, under Goals in the left pane. Each one opens to show its tasks.

// The most goals the pane lists; the Goals page lists them all.
const MOST = 200;
const ENDED = new Set(["met", "canceled"]);

export function GoalHistory() {
  const { rows, error } = useRows(() => api.goals([], 0, MOST).then((page) => page.items), "goals");
  if (!rows) return <TreeNote error={!!error}>{error ? readFailed("the goals", error) : "Loading…"}</TreeNote>;
  const current = rows.filter((g) => !ENDED.has(g.status));
  const past = rows.filter((g) => ENDED.has(g.status));
  return (
    <>
      {error && <TreeNote error>{readFailed("the goals", error)}</TreeNote>}
      {current.length === 0 && <TreeNote>No open goals.</TreeNote>}
      {current.map((g) => <GoalItem key={g.id} goal={g} />)}
      {past.length > 0 && (
        <PastGroup label={`Past goals (${past.length})`}>
          {past.map((g) => <GoalItem key={g.id} goal={g} />)}
        </PastGroup>
      )}
    </>
  );
}

// One goal: its row links to its page, and it opens to list its tasks. The goal whose page is open starts open.
function GoalItem({ goal }: { goal: Goal }) {
  const path = useLocation().pathname;
  const to = `/goals/${goal.id}`;
  const [opened, setOpened] = useState(path === to);
  return (
    <TreeItem
      label={goalTitle(goal)}
      to={to}
      active={path === to}
      leftSection={<StatusDot status={goalStatus(goal)} />}
      opened={opened}
      onToggle={() => setOpened(!opened)}
    >
      <GoalTasks goalId={goal.id} />
    </TreeItem>
  );
}

// A goal's tasks, newest first: read only while the goal is open.
function GoalTasks({ goalId }: { goalId: string }) {
  const { rows, error } = useRows(() => api.tasks({ goalId }, 0, MOST).then((page) => page.items), `goal-tasks:${goalId}`);
  if (!rows) return <TreeNote error={!!error}>{error ? readFailed("its tasks", error) : "Loading…"}</TreeNote>;
  return (
    <>
      {error && <TreeNote error>{readFailed("its tasks", error)}</TreeNote>}
      {rows.length === 0 && <TreeNote>No tasks yet.</TreeNote>}
      {rows.map((t) => <LeafItem key={t.id} label={t.title} to={`/tasks/${t.id}`} status={t.status} />)}
    </>
  );
}
