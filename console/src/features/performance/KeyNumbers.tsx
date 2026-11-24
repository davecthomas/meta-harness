import { useEffect, useState } from "react";
import { Card, Group, SimpleGrid, Text, Tooltip } from "@mantine/core";
import { api } from "../../api";
import { ErrorAlert } from "../../components/ui";
import { usd } from "../../money";
import type { Both, Performance } from "./api";

// The Performance page's key numbers, large: what a task, a pull request and a goal cost, and the dollar-hours a
// successfully completed task takes, each as an average and a median, for the tasks whose last run finished in
// the days in view (GET /v1/performance). Dollar-hours is the one that says which harness, model and thinking
// level to run each kind of task on: dollars times hours for every attempt, over the share of checks that passed.

const DOLLAR_HOURS =
  "For every run of the tasks in view: its dollars times its hours, added up, then divided by how far the tasks succeeded " +
  "(the share of each task's checks that passed: the code host's checks and the merge). Failed work counts against it.";

function dh(value: number | null): string {
  if (value === null) return "none";
  return value >= 100 ? value.toFixed(0) : value >= 10 ? value.toFixed(1) : value.toFixed(2);
}

function Big(props: { label: string; both: Both; show: (value: number | null) => string; of: string; help?: string; testid: string }) {
  const label = <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{props.label}</Text>;
  return (
    <Card withBorder padding="lg" data-testid={props.testid}>
      {props.help ? <Tooltip label={props.help} multiline maw={360}>{label}</Tooltip> : label}
      {/* Each number on a line of its own width: side by side in a narrow card, the second was cut off. */}
      <Group gap="lg" mt={6} align="flex-end" wrap="wrap">
        <div style={{ whiteSpace: "nowrap" }}>
          <Text fz={32} fw={700} lh={1.1}>{props.show(props.both.median)}</Text>
          <Text size="xs" c="dimmed">median</Text>
        </div>
        <div style={{ whiteSpace: "nowrap" }}>
          <Text fz={32} fw={700} lh={1.1}>{props.show(props.both.average)}</Text>
          <Text size="xs" c="dimmed">average</Text>
        </div>
      </Group>
      <Text size="xs" c="dimmed" mt={6}>{props.both.count ? `${props.both.count} ${props.of}` : `No ${props.of} in this time`}</Text>
    </Card>
  );
}

export function KeyNumbers({ days }: { days: number }) {
  const [found, setFound] = useState<Performance | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    api.performance(days).then(setFound, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [days]);
  const money = (value: number | null) => (value === null ? "none" : usd(value));
  if (error) return <ErrorAlert error={error} />;
  if (!found) return null;
  return (
    <>
      <SimpleGrid cols={{ base: 1, sm: 2, xl: 4 }} mb="xs" data-testid="key-numbers">
        <Big testid="dollar-hours" label="Dollar-hours per completed task" both={found.dollar_hours_per_completed_task} show={dh} of="merged tasks" help={DOLLAR_HOURS} />
        <Big testid="cost-per-task" label="Cost per task" both={found.cost_per_task} show={money} of="tasks" />
        <Big testid="cost-per-pr" label="Cost per pull request" both={found.cost_per_pull_request} show={money} of="pull requests" />
        <Big testid="cost-per-goal" label="Cost per goal" both={found.cost_per_goal} show={money} of="goals" />
      </SimpleGrid>
      <Text size="sm" c="dimmed" mb="lg" data-testid="key-numbers-note">
        {found.tasks} tasks finished in this time, {found.merged} merged. {usd(Number(found.total_usd))} spent: {usd(Number(found.review_loop_usd))} in
        review and fix runs, {usd(Number(found.planning_usd ?? 0))} on planning, and {usd(Number(found.wasted_usd))} on tasks that did not merge. Deploys' smoke
        tasks are left out.
      </Text>
    </>
  );
}
