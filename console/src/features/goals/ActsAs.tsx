import { useEffect, useState } from "react";
import { Badge, Button, Group, Stack, Text } from "@mantine/core";
import { IconBrandGithub } from "@tabler/icons-react";
import { api, type Goal } from "../../api";
import { ErrorAlert } from "../../components/ui";
import { ConnectButton } from "../github/GitHubConnect";

// Whose GitHub access the goal uses, from GET /v1/goals/{id}/acts-as.
export interface ActsAsShown {
  acts_as: string | null;
  login: string | null;
  state: "connected" | "broken" | "none" | null;
  mine: boolean;
  can_take: boolean;
}

const say = (err: unknown) => (err instanceof Error ? err.message : String(err));

// The goal page's row for whose GitHub access the goal uses: the operator's account its pull requests come
// from, or the GitHub App. A connection that no longer works says so, with the button that fixes it. Another
// operator with a connection can take the goal over.
export function ActsAs({ goal, readOnly }: { goal: Goal; readOnly: boolean }) {
  const [found, setFound] = useState<ActsAsShown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.goalActsAs(goal.id).then(setFound, (err) => setError(`${say(err)}. Reload the page to try again.`));
  }, [goal.id]);
  const take = async () => {
    setBusy(true);
    setError(null);
    try {
      setFound(await api.goalActAsMe(goal.id));
    } catch (err) {
      setError(`${say(err)}.`);
    } finally {
      setBusy(false);
    }
  };
  if (!found) return error ? <ErrorAlert error={error} /> : <Text size="sm" c="dimmed">Reading…</Text>;
  const stopped = found.acts_as !== null && found.state !== "connected";
  return (
    <Stack gap={6} data-testid="acts-as">
      <Group gap="xs" wrap="wrap">
        <IconBrandGithub size={16} />
        {found.acts_as === null ? (
          <Text size="sm">The Muxxy GitHub App</Text>
        ) : (
          <Text size="sm"><b>{found.login ?? found.acts_as}</b>{found.mine ? " (you)" : ""}</Text>
        )}
        {stopped && <Badge variant="light" color="red" tt="none" radius="sm">{found.state === "broken" ? "GitHub refused the connection" : "Disconnected"}</Badge>}
        {!readOnly && found.can_take && <Button size="compact-xs" variant="default" loading={busy} onClick={() => void take()}>Act as me</Button>}
      </Group>
      <Text size="xs" c="dimmed">
        {found.acts_as === null
          ? "Its pull requests come from the app, and it works only in repos the app is installed on."
          : "Its clones, pushes and pull requests use this account's access."}
      </Text>
      {stopped && (
        <Stack gap={4}>
          <Text size="sm" c="red">The goal stops where it next needs GitHub.{found.mine ? " Reconnect to carry on." : " Its operator reconnects in Settings, or you take it over."}</Text>
          {found.mine && <ConnectButton label="Reconnect GitHub" size="xs" />}
        </Stack>
      )}
      <ErrorAlert error={error} />
    </Stack>
  );
}
