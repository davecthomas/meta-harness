import type { ReactNode } from "react";
import { Anchor, Badge, Group, Paper, Progress, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { useNavigate } from "react-router-dom";
import { issueUrl, type Goal, type IssueProgress, type Task } from "../../api";
import { budgetColor, budgetStanding, budgetWords } from "./budget";
import { PrLink, StatusBadge } from "../../components/ui";
import { projection } from "../tasks/Spend";
import { moneyText } from "../../money";
import { SizeBadge, gapText } from "./sizing";

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
  // Held by the planner for its size or its gaps: it waits for Run anyway or Split.
  if (issue.held_reason && !issue.task_id) return "attention";
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

function IssueCard(props: { issue: IssueProgress; task?: Task; usd?: number; actions?: ReactNode }) {
  const navigate = useNavigate();
  const { issue, task } = props;
  const url = issueUrl(issue.ref);
  const to = issue.task_id ? `/tasks/${issue.task_id}` : null;
  const checks = issue.checks?.state;
  const held = issue.held_reason && !issue.task_id ? issue.held_reason : null;
  // A gap a held issue's reason already names is not said twice.
  const gap = held ? null : gapText(issue.missing);
  return (
    <Paper
      withBorder
      p="xs"
      radius="sm"
      role={to ? "link" : undefined}
      tabIndex={to ? 0 : undefined}
      aria-label={to ? `Open task ${task?.title ?? issue.title ?? issue.ref}` : undefined}
      onClick={to ? () => navigate(to) : undefined}
      onKeyDown={to ? (e) => e.key === "Enter" && navigate(to) : undefined}
      style={{ cursor: to ? "pointer" : "default" }}
    >
      <Stack gap={4}>
        <Text size="sm" fw={500} lineClamp={2}>{task?.title ?? issue.title ?? shortRef(issue.ref)}</Text>
        <Group gap={6} onClick={(e) => e.stopPropagation()}>
          {url ? <Anchor href={url} target="_blank" size="xs" ff="monospace">{shortRef(issue.ref)}</Anchor> : <Text size="xs" ff="monospace">{issue.ref}</Text>}
        </Group>
        <Group gap={6}>
          {issue.task_status && <StatusBadge status={issue.task_status} />}
          <SizeBadge issue={issue} />
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
        {held && <Text size="xs" c="orange" data-testid="held-reason">Held: {held}</Text>}
        {gap && <Text size="xs" c="yellow.8" data-testid="issue-gap">{gap}</Text>}
        {props.actions}
        {/* Why a queued task has not started, from the scheduler. */}
        {task?.status === "queued" && task.held && <Text size="xs" c="dimmed" lineClamp={3}>{moneyText(task.held.reason)}</Text>}
      </Stack>
    </Paper>
  );
}

// The goal's progress: its issues done of all, and what its tasks spent against its budget.
// goal: the goal, for how it stands against its budget: past it, the spend line says so and the bar changes color.
export function GoalProgress(props: { issues: IssueProgress[]; spend: Record<string, number>; budget: string | null; onSpend?: () => void; goal?: Goal }) {
  const { column } = layout(props.issues);
  const done = props.issues.filter((i) => column.get(i.ref) === "done").length;
  const spent = Object.values(props.spend).reduce((a, b) => a + b, 0);
  const budget = props.budget ? Number(props.budget) : null;
  const projected = projection(spent, done, props.issues.length);
  const standing = props.goal ? budgetStanding(props.goal, spent) : "ok";
  const words = props.goal ? budgetWords(props.goal, spent) : null;
  return (
    <Group gap="xl" mb="md" data-testid="goal-progress">
      <Stack gap={2} w={220}>
        <Text size="xs" c="dimmed">{done} of {props.issues.length} issues done</Text>
        <Progress value={(100 * done) / props.issues.length} color="teal" />
      </Stack>
      <Stack gap={2} w={220}>
        <Text size="xs" c="dimmed">
          ${spent.toFixed(2)} spent{budget ? ` of $${budget.toFixed(2)}` : ""}{words && `: ${words}`}
          {projected !== null && done < props.issues.length && ` · about $${projected.toFixed(2)} to finish`}
          {props.onSpend && spent > 0 && (
            <>
              {" · "}
              <Anchor component="button" type="button" size="xs" onClick={props.onSpend}>where it went</Anchor>
            </>
          )}
        </Text>
        {budget !== null && (
          <Progress value={Math.min(100, (100 * spent) / budget)} color={props.goal ? (standing !== "ok" ? budgetColor(standing) : spent >= 0.8 * budget ? "yellow" : "blue") : spent >= budget ? "red" : spent >= 0.8 * budget ? "yellow" : "blue"} />
        )}
      </Stack>
    </Group>
  );
}

// actions: what a card offers beyond opening its task, such as Run anyway and Split on an issue the planner held.
export function GoalBoard(props: {
  issues: IssueProgress[];
  tasks: Record<string, Task>;
  spend: Record<string, number>;
  actions?: (issue: IssueProgress) => ReactNode;
}) {
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
              <IssueCard
                key={i.ref}
                issue={i}
                task={i.task_id ? props.tasks[i.task_id] : undefined}
                usd={i.task_id ? props.spend[i.task_id] : undefined}
                actions={props.actions?.(i)}
              />
            ))}
          </Stack>
        );
      })}
    </SimpleGrid>
  );
}

const NODE_W = 260;
const NODE_H = 58;
const GAP_X = 72;
const GAP_Y = 16;
// The graph uses color for two things only: work that is done, and work that needs the operator. Everything
// else is neutral and says its state in words, so the picture reads as an order of work and not as a palette.
const NEUTRAL = "var(--mantine-color-default-border)";
const COLORS: Record<Column, string> = {
  queued: NEUTRAL,
  running: NEUTRAL,
  review: NEUTRAL,
  done: "var(--mantine-color-teal-6)",
  attention: "var(--mantine-color-red-6)",
};

