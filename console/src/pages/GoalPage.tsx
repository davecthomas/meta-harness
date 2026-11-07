import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Anchor, Badge, Button, Center, Group, Loader, Stack, Table, Text } from "@mantine/core";
import { IconDownload, IconPlayerPause, IconPlayerPlay, IconX } from "@tabler/icons-react";
import { api, type Goal, type IssueLink as Issue, type Task } from "../api";
import { useInfinite } from "../hooks";
import { DataTable, Empty, ErrorAlert, Facts, IssueLink, PageHeader, SectionTitle, StatusBadge, when } from "../components/ui";

export function GoalPage() {
  const { goalId = "" } = useParams();
  const [goal, setGoal] = useState<(Goal & { issues: Issue[] }) | null>(null);
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const g = await api.goal(goalId);
      setGoal(g);
      const page = await api.tasks({ goalId }, 0, 1000);
      setTasks(Object.fromEntries(page.items.map((t) => [t.id, t])));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      setError(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  // A goal's issues are all in one response; the table reads ahead through them 50 at a time.
  const issues = useMemo(() => goal?.issues ?? [], [goal]);
  const shown = useInfinite(
    async (offset, limit) => ({ items: issues.slice(offset, offset + limit), total: issues.length }),
    50,
    `${goalId}:${issues.length}`,
  );

  if (!goal) return error ? <ErrorAlert error={error} /> : <Center h={240}><Loader /></Center>;
  const open = !["met", "canceled"].includes(goal.status);

  return (
    <>
      <PageHeader
        title={goal.statement}
        description={<Group gap="xs"><StatusBadge status={goal.status} /><Text size="sm" c="dimmed" ff="monospace">{goal.id}</Text></Group>}
        actions={
          <>
            {goal.status === "active" && (
              <>
                <Button leftSection={<IconDownload size={16} />} onClick={() => act(() => api.goalAction(goal.id, "sweep"))}>Take in issues now</Button>
                <Button variant="default" leftSection={<IconPlayerPause size={16} />} onClick={() => act(() => api.goalAction(goal.id, "pause"))}>Pause</Button>
              </>
            )}
            {["paused", "stalled", "budget_exhausted"].includes(goal.status) && (
              <Button leftSection={<IconPlayerPlay size={16} />} onClick={() => act(() => api.goalAction(goal.id, "resume"))}>Resume</Button>
            )}
            {open && (
              <Button color="red" variant="light" leftSection={<IconX size={16} />} onClick={() => act(() => api.goalAction(goal.id, "cancel"))}>Cancel goal</Button>
            )}
          </>
        }
      />
      <ErrorAlert error={error} />
      <Facts
        items={[
          ["Modality", <Text size="sm">{goal.modality}</Text>],
          ["Label", <Badge variant="outline" tt="none" radius="sm">{goal.label}</Badge>],
          ["Repos", <Stack gap={2}>{goal.repos.map((r) => <Text ff="monospace" size="sm" key={r}>{r}</Text>)}</Stack>],
          ["Auto-merge", (
            <Group gap="sm">
              <Text size="sm">{goal.auto_merge ? "On" : "Off: pull requests wait for you"}</Text>
              {open && (
                <Button size="compact-xs" variant="light" onClick={() => act(() => api.patchGoal(goal.id, { auto_merge: !goal.auto_merge }))}>
                  Turn {goal.auto_merge ? "off" : "on"}
                </Button>
              )}
            </Group>
          )],
          ["Budget", <Text size="sm">{goal.max_usd ? `$${goal.max_usd}` : "None"}</Text>],
          ["Created", <Text size="sm">{goal.created_by}, {when(goal.created_at)}</Text>],
        ]}
      />

      <SectionTitle>Issues and tasks</SectionTitle>
      {issues.length ? (
        <DataTable head={["Issue", "Issue state", "Task", "Task status", "Pull request", "Waits on"]}>
          {shown.items.map((i) => {
            const task = i.task_id ? tasks[i.task_id] : undefined;
            return (
              <Table.Tr key={i.ref}>
                <Table.Td><IssueLink issueRef={i.ref} /></Table.Td>
                <Table.Td><Text size="sm">{i.state}</Text></Table.Td>
                <Table.Td>{i.task_id && <Anchor component={Link} to={`/tasks/${i.task_id}`} ff="monospace" size="xs">{i.task_id}</Anchor>}</Table.Td>
                <Table.Td>
                  {task ? <StatusBadge status={task.status} /> : i.refused_reason && <StatusBadge status="refused" />}
                  {i.refused_reason && <Text size="xs" c="dimmed" mt={4}>{i.refused_reason}</Text>}
                </Table.Td>
                <Table.Td>{task?.pr_url && <Anchor href={task.pr_url} target="_blank" size="sm">Open</Anchor>}</Table.Td>
                <Table.Td>{i.depends_on.map((d) => <Text ff="monospace" size="xs" key={d}>{d}</Text>)}</Table.Td>
              </Table.Tr>
            );
          })}
        </DataTable>
      ) : (
        <Empty>
          No issues yet. Label an open issue <Badge variant="outline" tt="none" radius="sm">{goal.label}</Badge> in one of the goal's
          repos. The planner takes it in within a minute, or now with Take in issues now.
        </Empty>
      )}
      <div ref={shown.sentinel} />
    </>
  );
}
