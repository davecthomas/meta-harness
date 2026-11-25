import { useCallback, useEffect, useState } from "react";
import { Anchor, Badge, Button, Collapse, Group, List, Loader, Paper, SimpleGrid, Stack, Switch, Text, Timeline, UnstyledButton } from "@mantine/core";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Goal, type GoalPlan as Plan, type PlanAsk, type PlanFinding, type PlanGlance, type PlanVersion } from "../../api";
import { ErrorAlert, PageHeader, SectionTitle, when } from "../../components/ui";
import { ApprovedPlan, GoalPlan } from "../goals/GoalPlan";
import { APPROVE_PLAN, FixFacts, PLAN_CHANGED, REPLAN } from "../goals/PlannerUpdates";
import { EVENT_BY_DO } from "../goals/needActions";
import { reloadNeeds } from "../overview/needsYou";

// The plan's page. A plan is what the operator decides on before any work starts, so it has a page of its own,
// laid out for that decision: what is being asked, with its buttons; a few figures; what changed and why, as
// counts; the questions still open; the tasks; and the versions it has had. Everything on it helps the
// decision and costs as little reading as the decision allows.

// What each state of a plan is called on its chip.
const STATE_NAMES: Record<PlanAsk["state"], string> = {
  none: "No plan",
  drafting: "Drafting",
  improving: "Being revised",
  fix_waiting: "A fix is waiting",
  incomplete: "Not finished",
  cannot_approve: "Cannot be approved",
  ready: "Ready to approve",
  approved: "Approved",
};
const STATE_COLORS: Partial<Record<PlanAsk["state"], string>> = { ready: "teal", approved: "teal", cannot_approve: "red", incomplete: "yellow", fix_waiting: "yellow" };

// The most of a list shown before it is counted: a person deciding reads a few, never a page.
export const SHOWN_BEFORE_COUNTING = 5;

const dollars = (amount: string | null | undefined): string => {
  const n = Number(amount ?? 0);
  return `$${Number.isInteger(n) ? n : n.toFixed(2)}`;
};

// The one thing the plan asks now, in a sentence or two, beside the buttons that answer it: the first is the
// recommended one. The service decides what it says and offers; this draws it. It stays in view as the page
// scrolls, so the decision is never more than a glance away.
export function DecisionBar({ decision, onAct, busy }: { decision: PlanAsk; onAct: (action: PlanAsk["buttons"][number]["action"]) => void; busy: boolean }) {
  const working = decision.state === "drafting" || decision.state === "improving";
  return (
    <Paper withBorder p="md" radius="md" shadow="sm" data-testid="plan-decision" style={{ position: "sticky", top: "var(--app-shell-header-height, 72px)", zIndex: 5 }}>
      <Group justify="space-between" wrap="wrap" gap="md">
        <Group gap="sm" wrap="nowrap" style={{ flex: "1 1 320px" }}>
          {working && <Loader size="sm" />}
          <Text size="lg" fw={600} lh={1.35} data-testid="plan-decision-says">{decision.says}</Text>
        </Group>
        <Group gap="xs">
          {decision.buttons.map((button, i) => (
            <Button key={button.action + button.label} color={i === 0 ? "teal" : undefined} variant={i === 0 ? "filled" : "default"} disabled={busy} onClick={() => onAct(button.action)}>
              {button.label}
            </Button>
          ))}
        </Group>
      </Group>
    </Paper>
  );
}

function Tile({ label, value, note, warn = false }: { label: string; value: string; note?: string; warn?: boolean }) {
  return (
    <Paper withBorder p="sm" radius="md" data-testid="plan-tile">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{label}</Text>
      <Text size="xl" fw={650} lh={1.2}>{value}</Text>
      {note && <Text size="xs" c={warn ? "red" : "dimmed"}>{note}</Text>}
    </Paper>
  );
}

