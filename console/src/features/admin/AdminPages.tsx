import { useEffect, useState } from "react";
import { Anchor, Code, Paper, SimpleGrid, Stack, Table, Text } from "@mantine/core";
import { api, type MhEvent } from "../../api";
import { DataTable, Empty, ErrorAlert, Facts, PageHeader, when } from "../../components/ui";

const REPO_URL = "https://github.com/davecthomas/meta-harness";

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

const AUDITED = ["config_changed", "kill_switch_changed", "user_invited", "user_role_changed", "user_deprovisioned"];

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
      {events && events.length === 0 && <Empty>No change has been made yet. A change to the configuration, the kill switch or a user's access is recorded here, with who made it.</Empty>}
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
  if (e.event_type === "user_invited") return `Invited ${p.email} as ${p.role === "admin" ? "an admin" : "a regular user"}`;
  if (e.event_type === "user_role_changed") return `Made ${p.email} ${p.to === "admin" ? "an admin" : "a regular user"}`;
  if (e.event_type === "user_deprovisioned") return `Ended ${p.email}'s access`;
  if (e.event_type === "kill_switch_changed") return `${p.engaged ? "Turned the kill switch on" : "Lifted the kill switch"}${p.reason ? `: ${p.reason}` : ""}`;
  const verb = p.origin === "rollback" ? `rolled ${p.kind} back to version ${p.rolled_back_from}, as` : `saved ${p.kind}`;
  return `${verb} version ${p.version}${p.note ? `: ${p.note}` : ""}`;
}

export function CloudPage() {
  // Where the deployment runs, as the edge reports it (deploy/environment.json, through the release stack).
  const [where, setWhere] = useState<{ project: string; region: string | null; zone: string | null } | null>(null);
  useEffect(() => {
    api.deploys().then(
      (d) => setWhere(d.project ? { project: d.project, region: d.region ?? null, zone: d.zone ?? null } : null),
      () => setWhere(null),
    );
  }, []);
  const p = where?.project;
  const links: [string, string, string][] = [
    ...(p
      ? ([
          ...(where?.zone ? [["The VM", `https://console.cloud.google.com/compute/instancesDetail/zones/${where.zone}/instances/mh-vm?project=${p}`, "mh-vm, every control-plane service and the per-run sandboxes"]] : []),
          ...(where?.region ? [["The edge", `https://console.cloud.google.com/run/detail/${where.region}/mh-edge?project=${p}`, "Cloud Run service mh-edge: the console and /v1 routing"]] : []),
          ["Logs", `https://console.cloud.google.com/logs/query?project=${p}`, "Logs Explorer for the project"],
          ["Secrets", `https://console.cloud.google.com/security/secret-manager?project=${p}`, "Secret Manager: provider keys, the service key, GitHub App keys"],
        ] as [string, string, string][])
      : []),
    ["Deploy runs", `${REPO_URL}/actions/workflows/deploy.yml`, "GitHub Actions: every deploy, its plan and its result"],
  ];
  const description = p
    ? `The project ${p}, in the Google Cloud console. Each link opens a new tab.`
    : "The edge has not said which Google Cloud project it runs in, so only the deploy runs are linked.";
  return (
    <>
      <PageHeader title="Google Cloud" description={description} />
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
