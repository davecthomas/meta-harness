import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Anchor, Badge, Button, Card, Code, Group, List, Loader, Modal, Select, Stack, Switch, TagsInput, Text, TextInput, Textarea } from "@mantine/core";
import { IconCheck, IconEdit, IconPlus, IconRefresh, IconTrash } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type Goal, type GoalPlan as Plan, type PlanDraft, type PlanItem } from "../../api";
import { OWN, trackerTitle } from "../trackers/refs";
import { IssueDecisions, OrderOfWork, planSentence, tasksOf } from "./PlanReview";
import { APPROVE_PLAN, PLAN_CHANGED } from "./PlannerUpdates";
import { useUnsavedChanges } from "../../components/UnsavedChanges";
import { ErrorAlert, SectionTitle, when } from "../../components/ui";

const CHECKS = /^##\s+acceptance checks\s*$/im;
const REFRESH_MS = 5000;

// The drafts with each one after the drafts it depends on, otherwise as given. A cycle is left as it stands:
// the planner names it among the plan's reasons.
export function inOrder(drafts: PlanDraft[]): PlanDraft[] {
  const byId = new Map(drafts.map((d) => [d.draft_id, d]));
  const placed: PlanDraft[] = [];
  const seen = new Set<string>();
  const place = (draft: PlanDraft) => {
    if (seen.has(draft.draft_id)) return;
    seen.add(draft.draft_id);
    for (const dep of draft.depends_on) if (byId.has(dep)) place(byId.get(dep)!);
    placed.push(draft);
  };
  drafts.forEach(place);
  return placed;
}

// A draft's body as two parts: what to build, and its acceptance checks, which the card sets apart.
export function splitBody(body: string): { text: string; checks: string | null } {
  const found = CHECKS.exec(body);
  if (!found) return { text: body.trim(), checks: null };
  return { text: body.slice(0, found.index).trim(), checks: body.slice(found.index + found[0].length).trim() };
}

