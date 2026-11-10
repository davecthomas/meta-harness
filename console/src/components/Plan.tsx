import { Badge, Group, Paper, Stack, Text } from "@mantine/core";
import { IconCircle, IconCircleCheck, IconLoader2 } from "@tabler/icons-react";
import type { MhEvent } from "../api";

export interface PlanStep {
  step: string;
  status: "pending" | "in_progress" | "done";
}

// The agent's latest plan: the steps of the newest plan_updated event.
export function latestPlan(events: MhEvent[]): PlanStep[] | null {
  const last = [...events].filter((e) => e.event_type === "plan_updated").sort((a, b) => a.position - b.position).at(-1);
  return last ? ((last.payload.steps ?? []) as PlanStep[]) : null;
}

// The agent's plan as a checklist, the step in progress marked, updated as the run records new plans.
export function Plan({ events }: { events: MhEvent[] }) {
  const steps = latestPlan(events);
  if (!steps) return null;
  const done = steps.filter((s) => s.status === "done").length;
  return (
    <Paper withBorder p="sm" mb="md" data-testid="plan">
      <Group justify="space-between" mb={6}>
        <Text size="sm" fw={600}>Plan</Text>
        <Badge size="xs" variant="light">{done} of {steps.length} done</Badge>
      </Group>
      <Stack gap={4}>
        {steps.map((s, i) => (
          <Group key={i} gap={6} wrap="nowrap" data-status={s.status}>
            {s.status === "done" ? <IconCircleCheck size={16} color="var(--mantine-color-teal-6)" /> : s.status === "in_progress" ? <IconLoader2 size={16} color="var(--mantine-color-blue-6)" /> : <IconCircle size={16} color="var(--mantine-color-dimmed)" />}
            <Text size="sm" fw={s.status === "in_progress" ? 600 : 400} td={s.status === "done" ? "line-through" : undefined} c={s.status === "done" ? "dimmed" : undefined}>
              {s.step}
            </Text>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}
