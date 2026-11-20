// The `provenance_added` event as one line for the timeline: how many of the files a run created were given a
// header naming the run, and the first few of them.

const NAMED = 3;

export function provenanceLine(payload: Record<string, unknown>): string {
  const files = Array.isArray(payload.files) ? payload.files.map(String) : [];
  const count = Number(payload.count) || files.length;
  const run = payload.run_id ? ` by run ${payload.run_id}` : "";
  const named = files.slice(0, NAMED);
  const more = count > named.length ? `, and ${count - named.length} more` : "";
  return `Provenance header added to ${count} ${count === 1 ? "file" : "files"} created${run}${named.length ? `: ${named.join(", ")}${more}` : ""}`;
}
