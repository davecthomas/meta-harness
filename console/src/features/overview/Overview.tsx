import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Button, Card, Group, Paper, SimpleGrid, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconAlertTriangle, IconPlayerStop, IconPlayerPlay } from "@tabler/icons-react";
import { goalTitle, api, goalStatus, type Goal, type KillSwitch, type MhEvent, type Task } from "../../api";
import { PullRequestsRow } from "./PullRequestsRow";
import { ReviewsInProgress } from "../tasks/reviewing";
import { BudgetMark, ResumeBudget, budgetStanding, budgetStops, budgetWords } from "../goals/budget";
import { useInfinite } from "../../hooks";
import { DataTable, Empty, ErrorAlert, IssueLink, PageHeader, PrLink, SectionTitle, StatusBadge, TitleLink, when } from "../../components/ui";
import { moneyText } from "../../money";
import { diagnose } from "../tasks/diagnosis";
import { FailureGroups, groupFailures } from "../tasks/FailureGroups";
import { needsYouNote } from "./needsYou";

const OPEN_GOALS = ["active", "paused", "stalled", "budget_exhausted"];
const NEEDS_YOU = ["ready", "failed", "review_unresolved"];

export function Overview() {
  // Counted up by each action taken here, so the lists reread.
  const [resumed, setResumed] = useState(0);
  const goals = useInfinite((offset, limit) => api.goals(OPEN_GOALS, offset, limit), 25, "open-goals");
  const waiting = useInfinite((offset, limit) => api.tasks({ statuses: NEEDS_YOU }, offset, limit), 25, `needs-you-${resumed}`);
  // Running tasks held for budget wait on an operator too: they head the list.
  const held = useInfinite((offset, limit) => api.tasks({ waiting: true }, offset, limit), 25, "held");
  const running = useInfinite((offset, limit) => api.tasks({ statuses: ["running"] }, offset, limit), 1, "running");
  const queued = useInfinite((offset, limit) => api.tasks({ statuses: ["queued", "blocked"] }, offset, limit), 1, "queued");
  // A goal past its budget waits on an operator too: to raise it, or to let it stand.
  const [pastBudget, setPastBudget] = useState<Goal[]>([]);
  useEffect(() => {
    api.goals(["active", "budget_exhausted"], 0, 200).then(
      (page) => setPastBudget(page.items.filter((g) => budgetStanding(g) !== "ok")),
      () => setPastBudget([]),
    );
  }, [resumed]);

  const needsYou = useRef<HTMLDivElement | null>(null);
  const needing = waiting.total === null ? null : waiting.total + (held.total ?? 0) + pastBudget.length;
  // The failed and unresolved tasks by what went wrong, so the same failure is said, and fixed, once.
  const failed = groupFailures(
    waiting.items
      .filter((t) => t.status !== "ready")
      .map((t) => ({ key: t.id, name: t.title, taskId: t.id, issueRef: t.external_ref, status: t.status, reason: t.status_reason, max_usd: t.max_usd })),
  );

  return (
    <>
      <PageHeader title="Overview" description="What is running, what waits on you, and the goals in flight." />
      <KillSwitchPanel />

      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} mb="lg">
        <Stat label="Running" value={running.total} to="/tasks?view=tiles" link="Watch them"
          note={running.total ? "Tasks an agent is working on now." : "No agent is working. A task starts when a goal has an open issue and a free place."} />
        <Stat label="Queued or blocked" value={queued.total} to="/tasks?show=waiting" link="See why each waits"
          note={queued.total ? "Tasks that have not started: each says what holds it." : "Nothing waits to start."} />
        <Stat label="Needs you" value={needing} link={needing ? "See what to do" : undefined} alert={!!needing}
          onClick={needing ? () => needsYou.current?.scrollIntoView({ behavior: "smooth", block: "start" }) : undefined}
          note={needsYouNote(waiting.items, held.total ?? 0, pastBudget.length, needing)} />
        <Stat label="Open goals" value={goals.total} to="/goals" link="All goals"
          note={goals.total ? "Goals taking in issues or waiting on you." : "No goal is open: create one to start work."} />
      </SimpleGrid>

      <ReviewsInProgress />
      <TodaySpend />
      <PullRequestsRow />

      <div ref={needsYou} />
      <SectionTitle>Needs you</SectionTitle>
      <ErrorAlert error={waiting.error} />
      {failed.length > 0 && (
        <Paper withBorder p="md" mb="md" data-testid="needs-you-failures" style={{ borderColor: "var(--mantine-color-red-6)" }}>
          <FailureGroups groups={failed} onChanged={() => setResumed((n) => n + 1)} />
        </Paper>
      )}
      {waiting.items.length + held.items.length + pastBudget.length ? (
        <DataTable head={["Task", "Status", "Issue", "Pull request", "What to do"]}>
          {pastBudget.map((g) => (
            <Table.Tr key={g.id} data-testid={`past-budget-${g.id}`}>
              <Table.Td><TitleLink to={`/goals/${g.id}`} title={goalTitle(g)} id={g.id} /></Table.Td>
              <Table.Td><BudgetMark goal={g} /></Table.Td>
              <Table.Td />
              <Table.Td />
              <Table.Td>
                <Group gap="sm" wrap="nowrap">
                  <Text size="sm">The goal has spent ${Number(g.spent_usd ?? 0).toFixed(2)} of ${Number(g.max_usd).toFixed(2)}: {budgetWords(g)}</Text>
                  {budgetStops(g) && <ResumeBudget goalId={g.id} goal={g} size="xs" variant="light" onResumed={() => setResumed((n) => n + 1)} />}
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
          {[...held.items, ...waiting.items].map((t) => (
            <Table.Tr key={t.id}>
              <Table.Td><TitleLink to={`/tasks/${t.id}`} title={t.title} id={t.id} /></Table.Td>
              <Table.Td>{t.waiting_on === "budget" ? (
                  <Badge color="orange" variant="light" tt="none">out of budget</Badge>
                ) : t.waiting_on === "operator_review" ? (
                  <Badge color="violet" variant="light" tt="none">waits for {t.review_claimed_by ?? "an operator"}'s review</Badge>
                ) : t.waiting_on === "review" ? (
                  <Badge color="orange" variant="light" tt="none">review waits</Badge>
                ) : (
                  <StatusBadge status={t.status} />
                )}</Table.Td>
              <Table.Td><IssueLink issueRef={t.external_ref} /></Table.Td>
              <Table.Td>{t.pr_url && <PrLink url={t.pr_url} />}</Table.Td>
              <Table.Td>
                <Group gap="sm" wrap="nowrap">
                  <Text size="sm">{whatToDo(t)}</Text>
                  {t.waiting_on === "budget" && t.goal_id && (t.status_reason ?? "").includes("goal's budget") && (
                    <ResumeBudget goalId={t.goal_id} size="xs" variant="light" onResumed={() => setResumed((n) => n + 1)} />
                  )}
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
      ) : (
        waiting.total !== null && <Empty>Nothing waits on you.</Empty>
      )}
      <div ref={waiting.sentinel} />

      <SectionTitle right={<Button component={Link} to="/goals/new" size="xs" variant="light">New goal</Button>}>Open goals</SectionTitle>
      <ErrorAlert error={goals.error} />
      {goals.items.length ? (
        <DataTable head={["Goal", "Status", "Label", "Repos", "Auto-merge", "Created"]}>
          {goals.items.map((g) => (
            <Table.Tr key={g.id}>
              <Table.Td maw={420}><TitleLink to={`/goals/${g.id}`} title={goalTitle(g)} id={g.id} /></Table.Td>
              <Table.Td><StatusBadge status={goalStatus(g)} /></Table.Td>
              <Table.Td>{g.label ? <Badge variant="outline" tt="none" radius="sm">{g.label}</Badge> : <Text size="xs" c="dimmed">Picked issues</Text>}</Table.Td>
              <Table.Td>{g.repos.map((r) => <Text ff="monospace" size="xs" key={r}>{r}</Text>)}</Table.Td>
              <Table.Td><Text size="sm">{g.auto_merge ? "On" : "Off"}</Text></Table.Td>
              <Table.Td><Text size="sm" c="dimmed">{when(g.created_at)}</Text></Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
      ) : (
        goals.total !== null && <Empty>No open goals. <Anchor component={Link} to="/goals/new">Create one.</Anchor></Empty>
      )}
      <div ref={goals.sentinel} />
    </>
  );
}

// What a task in the Needs you list waits for, as an instruction.
function whatToDo(t: Task): string {
  if (t.waiting_on === "budget") return `${moneyText(t.status_reason) ?? "It reached its spend limit"}. Open it to raise the limit or end it.`;
  if (t.waiting_on === "operator_review") return "An operator's review is in progress: open it to finish the review.";
  if (t.waiting_on === "review") return "Its review asks for changes and waits for you: open it to dismiss findings or let the fix run start.";
  if (t.status === "ready") return "Its review is clean and its pull request is open: merge it.";
  const diagnosis = diagnose({ status: t.status, reason: t.status_reason });
  return diagnosis ? `${diagnosis.summary}. ${diagnosis.fix}` : moneyText(t.status_reason) ?? t.status;
}

// A number on the Overview with what it means and where to act on it. The whole tile is the link.
function Stat(props: { label: string; value: number | null; note?: string; to?: string; link?: string; onClick?: () => void; alert?: boolean }) {
  const body = (
    <>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{props.label}</Text>
      <Group justify="space-between" align="flex-end" mt={6}>
        <Text fz={32} fw={650} lh={1} c={props.alert ? "red" : undefined}>{props.value ?? "–"}</Text>
        {props.link && <Text size="sm" c="var(--mantine-color-anchor)">{props.link}</Text>}
      </Group>
      {props.note && <Text size="xs" c="dimmed" mt={8}>{props.note}</Text>}
    </>
  );
  const style = { cursor: props.to || props.onClick ? "pointer" : "default", textDecoration: "none", color: "inherit", textAlign: "left" as const };
  if (props.to) return <Card withBorder padding="lg" component={Link} to={props.to} style={style} aria-label={`${props.label}: ${props.value ?? "loading"}. ${props.link ?? ""}`}>{body}</Card>;
  if (props.onClick) return <Card withBorder padding="lg" component="button" type="button" onClick={props.onClick} style={style} aria-label={`${props.label}: ${props.value ?? "loading"}. ${props.link ?? ""}`}>{body}</Card>;
  return <Card withBorder padding="lg">{body}</Card>;
}

// The kill switch's state, and for an admin the control that flips it.
export function KillSwitchPanel({ canChange = false }: { canChange?: boolean }) {
  const [state, setState] = useState<KillSwitch | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.killSwitch().then(setState, (err) => setError(String(err.message ?? err)));
  }, []);

  async function flip() {
    if (!state) return;
    setBusy(true);
    try {
      setState(await api.setKillSwitch(!state.engaged, reason || (state.engaged ? "lifted" : "stopped from the console")));
      setReason("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorAlert error={`Kill switch: ${error}`} />;
  if (!state) return null;
  return (
    <Paper mb="lg" p="md" style={state.engaged ? { borderColor: "var(--mantine-color-red-6)" } : undefined}>
      <Group justify="space-between" wrap="wrap" gap="md">
        <Group gap="sm" wrap="nowrap">
          {state.engaged && <IconAlertTriangle color="var(--mantine-color-red-6)" />}
          <div>
            <Text fw={600}>
              {state.engaged
                ? "Kill switch is on: nothing runs"
                : state.draining
                  ? "Draining for a deploy: no new task starts, and running ones finish"
                  : "Kill switch is off"}
            </Text>
            <Text size="xs" c="dimmed">
              {state.changed_by}, {when(state.changed_at)}{state.reason ? `: ${state.reason}` : ""}
            </Text>
          </div>
        </Group>
        {canChange && <Group gap="sm" wrap="wrap" style={{ flex: "1 1 360px", justifyContent: "flex-end" }}>
          <TextInput placeholder="Reason" value={reason} onChange={(e) => setReason(e.currentTarget.value)} style={{ flex: "1 1 200px", maxWidth: 360 }} />
          <Button
            color={state.engaged ? "teal" : "red"}
            variant={state.engaged ? "filled" : "light"}
            leftSection={state.engaged ? <IconPlayerPlay size={16} /> : <IconPlayerStop size={16} />}
            loading={busy}
            onClick={flip}
          >
            {state.engaged ? "Lift the kill switch" : "Stop everything"}
          </Button>
        </Group>}
      </Group>
    </Paper>
  );
}

// Today's spend across every goal, and the five tasks that cost the most today, from today's attempts.
export function TodaySpend() {
  const [attempts, setAttempts] = useState<MhEvent[] | null>(null);
  useEffect(() => {
    api.recentEvents(["attempt_finished"], 2000).then(setAttempts, () => setAttempts(null));
  }, []);
  if (!attempts) return null;
  const today = new Date().toISOString().slice(0, 10);
  const byTask: Record<string, number> = {};
  for (const e of attempts) {
    if (!e.time_utc.startsWith(today) || !e.task_id) continue;
    byTask[e.task_id] = (byTask[e.task_id] ?? 0) + (Number(e.payload.spent_usd) || 0);
  }
  const total = Object.values(byTask).reduce((a, b) => a + b, 0);
  const top = Object.entries(byTask).sort((a, b) => b[1] - a[1]).slice(0, 5);
  return (
    <Paper p="md" mb="lg" withBorder data-testid="today-spend">
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={600}>Spent today</Text>
          <Text size="xl" fw={700}>${total.toFixed(2)}</Text>
        </div>
        {top.length > 0 && (
          <Stack gap={2}>
            <Text size="xs" c="dimmed">Most expensive today</Text>
            {top.map(([id, usd]) => (
              <Group key={id} gap="xs">
                <Anchor component={Link} to={`/tasks/${id}`} ff="monospace" size="xs">{id}</Anchor>
                <Text size="xs">${usd.toFixed(2)}</Text>
              </Group>
            ))}
            <Text size="xs" fw={650} pt={2} style={{ borderTop: "1px solid var(--mantine-color-default-border)" }} data-testid="today-top-total">
              These {top.length}: ${top.reduce((all, [, usd]) => all + usd, 0).toFixed(2)}
            </Text>
          </Stack>
        )}
      </Group>
    </Paper>
  );
}