function DraftCard({ draft, index, onEdit, onRemove }: { draft: PlanDraft; index: number; onEdit?: () => void; onRemove?: () => void }) {
  const { text, checks } = splitBody(draft.body);
  // The body is written for the agent that does the task. It stays folded so the plan reads as a short list;
  // one click shows it.
  const [open, setOpen] = useState(false);
  return (
    <Card withBorder padding="md" data-testid={`draft-${draft.draft_id}`}>
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Group gap="xs" wrap="nowrap">
            <Badge variant="light" radius="sm">{index + 1}</Badge>
            <Text fw={650}>{draft.title || "(no title)"}</Text>
            <Badge size="xs" variant="light" color="indigo" tt="none">new</Badge>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Code>{draft.draft_id}</Code>
            <Badge variant="outline" tt="none" radius="sm">{draft.repo}</Badge>
            {onEdit && <Button size="compact-xs" variant="subtle" leftSection={<IconEdit size={14} />} onClick={onEdit} aria-label={`Edit ${draft.draft_id}`}>Edit</Button>}
            {onRemove && <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconTrash size={14} />} onClick={onRemove} aria-label={`Remove ${draft.draft_id}`}>Remove</Button>}
          </Group>
        </Group>
        <Group>
          <Anchor component="button" type="button" size="sm" aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} the details of ${draft.draft_id}`} onClick={() => setOpen((v) => !v)}>
            {open ? "Hide details" : "Show details"}
          </Anchor>
        </Group>
        {open && text && <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{text}</Text>}
        {checks !== null ? (
          open && (
            <Alert color="teal" variant="light" title="Acceptance checks" p="xs">
              <Text size="sm" ff="monospace" style={{ whiteSpace: "pre-wrap" }}>{checks || "(none listed)"}</Text>
            </Alert>
          )
        ) : (
          <Alert color="orange" variant="light" p="xs">No acceptance checks: the body needs a "## Acceptance checks" section.</Alert>
        )}
        {(draft.depends_on.length > 0 || draft.labels.length > 0) && (
          <Group gap="xs">
            {draft.depends_on.length > 0 && <Text size="xs" c="dimmed">After</Text>}
            {draft.depends_on.map((dep) => <Badge key={dep} size="sm" variant="dot" tt="none">{dep}</Badge>)}
            {draft.labels.map((label) => <Badge key={label} size="sm" variant="outline" color="gray" tt="none">{label}</Badge>)}
          </Group>
        )}
      </Stack>
    </Card>
  );
}

function DraftForm({ draft, isNew, repos, others, onSave, onCancel }: {
  draft: PlanDraft;
  isNew: boolean;
  repos: string[];
  // The other drafts' ids, which this one may wait on.
  others: string[];
  onSave: (draft: PlanDraft) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState(draft);
  const idTaken = isNew && others.includes(form.draft_id.trim());
  const ok = /^[a-z0-9][a-z0-9-]{0,39}$/.test(form.draft_id.trim()) && !idTaken && form.title.trim() !== "";
  return (
    <Modal opened onClose={onCancel} title={isNew ? "Add a new task" : `Edit ${draft.draft_id}`} size="xl">
      <Stack gap="md">
        <Group grow align="flex-start">
          <TextInput
            label="Task id"
            description="A short name other tasks use to wait on this one: lowercase letters, digits and hyphens."
            disabled={!isNew}
            value={form.draft_id}
            error={idTaken ? "Another task has this id" : undefined}
            onChange={(e) => setForm({ ...form, draft_id: e.currentTarget.value })}
          />
          <Select label="Repo" data={repos} value={form.repo} allowDeselect={false} onChange={(repo) => setForm({ ...form, repo: repo ?? form.repo })} />
        </Group>
        <TextInput label="Title" maxLength={200} value={form.title} onChange={(e) => setForm({ ...form, title: e.currentTarget.value })} />
        <Textarea
          label="Body"
          description='Markdown. State its checks under "## Acceptance checks", and its paths on a "Paths:" line.'
          autosize
          minRows={8}
          maxRows={24}
          value={form.body}
          onChange={(e) => setForm({ ...form, body: e.currentTarget.value })}
          styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)", fontSize: 13 } }}
        />
        <TagsInput
          label="Waits on"
          description="Other new tasks' ids, or the goal's issues by their refs."
          data={others}
          value={form.depends_on}
          onChange={(depends_on) => setForm({ ...form, depends_on })}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onCancel}>Cancel</Button>
          <Button disabled={!ok} onClick={() => onSave({ ...form, draft_id: form.draft_id.trim(), title: form.title.trim() })}>
            {isNew ? "Add" : "Keep the change"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// A goal's plan, for a goal in planning: what its planning task made of the goal and the issues it already
// has. It shows what the planner says of the plan, the order its tasks can be worked in, its decision on each
// existing issue, and the new tasks it wrote. The operator edits any of it (each save is a new version), asks
// for a new plan with a note, or approves it, choosing whether the new tasks go to the goal's tracker. Nothing
// is created or run before that.
export function GoalPlan({ goal, onApproved }: { goal: Goal; onApproved: () => void }) {
  const [shown, setShown] = useState<Plan | null>(null);
  // The drafts as edited here and not yet saved; null when they are the saved version's.
  const [edited, setEdited] = useState<PlanDraft[] | null>(null);
  // The decisions on the goal's issues as edited here and not yet saved; null when they are the saved version's.
  const [editedItems, setEditedItems] = useState<PlanItem[] | null>(null);
  // Whether the new tasks go to the goal's tracker when the plan is approved, or stay in the meta-harness.
  const [track, setTrack] = useState(true);
  // It starts as the planning configuration says; the operator may choose the other for this plan.
  useEffect(() => {
    api.limits().then((l) => setTrack(l.track_new_tasks !== false), () => undefined);
  }, []);
  const [editing, setEditing] = useState<{ draft: PlanDraft; isNew: boolean } | null>(null);
  const [asking, setAsking] = useState<"approve" | "replan" | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setShown(await api.goalPlan(goal.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [goal.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // While the planning task drafts, look again every few seconds for its plan.
  const drafting = !!shown?.planning_task_id;
  useEffect(() => {
    if (!drafting) return;
    const timer = setInterval(() => !document.hidden && void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [drafting, load]);

  const plan = shown?.plan ?? null;
  const drafts = useMemo(() => inOrder(edited ?? plan?.drafts ?? []), [edited, plan]);

  async function run(action: () => Promise<Plan>, after?: () => void): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      setShown(await action());
      setEdited(null);
      setEditedItems(null);
      setAsking(null);
      setNote("");
      after?.();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  const dirty = edited !== null || editedItems !== null;
  const save = () =>
    dirty ? run(() => api.savePlan(goal.id, edited ?? plan?.drafts ?? [], editedItems ?? undefined)) : Promise.resolve(true);
  const unsaved = useUnsavedChanges({
    dirty,
    describe: `the plan of ${goal.title ?? "this goal"}, edited from version ${plan?.version ?? 0}`,
    save,
    discard: () => {
      setEdited(null);
      setEditedItems(null);
    },
  });

  // "Approve the plan" in the planner's panel opens this plan's approval, where its counts are said first; a
  // fix accepted there makes a new version, read here at once.
  useEffect(() => {
    const approve = () => setAsking("approve");
    const changed = () => void load();
    window.addEventListener(APPROVE_PLAN, approve);
    window.addEventListener(PLAN_CHANGED, changed);
    return () => {
      window.removeEventListener(APPROVE_PLAN, approve);
      window.removeEventListener(PLAN_CHANGED, changed);
    };
  }, [load]);

  if (!shown) return error ? <ErrorAlert error={error} /> : <Loader size="sm" />;
  const change = (next: PlanDraft[]) => setEdited(next);
  const base = edited ?? plan?.drafts ?? [];
  const canApprove = !!plan && plan.invalid.length === 0 && !dirty && !drafting;
  const issues = plan?.issues ?? [];
  const items = editedItems ?? plan?.items ?? [];
  const tasks = tasksOf(base, items, issues);
  // A goal with no tracker keeps every task in the meta-harness: there is nowhere else to put them.
  const untracked = (goal.tracker ?? "github") === OWN;
  const tracker = trackerTitle(goal.tracker ?? "github");

  return (
    <>
      <SectionTitle
        right={
          !drafting && (
            <Group gap="xs">
              <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} onClick={() => setEditing({ draft: { draft_id: "", repo: goal.repos[0], title: "", body: "\n\n## Acceptance checks\n\n- ", labels: [], depends_on: [] }, isNew: true })}>
                Add a task
              </Button>
              <Button size="xs" variant="default" leftSection={<IconRefresh size={14} />} disabled={dirty} onClick={() => setAsking("replan")}>Plan again</Button>
              <Button size="xs" color="teal" leftSection={<IconCheck size={14} />} disabled={!canApprove} onClick={() => setAsking("approve")}>Approve the plan</Button>
            </Group>
          )
        }
      >
        The plan
      </SectionTitle>
      {unsaved.element}
      <ErrorAlert error={error} />
      {drafting && (
        <Alert color="blue" mb="md" title="The planner is working on the plan">
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm">Nothing starts until you approve its plan.</Text>
            <Anchor component={Link} to={`/tasks/${shown.planning_task_id}`} size="sm">Watch its run</Anchor>
          </Group>
        </Alert>
      )}
      {plan && (
        <Stack gap="md">
          <Group gap="sm">
            <Text size="sm">
              Version {plan.version}, {plan.source === "planned" ? "made by the planner" : plan.source === "suggested" ? `the planner's fix, accepted by ${plan.created_by}` : `edited by ${plan.created_by}`}, {when(plan.created_at)}.
              {base.length > 0 && <> Its new tasks will carry the label <Badge variant="outline" tt="none" radius="sm">{shown.label}</Badge></>}
            </Text>
            {plan.task_id && <Anchor component={Link} to={`/tasks/${plan.task_id}`} size="sm">The planning task's run</Anchor>}
          </Group>
          {dirty && (
            <Alert color="yellow" title="Edits not saved yet">
              <Group gap="sm">
                <Text size="sm">Saving makes version {plan.version + 1}. The plan can be approved once it is saved.</Text>
                <Button size="xs" loading={busy} onClick={() => void save()}>Save as version {plan.version + 1}</Button>
                <Button
                  size="xs"
                  variant="default"
                  onClick={() => {
                    setEdited(null);
                    setEditedItems(null);
                  }}
                >
                  Discard
                </Button>
              </Group>
            </Alert>
          )}
          {!dirty && plan.invalid.length > 0 && (
            <Alert color="red" title="This plan can't be approved yet" role="alert">
              <List size="sm" spacing={2}>{plan.invalid.map((reason) => <List.Item key={reason}>{reason}</List.Item>)}</List>
            </Alert>
          )}
          {tasks.length > 1 && (
            <div>
              <Text fw={600} size="sm" mb={4}>The order of work</Text>
              <OrderOfWork tasks={tasks} />
            </div>
          )}
          {issues.length > 0 && (
            <div>
              <Text fw={600} size="sm">The goal's issues</Text>
              <Text size="xs" c="dimmed" mb={6}>Change any decision before you approve.</Text>
              <IssueDecisions issues={issues} items={items} drafts={base} onChange={drafting ? undefined : setEditedItems} />
            </div>
          )}
          {(drafts.length > 0 || issues.length > 0) && (
            <div>
              <Text fw={600} size="sm">New tasks</Text>
              <Text size="xs" c="dimmed">
                {drafts.length
                  ? "Written by the planner. Created when you approve."
                  : "None: the goal's issues cover it."}
              </Text>
            </div>
          )}
          {drafts.map((draft, index) => (
            <DraftCard
              key={draft.draft_id}
              draft={draft}
              index={index}
              onEdit={drafting ? undefined : () => setEditing({ draft, isNew: false })}
              onRemove={drafting ? undefined : () => change(base.filter((d) => d.draft_id !== draft.draft_id))}
            />
          ))}
          {drafts.length === 0 && issues.length === 0 && <Text size="sm" c="dimmed">The plan has no tasks. Add one, or ask for a new plan.</Text>}
        </Stack>
      )}
      {editing && (
        <DraftForm
          draft={editing.draft}
          isNew={editing.isNew}
          repos={goal.repos}
          others={[
            ...base.map((d) => d.draft_id).filter((id) => id !== editing.draft.draft_id || editing.isNew),
            ...items.filter((i) => i.decision === "keep").map((i) => i.ref),
          ]}
          onCancel={() => setEditing(null)}
          onSave={(draft) => {
            change(editing.isNew ? [...base, draft] : base.map((d) => (d.draft_id === draft.draft_id ? draft : d)));
            setEditing(null);
          }}
        />
      )}
      <Modal opened={asking === "approve"} onClose={() => setAsking(null)} title="Approve the plan" size="lg">
        <Stack gap="md">
          <Text size="sm" data-testid="approve-says">
            {planSentence(base, items)} Then the goal starts: each task runs to a pull request, in the order shown.
          </Text>
          {base.length > 0 && !untracked && (
            <Switch
              data-testid="approve-track"
              checked={track}
              onChange={(e) => setTrack(e.currentTarget.checked)}
              label={`Add the ${base.length === 1 ? "new task" : `${base.length} new tasks`} to ${tracker} as ${base.length === 1 ? "an issue" : "issues"}`}
              description={
                track
                  ? `Each becomes an issue there, with the label ${shown.label}.`
                  : `Off: kept in the meta-harness only. Nothing is written to ${tracker}.`
              }
            />
          )}
          {base.length > 0 && untracked && (
            <Text size="sm" c="dimmed">This goal has no issue tracker: its tasks are kept in the meta-harness only.</Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(null)}>Not yet</Button>
            <Button color="teal" loading={busy} onClick={() => void run(() => api.approvePlan(goal.id, untracked ? false : track), onApproved)}>Approve and start</Button>
          </Group>
        </Stack>
      </Modal>
      <Modal opened={asking === "replan"} onClose={() => setAsking(null)} title="Ask for a new plan">
        <Stack gap="md">
          <Text size="sm">The new plan replaces this one.</Text>
          <Textarea label="Note for the planner" placeholder="What to do differently" autosize minRows={3} value={note} onChange={(e) => setNote(e.currentTarget.value)} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(null)}>Cancel</Button>
            <Button loading={busy} onClick={() => void run(() => api.replan(goal.id, note.trim()))}>Plan again</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}

