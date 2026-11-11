import { useNavigate, useSearchParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { Badge, Button, Group, SegmentedControl, Table, Text } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import { goalTitle, api, goalStatus } from "../../api";
import { useInfinite } from "../../hooks";
import { useRowKeys } from "../../components/CommandPalette";
import { BudgetMark } from "./budget";
import { DataTable, Empty, ErrorAlert, PageHeader, StatusBadge, TitleLink, when } from "../../components/ui";

const FILTERS: Record<string, { label: string; statuses: string[] }> = {
  open: { label: "Open", statuses: ["planning", "active", "paused", "stalled", "budget_exhausted"] },
  met: { label: "Met", statuses: ["met"] },
  ended: { label: "Ended", statuses: ["met", "canceled"] },
  all: { label: "All", statuses: [] },
};

export function GoalsPage() {
  const [params, setParams] = useSearchParams();
  const show = FILTERS[params.get("show") ?? "open"] ? (params.get("show") ?? "open") : "open";
  const goals = useInfinite((offset, limit) => api.goals(FILTERS[show].statuses, offset, limit), 50, `goals:${show}`);

  // j and k move through the goals, Enter opens the one picked.
  const navigate = useNavigate();
  const picked = useRowKeys(goals.items.length, (i) => navigate(`/goals/${goals.items[i].id}`));

  return (
    <>
      <PageHeader
        title="Goals"
        description="Each goal turns the labeled issues in its repos into tasks."
        actions={<Button component={Link} to="/goals/new" leftSection={<IconPlus size={16} />}>New goal</Button>}
      />
      <Group mb="md" justify="space-between">
        <SegmentedControl
          value={show}
          onChange={(value) => setParams({ show: value })}
          data={Object.entries(FILTERS).map(([value, f]) => ({ value, label: f.label }))}
        />
        {goals.total !== null && <Text size="sm" c="dimmed">{goals.items.length} of {goals.total}</Text>}
      </Group>
      <ErrorAlert error={goals.error} />
      {goals.items.length ? (
        <DataTable head={["Goal", "Status", "Modality", "Label", "Repos", "Budget", "Created"]}>
          {goals.items.map((g, i) => (
            <Table.Tr key={g.id} data-picked={picked === i || undefined} bg={picked === i ? "var(--mantine-color-default-hover)" : undefined}>
              <Table.Td maw={420}><TitleLink to={`/goals/${g.id}`} title={goalTitle(g)} id={g.id} /></Table.Td>
              <Table.Td><StatusBadge status={goalStatus(g)} /></Table.Td>
              <Table.Td><Text size="sm">{g.modality}</Text></Table.Td>
              <Table.Td>{g.label ? <Badge variant="outline" tt="none" radius="sm">{g.label}</Badge> : <Text size="xs" c="dimmed">Picked issues</Text>}</Table.Td>
              <Table.Td>{g.repos.map((r) => <Text ff="monospace" size="xs" key={r}>{r}</Text>)}</Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  <Text size="sm">{g.max_usd ? `$${Number(g.max_usd).toFixed(2)}` : "None"}</Text>
                  <BudgetMark goal={g} />
                </Group>
              </Table.Td>
              <Table.Td><Text size="sm" c="dimmed">{when(g.created_at)}</Text></Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
      ) : (
        goals.total !== null && <Empty>No {FILTERS[show].label.toLowerCase()} goals.</Empty>
      )}
      <div ref={goals.sentinel} />
    </>
  );
}
