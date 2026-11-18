import { Badge, Group, Stack, Text, Tooltip } from "@mantine/core";
import type { MhEvent } from "../../api";
import { usd as money } from "../../money";

// What a task's model calls cost, live: the total, the tokens, the cost by model and by attempt, and what the
// run under way has cost so far (the model calls since the last attempt finished).
export interface TaskSpend {
  usd: number;
  underWay: number;
  input: number;
  output: number;
  byModel: Record<string, number>;
  byAttempt: { attempt: number; candidate: string; usd: number; decision: string }[];
}

export function spendOf(events: MhEvent[]): TaskSpend {
  const out: TaskSpend = { usd: 0, underWay: 0, input: 0, output: 0, byModel: {}, byAttempt: [] };
  for (const e of events) {
    const p = e.payload ?? {};
    if (e.event_type === "model_call" && p.model) {
      const usd = Number(p.usd) || 0;
      const name = p.engine ? `${p.engine}:${p.model}` : String(p.model);
      out.usd += usd;
      out.underWay += usd;
      out.input += Number(p.input_tokens) || 0;
      out.output += Number(p.output_tokens) || 0;
      out.byModel[name] = (out.byModel[name] ?? 0) + usd;
    }
    if (e.event_type === "attempt_finished") {
      out.underWay = 0;
      out.byAttempt.push({ attempt: Number(p.attempt), candidate: String(p.candidate), usd: Number(p.spent_usd) || 0, decision: String(p.decision) });
    }
  }
  return out;
}

function tokens(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

// The task's cost, with the model breakdown on hover and each finished attempt's cost.
// total: what the task's runs have spent, as its Spend tab and its spend limit count it (the Task API's spend
// report), so the header, the tab and the path say one number. While a run is under way, what it has cost so far
// is shown beside it, named as that, since no report counts it until the run ends. Without total (the report
// not read yet), the figure is the live sum of the model calls.
export function SpendLine({ spend, turn, total, running }: { spend: TaskSpend; turn: number; total?: number; running?: boolean }) {
  const models = Object.entries(spend.byModel).sort((a, b) => b[1] - a[1]);
  return (
    <Stack gap={4}>
      <Group gap="xs">
        <Text size="sm">Turn {turn}</Text>
        <Tooltip
          label={models.length ? models.map(([m, usd]) => `${m}: ${money(usd)}`).join("\n") : "No model call yet"}
          style={{ whiteSpace: "pre" }}
          withArrow
        >
          <Text size="sm" fw={600} data-testid="task-spend">{money(total ?? spend.usd)}</Text>
        </Tooltip>
        {total !== undefined && running && spend.underWay > 0 && (
          <Text size="xs" c="dimmed" data-testid="task-spend-under-way">+ {money(spend.underWay)} in the run under way</Text>
        )}
        <Text size="xs" c="dimmed">{tokens(spend.input)} in · {tokens(spend.output)} out</Text>
      </Group>
      {spend.byAttempt.length > 1 && (
        <Group gap={6}>
          {spend.byAttempt.map((a) => (
            <Badge key={a.attempt} size="xs" variant="light" color={a.decision === "finished" ? "teal" : "gray"} tt="none">
              attempt {a.attempt}: ${a.usd.toFixed(2)}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  );
}

// Where a goal's spend ends up if the rest of its issues cost what the closed ones did on average.
export function projection(spent: number, done: number, total: number): number | null {
  if (done === 0 || total === 0) return null;
  return spent + (spent / done) * (total - done);
}
