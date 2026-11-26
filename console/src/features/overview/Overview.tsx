import { Narrative } from "../narratives/Narrative";
import { useEffect, useRef, useState } from "react";
import { TaskName } from "../../components/TaskName";
import { Link } from "react-router-dom";
import { Badge, Button, Card, Group, Paper, SimpleGrid, Stack, Text } from "@mantine/core";
import { VoiceMark } from "../../components/Voice";
import { api, type MhEvent } from "../../api";
import { PullRequestsRow } from "./PullRequestsRow";
import { ReviewsInProgress } from "../tasks/reviewing";
import { ResumeBudget, budgetStops, budgetWords, spentOf } from "../goals/budget";
import { useInfinite } from "../../hooks";
import { Empty, ErrorAlert, IssueLink, PageHeader, PrLink, SectionTitle, TitleLink, when } from "../../components/ui";
import { FailureGroups, groupFailures } from "../tasks/FailureGroups";
import { RetryAll, retryAllPlan } from "../tasks/Remedies";
import { NeedButton } from "../goals/needActions";
import { needsYouNote, useNeedsYou, type NeedsYouItem } from "./needsYou";
import { useWorkChanged } from "../goals/goalSync";
import { WorkReport } from "./WorkReport";
import { GettingStarted } from "./GettingStarted";
import { SupervisorHealth } from "./SupervisorHealth";
import { errorText, ReadFailed } from "../../components/ReadFailed";

// A goal that is open: taking in issues, planning them, or waiting on someone. The same list the Goals page's
// Open filter uses.
const OPEN_GOALS = ["planning", "active", "paused", "stalled", "budget_exhausted"];

export function Overview() {
  // Counted up by each action taken here, so the list rereads.
  const [acted, setActed] = useState(0);
  // What needs the operator: the one list, which the header's inbox counts too (needsYou.ts).
  const { items, error } = useNeedsYou(acted);
  const goals = useInfinite((offset, limit) => api.goals(OPEN_GOALS, offset, limit), 1, "open-goals");
  const running = useInfinite((offset, limit) => api.tasks({ statuses: ["running"] }, offset, limit), 1, "running");
  const queued = useInfinite((offset, limit) => api.tasks({ statuses: ["queued", "blocked"] }, offset, limit), 1, "queued");

  // The counts read again the moment anything in the work changes, with the list and the report (goalSync.ts).
  useWorkChanged(() => {
    goals.reload();
    running.reload();
    queued.reload();
  });

  const needsYou = useRef<HTMLDivElement | null>(null);
  const count = items?.length ?? null;
  // The failed and unresolved tasks by what went wrong, so the same failure is said, and fixed, once. The
  // rest of the list follows, each item once.
  const broken = (items ?? []).filter((i) => i.task && ["failed", "review_unresolved"].includes(i.task.status) && !i.task.waiting_on);
  const failed = groupFailures(
    broken.map(({ task }) => ({ key: task!.id, name: task!.title, taskId: task!.id, issueRef: task!.external_ref, status: task!.status, reason: task!.status_reason, max_usd: task!.max_usd, complexity: task!.complexity, prUrl: task!.pr_url })),
  );
  const rest = (items ?? []).filter((i) => !broken.includes(i));

  return (
    <>
      <PageHeader title="Overview" />

      <WorkReport />
      <Narrative title="Narrative of the running goals" path="/v1/overview/narrative" />
      <GettingStarted />
      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} mb="lg">
        <Stat label="Needs you" value={count} link={count ? "See what to do" : undefined} alert={!!count}
          onClick={count ? () => needsYou.current?.scrollIntoView({ behavior: "smooth", block: "start" }) : undefined}
          note={needsYouNote(items)} />
        <Stat label="Running" value={running.total} to={running.total ? "/tasks?view=tiles" : undefined} link={running.total ? "Watch them" : undefined}
          note={running.total === null ? "" : running.total ? "Tasks an agent is working on now." : "No agent is working. A task starts when a goal has an open issue and a free place."} />
        <Stat label="Queued or blocked" value={queued.total} to="/tasks?show=waiting" link="See why each waits"
          note={queued.total === null ? "" : queued.total ? "Tasks that have not started: each says what holds it." : "Nothing waits to start."} />
        <Stat label="Open goals" value={goals.total} to="/goals?show=open" link="All goals"
          note={goals.total === null ? "" : goals.total ? "Goals taking in issues or waiting on you." : "No goal is open: create one to start work."} />
      </SimpleGrid>

      <div ref={needsYou} />
      <SectionTitle><Group gap="xs" wrap="nowrap"><VoiceMark who="shepherd" size={24} />Needs you</Group></SectionTitle>
      <SupervisorHealth />
      {error && (
        <ErrorAlert
          error={`Could not read what needs you: ${error}`}
          remedy={<Text size="xs">It tries again in 30 seconds. Until then, what shows below may be out of date.</Text>}
        />
      )}
      {failed.length > 0 && (
        <Paper withBorder p="md" mb="md" data-testid="needs-you-failures" style={{ borderColor: "var(--mantine-color-red-6)" }}>
          <Group justify="flex-end" mb="sm"><RetryAll plan={retryAllPlan(failed)} onChanged={() => setActed((n) => n + 1)} /></Group>
          <FailureGroups groups={failed} onChanged={() => setActed((n) => n + 1)} />
        </Paper>
      )}
      {rest.length > 0 && (
        <Stack gap="xs" mb="lg" data-testid="needs-you-list">
          {rest.map((item) => <NeedsYouRow key={item.key} item={item} onActed={() => setActed((n) => n + 1)} />)}
        </Stack>
      )}
      {items !== null && items.length === 0 && !error && (
        <Empty>Nothing needs you. A task shows here when it is ready to merge, fails, or waits for a decision. A goal shows when it passes its budget or stops moving.</Empty>
      )}
      {items === null && !error && <Text size="sm" c="dimmed" mb="lg">Loading…</Text>}

      <ReviewsInProgress />
      <PullRequestsRow />
      <TodaySpend />
    </>
  );
}

