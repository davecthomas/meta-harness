import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useParams, useSearchParams } from "react-router-dom";
import { Anchor, Badge, Button, Center, Code, Group, Loader, Paper, SegmentedControl, Stack, Text } from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import { api, goalTitle, type MhEvent, type ReviewRound, type Task } from "../../api";
import { Reviews } from "./Reviews";
import { SpendReport } from "./SpendReport";
import { BudgetHold } from "./BudgetHold";
import { WaitingToStart } from "./WaitingToStart";
import { ReviewBanner } from "./reviewing";
import { OperatorReview } from "./OperatorReview";
import { TaskActions } from "./TaskActions";
import { TaskFailure } from "./TaskFailure";
import { Timeline } from "./Timeline";
import { Plan } from "./Plan";
import { SteerBox } from "./SteerBox";
import { CompareAttempts, RunAttempts } from "./Attempts";
import { Changes } from "./Changes";
import { SpendLine, spendOf } from "./Spend";
import { useEventStream } from "../../hooks";
import { describe, fold, type Line, type TileFacts } from "../../terminal";
import { Terminal } from "../../components/Terminal";
import { ErrorAlert, Facts, IssueLink, PageHeader, PrLink, SectionTitle, StatusBadge } from "../../components/ui";
import { moneyText } from "../../money";

const FIRST = 400;

const TABS = ["timeline", "terminal", "changes", "reviews", "attempts", "spend"] as const;
export type Tab = (typeof TABS)[number];

// The tab a task page URL shows: the one it names, else the Terminal when it names a run, else the Timeline.
export function tabOf(params: URLSearchParams): Tab {
  const named = params.get("tab");
  if (named && (TABS as readonly string[]).includes(named)) return named as Tab;
  return params.get("run") ? "terminal" : "timeline";
}

// An event as a terminal line, carrying its run so one run's lines can be shown alone.
function withRun(e: MhEvent): Line | null {
  const line = describe(e);
  return line ? { ...line, run: e.run_id } : null;
}
const OLDER = 200;

// Events (or their terminal lines) added to the ones held, each once, in position order.
export function merge<T extends { position: number }>(current: T[], more: T[]): T[] {
  const held = new Set(current.map((e) => e.position));
  const added = more.filter((e) => !held.has(e.position)).sort((a, b) => a.position - b.position);
  if (!added.length) return current;
  const last = current.at(-1)?.position ?? 0;
  // A live event comes after every one held, so it needs no sort.
  if (added.every((e) => e.position > last)) return [...current, ...added];
  return [...current, ...added].sort((a, b) => a.position - b.position);
}
const EMPTY: TileFacts = { turn: 0, usd: 0, model: "", status: null };
const STREAM_COLORS: Record<string, string> = { live: "teal", connecting: "yellow", reconnecting: "orange", idle: "gray" };

