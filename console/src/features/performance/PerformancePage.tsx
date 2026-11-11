import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Anchor, Card, Center, Group, Loader, MultiSelect, NativeSelect, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconTrendingDown, IconTrendingUp } from "@tabler/icons-react";
import { api, type CostFilter, type Costs } from "../../api";
import { DataTable, Empty, ErrorAlert, PageHeader, SectionTitle, when } from "../../components/ui";
import { CostChart, seriesColor, usd } from "./CostChart";

// Performance: what the meta-harness costs over time, where the money goes, and where it is heading. An admin
// sees every cost source; anyone else, the model cost of their own work, and the page says which it is showing.
// The range, bucket, breakdown and filters live in the URL, so a view can be shared.

const RANGES = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "custom", label: "Custom" },
];
const BUCKETS = [{ value: "day", label: "Daily" }, { value: "week", label: "Weekly" }, { value: "month", label: "Monthly" }];
const BREAKDOWNS = [
  { value: "source", label: "Cost source" },
  { value: "kind", label: "Kind of work" },
  { value: "goal", label: "Goal" },
  { value: "repo", label: "Repository" },
  { value: "model", label: "Model" },
  { value: "operator", label: "Operator" },
];
const FILTERS: { name: CostFilter; label: string }[] = [
  { name: "source", label: "Source" },
  { name: "kind", label: "Kind of work" },
  { name: "goal", label: "Goal" },
  { name: "repo", label: "Repository" },
  { name: "model", label: "Model" },
];
const NAMES: Record<string, string> = {
  model: "Models",
  cloud: "Google Cloud",
  task: "Tasks' own runs",
  review: "Reviews",
  fix: "Fixes",
  planning: "Planning",
  other: "Other",
  "": "Not attributed",
};

const iso = (day: Date) => day.toISOString().slice(0, 10);

// The first and last day of a range of n days ending today.
export function rangeDays(n: number, today = new Date()): { from: string; to: string } {
  const first = new Date(today);
  first.setUTCDate(first.getUTCDate() - (n - 1));
  return { from: iso(first), to: iso(today) };
}

export function costLabel(key: string, costs: Pick<Costs, "labels" | "group_by">): string {
  if (costs.labels[key]) return costs.labels[key];
  // A model named "task" would be a model: only the dimensions with fixed values are renamed.
  if (key === "" || key === "other" || costs.group_by === "source" || costs.group_by === "kind") return NAMES[key] ?? key;
  return key;
}

function Stat(props: { label: string; value: string; note?: React.ReactNode }) {
  return (
    <Card withBorder padding="lg">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{props.label}</Text>
      <Text fz={28} fw={650} lh={1.2} mt={4}>{props.value}</Text>
      {props.note && <Text size="xs" c="dimmed" mt={4}>{props.note}</Text>}
    </Card>
  );
}

