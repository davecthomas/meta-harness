// The writing pass's record on a task (the `writing_checked` event) as one line for the timeline: what was
// checked, what the scanner found, and whether a rewrite was posted in its place.

const WHAT: Record<string, string> = {
  pull_request: "the pull request's description",
  review: "the review",
  comment: "a comment",
  issue: "an issue",
};

export function writingLine(payload: Record<string, unknown>): string {
  const what = WHAT[String(payload.kind)] ?? "a post";
  const findings = Number(payload.findings) || 0;
  if (!findings) return `Writing pass on ${what}: nothing found`;
  const texts = Number(payload.texts) || 1;
  const kept = Number(payload.kept) || 0;
  const reasons = Array.isArray(payload.reasons) ? payload.reasons.map(String) : [];
  const found = `${findings} ${findings === 1 ? "finding" : "findings"}`;
  if (kept && !reasons.length) return `Writing pass on ${what}: ${found}, rewritten before it was posted`;
  if (kept) return `Writing pass on ${what}: ${found}; ${kept} of ${texts} texts rewritten, the rest posted as written (${reasons.join("; ")})`;
  return `Writing pass on ${what}: ${found}, posted as written (${reasons.join("; ") || "no rewrite was made"})`;
}
