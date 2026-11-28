import { useCallback, useEffect, useState } from "react";
import { Anchor, Badge, Button, Center, Group, Loader, Paper, SimpleGrid, Stack, Tabs, Text } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ConfigSummary, type RepoHealth } from "../../api";
import { ErrorAlert, PageHeader, when } from "../../components/ui";
import { KindPanel } from "../config/ConfigPage";
import { span } from "../tasks/path";
import { BaseBadge } from "./RepoMarker";
import { useReposHealth } from "./health";

// Everything about the repos Muxxy works in, in one place: how each one's own checks and deploy stand (Health),
// and the settings Muxxy keeps for it (Settings). Goals stay the unit of work, and this page starts none. It
// shows no code and lists no pull requests: the code host does both, and every name here links there.

const Label = ({ children }: { children: string }) => (
  <Text size="xs" fw={600} c="dimmed" tt="uppercase">{children}</Text>
);

const settingsOf = (repo: string) => `/repos?tab=settings&repo=${encodeURIComponent(repo)}`;

function FixAction({ repo }: { repo: RepoHealth }) {
  const fix = repo.fix;
  if (!fix || !fix.open) return null;
  if (fix.task_id) return <Button component={Link} to={`/tasks/${fix.task_id}`} size="xs" color="teal">Open the fix task</Button>;
  // Asked of the goal's operator, or started and not yet a task: the goal's page has the button and the issue.
  return <Button component={Link} to={`/goals/${fix.goal_id}`} size="xs" color="teal">{fix.asked ? "Fix it on the goal's page" : "Open the goal"}</Button>;
}

function Base({ repo }: { repo: RepoHealth }) {
  const base = repo.base;
  if (!base) return <Text size="sm" c="dimmed">Not checked yet</Text>;
  const onHost = base.checks.length > 0 && base.checks.every((c) => c.on === "code_host");
  return (
    <>
      {base.state === "failing" ? (
        <>
          <Text size="sm" c="orange">Fails in the sandbox</Text>
          {base.failing.map((command) => <Text key={command} size="xs" ff="monospace">{command}</Text>)}
        </>
      ) : (
        <Text size="sm">{onHost ? "Passes on the code host, so not run in the sandbox" : "Passes in the sandbox"}</Text>
      )}
      <Text size="xs" c="dimmed">
        at {base.commit.slice(0, 7)}{base.state === "failing" && base.found_by ? `, found by "${base.found_by}"` : ""}
      </Text>
    </>
  );
}