// The plan's figures, each a tile: read at a glance, before any task is.
export function Glance({ glance }: { glance: PlanGlance }) {
  const repos = glance.repos.length === 1 ? glance.repos[0].split("/").pop()! : String(glance.repos.length);
  const known = glance.may_spend_usd !== null;
  const over = known && !!glance.budget_usd && Number(glance.may_spend_usd) > Number(glance.budget_usd);
  const budget = !glance.budget_usd ? "no budget set" : over ? `more than your ${dollars(glance.budget_usd)} budget` : `of your ${dollars(glance.budget_usd)} budget`;
  return (
    <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="sm" data-testid="plan-glance">
      <Tile label="Tasks" value={String(glance.tasks)} note={glance.unsized ? `${glance.unsized} not sized yet` : undefined} />
      <Tile label={glance.repos.length === 1 ? "Repo" : "Repos"} value={repos} />
      {known ? (
        <Tile label="May spend up to" value={dollars(glance.may_spend_usd)} note={budget} warn={over} />
      ) : (
        <Tile label="Budget" value={glance.budget_usd ? dollars(glance.budget_usd) : "None set"} note="what the tasks may spend is known once they are sized" />
      )}
      <Tile label="Can start at once" value={String(glance.can_start)} />
      <Tile label="Open questions" value={String(glance.open_questions)} />
    </SimpleGrid>
  );
}

const KINDS: [string, string][] = [["added", "added"], ["removed", "removed"], ["reordered", "reordered"], ["changed", "changed"], ["reworded", "reworded"]];

// What changed about the plan and why: the reasons first, then a count for each kind of change. A count opens
// to its tasks. Never a line for each task, which is what nobody reads.
export function WhatChanged({ plan }: { plan: Plan }) {
  const [open, setOpen] = useState<string | null>(null);
  // A fix that waits is shown by what accepting it would change. With none, the plan is shown against the
  // version it is compared with, and a fix the planner applied gives the reasons.
  const waiting = plan.suggestion ?? null;
  const fix = waiting ?? plan.applied_fix ?? null;
  const changes = waiting ? null : plan.changes ?? null;
  if (!fix && !changes) return null;
  const diff = (waiting ? waiting.diff : changes?.diff) ?? {};
  const kinds = KINDS.filter(([kind]) => (diff[kind] ?? []).length > 0);
  const since = waiting ? undefined : changes?.from_version ?? plan.applied_fix?.from_version;
  return (
    <div data-testid="plan-what-changed">
      <SectionTitle>{since ? `What changed since version ${since}` : "What the fix would change"}</SectionTitle>
      <Paper withBorder p="md" radius="md">
        <Stack gap="sm">
          {fix && fix.fixes.length > 0 && (
            <div>
              <Text size="sm" c="dimmed" mb={4}>{plan.applied_fix ? "The planner fixed" : "The planner's fix would settle"}</Text>
              <FixFacts need={{ fixes: fix.fixes, changes: [] }} />
            </div>
          )}
          {kinds.length > 0 && (
            <Group gap={6} data-testid="plan-change-counts">
              {kinds.map(([kind, word]) => (
                <UnstyledButton key={kind} onClick={() => setOpen(open === kind ? null : kind)} aria-expanded={open === kind} data-testid={`plan-change-${kind}`}>
                  <Badge variant={open === kind ? "filled" : "default"} tt="none" radius="sm" size="lg" style={{ cursor: "pointer" }}>
                    {diff[kind].length} {word}
                  </Badge>
                </UnstyledButton>
              ))}
            </Group>
          )}
          {kinds.length === 0 && fix && <FixFacts need={{ fixes: [], changes: fix.changes }} />}
          <Collapse expanded={open !== null}>
            <List size="sm" spacing={2} data-testid="plan-change-list">
              {(open ? diff[open] ?? [] : []).map((task) => <List.Item key={task.id}>{task.title}</List.Item>)}
            </List>
          </Collapse>
          {plan.applied_fix && (
            <Group>
              <UndoFix goalId={plan.goal_id} />
            </Group>
          )}
        </Stack>
      </Paper>
    </div>
  );
}

