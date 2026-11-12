import { Badge, Group, Stack, Text } from "@mantine/core";
import type { TaskStanding } from "../../api";
import { when } from "../../components/ui";

// Who has a task, from the supervisor: every open task waits on someone, and this says who, since when, and
// for a person what to do. A task the supervisor is moving itself says what it found and how that is going.

const OWNERS: Record<TaskStanding["owner"], { label: string; color: string }> = {
  workflow: { label: "Its workflow", color: "blue" },
  scheduler: { label: "The scheduler", color: "gray" },
  planner: { label: "Its goal's sweep", color: "gray" },
  supervisor: { label: "The supervisor", color: "yellow" },
  operator: { label: "You", color: "red" },
};

export function standingLine(standing: TaskStanding): string {
  const reason = standing.reason.charAt(0).toUpperCase() + standing.reason.slice(1);
  return standing.ask ? `${reason}. ${standing.ask}` : `${reason}.`;
}

export function Standing({ standing }: { standing: TaskStanding }) {
  const owner = OWNERS[standing.owner] ?? { label: standing.owner, color: "gray" };
  return (
    <Stack gap={2} data-testid="standing">
      <Group gap="xs">
        <Badge color={owner.color} variant="light" tt="none" radius="sm">{owner.label}</Badge>
        <Text size="sm">{standingLine(standing)}</Text>
      </Group>
      <Text size="xs" c="dimmed">
        Since {when(standing.since)}; the supervisor last looked {when(standing.checked_at)}.
        {standing.owner === "supervisor" && standing.attempts > 0 && ` It has acted ${standing.attempts} ${standing.attempts === 1 ? "time" : "times"}.`}
        {standing.last_error && ` Its last try failed: ${standing.last_error}`}
      </Text>
    </Stack>
  );
}
