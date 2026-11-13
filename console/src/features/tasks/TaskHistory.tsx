import { useState } from "react";
import { Button } from "@mantine/core";
import { api } from "../../api";
import { LeafItem, PastGroup, readFailed, TreeNote, useRows } from "../../components/NavTree";

// Every task, current and past, under Tasks in the left pane: the ones still going first, then the finished
// ones, read a page at a time when that list is opened.

const CURRENT = ["queued", "blocked", "running", "taken_over", "ready", "review_unresolved"];
const ENDED = ["closed", "failed", "canceled"];
const MOST_CURRENT = 200;
const PAGE = 50;

export function TaskHistory() {
  const { rows, error } = useRows(() => api.tasks({ statuses: CURRENT }, 0, MOST_CURRENT).then((page) => page.items), "tasks:current");
  if (!rows) return <TreeNote error={!!error}>{error ? readFailed("the tasks", error) : "Loading…"}</TreeNote>;
  return (
    <>
      {error && <TreeNote error>{readFailed("the tasks", error)}</TreeNote>}
      {rows.length === 0 && <TreeNote>No tasks running or waiting.</TreeNote>}
      {rows.map((t) => <LeafItem key={t.id} label={t.title} to={`/tasks/${t.id}`} status={t.status} />)}
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
      {found.items.map((t) => <LeafItem key={t.id} label={t.title} to={`/tasks/${t.id}`} status={t.status} />)}
      {found.items.length < found.total && (
        <Button variant="subtle" size="compact-xs" ml="sm" onClick={() => setPages(pages + 1)}>
          Show more ({found.total - found.items.length} left)
        </Button>
      )}
    </>
  );
}
