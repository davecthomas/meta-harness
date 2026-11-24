import { useEffect, useState } from "react";
import { Anchor, Paper, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { DataTable, ErrorAlert, SectionTitle } from "../../components/ui";
import { usd } from "../../money";
import type { GoalCosts as Found } from "./api";

// What each goal's cost is made of, for the days in view (GET /v1/costs/goals): its task runs, its reviews, its
// fix runs, planning it (the planning run, and sizing, splitting and fixing its issues), telling the operator
// about it (narratives and next steps), and the writing pass on what it posts. Most expensive goal first.

export const KIND_NAMES: Record<string, string> = {
  task: "Task runs",
  review: "Reviews",
  fix: "Fixes",
  planning: "Planning",
  narratives: "Narratives",
  writing: "Writing pass",
};

export function GoalCosts({ from, to }: { from: string; to: string }) {
  const [found, setFound] = useState<Found | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    api.goalCosts({ from, to }).then(setFound, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [from, to]);
  if (error) return <ErrorAlert error={error} />;
  if (!found || !found.goals.length) return null;
  const cell = (value: number | undefined) => (value ? usd(value) : <Text span c="dimmed">none</Text>);
  return (
    <Paper p="md" mb="lg" withBorder data-testid="goal-costs">
      <SectionTitle>What each goal's cost is made of</SectionTitle>
      <Text size="sm" c="dimmed" mb="sm">
        Planning is the planning run, and sizing, splitting and fixing the goal's issues. Narratives is its narratives and next steps.
      </Text>
      <DataTable head={["Goal", "Total", ...found.kinds.map((k) => KIND_NAMES[k] ?? k)]}>
        {found.goals.map((goal) => (
          <Table.Tr key={goal.goal_id} data-testid="goal-cost">
            <Table.Td>
              <Anchor component={Link} to={`/goals/${goal.goal_id}`} size="sm">{goal.title}</Anchor>
            </Table.Td>
            <Table.Td><Text size="sm" fw={600}>{usd(goal.total)}</Text></Table.Td>
            {found.kinds.map((k) => <Table.Td key={k}><Text size="sm">{cell(goal.parts[k])}</Text></Table.Td>)}
          </Table.Tr>
        ))}
      </DataTable>
    </Paper>
  );
}
