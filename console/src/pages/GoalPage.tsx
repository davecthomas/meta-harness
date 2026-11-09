import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Anchor, Badge, Button, Center, Group, Loader, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconArrowBackUp, IconCheck, IconCircleCheck, IconDownload, IconPencil, IconPlayerPause, IconPlayerPlay, IconX } from "@tabler/icons-react";
import { api, goalStatus, goalTitle, type Goal, type IssueLink as Issue, type LabelIssue, type Task } from "../api";
import { useInfinite } from "../hooks";
import { DataTable, Empty, ErrorAlert, Facts, IssueLink, PageHeader, SectionTitle, StatusBadge, when } from "../components/ui";

export function GoalPage() {
  const { goalId = "" } = useParams();
  const navigate = useNavigate();
  const [goal, setGoal] = useState<(Goal & { issues: Issue[] }) | null>(null);
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  // For a goal not started yet: its label's open issues, read but not taken in, so the operator sees what Start runs.
  const [waiting, setWaiting] = useState<(LabelIssue & { repo: string })[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The title being edited, or null while it is not.
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const g = await api.goal(goalId);
      setGoal(g);
      const page = await api.tasks({ goalId }, 0, 1000);
      setTasks(Object.fromEntries(page.items.map((t) => [t.id, t])));
      if (goalStatus(g) === "not_started") {
        const found = await Promise.all(
          g.repos.map(async (repo) => (await api.labelIssues(repo, g.label)).map((issue) => ({ ...issue, repo }))),
        );
        setWaiting(found.flat());
      } else {
        setWaiting(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (goal) document.title = `${goalTitle(goal)} · meta-harness`;
  }, [goal]);

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
  const status = goalStatus(goal);

  return (
    <>
      <PageHeader
        title={
          <Group gap="xs" wrap="nowrap" align="center">
            <span>{goalTitle(goal)}</span>
            {editing === null && (
              <Button size="compact-xs" variant="subtle" leftSection={<IconPencil size={14} />} onClick={() => setEditing(goal.title ?? goalTitle(goal))}>
                Edit title
              </Button>
            )}
          </Group>
        }
        description={
          <Stack gap={6}>
            <Text size="sm">{goal.statement}</Text>
            <Group gap="xs"><StatusBadge status={status} /><Text size="sm" c="dimmed" ff="monospace">{goal.id}</Text></Group>
          </Stack>
        }
        actions={
          <>
            {goal.status === "active" && (
              <>
                <Button leftSection={<IconDownload size={16} />} onClick={() => act(() => api.goalAction(goal.id, "sweep"))}>Take in issues now</Button>
                <Button variant="default" leftSection={<IconPlayerPause size={16} />} onClick={() => act(() => api.goalAction(goal.id, "pause"))}>Pause</Button>
              </>
            )}
            {status === "not_started" && (
              <Button leftSection={<IconPlayerPlay size={16} />} onClick={() => act(() => api.goalAction(goal.id, "resume"))}>Start</Button>
            )}
            {["paused", "stalled", "budget_exhausted"].includes(status) && (
              <Button leftSection={<IconPlayerPlay size={16} />} onClick={() => act(() => api.goalAction(goal.id, "resume"))}>Resume</Button>
            )}
            {["active", "paused", "stalled"].includes(goal.status) && (
              <Button color="teal" variant="light" leftSection={<IconCircleCheck size={16} />} onClick={() => act(() => api.goalAction(goal.id, "complete"))}>Complete goal</Button>
            )}
            {!open && (
              <Button leftSection={<IconArrowBackUp size={16} />} onClick={() => act(() => api.goalAction(goal.id, "reopen"))}>Reopen goal</Button>
            )}
            {open && (
              <Button color="red" variant="light" leftSection={<IconX size={16} />} onClick={() => act(() => api.goalAction(goal.id, "cancel"))}>Cancel goal</Button>
            )}
          </>
        }
      />
      <ErrorAlert error={error} />
      {editing !== null && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => api.patchGoal(goal.id, { title: editing.trim() })).then(() => setEditing(null));
          }}
        >
          <Group gap="xs" mb="md" maw={720}>
            <TextInput aria-label="Title" value={editing} maxLength={80} required onChange={(e) => setEditing(e.currentTarget.value)} style={{ flex: 1 }} />
            <Button type="submit" leftSection={<IconCheck size={16} />}>Save title</Button>
            <Button variant="default" onClick={() => setEditing(null)}>Cancel</Button>
          </Group>
        </form>
      )}
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
          ["Started", <Text size="sm">{goal.started_at ? when(goal.started_at) : "Not started"}</Text>],
          ...(goal.closed_at ? [[goal.status === "met" ? "Met" : "Ended", <Text size="sm">{when(goal.closed_at)}</Text>] as [string, ReactNode]] : []),
        ]}
      />

      {waiting && (
        <>
          <SectionTitle>Open issues with its label</SectionTitle>
          <Text size="sm" c="dimmed" mb="sm">
            Read, not taken in. Start turns each one into a task, except one another goal already holds.
          </Text>
          {waiting.length ? (
            <DataTable head={["Issue", "Title", "Updated", "Held by"]}>
              {waiting.map((i) => (
                <Table.Tr key={i.ref}>
                  <Table.Td><IssueLink issueRef={i.ref} /></Table.Td>
                  <Table.Td><Text size="sm">{i.title}</Text></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{when(i.updated_at)}</Text></Table.Td>
                  <Table.Td>
                    {i.held_by && <Anchor component={Link} to={`/goals/${i.held_by}`} ff="monospace" size="xs">{i.held_by}</Anchor>}
                  </Table.Td>
                </Table.Tr>
              ))}
            </DataTable>
          ) : (
            <Empty>
              No open issue has the label <Badge variant="outline" tt="none" radius="sm">{goal.label}</Badge> in the goal's repos yet.
            </Empty>
          )}
        </>
      )}

      <SectionTitle>Issues and tasks</SectionTitle>
      {issues.length ? (
        <DataTable head={["Task", "Issue state", "Task status", "Pull request", "Waits on"]}>
          {shown.items.map((i) => {
            const task = i.task_id ? tasks[i.task_id] : undefined;
            const to = i.task_id ? `/tasks/${i.task_id}` : null;
            // A row with a task opens it: a click, Enter, or Ctrl- or Cmd-click for a new tab, as a link does.
            const open = (newTab: boolean) => {
              if (!to) return;
              if (newTab) window.open(to, "_blank", "noopener");
              else navigate(to);
            };
            const running = task?.status === "running" || task?.status === "taken_over";
            return (
              <Table.Tr
                key={i.ref}
                data-testid={`issue-row-${i.ref}`}
                role={to ? "link" : undefined}
                tabIndex={to ? 0 : undefined}
                aria-label={to ? `Open task ${task?.title ?? i.task_id}` : undefined}
                onClick={to ? (e) => open(e.ctrlKey || e.metaKey) : undefined}
                onKeyDown={to ? (e) => e.key === "Enter" && open(e.ctrlKey || e.metaKey) : undefined}
                style={{
                  cursor: to ? "pointer" : "default",
                  boxShadow: running ? "inset 3px 0 0 var(--mantine-color-teal-6)" : undefined,
                }}
              >
                <Table.Td maw={420}>
                  {task ? <Text fw={500} size="sm">{task.title}</Text> : <Text size="sm" c="dimmed">No task</Text>}
                  {/* The issue link opens GitHub, not the task. */}
                  <span onClick={(e) => e.stopPropagation()}><IssueLink issueRef={i.ref} /></span>
                </Table.Td>
                <Table.Td><Text size="sm">{i.state}</Text></Table.Td>
                <Table.Td>
                  {task ? <StatusBadge status={task.status} /> : i.refused_reason && <StatusBadge status="refused" />}
                  {i.refused_reason && <Text size="xs" c="dimmed" mt={4}>{i.refused_reason}</Text>}
                </Table.Td>
                <Table.Td onClick={(e) => e.stopPropagation()}>
                  {task?.pr_url && <Anchor href={task.pr_url} target="_blank" size="sm">Open</Anchor>}
                </Table.Td>
                <Table.Td>{i.depends_on.map((d) => <Text ff="monospace" size="xs" key={d}>{d}</Text>)}</Table.Td>
              </Table.Tr>
            );
          })}
        </DataTable>
      ) : status === "not_started" ? (
        <Empty>No tasks yet: the goal takes in its issues when it starts.</Empty>
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
