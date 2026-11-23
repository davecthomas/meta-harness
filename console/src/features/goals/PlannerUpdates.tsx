import { useCallback, useEffect, useState } from "react";
import { Alert, Anchor, Button, Collapse, Group, List, Loader, Paper, Stack, Text, Timeline } from "@mantine/core";
import { IconCheck, IconRefresh } from "@tabler/icons-react";
import { ConfirmButton } from "../../components/ConfirmButton";
import { VoiceMark } from "../../components/Voice";
import { api } from "../../api";
import { issueUrl } from "../trackers/refs";
import { when } from "../../components/ui";
import { HeldIssueAction, TaskNeedAction } from "./needActions";

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

// What resolves a need: the page's own button for it. Every need has one.
export type NeedAction =
  | "approve_plan" | "accept_fix" | "merge" | "review" | "open_task" | "raise_budget" | "open_issue"
  | "settle_review" | "retry" | "split_or_run" | "fix_plan";
// changes: for accept_fix, what the planner's fix changes in the plan, a line each.
export type Need = { what: string; ref: string; action?: NeedAction | ""; task_id?: string; pr_url?: string; changes?: string[] };

// Events the goal page listens for, so a need is resolved where it is shown: the plan's approval, and the
// goal's settings (its budget).
export const APPROVE_PLAN = "mh:approve-plan";
// Opens the plan's Plan again, for a plan that cannot be approved as it is.
export const REPLAN = "mh:replan";
export const OPEN_SETTINGS = "mh:open-settings";
export const SHOW_ISSUES = "mh:show-issues";
// Sent once the plan changed from here (a fix accepted or dismissed), so the plan's section reads it again.
export const PLAN_CHANGED = "mh:plan-changed";

// The button that resolves one need, or takes the operator to where they do.
// planning: the goal is being planned, so a need written before needs had actions, about no issue, is the
// plan's approval.
function NeedAction({ need, planning, goalId, label, onDone }: { need: Need; planning: boolean; goalId: string; label: string | null; onDone: () => void }) {
  const changed = () => {
    window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
    onDone();
  };
  const send = (name: string) => () => window.dispatchEvent(new CustomEvent(name));
  const task = need.task_id ?? "";
  const action = need.action || (planning && !need.ref ? "approve_plan" : "");
  switch (action) {
    case "approve_plan":
      return <Button size="xs" color="teal" leftSection={<IconCheck size={14} />} onClick={send(APPROVE_PLAN)}>Approve the plan</Button>;
    case "fix_plan":
      return <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={send(REPLAN)}>Plan again</Button>;
    case "accept_fix":
      return (
        <Group gap="xs" wrap="nowrap">
          <ConfirmButton
            size="xs"
            color="teal"
            label="Accept the fix"
            title="Accept the planner's fix?"
            says={
              <Stack gap={4}>
                <Text size="sm">It becomes the plan. You still approve the plan before anything runs.</Text>
                <List size="sm" spacing={2}>{(need.changes ?? []).map((line) => <List.Item key={line}>{line}</List.Item>)}</List>
              </Stack>
            }
            confirm="Accept"
            leftSection={<IconCheck size={14} />}
            onConfirm={async () => {
              await api.acceptFix(goalId);
              changed();
            }}
          />
          <Button
            size="xs"
            variant="subtle"
            color="gray"
            onClick={async () => {
              await api.dismissFix(goalId);
              changed();
            }}
          >
            Dismiss
          </Button>
        </Group>
      );
    // A need about a task gets the buttons its task's state calls for now (needActions.tsx): merge or try
    // another fix on a review no fix settled, retry a failed task, and none once it is under way again.
    case "merge":
    case "review":
    case "settle_review":
    case "retry":
    case "open_task":
      return task ? <TaskNeedAction taskId={task} onDone={onDone} /> : <Button size="xs" variant="light" onClick={send(SHOW_ISSUES)}>Show the goal's issues</Button>;
    case "split_or_run": {
      const issues = <Button size="xs" variant="light" onClick={send(SHOW_ISSUES)}>Show the goal's issues</Button>;
      return need.ref ? <HeldIssueAction goalId={goalId} issueRef={need.ref} label={label} fallback={issues} onDone={onDone} /> : issues;
    }
    case "raise_budget":
      return <Button size="xs" variant="light" onClick={send(OPEN_SETTINGS)}>Raise the budget</Button>;
    default: {
      const url = need.ref ? issueUrl(need.ref) : null;
      const open = url ? (
        <Button size="xs" variant="light" component="a" href={url} target="_blank" rel="noreferrer">Open the issue</Button>
      ) : (
        <Button size="xs" variant="light" onClick={send(SHOW_ISSUES)}>Show the goal's issues</Button>
      );
      // A need about an issue reads the issue as it is now, whatever the update said when it was written: a
      // held one is split or run as one right here, one with a task gets its task's buttons.
      return need.ref ? <HeldIssueAction goalId={goalId} issueRef={need.ref} label={label} fallback={open} onDone={onDone} /> : open;
    }
  }
}

// The actions that are about the plan itself.
const PLAN_ACTIONS: string[] = ["approve_plan", "fix_plan", "accept_fix"];

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
              {need.action === "accept_fix" && (need.changes ?? []).length > 0 && (
                <Text size="sm" c="dimmed">{(need.changes ?? []).join(" · ")}</Text>
              )}
            </Stack>
            <NeedAction need={need} planning={planning} goalId={goalId} label={label} onDone={onDone} />
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
  // An update is about the plan as it was when written. Once the plan is another version, what the update asks
  // is about a plan that is gone: it asks nothing, and says the next update is on its way. While the planner
  // writes a new version, nothing about the plan is the operator's either.
  const standing = planning ? read.plan : null;
  const replaced =
    standing?.version != null && latest?.plan_version != null && standing.version !== latest.plan_version
      ? { now: standing.version, then: latest.plan_version }
      : null;
  const ofPlan = (need: Need) => PLAN_ACTIONS.includes(need.action || (need.ref ? "open_issue" : "approve_plan"));
  const asked = latest?.needs_you ?? [];
  const needs = standing?.replanning ? asked.filter((need) => !ofPlan(need)) : asked;

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
          <Button size="xs" variant="subtle" style={{ flexShrink: 0 }} leftSection={<IconRefresh size={14} />} loading={asking} onClick={() => void ask()}>
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
