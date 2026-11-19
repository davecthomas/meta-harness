import { useCallback, useEffect, useState } from "react";
import { Alert, Anchor, Button, Collapse, Group, List, Paper, Stack, Text, ThemeIcon, Timeline } from "@mantine/core";
import { IconCompass, IconRefresh } from "@tabler/icons-react";
import { api } from "../../api";
import { issueUrl, refLabel } from "../trackers/refs";
import { when } from "../../components/ui";

// One update the planner wrote for the goal: where it stands, what changed, what needs the operator and what
// happens next.
export interface PlannerUpdate {
  number: number;
  headline: string;
  body: string;
  needs_you: { what: string; ref: string }[];
  next: string;
  created_at: string;
}

export interface PlannerUpdatesRead {
  updates?: PlannerUpdate[];
  enabled?: boolean;
  // Why the latest update could not be written, when it could not.
  error?: string | null;
}

const REFRESH_MS = 15000;

// How long ago, in the words a person would use.
export function since(iso: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  return when(iso);
}

// What an update asks of the operator, each with the issue it is about when it names one.
function NeedsYou({ needs }: { needs: PlannerUpdate["needs_you"] }) {
  if (!needs.length) return null;
  return (
    <Alert color="yellow" variant="light" p="xs" title={needs.length === 1 ? "Needs you" : `Needs you: ${needs.length} things`} data-testid="planner-needs-you">
      <List size="sm" spacing={2}>
        {needs.map((need, i) => {
          const url = need.ref ? issueUrl(need.ref) : null;
          return (
            <List.Item key={i}>
              {need.what}
              {need.ref && (
                <>
                  {" "}
                  {url ? <Anchor href={url} target="_blank" rel="noreferrer" size="sm">{refLabel(need.ref)}</Anchor> : <Text span size="sm" c="dimmed">{refLabel(need.ref)}</Text>}
                </>
              )}
            </List.Item>
          );
        })}
      </List>
    </Alert>
  );
}

// The planner keeping the operator up to date, at the head of the goal's page: its latest update in plain
// language (where the goal stands, what changed, what needs the operator, what happens next), the ones before
// it, and a way to ask for one now. open: the goal is not closed, so its updates are reread as it moves.
// Nothing is shown when updates are off, or for a goal that never had one and will not get one.
export function PlannerUpdates({ goalId, open }: { goalId: string; open: boolean }) {
  const [read, setRead] = useState<PlannerUpdatesRead | null>(null);
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [earlier, setEarlier] = useState(false);
  const [, setNow] = useState(0);

  const load = useCallback(() => api.goalUpdates(goalId).then(setRead, () => undefined), [goalId]);

  useEffect(() => {
    void load();
    if (!open) return;
    const refresh = () => {
      if (document.hidden) return;
      void load();
      setNow(Date.now());
    };
    const timer = setInterval(refresh, REFRESH_MS);
    // A tab that was out of view reads the latest as soon as it is looked at again.
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load, open]);

  async function ask() {
    setAsking(true);
    setRefused(null);
    try {
      await api.askForGoalUpdate(goalId);
      await load();
    } catch (err) {
      setRefused(err instanceof Error ? err.message : String(err));
    } finally {
      setAsking(false);
    }
  }

  const updates = read?.updates ?? [];
  if (!read || read.enabled === false || !Array.isArray(read.updates)) return null;
  if (!updates.length && !open) return null;
  const [latest, ...before] = updates;

  return (
    <Paper withBorder p="lg" mb="md" radius="md" data-testid="planner-updates" style={{ borderLeft: "5px solid var(--mantine-color-indigo-6)" }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap" mb={latest ? "xs" : 0}>
        <Group gap="sm" wrap="nowrap" align="flex-start">
          <ThemeIcon size={38} radius="xl" variant="light" color="indigo" mt={2}><IconCompass size={22} /></ThemeIcon>
          <Stack gap={2}>
            <Text size="xs" tt="uppercase" fw={700} c="indigo" style={{ letterSpacing: 0.4 }}>
              From your planner{latest ? ` · ${since(latest.created_at)}` : ""}
            </Text>
            {latest ? (
              <Text size="xl" fw={650} lh={1.3} data-testid="planner-headline">{latest.headline}</Text>
            ) : (
              <Text size="sm">Your planner is looking at this goal. Its first update is written within a couple of minutes.</Text>
            )}
          </Stack>
        </Group>
        {open && (
          <Button size="xs" variant="subtle" leftSection={<IconRefresh size={14} />} loading={asking} onClick={() => void ask()}>
            Ask for an update
          </Button>
        )}
      </Group>
      {latest && (
        <Stack gap="sm" pl={50}>
          <Text size="md" style={{ whiteSpace: "pre-wrap" }}>{latest.body}</Text>
          <NeedsYou needs={latest.needs_you ?? []} />
          {latest.next && (
            <Text size="sm"><Text span fw={650}>Next: </Text>{latest.next}</Text>
          )}
        </Stack>
      )}
      {refused && <Alert color="red" variant="light" mt="sm" p="xs" data-testid="planner-update-refused">{refused}</Alert>}
      {read.error && !refused && (
        <Text size="xs" c="dimmed" mt="sm" pl={50}>
          The planner could not write its latest update ({read.error}). It tries again every minute; Ask for an update tries now.
        </Text>
      )}
      {before.length > 0 && (
        <Stack gap="xs" mt="sm" pl={50}>
          <Anchor component="button" type="button" size="sm" style={{ alignSelf: "flex-start" }} onClick={() => setEarlier((v) => !v)} aria-expanded={earlier}>
            {earlier ? "Hide earlier updates" : before.length === 1 ? "1 earlier update" : `${before.length} earlier updates`}
          </Anchor>
          <Collapse expanded={earlier}>
            <Timeline bulletSize={12} lineWidth={2} color="indigo">
              {before.map((update) => (
                <Timeline.Item key={update.number} title={<Text size="sm" fw={600}>{update.headline}</Text>}>
                  <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{update.body}</Text>
                  <Text size="xs" c="dimmed">{when(update.created_at)}</Text>
                </Timeline.Item>
              ))}
            </Timeline>
          </Collapse>
        </Stack>
      )}
    </Paper>
  );
}
