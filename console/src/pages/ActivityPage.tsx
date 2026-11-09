import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Box, Group, SegmentedControl, Stack, Text, Tooltip } from "@mantine/core";
import { api, type Activity, type ActivityPr, type ActivitySpend } from "../api";
import { ErrorAlert, Empty, PageHeader, when } from "../components/ui";

// What each pull request state means, and its color: the two that need an operator are the strongest.
export const PR_STATES = [
  { key: "blocked", label: "Blocked", color: "var(--mantine-color-red-6)" },
  { key: "waiting", label: "Waiting", color: "var(--mantine-color-orange-5)" },
  { key: "in_review", label: "In review", color: "var(--mantine-color-blue-5)" },
  { key: "merged", label: "Merged", color: "var(--mantine-color-teal-3)" },
  { key: "closed", label: "Closed", color: "var(--mantine-color-gray-5)" },
] as const;
const STATE_COLOR = Object.fromEntries(PR_STATES.map((s) => [s.key, s.color])) as Record<string, string>;
const CELL = 14;

// A cost on the map's gradient, cool to warm, between the scale's ends.
export function costColor(usd: number, scale: { low: number; high: number }): string {
  const span = scale.high - scale.low;
  const share = span > 0 ? Math.min(1, Math.max(0, (usd - scale.low) / span)) : 0.5;
  return `hsl(${Math.round(210 - 210 * share)} 70% 50%)`;
}

function prCell(pr: ActivityPr, view: string, scale: Activity["cost_scale"], open: (pr: { task_id: string; latest_run_id: string | null }) => void) {
  const color = view === "cost" ? costColor(pr.usd, scale) : STATE_COLOR[pr.state];
  const label = (
    <Stack gap={2}>
      <Text size="xs" fw={600}>{pr.title}</Text>
      <Text size="xs">{pr.repo} #{pr.pr_url.split("/").pop()} · {pr.state.replace("_", " ")}</Text>
      <Text size="xs">Opened {when(pr.opened_at)}, {pr.state.replace("_", " ")} since {when(pr.state_at)}</Text>
      <Text size="xs">{pr.fix_rounds} fix rounds · ${pr.usd.toFixed(2)}</Text>
    </Stack>
  );
  return (
    <Tooltip key={pr.task_id} label={label} withArrow>
      <Box
        role="link"
        tabIndex={0}
        aria-label={`${pr.title}: ${pr.state}`}
        data-testid={`cell-${pr.task_id}`}
        data-state={pr.state}
        onClick={() => open(pr)}
        onKeyDown={(e) => e.key === "Enter" && open(pr)}
        style={{ width: CELL, height: CELL, borderRadius: 3, background: color, cursor: "pointer" }}
      />
    </Tooltip>
  );
}

function spendCell(spent: ActivitySpend, open: (pr: { task_id: string; latest_run_id: string | null }) => void) {
  return (
    <Tooltip key={spent.task_id} label={`${spent.title}: spent $${spent.usd.toFixed(2)}, no pull request`} withArrow>
      <Box
        role="link"
        tabIndex={0}
        aria-label={`${spent.title}: spent, no pull request`}
        data-testid={`cell-${spent.task_id}`}
        onClick={() => open(spent)}
        style={{
          width: CELL, height: CELL, borderRadius: 3, cursor: "pointer",
          background: "repeating-linear-gradient(45deg, var(--mantine-color-gray-5) 0 2px, transparent 2px 4px)",
          outline: "1px solid var(--mantine-color-gray-5)",
        }}
      />
    </Tooltip>
  );
}

