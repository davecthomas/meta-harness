import { Badge, Group, Paper, Progress, Stack, Text } from "@mantine/core";
import { IconCircle, IconCircleCheck, IconLoader2, IconPlayerPause } from "@tabler/icons-react";
import type { MhEvent } from "../../api";
import { ago } from "./reviewing";
import { SubTitle } from "../../components/ui";

export interface PlanStep {
  step: string;
  status: "pending" | "in_progress" | "done";
}

// The newest plan_updated event: the agent's plan as it last recorded it.
function latestPlanEvent(events: MhEvent[]): MhEvent | null {
  return [...events].filter((e) => e.event_type === "plan_updated").sort((a, b) => a.position - b.position).at(-1) ?? null;
}

// The agent's latest plan: the steps of the newest plan_updated event.
export function latestPlan(events: MhEvent[]): PlanStep[] | null {
  const last = latestPlanEvent(events);
  return last ? ((last.payload.steps ?? []) as PlanStep[]) : null;
}

// Whose plan it is and how fresh, in a line: a checklist with a step "in progress" reads as work going on,
// which is false once the run has ended, and false of a new run that has not recorded its own plan yet.
export function planStanding(events: MhEvent[], running: boolean, now = Date.now()): string {
  const last = latestPlanEvent(events);
  if (!last) return "";
  if (!running) return "As the last run left it. No run is under way.";
  const newestRun = [...events].filter((e) => e.run_id).sort((a, b) => a.position - b.position).at(-1)?.run_id;
  if (newestRun && last.run_id && newestRun !== last.run_id) return "From an earlier run. The run under way has not recorded a plan yet.";
  return last.time_utc ? `Live: the agent last updated it ${ago(last.time_utc, now)}.` : "Live: it changes as the agent updates it.";
}

// The agent's plan as a checklist, updated as the run records new plans. While a run is under way the step in
// progress is marked as going on; once none is, the same step is marked as where the work stopped.
export function Plan({ events, running = false }: { events: MhEvent[]; running?: boolean }) {
  const steps = latestPlan(events);
  if (!steps) {
    // Only while a run could still record one: an ended task with no plan has nothing to say.
    if (!running) return null;
    return (
      <Text size="xs" c="dimmed" mb="md" data-testid="no-plan">
        No plan yet. An agent records one, as a checklist shown here, on a task of more than a few steps.
      </Text>
    );
  }
  const done = steps.filter((s) => s.status === "done").length;
  const live = running && planStanding(events, running).startsWith("Live");
  return (
    <Paper withBorder p="sm" mb="md" data-testid="plan">
      <Group justify="space-between" mb={4}>
        <SubTitle>Plan</SubTitle>
        <Badge size="xs" variant="light">{done} of {steps.length} done</Badge>
      </Group>
      <Text size="xs" c="dimmed" mb={6} data-testid="plan-standing">{planStanding(events, running)}</Text>
      {steps.length > 0 && <Progress size="xs" mb={8} value={(100 * done) / steps.length} color="teal" aria-label={`${done} of ${steps.length} steps done`} />}
      <Stack gap={4}>
        {steps.map((s, i) => {
          const going = s.status === "in_progress";
          return (
            <Group key={i} gap={6} wrap="nowrap" data-status={s.status}>
              {s.status === "done" ? (
                <IconCircleCheck size={16} color="var(--mantine-color-teal-6)" />
              ) : going && live ? (
                <IconLoader2 size={16} color="var(--mantine-color-blue-6)" />
              ) : going ? (
                <IconPlayerPause size={16} color="var(--mantine-color-orange-6)" />
              ) : (
                <IconCircle size={16} color="var(--mantine-color-dimmed)" />
              )}
              <Text size="sm" fw={going ? 600 : 400} td={s.status === "done" ? "line-through" : undefined} c={s.status === "done" ? "dimmed" : undefined}>
                {s.step}
                {going && (
                  <Text span size="xs" c="dimmed" fw={400}>
                    {live ? " · in progress" : " · stopped here"}
                  </Text>
                )}
              </Text>
            </Group>
          );
        })}
      </Stack>
    </Paper>
  );
}