const PLAN_NEEDS = ["approve_plan", "fix_plan", "accept_fix"];

// One thing that needs the operator: what it asks for, what it is about, why, and the action where there is one
// to take from here.
function NeedsYouRow({ item, onActed }: { item: NeedsYouItem; onActed: () => void }) {
  const { goal, task } = item;
  const raisesGoal = (goal && budgetStops(goal) && item.key === `goal-${goal.id}`) || (task?.waiting_on === "budget" && !!task.goal_id && (task.status_reason ?? "").includes("goal's budget"));
  return (
    <Paper p="sm" data-testid={`needs-you-${item.key}`}>
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
        <Stack gap={2} style={{ flex: "1 1 420px", minWidth: 0 }}>
          <Group gap="xs" wrap="wrap">
            <Badge color="red" variant="light" tt="none" miw="max-content">{item.action}</Badge>
            <TitleLink to={item.to} title={item.title} id={(task ?? goal)?.id ?? ""} />
          </Group>
          <Text size="sm">{item.detail}</Text>
          <Group gap="md">
            {task && <IssueLink issueRef={task.external_ref} />}
            {task?.pr_url && <PrLink url={task.pr_url} />}
            {goal && budgetWords(goal) && <Text size="sm" c="dimmed">Spent ${spentOf(goal).toFixed(2)} of ${Number(goal.max_usd).toFixed(2)}: {budgetWords(goal)}</Text>}
            {item.since && <Text size="xs" c="dimmed">Waiting on you since {when(item.since)}</Text>}
          </Group>
        </Stack>
        <Group gap="xs">
          {raisesGoal && <ResumeBudget goalId={(goal?.id ?? task?.goal_id)!} goal={goal} size="xs" variant="light" onResumed={onActed} />}
          {/* A plan's own steps (approve it, update it with the planner's fix, plan again) are the same button
              the report's row and the goal's page show for them. */}
          {PLAN_NEEDS.includes(item.need.action ?? "") && (
            <NeedButton need={item.need} planning={false} goalId={item.need.goal_id} label={item.need.label || null} onDone={onActed} away />
          )}
          <Button component={Link} to={item.to} size="xs" variant="default">Open</Button>
        </Group>
      </Group>
    </Paper>
  );
}

// A number on the Overview with what it means and where to act on it. The whole tile is the link.
function Stat(props: { label: string; value: number | null; note?: string; to?: string; link?: string; onClick?: () => void; alert?: boolean }) {
  const body = (
    <>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{props.label}</Text>
      <Group justify="space-between" align="flex-end" mt={6}>
        <Text fz={32} fw={650} lh={1} c={props.alert ? "red" : undefined}>{props.value ?? "–"}</Text>
        {props.link && <Text size="sm" c="var(--mantine-color-anchor)">{props.link}</Text>}
      </Group>
      {props.note && <Text size="sm" c="dimmed" mt={8}>{props.note}</Text>}
    </>
  );
  const style = { cursor: props.to || props.onClick ? "pointer" : "default", textDecoration: "none", color: "inherit", textAlign: "left" as const };
  if (props.to) return <Card withBorder padding="lg" component={Link} to={props.to} style={style} aria-label={`${props.label}: ${props.value ?? "loading"}. ${props.link ?? ""}`}>{body}</Card>;
  if (props.onClick) return <Card withBorder padding="lg" component="button" type="button" onClick={props.onClick} style={style} aria-label={`${props.label}: ${props.value ?? "loading"}. ${props.link ?? ""}`}>{body}</Card>;
  return <Card withBorder padding="lg">{body}</Card>;
}

// Today's spend across every goal, and the five tasks that cost the most today, from today's attempts.
export function TodaySpend() {
  const [attempts, setAttempts] = useState<MhEvent[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    api.recentEvents(["attempt_finished"], 2000).then(setAttempts, (err) => setFailed(errorText(err)));
  }, []);
  if (failed) return <ReadFailed what="today's spend" error={failed} />;
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
                <TaskName id={id} size="xs" />
                <Text size="xs">${usd.toFixed(2)}</Text>
              </Group>
            ))}
            <Text size="xs" fw={650} pt={2} style={{ borderTop: "1px solid var(--mantine-color-default-border)" }} data-testid="today-top-total">
              These {top.length}: ${top.reduce((all, [, usd]) => all + usd, 0).toFixed(2)}
            </Text>
          </Stack>
        )}
      </Group>
    </Paper>
  );
}