// The map's rows: one per group, a cell per pull request, oldest on the left.
export function ActivityMap(props: { activity: Activity; view: "state" | "cost"; compact?: boolean }) {
  const navigate = useNavigate();
  const open = (pr: { task_id: string; latest_run_id: string | null }) =>
    navigate(`/tasks/${pr.task_id}` + (pr.latest_run_id ? `?run=${encodeURIComponent(pr.latest_run_id)}` : ""));
  const groups = props.compact
    ? [{ key: "all", name: "Every pull request", total: 0, left_out: 0, prs: props.activity.groups.flatMap((g) => g.prs).sort((a, b) => a.opened_at.localeCompare(b.opened_at)), spent_without_pr: [] }]
    : props.activity.groups;
  return (
    <Stack gap="sm">
      {groups.map((group) => {
        const counts = PR_STATES.map((s) => [s.label, group.prs.filter((p) => p.state === s.key).length] as const).filter(([, n]) => n);
        const cost = group.prs.reduce((sum, p) => sum + p.usd, 0) + group.spent_without_pr.reduce((sum, p) => sum + p.usd, 0);
        return (
          <Stack key={group.key} gap={4} data-testid={`row-${group.key}`}>
            <Group gap="xs" justify="space-between" wrap="nowrap">
              <Text size="sm" fw={500} truncate>{group.name}</Text>
              <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
                {props.view === "cost" ? `$${cost.toFixed(2)}` : counts.map(([label, n]) => `${n} ${label.toLowerCase()}`).join(" · ")}
                {group.left_out > 0 && ` · ${group.left_out} older left out`}
              </Text>
            </Group>
            <Group gap={3}>
              {group.prs.map((pr) => prCell(pr, props.view, props.activity.cost_scale, open))}
              {props.view === "cost" && group.spent_without_pr.map((s) => spendCell(s, open))}
            </Group>
          </Stack>
        );
      })}
    </Stack>
  );
}

function Legend(props: { activity: Activity; view: "state" | "cost" }) {
  const all = props.activity.groups.flatMap((g) => g.prs);
  if (props.view === "cost") {
    const { low, high } = props.activity.cost_scale;
    return (
      <Group gap="xs">
        <Text size="xs">${low.toFixed(2)}</Text>
        <Box w={120} h={10} style={{ borderRadius: 3, background: `linear-gradient(90deg, ${costColor(low, props.activity.cost_scale)}, ${costColor(high, props.activity.cost_scale)})` }} />
        <Text size="xs">${high.toFixed(2)}</Text>
        <Text size="xs" c="dimmed">Hatched: spent, no PR</Text>
      </Group>
    );
  }
  return (
    <Group gap="md">
      {PR_STATES.map((s) => (
        <Group key={s.key} gap={4}>
          <Box w={10} h={10} style={{ borderRadius: 2, background: s.color }} />
          <Text size="xs">{s.label} {all.filter((p) => p.state === s.key).length}</Text>
        </Group>
      ))}
    </Group>
  );
}

export function ActivityPage() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "cost" ? "cost" : "state";
  const groupBy = (["goal", "repo", "label"].find((g) => g === params.get("by")) ?? "goal") as "goal" | "repo" | "label";
  const [activity, setActivity] = useState<Activity | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.activity(groupBy).then(setActivity, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [groupBy]);

  return (
    <>
      <PageHeader title="Activity" description="Every pull request from the last 13 weeks, one cell each. Click a cell to open its latest run." />
      <ErrorAlert error={error} />
      <Group justify="space-between" mb="md" wrap="wrap" gap="sm">
        <SegmentedControl value={view} onChange={(v) => setParams({ view: v, by: groupBy })} data={[{ value: "state", label: "PR state" }, { value: "cost", label: "Cost" }]} />
        <SegmentedControl value={groupBy} onChange={(v) => setParams({ view, by: v })} data={[{ value: "goal", label: "By goal" }, { value: "repo", label: "By repo" }, { value: "label", label: "By label" }]} />
      </Group>
      {activity && <Box mb="md"><Legend activity={activity} view={view} /></Box>}
      {activity && activity.groups.length === 0 && <Empty>No pull requests in the last 13 weeks.</Empty>}
      {activity && <ActivityMap activity={activity} view={view} />}
    </>
  );
}
