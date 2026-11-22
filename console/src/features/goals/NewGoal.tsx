import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert, Anchor, Button, Checkbox, Collapse, Group, NativeSelect, NumberInput, Paper, SegmentedControl, SimpleGrid, Stack, Switch, Text, TextInput, Textarea, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconDeviceFloppy, IconListCheck, IconPlayerPlay } from "@tabler/icons-react";
import { IssuesPicker } from "./IssuesPicker";
import { useUnsavedChanges } from "../../components/UnsavedChanges";
import { api, refLabel, type GoalDraft, type IssueSource, type LabelIssue, type Tracker } from "../../api";
import { GoalTrackerFields } from "../trackers/GoalTrackerFields";
import { LabelField } from "./LabelField";
import { LabelStart } from "./LabelStart";
import { ImproveGoal } from "./ImproveGoal";
import { SpecField, type GoalSpec } from "./SpecField";
import { PageHeader } from "../../components/ui";
import { PARALLEL_HELP } from "./parallel";

type LabeledIssue = LabelIssue & { repo: string };
const SHOWN = 8;

// What the goal's label brings in: its open issues in the goal's repos, by title. Issues another goal holds
// are listed apart, since this goal will not get them.
export function LabelIssues({ label, issues }: { label: string; issues: LabeledIssue[] | null }) {
  if (!label.trim() || issues === null) return null;
  const free = issues.filter((i) => !i.held_by);
  const held = issues.length - free.length;
  // The label field already says no open issue has it; this says only what then happens.
  if (!issues.length) return <Text size="sm" c="dimmed">The goal takes in issues with this label as they appear.</Text>;
  return (
    <Stack gap={4} data-testid="label-issues">
      <Text size="sm" fw={600}>
        {free.length} open {free.length === 1 ? "issue" : "issues"} with this label{held ? `, and ${held} more that another goal holds` : ""}
      </Text>
      {free.slice(0, SHOWN).map((i) => (
        <Text key={i.ref} size="sm">
          {i.title} <Text span size="xs" c="dimmed" ff="monospace">{refLabel(i.ref)}</Text>
        </Text>
      ))}
      {free.length > SHOWN && <Text size="xs" c="dimmed">and {free.length - SHOWN} more</Text>}
      <Text size="sm" c="dimmed">The goal takes every open issue with this label, now and later. Pick issues adds others.</Text>
    </Stack>
  );
}

