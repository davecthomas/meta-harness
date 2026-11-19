import { Alert, Anchor, Badge, Card, Code, Group, List, MultiSelect, Paper, SegmentedControl, Stack, Text, TextInput } from "@mantine/core";
import { IconArrowRight, IconBulb } from "@tabler/icons-react";
import type { PlanDecision, PlanDraft, PlanItem } from "../../api";
import { issueUrl, refLabel } from "../trackers/refs";

// The parts of a goal's plan that are about the whole of it: what the planner says of it, the order its tasks
// can be worked in, and its decisions on the issues the goal already had. GoalPlan puts them on the page with
// the plan's new tasks and its actions.

export type PlanIssue = { ref: string; repo: string; title: string };

const DECISIONS: { value: PlanDecision; label: string }[] = [
  { value: "keep", label: "Run as it is" },
  { value: "split", label: "Split" },
  { value: "leave_out", label: "Leave out" },
];

// What the planner says of its plan, set apart at the top: how it reaches the goal, and what it does not cover.
export function PlannerSays({ summary, gaps }: { summary: string; gaps: string[] }) {
  if (!summary && !gaps.length) return null;
  return (
    <Paper withBorder p="md" data-testid="planner-says" style={{ borderLeft: "4px solid var(--mantine-color-indigo-6)" }}>
      <Group gap="xs" mb={6}>
        <IconBulb size={18} color="var(--mantine-color-indigo-5)" />
        <Text fw={650}>From the planner</Text>
      </Group>
      {summary && <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{summary}</Text>}
      {gaps.length > 0 && (
        <Alert color="yellow" variant="light" mt="sm" p="xs" title={gaps.length === 1 ? "One thing to know before you approve" : `${gaps.length} things to know before you approve`}>
          <List size="sm" spacing={2}>{gaps.map((gap) => <List.Item key={gap}>{gap}</List.Item>)}</List>
        </Alert>
      )}
    </Paper>
  );
}

// One task the plan runs: an issue it keeps, or a new task it wrote.
export interface PlanTask {
  // The name other tasks wait on it by: an issue's ref, or a new task's draft_id.
  key: string;
  title: string;
  kind: "issue" | "new";
  depends_on: string[];
}

// The tasks an approved plan would run, each after the ones it waits on, otherwise as given: the issues it
// keeps and its new tasks. A split issue and one left out run nothing, so they are not here.
export function tasksOf(drafts: PlanDraft[], items: PlanItem[], issues: PlanIssue[]): PlanTask[] {
  const titles = new Map(issues.map((i) => [i.ref, i.title]));
  const all: PlanTask[] = [
    ...items.filter((i) => i.decision === "keep").map((i) => ({ key: i.ref, title: titles.get(i.ref) ?? i.ref, kind: "issue" as const, depends_on: i.depends_on })),
    ...drafts.map((d) => ({ key: d.draft_id, title: d.title, kind: "new" as const, depends_on: d.depends_on })),
  ];
  const byKey = new Map(all.map((t) => [t.key, t]));
  const placed: PlanTask[] = [];
  const seen = new Set<string>();
  const place = (task: PlanTask) => {
    if (seen.has(task.key)) return;
    seen.add(task.key);
    for (const dep of task.depends_on) if (byKey.has(dep)) place(byKey.get(dep)!);
    placed.push(task);
  };
  all.forEach(place);
  return placed;
}

// A task's name as people read it: an issue by its key, a new task by its id.
export const taskName = (task: { key: string; kind: "issue" | "new" }) => (task.kind === "issue" ? refLabel(task.key) : task.key);

// The order of work: each task with what it waits on, and how many can start at once.
export function OrderOfWork({ tasks }: { tasks: PlanTask[] }) {
  const keys = new Set(tasks.map((t) => t.key));
  const kinds = new Map(tasks.map((t) => [t.key, t.kind]));
  const first = tasks.filter((t) => !t.depends_on.some((dep) => keys.has(dep))).length;
  return (
    <Stack gap={4} data-testid="plan-order">
      <Text size="sm" c="dimmed">
        {tasks.length} {tasks.length === 1 ? "task" : "tasks"}. {first === tasks.length ? "None waits on another." : `${first} can start at once; the rest wait on the tasks before their arrows.`}
      </Text>
      {tasks.map((task, i) => {
        const after = task.depends_on.filter((dep) => keys.has(dep));
        return (
          <Group key={task.key} gap={6} wrap="nowrap">
            <Text size="xs" c="dimmed" w={18}>{i + 1}.</Text>
            {after.map((dep) => (
              <Group key={dep} gap={4} wrap="nowrap">
                <Code>{taskName({ key: dep, kind: kinds.get(dep) ?? "new" })}</Code>
                <IconArrowRight size={12} />
              </Group>
            ))}
            <Code fw={700}>{taskName(task)}</Code>
            <Text size="xs" lineClamp={1}>{task.title}</Text>
            {task.kind === "new" && <Badge size="xs" variant="light" color="indigo" tt="none">new</Badge>}
          </Group>
        );
      })}
    </Stack>
  );
}

