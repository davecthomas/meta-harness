import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Checkbox, Group, SegmentedControl, Table, Text } from "@mantine/core";
import { api, type Task } from "../api";
import { useInfinite } from "../hooks";
import { TileGrid, type TileSize } from "../components/TileGrid";
import { VerdictBadge } from "../components/Reviews";
import { useRowKeys } from "../components/CommandPalette";
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
  { value: "1", label: "1 up" },
  { value: "4", label: "4 up" },
  { value: "16", label: "16 up" },
  { value: "32", label: "32 up" },
];
const STREAM_COLORS: Record<string, string> = { live: "teal", connecting: "yellow", reconnecting: "orange", idle: "gray" };

// Tasks as a list or as terminal tiles, 4, 16 or 32 up. Both read ahead as you scroll.
export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const tiles = params.get("view") === "tiles";
  // No size in the URL: Auto, which follows how many tasks the tab lists. A size the operator picks stays.
  const picked_size = [1, 4, 16, 32].find((s) => String(s) === params.get("size")) as TileSize | undefined;
  const auto = picked_size === undefined;
  const filter = FILTERS.find(([key]) => key === (params.get("show") ?? "running")) ?? FILTERS[0];
  const [streamState, setStreamState] = useState("idle");
  // On "Pull request open": the tasks checked to clear, and what clearing could not do.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [clearing, setClearing] = useState(false);
  const [unclear, setUnclear] = useState<string[]>([]);
  const [reload, setReload] = useState(0);
  const clearable = filter[0] === "ready" && !tiles;
  // On Pull request open and Ended, each task's latest review verdict, read for the tasks listed.
  const [verdicts, setVerdicts] = useState<Record<string, string>>({});

  const tasks = useInfinite(
    (offset, limit) => api.tasks({ statuses: filter[2], pr: filter[3] }, offset, limit),
    tiles ? (picked_size ?? 32) : 50,
    `${filter[0]}:${tiles}:${picked_size ?? "auto"}:${reload}`,
  );
  const size: TileSize = picked_size ?? autoSize(tasks.total ?? 0);
  // 1 up shows one task at a time, the newest first, with previous and next.
  const newestFirst = [...tasks.items].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const [one, setOne] = useState(0);
  useEffect(() => setOne(0), [tasks.total, filter[0]]);
  const at = Math.min(one, Math.max(0, newestFirst.length - 1));
  useEffect(() => {
    if (!tiles || size !== 1) return;
    const key = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "[") setOne((i) => Math.max(0, i - 1));
      if (e.key === "]") setOne((i) => Math.min(newestFirst.length - 1, i + 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [tiles, size, newestFirst.length]);

  const showVerdicts = !tiles && (filter[0] === "ready" || filter[0] === "ended");
  const reviewed = tasks.items.filter((t) => t.pr_url && verdicts[t.id] === undefined).map((t) => t.id);
  useEffect(() => {
    if (!showVerdicts || !reviewed.length) return;
    let stale = false;
    for (const id of reviewed) {
      api.reviews(id).then(
        (rounds) => !stale && setVerdicts((v) => ({ ...v, [id]: (rounds ?? []).at(-1)?.verdict ?? "" })),
        () => !stale && setVerdicts((v) => ({ ...v, [id]: "" })),
      );
    }
    return () => {
      stale = true;
    };
  }, [showVerdicts, reviewed.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

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
    const view = next.view ?? (tiles ? (auto ? "auto" : String(size)) : "list");
    setParams({
      show: next.show ?? filter[0],
      ...(view === "list" ? { view: "list" } : view === "auto" ? { view: "tiles" } : { view: "tiles", size: view }),
    });
  }

  // j and k move through the list, Enter opens the task picked.
  const navigate = useNavigate();
  const row = useRowKeys(tiles ? 0 : tasks.items.length, (i) => navigate(`/tasks/${tasks.items[i].id}`));

  return (
    <>
      <PageHeader title="Tasks" description="Every task from every goal. Tiles stream the runs on screen live." />
      <Group mb="md" justify="space-between" wrap="wrap" gap="sm">
        <SegmentedControl value={filter[0]} onChange={(show) => set({ show })} data={FILTERS.map(([value, label]) => ({ value, label }))} />
        <Group gap="sm">
          {tiles && <Badge variant="dot" color={STREAM_COLORS[streamState] ?? "gray"} tt="none">{streamState}</Badge>}
          {tasks.total !== null && <Text size="sm" c="dimmed">{tasks.items.length} of {tasks.total}</Text>}
          <SegmentedControl
            value={tiles ? (auto ? "auto" : String(size)) : "list"}
            onChange={(view) => set({ view })}
            data={[VIEWS[0], { value: "auto", label: `Auto (${size} up)` }, ...VIEWS.slice(1)]}
          />
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
        <>
          {size === 1 && newestFirst.length > 1 && (
            <Group gap="xs" mb="xs">
              <Button size="compact-sm" variant="default" disabled={at === 0} onClick={() => setOne(at - 1)}>Previous [</Button>
              <Text size="sm" c="dimmed">{at + 1} of {newestFirst.length}</Text>
              <Button size="compact-sm" variant="default" disabled={at >= newestFirst.length - 1} onClick={() => setOne(at + 1)}>Next ]</Button>
            </Group>
          )}
          <TileGrid tasks={size === 1 ? newestFirst.slice(at, at + 1) : tasks.items} size={size} onStreamState={setStreamState} />
        </>
      ) : (
        tasks.items.length > 0 && (
          <DataTable head={[...(clearable ? [""] : []), "Task", "Status", "Repo", "Issue", "Pull request", "Updated"]}>
            {tasks.items.map((t, i) => (
              <Table.Tr key={t.id} data-picked={row === i || undefined} bg={row === i ? "var(--mantine-color-default-hover)" : undefined}>
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
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <StatusBadge status={shownStatus(t)} />
                    {showVerdicts && verdicts[t.id] && <VerdictBadge verdict={verdicts[t.id]} />}
                  </Group>
                </Table.Td>
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

// The tile size Auto picks from how many tasks there are: one fills the page, a few share it.
export function autoSize(count: number): TileSize {
  return count <= 1 ? 1 : count <= 4 ? 4 : 16;
}