// A node's second line: where the issue stands, and what it waits for.
export function standing(issue: IssueProgress, column: Column, byRef: Map<string, IssueProgress>): string {
  if (column === "done") return "Done";
  if (column === "running") return "Running now";
  if (column === "review") return issue.pr_url ? `In review: pull request #${issue.pr_url.split("/").pop()}` : "In review";
  const open = issue.depends_on.filter((d) => byRef.has(d) && byRef.get(d)!.state !== "closed");
  if (column === "attention") {
    if (issue.refused_reason) return "Needs you: not started, see why above";
    if (issue.task_waiting_on) return "Needs you: paused for budget";
    if (issue.task_status === "failed") return "Needs you: failed, retry it";
    if (issue.task_status === "review_unresolved") return "Needs you: review unresolved";
    if (issue.task_status === "canceled") return "Canceled";
    if (issue.checks?.state === "failed") return "Needs you: checks failed";
    return "Stuck behind an issue that needs you";
  }
  return open.length ? `Waits on ${open.length} ${open.length === 1 ? "issue" : "issues"} to its left` : "Queued: starts when a place is free";
}

// The goal's issues in the order they can be worked: an issue sits to the right of every issue it waits on,
// with an arrow from each of those. It answers what blocks what, and so what to fix first. A goal whose
// issues do not depend on each other has no order to draw, and says so.
export function GoalGraph(props: { issues: IssueProgress[]; tasks: Record<string, Task> }) {
  const navigate = useNavigate();
  const { column, depth } = layout(props.issues);
  const byRef = new Map(props.issues.map((i) => [i.ref, i]));
  const linked = props.issues.some((i) => i.depends_on.some((d) => byRef.has(d)));
  if (!linked) {
    return (
      <Paper withBorder p="md" mb="lg" data-testid="graph-no-order">
        <Text size="sm" fw={600}>No issue in this goal waits on another</Text>
        <Text size="sm" c="dimmed">
          The graph draws the order of work: which issue must finish before another can start. These issues are
          independent, so they can run in any order and there is nothing to draw. The Board shows where each one
          stands. To make one wait on another, add a line "Depends on: #12" to its issue.
        </Text>
      </Paper>
    );
  }
  const levels: IssueProgress[][] = [];
  props.issues.forEach((i) => (levels[depth.get(i.ref)!] ??= []).push(i));
  const at = new Map<string, { x: number; y: number }>();
  levels.forEach((level, x) => level.forEach((i, y) => at.set(i.ref, { x: x * (NODE_W + GAP_X), y: y * (NODE_H + GAP_Y) })));
  const width = Math.max(levels.length, 1) * (NODE_W + GAP_X) - GAP_X;
  const height = Math.max(...levels.map((l) => l.length), 1) * (NODE_H + GAP_Y) - GAP_Y;
  return (
    <Stack gap="xs" mb="lg">
      <Group gap="lg" data-testid="graph-legend">
        <Text size="xs" c="dimmed">Work runs left to right: an arrow points from an issue to one that waits on it. Click an issue to open its task.</Text>
        <Group gap={6}><Badge size="xs" color="red" variant="outline">red</Badge><Text size="xs" c="dimmed">needs you, or is stuck behind one that does</Text></Group>
        <Group gap={6}><Badge size="xs" color="teal" variant="outline">green</Badge><Text size="xs" c="dimmed">done</Text></Group>
      </Group>
      <div style={{ overflowX: "auto" }}>
        <svg width={width} height={height} role="img" aria-label="The goal's issues in the order they can be worked">
          <defs>
            <marker id="goal-graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--mantine-color-dimmed)" />
            </marker>
          </defs>
          {props.issues.flatMap((i) =>
            i.depends_on
              .filter((d) => at.has(d))
              .map((d) => {
                const from = at.get(d)!;
                const to = at.get(i.ref)!;
                return (
                  <line key={`${d}->${i.ref}`} x1={from.x + NODE_W} y1={from.y + NODE_H / 2} x2={to.x - 2} y2={to.y + NODE_H / 2}
                    stroke="var(--mantine-color-dimmed)" strokeWidth={1.5} markerEnd="url(#goal-graph-arrow)" />
                );
              }),
          )}
          {props.issues.map((i) => {
            const p = at.get(i.ref)!;
            const task = i.task_id ? props.tasks[i.task_id] : undefined;
            const where = column.get(i.ref)!;
            const title = task?.title ?? i.title ?? shortRef(i.ref);
            return (
              <g key={i.ref} transform={`translate(${p.x},${p.y})`} style={{ cursor: i.task_id ? "pointer" : "default" }}
                onClick={() => i.task_id && navigate(`/tasks/${i.task_id}`)} data-testid={`node-${i.ref}`} data-column={where}>
                <title>{`${title} (${shortRef(i.ref)}): ${standing(i, where, byRef)}`}</title>
                <rect width={NODE_W} height={NODE_H} rx={6} fill="var(--mantine-color-body)" stroke={COLORS[where]} strokeWidth={where === "queued" || where === "running" || where === "review" ? 1 : 2} />
                <text x={10} y={19} fontSize={12} fontWeight={600} fill="var(--mantine-color-text)">{title.length > 38 ? `${title.slice(0, 37)}…` : title}</text>
                <text x={10} y={35} fontSize={11} fill={where === "attention" ? "var(--mantine-color-red-5)" : "var(--mantine-color-text)"}>{standing(i, where, byRef)}</text>
                <text x={10} y={50} fontSize={10} fill="var(--mantine-color-dimmed)">{shortRef(i.ref).split("/").pop()}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </Stack>
  );
}
