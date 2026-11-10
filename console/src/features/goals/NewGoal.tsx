import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Checkbox, Group, NumberInput, Paper, SegmentedControl, Stack, Switch, Text, TextInput, Textarea } from "@mantine/core";
import { IconDeviceFloppy, IconListCheck, IconPlayerPlay } from "@tabler/icons-react";
import { IssuesPicker } from "./IssuesPicker";
import { useUnsavedChanges } from "../../components/UnsavedChanges";
import { api, type GoalDraft, type LabelIssue } from "../../api";
import { LabelField } from "./LabelField";
import { LabelStart } from "./LabelStart";
import { ErrorAlert, PageHeader } from "../../components/ui";
import { PARALLEL_HELP } from "./parallel";

type LabeledIssue = LabelIssue & { repo: string };
const SHOWN = 8;

// What the goal's label brings in: its open issues in the goal's repos, by title. Issues another goal holds
// are listed apart, since this goal will not get them.
export function LabelIssues({ label, issues }: { label: string; issues: LabeledIssue[] | null }) {
  if (!label.trim() || issues === null) return null;
  const free = issues.filter((i) => !i.held_by);
  const held = issues.length - free.length;
  if (!issues.length) return <Text size="sm" c="dimmed">No open issue has this label in the goal's repos yet. The goal takes them in as they appear.</Text>;
  return (
    <Stack gap={4} data-testid="label-issues">
      <Text size="sm" fw={600}>
        {free.length} open {free.length === 1 ? "issue" : "issues"} with this label{held ? `, and ${held} more that another goal holds` : ""}
      </Text>
      {free.slice(0, SHOWN).map((i) => (
        <Text key={i.ref} size="sm">
          {i.title} <Text span size="xs" c="dimmed" ff="monospace">{i.repo}#{i.number}</Text>
        </Text>
      ))}
      {free.length > SHOWN && <Text size="xs" c="dimmed">and {free.length - SHOWN} more</Text>}
      <Text size="xs" c="dimmed">The goal takes every open issue with this label, now and later. Pick issues adds others.</Text>
    </Stack>
  );
}