export function PerformancePage() {
  const [params, setParams] = useSearchParams();
  const range = params.get("range") ?? "30";
  const bucket = params.get("bucket") ?? "day";
  const by = params.get("by") ?? "source";
  const custom = range === "custom";
  const span = useMemo(
    () => (custom ? { from: params.get("from") ?? rangeDays(30).from, to: params.get("to") ?? rangeDays(30).to } : rangeDays(Number(range) || 30)),
    [custom, range, params],
  );
  const filters = useMemo(() => Object.fromEntries(FILTERS.flatMap((f) => (params.get(f.name) ? [[f.name, params.get(f.name)!]] : []))), [params]);
  const [costs, setCosts] = useState<Costs | null>(null);
  const [error, setError] = useState<string | null>(null);

  const set = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };

  const asked = JSON.stringify({ ...span, bucket, by, filters });
  useEffect(() => {
    let live = true;
    api.costs({ ...span, bucket, group_by: by, ...filters }).then(
      (found) => live && (setCosts(found), setError(null)),
      (err) => live && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      live = false;
    };
    // The request is its parameters: asked is all of them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asked]);

  const label = (key: string) => (costs ? costLabel(key, costs) : key);
  // A series' color is its place among every value its dimension takes, so filtering never repaints the rest.
  const order = costs ? (costs.choices[by]?.length ? costs.choices[by] : costs.series.map((s) => s.key).sort()) : [];
  const own = costs?.scope === "own";
  const trend = costs?.trend;
  const per = bucket === "day" ? "day" : bucket;

  return (
    <>
      <PageHeader title="Performance" description="What the meta-harness costs over time, where the money goes, and where it is heading." />
      <ErrorAlert error={error} />
      {own && (
        <Alert color="blue" mb="md" data-testid="own-scope">
          You see the model cost of your own work: the tasks of the goals you created. An admin sees every goal and the deployment's other costs.
        </Alert>
      )}
      <Paper p="md" mb="lg" withBorder>
        <Stack gap="sm">
          <Group gap="md" align="flex-end" wrap="wrap">
            <SegmentedControl aria-label="Range" value={range} onChange={(v) => set({ range: v, ...(v === "custom" ? span : { from: null, to: null }) })} data={RANGES} />
            {custom && (
              <>
                <TextInput type="date" label="From" value={span.from} max={span.to} onChange={(e) => set({ from: e.currentTarget.value })} />
                <TextInput type="date" label="To" value={span.to} min={span.from} onChange={(e) => set({ to: e.currentTarget.value })} />
              </>
            )}
            <SegmentedControl aria-label="Bucket" value={bucket} onChange={(v) => set({ bucket: v })} data={BUCKETS} />
            <NativeSelect label="Break down by" data={own ? BREAKDOWNS.filter((b) => !["source", "operator"].includes(b.value)) : BREAKDOWNS} value={by} onChange={(e) => set({ by: e.currentTarget.value })} />
          </Group>
          {costs && (
            <Group gap="md" align="flex-end" wrap="wrap">
              {FILTERS.filter((f) => !(own && f.name === "source") && (costs.choices[f.name]?.length ?? 0) > 1).map((f) => (
                <MultiSelect
                  key={f.name}
                  label={f.label}
                  placeholder="All"
                  clearable
                  w={190}
                  data={costs.choices[f.name].map((value) => ({ value, label: costs.labels[value] ?? (f.name === "source" || f.name === "kind" ? (NAMES[value] ?? value) : value) }))}
                  value={(filters[f.name] ?? "").split(",").filter(Boolean)}
                  onChange={(values) => set({ [f.name]: values.join(",") || null })}
                />
              ))}
            </Group>
          )}
        </Stack>
      </Paper>

      {!costs && !error && <Center h={240}><Loader /></Center>}
      {costs && (
        <>
          <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} mb="lg" data-testid="cost-stats">
            <Stat label="Total" value={usd(costs.total)} note={`${costs.from} to ${costs.to}`} />
            <Stat label="Daily average" value={usd(costs.daily_average)} />
            <Stat
              label="Against the span before"
              value={costs.change_percent === null ? "–" : `${costs.change_percent > 0 ? "+" : ""}${costs.change_percent}%`}
              note={costs.change_percent === null ? "Nothing was spent in the span before" : `${usd(costs.previous_total)} before`}
            />
            <Stat
              label="A month at this trend"
              value={costs.projected_month === null ? "–" : usd(costs.projected_month)}
              note={
                trend ? (
                  <Group gap={4} component="span">
                    {trend.slope >= 0 ? <IconTrendingUp size={14} /> : <IconTrendingDown size={14} />}
                    {trend.slope >= 0 ? "Rising" : "Falling"} {usd(Math.abs(trend.slope))} a {per}
                  </Group>
                ) : "Needs two or more buckets"
              }
            />
          </SimpleGrid>

          <Paper p="md" mb="lg" withBorder>
            <Group justify="space-between" mb="xs" wrap="wrap">
              <Text fw={650}>Cost per {per}, by {BREAKDOWNS.find((b) => b.value === by)?.label.toLowerCase()}</Text>
              <Group gap="md" wrap="wrap" className="cost-viz" data-testid="cost-legend">
                {costs.series.map((s) => (
                  <Group key={s.key} gap={6} wrap="nowrap">
                    <span style={{ width: 10, height: 10, borderRadius: 2, background: seriesColor(s.key, order) }} />
                    <Text size="xs">{label(s.key)}</Text>
                  </Group>
                ))}
                {trend && (
                  <Group gap={6} wrap="nowrap">
                    <span style={{ width: 18, borderTop: "2px dashed var(--mantine-color-text)" }} />
                    <Text size="xs">Trend</Text>
                  </Group>
                )}
              </Group>
            </Group>
            {costs.total > 0 ? <CostChart costs={costs} order={order} label={label} /> : <Empty>Nothing was spent in this range{Object.keys(filters).length ? " with these filters" : ""}.</Empty>}
          </Paper>

          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg" mb="lg">
            <div>
              <SectionTitle>Where it went</SectionTitle>
              <Stack gap="xs" className="cost-viz" data-testid="cost-breakdown">
                {costs.series.map((s) => (
                  <div key={s.key}>
                    <Group justify="space-between" gap="sm" wrap="nowrap">
                      {by === "goal" && s.key.startsWith("g-") ? (
                        <Anchor component={Link} to={`/goals/${s.key}`} size="sm" lineClamp={1}>{label(s.key)}</Anchor>
                      ) : (
                        <Text size="sm" lineClamp={1}>{label(s.key)}</Text>
                      )}
                      <Text size="sm" fw={600} style={{ whiteSpace: "nowrap" }}>
                        {usd(s.total)} <Text span size="xs" c="dimmed" fw={400}>{costs.total ? Math.round((100 * s.total) / costs.total) : 0}%</Text>
                      </Text>
                    </Group>
                    <div style={{ height: 6, borderRadius: 3, background: "var(--mantine-color-default-border)", marginTop: 4 }}>
                      <div style={{ height: 6, borderRadius: 3, width: `${costs.total ? (100 * s.total) / costs.total : 0}%`, background: seriesColor(s.key, order) }} />
                    </div>
                  </div>
                ))}
                {costs.series.length === 0 && <Text size="sm" c="dimmed">Nothing to break down.</Text>}
              </Stack>
            </div>
            <div>
              <SectionTitle>Costliest tasks</SectionTitle>
              {costs.top_tasks.length ? (
                <DataTable head={["Task", "Cost"]} minWidth={0}>
                  {costs.top_tasks.map((t) => (
                    <Table.Tr key={t.task_id}>
                      <Table.Td><Anchor component={Link} to={`/tasks/${t.task_id}?tab=spend`} size="sm">{t.title ?? t.task_id}</Anchor></Table.Td>
                      <Table.Td><Text size="sm" fw={600}>{usd(t.usd)}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </DataTable>
              ) : (
                <Text size="sm" c="dimmed">No task spent anything in this range.</Text>
              )}
            </div>
          </SimpleGrid>

          {!own && (
            <>
              <SectionTitle>Cost sources</SectionTitle>
              <Stack gap={6} mb="lg" data-testid="cost-sources">
                {Object.entries(costs.sources).map(([name, s]) => (
                  <Text key={name} size="sm">
                    <Text span fw={600}>{NAMES[name] ?? name[0].toUpperCase() + name.slice(1)}</Text>: {s.note}
                    {s.harvested_at && <Text span c="dimmed"> Last read {when(s.harvested_at)}.</Text>}
                    {s.error && <Text span c="red"> The last read failed: {s.error}</Text>}
                  </Text>
                ))}
              </Stack>
            </>
          )}
        </>
      )}
    </>
  );
}
