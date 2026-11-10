import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Checkbox, Group, SegmentedControl, Table, Text } from "@mantine/core";
import { api, type Task } from "../api";
import { useInfinite } from "../hooks";
import { TileGrid, type TileSize } from "../components/TileGrid";
import { DataTable, Empty, ErrorAlert, IssueLink, PageHeader, PrLink, StatusBadge, TitleLink, when } from "../components/ui";

// Each tab: its key, its label, the statuses it shows, and whether the tasks have a pull request.
// "Running" is every task whose workflow is working on it, first run or review loop (a running task with a
// pull request shows as in review). "Pull request open" is the tasks waiting on someone: ready or
// review_unresolved, with no workflow left. A task is under one tab, not both.
const FILTERS: [string, string, string[], boolean | undefined][] = [
  ["running", "Running", ["running", "taken_over"], undefined],
  ["waiting", "Queued and blocked", ["queued", "blocked"], undefined],
  ["ready", "Pull request open", ["ready", "review_unresolved"], undefined],
  ["ended", "Ended", ["closed", "failed", "canceled"], undefined],
  ["all", "All", [], undefined],
];
const VIEWS = [
  { value: "list", label: "List" },
  { value: "4", label: "4 up" },
  { value: "16", label: "16 up" },
  { value: "32", label: "32 up" },
];
const STREAM_COLORS: Record<string, string> = { live: "teal", connecting: "yellow", reconnecting: "orange", idle: "gray" };

// Tasks as a list or as terminal tiles, 4, 16 or 32 up. Both read ahead as you scroll.
export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const tiles = params.get("view") === "tiles";
  const size = ([4, 16, 32].find((s) => String(s) === params.get("size")) ?? 16) as TileSize;
  const filter = FILTERS.find(([key]) => key === (params.get("show") ?? "running")) ?? FILTERS[0];
  const [streamState, setStreamState] = useState("idle");
  // On "Pull request open": the tasks checked to clear, and what clearing could not do.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [clearing, setClearing] = useState(false);
  const [unclear, setUnclear] = useState<string[]>([]);
  const [reload, setReload] = useState(0);
  const clearable = filter[0] === "ready" && !tiles;

  const tasks = useInfinite(
    (offset, limit) => api.tasks({ statuses: filter[2], pr: filter[3] }, offset, limit),
    tiles ? size : 50,
    `${filter[0]}:${tiles}:${size}:${reload}`,
  );

  async function clearPicked() {
    setClearing(true);
    const failed: string[] = [];
    for (const id of picked) {
      try {
        await api.clearTask(id);
      } catch (err) {
        failed.push(`${id}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    setUnclear(failed);
    setPicked(new Set());
    setClearing(false);
    setReload((n) => n + 1);
  }

  function set(next: { show?: string; view?: string }) {
    const view = next.view ?? (tiles ? String(size) : "list");
    setParams({
      show: next.show ?? filter[0],
      ...(view === "list" ? { view: "list" } : { view: "tiles", size: view }),
    });
  }

  return (
    <>
      <PageHeader title="Tasks" description="Every task from every goal. Tiles stream the runs on screen live." />
      <Group mb="md" justify="space-between" wrap="wrap" gap="sm">
        <SegmentedControl value={filter[0]} onChange={(show) => set({ show })} data={FILTERS.map(([value, label]) => ({ value, label }))} />
        <Group gap="sm">
          {tiles && <Badge variant="dot" color={STREAM_COLORS[streamState] ?? "gray"} tt="none">{streamState}</Badge>}
          {tasks.total !== null && <Text size="sm" c="dimmed">{tasks.items.length} of {tasks.total}</Text>}
          <SegmentedControl value={tiles ? String(size) : "list"} onChange={(view) => set({ view })} data={VIEWS} />
        </Group>
      </Group>
      <ErrorAlert error={tasks.error} />
      {clearable && tasks.items.length > 0 && (
        <Group mb="sm" gap="sm">
          <Button size="xs" variant="light" disabled={!picked.size} loading={clearing} onClick={() => void clearPicked()}>
            Clear selected ({picked.size})
          </Button>
          <Text size="xs" c="dimmed">A merged pull request closes its task; any other cancels it. The pull request stays on GitHub.</Text>
        </Group>
      )}
      {unclear.length > 0 && (
        <Alert color="red" mb="sm" title="Not cleared">{unclear.map((u) => <Text key={u} size="sm">{u}</Text>)}</Alert>
      )}
      {tasks.total === 0 && <Empty>No {filter[1].toLowerCase()} tasks.</Empty>}

      {tiles ? (
        <TileGrid tasks={tasks.items} size={size} onStreamState={setStreamState} />
      ) : (
        tasks.items.length > 0 && (
          <DataTable head={[...(clearable ? [""] : []), "Task", "Status", "Repo", "Issue", "Pull request", "Updated"]}>
            {tasks.items.map((t) => (
              <Table.Tr key={t.id}>
                {clearable && (
                  <Table.Td w={36}>
                    <Checkbox
                      aria-label={`Clear ${t.title}`}
                      checked={picked.has(t.id)}
                      onChange={(e) => {
                        const on = e.currentTarget.checked;
                        setPicked((now) => {
                          const next = new Set(now);
                          if (on) next.add(t.id);
                          else next.delete(t.id);
                          return next;
                        });
                      }}
                    />
                  </Table.Td>
                )}
                <Table.Td maw={420}><TitleLink to={`/tasks/${t.id}`} title={t.title} id={t.id} /></Table.Td>
                <Table.Td><StatusBadge status={shownStatus(t)} /></Table.Td>
                <Table.Td><Text ff="monospace" size="xs">{t.repo}</Text></Table.Td>
                <Table.Td><IssueLink issueRef={t.external_ref} /></Table.Td>
                <Table.Td>{t.pr_url && <PrLink url={t.pr_url} />}</Table.Td>
                <Table.Td><Text size="sm" c="dimmed">{when(t.updated_at)}</Text></Table.Td>
              </Table.Tr>
            ))}
          </DataTable>
        )
      )}
      <div ref={tasks.sentinel} style={{ minHeight: 24 }}>
        {!tasks.done && tasks.total !== null && <Text size="sm" c="dimmed" py="sm">Loading more…</Text>}
      </div>
    </>
  );
}

// A running task with a pull request is in its review loop: checks, review, fixes.
export function shownStatus(task: Task): string {
  return task.status === "running" && task.pr_url ? "in_review" : task.status;
}
