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
// Cost category first: AI (what model calls cost) or cloud (the Google Cloud bill), or both. Each category
// has its own breakdowns and filters, since a model, a goal or a kind of run means nothing for the cloud bill,
// and a billed service nothing for model calls. Each category's costs are the source of that name.
type Category = "all" | "ai" | "cloud";
const CATEGORIES = [{ value: "all", label: "All costs" }, { value: "ai", label: "AI" }, { value: "cloud", label: "Cloud" }];
const SOURCE_OF: Record<Category, string | null> = { all: null, ai: "model", cloud: "cloud" };
// Within a category, the dimension each breakdown is. In both kinds the source's item is "kind": for AI the
// kind of run, for the cloud bill the service billed.
const BREAKDOWNS: Record<Category, { value: string; label: string; group_by: string }[]> = {
  all: [{ value: "category", label: "Cost category", group_by: "source" }],
  ai: [
    { value: "model", label: "Model", group_by: "model" },
    { value: "run", label: "Run type", group_by: "kind" },
    { value: "goal", label: "Goal", group_by: "goal" },
    { value: "repo", label: "Repository", group_by: "repo" },
    { value: "operator", label: "Operator", group_by: "operator" },
  ],
  cloud: [{ value: "service", label: "Cloud service", group_by: "kind" }],
};
const FILTERS: Record<Category, { name: CostFilter; label: string }[]> = {
  all: [],
  ai: [
    { name: "model", label: "Model" },
    { name: "kind", label: "Run type" },
    { name: "goal", label: "Goal" },
    { name: "repo", label: "Repository" },
  ],
  cloud: [{ name: "kind", label: "Cloud service" }],
};
const ALL_FILTERS: CostFilter[] = ["source", "kind", "goal", "repo", "model"];
const NAMES: Record<string, string> = {
  model: "AI",
  cloud: "Cloud",
  task: "Task runs",
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

// What a series or a filter's value is called. category: the cost category in view, since under Cloud a
// "kind" value is a service's own name and is shown as it is.
export function costLabel(key: string, costs: Pick<Costs, "labels" | "group_by">, category: Category = "all"): string {
  if (costs.labels[key]) return costs.labels[key];
  if (key === "" || key === "other") return NAMES[key];
  // A model named "task" would be a model: only the dimensions with fixed values are renamed.
  if (costs.group_by === "source" || (costs.group_by === "kind" && category !== "cloud")) return NAMES[key] ?? key;
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
  // A regular user sees only their own AI costs: the category is AI, whatever the URL says.
  const [own, setOwn] = useState(false);
  const asked_category = (params.get("category") ?? "all") as Category;
  const category: Category = own ? "ai" : CATEGORIES.some((c) => c.value === asked_category) ? asked_category : "all";
  const breakdowns = BREAKDOWNS[category];
  const breakdown = breakdowns.find((b) => b.value === params.get("by")) ?? breakdowns[0];
  const by = breakdown.value;
  const custom = range === "custom";
  const span = useMemo(
    () => (custom ? { from: params.get("from") ?? rangeDays(30).from, to: params.get("to") ?? rangeDays(30).to } : rangeDays(Number(range) || 30)),
    [custom, range, params],
  );
  // Only the category's own filters apply: one left in the URL from another category would hide its costs.
  const filters = useMemo(
    () => Object.fromEntries(FILTERS[category].flatMap((f) => (params.get(f.name) ? [[f.name, params.get(f.name)!]] : []))),
    [params, category],
  );
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

  const source = SOURCE_OF[category];
  const asked = JSON.stringify({ ...span, bucket, group_by: breakdown.group_by, filters, source });
  useEffect(() => {
    let live = true;
    api.costs({ ...span, bucket, group_by: breakdown.group_by, ...filters, ...(source ? { source } : {}) }).then(
      (found) => live && (setCosts(found), setError(null), setOwn(found.scope === "own")),
      (err) => live && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      live = false;
    };
    // The request is its parameters: asked is all of them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asked]);

  const label = (key: string) => (costs ? costLabel(key, costs, category) : key);
  // A series' color is its place among every value its dimension takes, so filtering never repaints the rest.
  const dimension = breakdown.group_by === "kind" ? "kind" : breakdown.group_by;
  const order = costs ? (costs.choices[dimension]?.length ? costs.choices[dimension] : costs.series.map((s) => s.key).sort()) : [];
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
          </Group>
          <Group gap="md" align="flex-end" wrap="wrap">
            {!own && (
              <Stack gap={4}>
                <Text size="sm" fw={500}>Cost category</Text>
                <SegmentedControl
                  aria-label="Cost category"
                  value={category}
                  data={CATEGORIES}
                  // A new category starts from its own breakdown, with no filter of another category's.
                  onChange={(v) => set({ category: v === "all" ? null : v, by: null, ...Object.fromEntries(ALL_FILTERS.map((f) => [f, null])) })}
                />
              </Stack>
            )}
            {breakdowns.length > 1 ? (
              <NativeSelect label="Break down by" data={breakdowns} value={by} onChange={(e) => set({ by: e.currentTarget.value })} />
            ) : (
              <Stack gap={4}>
                <Text size="sm" fw={500}>Broken down by</Text>
                <Text size="sm" c="dimmed" h={36} style={{ display: "flex", alignItems: "center" }}>{breakdown.label}</Text>
              </Stack>
            )}
            {costs &&
              FILTERS[category]
                // A filter with a value always shows, so it can be cleared; one with a single choice has nothing to pick.
                .filter((f) => filters[f.name] || (costs.choices[f.name]?.length ?? 0) > 1)
                .map((f) => (
                  <MultiSelect
                    key={f.name}
                    label={f.label}
                    placeholder="All"
                    clearable
                    w={190}
                    data={[...new Set([...(costs.choices[f.name] ?? []), ...(filters[f.name] ?? "").split(",").filter(Boolean)])].map((value) => ({
                      value,
                      label: costs.labels[value] ?? (f.name === "kind" && category !== "cloud" ? (NAMES[value] ?? value) : value),
                    }))}
                    value={(filters[f.name] ?? "").split(",").filter(Boolean)}
                    onChange={(values) => set({ [f.name]: values.join(",") || null })}
                  />
                ))}
          </Group>
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
              <Text fw={650}>
                {category === "all" ? "" : `${CATEGORIES.find((c) => c.value === category)?.label} cost`}
                {category === "all" ? "Cost" : ""} per {per}, by {breakdown.label.toLowerCase()}
              </Text>
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
                      {breakdown.group_by === "goal" && s.key.startsWith("g-") ? (
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
            {/* The cloud bill is the deployment's, not any task's. */}
            {category !== "cloud" && <div>
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
            </div>}
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