function UndoFix({ goalId }: { goalId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const undo = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.undoFix(goalId);
      window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
      void reloadNeeds();
    } catch (err) {
      setError(`${err instanceof Error ? err.message : String(err)}. Reload the page to see where the plan stands.`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack gap={4}>
      <Button size="xs" variant="default" loading={busy} onClick={() => void undo()}>Undo the fix</Button>
      <ErrorAlert error={error} />
    </Stack>
  );
}

const KIND_NAMES: Record<string, string> = { missing_step: "Missing work", order: "Order", size: "Size", repo: "Repo", check: "Checks", risk: "Risk", scope: "Scope" };

// One problem the review left open, as a card: what is wrong, the change that would settle it, the tasks it
// names, and the two ways to settle it here. Fixing all of them at once is the decision bar's first button.
function FindingCard({ finding, busy, onDismiss }: { finding: PlanFinding; busy: boolean; onDismiss: () => void }) {
  return (
    <Paper withBorder p="sm" radius="md" data-testid="plan-finding">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
        <Stack gap={4} style={{ flex: "1 1 320px" }}>
          <Group gap={6}>
            {finding.kind && <Badge variant="light" color="red" tt="none" radius="sm">{KIND_NAMES[finding.kind] ?? finding.kind}</Badge>}
            {finding.tasks.map((task) => <Badge key={task} variant="outline" tt="none" radius="sm">{task}</Badge>)}
          </Group>
          <Text size="sm">{finding.says}</Text>
          {finding.change && <Text size="sm" c="dimmed">To settle it: {finding.change}</Text>}
        </Stack>
        <Button size="xs" variant="default" disabled={busy} onClick={onDismiss}>Not a problem</Button>
      </Group>
    </Paper>
  );
}

// The plan's adversarial review: a second model's attempt to find what is wrong with the plan. One line says
// what it found; only the problems still open are shown in full, and past a few they are grouped by kind. What
// the planner fixed and what was only advice are counts that open.
export function ReviewPart({ plan, goal, onChanged }: { plan: Plan; goal: Goal; onChanged: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const review = plan.review ?? null;
  const planning = goal.status === "planning";
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(`${err instanceof Error ? err.message : String(err)}. Reload the page to see where the plan stands.`);
    } finally {
      setBusy(false);
    }
  };
  if (!planning && !review) return null;
  const findings = review?.findings ?? [];
  const still = findings.filter((f) => f.status === "open" && f.severity === "blocking");
  const advice = findings.filter((f) => f.status === "open" && f.severity === "advice");
  const fixed = findings.filter((f) => f.status === "fixed" || f.status === "revised");
  const kinds = [...new Set(still.map((f) => f.kind || "other"))];
  const grouped = still.length > SHOWN_BEFORE_COUNTING;
  const said = !review
    ? "This plan has not been reviewed by a second model."
    : review.state === "running"
      ? `${review.model} is reading the plan now.`
      : review.state === "failed" || review.state === "canceled"
        ? `The review by ${review.model} did not finish, so it says nothing about the plan.`
        : review.counts.found === 0
          ? `${review.model} looked for problems and found none.`
          : `${review.model} found ${review.counts.found} problem${review.counts.found === 1 ? "" : "s"}: ${review.counts.fixed} fixed, ${review.counts.open} open${review.state === "partial" ? ". It stopped before it finished" : ""}.`;
  const lists: [string, string, PlanFinding[]][] = [["fixed", `${fixed.length} fixed`, fixed], ["advice", `${advice.length} suggestion${advice.length === 1 ? "" : "s"}`, advice]];
  return (
    <div data-testid="plan-review">
      <SectionTitle
        right={
          planning && (
            <Group gap="md">
              <Switch
                size="xs"
                label="Review this goal's plans"
                checked={!!goal.adversarial_review}
                disabled={busy}
                onChange={(event) => void run(() => api.setPlanReview(goal.id, event.currentTarget.checked))}
              />
              {review?.state !== "running" && review?.version !== plan.plan?.version && (
                <Button size="xs" variant="default" disabled={busy} onClick={() => void run(() => api.reviewPlan(goal.id))}>Review this plan</Button>
              )}
            </Group>
          )
        }
      >
        Adversarial review
      </SectionTitle>
      <Stack gap="sm">
        <Text size="sm" data-testid="plan-review-says">{said}</Text>
        <ErrorAlert error={error} />
        {!grouped && still.map((f) => <FindingCard key={f.id} finding={f} busy={busy} onDismiss={() => void run(() => api.dismissPlanFinding(goal.id, f.id))} />)}
        {grouped && (
          <Group gap={6} data-testid="plan-finding-kinds">
            {kinds.map((kind) => (
              <UnstyledButton key={kind} onClick={() => setOpen(open === kind ? null : kind)} aria-expanded={open === kind}>
                <Badge variant={open === kind ? "filled" : "light"} color="red" tt="none" radius="sm" size="lg" style={{ cursor: "pointer" }}>
                  {KIND_NAMES[kind] ?? "Other"}: {still.filter((f) => (f.kind || "other") === kind).length}
                </Badge>
              </UnstyledButton>
            ))}
          </Group>
        )}
        {grouped && open && kinds.includes(open) && still.filter((f) => (f.kind || "other") === open).map((f) => <FindingCard key={f.id} finding={f} busy={busy} onDismiss={() => void run(() => api.dismissPlanFinding(goal.id, f.id))} />)}
        {(fixed.length > 0 || advice.length > 0) && (
          <Group gap={6}>
            {lists.filter(([, , items]) => items.length > 0).map(([key, label]) => (
              <UnstyledButton key={key} onClick={() => setOpen(open === key ? null : key)} aria-expanded={open === key} data-testid={`plan-review-${key}`}>
                <Badge variant={open === key ? "filled" : "default"} tt="none" radius="sm" style={{ cursor: "pointer" }}>{label}</Badge>
              </UnstyledButton>
            ))}
          </Group>
        )}
        <Collapse expanded={open === "fixed" || open === "advice"}>
          <List size="sm" spacing={4} data-testid="plan-review-list">
            {(open === "fixed" ? fixed : open === "advice" ? advice : []).map((f) => (
              <List.Item key={f.id}>{f.says}{f.change ? <Text span c="dimmed"> {f.change}</Text> : null}</List.Item>
            ))}
          </List>
        </Collapse>
      </Stack>
    </div>
  );
}

