import { useState } from "react";
import { taskName } from "../../components/TaskName";
import { Button } from "@mantine/core";
import { api } from "../../api";
import { LeafItem, MoreLink, PastGroup, readFailed, TreeNote, useRows } from "../../components/NavTree";
import { shownStatus } from "./shownStatus";

// Every task, current and past, under Tasks in the left pane: the ones still going first, then the finished
// ones, read a page at a time when that list is opened.

// What the pane lists under Tasks: the ones an agent is working on, and the ones whose pull request waits on
// someone. A task that has not started is under its goal, and on the Tasks page. Then the finished ones.
const NOW = ["running", "taken_over", "ready", "review_unresolved"];
const ENDED = ["closed", "failed", "canceled"];
const MOST_NOW = 200;
const SHOWN = 10;
const PAGE = 50;

export function TaskHistory() {
  const { rows, error } = useRows(() => api.tasks({ statuses: NOW }, 0, MOST_NOW).then((page) => page.items), "tasks:now");
  if (!rows) return <TreeNote error={!!error}>{error ? readFailed("the tasks", error) : "Loading…"}</TreeNote>;
  return (
    <>
      {error && <TreeNote error>{readFailed("the tasks", error)}</TreeNote>}
      {rows.length === 0 && <TreeNote>No task is being worked on or waits on anyone.</TreeNote>}
      {rows.slice(0, SHOWN).map((t) => <LeafItem key={t.id} label={taskName(t)} title={t.title} to={`/tasks/${t.id}`} status={shownStatus(t)} />)}
      {rows.length > SHOWN && <MoreLink to="/tasks">All {rows.length} in progress</MoreLink>}
      <PastGroup label="Past tasks">
        <PastTasks />
      </PastGroup>
    </>
  );
}

// The finished tasks, newest first, a page at a time.
function PastTasks() {
  const [pages, setPages] = useState(1);
  const { rows, error } = useRows(
    () => api.tasks({ statuses: ENDED }, 0, PAGE * pages).then((page) => [page]),
    `tasks:past:${pages}`,
  );
  const found = rows?.[0];
  if (!found) return <TreeNote error={!!error}>{error ? readFailed("the past tasks", error) : "Loading…"}</TreeNote>;
  return (
    <>
      {error && <TreeNote error>{readFailed("the past tasks", error)}</TreeNote>}
      {found.items.length === 0 && <TreeNote>No finished tasks.</TreeNote>}
      {found.items.map((t) => <LeafItem key={t.id} label={taskName(t)} title={t.title} to={`/tasks/${t.id}`} status={shownStatus(t)} />)}
      {found.items.length < found.total && (
        <Button variant="subtle" size="compact-xs" ml="sm" onClick={() => setPages(pages + 1)}>
          Show more ({found.total - found.items.length} left)
        </Button>
      )}
    </>
  );
}