export function NewGoal() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<string[]>([]);
  // Whether the registered repos have been read: only then does "none" mean there are none.
  const [reposRead, setReposRead] = useState(false);
  const [title, setTitle] = useState("");
  const [statement, setStatement] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  // Issues picked by ref: the goal holds these besides its label's, and needs no label when there are some.
  const [refs, setRefs] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  const [label, setLabel] = useState("");
  // The issue tracker the goal uses, one for all its issues: the default one, unless another that is set up is
  // chosen. Empty until the trackers are read; the service then uses the default.
  const [tracker, setTracker] = useState("");
  const [trackers, setTrackers] = useState<Tracker[]>([]);
  // Until the trackers are read, what the goal's tracker asks of it is not known, so no issues are read for it.
  const [trackersRead, setTrackersRead] = useState(false);
  useEffect(() => {
    api.trackers().then(
      (found) => {
        setTrackersRead(true);
        setTrackers(found.trackers.filter((t) => t.configured));
        setTracker((now) => now || found.default);
        // A goal starts with what its tracker's setup says a new one does: Jira's default project.
        const defaults = found.trackers.find((t) => t.scheme === found.default)?.goal_defaults ?? {};
        setTrackerSettings((now) => (Object.keys(now).length ? now : { ...defaults }));
      },
      () => setTrackersRead(true),
    );
  }, []);
  // What the tracker asks of a goal besides its repos, such as the Jira project its issues are in.
  const [trackerSettings, setTrackerSettings] = useState<Record<string, string>>({});
  // Another tracker's issues, or another project's, are other issues: the picked ones start again.
  function chooseTracker(next: string) {
    setTracker(next);
    setTrackerSettings({ ...(trackers.find((t) => t.scheme === next)?.goal_defaults ?? {}) });
    setRefs([]);
  }
  function chooseSettings(next: Record<string, string>) {
    setTrackerSettings(next);
    // A switch changes which issues come in by themselves, and the assignee who gets them. Neither changes
    // where the issues are, so the picked ones stay.
    const places = asked.filter((s) => s.kind !== "switch" && s.name !== "assignee");
    if (places.some((s) => (next[s.name] ?? "") !== (trackerSettings[s.name] ?? ""))) setRefs([]);
  }
  const asked = trackers.find((t) => t.scheme === tracker)?.goal_settings ?? [];
  // No tracker: the goal has no issues to take in, so its tasks are planned from its description, and kept
  // in the meta-harness.
  const untracked = !!trackers.find((t) => t.scheme === tracker)?.built_in;
  const missing = asked.filter((s) => s.required && !(trackerSettings[s.name] ?? "").trim());
  // A goal that takes the issues assigned to the meta-harness in its tracker has issues to take in without a
  // label or picked ones.
  const takesAssigned = asked.some((s) => s.name === "assigned" && s.kind === "switch") && trackerSettings.assigned === "yes";
  // Which issues the form reads: none until the tracker has what it asks of a goal.
  const source = useMemo<IssueSource | undefined>(
    () => (tracker ? { tracker, settings: trackerSettings } : undefined),
    [tracker, trackerSettings],
  );
  const readable = trackersRead && missing.length === 0;
  const [budget, setBudget] = useState<string | number>("");
  // What the budget does when it is reached, and how far past it a goal that stops may go.
  const [budgetMode, setBudgetMode] = useState<"block" | "warn">("block");
  const [overage, setOverage] = useState<string | number>(10);
  const [parallel, setParallel] = useState<string | number>("");
  const [systemParallel, setSystemParallel] = useState<number | null>(null);
  // Whether every goal is planned before its tasks run, as the planning configuration says.
  const [plansEvery, setPlansEvery] = useState(false);
  useEffect(() => {
    api.limits().then(
      (l) => {
        setSystemParallel(l.max_parallel);
        setPlansEvery(!!l.plans_every_goal);
      },
      () => setSystemParallel(null),
    );
  }, []);
  const [priority, setPriority] = useState<string | number>(0);
  const [autoMerge, setAutoMerge] = useState(false);
  // A specification to plan the goal from: a loaded file, or the goal's parent issue.
  const [spec, setSpec] = useState<GoalSpec | null>(null);
  // The settings most goals leave as they are, folded away until asked for.
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which button submitted the form: Save (false) or Save and start (true). Null while not saving.
  const [saving, setSaving] = useState<boolean | null>(null);
  const start = useRef(true);

  useEffect(() => {
    api.repos().then(
      (r) => {
        setRepos(r.map((x) => x.name));
        setReposRead(true);
      },
      (err) => setError(String(err.message ?? err)),
    );
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
    if (!name || !chosen.length || !readable) {
      setLabeled(null);
      return;
    }
    let stale = false;
    const wait = setTimeout(() => {
      Promise.all(chosen.map(async (repo) => (await api.labelIssues(repo, name, source)).map((issue) => ({ ...issue, repo })))).then(
        (found) => !stale && setLabeled(found.flat()),
        () => !stale && setLabeled(null),
      );
    }, 300);
    return () => {
      stale = true;
      clearTimeout(wait);
    };
  }, [label, chosen, source, readable]);
  const viaLabel = useMemo(() => (labeled ?? []).filter((i) => !i.held_by).map((i) => i.ref), [labeled]);

  // Whether the goal plans its own issues from its description (a feature goal) or runs issues that exist.
  const [plans, setPlans] = useState(false);

  // Whether this goal plans its tasks: asked for, from a specification, or the only way with no tracker.
  const plansNow = plans || untracked || plansEvery || !!spec;
  // The issue the goal names as its parent, where its tracker has parents: its description can be the specification.
  const parent = asked.some((s) => s.name === "parent") ? (trackerSettings.parent ?? "").trim() : "";
  // A parent that is no longer chosen is no longer the specification.
  useEffect(() => {
    setSpec((now) => (now && "ref" in now && now.ref !== parent ? null : now));
  }, [parent]);

  function fromDraft(draft: GoalDraft) {
    setTitle(draft.title);
    setStatement(draft.statement);
    setChosen(draft.repos);
    if (draft.label) {
      setLabel(draft.label);
    } else {
      // Drafted from several labels: a goal takes one, so it holds the issues the draft read, picked by hand.
      setLabel("");
      setRefs(draft.refs);
    }
  }

  // Creates the goal, started or not. Returns its id, or null when it is refused (the form shows why).
  async function create(started: boolean): Promise<string | null> {
    if (!chosen.length) {
      setError("Choose at least one repository, under Repositories.");
      return null;
    }
    if (!statement.trim()) {
      setError("Describe the goal, under Description.");
      return null;
    }
    if (missing.length) {
      setError(`Choose the goal's ${missing.map((s) => s.label).join(" and ")}.`);
      return null;
    }
    if (!(plans || untracked || spec) && !label.trim() && !refs.length && !takesAssigned) {
      setError(`Say which issues are the goal's: give it a label, or pick issues by hand, under Where its issues come from${asked.some((s) => s.name === "assigned") ? ", or turn on taking the issues assigned to the meta-harness" : ""}.`);
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
        ...(tracker ? { tracker } : {}),
        ...(Object.keys(trackerSettings).length ? { tracker_settings: trackerSettings } : {}),
        refs,
        max_usd: budget === "" ? null : String(budget),
        ...(budget === "" ? {} : { budget_mode: budgetMode, budget_overage_percent: Number(overage) || 0 }),
        max_parallel: parallel === "" ? null : Number(parallel),
        priority: Number(priority) || 0,
        auto_merge: autoMerge,
        start: started,
        ...(spec ? { spec } : {}),
        // Where its issues come from, which is not whether it is planned: the planner decides that.
        modality: plans || untracked ? "feature" : "fix",
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
      <PageHeader title="New goal" description="A goal says which repositories to work in and which issues are its work. Each issue becomes a task, and each task a pull request." />
      {unsaved.element}
      {/* The quick ways to start a goal come first: from the issues a label holds, or from a spec or an epic. */}
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="lg" maw={1120}>
        {readable && !untracked && <LabelStart onDraft={fromDraft} source={source} />}
        <SpecField value={spec} onChange={setSpec} parent={parent || undefined} parentLabel={parent ? refLabel(parent) : undefined} />
      </SimpleGrid>
      <Paper maw={1120}>
        <form onSubmit={submit}>
          <Stack gap="lg">
            {/* Two columns where there is room, so the whole form is in view: what the goal is on the left,
                where its work comes from on the right. */}
            <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="xl" verticalSpacing="lg">
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
            {/* Accept fills the field above; the goal is saved only by the buttons below. */}
            <ImproveGoal
              statement={statement}
              context={{
                ...(title.trim() ? { title: title.trim() } : {}),
                repos: chosen,
                ...(tracker ? { tracker } : {}),
                ...(trackerSettings.project ? { project: trackerSettings.project } : {}),
                ...(label.trim() ? { label: label.trim() } : {}),
                issues: [
                  ...(labeled ?? []).filter((i) => !i.held_by).map((i) => ({ ref: i.ref, title: i.title })),
                  ...refs.filter((r) => !viaLabel.includes(r)).map((ref) => ({ ref })),
                ].slice(0, 200),
              }}
              onAccept={setStatement}
            />
            <Checkbox.Group label="Repositories" description="The repositories the goal may change." withAsterisk value={chosen} onChange={setChosen}>
              <Stack gap="xs" mt="xs">
                {reposRead && repos.length === 0 && (
                  <Text size="sm" c="orange" data-testid="no-repos">
                    No repository is registered yet, so a goal has nothing to change. An admin adds one under{" "}
                    <Anchor component={Link} to="/config?kind=repos" size="sm">Configuration, Repositories</Anchor>.
                  </Text>
                )}
                {repos.map((name) => <Checkbox key={name} value={name} label={name} styles={{ label: { fontFamily: "var(--mantine-font-family-monospace)" } }} />)}
              </Stack>
            </Checkbox.Group>
              </Stack>
              <Stack gap="lg">
            <GoalTrackerFields trackers={trackers} tracker={tracker} onTracker={chooseTracker} settings={trackerSettings} onSettings={chooseSettings} repos={chosen} />
            {untracked ? (
              <Text size="sm" data-testid="untracked-note">
                With no issue tracker, a planning task reads the repos and writes the goal's tasks from its description. You review, edit
                and approve the plan before anything runs. The tasks are kept in the meta-harness: nothing is written to any tracker, and
                the pull requests are the only record outside it.
              </Text>
            ) : (
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
              <Text size="sm" c="dimmed" mt={4}>
                {plans
                  ? "A planning task reads the repos and drafts the issues. You review, edit and approve the plan before anything is created or run."
                  : plansEvery
                    ? "The goal takes the open issues with its label, and any you pick. A planner reads them together and proposes the order, any splits and anything missing; you approve its plan before anything runs."
                    : "The goal runs the open issues with its label, and any you pick."}
              </Text>
            </div>
            )}
            {untracked ? null : plans ? (
              <LabelField
                value={label}
                onChange={setLabel}
                repos={chosen}
                source={readable ? source : null}
                required={false}
                fieldProps={{ description: "Optional. Left empty, the goal gets a label of its own for the issues it creates." }}
              />
            ) : (
              // Which issues, for a goal that runs issues that exist: those with its label, ones picked by hand, or both.
              <Stack gap="md" pl="md" style={{ borderLeft: "2px solid var(--mantine-color-default-border)" }} data-testid="existing-issues">
                <Text size="sm" c="dimmed">Give the goal a label, pick issues by hand, or both. It needs at least one of the two.</Text>
                <LabelField value={label} onChange={setLabel} repos={chosen} source={readable ? source : null} required={false} />
                <LabelIssues label={label} issues={labeled} />
                <Group gap="sm">
                  <Button variant="default" leftSection={<IconListCheck size={16} />} disabled={!chosen.length || !readable} onClick={() => setPicking(true)}>
                    Pick issues
                  </Button>
                  <Text size="sm" c="dimmed">
                    {!chosen.length
                      ? "Choose a repository above first: issues are picked from the goal's repositories."
                      : !trackersRead
                        ? "Reading the issue tracker…"
                        : !readable
                        ? `Choose the goal's ${missing.map((s) => s.label).join(" and ")} above first.`
                        : refs.length
                          ? `${refs.length} picked by hand: the goal takes these besides its label's, or instead of a label.`
                          : "Optional: pick open issues for the goal by hand."}
                  </Text>
                </Group>
              </Stack>
            )}
              </Stack>
            </SimpleGrid>
            <IssuesPicker opened={picking} onClose={() => setPicking(false)} repos={chosen} chosen={refs} viaLabel={viaLabel} action="Use these issues" onPick={setRefs} source={source} />
            <NumberInput
              label="Budget"
              description="What the goal's runs may spend. Leave it empty for none."
              prefix="$"
              min={0}
              decimalScale={2}
              maw={260}
              value={budget}
              onChange={setBudget}
            />
            <div>
              <UnstyledButton onClick={() => setMore((now) => !now)} aria-expanded={more} data-testid="more-settings">
                <Group gap={6}>
                  {more ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
                  <Text size="sm" fw={500}>More settings</Text>
                  <Text size="sm" c="dimmed">priority, parallel tasks, what the budget does when it is reached</Text>
                </Group>
              </UnstyledButton>
              <Collapse expanded={more}>
                <Stack gap="md" mt="sm">
                  <Group grow align="flex-start">
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
                  {budget === "" ? (
                    <Text size="sm" c="dimmed">Give the goal a budget to choose what happens when it is reached.</Text>
                  ) : (
                    <Group grow align="flex-start">
                      <NativeSelect
                        label="When the budget is reached"
                        description="Stop: no more of its tasks start. Warn: the overrun is recorded and shown, and the goal keeps going."
                        data={[{ value: "block", label: "Stop" }, { value: "warn", label: "Warn and keep going" }]}
                        value={budgetMode}
                        onChange={(e) => setBudgetMode(e.currentTarget.value as "block" | "warn")}
                      />
                      {budgetMode === "block" && (
                        <NumberInput
                          label="Allowed overage"
                          description="How far past its budget the goal may go before it stops. 0 stops at the budget exactly."
                          suffix="%"
                          min={0}
                          allowDecimal={false}
                          value={overage}
                          onChange={setOverage}
                        />
                      )}
                    </Group>
                  )}
                </Stack>
              </Collapse>
            </div>
            {/* The one setting that decides whether a person is in the loop, right above the buttons. */}
            <Switch
              label="Merge pull requests automatically"
              description="Off: every pull request waits for you to merge it."
              checked={autoMerge}
              onChange={(e) => setAutoMerge(e.currentTarget.checked)}
            />
            {/* Beside the buttons that were pressed: a refusal is never out of sight at the top of a long form. */}
            {error && (
              <Alert color="red" title="The goal was not saved" data-testid="goal-not-saved">
                <Text size="sm">{error}</Text>
                <Text size="sm" mt={4}>Nothing was created. Fix what it names, then save again.</Text>
              </Alert>
            )}
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
                {plansNow ? "Save and plan" : "Save and start"}
              </Button>
            </Group>
          </Stack>
        </form>
      </Paper>
    </>
  );
}