export function NewGoal() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [statement, setStatement] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  // Issues picked by ref: the goal holds these besides its label's, and needs no label when there are some.
  const [refs, setRefs] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  const [label, setLabel] = useState("");
  const [budget, setBudget] = useState<string | number>("");
  const [parallel, setParallel] = useState<string | number>("");
  const [systemParallel, setSystemParallel] = useState<number | null>(null);
  useEffect(() => {
    api.limits().then((l) => setSystemParallel(l.max_parallel), () => setSystemParallel(null));
  }, []);
  const [priority, setPriority] = useState<string | number>(0);
  const [autoMerge, setAutoMerge] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which button submitted the form: Save (false) or Save and start (true). Null while not saving.
  const [saving, setSaving] = useState<boolean | null>(null);
  const start = useRef(true);

  useEffect(() => {
    api.repos().then((r) => setRepos(r.map((x) => x.name)), (err) => setError(String(err.message ?? err)));
    // The form starts from the user's planner defaults, filling only what they haven't touched yet.
    api.settings().then(
      (s) => {
        setChosen((now) => (now.length ? now : s.planner.repos));
        setBudget((now) => (now === "" && s.planner.max_usd ? Number(s.planner.max_usd) : now));
        setAutoMerge((now) => now || s.planner.auto_merge);
      },
      () => undefined,
    );
  }, []);

  // The open issues the goal's label takes, in its repos: read as the label and repos change, so the form
  // shows what the goal will run and the picker shows them as already in.
  const [labeled, setLabeled] = useState<LabeledIssue[] | null>(null);
  useEffect(() => {
    const name = label.trim();
    if (!name || !chosen.length) {
      setLabeled(null);
      return;
    }
    let stale = false;
    const wait = setTimeout(() => {
      Promise.all(chosen.map(async (repo) => (await api.labelIssues(repo, name)).map((issue) => ({ ...issue, repo })))).then(
        (found) => !stale && setLabeled(found.flat()),
        () => !stale && setLabeled(null),
      );
    }, 300);
    return () => {
      stale = true;
      clearTimeout(wait);
    };
  }, [label, chosen]);
  const viaLabel = useMemo(() => (labeled ?? []).filter((i) => !i.held_by).map((i) => i.ref), [labeled]);

  // Whether the goal plans its own issues from its description (a feature goal) or runs issues that exist.
  const [plans, setPlans] = useState(false);

  function fromDraft(draft: GoalDraft) {
    setTitle(draft.title);
    setStatement(draft.statement);
    setLabel(draft.label);
    setChosen(draft.repos);
  }

  // Creates the goal, started or not. Returns its id, or null when it is refused (the form shows why).
  async function create(started: boolean): Promise<string | null> {
    if (!chosen.length) {
      setError("Pick at least one repo.");
      return null;
    }
    if (!statement.trim()) {
      setError("Describe the goal.");
      return null;
    }
    if (!plans && !label.trim() && !refs.length) {
      setError("Give the goal a label, or pick its issues.");
      return null;
    }
    setSaving(started);
    try {
      const goal = await api.createGoal({
        // Left empty, the planner's model names the goal from its description.
        ...(title.trim() ? { title: title.trim() } : {}),
        statement,
        repos: chosen,
        ...(label.trim() ? { label: label.trim() } : {}),
        refs,
        max_usd: budget === "" ? null : String(budget),
        max_parallel: parallel === "" ? null : Number(parallel),
        priority: Number(priority) || 0,
        auto_merge: autoMerge,
        start: started,
        modality: plans ? "feature" : "fix",
      });
      return goal.id;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setSaving(null);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const id = await create(start.current);
    if (id) {
      unsaved.release();
      navigate(`/goals/${id}`);
    }
  }

  // Anything typed or drafted is worth asking about before it is lost.
  const dirty = Boolean(title.trim() || statement.trim() || label.trim() || refs.length || budget !== "");
  const unsaved = useUnsavedChanges({
    dirty,
    describe: `the new goal${title.trim() || statement.trim() ? ` "${(title.trim() || statement.trim()).slice(0, 60)}"` : ""}`,
    // Save here saves the goal without starting it, so leaving never starts work.
    save: async () => (await create(false)) !== null,
    discard: () => {
      setTitle("");
      setStatement("");
      setLabel("");
      setRefs([]);
      setBudget("");
    },
  });

  return (
    <>
      <PageHeader title="New goal" description="A goal takes the open issues with its label, in its repos, and runs each one to a pull request." />
      <ErrorAlert error={error} />
      {unsaved.element}
      <LabelStart onDraft={fromDraft} />
      <Paper maw={720}>
        <form onSubmit={submit}>
          <Stack gap="lg">
            <TextInput
              label="Title"
              description="A few words for lists. Leave it empty and a model writes one from the description."
              maxLength={80}
              value={title}
              onChange={(e) => setTitle(e.currentTarget.value)}
            />
            <Textarea
              label="Description"
              description="What the goal is. The planner reads it."
              required
              autosize
              minRows={3}
              value={statement}
              onChange={(e) => setStatement(e.currentTarget.value)}
            />
            <Checkbox.Group label="Repos" description="The registered repos the goal may change." withAsterisk value={chosen} onChange={setChosen}>
              <Stack gap="xs" mt="xs">
                {repos.map((name) => <Checkbox key={name} value={name} label={name} styles={{ label: { fontFamily: "var(--mantine-font-family-monospace)" } }} />)}
              </Stack>
            </Checkbox.Group>
            <div>
              <Text size="sm" fw={500} mb={4}>Where its issues come from</Text>
              <SegmentedControl
                aria-label="Where its issues come from"
                value={plans ? "feature" : "fix"}
                onChange={(v) => setPlans(v === "feature")}
                data={[
                  { value: "fix", label: "Issues that exist" },
                  { value: "feature", label: "Plan them from the description" },
                ]}
              />
              <Text size="xs" c="dimmed" mt={4}>
                {plans
                  ? "A planning task reads the repos and drafts the issues. You review, edit and approve the plan before anything is created or run."
                  : "The goal runs the open issues with its label, and any you pick."}
              </Text>
            </div>
            <LabelField
              value={label}
              onChange={setLabel}
              repos={chosen}
              required={!plans && !refs.length}
              fieldProps={plans ? { description: "Optional. Left empty, the goal gets a label of its own for the issues it creates." } : undefined}
            />
            <LabelIssues label={label} issues={labeled} />
            <Group gap="sm">
              <Button variant="default" leftSection={<IconListCheck size={16} />} disabled={!chosen.length} onClick={() => setPicking(true)}>
                Pick issues
              </Button>
              <Text size="sm" c="dimmed">
                {refs.length ? `${refs.length} picked by hand: the goal takes these besides its label's, or instead of a label.` : "Optional: pick more open issues for the goal by hand."}
              </Text>
            </Group>
            <IssuesPicker opened={picking} onClose={() => setPicking(false)} repos={chosen} chosen={refs} viaLabel={viaLabel} action="Use these issues" onPick={setRefs} />
            <Group grow align="flex-start">
              <NumberInput
                label="Budget"
                description="Optional. No more of the goal's tasks start once its runs have spent this."
                prefix="$"
                min={0}
                decimalScale={2}
                value={budget}
                onChange={setBudget}
              />
              <NumberInput label="Priority" description="Higher runs first." value={priority} onChange={setPriority} />
              <NumberInput
                label="Parallel tasks"
                description={PARALLEL_HELP}
                placeholder={systemParallel ? `System setting: ${systemParallel}` : "System setting"}
                min={1}
                max={systemParallel ?? undefined}
                allowDecimal={false}
                value={parallel}
                onChange={setParallel}
              />
            </Group>
            <Switch
              label="Merge pull requests without me"
              description="Off: every pull request waits for you to merge it."
              checked={autoMerge}
              onChange={(e) => setAutoMerge(e.currentTarget.checked)}
            />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => navigate(-1)}>Cancel</Button>
              <Button
                type="submit"
                variant="default"
                leftSection={<IconDeviceFloppy size={16} />}
                loading={saving === false}
                disabled={saving === true}
                onClick={() => (start.current = false)}
              >
                Save
              </Button>
              <Button
                type="submit"
                variant="filled"
                leftSection={<IconPlayerPlay size={16} />}
                loading={saving === true}
                disabled={saving === false}
                onClick={() => (start.current = true)}
              >
                {plans ? "Save and plan" : "Save and start"}
              </Button>
            </Group>
          </Stack>
        </form>
      </Paper>
    </>
  );
}
