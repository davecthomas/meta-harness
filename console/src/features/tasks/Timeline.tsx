import { useEffect, useRef, useState } from "react";
import { Badge, Code, Collapse, Group, Paper, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconFile, IconRobot, IconSettings } from "@tabler/icons-react";
import type { MhEvent } from "../../api";

// One entry on the timeline: a turn with what happened in it, or an event between turns.
export type Entry =
  | { kind: "turn"; turn: number; position: number; text: string; models: MhEvent[]; tools: Tool[]; files: string[]; compacted: boolean; planned: boolean }
  | { kind: "status"; position: number; text: string; error: boolean }
  | { kind: "prompt"; position: number; template: string; prompt: string; runKind: string };

export interface Tool {
  id: string;
  tool: string;
  args: unknown;
  output: unknown;
  ok: boolean | null;
  duration_ms: number | null;
}

function short(value: unknown, n: number): string {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? "");
  return text.length > n ? `${text.slice(0, n)}…` : text;
}

// A tool call's arguments in one line: the path or command when it has one.
export function summarize(args: unknown): string {
  if (args && typeof args === "object") {
    const a = args as Record<string, unknown>;
    for (const key of ["command", "path", "pattern", "query"]) if (typeof a[key] === "string") return short(a[key], 120);
  }
  return short(args, 120);
}

// The task's events as turns, in order. Events of a turn are the ones between its turn_started and the next.
export function entries(events: MhEvent[]): Entry[] {
  const out: Entry[] = [];
  let turn: Extract<Entry, { kind: "turn" }> | null = null;
  const tools = new Map<string, Tool>();
  for (const e of [...events].sort((a, b) => a.position - b.position)) {
    const p = e.payload ?? {};
    switch (e.event_type) {
      case "run_prompt":
        // Recorded before its run starts, so it heads that run's turns.
        out.push({ kind: "prompt", position: e.position, template: String(p.template ?? ""), prompt: String(p.prompt ?? ""), runKind: String(p.kind ?? "") });
        turn = null;
        break;
      case "turn_started":
        turn = { kind: "turn", turn: Number(p.turn) || 0, position: e.position, text: "", models: [], tools: [], files: [], compacted: false, planned: false };
        out.push(turn);
        break;
      case "model_call":
        turn?.models.push(e);
        break;
      case "tool_call": {
        const tool: Tool = { id: String(p.tool_call_id ?? e.position), tool: String(p.tool ?? "?"), args: p.args, output: null, ok: null, duration_ms: null };
        tools.set(tool.id, tool);
        turn?.tools.push(tool);
        break;
      }
      case "tool_result": {
        const tool = tools.get(String(p.tool_call_id));
        if (tool) Object.assign(tool, { output: p.output, ok: !p.is_error, duration_ms: p.duration_ms ?? null });
        break;
      }
      case "file_changed":
        if (turn && !turn.files.includes(String(p.path))) turn.files.push(String(p.path));
        break;
      case "compacted":
        if (turn) turn.compacted = true;
        break;
      case "plan_updated":
        if (turn) turn.planned = true;
        break;
      case "turn_finished":
        if (turn && p.text) turn.text = String(p.text);
        break;
      case "task_routed":
        out.push({ kind: "status", position: e.position, text: `Routed to tier ${p.tier}: ${(p.candidates ?? []).join(", ")}`, error: false });
        break;
      case "attempt_finished":
        out.push({ kind: "status", position: e.position, text: `Attempt ${p.attempt} on ${p.candidate}: ${p.decision}, $${p.spent_usd}${p.detail ? ` (${p.detail})` : ""}`, error: p.decision !== "finished" });
        turn = null;
        break;
      case "run_finished":
        out.push({ kind: "status", position: e.position, text: `Run ${p.status}${p.end_reason ? ` (${p.end_reason})` : ""}${p.error ? `: ${p.error}` : ""}`, error: p.status !== "FINISHED" });
        turn = null;
        break;
      case "pull_request_opened":
        out.push({ kind: "status", position: e.position, text: `Pull request opened: ${p.pr_url}`, error: false });
        break;
      case "operator_message":
        out.push({ kind: "status", position: e.position, text: `${e.actor ?? "Operator"}: ${p.when === "stop" ? "stop after this turn" : `${p.text} (${p.when === "after_stop" ? "after it stops" : "next turn"})`}`, error: false });
        break;
      case "task_held":
        out.push({ kind: "status", position: e.position, text: `Held by the scheduler: ${p.reason}`, error: false });
        break;
      case "task_admitted":
        out.push({ kind: "status", position: e.position, text: "Admitted by the scheduler: its run starts", error: false });
        break;
      case "task_status_changed":
        out.push({ kind: "status", position: e.position, text: `Status ${p.from} → ${p.to}${p.reason ? `: ${p.reason}` : ""}`, error: p.to === "failed" });
        break;
    }
  }
  return out;
}