// One task: its facts, cancel, and its run as a live terminal. The terminal opens at the newest
// events and loads older ones as you scroll up.
export function TaskPage() {
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  // The raw events, for the timeline; the terminal shows them as lines.
  const [events, setEvents] = useState<MhEvent[]>([]);
  // ?run=<run id> opens on one run's events, as a cell on the activity map links; null shows every run.
  const [params, setParams] = useSearchParams();
  const onlyRun = params.get("run");
  const [facts, setFacts] = useState<TileFacts>(EMPTY);
  const [from, setFrom] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The task's review rounds, for the Reviews tab, read once the task has a pull request.
  // The tab is in the URL (?tab=), so a link can name it and Back returns to the one before. A link to one run
  // (?run=) with no tab opens the Terminal on that run, which is what "Sent to fix run 3" means to show.
  const tab = tabOf(params);
  const setTab = (next: Tab) => {
    const kept: Record<string, string> = { tab: next };
    // One run's filter belongs to the views that show runs.
    if (onlyRun && (next === "terminal" || next === "timeline")) kept.run = onlyRun;
    setParams(kept);
  };
  const [rounds, setRounds] = useState<ReviewRound[] | null>(null);
  // The task's goal's name, for the link back to it. Null until read, or when the task has no goal.
  const [goalName, setGoalName] = useState<string | null>(null);
  const oldest = useRef<number | null>(null);
  const newest = useRef(0);
  const loadingOlder = useRef(false);
  const exhausted = useRef(false);

  useEffect(() => {
    let stale = false;
    setLines([]);
    setEvents([]);
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
        setEvents(page.events);
        setFacts(page.events.reduce(fold, EMPTY));
        oldest.current = page.events[0]?.position ?? null;
        exhausted.current = page.events.length < FIRST;
        newest.current = Math.max(page.events.at(-1)?.position ?? 0, page.latest_position);
        setFrom(newest.current);
        // Every run's prompt, older runs' too: the first page holds only the newest events, and the
        // timeline (or a ?run= link to an older run) would otherwise show no prompt for those runs.
        // The terminal gets them too, so an older run's Terminal opens on its prompt.
        api.taskEvents([taskId], ["run_prompt"]).then((prompts) => {
          if (stale) return;
          setEvents((current) => merge(current, prompts));
          setLines((current) => merge(current, prompts.map(withRun).filter((l): l is Line => l !== null)));
        }, () => undefined);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      stale = true;
    };
  }, [taskId]);

  // Reviews come from events the workflow records; reread them when the pull request appears or a round ends.
  const prUrl = task?.pr_url ?? null;
  useEffect(() => {
    if (!prUrl) return;
    api.reviews(taskId).then(setRounds, () => setRounds([]));
  }, [taskId, prUrl, facts.status]);

  const onEvent = useCallback((e: MhEvent) => {
    if (e.position <= newest.current) return;
    newest.current = e.position;
    setFacts((f) => fold(f, e));
    // A hold for budget, or its answer, changes what the task waits on: read the task again.
    // So does a new reason the scheduler holds it for.
    if (["budget_hold", "budget_decision", "review_hold", "review_proceeded", "task_held", "operator_review_claimed", "operator_review_released", "operator_review_submitted"].includes(e.event_type)) void api.task(taskId).then(setTask, () => undefined);
    // A dismissal shows on its finding: read the rounds again.
    if (e.event_type === "finding_dismissed" || e.event_type === "review_hold") void api.reviews(taskId).then(setRounds, () => undefined);
    if (e.event_type === "task_status_changed") setTask((t) => (t ? { ...t, status: e.payload.to, status_reason: e.payload.reason ?? null } : t));
    if (e.event_type === "pull_request_opened") setTask((t) => (t ? { ...t, pr_url: e.payload.pr_url } : t));
    // A prompt read with every run's prompts may come again on the stream.
    setEvents((current) => merge(current, [e]));
    const line = withRun(e);
    if (line) setLines((current) => merge(current, [line]));
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
        setLines((current) => merge(current, older));
        setEvents((current) => merge(current, page.events));
      }
    } finally {
      loadingOlder.current = false;
    }
  }, [taskId]);

  if (!task) return error ? <ErrorAlert error={error} /> : <Center h={240}><Loader /></Center>;
  const status = facts.status ?? task.status;
  const cancellable = ["queued", "blocked", "running"].includes(status);
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
            <TaskActions
              task={task}
              status={status}
              merged={events.some((e) => e.event_type === "pull_request_merged")}
              onChanged={setTask}
              onError={setError}
            />
          </>
        }
      />
      <ErrorAlert error={error} />
      <ReviewBanner task={{ ...task, status }} events={events} onChanged={setTask} />
      <OperatorReview task={{ ...task, status }} rounds={rounds ?? []} onChanged={setTask} />
      <BudgetHold task={{ ...task, status }} onAnswered={setTask} />
      <WaitingToStart task={{ ...task, status }} onChanged={() => void api.task(taskId).then(setTask, () => undefined)} />
      <TaskFailure task={task} status={status} events={events} />
      <Facts
        items={[
          ["Status", (
            <Text size="sm">
              {status}{task.status_reason && `: ${moneyText(task.status_reason)}`}
              {task.cancel_requested && status === "running" && " (cancel requested)"}
            </Text>
          )],
          ["Repo", <Code>{task.repo}</Code>],
          ["Issue", <IssueLink issueRef={task.external_ref} />],
          ...(task.goal_id ? [["Goal", <Anchor component={Link} to={`/goals/${task.goal_id}`} size="sm" ff={goalName ? undefined : "monospace"}>{goalName ?? task.goal_id}</Anchor>] as [string, React.ReactNode]] : []),
          ["Pull request", task.pr_url ? <Group gap="xs"><PrLink url={task.pr_url} /><Text size="xs" c="dimmed">{task.pr_url}</Text></Group> : <Text size="sm" c="dimmed">None yet</Text>],
          ["Acceptance checks", <Stack gap={2}>{task.acceptance_checks.map((c) => <Code key={c}>{c}</Code>)}</Stack>],
          ["Turn and spend", <Stack gap={4}><SpendLine spend={spendOf(events)} turn={facts.turn} />{facts.model && <Code>{facts.model}</Code>}</Stack>],
        ]}
      />
      {status === "running" && <SteerBox taskId={task.id} />}
      {["queued", "blocked"].includes(status) && (
        <RunAttempts taskId={task.id} attempts={task.attempts ?? 1} onSet={() => void api.task(task.id).then(setTask)} />
      )}
      <Plan events={events} />
      <SegmentedControl
        mb="md"
        value={tab}
        onChange={(v) => setTab(v as Tab)}
        data={[
          { value: "timeline", label: "Timeline" },
          { value: "terminal", label: "Terminal" },
          ...((task.attempts ?? 1) > 1 ? [{ value: "attempts", label: "Attempts" }] : []),
          ...(task.pr_url ? [{ value: "changes", label: "Changes" }, { value: "reviews", label: `Reviews${rounds ? ` (${rounds.length})` : ""}` }] : []),
          { value: "spend", label: "Spend" },
        ]}
      />
      {tab === "reviews" && task.pr_url ? (
        rounds ? (
          <Reviews
            task={{ ...task, status }}
            rounds={rounds}
            onAnswered={() => {
              void api.task(task.id).then(setTask, () => undefined);
              void api.reviews(task.id).then(setRounds, () => undefined);
            }}
          />
        ) : <Center h={120}><Loader /></Center>
      ) : tab === "spend" ? (
        <SpendReport taskId={task.id} status={status} />
      ) : tab === "attempts" ? (
        <CompareAttempts taskId={task.id} />
      ) : tab === "changes" && task.pr_url ? (
        <Changes taskId={task.id} />
      ) : tab === "timeline" ? (
        <Timeline events={onlyRun ? events.filter((e) => e.run_id === onlyRun || !e.run_id) : events} />
      ) : (
      <>
      <SectionTitle right={<Badge variant="dot" color={STREAM_COLORS[state] ?? "gray"} tt="none">{state}</Badge>}>Run</SectionTitle>
      {onlyRun && (
        <Group gap="xs" mb="xs">
          <Text size="sm">Showing run <Code>{onlyRun}</Code></Text>
          <Button size="compact-xs" variant="subtle" onClick={() => setParams({ tab })}>Show every run</Button>
        </Group>
      )}
      <Paper p={0} style={{ overflow: "hidden" }}>
        <Terminal lines={onlyRun ? lines.filter((l) => l.run === onlyRun) : lines} className="tall" onScrollTop={loadOlder} />
      </Paper>
      </>
      )}
    </>
  );
}