function RepoBox({ repo }: { repo: RepoHealth }) {
  const failing = repo.base?.state === "failing";
  const idle = repo.goals.length === 0;
  const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  return (
    <Paper withBorder p="md" radius="md" data-testid={`repo-health-${repo.repo}`} style={failing ? { borderColor: "var(--mantine-color-orange-8)" } : undefined}>
      <Stack gap="sm">
        <Group justify="space-between" wrap="wrap" gap="xs">
          <Group gap="xs" wrap="wrap">
            <Anchor href={`https://github.com/${repo.repo}`} target="_blank" rel="noreferrer" ff="monospace" fw={500}>{repo.repo}</Anchor>
            {repo.base ? <BaseBadge repo={repo} link={false} /> : <Badge color="gray" variant="light" tt="none" radius="sm">Not checked yet</Badge>}
          </Group>
          <Group gap="md">
            <Anchor component={Link} to={settingsOf(repo.repo)} size="sm">{repo.registered ? "Edit settings" : "Add its settings"}</Anchor>
            <FixAction repo={repo} />
          </Group>
        </Group>
        {idle && !repo.base ? (
          <Text size="sm" c="dimmed">No goal is working here. A task's next start checks its base.</Text>
        ) : (
          <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="md">
            <Stack gap={2}>
              <Label>Base</Label>
              <Base repo={repo} />
            </Stack>
            <Stack gap={2}>
              <Label>Checks</Label>
              <Text size="sm">{repo.usual_seconds ? `Usually ${span(repo.usual_seconds * 1000)}` : "No usual time yet"}</Text>
              {repo.reruns.length === 0 ? (
                <Text size="xs" c="dimmed">No check rerun this week</Text>
              ) : (
                repo.reruns.map((r) => <Text key={r.check} size="xs" c="dimmed">{r.check}: rerun {count(r.times, "time", "times")} this week</Text>)
              )}
            </Stack>
            <Stack gap={2}>
              <Label>Work here</Label>
              {idle && <Text size="sm" c="dimmed">No goal is working here</Text>}
              {repo.goals.map((g) => <Anchor key={g.id} component={Link} to={`/goals/${g.id}`} size="sm" lineClamp={1}>{g.title || g.id}</Anchor>)}
              {!idle && <Text size="xs" c="dimmed">{count(repo.tasks, "task", "tasks")} running, {count(repo.pulls, "open pull request", "open pull requests")}</Text>}
            </Stack>
            <Stack gap={2}>
              <Label>Live</Label>
              {repo.live ? (
                <>
                  <Text size="sm">{repo.live.at ? when(repo.live.at) : "Live"}</Text>
                  <Anchor component={Link} to={`/tasks/${repo.live.task_id}`} size="xs" lineClamp={2}>{repo.live.title || repo.live.task_id}</Anchor>
                </>
              ) : repo.deploy_check ? (
                <Text size="sm" c="dimmed">Nothing has gone live through Muxxy yet</Text>
              ) : (
                <>
                  <Text size="sm" c="dimmed">No deploy check named</Text>
                  <Anchor component={Link} to={settingsOf(repo.repo)} size="xs">Name one</Anchor>
                </>
              )}
            </Stack>
          </SimpleGrid>
        )}
        {repo.registered && (
          <Group gap="lg" wrap="wrap" pt="xs" style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
            <Text size="xs" c="dimmed">Standing checks: <Text span ff="monospace" inherit>{repo.default_checks.join(", ") || "none"}</Text></Text>
            <Text size="xs" c="dimmed">Deploy check: {repo.deploy_check || "none"}</Text>
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

function Health() {
  const { health, error } = useReposHealth();
  if (!health) return error ? <ErrorAlert error={`${error}. Reload the page to try again.`} /> : <Center h={200}><Loader /></Center>;
  if (health.repos.length === 0) {
    return (
      <Paper withBorder p="lg" radius="md" data-testid="no-repo-health">
        <Text fw={600}>No repository yet</Text>
        <Text size="sm" c="dimmed">A repository shows here once a goal works in it, or once it has settings.</Text>
      </Paper>
    );
  }
  return <Stack gap="md">{health.repos.map((repo) => <RepoBox key={repo.repo} repo={repo} />)}</Stack>;
}

// The repositories' settings: the Configuration kind, with its review, its save as a version and its history.
function Settings() {
  const [summary, setSummary] = useState<ConfigSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    api.configKinds().then(
      (found) => setSummary(found.kinds.find((k) => k.kind === "repos") ?? null),
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);
  useEffect(reload, [reload]);
  if (error) return <ErrorAlert error={`${error}. Reload the page to try again.`} />;
  if (!summary) return <Center h={200}><Loader /></Center>;
  return <KindPanel summary={summary} onSaved={reload} maxSandboxes={null} />;
}

export function ReposPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "settings" ? "settings" : "health";
  return (
    <>
      <PageHeader
        title="Repos"
        actions={<Button component={Link} to="/repos?tab=settings&add=1" variant="default" leftSection={<IconPlus size={16} />}>Add repository</Button>}
      />
      <Tabs value={tab} onChange={(value) => setParams(value === "settings" ? { tab: "settings" } : {})} keepMounted={false}>
        <Tabs.List mb="lg">
          <Tabs.Tab value="health">Health</Tabs.Tab>
          <Tabs.Tab value="settings">Settings</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="health"><Health /></Tabs.Panel>
        <Tabs.Panel value="settings"><Settings /></Tabs.Panel>
      </Tabs>
    </>
  );
}