// What only the operator can settle, a few at most in view.
export function OpenQuestions({ gaps }: { gaps: string[] }) {
  const [all, setAll] = useState(false);
  if (!gaps.length) return null;
  const shown = all ? gaps : gaps.slice(0, SHOWN_BEFORE_COUNTING);
  return (
    <div data-testid="plan-questions">
      <SectionTitle>{gaps.length === 1 ? "1 open question" : `${gaps.length} open questions`}</SectionTitle>
      <Stack gap="xs">
        {shown.map((gap, i) => (
          <Paper key={i} withBorder p="sm" radius="md"><Text size="sm">{gap}</Text></Paper>
        ))}
        {gaps.length > shown.length && <Anchor component="button" size="sm" onClick={() => setAll(true)}>Show the other {gaps.length - shown.length}</Anchor>}
      </Stack>
    </div>
  );
}

const MADE_BY: Record<string, string> = { planned: "The planner wrote it", suggested: "The planner fixed its gaps", edited: "Edited" };

// The plan's versions, newest first: who or what made each, and how many tasks it had.
export function History({ versions }: { versions: PlanVersion[] }) {
  if (versions.length < 2) return null;
  return (
    <div data-testid="plan-history">
      <SectionTitle>History</SectionTitle>
      <Timeline bulletSize={14} lineWidth={2}>
        {[...versions].reverse().map((v) => (
          <Timeline.Item key={v.version} title={<Text size="sm" fw={600}>Version {v.version}</Text>}>
            <Text size="sm" c="dimmed">
              {MADE_BY[v.source] ?? v.source}{v.source === "edited" ? ` by ${v.created_by}` : ""} · {v.tasks} task{v.tasks === 1 ? "" : "s"} · {when(v.created_at)}
              {v.stopped_at ? ` · stopped at its ${v.stopped_at} limit` : ""}
            </Text>
          </Timeline.Item>
        ))}
      </Timeline>
    </div>
  );
}

