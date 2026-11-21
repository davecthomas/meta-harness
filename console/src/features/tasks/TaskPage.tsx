import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CopyId, taskName } from "../../components/TaskName";
import { Link } from "react-router-dom";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { Anchor, Badge, Button, Center, Code, Group, Loader, Paper, SegmentedControl, Stack, Text } from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import { api, goalTitle, type MhEvent, type PullChecks, type ReviewRound, type Task } from "../../api";
import { Checks, useChecks } from "./Checks";
import { Reviews } from "./Reviews";
import { SpendReport } from "./SpendReport";
import { BudgetHold } from "./BudgetHold";
import { WaitingToStart } from "./WaitingToStart";
import { ReviewBanner } from "./reviewing";
import { OperatorReview } from "./OperatorReview";
import { TaskActions } from "./TaskActions";
import { ShepherdPanel } from "./ShepherdPanel";
import { TaskFailure, TITLES } from "./TaskFailure";
import { NowLine, TaskPath, waitsForChecks } from "./TaskPath";
import { steps } from "./path";
import { STEP_EVENTS } from "./path";
import { Plan } from "./Plan";
import { SteerBox } from "./SteerBox";
import { CompareAttempts, RunAttempts } from "./Attempts";
import { Changes } from "./Changes";
import { OpeningSummaryBadge, PullSummaries } from "./PullSummaries";
import { provenanceFacts } from "./Provenance";
import { SpendLine, spendOf } from "./Spend";
import { PROVENANCE_EVENT, RunProvenance } from "./RunProvenance";
import { useEventStream } from "../../hooks";
import { describe, fold, type Line, type TileFacts } from "../../terminal";
import { Terminal } from "../../components/Terminal";
import { ErrorAlert, Facts, IssueLink, PageHeader, PrLink, SectionTitle, StatusBadge } from "../../components/ui";
import { statusLabel } from "../../components/status";
import { shownStatus } from "./shownStatus";
import { moneyText } from "../../money";
import { NoSuchRecord, isNotFound } from "../../components/NoSuchRecord";
import { errorText, ReadFailed } from "../../components/ReadFailed";
import { ConfirmButton } from "../../components/ConfirmButton";

const FIRST = 400;

const TABS = ["timeline", "terminal", "changes", "checks", "reviews", "attempts", "spend"] as const;
export type Tab = (typeof TABS)[number];

const DETAILS_KEY = "mh.task.details";

