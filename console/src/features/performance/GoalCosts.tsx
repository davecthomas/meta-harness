import { useEffect, useState } from "react";
import { Anchor, Collapse, Group, Paper, RingProgress, Stack, Table, Text, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { DataTable, ErrorAlert, SectionTitle } from "../../components/ui";
import { usd } from "../../money";
import { seriesColor } from "./CostChart";
import type { GoalCosts as Found } from "./api";

// Goal Cost Composition: of what a goal costs, the share that goes to each stage of its work, averaged over the
// completed goals in the days in view (GET /v1/costs/goals). A ring shows the shares, beside the average dollars
// of each. The goals one by one are in a table under it, folded away.

// The stages in the order the work goes through them, from planning to what is posted and told at the end.
export const STAGES = ["planning", "task", "review", "fix", "writing", "narratives"];
export const STAGE_NAMES: Record<string, string> = {
  planning: "Planning",
  task: "Task runs",
  review: "Reviews",
  fix: "Fixes",
  writing: "Writing pass",
  narratives: "Narratives",
};
const STAGE_HELP: Record<string, string> = {
  planning: "The planning run, and sizing, splitting and fixing the goal's issues",
  task: "The agent doing each task",
  review: "Review runs on each pull request",
  fix: "Fix runs after a review or a failed check",
  writing: "The rewrite of text before it is posted",
  narratives: "The goal's narratives and next steps",
};

type Goal = Found["goals"][number];

// Each stage's average share of a goal's cost, and its average dollars, over these goals: every goal counts
// the same, so one large goal does not decide the shares.
export function composition(goals: Goal[]): { stage: string; share: number; usd: number }[] {
  const counted = goals.filter((g) => g.total > 0);
  return STAGES.map((stage) => ({
    stage,
    share: counted.length ? counted.reduce((sum, g) => sum + (g.parts[stage] ?? 0) / g.total, 0) / counted.length : 0,
    usd: counted.length ? counted.reduce((sum, g) => sum + (g.parts[stage] ?? 0), 0) / counted.length : 0,
  }));
}

export function GoalCosts({ from, to }: { from: string; to: string }) {
  const [found, setFound] = useState<Found | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [listed, setListed] = useState(false);
  useEffect(() => {
    setError(null);
    api.goalCosts({ from, to }).then(setFound, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [from, to]);
  if (error) return <ErrorAlert error={error} />;
  if (!found || !found.goals.length) return null;
  // Completed goals say what a goal costs from start to end; with none yet, every goal with a cost stands in.
  const done = found.goals.filter((g) => g.status === "met");
  const over = done.length ? done : found.goals;
  const parts = composition(over);
  const average = over.reduce((sum, g) => sum + g.total, 0) / over.length;
  const percent = (share: number) => `${Math.round(share * 100)}%`;
  const cell = (value: number | undefined) => (value ? usd(value) : <Text span c="dimmed">none</Text>);
  return (
    <Paper p="md" mb="lg" withBorder data-testid="goal-costs">
      <SectionTitle>Goal Cost Composition</SectionTitle>
      <Text size="sm" c="dimmed" mb="md" data-testid="composition-over">
        {done.length
          ? `The average over ${done.length} completed ${done.length === 1 ? "goal" : "goals"} in this time.`
          : `No goal was completed in this time, so this is the average over all ${over.length} with a cost.`}
      </Text>
      <Group gap="xl" align="center" wrap="wrap">
        <RingProgress
          size={190}
          thickness={26}
          aria-label="The share of a goal's cost that goes to each stage"
          sections={parts.filter((p) => p.share > 0).map((p) => ({ value: p.share * 100, color: seriesColor(p.stage, STAGES), tooltip: `${STAGE_NAMES[p.stage]}: ${percent(p.share)}` }))}
          label={
            <Stack gap={0} align="center">
              <Text fz={22} fw={700} lh={1.1}>{usd(average)}</Text>
              <Text size="xs" c="dimmed">a goal</Text>
            </Stack>
          }
        />
        <Stack gap={6} data-testid="composition">
          {parts.map((p) => (
            <Group key={p.stage} gap="sm" wrap="nowrap" title={STAGE_HELP[p.stage]} data-testid={`stage-${p.stage}`}>
              <span style={{ width: 12, height: 12, borderRadius: 3, background: seriesColor(p.stage, STAGES), flex: "none" }} />
              <Text size="sm" w={110}>{STAGE_NAMES[p.stage]}</Text>
              <Text size="sm" fw={700} w={48} ta="right">{percent(p.share)}</Text>
              <Text size="sm" c="dimmed">{usd(p.usd)}</Text>
            </Group>
          ))}
        </Stack>
      </Group>
      <UnstyledButton mt="md" onClick={() => setListed((now) => !now)} aria-expanded={listed} data-testid="goal-costs-toggle">
        <Group gap={6}>
          {listed ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          <Text size="sm" fw={500}>Each goal</Text>
          <Text size="sm" c="dimmed">{found.goals.length} with a cost in this time</Text>
        </Group>
      </UnstyledButton>
      <Collapse expanded={listed}>
        <DataTable head={["Goal", "Total", ...STAGES.map((s) => STAGE_NAMES[s])]}>
          {found.goals.map((goal) => (
            <Table.Tr key={goal.goal_id} data-testid="goal-cost">
              <Table.Td>
                <Anchor component={Link} to={`/goals/${goal.goal_id}`} size="sm">{goal.title}</Anchor>
              </Table.Td>
              <Table.Td><Text size="sm" fw={600}>{usd(goal.total)}</Text></Table.Td>
              {STAGES.map((s) => <Table.Td key={s}><Text size="sm">{cell(goal.parts[s])}</Text></Table.Td>)}
            </Table.Tr>
          ))}
        </DataTable>
      </Collapse>
    </Paper>
  );
}
