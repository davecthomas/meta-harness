import { Badge, Group, Paper, Stack, Text, Tooltip } from "@mantine/core";
import type { MhEvent } from "../../api";
import { provenance, summariesOf, type PullSummary, type SummarySource } from "./provenance";

// A summary's source as a badge, with what that means on hover.
export function SummaryBadge({ source, size = "sm" }: { source: SummarySource; size?: "xs" | "sm" }) {
  const { label, says, color } = provenance(source);
  return (
    <Tooltip label={says} multiline maw={360}>
      <Badge size={size} variant="light" color={color} tt="none" data-testid="summary-source">{label}</Badge>
    </Tooltip>
  );
}

function SummaryCard({ summary }: { summary: PullSummary }) {
  const { says } = provenance(summary.source);
  return (
    <Paper withBorder p="sm" data-testid={`pull-summary-${summary.round}`}>
      <Stack gap={6}>
        <Group gap="xs" justify="space-between" wrap="nowrap">
          <Text size="sm" fw={600}>{summary.round ? `Fix round ${summary.round}` : summary.heading || "Summary"}</Text>
          <SummaryBadge source={summary.source} />
        </Group>
        {summary.source !== "run" && <Text size="sm" c="orange.8">{says}</Text>}
        <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{summary.text}</Text>
      </Stack>
    </Paper>
  );
}

// The summaries a task's pull request description holds, each saying whose words it is: the run's own, or a
// fallback the meta-harness wrote. Nothing for a pull request from before summaries were recorded.
export function PullSummaries({ events }: { events: MhEvent[] }) {
  const summaries = summariesOf(events);
  if (!summaries.length) return null;
  return (
    <Stack gap="xs" mb="md" data-testid="pull-summaries">
      <Text size="sm" fw={600}>The pull request's description</Text>
      {summaries.map((s) => <SummaryCard key={s.round} summary={s} />)}
    </Stack>
  );
}

// The pull request's opening summary's source, for the task's facts. Null when none was recorded.
export function OpeningSummaryBadge({ events }: { events: MhEvent[] }) {
  const opening = summariesOf(events).find((s) => s.round === 0);
  return opening ? <SummaryBadge source={opening.source} size="xs" /> : null;
}
