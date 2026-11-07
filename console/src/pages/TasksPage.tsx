import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Anchor, Badge, Group, SegmentedControl, Table, Text } from "@mantine/core";
import { api } from "../api";
import { useInfinite } from "../hooks";
import { TileGrid, type TileSize } from "../components/TileGrid";
import { DataTable, Empty, ErrorAlert, IssueLink, PageHeader, StatusBadge, TitleLink, when } from "../components/ui";

const FILTERS: [string, string, string[]][] = [
  ["running", "Running", ["running"]],
  ["waiting", "Queued and blocked", ["queued", "blocked"]],
  ["ready", "Pull request open", ["ready", "review_unresolved"]],
  ["ended", "Ended", ["closed", "failed", "canceled"]],
  ["all", "All", []],
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

  const tasks = useInfinite(
    (offset, limit) => api.tasks({ statuses: filter[2] }, offset, limit),
    tiles ? size : 50,
    `${filter[0]}:${tiles}:${size}`,
  );

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
      {tasks.total === 0 && <Empty>No {filter[1].toLowerCase()} tasks.</Empty>}

      {tiles ? (
        <TileGrid tasks={tasks.items} size={size} onStreamState={setStreamState} />
      ) : (
        tasks.items.length > 0 && (
          <DataTable head={["Task", "Status", "Repo", "Issue", "Pull request", "Updated"]}>
            {tasks.items.map((t) => (
              <Table.Tr key={t.id}>
                <Table.Td maw={420}><TitleLink to={`/tasks/${t.id}`} title={t.title} id={t.id} /></Table.Td>
                <Table.Td><StatusBadge status={t.status} /></Table.Td>
                <Table.Td><Text ff="monospace" size="xs">{t.repo}</Text></Table.Td>
                <Table.Td><IssueLink issueRef={t.external_ref} /></Table.Td>
                <Table.Td>{t.pr_url && <Anchor href={t.pr_url} target="_blank" size="sm">Open</Anchor>}</Table.Td>
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
