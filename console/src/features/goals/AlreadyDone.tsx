import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Group, List, Paper, Text, Title, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconCircleCheck } from "@tabler/icons-react";
import { api, refLabel, type Task } from "../../api";
import type { GoalPlan } from "./api";

// How a task's reason starts when its run changed nothing because the base branch has the work
// (run_service/shared.py ALREADY_DONE_REASON).
export const ALREADY_DONE_REASON = "the work is already on the base branch: ";
const PLANNED = /^already done:\s*/i;
const SAME = /^same work as\b/i;

// One piece of work the goal asked for and did not build, with where it already is.
export interface DoneAlready {
  key: string;
  // What says so: the plan, before any task ran, or a task's check of the base branch.
  by: "plan" | "task";
  // The issue or task it was, to link to; empty for work a specification asked for that had no issue.
  ref: string;
  taskId: string;
  title: string;
  where: string;
}

// What the plan left out because the repository already had it, or because another issue asks for the same
// change, and the tasks that closed because their work was already on the base branch.
export function found(plan: GoalPlan["plan"] | null | undefined, tasks: Task[]): DoneAlready[] {
  const titles = new Map((plan?.issues ?? []).map((i) => [i.ref, i.title]));
  const out: DoneAlready[] = [];
  for (const item of plan?.items ?? []) {
    const reason = (item.reason ?? "").trim();
    if (item.decision !== "leave_out" || !(PLANNED.test(reason) || SAME.test(reason))) continue;
    out.push({ key: `item:${item.ref}`, by: "plan", ref: item.ref, taskId: "", title: titles.get(item.ref) ?? "", where: reason.replace(PLANNED, "") });
  }
  (plan?.gaps ?? []).forEach((gap, n) => {
    if (PLANNED.test(gap.trim())) out.push({ key: `gap:${n}`, by: "plan", ref: "", taskId: "", title: "", where: gap.trim().replace(PLANNED, "") });
  });
  for (const task of tasks) {
    const reason = task.status_reason ?? "";
    if (task.status !== "closed" || !reason.startsWith(ALREADY_DONE_REASON)) continue;
    out.push({ key: `task:${task.id}`, by: "task", ref: "", taskId: task.id, title: task.title ?? "", where: reason.slice(ALREADY_DONE_REASON.length) });
  }
  return out;
}

// On a goal's page: the work the goal asked for that needed no building, each with where it already is. So an
// operator sees that an issue was looked at and found done, and can check the place named, without opening the
// plan or each task. Nothing is shown for a goal with none.
export function AlreadyDone({ goalId, tasks, watch }: { goalId: string; tasks: Task[]; watch?: string }) {
  const [plan, setPlan] = useState<GoalPlan["plan"] | null>(null);
  const [opened, setOpened] = useState(false);

  useEffect(() => {
    let stale = false;
    // A goal with no plan answers with none, and one whose plan cannot be read still lists its tasks.
    api.goalPlan(goalId).then((read) => !stale && setPlan(read.plan ?? null), () => undefined);
    return () => {
      stale = true;
    };
  }, [goalId, watch]);

  const items = useMemo(() => found(plan, tasks), [plan, tasks]);
  if (!items.length) return null;
  const planned = items.filter((i) => i.by === "plan").length;
  const checked = items.length - planned;
  const count = `${items.length} ${items.length === 1 ? "piece" : "pieces"} of work ${items.length === 1 ? "was" : "were"} already done`;
  return (
    <Paper withBorder p="sm" mb="md" data-testid="already-done">
      <UnstyledButton onClick={() => setOpened((now) => !now)} aria-expanded={opened} aria-label="Work that was already done">
        <Group gap={6} wrap="nowrap">
          {opened ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          <IconCircleCheck size={16} color="var(--mantine-color-teal-6)" />
          <Title order={5}>{count}</Title>
          <Text size="sm" c="dimmed">
            Nothing was built for {items.length === 1 ? "it" : "them"}
            {planned > 0 && checked > 0 ? `: ${planned} found by the plan, ${checked} by a task's check.` : "."}
          </Text>
        </Group>
      </UnstyledButton>
      {opened && (
        <List size="sm" spacing="xs" mt="sm" data-testid="already-done-list">
          {items.map((item) => (
            <List.Item key={item.key}>
              {item.taskId ? (
                <Anchor component={Link} to={`/tasks/${item.taskId}`} size="sm">{item.title || item.taskId}</Anchor>
              ) : item.ref ? (
                <Text span size="sm" fw={500}>{item.title || refLabel(item.ref)} <Text span size="xs" c="dimmed" ff="monospace">{refLabel(item.ref)}</Text></Text>
              ) : (
                <Text span size="sm" fw={500}>Asked for by the goal</Text>
              )}
              <Text size="sm">{item.where}</Text>
              <Text size="xs" c="dimmed">
                {item.by === "plan" ? "Left out of the plan, so no task ran." : "Its run changed nothing, and a second model confirmed the work is on the base branch."}
              </Text>
            </List.Item>
          ))}
        </List>
      )}
    </Paper>
  );
}