// The Checks tab's name, with how the checks stand once they are known.
function checksLabel(checks: PullChecks | null): string {
  if (!checks || !checks.total) return "Checks";
  return checks.state === "pending" ? `Checks (${checks.passed}/${checks.total})` : checks.state === "failed" ? "Checks (failed)" : "Checks";
}

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
  const [missing, setMissing] = useState(false);
  // The task's review rounds, for the Reviews tab, read once the task has a pull request.
  // The tab is in the URL (?tab=), so a link can name it and Back returns to the one before. A link to one run
  // (?run=) with no tab opens the Terminal on that run, which is what "Sent to fix run 3" means to show.
  const tab = tabOf(params);
  // A link that names a tab brings that tab's content into view; choosing a tab on the control itself does not.
  const location = useLocation();
  const tabsTop = useRef<HTMLDivElement>(null);
  const scrolledFor = useRef<string | null>(null);
  const pickedHere = useRef(false);
  const named = params.has("tab") || params.has("run");
  const loaded = task !== null;
  useEffect(() => {
    if (!loaded || scrolledFor.current === location.key) return;
    scrolledFor.current = location.key;
    if (pickedHere.current) {
      pickedHere.current = false;
      return;
    }
    if (named) tabsTop.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [loaded, location.key, named]);
  const setTab = (next: Tab) => {
    pickedHere.current = true;
    const kept: Record<string, string> = { tab: next };
    // One run's filter belongs to the views that show runs.
    if (onlyRun && (next === "terminal" || next === "timeline")) kept.run = onlyRun;
    setParams(kept);
  };
  const [rounds, setRounds] = useState<ReviewRound[] | null>(null);
  const [reviewsFailed, setReviewsFailed] = useState<string | null>(null);
  // The task's goal's name, for the link back to it. Null until read, or when the task has no goal.
  const [goalName, setGoalName] = useState<string | null>(null);
  // What the task's runs have spent, as the Task API's spend report counts it: the one figure the header, the
  // Spend tab and the path show. Read again whenever a run finishes or the task's status changes.
  const [spent, setSpent] = useState<number | undefined>(undefined);
  const runsFinished = events.filter((e) => e.event_type === "attempt_finished").length;
  const statusNow = facts.status ?? task?.status;
  useEffect(() => {
    if (!taskId) return;
    // A read that fails, or says no total, leaves the live sum of the model calls on show.
    api.taskSpend(taskId).then(
      (report) => setSpent(report?.totals?.usd == null ? undefined : Number(report.totals.usd) || 0),
      () => setSpent(undefined),
    );
  }, [taskId, runsFinished, statusNow]);
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
    setMissing(false);
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
        // Every step of the task's path too, however old, so the picture of it is whole.
        // turn_started too, so the header counts every turn and run, not only those on the loaded pages.
        // Every run's provenance too, for the details' list of where each run came from.
        api.taskEvents([taskId], [...new Set(["run_prompt", "turn_started", PROVENANCE_EVENT, ...STEP_EVENTS])]).then((prompts) => {
          if (stale) return;
          setEvents((current) => merge(current, prompts));
          // The terminal takes only the prompts: older steps reach it in order, as it scrolls up.
          setLines((current) => merge(current, prompts.filter((e) => e.event_type === "run_prompt").map(withRun).filter((l): l is Line => l !== null)));
        }, () => undefined);
      } catch (err) {
        if (isNotFound(err)) setMissing(true);
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
    api.reviews(taskId).then(
      (read) => {
        setRounds(read);
        setReviewsFailed(null);
      },
      // A failed read is not "no review yet": the tab says it could not be read.
      (err) => setReviewsFailed(errorText(err)),
    );
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

  // The code host's checks, read while someone can see them: on the Checks tab, and while the task waits for
  // them, when the timeline's step and the line above the terminal say how they stand. Read again at each step.
  const newestStep = useMemo(() => steps(events).at(-1), [events]);
  const { checks, error: checksError } = useChecks(taskId, !!task?.pr_url && (tab === "checks" || waitsForChecks(newestStep)), events.length);

  // Whether the task's facts are unfolded: closed unless this browser last left them open.
  const [detailsOpen, setDetailsOpen] = useState(() => {
    try {
      return window.localStorage.getItem(DETAILS_KEY) === "open";
    } catch {
      return false;
    }
  });
  const toggleDetails = () => {
    setDetailsOpen((open) => {
      try {
        window.localStorage.setItem(DETAILS_KEY, open ? "closed" : "open");
      } catch {
        // Without storage the choice lasts for this page only.
      }
      return !open;
    });
  };

  if (!task && missing) return <NoSuchRecord kind="task" id={taskId} />;
  if (!task) return error ? <ErrorAlert error={error} /> : <Center h={240}><Loader /></Center>;
  const status = facts.status ?? task.status;
  // As every list shows it: a working task with a pull request open is in review.
  const shown = shownStatus({ ...task, status });
  // Only a running task has a run under way; an ended one's output is the record of its runs.
  const running = status === "running";
  const ended = ["closed", "failed", "canceled"].includes(status);
  const cancellable = ["queued", "blocked", "running"].includes(status);
  // What stopping it does, by where it is: said before it is stopped.
  const cancelSays =
    status === "running"
      ? "The agent's run is stopped and the task is canceled. What the run changed so far stays in its workspace and is not published. A pull request already open stays open. You can retry the task later."
      : "The task is canceled before it starts. Its issue stays open; retry the task later, or remove the issue from its goal.";

  return (
    <>
      <PageHeader
        title={taskName(task)}
        description={
          <Stack gap={6}>
            {task.name && <Text size="sm">{task.title}</Text>}
            <Group gap={6}><StatusBadge status={shown} /><CopyId id={task.id} /></Group>
          </Stack>
        }
        actions={
          <>
            {cancellable && (
              <ConfirmButton
                label="Cancel task"
                title="Cancel this task?"
                says={cancelSays}
                cancel="Keep the task"
                color="red"
                variant="light"
                leftSection={<IconX size={16} />}
                onConfirm={async () => setTask(await api.cancelTask(task!.id))}
              />
            )}
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
      <ShepherdPanel task={task} status={status} onChanged={() => void api.task(taskId).then(setTask, () => undefined)}>
        {TITLES[status] && <TaskFailure task={task} status={status} events={events} onChanged={() => void api.task(taskId).then(setTask, () => undefined)} />}
      </ShepherdPanel>
      {/* The task's facts, folded away: the timeline below is what the page is for. The fold's own line keeps
          the way back to the goal. Whoever opens it finds it open on the next task too. */}
      <Paper withBorder p="sm" mb="md" data-testid="task-details">
        <Group justify="space-between" wrap="nowrap" gap="md">
          <Text size="sm" lineClamp={1} data-testid="task-details-summary">
            {task.goal_id ? (
              <>
                <Text span c="dimmed">Goal: </Text>
                <Anchor component={Link} to={`/goals/${task.goal_id}`} size="sm" ff={goalName ? undefined : "monospace"}>{goalName ?? task.goal_id}</Anchor>
              </>
            ) : (
              <Text span c="dimmed">A task with no goal</Text>
            )}
          </Text>
          <Button size="compact-sm" variant="subtle" onClick={toggleDetails} aria-expanded={detailsOpen} aria-controls="task-details-body">
            {detailsOpen ? "Hide details" : "Show details"}
          </Button>
        </Group>
        {detailsOpen && (
          <div id="task-details-body" style={{ marginTop: 12 }}>
        <Facts
          items={[
            ["Status", (
              <Text size="sm">
                {statusLabel(shown)}{task.status_reason && `: ${moneyText(task.status_reason)}`}
                {task.cancel_requested && status === "running" && " (cancel requested)"}
              </Text>
            )],
            ["Repo", <Code>{task.repo}</Code>],
            ["Issue", <IssueLink issueRef={task.external_ref} />],
            ...(task.goal_id ? [["Goal", <Anchor component={Link} to={`/goals/${task.goal_id}`} size="sm" ff={goalName ? undefined : "monospace"}>{goalName ?? task.goal_id}</Anchor>] as [string, React.ReactNode]] : []),
            ["Pull request", task.pr_url ? <Group gap="xs"><PrLink url={task.pr_url} /><Text size="xs" c="dimmed">{task.pr_url}</Text><OpeningSummaryBadge events={events} /></Group> : <Text size="sm" c="dimmed">None yet</Text>],
            ...provenanceFacts(events),
            ["Acceptance checks", <Stack gap={2}>{task.acceptance_checks.map((c) => <Code key={c}>{c}</Code>)}</Stack>],
            ["Run provenance", <RunProvenance events={events} />],
            ["Turn and spend", <Stack gap={4}><SpendLine spend={spendOf(events)} turn={facts.turn} total={spent} running={status === "running"} model={facts.model} /></Stack>],
          ]}
        />
          </div>
        )}
      </Paper>
      {status === "running" && <SteerBox taskId={task.id} />}
      {/* Several attempts at once is a choice made before a task's first run, not one it can take with a pull request open. */}
      {["queued", "blocked"].includes(status) && !task.pr_url && (
        <RunAttempts taskId={task.id} attempts={task.attempts ?? 1} onSet={() => void api.task(task.id).then(setTask)} />
      )}
      <Plan events={events} running={status === "running"} />
      <div ref={tabsTop}>
      <SegmentedControl
        mb="md"
        value={tab}
        onChange={(v) => setTab(v as Tab)}
        data={[
          { value: "timeline", label: "Timeline" },
          { value: "terminal", label: "Terminal" },
          ...((task.attempts ?? 1) > 1 ? [{ value: "attempts", label: "Attempts" }] : []),
          ...(task.pr_url ? [{ value: "changes", label: "Changes" }, { value: "checks", label: checksLabel(checks) }, { value: "reviews", label: `Reviews${rounds ? ` (${rounds.length})` : ""}` }] : []),
          { value: "spend", label: "Spend" },
        ]}
      />
      </div>
      {tab === "reviews" && task.pr_url ? (
        reviewsFailed && !rounds ? (
          <ReadFailed what="this task's reviews" error={reviewsFailed} />
        ) : rounds ? (
          <Reviews
            task={{ ...task, status }}
            rounds={rounds}
            onAnswered={() => {
              void api.task(task.id).then(setTask, () => undefined);
              void api.reviews(task.id).then(setRounds, () => undefined);
            }}
          />
        ) : <Center h={120}><Loader /></Center>
      ) : tab === "checks" && task.pr_url ? (
        <Checks checks={checks} error={checksError} />
      ) : tab === "spend" ? (
        <SpendReport taskId={task.id} status={status} />
      ) : tab === "attempts" ? (
        <CompareAttempts taskId={task.id} />
      ) : tab === "changes" && task.pr_url ? (
        <>
          <PullSummaries events={events} />
          <Changes taskId={task.id} />
        </>
      ) : tab === "timeline" ? (
        <TaskPath
          events={events}
          checks={checks}
          usd={spent}
          pick={onlyRun}
          onOpenRun={(run) => setParams({ tab: "terminal", run })}
          onOpenReviews={(round) => setParams(round !== undefined ? { tab: "reviews", round: String(round) } : { tab: "reviews" })}
          onOpenTab={(tab) => setParams({ tab })}
          onPickRun={(run) => void api.runEvents(run).then((found) => setEvents((current) => merge(current, found)), () => undefined)}
        />
      ) : (
      <>
      <SectionTitle
        right={running
          ? <Badge variant="dot" color={STREAM_COLORS[state] ?? "gray"} tt="none">{state}</Badge>
          : ended ? <Text size="xs" c="dimmed">Output is the record of this task's runs</Text> : undefined}
      >Run</SectionTitle>
      {onlyRun && (
        <Group gap="xs" mb="xs">
          <Text size="sm">Showing run <Code>{onlyRun}</Code></Text>
          <Button size="compact-xs" variant="subtle" onClick={() => setParams({ tab })}>Show every run</Button>
        </Group>
      )}
      {!onlyRun && <NowLine events={events} checks={checks} />}
      <Paper p={0} style={{ overflow: "hidden" }}>
        <Terminal lines={onlyRun ? lines.filter((l) => l.run === onlyRun) : lines} className="tall" onScrollTop={loadOlder} />
      </Paper>
      </>
      )}
    </>
  );
}
