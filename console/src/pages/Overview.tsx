import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Button, Card, Group, Paper, SimpleGrid, Table, Text, TextInput } from "@mantine/core";
import { IconAlertTriangle, IconPlayerStop, IconPlayerPlay } from "@tabler/icons-react";
import { api, goalStatus, type KillSwitch } from "../api";
import { useInfinite } from "../hooks";
import { DataTable, Empty, ErrorAlert, IssueLink, PageHeader, SectionTitle, StatusBadge, TitleLink, when } from "../components/ui";

const OPEN_GOALS = ["active", "paused", "stalled", "budget_exhausted"];
const NEEDS_YOU = ["ready", "failed", "review_unresolved"];

export function Overview() {
  const goals = useInfinite((offset, limit) => api.goals(OPEN_GOALS, offset, limit), 25, "open-goals");
  const waiting = useInfinite((offset, limit) => api.tasks({ statuses: NEEDS_YOU }, offset, limit), 25, "needs-you");
  const running = useInfinite((offset, limit) => api.tasks({ statuses: ["running"] }, offset, limit), 1, "running");
  const queued = useInfinite((offset, limit) => api.tasks({ statuses: ["queued", "blocked"] }, offset, limit), 1, "queued");

  return (
    <>
      <PageHeader title="Overview" description="What is running, what waits on you, and the goals in flight." />
      <KillSwitchPanel />

      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} mb="lg">
        <Stat label="Running" value={running.total} to="/tasks?view=tiles&size=16" link="Watch them" />
        <Stat label="Queued or blocked" value={queued.total} to="/tasks?show=waiting" link="See them" />
        <Stat label="Needs you" value={waiting.total} />
        <Stat label="Open goals" value={goals.total} to="/goals" link="All goals" />
      </SimpleGrid>

      <SectionTitle>Needs you</SectionTitle>
      <ErrorAlert error={waiting.error} />
      {waiting.items.length ? (
        <DataTable head={["Task", "Status", "Issue", "Pull request", "Why"]}>
          {waiting.items.map((t) => (
            <Table.Tr key={t.id}>
              <Table.Td><TitleLink to={`/tasks/${t.id}`} title={t.title} id={t.id} /></Table.Td>
              <Table.Td><StatusBadge status={t.status} /></Table.Td>
              <Table.Td><IssueLink issueRef={t.external_ref} /></Table.Td>
              <Table.Td>{t.pr_url && <Anchor href={t.pr_url} target="_blank" size="sm">Open</Anchor>}</Table.Td>
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
              <Table.Td maw={420}><TitleLink to={`/goals/${g.id}`} title={g.statement} id={g.id} /></Table.Td>
              <Table.Td><StatusBadge status={goalStatus(g)} /></Table.Td>
              <Table.Td><Badge variant="outline" tt="none" radius="sm">{g.label}</Badge></Table.Td>
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

function KillSwitchPanel() {
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
            <Text fw={600}>{state.engaged ? "Kill switch is on: nothing runs" : "Kill switch is off"}</Text>
            <Text size="xs" c="dimmed">
              {state.changed_by}, {when(state.changed_at)}{state.reason ? `: ${state.reason}` : ""}
            </Text>
          </div>
        </Group>
        <Group gap="sm" wrap="wrap" style={{ flex: "1 1 360px", justifyContent: "flex-end" }}>
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
        </Group>
      </Group>
    </Paper>
  );
}
