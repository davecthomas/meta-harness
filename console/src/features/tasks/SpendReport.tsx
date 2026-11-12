import { useEffect, useState } from "react";
import { Anchor, Badge, Center, Group, Loader, Progress, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { api, type TaskSpend } from "../../api";
import { DataTable, Empty, ErrorAlert } from "../../components/ui";

const KIND_NAMES: Record<string, string> = { task: "the task's own run", review: "review", fix: "fix" };

// A count of tokens, short: 8.1M, 550k, 900.
export function tokens(n: number | null): string {
  if (n === null || n === undefined) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

// The share of a run's input that came from the cache: what it did not pay full price to read again.
export function cachedShare(input: number | null, cached: number | null): string {
  if (!input || cached === null || cached === undefined) return "";
  return `${Math.round((100 * cached) / input)}%`;
}

// Where a task's money went: each run with its kind, turns, tokens and cost, the most expensive marked, and the
// totals against the task's spend limit. status is the task's, so the tab rereads as the task moves on.
export function SpendReport({ taskId, status }: { taskId: string; status: string }) {
  const [spend, setSpend] = useState<TaskSpend | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.taskSpend(taskId).then(setSpend, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [taskId, status]);

  if (error) return <ErrorAlert error={error} />;
  if (!spend) return <Center h={120}><Loader /></Center>;
  if (!spend.runs.length) return <Empty>No run has finished yet, so nothing is spent.</Empty>;
  const total = Number(spend.totals.usd);
  const limit = spend.max_usd ? Number(spend.max_usd) : null;
  const costliest = Math.max(...spend.runs.map((r) => Number(r.usd)));
  return (
    <Stack gap="md">
      <Group gap="xl" align="flex-end" data-testid="spend-totals">
        <Stack gap={2} w={260}>
          <Text size="sm">
            <Text span fw={650}>${total.toFixed(2)}</Text> spent{limit !== null ? ` of $${limit.toFixed(2)}` : ""}, in {spend.totals.runs} runs and {spend.totals.turns} turns
          </Text>
          {limit !== null && <Progress value={Math.min(100, (100 * total) / limit)} color={total >= limit ? "red" : total >= 0.8 * limit ? "orange" : "blue"} />}
        </Stack>
        {Object.entries(spend.totals.by_kind).filter(([, usd]) => Number(usd) > 0).map(([kind, usd]) => (
          <Text key={kind} size="sm" c="dimmed">
            {KIND_NAMES[kind] ?? kind}: ${Number(usd).toFixed(2)}
          </Text>
        ))}
        <Text size="sm" c="dimmed">
          {tokens(spend.totals.input_tokens)} tokens in ({cachedShare(spend.totals.input_tokens, spend.totals.cached_input_tokens) || "0%"} from the cache), {tokens(spend.totals.output_tokens)} out
        </Text>
      </Group>
      <DataTable head={["Run", "Kind", "Model", "Turns", "Tokens in", "Cached", "Tokens out", "Cost", "Ended"]} minWidth={860}>
        {spend.runs.map((run) => {
          const usd = Number(run.usd);
          return (
            <Table.Tr key={run.run_id ?? run.attempt} data-testid={`spend-run-${run.attempt}`}>
              <Table.Td>
                <Anchor component={Link} to={`/tasks/${taskId}?run=${encodeURIComponent(run.run_id ?? "")}`} size="sm">Run {run.attempt}</Anchor>
              </Table.Td>
              <Table.Td><Badge variant="light" tt="none" color={run.kind === "review" ? "grape" : run.kind === "fix" ? "orange" : "blue"}>{run.kind}</Badge></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{(run.candidate ?? "").replace(/^harness:/, "")}</Text></Table.Td>
              <Table.Td><Text size="sm">{run.turns ?? ""}</Text></Table.Td>
              <Table.Td><Text size="sm">{tokens(run.input_tokens)}</Text></Table.Td>
              <Table.Td><Text size="sm">{cachedShare(run.input_tokens, run.cached_input_tokens)}</Text></Table.Td>
              <Table.Td><Text size="sm">{tokens(run.output_tokens)}</Text></Table.Td>
              <Table.Td>
                <Group gap="xs" wrap="nowrap">
                  <Text size="sm" fw={usd === costliest ? 700 : 400}>${usd.toFixed(2)}</Text>
                  {usd === costliest && spend.runs.length > 1 && <Badge size="xs" color="red" variant="light" tt="none">most expensive</Badge>}
                </Group>
                <Progress value={costliest ? (100 * usd) / costliest : 0} size="xs" mt={4} />
              </Table.Td>
              <Table.Td><Text size="xs" c={run.status === "FINISHED" ? "dimmed" : "red"}>{run.status === "FINISHED" ? "finished" : `${run.status ?? "did not start"}${run.end_reason ? ` (${run.end_reason})` : ""}`}</Text></Table.Td>
            </Table.Tr>
          );
        })}
        <Table.Tr data-testid="spend-total" style={{ borderTop: "2px solid var(--mantine-color-default-border)" }}>
          <Table.Td><Text size="sm" fw={650}>Total</Text></Table.Td>
          <Table.Td><Text size="sm" c="dimmed">{spend.totals.runs} runs</Text></Table.Td>
          <Table.Td />
          <Table.Td><Text size="sm" fw={650}>{spend.totals.turns}</Text></Table.Td>
          <Table.Td><Text size="sm" fw={650}>{tokens(spend.totals.input_tokens)}</Text></Table.Td>
          <Table.Td><Text size="sm" fw={650}>{cachedShare(spend.totals.input_tokens, spend.totals.cached_input_tokens)}</Text></Table.Td>
          <Table.Td><Text size="sm" fw={650}>{tokens(spend.totals.output_tokens)}</Text></Table.Td>
          <Table.Td><Text size="sm" fw={650}>${total.toFixed(2)}</Text></Table.Td>
          <Table.Td />
        </Table.Tr>
      </DataTable>
    </Stack>
  );
}
