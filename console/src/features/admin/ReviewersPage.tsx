import { useEffect, useState } from "react";
import { SegmentedControl, Table, Text, Tooltip } from "@mantine/core";
import { api } from "../../api";
import { DataTable, Empty, ErrorAlert, PageHeader } from "../../components/ui";
import { usd } from "../../money";
import { candidateName } from "../tasks/path";
import type { ReviewerRow } from "./api";

// How each reviewer's findings turned out: fixed or not as the next round's reviewer judged them, dismissed by an
// operator, or never judged. A reviewer whose findings are often dismissed or never fixed costs fix rounds for
// nothing, and this is where that shows (GET /v1/reviewers).

const RANGES = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
];

// The share of a reviewer's judged findings that were fixed: what its findings were worth.
function fixedShare(row: ReviewerRow): string {
  const judged = row.fixed + row.not_fixed + row.dismissed;
  return judged ? `${Math.round((100 * row.fixed) / judged)}%` : "none judged";
}

export function ReviewersPage() {
  const [days, setDays] = useState("30");
  const [rows, setRows] = useState<ReviewerRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setRows(null);
    setError(null);
    api.reviewers(Number(days)).then((found) => setRows(found.reviewers), (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [days]);
  return (
    <>
      <PageHeader
        title="Reviewers"
        description="The next round's reviewer judges each finding fixed or not. A finding no round followed is never judged."
      />
      <SegmentedControl mb="md" size="xs" value={days} onChange={setDays} data={RANGES} />
      <ErrorAlert error={error} />
      {rows && rows.length === 0 && <Empty>No review round in this time. Each round a reviewer runs is counted here.</Empty>}
      {rows && rows.length > 0 && (
        <DataTable head={["Reviewer", "Rounds", "Findings", "Fixed", "Not fixed", "Dismissed", "Never judged", "Fixed of judged", "Cost"]}>
          {rows.map((row) => (
            <Table.Tr key={row.reviewer} data-testid="reviewer">
              <Table.Td>
                <Tooltip label={row.reviewer}><Text size="sm">{candidateName(row.reviewer)}</Text></Tooltip>
              </Table.Td>
              <Table.Td>{row.rounds}</Table.Td>
              <Table.Td>{row.findings}</Table.Td>
              <Table.Td>{row.fixed}</Table.Td>
              <Table.Td>{row.not_fixed}</Table.Td>
              <Table.Td>{row.dismissed}</Table.Td>
              <Table.Td>{row.never_judged}</Table.Td>
              <Table.Td>{fixedShare(row)}</Table.Td>
              <Table.Td>{usd(Number(row.cost_usd))}</Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
      )}
    </>
  );
}
