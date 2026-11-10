import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useParams, useSearchParams } from "react-router-dom";
import { Anchor, Badge, Button, Center, Code, Group, Loader, Paper, Stack, Text } from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import { api, goalTitle, type MhEvent, type Task } from "../api";
import { useEventStream } from "../hooks";
import { describe, fold, type Line, type TileFacts } from "../terminal";
import { Terminal } from "../components/Terminal";
import { ErrorAlert, Facts, IssueLink, PageHeader, PrLink, SectionTitle, StatusBadge } from "../components/ui";

const FIRST = 400;

// An event as a terminal line, carrying its run so one run's lines can be shown alone.
function withRun(e: MhEvent): Line | null {
  const line = describe(e);
  return line ? { ...line, run: e.run_id } : null;
}
const OLDER = 200;
const EMPTY: TileFacts = { turn: 0, usd: 0, model: "", status: null };
const STREAM_COLORS: Record<string, string> = { live: "teal", connecting: "yellow", reconnecting: "orange", idle: "gray" };

// One task: its facts, cancel, and its run as a live terminal. The terminal opens at the newest
// events and loads older ones as you scroll up.
export function TaskPage() {
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  // ?run=<run id> opens on one run's events, as a cell on the activity map links; null shows every run.
  const [params, setParams] = useSearchParams();
  const onlyRun = params.get("run");
  const [facts, setFacts] = useState<TileFacts>(EMPTY);
  const [from, setFrom] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The task's goal's name, for the link back to it. Null until read, or when the task has no goal.
  const [goalName, setGoalName] = useState<string | null>(null);
  const oldest = useRef<number | null>(null);
  const newest = useRef(0);
  const loadingOlder = useRef(false);
  const exhausted = useRef(false);

  useEffect(() => {
    let stale = false;
    setLines([]);
    setFacts(EMPTY);
    setFrom(null);
    exhausted.current = false;
    (async () => {
      try {
        const [t, page] = await Promise.all([api.task(taskId), api.events([taskId], FIRST)]);
        if (stale) return;
        setTask(t);
        if (t.goal_id) api.goal(t.goal_id).then((g) => !stale && setGoalName(goalTitle(g)), () => undefined);
        setLines(page.events.map(withRun).filter((l): l is Line => l !== null));
        setFacts(page.events.reduce(fold, EMPTY));
        oldest.current = page.events[0]?.position ?? null;
        exhausted.current = page.events.length < FIRST;
        newest.current = Math.max(page.events.at(-1)?.position ?? 0, page.latest_position);
        setFrom(newest.current);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      stale = true;
    };
  }, [taskId]);

  const onEvent = useCallback((e: MhEvent) => {
    if (e.position <= newest.current) return;
    newest.current = e.position;
    setFacts((f) => fold(f, e));
    if (e.event_type === "task_status_changed") setTask((t) => (t ? { ...t, status: e.payload.to } : t));
    if (e.event_type === "pull_request_opened") setTask((t) => (t ? { ...t, pr_url: e.payload.pr_url } : t));
    const line = withRun(e);
    if (line) setLines((current) => [...current, line]);
  }, []);

  const state = useEventStream(from === null ? [] : [taskId], from, onEvent);

  const loadOlder = useCallback(async () => {
    if (loadingOlder.current || exhausted.current || oldest.current === null) return;
    loadingOlder.current = true;
    try {
      const page = await api.events([taskId], OLDER, oldest.current);
      exhausted.current = page.events.length < OLDER;
      if (page.events.length) {
        oldest.current = page.events[0].position;
        const older = page.events.map(withRun).filter((l): l is Line => l !== null);
        setLines((current) => [...older, ...current]);
      }
    } finally {
      loadingOlder.current = false;
    }
  }, [taskId]);

  if (!task) return error ? <ErrorAlert error={error} /> : <Center h={240}><Loader /></Center>;
  const status = facts.status ?? task.status;
  const cancellable = ["queued", "blocked", "running"].includes(status);
  // A task waiting on someone, its pull request open with no workflow left, is cleared rather than canceled.
  const clearable = ["ready", "review_unresolved"].includes(status);

  async function clear() {
    try {
      setTask(await api.clearTask(task!.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function cancel() {
    try {
      setTask(await api.cancelTask(task!.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <PageHeader
        title={task.title}
        description={<Group gap="xs"><StatusBadge status={status} /><Text size="sm" c="dimmed" ff="monospace">{task.id}</Text></Group>}
        actions={
          <>
            {cancellable && <Button color="red" variant="light" leftSection={<IconX size={16} />} onClick={cancel}>Cancel task</Button>}
            {clearable && <Button variant="light" leftSection={<IconX size={16} />} onClick={clear}>Clear task</Button>}
          </>
        }
      />
      <ErrorAlert error={error} />
      <Facts
        items={[
          ["Status", (
            <Text size="sm">
              {status}{task.status_reason && `: ${task.status_reason}`}
              {task.cancel_requested && status === "running" && " (cancel requested)"}
            </Text>
          )],
          ["Repo", <Code>{task.repo}</Code>],
          ["Issue", <IssueLink issueRef={task.external_ref} />],
          ...(task.goal_id ? [["Goal", <Anchor component={Link} to={`/goals/${task.goal_id}`} size="sm" ff={goalName ? undefined : "monospace"}>{goalName ?? task.goal_id}</Anchor>] as [string, React.ReactNode]] : []),
          ["Pull request", task.pr_url ? <Group gap="xs"><PrLink url={task.pr_url} /><Text size="xs" c="dimmed">{task.pr_url}</Text></Group> : <Text size="sm" c="dimmed">None yet</Text>],
          ["Acceptance checks", <Stack gap={2}>{task.acceptance_checks.map((c) => <Code key={c}>{c}</Code>)}</Stack>],
          ["Turn and spend", <Text size="sm">Turn {facts.turn}, ${facts.usd.toFixed(4)} {facts.model && <Code>{facts.model}</Code>}</Text>],
        ]}
      />
      <SectionTitle right={<Badge variant="dot" color={STREAM_COLORS[state] ?? "gray"} tt="none">{state}</Badge>}>Run</SectionTitle>
      {onlyRun && (
        <Group gap="xs" mb="xs">
          <Text size="sm">Showing run <Code>{onlyRun}</Code></Text>
          <Button size="compact-xs" variant="subtle" onClick={() => setParams({})}>Show every run</Button>
        </Group>
      )}
      <Paper p={0} style={{ overflow: "hidden" }}>
        <Terminal lines={onlyRun ? lines.filter((l) => l.run === onlyRun) : lines} className="tall" onScrollTop={loadOlder} />
      </Paper>
    </>
  );
}
