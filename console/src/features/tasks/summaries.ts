// Where a pull request description's summary came from (the `pull_request_summary` event Publish records):
// the run's own words, or a fallback the meta-harness wrote when the run left none it could use.
import type { MhEvent } from "../../api";

export type SummarySource = "run" | "recorded" | "files";

// A summary as Publish recorded it: the opening one (round 0) or a fix round's.
export interface PullSummary {
  position: number;
  runId: string | null;
  round: number;
  source: SummarySource;
  heading: string;
  text: string;
}

const SOURCES: Record<SummarySource, { label: string; says: string; color: string }> = {
  run: {
    label: "The run's own words",
    says: "The run wrote this summary at the end of its work. The writing pass checked it before it was posted.",
    color: "teal",
  },
  recorded: {
    label: "Written by Octofusion",
    says: "The run left no usable summary, so Octofusion wrote this one from its record of the run. That record holds the files it changed, how each acceptance check ended, its last plan and the commands it ran.",
    color: "orange",
  },
  files: {
    label: "Written by Octofusion",
    says: "The run left no usable summary, so Octofusion wrote this one from the files the branch changes.",
    color: "orange",
  },
};

export function isSource(value: unknown): value is SummarySource {
  return typeof value === "string" && value in SOURCES;
}

// What to call a summary's source, what to say about it, and its color.
export function provenance(source: SummarySource): { label: string; says: string; color: string } {
  return SOURCES[source];
}

// The task's recorded summaries, oldest first, each round once: a repeated event keeps the newest.
export function summariesOf(events: MhEvent[]): PullSummary[] {
  const byRound = new Map<number, PullSummary>();
  for (const e of [...events].sort((a, b) => a.position - b.position)) {
    if (e.event_type !== "pull_request_summary") continue;
    const p = e.payload ?? {};
    if (!isSource(p.source)) continue;
    const round = Number(p.fix_round) || 0;
    byRound.set(round, { position: e.position, runId: e.run_id, round, source: p.source, heading: String(p.heading ?? ""), text: String(p.text ?? "") });
  }
  return [...byRound.values()].sort((a, b) => a.round - b.round);
}

// One line for the timeline: which summary went into the description, and whose words it is.
export function summaryLine(payload: Record<string, unknown>): string {
  const round = Number(payload.fix_round) || 0;
  const what = round ? `Fix round ${round}'s summary` : "The pull request's summary";
  if (!isSource(payload.source)) return `${what} was added to the description`;
  if (payload.source === "run") return `${what}: the run's own words`;
  const from = payload.source === "recorded" ? "its record of the run" : "the files the branch changes";
  return `${what}: written by Octofusion from ${from}, as the run left none it could use`;
}
