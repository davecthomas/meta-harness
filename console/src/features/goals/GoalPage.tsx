import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Anchor, Badge, Button, Center, Collapse, Group, Loader, Paper, SegmentedControl, Stack, Table, Text } from "@mantine/core";
import { IconArrowBackUp, IconCircleCheck, IconDownload, IconListCheck, IconPlayerPause, IconPlayerPlay } from "@tabler/icons-react";
import { IssuesPicker } from "./IssuesPicker";
import { STEP_EVENTS } from "../tasks/path";
import { api, trackerTitle, goalStatus, goalTitle, type Goal, type IssueLink as Issue, type IssueProgress, type LabelIssue, type MhEvent, type Task } from "../../api";
import { goalSource } from "../trackers/refs";
import { issueSourceLabel } from "./issueSource";
import { CancelGoal } from "./CancelGoal";
import { GoalBoard, GoalGraph, GoalProgress } from "./GoalBoard";
import { GoalStanding } from "./GoalStanding";
import { RemoveIssue } from "./RemoveIssue";
import { GoalFailures, failures } from "./GoalFailures";
import { usd } from "../../money";
import { ApprovedPlan, GoalPlan } from "./GoalPlan";
import { GoalSpend } from "./GoalSpend";
import { BudgetAlert, BudgetRow, ResumeBudget, budgetStanding, budgetStops } from "./budget";
import { AutoMergeEdit, DescriptionField, LabelEdit, ParallelEdit, PriorityEdit, ReposEdit, TitleField } from "./GoalFields";
import { HeldActions, StrictIssuesEdit } from "./sizing";
import { useEventStream, useInfinite } from "../../hooks";
import { DataTable, Empty, ErrorAlert, Facts, IssueLink, PageHeader, PrLink, SectionTitle, StatusBadge, when } from "../../components/ui";
import { ConfirmButton } from "../../components/ConfirmButton";

// How often an open goal's page rereads its issues.
const REFRESH_MS = 5000;

