import { useState } from "react";
import { waitReason } from "../tasks/WaitingToStart";
import { taskName } from "../../components/TaskName";
import { useLocation } from "react-router-dom";
import { api, goalStatus, goalTitle, type Goal, type Task } from "../../api";
import { LeafItem, MoreLink, PastGroup, readFailed, StatusDot, TreeItem, TreeNote, useRows } from "../../components/NavTree";
import { shownStatus } from "../tasks/shownStatus";

// Every goal, current and past, under Goals in the left pane. Each one opens to show its tasks.

// The most goals the pane reads, and the most of each kind it shows: the Goals page has them all.
const MOST = 200;
const SHOWN = 10;
const PAST_SHOWN = 20;
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
      {current.slice(0, SHOWN).map((g) => <GoalItem key={g.id} goal={g} />)}
      {current.length > SHOWN && <MoreLink to="/goals?show=open">All {current.length} open goals</MoreLink>}
      {past.length > 0 && (
        <PastGroup label={`Past goals (${past.length})`}>
          {past.slice(0, PAST_SHOWN).map((g) => <GoalItem key={g.id} goal={g} />)}
          {past.length > PAST_SHOWN && <MoreLink to="/goals?show=ended">All {past.length} past goals</MoreLink>}
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

// A goal's tasks in the order they ask for attention: those that need you, then those being worked on, then
// those waiting. Finished ones fold into one row. The planner's own planning runs are left out: the goal's page
// has its plan. Read only while the goal is open.
const NEEDS_YOU = new Set(["review_unresolved", "failed", "refused"]);
const MOVING = new Set(["running", "in_review", "taken_over", "ready"]);
const FINISHED = new Set(["closed", "canceled"]);

function rank(status: string): number {
  if (NEEDS_YOU.has(status)) return 0;
  if (MOVING.has(status)) return 1;
  return 2;
}

// A row's hover text: the task's title, and why it waits when it does.
const waiting = (t: Task) => {
  const why = waitReason(t);
  return why ? `${t.title}. It ${why}` : t.title;
};

function GoalTasks({ goalId }: { goalId: string }) {
  const { rows, error } = useRows(() => api.tasks({ goalId }, 0, MOST).then((page) => page.items), `goal-tasks:${goalId}`);
  if (!rows) return <TreeNote error={!!error}>{error ? readFailed("its tasks", error) : "Loading…"}</TreeNote>;
  const tasks = rows.filter((t) => t.task_type !== "plan").map((t) => ({ task: t, status: shownStatus(t) }));
  const live = tasks.filter((t) => !FINISHED.has(t.status)).sort((a, b) => rank(a.status) - rank(b.status));
  const done = tasks.filter((t) => FINISHED.has(t.status));
  const leaf = ({ task: t, status }: { task: Task; status: string }) => (
    <LeafItem key={t.id} label={taskName(t)} title={waiting(t)} to={`/tasks/${t.id}`} status={status} />
  );
  return (
    <>
      {error && <TreeNote error>{readFailed("its tasks", error)}</TreeNote>}
      {tasks.length === 0 && <TreeNote>No tasks yet.</TreeNote>}
      {live.map(leaf)}
      {done.length > 0 && <PastGroup label={`Finished (${done.length})`}>{done.map(leaf)}</PastGroup>}
    </>
  );
}
