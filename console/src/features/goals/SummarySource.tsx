import { Badge, Tooltip } from "@mantine/core";
import type { IssueProgress } from "../../api";
import { provenance } from "../tasks/provenance";

// Whose words an issue's pull request description holds, on its card: the run's own summary, or one the
// meta-harness wrote because the run left none usable. Nothing for a pull request with none recorded.
export function SummarySource({ issue }: { issue: IssueProgress }) {
  const source = issue.summary_source;
  const fixes = issue.fix_summaries_written ?? 0;
  if (!source && !fixes) return null;
  // The run's own words throughout; any summary the meta-harness wrote is what a reviewer should know of.
  const byRun = source === "run" && !fixes;
  const lines = [
    source ? `Opening summary: ${provenance(source).says}` : null,
    fixes ? `The meta-harness wrote the summary of ${fixes} fix ${fixes === 1 ? "round" : "rounds"}, as ${fixes === 1 ? "its run" : "their runs"} left none usable.` : null,
  ].filter((l): l is string => l !== null);
  const label = byRun ? "summary by the run" : "summary by the meta-harness";
  return (
    <Tooltip label={lines.join(" ")} multiline maw={360}>
      <Badge size="xs" variant="light" color={byRun ? "teal" : "orange"} tt="none" data-testid="summary-source">{label}</Badge>
    </Tooltip>
  );
}