export function PlanPage() {
  const { goalId = "" } = useParams();
  const navigate = useNavigate();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [versions, setVersions] = useState<PlanVersion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setGoal(await api.goal(goalId));
      setError(null);
    } catch (err) {
      setError(`${err instanceof Error ? err.message : String(err)}. Reload the page to try again.`);
    }
  }, [goalId]);
  useEffect(() => void load(), [load]);

  // An approved plan's section reads the plan for itself, so the page reads it here for its decision and figures.
  const approved = !!goal && goal.status !== "planning";
  useEffect(() => {
    if (approved) api.goalPlan(goalId).then(setPlan, () => setPlan(null));
  }, [approved, goalId]);

  // The versions are read again whenever the plan shown is another one.
  const version = plan?.plan?.version;
  useEffect(() => {
    if (!goalId) return;
    api.planVersions(goalId).then((found) => setVersions(found.versions), () => setVersions([]));
  }, [goalId, version]);

  // A link from another page can ask for the approval or for Plan again on arrival (`?do=`): done once the
  // plan's section is listening, then taken off the address so a reload does not do it again.
  const [search, setSearch] = useSearchParams();
  const asked = search.get("do");
  const ready = !!plan;
  useEffect(() => {
    if (!asked || !ready) return;
    const name = EVENT_BY_DO[asked];
    const timer = window.setTimeout(() => {
      if (name === APPROVE_PLAN || name === REPLAN) window.dispatchEvent(new CustomEvent(name));
      setSearch((now) => { now.delete("do"); return now; }, { replace: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [asked, ready, setSearch]);

  const act = async (action: PlanAsk["buttons"][number]["action"]) => {
    // Approving and planning again open the plan section's own dialogs, which say what each will do first.
    if (action === "approve_plan") return void window.dispatchEvent(new CustomEvent(APPROVE_PLAN));
    if (action === "replan") return void window.dispatchEvent(new CustomEvent(REPLAN));
    // The budget is the goal's setting, changed on the goal's page, which opens it on arrival.
    if (action === "raise_budget") return void navigate(`/goals/${goalId}?do=budget`);
    setBusy(true);
    setError(null);
    try {
      await (action === "fix_findings" ? api.fixFindings(goalId) : action === "accept_fix" ? api.acceptFix(goalId) : api.dismissFix(goalId));
      window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
      void reloadNeeds();
    } catch (err) {
      setError(`${err instanceof Error ? err.message : String(err)}. Reload the page to see where the plan stands.`);
    } finally {
      setBusy(false);
    }
  };

  if (!goal) return error ? <ErrorAlert error={error} /> : <Loader />;
  const planning = goal.status === "planning";
  const decision = plan?.decision;
  return (
    <Stack gap="lg" data-testid="plan-page">
      <PageHeader
        title={goal.title || goal.statement}
        description={
          <Group gap="xs">
            <Anchor component={Link} to={`/goals/${goal.id}`} size="sm">Back to the goal</Anchor>
            {plan?.plan && <Text size="sm" c="dimmed">· Plan version {plan.plan.version}</Text>}
            {decision && <Badge variant="light" color={STATE_COLORS[decision.state] ?? "gray"} tt="none" radius="sm" data-testid="plan-state">{STATE_NAMES[decision.state]}</Badge>}
          </Group>
        }
      />
      <ErrorAlert error={error} />
      {decision && <DecisionBar decision={decision} onAct={(action) => void act(action)} busy={busy} />}
      {plan?.glance && <Glance glance={plan.glance} />}
      {plan && <WhatChanged plan={plan} />}
      {plan?.plan && (
        <ReviewPart
          plan={plan}
          goal={goal}
          onChanged={() => {
            void load();
            window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
            void reloadNeeds();
          }}
        />
      )}
      {planning && plan?.plan && <OpenQuestions gaps={plan.plan.gaps ?? []} />}
      {planning ? (
        <GoalPlan
          goal={goal}
          embedded
          onShown={setPlan}
          onApproved={() => {
            void reloadNeeds();
            navigate(`/goals/${goal.id}`);
          }}
        />
      ) : (
        <ApprovedPlan goal={goal} />
      )}
      <History versions={versions} />
    </Stack>
  );
}