// When the board last refreshed, counted up each second, or why its latest refresh failed.
function Refreshed({ at, error }: { at: number | null; error: string | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  if (error) return <Text size="xs" c="red" data-testid="refreshed">not refreshed: {error}</Text>;
  if (at === null) return null;
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  return <Text size="xs" c="dimmed" data-testid="refreshed">updated {seconds < 2 ? "just now" : `${seconds} s ago`}</Text>;
}

// How the note of a refresh that failed starts, so the next one that works can take it away.
const REFRESH_FAILED = "The goal could not be refreshed";

export function GoalPage() {
  const { goalId = "" } = useParams();
  const navigate = useNavigate();
  const [goal, setGoal] = useState<(Goal & { issues: Issue[] }) | null>(null);
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  // For a goal not started yet: its label's open issues, read but not taken in, so the operator sees what Start runs.
  const [waiting, setWaiting] = useState<(LabelIssue & { repo: string })[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Each issue's progress and what each task spent, for the board, and the position the live stream starts from.
  const [progress, setProgress] = useState<IssueProgress[] | null>(null);
  const [spend, setSpend] = useState<Record<string, number>>({});
  // Each running task's recorded steps, for the board's line that says what it is doing now.
  const [taskSteps, setTaskSteps] = useState<Record<string, MhEvent[]>>({});
  const [spentNow, setSpentNow] = useState<number | undefined>(undefined);
  // Whether the settings are shown: null until the operator says, then as they left it.
  const [settingsOpen, setSettingsOpen] = useState<boolean | null>(null);
  const [from, setFrom] = useState<number | null>(null);
  // When the board last reread its issues, and why its latest reread failed, if it did.
  const [refreshed, setRefreshed] = useState<number | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  // For a goal no sweep covers: when its issues' states were read from GitHub, and whether every one was.
  const [states, setStates] = useState<{ at: string; complete: boolean } | null>(null);
  const [view, setView] = useState<"board" | "graph" | "table" | null>(null);
  const [systemParallel, setSystemParallel] = useState<number | null>(null);
  useEffect(() => {
    api.limits().then((l) => setSystemParallel(l.max_parallel), () => setSystemParallel(null));
  }, []);
  const [picking, setPicking] = useState(false);
  const [spending, setSpending] = useState(false);

  // quiet: a timed refresh, which rereads the goal and its tasks but not its label's issues on the code host.
  const load = useCallback(async (quiet = false) => {
    try {
      const g = await api.goal(goalId);
      setGoal(g);
      const page = await api.tasks({ goalId }, 0, 1000);
      setTasks(Object.fromEntries(page.items.map((t) => [t.id, t])));
      // A refresh that worked ends the note of one that did not: a passing failure leaves no banner behind.
      setError((now) => (now?.startsWith(REFRESH_FAILED) ? null : now));
      if (quiet) return;
      if (goalStatus(g) === "not_started" && g.label) {
        const found = await Promise.all(
          g.repos.map(async (repo) => (await api.labelIssues(repo, g.label!, goalSource(g))).map((issue) => ({ ...issue, repo }))),
        );
        setWaiting(found.flat());
      } else {
        setWaiting(null);
      }
    } catch (err) {
      setError(`${REFRESH_FAILED}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadProgress = useCallback(async () => {
    try {
      const read = await api.goalIssuesChecked(goalId);
      const issues = read.issues;
      setStates(read.states_checked_at ? { at: read.states_checked_at, complete: read.states_complete !== false } : null);
      // What each task's runs spent and the goal's total, from the one place that adds it up: the same read
      // as the goal's Spend window, so the board, the budget line and that window never disagree.
      const cost = issues.some((i) => i.task_id) ? await api.goalSpend(goalId) : null;
      const spent: Record<string, number> = {};
      for (const task of cost?.tasks ?? []) spent[task.task_id] = Number(task.usd) || 0;
      setProgress(issues);
      setSpend(spent);
      const running = issues.filter((i) => i.task_id && i.task_status === "running").map((i) => i.task_id!);
      if (running.length) {
        // Beside the rest, not before it: the board shows without this line if the read is slow or fails.
        void api.taskEvents(running, STEP_EVENTS).then((found) => {
          const by: Record<string, MhEvent[]> = {};
          for (const e of found) if (e.task_id) (by[e.task_id] ??= []).push(e);
          setTaskSteps(by);
        }, () => undefined);
      } else {
        setTaskSteps({});
      }
      setSpentNow(cost ? Number(cost.usd) || 0 : 0);
      setRefreshed(Date.now());
      setRefreshError(null);
    } catch (err) {
      // The board keeps what it shows; the header says the reload failed and why.
      setRefreshError(err instanceof Error ? err.message : String(err));
    }
  }, [goalId]);

  // The board stays current while it is open. It listens to the tasks it knows for changes within a second,
  // and rereads the goal's issues every few seconds, because an issue with no task yet has nothing to listen
  // to: without that, a started goal's cards sat in Queued until a reload. Not while the tab is hidden.
  const closed = goal ? ["met", "canceled"].includes(goal.status) : true;
  useEffect(() => {
    if (closed) return;
    const refresh = () => {
      if (document.hidden) return;
      void loadProgress();
      void load(true);
    };
    const timer = setInterval(refresh, REFRESH_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [closed, load, loadProgress]);

  useEffect(() => {
    void loadProgress();
    api.latestPosition().then((r) => setFrom(Number.isFinite(r.latest_position) ? r.latest_position : null), () => setFrom(null));
  }, [loadProgress]);

  // Cards move as the goal's tasks record events: one reload of the progress per burst of events.
  const pending = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const taskIds = useMemo(() => (progress ?? []).flatMap((i) => (i.task_id ? [i.task_id] : [])), [progress]);
  useEventStream(taskIds, from, (e) => {
    if (e.event_type === "turn_started" || e.event_type === "tool_call" || e.event_type === "tool_result") return;
    clearTimeout(pending.current);
    pending.current = setTimeout(() => {
      void loadProgress();
      void load();
    }, 1000);
  });
  useEffect(() => () => clearTimeout(pending.current), []);

  useEffect(() => {
    if (goal) document.title = `${goalTitle(goal)} · meta-harness`;
  }, [goal]);

  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      setError(null);
      // The action changed what the goal's issues are doing: the board rereads at once, with the goal.
      await Promise.all([load(), loadProgress()]);
    } catch (err) {
      // What was pressed did not happen: say so, since reloading would not do it either.
      setError(`That did not happen: ${err instanceof Error ? err.message : String(err)}. Nothing changed; press it again, or fix what the message names first`);
    }
  }

  // Save one field the operator edited in place, and show the saved goal. A refusal throws, for the field to show.
  async function saveField(fields: Partial<Goal>) {
    const saved = await api.patchGoal(goalId, fields);
    setGoal((now) => (now ? { ...now, ...saved, issues: now.issues } : now));
    // A new label or repos changes which open issues a goal not started yet would take in.
    if ("label" in fields || "repos" in fields) void load();
    // New repos let go of the issues with no task yet in the repos dropped.
    if ("repos" in fields) void loadProgress();
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
  // An active goal opens on its board; any other on the table, until the operator picks a view.
  const shownView = view ?? (goal.status === "active" ? "board" : "table");
  const editable = { goal, readOnly: !open, save: saveField };
  // Settings are open until the goal has issues, then folded unless the operator opened them.
  const showSettings = settingsOpen ?? issues.length === 0;
  // The tasks the failures alert above the board already explains, so the standing alert does not list them again.
  const explained = new Set(progress ? failures(progress).flatMap((i) => (i.task_id ? [i.task_id] : [])) : []);
  // What the goal's runs have spent, once the board has added it up: newer than the planner's last look.
  // (spentNow: read with the board, newer than the figure the goal came with.)

  return (
    <>
      <PageHeader
        title={<TitleField {...editable} />}
        description={
          <Stack gap={6}>
            <DescriptionField {...editable} />
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
            {["paused", "stalled"].includes(status) && (
              <Button leftSection={<IconPlayerPlay size={16} />} onClick={() => act(() => api.goalAction(goal.id, "resume"))}>Resume</Button>
            )}
            {/* Stopped by its budget, or past it and heading there: Resume raises the budget. */}
            {budgetStops(goal) && ["active", "budget_exhausted"].includes(goal.status) && budgetStanding(goal, spentNow) !== "ok" && (
              <ResumeBudget goalId={goal.id} goal={goal} onResumed={() => void act(async () => undefined)} />
            )}
            {["active", "paused", "stalled"].includes(goal.status) && (
              <ConfirmButton
                label="Complete goal"
                title="Mark this goal met?"
                says="The goal is marked met and takes in no more issues. Tasks already running go on to their pull requests; tasks that have not started stay queued and are not canceled. Use Cancel goal to stop its work. You can reopen the goal later."
                confirm="Mark it met"
                color="teal"
                variant="light"
                leftSection={<IconCircleCheck size={16} />}
                onConfirm={async () => {
                  await api.goalAction(goal.id, "complete");
                  await Promise.all([load(), loadProgress()]);
                }}
              />
            )}
            {!open && (
              <Button leftSection={<IconArrowBackUp size={16} />} onClick={() => act(() => api.goalAction(goal.id, "reopen"))}>Reopen goal</Button>
            )}
            {open && (
              <Button variant="default" leftSection={<IconListCheck size={16} />} onClick={() => setPicking(true)}>Add issues</Button>
            )}
            {open && <CancelGoal tasks={Object.values(tasks)} onCancel={() => act(() => api.goalAction(goal.id, "cancel"))} />}
          </>
        }
      />
      <ErrorAlert error={error} />
      <BudgetAlert goal={goal} spent={spentNow} onResumed={() => void act(async () => undefined)} />
      <GoalStanding standing={goal.standing} explained={explained} budgetShown={budgetStanding(goal, spentNow) !== "ok"} />
      <IssuesPicker
        opened={picking}
        onClose={() => setPicking(false)}
        repos={goal.repos}
        chosen={issues.map((i) => i.ref)}
        goalId={goal.id}
        source={goalSource(goal)}
        action="Add to goal"
        onPick={async (refs) => {
          const held = new Set(issues.map((i) => i.ref));
          const added = refs.filter((r) => !held.has(r));
          if (added.length) await api.addIssues(goal.id, added);
          await load();
          await loadProgress();
        }}
      />
      {/* The goal's settings, folded away once it has work to show: the board is what the page is for, and
          one line says what the settings are. Open on a goal with no issues yet, which is when they are set. */}
      <Paper withBorder p="sm" mb="md" data-testid="goal-settings">
        <Group justify="space-between" wrap="nowrap" gap="md">
          <Text size="sm" c="dimmed" lineClamp={1} data-testid="goal-settings-summary">
            <Text span fw={600} c="var(--mantine-color-text)">Settings</Text>
            {" · "}{[
              goal.label ? `label ${goal.label}` : "no label",
              goal.repos.join(", "),
              goal.max_usd ? `budget ${usd(goal.max_usd)}` : "no budget",
              goal.auto_merge ? "auto-merge on" : "auto-merge off",
            ].join(" · ")}
          </Text>
          <Button size="compact-sm" variant="subtle" onClick={() => setSettingsOpen(!showSettings)} aria-expanded={showSettings} aria-controls="goal-settings-body">
            {showSettings ? "Hide settings" : "Show and edit settings"}
          </Button>
        </Group>
        <Collapse expanded={showSettings} id="goal-settings-body">
          <div style={{ marginTop: 12 }}>
          <Facts
            items={[
              [
                "Where its issues come from",
                <Stack gap={0}>
                  <Text size="sm">{issueSourceLabel(goal.modality)}</Text>
                  <Text size="xs" c="dimmed">Fixed when the goal was created.</Text>
                </Stack>,
              ],
              // One tracker for all the goal's issues, fixed when it was created.
              [
                "Issue tracker",
                <Text size="sm">
                  {trackerTitle(goal.tracker ?? "github")}
                  {Object.entries(goal.tracker_settings ?? {}).map(([name, value]) => (name === "assigned" && value === "yes" ? ", takes the issues assigned to the meta-harness" : `, ${name} ${value}`)).join("")}
                  <Text span size="xs" c="dimmed" display="block">Fixed when the goal was created.</Text>
                </Text>,
              ],
              ["Label", <LabelEdit {...editable} />],
              ["Repos", <ReposEdit {...editable} />],
              ["Auto-merge", <AutoMergeEdit {...editable} />],
              // The amount, what happens when it is reached, and the allowed overage, in one row.
              ["Budget", <BudgetRow {...editable} />],
              ["Priority", <PriorityEdit {...editable} />],
              ["Parallel tasks", <ParallelEdit {...editable} system={systemParallel} />],
              ["Strict issues", <StrictIssuesEdit goal={goal} readOnly={!open} save={(fields) => saveField(fields as Partial<Goal>)} />],
              ["Created", <Text size="sm">{goal.created_by}, {when(goal.created_at)}</Text>],
              ["Started", <Text size="sm">{goal.started_at ? when(goal.started_at) : "Not started"}</Text>],
              ...(goal.closed_at ? [[goal.status === "met" ? "Met" : "Ended", <Text size="sm">{when(goal.closed_at)}</Text>] as [string, ReactNode]] : []),
            ]}
          />
          </div>
        </Collapse>
      </Paper>

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

      {goal.status === "planning" && <GoalPlan goal={goal} onApproved={() => void Promise.all([load(), loadProgress()])} />}
      {/* Once approved, what the planner said stays at the head of the goal's work. */}
      {goal.status !== "planning" && goal.plan_approved_at && (
        <ApprovedPlan goal={goal} onPlanAgain={["active", "stalled"].includes(goal.status) ? () => void Promise.all([load(), loadProgress()]) : undefined} />
      )}

      <SectionTitle
        right={
          issues.length > 0 && (
            <Group gap="md">
            {states && (
              <Text size="xs" c="dimmed" data-testid="states-checked">
                {states.complete ? "issue states read from " + trackerTitle(goal.tracker ?? "github") : "some issue states could not be read from " + trackerTitle(goal.tracker ?? "github") + "; last tried"} {when(states.at)}
              </Text>
            )}
            <Refreshed at={refreshed} error={refreshError} />
            <SegmentedControl
              size="xs"
              value={shownView}
              onChange={(v) => setView(v as "board" | "graph" | "table")}
              data={[{ value: "board", label: "Board" }, { value: "graph", label: "Graph" }, { value: "table", label: "Table" }]}
            />
            </Group>
          )
        }
      >
        Issues and tasks
      </SectionTitle>
      {progress && <GoalFailures issues={progress} tasks={tasks} onChanged={() => void Promise.all([load(), loadProgress()])} />}
      {progress && progress.length > 0 && <GoalProgress issues={progress} spend={spend} budget={goal.max_usd} goal={goal} onSpend={() => setSpending(true)} />}
      <GoalSpend goalId={goal.id} goalName={goalTitle(goal)} opened={spending} onClose={() => setSpending(false)} />
      {issues.length > 0 && progress && shownView === "board" ? (
        <GoalBoard
          issues={progress}
          tasks={tasks}
          spend={spend}
          steps={taskSteps}
          actions={(i) => (
            <HeldActions goalId={goal.id} label={goal.label} issue={i} readOnly={!open} onChanged={() => void Promise.all([load(), loadProgress()])} />
          )}
        />
      ) : issues.length > 0 && progress && shownView === "graph" ? (
        <GoalGraph issues={progress} tasks={tasks} />
      ) : issues.length ? (
        <DataTable head={["Task", "Issue state", "Task status", "Pull request", "Waits on", ""]}>
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
                  {task?.pr_url && <PrLink url={task.pr_url} />}
                </Table.Td>
                <Table.Td>{i.depends_on.map((d) => <Text ff="monospace" size="xs" key={d}>{d}</Text>)}</Table.Td>
                <Table.Td>
                  {!["met", "canceled"].includes(goal.status) && <RemoveIssue goalId={goal.id} issueRef={i.ref} task={task} onRemoved={() => void Promise.all([load(), loadProgress()])} />}
                </Table.Td>
              </Table.Tr>
            );
          })}
        </DataTable>
      ) : status === "not_started" ? (
        <Empty>No tasks yet: the goal takes in its issues when it starts.</Empty>
      ) : (
        <Empty>
          No issues yet. {goal.label ? <>Label an open issue <Badge variant="outline" tt="none" radius="sm">{goal.label}</Badge> in one of the goal's repos, or pick issues with Add issues.</> : "Pick issues with Add issues."} The planner takes them in within a minute, or now with Take in issues now.
        </Empty>
      )}
      <div ref={shown.sentinel} />
    </>
  );
}