function ToolRow({ tool }: { tool: Tool }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <UnstyledButton onClick={() => setOpen((o) => !o)} w="100%" aria-expanded={open}>
        <Group gap={6} wrap="nowrap">
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Code>{tool.tool}</Code>
          <Text size="xs" ff="monospace" c="dimmed" truncate style={{ flex: 1 }}>{summarize(tool.args)}</Text>
          {tool.duration_ms !== null && <Text size="xs" c="dimmed">{Math.round(tool.duration_ms)}ms</Text>}
          {tool.ok !== null && <Badge size="xs" variant="light" color={tool.ok ? "teal" : "red"}>{tool.ok ? "ok" : "error"}</Badge>}
        </Group>
      </UnstyledButton>
      <Collapse expanded={open}>
        <Stack gap={4} mt={4} ml={20}>
          <Code block>{typeof tool.args === "string" ? tool.args : JSON.stringify(tool.args, null, 2)}</Code>
          {tool.output !== null && <Code block>{typeof tool.output === "string" ? tool.output : JSON.stringify(tool.output, null, 2)}</Code>}
        </Stack>
      </Collapse>
    </div>
  );
}

// A run's prompt, collapsed to its template's name, opening to the full text the run was given.
function PromptRow({ entry }: { entry: Extract<Entry, { kind: "prompt" }> }) {
  const [open, setOpen] = useState(false);
  return (
    <Paper p="sm" withBorder data-testid="prompt">
      <UnstyledButton onClick={() => setOpen((o) => !o)} w="100%" aria-expanded={open}>
        <Group gap={6} wrap="nowrap">
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Text size="xs" fw={600} c="dimmed">Prompt</Text>
          <Code>{entry.template}</Code>
          {entry.runKind && entry.runKind !== "task" && <Badge size="xs" variant="light">{entry.runKind} run</Badge>}
        </Group>
      </UnstyledButton>
      <Collapse expanded={open}>
        <Code block mt={6} style={{ whiteSpace: "pre-wrap" }}>{entry.prompt}</Code>
      </Collapse>
    </Paper>
  );
}

function ModelLine({ e }: { e: MhEvent }) {
  const p = e.payload ?? {};
  if (!p.model) return <Text size="xs" c="red">Model call failed after {p.latency_ms ?? "?"}ms: {short(p.error ?? p.error_code, 200)}</Text>;
  return (
    <Group gap={8}>
      <IconRobot size={14} />
      <Text size="xs" ff="monospace">{p.engine ? `${p.engine}:${p.model}` : p.model}</Text>
      <Text size="xs" c="dimmed">{p.input_tokens} in · {p.output_tokens} out · ${Number(p.usd ?? 0).toFixed(4)} · {p.latency_ms}ms</Text>
    </Group>
  );
}

// A task's run as a timeline of turns: the agent's message, its model calls, its tool calls (each opening to
// its arguments and output), and the files it changed. It stays on the newest entry unless scrolled up.
export function Timeline({ events }: { events: MhEvent[] }) {
  const list = entries(events);
  const box = useRef<HTMLDivElement | null>(null);
  const pinned = useRef(true);
  useEffect(() => {
    if (pinned.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [list.length, events.length]);
  return (
    <div
      ref={box}
      data-testid="timeline"
      style={{ maxHeight: "70vh", overflowY: "auto" }}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      <Stack gap="sm">
        {list.length === 0 && <Text size="sm" c="dimmed">Nothing recorded yet.</Text>}
        {list.map((entry) =>
          entry.kind === "prompt" ? (
            <PromptRow key={entry.position} entry={entry} />
          ) : entry.kind === "status" ? (
            <Group key={entry.position} gap={6}>
              <IconSettings size={14} />
              <Text size="xs" c={entry.error ? "red" : "dimmed"}>{entry.text}</Text>
            </Group>
          ) : (
            <Paper key={entry.position} p="sm" withBorder data-testid={`turn-${entry.turn}`}>
              <Stack gap={6}>
                <Group justify="space-between">
                  <Text size="xs" fw={600} c="dimmed">Turn {entry.turn}</Text>
                  <Group gap={4}>
                    {entry.planned && <Badge size="xs" variant="light" color="grape">plan updated</Badge>}
                    {entry.compacted && <Badge size="xs" variant="light">context compacted</Badge>}
                  </Group>
                </Group>
                {entry.text && <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{entry.text}</Text>}
                {entry.models.map((m) => <ModelLine key={m.position} e={m} />)}
                {entry.tools.map((t) => <ToolRow key={t.id} tool={t} />)}
                {entry.files.map((f) => (
                  <Group key={f} gap={6}>
                    <IconFile size={14} />
                    <Text size="xs" ff="monospace">{f}</Text>
                  </Group>
                ))}
              </Stack>
            </Paper>
          ),
        )}
      </Stack>
    </div>
  );
}
