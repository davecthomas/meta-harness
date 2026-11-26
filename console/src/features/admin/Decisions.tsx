import { useEffect, useState } from "react";
import { Anchor, SegmentedControl, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { api, type MhEvent } from "../../api";
import { DataTable, Empty, ErrorAlert, when } from "../../components/ui";
import { usd } from "../../money";
import { candidateName } from "../tasks/path";

// Every decision Muxxy made about its work, newest first: which tier and model each task was routed
// to, how each run ended, each review's verdict, each merge, and what the Task Manager or the Goal Planner did
// to unstick a task. The Audit page's other list holds the changes people made.

export const DECISIONS: Record<string, string> = {
  task_routed: "Routing",
  attempt_finished: "Runs",
  pull_request_reviewed: "Reviews",
  pull_request_merged: "Merges",
  remedy_applied: "Unsticking",
};

export function describeDecision(e: MhEvent): string {
  const p = e.payload ?? {};
  const first = Array.isArray(p.candidates) && p.candidates.length ? candidateName(String(p.candidates[0])) : "";
  switch (e.event_type) {
    case "task_routed": {
      const how = p.method === "classifier" && typeof p.confidence === "number" ? `by the classifier, ${Math.round(p.confidence * 100)}% sure` : `by ${p.method}`;
      return `Routed to the ${p.tier} tier ${how}${first ? `. First choice: ${first}` : ""}`;
    }
    case "attempt_finished": {
      const ended = p.decision === "finished" ? "finished" : p.decision === "next_candidate" ? "failed, and the next model took it" : "failed";
      const spent = p.spent_usd ? `, ${usd(Number(p.spent_usd))}` : "";
      return `A ${p.tier === "review" ? "review" : p.tier === "fix" ? "fix" : "task"} run on ${candidateName(String(p.candidate ?? ""))} ${ended}${spent}`;
    }
    case "pull_request_reviewed": {
      const found = Number(p.findings) || 0;
      const verdict = p.verdict === "clean" ? "passed" : `asked for changes (${found} finding${found === 1 ? "" : "s"})`;
      return `Review round ${p.round} by ${candidateName(String(p.reviewer ?? ""))} ${verdict}`;
    }
    case "pull_request_merged":
      return `Merged ${String(p.pr_url ?? "").replace(/^https:\/\/github\.com\//, "")}`;
    case "remedy_applied":
      return `${e.actor === "planner" ? "The Goal Planner" : "The Task Manager"}: ${p.did}${p.because ? `, because ${String(p.because).replace(/\.$/, "")}` : ""}`;
    default:
      return e.event_type;
  }
}

export function Decisions() {
  const [kind, setKind] = useState("all");
  const [events, setEvents] = useState<MhEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setEvents(null);
    const types = kind === "all" ? Object.keys(DECISIONS) : [kind];
    api.recentEvents(types, 200).then((found) => setEvents([...found].reverse()), (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [kind]);
  return (
    <>
      <SegmentedControl
        mb="md"
        size="xs"
        value={kind}
        onChange={setKind}
        data={[{ value: "all", label: "All" }, ...Object.entries(DECISIONS).map(([value, label]) => ({ value, label }))]}
      />
      <ErrorAlert error={error} />
      {events && events.length === 0 && <Empty>No decisions of this kind yet.</Empty>}
      {events && events.length > 0 && (
        <DataTable head={["When", "Task", "Decision"]}>
          {events.map((e) => (
            <Table.Tr key={e.event_id} data-testid="decision">
              <Table.Td><Text size="sm" c="dimmed">{when(e.time_utc)}</Text></Table.Td>
              <Table.Td>{e.task_id ? <Anchor component={Link} to={`/tasks/${e.task_id}`} size="sm" ff="monospace">{e.task_id}</Anchor> : <Text size="sm" c="dimmed">none</Text>}</Table.Td>
              <Table.Td><Text size="sm">{describeDecision(e)}</Text></Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
      )}
    </>
  );
}
