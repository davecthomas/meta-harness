import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { Badge, Button, Group, Progress, SegmentedControl, Select, Stack, Table, Text, Tooltip } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import { goalTitle, api, goalStatus } from "../../api";
import { useInfinite } from "../../hooks";
import { useRowKeys } from "../../components/CommandPalette";
import { budgetColor, budgetStanding } from "./budget";
import { budgetLine, costOf, money, sortGoals, totals, usedPercent, type SortKey } from "./goalCost";
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
  const [sort, setSort] = useState<SortKey>("newest");
  const shown = sortGoals(goals.items, sort);
  const picked = useRowKeys(shown.length, (i) => navigate(`/goals/${shown[i].id}`));
  const sum = totals(shown);

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
        <Group gap="sm">
          <Select
            size="xs"
            w={200}
            aria-label="Sort goals"
            value={sort}
            onChange={(value) => setSort((value as SortKey) ?? "newest")}
            data={[
              { value: "newest", label: "Newest first" },
              { value: "cost", label: "Most expensive first" },
              { value: "used", label: "Most budget used first" },
            ]}
            allowDeselect={false}
          />
          {goals.total !== null && <Text size="sm" c="dimmed">{goals.items.length} of {goals.total}</Text>}
        </Group>
      </Group>
      <ErrorAlert error={goals.error} />
      {goals.items.length ? (
        <>
        <DataTable head={["Goal", "Status", "Cost", "Budget", "Modality", "Label", "Repos", "Created"]} minWidth={980}>
          {shown.map((g, i) => (
            <Table.Tr key={g.id} data-picked={picked === i || undefined} bg={picked === i ? "var(--mantine-color-default-hover)" : undefined}>
              <Table.Td maw={420}><TitleLink to={`/goals/${g.id}`} title={goalTitle(g)} id={g.id} /></Table.Td>
              <Table.Td><StatusBadge status={goalStatus(g)} /></Table.Td>
              <Table.Td data-testid={`cost-${g.id}`}>
                {costOf(g) === null ? (
                  <Tooltip label={`The cost could not be read: ${g.cost_unknown ?? "no answer"}. Reload to try again.`} multiline w={280}>
                    <Text size="sm" c="dimmed" td="underline">unknown</Text>
                  </Tooltip>
                ) : (
                  <Text size="sm" ff="monospace">{money(costOf(g)!)}</Text>
                )}
              </Table.Td>
              <Table.Td miw={150} data-testid={`budget-${g.id}`}>
                {g.max_usd ? (
                  <Stack gap={3}>
                    <Text size="sm" ff="monospace">{money(Number(g.max_usd))}</Text>
                    {usedPercent(g) !== null && (
                      <Progress size="sm" value={Math.min(100, usedPercent(g)!)} color={budgetColor(budgetStanding(g, costOf(g)!))} aria-label="Budget used" />
                    )}
                    <Text size="xs" c={budgetStanding(g, costOf(g) ?? undefined) === "ok" ? "dimmed" : budgetColor(budgetStanding(g, costOf(g) ?? undefined))}>
                      {budgetLine(g)}
                    </Text>
                  </Stack>
                ) : (
                  <Text size="xs" c="dimmed">no budget</Text>
                )}
              </Table.Td>
              <Table.Td><Text size="sm">{g.modality}</Text></Table.Td>
              <Table.Td>{g.label ? <Badge variant="outline" tt="none" radius="sm" miw="max-content">{g.label}</Badge> : <Text size="xs" c="dimmed">Picked issues</Text>}</Table.Td>
              <Table.Td>{g.repos.map((r) => <Text ff="monospace" size="xs" key={r}>{r}</Text>)}</Table.Td>
              <Table.Td><Text size="sm" c="dimmed">{when(g.created_at)}</Text></Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
        <Text size="sm" mt="xs" data-testid="goal-totals">
          {shown.length} goal{shown.length === 1 ? "" : "s"} listed: {money(sum.cost)} spent
          {sum.unknown ? `, and ${sum.unknown} whose cost could not be read` : ""}
          {sum.budget !== null ? ` of ${money(sum.budget)} budgeted` : ""}.
        </Text>
        </>
      ) : (
        goals.total !== null && <Empty>No {FILTERS[show].label.toLowerCase()} goals.</Empty>
      )}
      <div ref={goals.sentinel} />
    </>
  );
}
