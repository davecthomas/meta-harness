import { useEffect, useState } from "react";
import { Anchor, Code, Paper, SimpleGrid, Stack, Table, Text } from "@mantine/core";
import { api, type MhEvent } from "../../api";
import { DataTable, Empty, ErrorAlert, Facts, PageHeader, when } from "../../components/ui";
import { KillSwitchPanel } from "../overview/Overview";

const REPO_URL = "https://github.com/davecthomas/meta-harness";

export function KillSwitchPage() {
  return (
    <>
      <PageHeader title="Kill switch" description="Stops every running task and admits none until it is lifted. Every change is recorded with who made it and why." />
      <KillSwitchPanel canChange />
    </>
  );
}

export function DeploysPage() {
  const [deployed, setDeployed] = useState<{ version: string; commit: string | null; revision: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.deploys().then(setDeployed, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  return (
    <>
      <PageHeader title="Deploys" description="Every merge to main deploys itself: the platform stack when it changed, then the VM and the edge at the merged commit." />
      <ErrorAlert error={error} />
      {deployed && (
        <Facts
          items={[
            ["Version", <Text size="sm">{deployed.version}</Text>],
            ["Commit, on the VM and the edge", deployed.commit ? <Anchor href={`${REPO_URL}/commit/${deployed.commit}`} target="_blank" ff="monospace" size="sm">{deployed.commit.slice(0, 12)}</Anchor> : <Text size="sm" c="dimmed">Not set here</Text>],
            ["Edge revision", <Code>{deployed.revision ?? "local"}</Code>],
            ["Deploy runs", <Anchor href={`${REPO_URL}/actions/workflows/deploy.yml`} target="_blank" size="sm">GitHub Actions</Anchor>],
          ]}
        />
      )}
    </>
  );
}

const AUDITED = ["config_changed", "kill_switch_changed"];

export function AuditPage() {
  const [events, setEvents] = useState<MhEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.recentEvents(AUDITED, 200).then((found) => setEvents([...found].reverse()), (err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  return (
    <>
      <PageHeader title="Audit" description="Configuration changes and kill switch changes, newest first, with who made them." />
      <ErrorAlert error={error} />
      {events && events.length === 0 && <Empty>Nothing recorded yet.</Empty>}
      {events && events.length > 0 && (
        <DataTable head={["When", "Who", "What"]}>
          {events.map((e) => (
            <Table.Tr key={e.event_id}>
              <Table.Td><Text size="sm" c="dimmed">{when(e.time_utc)}</Text></Table.Td>
              <Table.Td><Text size="sm">{e.actor ?? "the meta-harness"}</Text></Table.Td>
              <Table.Td><Text size="sm">{describeAudit(e)}</Text></Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
      )}
    </>
  );
}

export function describeAudit(e: MhEvent): string {
  const p = e.payload;
  if (e.event_type === "kill_switch_changed") return `${p.engaged ? "Turned the kill switch on" : "Lifted the kill switch"}${p.reason ? `: ${p.reason}` : ""}`;
  const verb = p.origin === "rollback" ? `rolled ${p.kind} back to version ${p.rolled_back_from}, as` : `saved ${p.kind}`;
  return `${verb} version ${p.version}${p.note ? `: ${p.note}` : ""}`;
}

export function CloudPage() {
  const [project, setProject] = useState<string | null>(null);
  useEffect(() => {
    api.deploys().then((d) => setProject(d.project ?? null), () => setProject(null));
  }, []);
  const p = project ?? "meta-harness-prod";
  const links: [string, string, string][] = [
    ["The VM", `https://console.cloud.google.com/compute/instancesDetail/zones/us-central1-a/instances/mh-vm?project=${p}`, "mh-vm, every control-plane service and the per-run sandboxes"],
    ["The edge", `https://console.cloud.google.com/run/detail/us-central1/mh-edge?project=${p}`, "Cloud Run service mh-edge: the console and /v1 routing"],
    ["Logs", `https://console.cloud.google.com/logs/query?project=${p}`, "Logs Explorer for the project"],
    ["Secrets", `https://console.cloud.google.com/security/secret-manager?project=${p}`, "Secret Manager: provider keys, the service key, GitHub App keys"],
    ["Deploy runs", `${REPO_URL}/actions/workflows/deploy.yml`, "GitHub Actions: every deploy, its plan and its result"],
  ];
  return (
    <>
      <PageHeader title="Google Cloud" description={`The project ${p}, in the Google Cloud console. Each link opens a new tab.`} />
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {links.map(([title, href, what]) => (
          <Paper key={title} p="md" withBorder>
            <Stack gap={4}>
              <Anchor href={href} target="_blank" fw={600}>{title}</Anchor>
              <Text size="sm" c="dimmed">{what}</Text>
            </Stack>
          </Paper>
        ))}
      </SimpleGrid>
    </>
  );
}
