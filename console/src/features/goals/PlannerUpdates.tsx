import { useCallback, useEffect, useState } from "react";
import { Alert, Anchor, Badge, Button, Collapse, Group, Loader, Paper, Stack, Text, Timeline } from "@mantine/core";
import { IconArrowRight, IconRefresh } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { VoiceMark } from "../../components/Voice";
import { api } from "../../api";
import { when } from "../../components/ui";
import { NeedButton, type Need } from "./needActions";
import { reloadNeeds } from "../overview/needsYou";
import { usePlan } from "./PlanLink";

// The need's shape, its actions and the events a need's button sends live with the buttons (needActions.tsx).
// They are named here too, for the pages that have always read them from the planner's panel.
export { APPROVE_PLAN, OPEN_SETTINGS, PLAN_CHANGED, REPLAN, SHOW_ISSUES } from "./needActions";
export type { Need, NeedAction } from "./needActions";

// One update the planner wrote for the goal: where it stands, what changed, what needs the operator and what
// happens next.
export interface PlannerUpdate {
  number: number;
  headline: string;
  body: string;
  needs_you: Need[];
  next: string;
  created_at: string;
  // The plan version it was written about; absent on an update from before versions were kept.
  plan_version?: number | null;
}

export interface PlannerUpdatesRead {
  updates?: PlannerUpdate[];
  enabled?: boolean;
  // Why the latest update could not be written, when it could not.
  error?: string | null;
  // Where a planning goal's plan stands now: its version, and whether the planner is writing a new one.
  plan?: { version: number | null; replanning: boolean } | null;
  // What the goal needs now, from the one list every page reads (planner/needs.py). The panel's rows and
  // buttons are these, so they say what the Overview and the inbox say. Absent from an older planner.
  needs?: Need[];
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

// The actions that are about the plan itself.
const PLAN_ACTIONS: string[] = ["approve_plan", "fix_plan", "accept_fix"];

// The most reasons a fix shows before it counts the rest: a person deciding reads a few, never a page.
export const FIX_REASONS_SHOWN = 5;

// What a fix to the plan is, for someone deciding whether to accept it: why it was drafted (each gap with what
// settles it), then its changes as a count for each kind. Never a line for each task it touches.
export function FixFacts({ need }: { need: Pick<Need, "fixes" | "changes"> }) {
  const fixes = need.fixes ?? [];
  const changes = need.changes ?? [];
  if (!fixes.length && !changes.length) return null;
  const shown = fixes.slice(0, FIX_REASONS_SHOWN);
  return (
    <Stack gap={6} data-testid="planner-fix-facts">
      {shown.map((fix, i) => (
        <Text key={i} size="sm" data-testid="planner-fix-reason">
          <Text span fw={600}>{fix.change}</Text>{" "}
          <Text span c="dimmed">{fix.gap}</Text>
        </Text>
      ))}
      {fixes.length > shown.length && <Text size="sm" c="dimmed">and {fixes.length - shown.length} more</Text>}
      {changes.length > 0 && (
        <Group gap={6} data-testid="planner-fix-counts">
          {changes.map((line) => <Badge key={line} variant="default" tt="none" radius="sm">{line}</Badge>)}
        </Group>
      )}
    </Stack>
  );
}

// What an update asks of the operator, each with the button that resolves it.
function NeedsYou({ needs, planning, goalId, label, onDone }: { needs: Need[]; planning: boolean; goalId: string; label: string | null; onDone: () => void }) {
  if (!needs.length) return null;
  return (
    <Alert color="yellow" variant="light" p="sm" title={needs.length === 1 ? "Needs you" : `Needs you: ${needs.length} things`} data-testid="planner-needs-you">
      <Stack gap="xs">
        {needs.map((need, i) => (
          <Group key={i} justify="space-between" wrap="wrap" gap="sm" data-testid="planner-need">
            <Stack gap={2} style={{ flex: "1 1 240px" }}>
              <Text size="sm">{need.what}</Text>
              {need.action === "accept_fix" && <FixFacts need={need} />}
            </Stack>
            <NeedButton need={need} planning={planning} goalId={goalId} label={label} onDone={onDone} />
          </Group>
        ))}
      </Stack>
    </Alert>
  );
}

// The planner keeping the operator up to date, at the head of the goal's page: its latest update in plain
// language (where the goal stands, what changed, what needs the operator, what happens next), the ones before
// it, and a way to ask for one now. open: the goal is not closed, so its updates are reread as it moves.
// Nothing is shown when updates are off, or for a goal that never had one and will not get one.
export function PlannerUpdates({ goalId, open, planning = false, label = null }: { goalId: string; open: boolean; planning?: boolean; label?: string | null }) {
  const [read, setRead] = useState<PlannerUpdatesRead | null>(null);
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [acted, setActed] = useState(false);
  const [earlier, setEarlier] = useState(false);
  const [, setNow] = useState(0);
  const plan = usePlan(goalId, planning && open);

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

  // An action taken from the panel: the need it answered is hidden at once, and a new update is asked for.
  async function done() {
    setActed(true);
    // What needs the operator changed: every page showing the one list reads it again.
    void reloadNeeds();
    try {
      await ask();
    } finally {
      setActed(false);
    }
  }

  const updates = read?.updates ?? [];
  if (!read || read.enabled === false || !Array.isArray(read.updates)) return null;
  if (!updates.length && !open) return null;
  const [latest, ...before] = updates;
  // While a goal is planned, the plan's own state is what this panel says: where the plan stands and the one
  // thing it asks, decided by the planner service as the page is read (plan.decision). An update is a model's
  // words from a minute or more ago, and beside the plan's state it said the plan was unwritten while the plan
  // waited, or asked for an approval while a reviewer was still reading. Its words show only when they are
  // about this version of the plan and the plan is waiting to be decided.
  const decision = planning && open ? plan?.decision ?? null : null;
  if (planning && open) {
    const working = !decision || decision.buttons.length === 0;
    const about = latest && plan?.plan && latest.plan_version === plan.plan.version && !working ? latest : null;
    const approve = decision?.buttons[0]?.action === "approve_plan";
    const others = (read.needs ?? []).filter((need) => !PLAN_ACTIONS.includes(need.action || (need.ref ? "open_issue" : "approve_plan")));
    return (
      <Paper withBorder p="lg" mb="md" radius="md" data-testid="planner-updates" style={{ borderLeft: "5px solid var(--mantine-color-indigo-6)" }}>
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
          <Group gap="sm" wrap="nowrap" align="flex-start" style={{ flex: "1 1 320px" }}>
            <VoiceMark who="planner" size={38} />
            <Stack gap={4}>
              <Text size="xs" tt="uppercase" fw={700} c="indigo" style={{ letterSpacing: 0.4 }}>
                Goal Planner{plan?.plan ? ` · the plan, version ${plan.plan.version}` : ""}
              </Text>
              <Group gap="xs" wrap="nowrap" align="center" data-testid="planner-plan-state" role="status">
                {working && <Loader size="xs" />}
                <Text size="xl" fw={650} lh={1.3} data-testid="planner-headline">{decision?.says ?? "Reading the plan."}</Text>
              </Group>
              {working && decision && <Text size="sm" c="dimmed">Nothing needs you until this is done. The page follows it as it goes.</Text>}
            </Stack>
          </Group>
          {plan?.plan && (
            <Button component={Link} to={`/goals/${goalId}/plan`} size="sm" color={approve ? "teal" : undefined} variant={approve ? "filled" : "default"} rightSection={<IconArrowRight size={14} />} style={{ flexShrink: 0 }}>
              {approve ? "Review and approve the plan" : working ? "Open the plan" : "Open the plan to decide"}
            </Button>
          )}
        </Group>
        {(about || others.length > 0) && (
          <Stack gap="sm" pl={50} mt="xs">
            {about && <Text size="md" style={{ whiteSpace: "pre-wrap" }}>{about.body}</Text>}
            <NeedsYou needs={others} planning={planning} goalId={goalId} label={label} onDone={() => void done()} />
          </Stack>
        )}
      </Paper>
    );
  }
  // An update is about the plan as it was when written. Once the plan is another version, what the update asks
  // is about a plan that is gone: it asks nothing, and says the next update is on its way. While the planner
  // writes a new version, nothing about the plan is the operator's either.
  const standing = planning ? read.plan : null;
  const replaced =
    standing?.version != null && latest?.plan_version != null && standing.version !== latest.plan_version
      ? { now: standing.version, then: latest.plan_version }
      : null;
  const ofPlan = (need: Need) => PLAN_ACTIONS.includes(need.action || (need.ref ? "open_issue" : "approve_plan"));
  // What needs the operator is the planner's one list, read as the panel is: the update's own rows are what
  // its model wrote when it was written, kept for a planner that does not serve the list.
  const asked = read.needs ?? latest?.needs_you ?? [];
  const needs = standing?.replanning ? asked.filter((need) => !ofPlan(need)) : asked;
  // What the update's model noted for the operator that the list does not hold: a plan's gaps to weigh
  // before approving. Said as text under the rows, with no button of its own.
  const held = new Set(needs.map((need) => need.what));
  const notes = read.needs ? (latest?.needs_you ?? []).map((need) => need.what).filter((what) => what && !held.has(what)) : [];

  return (
    <Paper withBorder p="lg" mb="md" radius="md" data-testid="planner-updates" style={{ borderLeft: "5px solid var(--mantine-color-indigo-6)" }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap" mb={latest ? "xs" : 0}>
        <Group gap="sm" wrap="nowrap" align="flex-start">
          <VoiceMark who="planner" size={38} />
          <Stack gap={2}>
            <Text size="xs" tt="uppercase" fw={700} c="indigo" style={{ letterSpacing: 0.4 }}>
              Goal Planner{latest ? ` · ${since(latest.created_at)}` : ""}
            </Text>
            {latest ? (
              <Text size="xl" fw={650} lh={1.3} data-testid="planner-headline">{latest.headline}</Text>
            ) : (
              <Text size="sm">Your planner is looking at this goal. Its first update is a minute or two away.</Text>
            )}
          </Stack>
        </Group>
        {open && (
          <Button size="xs" variant="default" style={{ flexShrink: 0 }} leftSection={<IconRefresh size={14} />} loading={asking} onClick={() => void ask()}>
            Ask for an update
          </Button>
        )}
      </Group>
      {latest && (
        <Stack gap="sm" pl={50}>
          <Text size="md" style={{ whiteSpace: "pre-wrap" }}>{latest.body}</Text>
          {acted ? (
            // What was asked is done, so it is no longer asked: the update that says so takes a model call, and
            // until it arrives the panel says what is happening, where the button was.
            <Group gap="xs" data-testid="planner-acted" role="status">
              <Loader size="xs" />
              <Text size="sm">Done. I'm writing what changed, which takes a few seconds.</Text>
            </Group>
          ) : open && replaced ? (
            <Group gap="xs" data-testid="planner-plan-replaced" role="status">
              <Loader size="xs" />
              <Text size="sm">
                I wrote this about version {replaced.then} of the plan. The plan is now version {replaced.now}, and I'm writing what changed.
              </Text>
            </Group>
          ) : open ? (
            <>
              {!replaced && notes.length > 0 && (
                <Stack gap={2} data-testid="planner-notes">
                  <Text size="sm" fw={600}>Worth weighing</Text>
                  {notes.map((note) => <Text key={note} size="sm">{note}</Text>)}
                </Stack>
              )}
              {needs.length < asked.length && (
                <Group gap="xs" data-testid="planner-replanning" role="status">
                  <Loader size="xs" />
                  <Text size="sm">I'm writing a new version of the plan. Nothing about it needs you until it's ready.</Text>
                </Group>
              )}
              <NeedsYou needs={needs} planning={planning} goalId={goalId} label={label} onDone={() => void done()} />
            </>
          ) : (
            // The goal is over. This update was written while it ran: what it asked for then is asked of
            // nobody now, and no button here would do anything the goal still wants.
            <Text size="sm" c="dimmed" data-testid="planner-goal-over">
              This is the last update from before the goal ended. Nothing here needs you now.
            </Text>
          )}
          {open && !replaced && latest.next && (
            <Text size="sm"><Text span fw={650}>Next: </Text>{latest.next}</Text>
          )}
        </Stack>
      )}
      {refused && <Alert color="red" variant="light" mt="sm" p="xs" data-testid="planner-update-refused">{refused}</Alert>}
      {read.error && !refused && (
        <Text size="sm" c="dimmed" mt="sm" pl={50}>
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
