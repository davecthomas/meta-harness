import { Badge, Tooltip } from "@mantine/core";
import type { IssueProgress } from "../../api";
import { provenance } from "../tasks/summaries";

// Whose words an issue's pull request description holds, on its card. One badge for the opening summary: the
// run's own, or one the meta-harness wrote because the run left none usable. A second badge, apart from it,
// for the fix rounds whose summary the meta-harness wrote. Nothing for a pull request with none recorded.
export function SummarySource({ issue }: { issue: IssueProgress }) {
  const source = issue.summary_source;
  const fixes = issue.fix_summaries_written ?? 0;
  const rounds = `${fixes} fix ${fixes === 1 ? "round" : "rounds"}`;
  return (
    <>
      {source && (
        <Tooltip label={`Opening summary: ${provenance(source).says}`} multiline maw={360}>
          <Badge size="xs" variant="light" color={source === "run" ? "teal" : "orange"} tt="none" data-testid="summary-source">
            {source === "run" ? "summary by the run" : "summary by the meta-harness"}
          </Badge>
        </Tooltip>
      )}
      {fixes > 0 && (
        <Tooltip
          label={`The meta-harness wrote the summary of ${rounds}, as ${fixes === 1 ? "its run" : "their runs"} left none usable.`}
          multiline
          maw={360}
        >
          <Badge size="xs" variant="light" color="orange" tt="none" data-testid="fix-summaries-source">
            {`${rounds} by the meta-harness`}
          </Badge>
        </Tooltip>
      )}
    </>
  );
}
