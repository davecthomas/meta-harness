import type { ReactNode } from "react";
import { Code, Stack, Text } from "@mantine/core";
import type { MhEvent } from "../../api";

// What a post the meta-harness wrote says it came from: the provenance line at the end of a pull request's
// description, a review or a comment, or the trailers of a merge commit (run_service/provenance.py). The
// events that record each post carry the same fields, so the task's page shows them without reading the host.
export interface Provenance {
  kind: string;
  version: string;
  goal?: string;
  task?: string;
  run?: string;
  model?: string;
  round?: string;
  by?: string;
}

// The event that records each kind of post, and how its line reads.
const POSTS: Record<string, string> = {
  pull_request_opened: "Pull request",
  pull_request_reviewed: "Review",
  pull_request_commented: "Line comment",
  pull_request_merged: "Merge commit",
};

export interface Signed {
  what: string;
  provenance: Provenance;
}

// Each post's provenance, oldest first: the events that carry one, and nothing for a post written before
// provenance was published.
export function provenanceOf(events: MhEvent[]): Signed[] {
  return events
    .filter((e) => e.event_type in POSTS && isProvenance(e.payload?.provenance))
    .map((e) => ({ what: POSTS[e.event_type], provenance: e.payload.provenance as Provenance }));
}

function isProvenance(value: unknown): value is Provenance {
  return !!value && typeof value === "object" && typeof (value as Provenance).kind === "string" && typeof (value as Provenance).version === "string";
}

// One post's provenance as a sentence: the version that wrote it, then the model, run, round and operator it names.
export function provenanceText({ what, provenance: p }: Signed): string {
  const said = [
    p.round && `round ${p.round}`,
    `Octofusion ${p.version}`,
    p.model && `model ${p.model}`,
    p.run && `run ${p.run}`,
    p.by && `for ${p.by}`,
  ].filter(Boolean);
  return `${what}: ${said.join(", ")}`;
}

// The Provenance row of the task's details, when a post carries one: the newest of each kind of post.
export function provenanceFacts(events: MhEvent[]): [string, ReactNode][] {
  const newest = new Map<string, Signed>();
  for (const s of provenanceOf(events)) newest.set(s.what, s);
  if (!newest.size) return [];
  return [[
    "Provenance",
    <Stack gap={2} data-testid="task-provenance">
      {[...newest.values()].map((s) => (
        <Text key={s.what} size="sm"><Code>{provenanceText(s)}</Code></Text>
      ))}
    </Stack>,
  ]];
}
