import { Anchor, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { MhEvent } from "../../api";
import { CandidateName } from "./words";

// Where each of a task's runs came from, as the run service recorded it before the run started (the
// run_provenance event): its kind, the pair that ran it, the run it followed on from, and which of the task's
// workflows it ran in. The control plane writes it, never the run, so it is the record to trust over a run's
// own words.

export interface RunOrigin {
  position: number;
  runId: string;
  attempt: number;
  kind: string;
  candidate: string;
  follows: string | null;
  workflow: number;
  parentTaskId: string | null;
}

export const PROVENANCE_EVENT = "run_provenance";

// The run's number within its task, from its run id (<workflow>:run-<n>); null for another id.
export function runNumber(runId: string | null | undefined): number | null {
  const match = /:run-(\d+)$/.exec(runId ?? "");
  return match ? Number(match[1]) : null;
}

// Each run's provenance, oldest first, once each.
export function provenanceOf(events: MhEvent[]): RunOrigin[] {
  const seen = new Set<string>();
  return events
    .filter((e) => e.event_type === PROVENANCE_EVENT)
    .sort((a, b) => a.position - b.position)
    .filter((e) => !seen.has(e.event_id) && !!seen.add(e.event_id))
    .map((e) => ({
      position: e.position,
      runId: String(e.payload.run_id ?? e.run_id ?? ""),
      attempt: Number(e.payload.attempt) || runNumber(e.payload.run_id) || 0,
      kind: String(e.payload.kind ?? "task"),
      candidate: String(e.payload.candidate ?? ""),
      follows: e.payload.follows ?? null,
      workflow: Number(e.payload.workflow) || 1,
      parentTaskId: e.payload.parent_task_id ?? null,
    }));
}

const KINDS: Record<string, string> = { task: "the task's own run", review: "a review run", fix: "a fix run" };

// One run's provenance in a sentence: "Run 3, a fix run, after run 2, in the task's second workflow".
export function originText(origin: RunOrigin): string {
  const parts = [`Run ${origin.attempt}`, KINDS[origin.kind] ?? `a ${origin.kind} run`];
  const after = runNumber(origin.follows);
  parts.push(after ? `after run ${after}` : "following no earlier run");
  if (origin.workflow > 1) parts.push(`in workflow ${origin.workflow}, after ${origin.workflow - 1 === 1 ? "one retry" : `${origin.workflow - 1} retries`}`);
  return parts.join(", ");
}

export function RunProvenance({ events }: { events: MhEvent[] }) {
  const origins = provenanceOf(events);
  if (!origins.length) return <Text size="sm" c="dimmed">None yet. It's recorded when a run starts.</Text>;
  const parent = origins.find((o) => o.parentTaskId)?.parentTaskId;
  return (
    <Stack gap={4} data-testid="run-provenance">
      {parent && (
        <Text size="sm">
          Made by task <Anchor component={Link} to={`/tasks/${parent}`} size="sm" ff="monospace">{parent}</Anchor>
        </Text>
      )}
      {origins.map((o) => (
        <Group key={o.position} gap="xs" wrap="nowrap" data-testid="run-origin">
          <Text size="sm">{originText(o)}</Text>
          <CandidateName pair={o.candidate} />
        </Group>
      ))}
    </Stack>
  );
}
