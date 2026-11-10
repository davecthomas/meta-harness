import { Anchor, Badge, Group, Paper, Progress, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { useNavigate } from "react-router-dom";
import { issueUrl, type IssueProgress, type Task } from "../../api";
import { PrLink, StatusBadge } from "../../components/ui";
import { projection } from "../tasks/Spend";

// The board's columns, in order, each with its color in the board and the graph.
export const COLUMNS = [
  { key: "queued", label: "Queued", color: "gray" },
  { key: "running", label: "Running", color: "blue" },
  { key: "review", label: "In review", color: "violet" },
  { key: "done", label: "Done", color: "teal" },
  { key: "attention", label: "Needs attention", color: "red" },
] as const;
export type Column = (typeof COLUMNS)[number]["key"];

const FAILED = new Set(["failed", "review_unresolved", "canceled"]);

// Which column an issue is in. byRef holds the goal's issues, to see whether a dependency failed.
export function columnOf(issue: IssueProgress, byRef: Map<string, IssueProgress>, seen = new Set<string>()): Column {
  const status = issue.task_status;
  if (issue.state === "closed" || status === "closed" || issue.pr_state === "merged") return "done";
  if (issue.refused_reason || (status && FAILED.has(status)) || issue.checks?.state === "failed") return "attention";
  // A running task held for budget waits on an operator.
  if (issue.task_waiting_on) return "attention";
  // A cycle stops here: the planner refuses those, so an issue already on the path adds nothing.
  seen.add(issue.ref);
  const blocked = (ref: string) => byRef.has(ref) && !seen.has(ref) && columnOf(byRef.get(ref)!, byRef, seen) === "attention";
  if (issue.depends_on.some(blocked)) return "attention";
  if (issue.pr_url && issue.pr_state !== "closed") return "review";
  if (status === "running" || status === "taken_over") return "running";
  return "queued";
}

// Each issue's column, and how far down the dependency order it is: 0 for an issue that waits on none of the goal's.
export function layout(issues: IssueProgress[]): { column: Map<string, Column>; depth: Map<string, number> } {
  const byRef = new Map(issues.map((i) => [i.ref, i]));
  const column = new Map(issues.map((i) => [i.ref, columnOf(i, byRef)]));
  const depth = new Map<string, number>();
  const visit = (ref: string, path: Set<string>): number => {
    if (depth.has(ref)) return depth.get(ref)!;
    if (path.has(ref)) return 0; // A cycle: the planner refuses those, so stop rather than loop.
    path.add(ref);
    const deps = (byRef.get(ref)?.depends_on ?? []).filter((d) => byRef.has(d));
    const d = deps.length ? 1 + Math.max(...deps.map((dep) => visit(dep, path))) : 0;
    path.delete(ref);
    depth.set(ref, d);
    return d;
  };
  issues.forEach((i) => visit(i.ref, new Set()));
  return { column, depth };
}

function shortRef(ref: string): string {
  return ref.replace(/^github:/, "");
}

function IssueCard(props: { issue: IssueProgress; task?: Task; usd?: number }) {
  const navigate = useNavigate();
  const { issue, task } = props;
  const url = issueUrl(issue.ref);
  const to = issue.task_id ? `/tasks/${issue.task_id}` : null;
  const checks = issue.checks?.state;
  return (
    <Paper
      withBorder
      p="xs"
      radius="sm"
      role={to ? "link" : undefined}
      tabIndex={to ? 0 : undefined}
      aria-label={to ? `Open task ${task?.title ?? issue.ref}` : undefined}
      onClick={to ? () => navigate(to) : undefined}
      onKeyDown={to ? (e) => e.key === "Enter" && navigate(to) : undefined}
      style={{ cursor: to ? "pointer" : "default" }}
    >
      <Stack gap={4}>
        <Text size="sm" fw={500} lineClamp={2}>{task?.title ?? shortRef(issue.ref)}</Text>
        <Group gap={6} onClick={(e) => e.stopPropagation()}>
          {url ? <Anchor href={url} target="_blank" size="xs" ff="monospace">{shortRef(issue.ref)}</Anchor> : <Text size="xs" ff="monospace">{issue.ref}</Text>}
        </Group>
        <Group gap={6}>
          {issue.task_status && <StatusBadge status={issue.task_status} />}
          {props.usd !== undefined && props.usd > 0 && <Text size="xs" c="dimmed">${props.usd.toFixed(2)}</Text>}
        </Group>
        {issue.pr_url && (
          <Group gap={6} onClick={(e) => e.stopPropagation()}>
            <PrLink url={issue.pr_url} size="xs" />
            {checks && (
              <Tooltip label={issue.checks?.failed.length ? `Failing: ${issue.checks.failed.join(", ")}` : `Checks ${checks}`}>
                <Badge size="xs" variant="light" color={checks === "passed" ? "teal" : checks === "failed" ? "red" : "yellow"}>checks {checks}</Badge>
              </Tooltip>
            )}
          </Group>
        )}
        {issue.refused_reason && <Text size="xs" c="red">{issue.refused_reason}</Text>}
      </Stack>
    </Paper>
  );
}

// The goal's progress: its issues done of all, and what its tasks spent against its budget.
export function GoalProgress(props: { issues: IssueProgress[]; spend: Record<string, number>; budget: string | null }) {
  const { column } = layout(props.issues);
  const done = props.issues.filter((i) => column.get(i.ref) === "done").length;
  const spent = Object.values(props.spend).reduce((a, b) => a + b, 0);
  const budget = props.budget ? Number(props.budget) : null;
  const projected = projection(spent, done, props.issues.length);
  return (
    <Group gap="xl" mb="md" data-testid="goal-progress">
      <Stack gap={2} w={220}>
        <Text size="xs" c="dimmed">{done} of {props.issues.length} issues done</Text>
        <Progress value={(100 * done) / props.issues.length} color="teal" />
      </Stack>
      <Stack gap={2} w={220}>
        <Text size="xs" c="dimmed">
          ${spent.toFixed(2)} spent{budget ? ` of $${budget.toFixed(2)}` : ""}
          {projected !== null && done < props.issues.length && ` · about $${projected.toFixed(2)} to finish`}
        </Text>
        {budget !== null && (
          <Progress value={Math.min(100, (100 * spent) / budget)} color={spent >= budget ? "red" : spent >= 0.8 * budget ? "yellow" : "blue"} />
        )}
      </Stack>
    </Group>
  );
}

export function GoalBoard(props: { issues: IssueProgress[]; tasks: Record<string, Task>; spend: Record<string, number> }) {
  const { column } = layout(props.issues);
  return (
    <SimpleGrid cols={{ base: 1, sm: 3, lg: 5 }} spacing="sm" mb="lg">
      {COLUMNS.map((c) => {
        const cards = props.issues.filter((i) => column.get(i.ref) === c.key);
        return (
          <Stack key={c.key} gap="xs" data-testid={`column-${c.key}`}>
            <Group gap={6}>
              <Badge color={c.color} variant="light">{c.label}</Badge>
              <Text size="xs" c="dimmed">{cards.length}</Text>
            </Group>
            {cards.map((i) => (
              <IssueCard key={i.ref} issue={i} task={i.task_id ? props.tasks[i.task_id] : undefined} usd={i.task_id ? props.spend[i.task_id] : undefined} />
            ))}
          </Stack>
        );
      })}
    </SimpleGrid>
  );
}

const NODE_W = 180;
const NODE_H = 44;
const GAP_X = 60;
const GAP_Y = 16;
const COLORS: Record<Column, string> = {
  queued: "var(--mantine-color-gray-5)",
  running: "var(--mantine-color-blue-6)",
  review: "var(--mantine-color-violet-6)",
  done: "var(--mantine-color-teal-6)",
  attention: "var(--mantine-color-red-6)",
};

// The goal's issues by dependency: each level waits on the one to its left, and each node is colored by its column.
export function GoalGraph(props: { issues: IssueProgress[]; tasks: Record<string, Task> }) {
  const navigate = useNavigate();
  const { column, depth } = layout(props.issues);
  const levels: IssueProgress[][] = [];
  props.issues.forEach((i) => (levels[depth.get(i.ref)!] ??= []).push(i));
  const at = new Map<string, { x: number; y: number }>();
  levels.forEach((level, x) => level.forEach((i, y) => at.set(i.ref, { x: x * (NODE_W + GAP_X), y: y * (NODE_H + GAP_Y) })));
  const width = Math.max(levels.length, 1) * (NODE_W + GAP_X) - GAP_X;
  const height = Math.max(...levels.map((l) => l.length), 1) * (NODE_H + GAP_Y) - GAP_Y;
  return (
    <div style={{ overflowX: "auto", marginBottom: "var(--mantine-spacing-lg)" }}>
      <svg width={width} height={height} role="img" aria-label="The goal's issues by dependency">
        {props.issues.flatMap((i) =>
          i.depends_on
            .filter((d) => at.has(d))
            .map((d) => {
              const from = at.get(d)!;
              const to = at.get(i.ref)!;
              return (
                <line key={`${d}->${i.ref}`} x1={from.x + NODE_W} y1={from.y + NODE_H / 2} x2={to.x} y2={to.y + NODE_H / 2}
                  stroke="var(--mantine-color-dimmed)" strokeWidth={1.5} />
              );
            }),
        )}
        {props.issues.map((i) => {
          const p = at.get(i.ref)!;
          const task = i.task_id ? props.tasks[i.task_id] : undefined;
          return (
            <g key={i.ref} transform={`translate(${p.x},${p.y})`} style={{ cursor: i.task_id ? "pointer" : "default" }}
              onClick={() => i.task_id && navigate(`/tasks/${i.task_id}`)} data-testid={`node-${i.ref}`} data-column={column.get(i.ref)}>
              <rect width={NODE_W} height={NODE_H} rx={6} fill="var(--mantine-color-body)" stroke={COLORS[column.get(i.ref)!]} strokeWidth={2} />
              <text x={8} y={18} fontSize={12} fill="var(--mantine-color-text)">{(task?.title ?? shortRef(i.ref)).slice(0, 26)}</text>
              <text x={8} y={34} fontSize={11} fill="var(--mantine-color-dimmed)">{shortRef(i.ref)}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
