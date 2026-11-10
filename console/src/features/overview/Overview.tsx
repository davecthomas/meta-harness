import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Button, Card, Group, Paper, SimpleGrid, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconAlertTriangle, IconPlayerStop, IconPlayerPlay } from "@tabler/icons-react";
import { goalTitle, api, goalStatus, type Activity, type KillSwitch, type MhEvent } from "../../api";
import { ActivityMap } from "../activity/ActivityPage";
import { useInfinite } from "../../hooks";
import { DataTable, Empty, ErrorAlert, IssueLink, PageHeader, PrLink, SectionTitle, StatusBadge, TitleLink, when } from "../../components/ui";

const OPEN_GOALS = ["active", "paused", "stalled", "budget_exhausted"];
const NEEDS_YOU = ["ready", "failed", "review_unresolved"];

export function Overview() {
  const goals = useInfinite((offset, limit) => api.goals(OPEN_GOALS, offset, limit), 25, "open-goals");
  const waiting = useInfinite((offset, limit) => api.tasks({ statuses: NEEDS_YOU }, offset, limit), 25, "needs-you");
  // Running tasks held for budget wait on an operator too: they head the list.
  const held = useInfinite((offset, limit) => api.tasks({ waiting: true }, offset, limit), 25, "held");
  const running = useInfinite((offset, limit) => api.tasks({ statuses: ["running"] }, offset, limit), 1, "running");
  const queued = useInfinite((offset, limit) => api.tasks({ statuses: ["queued", "blocked"] }, offset, limit), 1, "queued");

  return (
    <>
      <PageHeader title="Overview" description="What is running, what waits on you, and the goals in flight." />
      <KillSwitchPanel />

      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} mb="lg">
        <Stat label="Running" value={running.total} to="/tasks?view=tiles" link="Watch them" />
        <Stat label="Queued or blocked" value={queued.total} to="/tasks?show=waiting" link="See them" />
        <Stat label="Needs you" value={waiting.total === null ? null : waiting.total + (held.total ?? 0)} />
        <Stat label="Open goals" value={goals.total} to="/goals" link="All goals" />
      </SimpleGrid>

      <TodaySpend />
      <PullRequestsRow />

      <SectionTitle>Needs you</SectionTitle>
      <ErrorAlert error={waiting.error} />
      {waiting.items.length + held.items.length ? (
        <DataTable head={["Task", "Status", "Issue", "Pull request", "Why"]}>
          {[...held.items, ...waiting.items].map((t) => (
            <Table.Tr key={t.id}>
              <Table.Td><TitleLink to={`/tasks/${t.id}`} title={t.title} id={t.id} /></Table.Td>
              <Table.Td>{t.waiting_on === "budget" ? <Badge color="orange" variant="light" tt="none">out of budget</Badge> : <StatusBadge status={t.status} />}</Table.Td>
              <Table.Td><IssueLink issueRef={t.external_ref} /></Table.Td>
              <Table.Td>{t.pr_url && <PrLink url={t.pr_url} />}</Table.Td>
              <Table.Td><Text size="sm">{t.status === "ready" ? "Its pull request is open for you to merge" : t.status_reason ?? t.status}</Text></Table.Td>
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

function Stat(props: { label: string; value: number | null; to?: string; link?: string }) {
  return (
    <Card withBorder padding="lg">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{props.label}</Text>
      <Group justify="space-between" align="flex-end" mt={6}>
        <Text fz={32} fw={650} lh={1}>{props.value ?? "–"}</Text>
        {props.to && <Anchor component={Link} to={props.to} size="sm">{props.link}</Anchor>}
      </Group>
    </Card>
  );
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

// Every pull request in one row, by state, and how many are blocked or waiting on an operator now.
function PullRequestsRow() {
  const [activity, setActivity] = useState<Activity | null>(null);
  useEffect(() => {
    api.activity("goal").then(setActivity, () => setActivity(null));
  }, []);
  if (!activity || !activity.groups.some((g) => g.prs.length)) return null;
  const prs = activity.groups.flatMap((g) => g.prs);
  const blocked = prs.filter((p) => p.state === "blocked").length;
  const waiting = prs.filter((p) => p.state === "waiting").length;
  return (
    <>
      <SectionTitle right={<Anchor component={Link} to="/activity" size="sm">Activity</Anchor>}>Pull requests</SectionTitle>
      <Text size="sm" c="dimmed" mb="xs">{blocked} blocked · {waiting} waiting for you</Text>
      <Paper p="sm" mb="lg" withBorder>
        <ActivityMap activity={activity} view="state" compact />
      </Paper>
    </>
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
          </Stack>
        )}
      </Group>
    </Paper>
  );
}