// An approved plan, kept readable on the goal's page: what the planner said of it, and, when asked for, what it
// decided for each issue and the new tasks it added. It is read only: the goal is running it.
// onPlanAgain: the goal went back to planning; absent for a goal that has ended, which is not planned again.
export function ApprovedPlan({ goal, onPlanAgain }: { goal: Goal; onPlanAgain?: () => void }) {
  const [shown, setShown] = useState<Plan | null>(null);
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function planAgain() {
    setBusy(true);
    setError(null);
    try {
      await api.replan(goal.id, note.trim());
      setAsking(false);
      onPlanAgain?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    api.goalPlan(goal.id).then(setShown, () => setShown(null));
  }, [goal.id]);
  const plan = shown?.plan;
  if (!plan || !shown?.approved_at) return null;
  const drafts = inOrder(plan.drafts);
  const items = plan.items ?? [];
  const issues = plan.issues ?? [];
  const kept = plan.tracked === false || (goal.tracker ?? "github") === OWN;
  return (
    <Stack gap="sm" mb="md" data-testid="approved-plan">
      <Group gap="sm">
        <Text size="sm" c="dimmed">
          Plan version {plan.version}, approved {when(shown.approved_at)}. {planSentence(drafts, items).replace("Approving this plan", "It")}
          {drafts.length > 0 && (kept ? " The new tasks are kept in the meta-harness only." : ` The new tasks are issues in ${trackerTitle(goal.tracker ?? "github")}.`)}
        </Text>
        {(issues.length > 0 || drafts.length > 0) && (
          <Button size="compact-sm" variant="subtle" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide the plan" : "Show the plan"}</Button>
        )}
        {onPlanAgain && <Button size="compact-sm" variant="subtle" leftSection={<IconRefresh size={14} />} onClick={() => setAsking(true)}>Plan again</Button>}
      </Group>
      <Modal opened={asking} onClose={() => setAsking(false)} title="Plan the goal again">
        <Stack gap="md">
          <Text size="sm">
            Running tasks carry on. Nothing new starts until you approve the new plan.
          </Text>
          <Textarea label="Note for the planner" placeholder="What changed, or what to do differently" autosize minRows={3} value={note} onChange={(e) => setNote(e.currentTarget.value)} />
          {error && <Text size="sm" c="red" role="alert">{error}</Text>}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(false)}>Cancel</Button>
            <Button loading={busy} onClick={() => void planAgain()}>Plan again</Button>
          </Group>
        </Stack>
      </Modal>
      {open && (
        <Stack gap="md">
          {tasksOf(drafts, items, issues).length > 1 && <OrderOfWork tasks={tasksOf(drafts, items, issues)} />}
          <IssueDecisions issues={issues} items={items} drafts={drafts} />
          {drafts.map((draft, index) => <DraftCard key={draft.draft_id} draft={draft} index={index} />)}
        </Stack>
      )}
    </Stack>
  );
}
