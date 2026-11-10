import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Anchor, Badge, Button, Card, Code, Group, List, Loader, Modal, Select, Stack, TagsInput, Text, TextInput, Textarea } from "@mantine/core";
import { IconArrowRight, IconCheck, IconEdit, IconPlus, IconRefresh, IconTrash } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type Goal, type GoalPlan as Plan, type PlanDraft } from "../../api";
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
  return (
    <Card withBorder padding="md" data-testid={`draft-${draft.draft_id}`}>
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Group gap="xs" wrap="nowrap">
            <Badge variant="light" radius="sm">{index + 1}</Badge>
            <Text fw={650}>{draft.title || "(no title)"}</Text>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Code>{draft.draft_id}</Code>
            <Badge variant="outline" tt="none" radius="sm">{draft.repo}</Badge>
            {onEdit && <Button size="compact-xs" variant="subtle" leftSection={<IconEdit size={14} />} onClick={onEdit} aria-label={`Edit ${draft.draft_id}`}>Edit</Button>}
            {onRemove && <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconTrash size={14} />} onClick={onRemove} aria-label={`Remove ${draft.draft_id}`}>Remove</Button>}
          </Group>
        </Group>
        {text && <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{text}</Text>}
        {checks !== null ? (
          <Alert color="teal" variant="light" title="Acceptance checks" p="xs">
            <Text size="sm" ff="monospace" style={{ whiteSpace: "pre-wrap" }}>{checks || "(none listed)"}</Text>
          </Alert>
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

// The order of work as a small graph: each draft, and an arrow from each draft it waits on.
function OrderOfWork({ drafts }: { drafts: PlanDraft[] }) {
  const ids = new Set(drafts.map((d) => d.draft_id));
  return (
    <Stack gap={4} data-testid="plan-order">
      {drafts.map((d, i) => {
        const after = d.depends_on.filter((dep) => ids.has(dep));
        return (
          <Group key={d.draft_id} gap={6} wrap="nowrap">
            <Text size="xs" c="dimmed" w={18}>{i + 1}.</Text>
            {after.map((dep) => (
              <Group key={dep} gap={4} wrap="nowrap">
                <Code>{dep}</Code>
                <IconArrowRight size={12} />
              </Group>
            ))}
            <Code fw={700}>{d.draft_id}</Code>
          </Group>
        );
      })}
    </Stack>
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
    <Modal opened onClose={onCancel} title={isNew ? "Add a draft" : `Edit ${draft.draft_id}`} size="xl">
      <Stack gap="md">
        <Group grow align="flex-start">
          <TextInput
            label="Draft id"
            description="A short name other drafts use to wait on this one: lowercase letters, digits and hyphens."
            disabled={!isNew}
            value={form.draft_id}
            error={idTaken ? "Another draft has this id" : undefined}
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
          description="Other drafts' ids, or issues that exist as owner/repo#12."
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

// A feature goal's plan, for a goal in planning: what its planning task drafted, in the order the work can
// be done. The operator edits drafts (each save is a new version), asks for a new plan with a note, or
// approves it, which creates the issues on the code host and starts the goal. Nothing is created before that.
export function GoalPlan({ goal, onApproved }: { goal: Goal; onApproved: () => void }) {
  const [shown, setShown] = useState<Plan | null>(null);
  // The drafts as edited here and not yet saved; null when they are the saved version's.
  const [edited, setEdited] = useState<PlanDraft[] | null>(null);
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

  const save = () => (edited ? run(() => api.savePlan(goal.id, edited)) : Promise.resolve(true));
  const unsaved = useUnsavedChanges({
    dirty: edited !== null,
    describe: `the plan of ${goal.title ?? "this goal"}, edited from version ${plan?.version ?? 0}`,
    save,
    discard: () => setEdited(null),
  });

  if (!shown) return error ? <ErrorAlert error={error} /> : <Loader size="sm" />;
  const change = (next: PlanDraft[]) => setEdited(next);
  const base = edited ?? plan?.drafts ?? [];
  const canApprove = !!plan && plan.invalid.length === 0 && edited === null && !drafting;

  return (
    <>
      <SectionTitle
        right={
          !drafting && (
            <Group gap="xs">
              <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} onClick={() => setEditing({ draft: { draft_id: "", repo: goal.repos[0], title: "", body: "\n\n## Acceptance checks\n\n- ", labels: [], depends_on: [] }, isNew: true })}>
                Add a draft
              </Button>
              <Button size="xs" variant="default" leftSection={<IconRefresh size={14} />} disabled={edited !== null} onClick={() => setAsking("replan")}>Replan</Button>
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
        <Alert color="blue" mb="md" title="The planning task is drafting the plan">
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm">It reads the goal's repos and proposes issues. Nothing is created until you approve.</Text>
            <Anchor component={Link} to={`/tasks/${shown.planning_task_id}`} size="sm">Watch its run</Anchor>
          </Group>
        </Alert>
      )}
      {plan && (
        <Stack gap="md">
          <Group gap="sm">
            <Text size="sm">
              Version {plan.version}, {plan.source === "planned" ? "drafted by the planning task" : `edited by ${plan.created_by}`}, {when(plan.created_at)}.
              Its issues will carry the label <Badge variant="outline" tt="none" radius="sm">{shown.label}</Badge>
            </Text>
            {plan.task_id && <Anchor component={Link} to={`/tasks/${plan.task_id}`} size="sm">The planning task's run</Anchor>}
          </Group>
          {edited !== null && (
            <Alert color="yellow" title="Edits not saved yet">
              <Group gap="sm">
                <Text size="sm">Saving makes version {plan.version + 1}. The plan can be approved once it is saved.</Text>
                <Button size="xs" loading={busy} onClick={() => void save()}>Save as version {plan.version + 1}</Button>
                <Button size="xs" variant="default" onClick={() => setEdited(null)}>Discard</Button>
              </Group>
            </Alert>
          )}
          {edited === null && plan.invalid.length > 0 && (
            <Alert color="red" title="This plan can't be approved yet" role="alert">
              <List size="sm" spacing={2}>{plan.invalid.map((reason) => <List.Item key={reason}>{reason}</List.Item>)}</List>
            </Alert>
          )}
          {drafts.length > 1 && <OrderOfWork drafts={drafts} />}
          {drafts.map((draft, index) => (
            <DraftCard
              key={draft.draft_id}
              draft={draft}
              index={index}
              onEdit={drafting ? undefined : () => setEditing({ draft, isNew: false })}
              onRemove={drafting ? undefined : () => change(base.filter((d) => d.draft_id !== draft.draft_id))}
            />
          ))}
          {drafts.length === 0 && <Text size="sm" c="dimmed">The plan has no drafts. Add one, or ask for a new plan.</Text>}
        </Stack>
      )}
      {editing && (
        <DraftForm
          draft={editing.draft}
          isNew={editing.isNew}
          repos={goal.repos}
          others={base.map((d) => d.draft_id).filter((id) => id !== editing.draft.draft_id || editing.isNew)}
          onCancel={() => setEditing(null)}
          onSave={(draft) => {
            change(editing.isNew ? [...base, draft] : base.map((d) => (d.draft_id === draft.draft_id ? draft : d)));
            setEditing(null);
          }}
        />
      )}
      <Modal opened={asking === "approve"} onClose={() => setAsking(null)} title="Approve the plan">
        <Stack gap="md">
          <Text size="sm">
            This creates {drafts.length} {drafts.length === 1 ? "issue" : "issues"} on GitHub with the label {shown.label}, in the order shown, and
            starts the goal: its tasks then run each issue to a pull request.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(null)}>Not yet</Button>
            <Button color="teal" loading={busy} onClick={() => void run(() => api.approvePlan(goal.id), onApproved)}>Create the issues and start</Button>
          </Group>
        </Stack>
      </Modal>
      <Modal opened={asking === "replan"} onClose={() => setAsking(null)} title="Ask for a new plan">
        <Stack gap="md">
          <Text size="sm">A new planning task drafts the plan again, given this plan and your note. It replaces this version when it ends.</Text>
          <Textarea label="Note for the planner" placeholder="What to do differently" autosize minRows={3} value={note} onChange={(e) => setNote(e.currentTarget.value)} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setAsking(null)}>Cancel</Button>
            <Button loading={busy} onClick={() => void run(() => api.replan(goal.id, note.trim()))}>Replan</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