// The plan's decision on each issue the goal already had: run it as it is, split it into new tasks, or leave it
// out, with the planner's reason. onChange: an operator changes one; absent, the decisions are only read.
export function IssueDecisions(props: {
  issues: PlanIssue[];
  items: PlanItem[];
  drafts: PlanDraft[];
  onChange?: (items: PlanItem[]) => void;
}) {
  const { issues, items, drafts, onChange } = props;
  if (!issues.length) return null;
  const byRef = new Map(items.map((i) => [i.ref, i]));
  const set = (ref: string, change: Partial<PlanItem>) => onChange?.(items.map((i) => (i.ref === ref ? { ...i, ...change } : i)));
  const newTasks = drafts.map((d) => ({ value: d.draft_id, label: `${d.draft_id}: ${d.title}` }));
  return (
    <Stack gap="xs" data-testid="plan-issues">
      {issues.map((issue) => {
        const item = byRef.get(issue.ref) ?? { ref: issue.ref, decision: "keep" as const, reason: "", depends_on: [], replaced_by: [] };
        const url = issueUrl(issue.ref);
        // What it may wait on: the other issues the plan keeps, and the new tasks.
        const others = [
          ...items.filter((i) => i.ref !== issue.ref && i.decision === "keep").map((i) => ({ value: i.ref, label: refLabel(i.ref) })),
          ...newTasks,
        ];
        return (
          <Card key={issue.ref} withBorder padding="sm" data-testid={`plan-issue-${issue.ref}`} opacity={item.decision === "leave_out" ? 0.7 : 1}>
            <Stack gap="xs">
              <Group justify="space-between" wrap="wrap" gap="xs">
                <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                  {url ? <Anchor href={url} target="_blank" size="sm" ff="monospace">{refLabel(issue.ref)}</Anchor> : <Text size="sm" ff="monospace">{refLabel(issue.ref)}</Text>}
                  <Text size="sm" fw={600} lineClamp={1}>{issue.title}</Text>
                </Group>
                {onChange ? (
                  <SegmentedControl
                    size="xs"
                    aria-label={`What the plan does with ${refLabel(issue.ref)}`}
                    value={item.decision}
                    data={DECISIONS}
                    onChange={(decision) => set(issue.ref, { decision: decision as PlanDecision })}
                  />
                ) : (
                  <Badge variant="light" tt="none" color={item.decision === "keep" ? "teal" : item.decision === "split" ? "indigo" : "gray"}>
                    {DECISIONS.find((d) => d.value === item.decision)?.label ?? item.decision}
                  </Badge>
                )}
              </Group>
              {onChange ? (
                <TextInput size="xs" aria-label={`Why, for ${refLabel(issue.ref)}`} placeholder="Why" value={item.reason} onChange={(e) => set(issue.ref, { reason: e.currentTarget.value })} />
              ) : (
                item.reason && <Text size="sm" c="dimmed">{item.reason}</Text>
              )}
              {item.decision === "split" && (
                <MultiSelect
                  size="xs"
                  label="Replaced by these new tasks"
                  aria-label={`New tasks that replace ${refLabel(issue.ref)}`}
                  placeholder={newTasks.length ? "Pick the new tasks" : "Add the new tasks below first"}
                  data={newTasks}
                  value={item.replaced_by}
                  readOnly={!onChange}
                  onChange={(replaced_by) => set(issue.ref, { replaced_by })}
                />
              )}
              {item.decision === "keep" && (onChange || item.depends_on.length > 0) && (
                <MultiSelect
                  size="xs"
                  label="Waits on"
                  aria-label={`What ${refLabel(issue.ref)} waits on`}
                  placeholder="Nothing: it can start at once"
                  data={others}
                  value={item.depends_on}
                  readOnly={!onChange}
                  onChange={(depends_on) => set(issue.ref, { depends_on })}
                />
              )}
            </Stack>
          </Card>
        );
      })}
    </Stack>
  );
}

// What approving the plan would do, in numbers.
export function planCounts(drafts: PlanDraft[], items: PlanItem[]): { kept: number; split: number; out: number; fresh: number } {
  const count = (decision: PlanDecision) => items.filter((i) => i.decision === decision).length;
  return { kept: count("keep"), split: count("split"), out: count("leave_out"), fresh: drafts.length };
}

const many = (n: number, one: string, more: string) => `${n} ${n === 1 ? one : more}`;

// The same, in a sentence for the approval: "Runs 3 of the goal's issues as they are, replaces 1 with new tasks, leaves 1 out, and adds 2 new tasks."
export function planSentence(drafts: PlanDraft[], items: PlanItem[]): string {
  const { kept, split, out, fresh } = planCounts(drafts, items);
  const parts = [];
  if (kept) parts.push(`runs ${many(kept, "issue", "issues")} of the goal's as ${kept === 1 ? "it is" : "they are"}`);
  if (split) parts.push(`replaces ${many(split, "issue", "issues")} with new tasks`);
  if (out) parts.push(`leaves ${many(out, "issue", "issues")} out`);
  if (fresh) parts.push(`adds ${many(fresh, "new task", "new tasks")}`);
  if (!parts.length) return "Approving this plan runs nothing.";
  const text = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0];
  return `Approving this plan ${text}.`;
}
