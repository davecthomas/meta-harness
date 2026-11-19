import { useEffect, useState } from "react";
import { Anchor, Badge, Group, Stack, Table, Text } from "@mantine/core";
import { api, type PullCheck, type PullChecks } from "../../api";
import { ReadFailed } from "../../components/ReadFailed";
import { DataTable, Empty } from "../../components/ui";
import { span } from "./path";

// How often the checks are read again while any is still going, and once they have all ended.
const GOING_MS = 5000;
const ENDED_MS = 30_000;

const ended = (check: PullCheck) => check.status === "completed";
const PASSING = new Set(["success", "neutral", "skipped"]);

// How long a check has run, or ran: counted in the browser while it is going.
export function ran(check: PullCheck, now: number): number | null {
  if (check.seconds !== null) return check.seconds * 1000;
  return check.started_at && !ended(check) ? Math.max(0, now - Date.parse(check.started_at)) : null;
}

// The checks in a line, for a step or a card: "4 of 6 passed, sandbox running 2m 10s, usually about 4m".
export function checksLine(checks: PullChecks | null, now: number): string {
  if (!checks) return "";
  if (!checks.total) return "No check has started yet";
  const parts = [`${checks.passed} of ${checks.total} passed`];
  if (checks.failed.length) parts.push(`failed: ${checks.failed.join(", ")}`);
  const going = checks.checks.filter((c) => !ended(c));
  // The one that will finish last: the longest usual time, else the first.
  const slowest = [...going].sort((a, b) => (b.usual_seconds ?? 0) - (a.usual_seconds ?? 0))[0];
  if (slowest) {
    const took = ran(slowest, now);
    parts.push(`${slowest.name} ${took === null ? "waiting to start" : `running ${span(took)}`}${going.length > 1 ? ` and ${going.length - 1} more` : ""}`);
  }
  // How long this commit's own checks usually take: the slowest of them. Not every check of the default
  // branch runs on a pull request (a deploy does not), so only the ones here count.
  const usual = Math.max(0, ...checks.checks.map((c) => c.usual_seconds ?? 0));
  if (going.length && usual) parts.push(`usually about ${span(usual * 1000)} in all`);
  return parts.join(", ");
}

// The checks on the commit a task waits on, read again every few seconds while any is going. active false:
// nothing is read (the task has no pull request, or nobody is looking). again: read once more when it changes,
// such as when the task records a step.
export function useChecks(taskId: string, active: boolean, again?: unknown): { checks: PullChecks | null; error: string | null } {
  const [checks, setChecks] = useState<PullChecks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const going = !checks || checks.state === "pending" || checks.state === "none";
  useEffect(() => {
    if (!active) return;
    let stale = false;
    // A hidden page is not kept current, but it is read once, and again the moment it is looked at: a tab
    // opened in the background shows its checks when it is first seen.
    const read = (first = false) => {
      if (document.hidden && !first) return;
      api.pullChecks(taskId).then(
        (found) => {
          if (stale) return;
          setChecks(found);
          setError(null);
        },
        (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
      );
    };
    read(true);
    const timer = setInterval(read, going ? GOING_MS : ENDED_MS);
    const seen = () => read();
    document.addEventListener("visibilitychange", seen);
    return () => {
      stale = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", seen);
    };
  }, [taskId, active, going, again]);
  return { checks, error };
}

function StateBadge({ check }: { check: PullCheck }) {
  if (!ended(check)) return <Badge variant="light" color={check.status === "queued" ? "gray" : "blue"} tt="none">{check.status === "queued" ? "Waiting to start" : "Running"}</Badge>;
  const passed = PASSING.has(check.conclusion ?? "");
  return <Badge variant="light" color={passed ? "teal" : "red"} tt="none">{check.conclusion === "skipped" ? "Skipped" : passed ? "Passed" : `Failed${check.conclusion && check.conclusion !== "failure" ? ` (${check.conclusion})` : ""}`}</Badge>;
}

// A task's Checks tab: each of the code host's checks on the commit the task waits on, as it runs.
export function Checks({ checks, error }: { checks: PullChecks | null; error: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  const going = !!checks && checks.checks.some((c) => !ended(c));
  useEffect(() => {
    if (!going) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [going]);
  if (!checks) return error ? <ReadFailed what="this task's checks" error={error} retries="It is read again every few seconds." /> : <Empty>Reading the checks…</Empty>;
  const where = checks.of === "merge" ? "the merge commit" : `pull request #${checks.number}'s newest commit`;
  return (
    <Stack gap="sm">
      <Text size="sm" data-testid="checks-summary">
        <Text span fw={600}>{checks.total ? checksLine(checks, now) : "No check has started yet"}</Text>
        <Text span c="dimmed"> · on {where}, <Text span ff="monospace">{checks.sha.slice(0, 7)}</Text></Text>
      </Text>
      <Text size="xs" c="dimmed">
        {checks.of === "merge"
          ? "The code host runs these on the merge. The task closes when they pass."
          : "The code host runs these on every new commit. The task's review starts when they have all passed; a failed one sends the task to a fix run."}
      </Text>
      {error && <Text size="xs" c="orange">The last read failed, so this may be out of date: {error}</Text>}
      {checks.total > 0 && (
        <DataTable head={["Check", "State", "Time", "Usually", ""]} minWidth={560}>
          {checks.checks.map((check) => {
            const took = ran(check, now);
            return (
              <Table.Tr key={check.name} data-testid={`check-${check.name}`}>
                <Table.Td><Text size="sm">{check.name}</Text></Table.Td>
                <Table.Td><StateBadge check={check} /></Table.Td>
                <Table.Td><Text size="sm" ff="monospace">{took === null ? "" : `${span(took)}${ended(check) ? "" : " so far"}`}</Text></Table.Td>
                <Table.Td><Text size="sm" c="dimmed" ff="monospace">{check.usual_seconds === null ? "" : span(check.usual_seconds * 1000)}</Text></Table.Td>
                <Table.Td>{check.url && <Group justify="flex-end"><Anchor href={check.url} target="_blank" size="sm">Log</Anchor></Group>}</Table.Td>
              </Table.Tr>
            );
          })}
        </DataTable>
      )}
    </Stack>
  );
}
