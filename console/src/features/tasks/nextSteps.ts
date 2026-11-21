import type { Diagnosis } from "./diagnosis";

// Which way out of a failure to recommend, by fixed rules on what the console knows: the kind of failure, and
// whether the task already ran on the stronger tier. The same facts always give the same answer, as the
// supervisor's remedies do (docs/technical-design.md, Remedies).

export type Step = "stronger" | "retry" | "review" | "config" | "merge";

export interface NextSteps {
  // Offer Retry with a stronger model beside the plain retry.
  stronger: boolean;
  // Offer Merge as it is: an unsettled review whose pull request is open.
  merge: boolean;
  // The one marked as recommended, or null when none is clearly best.
  recommended: Step | null;
}

// The failures a stronger model may get past: the work, not the system, stopped them.
const STRONGER_HELPS = new Set(["fix_rounds", "limit"]);

export function nextSteps(diagnosis: Diagnosis, task: { status: string | null; complexity?: string | null; pr_url?: string | null }): NextSteps {
  const unresolved = task.status === "review_unresolved";
  // The review passed and only a conflict is left: the merge resolves it, and a retry would review again.
  if (diagnosis.kind === "clean_conflict") return { stronger: false, merge: unresolved && !!task.pr_url, recommended: "merge" };
  const helps = STRONGER_HELPS.has(diagnosis.kind) || (unresolved && diagnosis.kind.startsWith("other:"));
  const tried = task.complexity === "high";
  const merge = unresolved && !!task.pr_url;
  if (!helps) return { stronger: false, merge, recommended: null };
  if (!tried) return { stronger: true, merge, recommended: "stronger" };
  // A stronger model has had its try: a person reads what is left, or changes the limit that stopped it.
  return { stronger: true, merge, recommended: unresolved && task.pr_url ? "review" : diagnosis.kind === "limit" ? "config" : null };
}
