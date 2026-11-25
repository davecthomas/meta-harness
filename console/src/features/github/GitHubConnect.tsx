import { useEffect, useState } from "react";
import { Alert, Anchor, Badge, Button, Group, Loader, MultiSelect, Paper, Stack, Text, Title } from "@mantine/core";
import { IconBrandGithub } from "@tabler/icons-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type GitHubConnection, type Reachable } from "../../api";
import { ErrorAlert, PageHeader } from "../../components/ui";

// Where the operator was when they pressed Connect, kept across the trip to GitHub and back.
const RETURN_TO = "mh:github-return-to";

const say = (err: unknown) => (err instanceof Error ? err.message : String(err));

// Sends the browser to GitHub to sign in and consent. GitHub sends it back to /github/connected.
export async function startConnect(): Promise<void> {
  const { url } = await api.githubConnect();
  try {
    window.sessionStorage.setItem(RETURN_TO, window.location.pathname + window.location.search);
  } catch {
    // With no session storage the operator lands on Settings after connecting.
  }
  window.location.assign(url);
}

// The one button that connects the operator's GitHub account, wherever the console needs it. label: what it
// says, "Connect GitHub" unless the connection is broken.
export function ConnectButton({ label = "Connect GitHub", size = "sm" }: { label?: string; size?: "xs" | "sm" }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Stack gap={4}>
      <Group>
        <Button
          size={size}
          leftSection={<IconBrandGithub size={16} />}
          loading={busy}
          onClick={() => {
            setBusy(true);
            setError(null);
            startConnect().catch((err) => {
              setBusy(false);
              setError(`${say(err)}. Press ${label} to try again.`);
            });
          }}
        >
          {label}
        </Button>
      </Group>
      <ErrorAlert error={error} />
    </Stack>
  );
}

// The operator's GitHub connection, on their Settings page: who they are connected as, and the way to connect,
// reconnect or disconnect. A goal they create acts with what this account can reach on GitHub.
export function GitHubSection() {
  const [found, setFound] = useState<GitHubConnection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.githubConnection().then(setFound, (err) => setError(`${say(err)}. Reload the page to try again.`));
  }, []);
  const disconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      setFound(await api.githubDisconnect());
    } catch (err) {
      setError(`${say(err)}. Press Disconnect to try again.`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Paper p="md" withBorder maw={720} mb="lg" data-testid="github-section">
      <Title order={4} mb="sm">GitHub</Title>
      <Text size="sm" c="dimmed" mb="md">Connect your GitHub account once. A goal you create can then work in any repo you can push to, with no setup for each repo.</Text>
      <ErrorAlert error={error} />
      {!found && !error && <Loader size="sm" />}
      {found && !found.enabled && <Alert color="gray" variant="light">{found.why_off}</Alert>}
      {found?.enabled && found.state === "none" && <ConnectButton />}
      {found?.enabled && found.state === "broken" && (
        <Stack gap="xs">
          <Text size="sm">GitHub no longer accepts the connection as <b>{found.login}</b>. Your goals stop where they next need GitHub until you reconnect.</Text>
          <ConnectButton label="Reconnect GitHub" />
        </Stack>
      )}
      {found?.connected && (
        <Stack gap="md">
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Group gap="xs">
              <IconBrandGithub size={18} />
              <Text size="sm">Connected as <b>{found.login}</b></Text>
              <Badge variant="light" color="teal" tt="none" radius="sm">Connected</Badge>
            </Group>
            <Button size="xs" variant="default" color="red" loading={busy} onClick={() => void disconnect()}>Disconnect</Button>
          </Group>
          <Owners chosen={found.owners} onSaved={setFound} />
        </Stack>
      )}
    </Paper>
  );
}

// Which accounts and organizations Muxxy uses, picked from the ones the connection reaches. None picked: all.
// Each change saves at once.
function Owners({ chosen, onSaved }: { chosen: string[]; onSaved: (found: GitHubConnection) => void }) {
  const [reach, setReach] = useState<Reachable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    api.githubReachable().then(setReach, (err) => setError(`${say(err)}. Reload the page to try again.`));
  }, []);
  const save = async (owners: string[]) => {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.githubChooseOwners(owners));
    } catch (err) {
      setError(`${say(err)}. Pick again to retry.`);
    } finally {
      setSaving(false);
    }
  };
  const all = reach?.owners ?? [];
  // While the accounts are read, the field shows what is saved and waits; an error leaves it usable.
  const used = all.filter((o) => chosen.length === 0 || chosen.includes(o.name)).reduce((sum, o) => sum + o.repos, 0);
  // A saved name the connection no longer reaches stays in the list, marked, so it can be removed.
  const gone = chosen.filter((name) => reach !== null && !all.some((o) => o.name === name));
  return (
    <Stack gap={6} data-testid="github-owners">
      <MultiSelect
        label="Accounts and organizations Muxxy uses"
        description={reach ? `Muxxy offers ${used} ${used === 1 ? "repo" : "repos"}. Pick none to use every account you can reach.` : "Reading what your connection reaches."}
        placeholder={chosen.length === 0 ? "All you can reach" : undefined}
        data={[...all.map((o) => ({ value: o.name, label: `${o.name} (${o.repos})` })), ...gone.map((name) => ({ value: name, label: `${name} (no longer reached)` }))]}
        value={chosen}
        onChange={(owners) => void save(owners)}
        disabled={saving || (reach === null && !error)}
        searchable
        clearable
        nothingFoundMessage="Your connection reaches no account by that name."
        maw={520}
      />
      <ErrorAlert error={error} />
    </Stack>
  );
}

// Where GitHub sends the browser back after the operator consents: the code and state are handed to the
// service with the console's own session, so the connection is this operator's. Then on to where they were.
export function GitHubConnected() {
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const code = search.get("code");
  const state = search.get("state");
  const refused = search.get("error_description") ?? search.get("error");
  useEffect(() => {
    if (!code || !state) return;
    let back = "/settings";
    try {
      back = window.sessionStorage.getItem(RETURN_TO) || back;
      window.sessionStorage.removeItem(RETURN_TO);
    } catch {
      // No session storage: Settings shows the connection.
    }
    api.githubFinish(code, state).then(() => navigate(back, { replace: true }), (err) => setError(say(err)));
  }, [code, state, navigate]);
  const why = refused ? `GitHub did not connect: ${refused}.` : !code || !state ? "This page finishes a GitHub sign-in, and this address has none to finish." : error;
  return (
    <>
      <PageHeader title="Connecting GitHub" />
      {why ? (
        <Stack gap="sm" maw={720}>
          <ErrorAlert error={why} />
          <ConnectButton label="Connect GitHub" />
          <Anchor component={Link} to="/settings" size="sm">Back to Settings</Anchor>
        </Stack>
      ) : (
        <Group gap="sm"><Loader size="sm" /><Text size="sm">Finishing the connection.</Text></Group>
      )}
    </>
  );
}
