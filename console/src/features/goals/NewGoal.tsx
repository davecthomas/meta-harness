import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Collapse,
  Group,
  Loader,
  NumberInput,
  Paper,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
  UnstyledButton,
} from "@mantine/core";
import {
  IconArrowRight,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconDeviceFloppy,
  IconFileDescription,
  IconListCheck,
  IconPencil,
  IconPlayerPlay,
  IconWriting,
} from "@tabler/icons-react";
import { IssuesPicker } from "./IssuesPicker";
import { useUnsavedChanges } from "../../components/UnsavedChanges";
import { api, refLabel, type IssueSource, type LabelIssue, type Tracker, type UserSettings } from "../../api";
import { GoalTrackerFields } from "../trackers/GoalTrackerFields";
import { LabelField } from "./LabelField";
import { RepoPick } from "./RepoPick";
import { ImproveGoal } from "./ImproveGoal";
import { SpecField, type GoalSpec } from "./SpecField";
import { specName, useSpecDraft } from "./SpecDraft";
import { PARALLEL_HELP } from "./parallel";
import { AUTONOMY, AutonomyPicker, type Autonomy } from "./autonomy";

// A new goal, one decision at a time (docs/specs/new-goal-progressive.md): where the work comes from, where it
// is, what the goal is made of, and how it runs. Each panel opens once the one before is answered, and folds to
// its answer with a pencil to change it. The title is never asked for: it is written from what the goal is, and
// shown as the page's title, with a pencil.

type LabeledIssue = LabelIssue & { repo: string };
const SHOWN = 8;

// Where the goal's work comes from: issues that exist, a spec or an epic, or what the operator describes.
export type Source = "issues" | "spec" | "describe";
const SOURCES: { value: Source; label: string; says: string; icon: typeof IconListCheck }[] = [
  { value: "issues", label: "Issues that already exist", says: "Run open issues as they are: by label, picked by hand, or assigned to Muxxy.", icon: IconListCheck },
  { value: "spec", label: "A spec or an epic", says: "A planner splits a document or an epic into tasks. You approve the plan before anything runs.", icon: IconFileDescription },
  { value: "describe", label: "I'll describe it", says: "A planner splits your description into tasks. You approve the plan before anything runs.", icon: IconWriting },
];

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

// One panel of the flow: its number and question, and once answered and closed, its answer with a pencil.
function Panel(props: { n: number; question: string; answer: string; open: boolean; done: boolean; onEdit: () => void; children: ReactNode }) {
  return (
    <Paper withBorder p="lg" radius="md" maw={960} data-testid={`step-${props.n}`} data-open={props.open || undefined}>
      <Group justify="space-between" wrap="nowrap" align="center">
        <Group gap="sm" wrap="nowrap">
          <Badge size="lg" circle variant={props.done && !props.open ? "light" : "filled"} color={props.done && !props.open ? "teal" : undefined}>
            {props.done && !props.open ? <IconCheck size={14} /> : props.n}
          </Badge>
          <Stack gap={0}>
            <Text fw={600}>{props.question}</Text>
            {!props.open && props.done && <Text size="sm" c="dimmed" data-testid={`answer-${props.n}`}>{props.answer}</Text>}
          </Stack>
        </Group>
        {!props.open && props.done && (
          <ActionIcon variant="subtle" aria-label={`Change: ${props.question}`} onClick={props.onEdit}>
            <IconPencil size={18} />
          </ActionIcon>
        )}
      </Group>
      {/* Shown at once, not animated: a panel that opens is where the eye already is. */}
      {props.open && <Stack gap="md" mt="md">{props.children}</Stack>}
    </Paper>
  );
}

export function NewGoal() {
  const navigate = useNavigate();
  // Which panel is open, and the source chosen, live in the address: Back steps back one panel.
  const [params, setParams] = useSearchParams();
  const step = Math.min(4, Math.max(1, Number(params.get("step")) || 1));
  const source = (["issues", "spec", "describe"].includes(params.get("from") ?? "") ? params.get("from") : null) as Source | null;
  function go(next: number, from: Source | null = source) {
    const kept = new URLSearchParams(params);
    kept.set("step", String(next));
    if (from) kept.set("from", from);
    setParams(kept);
  }
  // The furthest panel answered: a panel before it shows its answer, one after it is not reached yet.
  const [reached, setReached] = useState(1);

  const [repos, setRepos] = useState<string[]>([]);
  // Whether the registered repos have been read: only then does "none" mean there are none.
  const [reposRead, setReposRead] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  // Whether the operator wrote the title: one they wrote is never written over.
  const [titleOwn, setTitleOwn] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titling, setTitling] = useState(false);
  const [statement, setStatement] = useState("");
  // Whether the operator wrote or changed the description: a drafted one is written again as its issues change.
  const [statementOwn, setStatementOwn] = useState(false);
  const [drafting, setDrafting] = useState(false);
  // Issues picked by ref: the goal holds these besides its label's, and needs no label when there are some.
  const [refs, setRefs] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  const [label, setLabel] = useState("");
  // The issue tracker the goal uses, one for all its issues: the default one until another is chosen.
  const [tracker, setTracker] = useState("");
  const [trackers, setTrackers] = useState<Tracker[]>([]);
  // Until the trackers are read, what the goal's tracker asks of it is not known, so no issues are read for it.
  const [trackersRead, setTrackersRead] = useState(false);
  // What the tracker asks of a goal besides its repos, such as the Jira project its issues are in.
  const [trackerSettings, setTrackerSettings] = useState<Record<string, string>>({});
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
  // Another tracker's issues, or another project's, are other issues: the picked ones start again.
  function chooseTracker(next: string) {
    setTracker(next);
    setTrackerSettings({ ...(trackers.find((t) => t.scheme === next)?.goal_defaults ?? {}) });
    setRefs([]);
    // An issue of the tracker left behind is no longer the specification.
    setSpec((now) => (now && "ref" in now ? null : now));
  }
  const asked = trackers.find((t) => t.scheme === tracker)?.goal_settings ?? [];
  function chooseSettings(next: Record<string, string>) {
    setTrackerSettings(next);
    // A switch changes which issues come in by themselves, and the assignee who gets them. Neither changes
    // where the issues are, so the picked ones stay.
    const places = asked.filter((s) => s.kind !== "switch" && s.name !== "assignee" && s.name !== "parent");
    if (places.some((s) => (next[s.name] ?? "") !== (trackerSettings[s.name] ?? ""))) setRefs([]);
  }
  // No tracker: the goal has no issues to take in, so its tasks are planned and kept in Muxxy.
  const builtIn = (scheme: string) => !!trackers.find((t) => t.scheme === scheme)?.built_in;
  const untracked = builtIn(tracker);
  const realTrackers = trackers.filter((t) => !t.built_in);
  const missing = asked.filter((s) => s.required && !(trackerSettings[s.name] ?? "").trim());
  const takesAssigned = asked.some((s) => s.name === "assigned" && s.kind === "switch") && trackerSettings.assigned === "yes";
  const issueSource = useMemo<IssueSource | undefined>(() => (tracker ? { tracker, settings: trackerSettings } : undefined), [tracker, trackerSettings]);
  const readable = trackersRead && missing.length === 0;
  const trackerTitle = trackers.find((t) => t.scheme === tracker)?.title ?? "the tracker";

  const [budget, setBudget] = useState<string | number>("");
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
  const [autonomy, setAutonomy] = useState<Autonomy>("standard");
  // Whether a second model reviews the goal's plan before it is approved. Null: not chosen by hand, so it
  // follows the autonomy: on for a goal that asks before each step, whose operator chose care over speed.
  const [reviewed, setReviewed] = useState<boolean | null>(null);
  const reviews = reviewed ?? autonomy === "ask";
  // The operator's settings as read, to keep the autonomy chosen here as the next goal's start.
  const saved = useRef<UserSettings | null>(null);
  const [spec, setSpec] = useState<GoalSpec | null>(null);
  // The settings most goals leave as they are, folded away until asked for.
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which button submitted the form: Save (false) or Save and start (true). Null while not saving.
  const [saving, setSaving] = useState<boolean | null>(null);
  const start = useRef(true);

  useEffect(() => {
    api.goalRepos().then(
      (r) => {
        const names = r.map((x) => x.name);
        setRepos(names);
        setReposRead(true);
        // A single registered repo is the only choice there is, so it starts chosen.
        if (names.length === 1) setChosen((now) => (now.length ? now : names));
      },
      (err) => setError(String(err.message ?? err)),
    );
    // The form starts from the user's planner defaults, filling only what they haven't touched yet.
    api.settings().then(
      (s) => {
        saved.current = s;
        setChosen((now) => (now.length ? now : s.planner.repos));
        setBudget((now) => (now === "" && s.planner.max_usd ? Number(s.planner.max_usd) : now));
        // What was chosen last time is where this goal starts.
        const last = s.planner.autonomy ?? (s.planner.auto_merge ? "full" : "standard");
        setAutonomy((now) => (now === "standard" ? last : now));
      },
      () => undefined,
    );
  }, []);

  // The open issues the goal's label takes, in its repos, so the form shows what the goal will run.
  const [labeled, setLabeled] = useState<LabeledIssue[] | null>(null);
  useEffect(() => {
    const name = label.trim();
    if (!name || !chosen.length || !readable) {
      setLabeled(null);
      return;
    }
    let stale = false;
    const wait = setTimeout(() => {
      Promise.all(chosen.map(async (repo) => (await api.labelIssues(repo, name, issueSource)).map((issue) => ({ ...issue, repo })))).then(
        (found) => !stale && setLabeled(found.flat()),
        () => !stale && setLabeled(null),
      );
    }, 300);
    return () => {
      stale = true;
      clearTimeout(wait);
    };
  }, [label, chosen, issueSource, readable]);
  const viaLabel = useMemo(() => (labeled ?? []).filter((i) => !i.held_by).map((i) => i.ref), [labeled]);

  // The issue the goal names as its parent, where its tracker has parents: its description can be the spec.
  const parentAsked = asked.some((s) => s.name === "parent");
  const parent = parentAsked ? (trackerSettings.parent ?? "").trim() : "";
  useEffect(() => {
    if (parentAsked) setSpec((now) => (now && "ref" in now && now.ref !== parent ? null : now));
  }, [parent, parentAsked]);

  // A spec that is chosen writes the title and description, where the operator has not.
  const specDraft = useSpecDraft({
    spec: source === "spec" ? spec : null,
    repos: chosen,
    tracker,
    trackerSettings,
    title: titleOwn ? title : "",
    statement: statementOwn ? statement : "",
    setTitle: (next) => !titleOwn && setTitle(next),
    setStatement: (next) => !statementOwn && setStatement(next),
  });

  // Each panel's answer, and whether it is answered enough to go on.
  const sourceAnswer = SOURCES.find((s) => s.value === source)?.label ?? "";
  const whereReady = chosen.length > 0 && missing.length === 0 && (source !== "issues" || !untracked) && (source !== "spec" || !spec || !("ref" in spec) || !untracked);
  const whereAnswer = `${chosen.join(", ")}${untracked ? " · kept in Muxxy" : ` · ${trackerTitle}`}${asked.filter((s) => s.required).map((s) => trackerSettings[s.name] ? ` ${trackerSettings[s.name]}` : "").join("")}`;
  const madeReady =
    source === "issues"
      ? Boolean(label.trim() || refs.length || takesAssigned)
      : source === "spec"
        ? Boolean(spec)
        : Boolean(statement.trim());
  const madeAnswer =
    source === "issues"
      ? [label.trim() && `label ${label.trim()}${labeled ? ` (${viaLabel.length} open)` : ""}`, refs.length && `${refs.length} picked`, takesAssigned && "assigned to Muxxy"].filter(Boolean).join(" · ")
      : source === "spec"
        ? spec ? specName(spec) : ""
        : statement.trim().slice(0, 90);
  const planned = source !== "issues" || plansEvery;
  const runsAnswer = `${AUTONOMY[autonomy].label}${planned && reviews ? " · adversarial plan review" : ""} · ${budget === "" ? "the most one goal may spend" : `budget $${budget}`}`;

  // The description is always the system's to write, from the issues, the spec, or what the operator typed:
  // drafted on the way to the last panel, unless the operator wrote their own.
  async function describe() {
    if (source === "issues" && !statementOwn) {
      setDrafting(true);
      try {
        const drafted = await api.draftGoal({
          labels: label.trim() ? [label.trim()] : [],
          repos: chosen,
          refs: label.trim() ? [] : refs,
          ...(tracker ? { tracker } : {}),
          ...(Object.keys(trackerSettings).length ? { tracker_settings: trackerSettings } : {}),
        });
        setStatement(drafted.statement);
        if (!titleOwn) setTitle(drafted.title);
      } catch {
        // A draft that failed leaves the description for the operator, and the goal is titled after it saves.
        if (!statement.trim()) setStatement(`Run the goal's issues: ${madeAnswer}.`);
      } finally {
        setDrafting(false);
      }
    }
    if (source === "describe" && !titleOwn && statement.trim()) {
      setTitling(true);
      api.goalTitle(statement).then(
        (t) => !titleOwn && setTitle(t.title),
        () => undefined,
      ).finally(() => setTitling(false));
    }
  }

  function answer(n: number) {
    setReached((now) => Math.max(now, n + 1));
    if (n === 3) void describe();
    go(n + 1);
  }
  function chooseSource(next: Source) {
    // Another source makes panel 3's answer another question: it starts again, and says so.
    if (next !== source) {
      setSpec(null);
      setLabel("");
      setRefs([]);
      if (!statementOwn) setStatement("");
      if (!titleOwn) setTitle("");
    }
    setReached((now) => Math.max(now, 2));
    go(2, next);
  }

  // What the goal still needs before it can be saved; null once it has what it needs.
  const lacking: string | null = !source
    ? "Choose where the work comes from."
    : !whereReady
      ? "Choose the goal's repositories and tracker, in panel 2."
      : !madeReady
        ? "Say what the goal is made of, in panel 3."
        : drafting || specDraft.writing
          ? "Writing the description…"
          : !statement.trim()
            ? "The description is being written. If it does not appear, type one in panel 3."
            : null;

  async function create(started: boolean): Promise<string | null> {
    if (lacking) {
      setError(lacking);
      return null;
    }
    setSaving(started);
    try {
      const goal = await api.createGoal({
        ...(title.trim() ? { title: title.trim() } : {}),
        statement,
        repos: chosen,
        ...(label.trim() ? { label: label.trim() } : {}),
        ...(tracker ? { tracker } : {}),
        ...(Object.keys(trackerSettings).length ? { tracker_settings: trackerSettings } : {}),
        refs,
        max_usd: budget === "" ? null : String(budget),
        ...(budget === "" ? {} : { budget_overage_percent: Number(overage) || 0 }),
        max_parallel: parallel === "" ? null : Number(parallel),
        priority: Number(priority) || 0,
        autonomy,
        ...(planned ? { adversarial_review: reviews } : {}),
        start: started,
        ...(source === "spec" && spec ? { spec } : {}),
        // Where its issues come from: issues that exist run as they are; anything else is planned.
        modality: source === "issues" && !untracked ? "fix" : "feature",
      });
      // The choice made here is the next goal's default. Not saving it is no reason to fail the goal.
      const mine = saved.current;
      if (mine && mine.planner.autonomy !== autonomy) {
        const kept = { ...mine, planner: { ...mine.planner, autonomy, auto_merge: autonomy === "full" } };
        void api.saveSettings(kept).then((s) => (saved.current = s), () => undefined);
      }
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

  const dirty = Boolean(source || statementOwn || label.trim() || refs.length || spec);
  const unsaved = useUnsavedChanges({
    dirty,
    describe: `the new goal${title.trim() ? ` "${title.trim().slice(0, 60)}"` : ""}`,
    // The open panel is in the address: stepping between panels is not leaving.
    ownsQuery: true,
    // Save here saves the goal without starting it, so leaving never starts work.
    save: async () => (await create(false)) !== null,
    discard: () => {
      setStatement("");
      setLabel("");
      setRefs([]);
      setSpec(null);
    },
  });

  // Backed out to the first question, the goal starts again: the title written for what it was goes too.
  useEffect(() => {
    if (step === 1 && !source) {
      setTitle("");
      setTitleOwn(false);
      setEditingTitle(false);
      // Nothing is answered any more: the later panels, and the title's pencil, wait for the first answer again.
      setReached(1);
    }
  }, [step, source]);

  const open = (n: number) => step === n;
  const done = (n: number) => reached > n;

  return (
    <>
      {/* The goal's title is the page's title: "New goal" until one is written from what the goal is. */}
      <Group gap="xs" mb="lg" wrap="nowrap" data-testid="goal-title">
        {editingTitle ? (
          <TextInput
            autoFocus
            size="lg"
            maxLength={80}
            aria-label="The goal's title"
            value={title}
            onChange={(e) => {
              setTitle(e.currentTarget.value);
              setTitleOwn(true);
            }}
            onBlur={() => setEditingTitle(false)}
            onKeyDown={(e) => e.key === "Enter" && setEditingTitle(false)}
            style={{ flex: 1, maxWidth: 720 }}
          />
        ) : (
          <Title order={2}>{title.trim() || "New goal"}</Title>
        )}
        {titling && <Loader size="xs" aria-label="Writing a title" />}
        {!editingTitle && (title.trim() || reached > 3) && (
          <ActionIcon variant="subtle" aria-label="Change the goal's title" onClick={() => setEditingTitle(true)}>
            <IconPencil size={18} />
          </ActionIcon>
        )}
      </Group>
      {unsaved.element}
      <form onSubmit={submit}>
        <Stack gap="md">
          <Panel n={1} question="Where does the work come from?" answer={sourceAnswer} open={open(1)} done={!!source} onEdit={() => go(1)}>
            <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
              {SOURCES.map((s) => {
                const blocked = s.value === "issues" && trackersRead && !realTrackers.length;
                return (
                  <UnstyledButton key={s.value} disabled={blocked} onClick={() => chooseSource(s.value)} data-testid={`source-${s.value}`} aria-pressed={source === s.value}>
                    <Paper withBorder p="md" radius="md" h="100%" style={{ borderColor: source === s.value ? "var(--mantine-primary-color-filled)" : undefined, opacity: blocked ? 0.5 : 1 }}>
                      <Stack gap={6}>
                        <s.icon size={22} />
                        <Text fw={600}>{s.label}</Text>
                        <Text size="sm" c="dimmed">{blocked ? "No issue tracker is set up yet. An admin adds one under Configuration, Trackers." : s.says}</Text>
                      </Stack>
                    </Paper>
                  </UnstyledButton>
                );
              })}
            </SimpleGrid>
          </Panel>

          {reached >= 2 && source && (
            <Panel n={2} question="Where is the work?" answer={whereAnswer} open={open(2)} done={done(2)} onEdit={() => go(2)}>
              <SimpleGrid cols={{ base: 1, md: 2 }} spacing="xl">
                <RepoPick offered={reposRead ? repos : null} value={chosen} onChange={setChosen} label="Repositories" description="The repositories the goal may change." required />
                <Stack gap="xs">
                  <GoalTrackerFields
                    trackers={source === "issues" ? realTrackers : trackers}
                    tracker={tracker}
                    onTracker={chooseTracker}
                    settings={trackerSettings}
                    onSettings={chooseSettings}
                    repos={chosen}
                    shows={(s) => s.required}
                  />
                  <Text size="sm" c="dimmed">
                    {source === "issues"
                      ? "Where the issues are."
                      : untracked
                        ? "No tracker: the planned tasks are kept in Muxxy, and the pull requests are the only record outside it."
                        : `The planned tasks are created as issues in ${trackerTitle}.`}
                  </Text>
                  {source === "issues" && untracked && <Text size="sm" c="orange">Choose the tracker the issues are in.</Text>}
                </Stack>
              </SimpleGrid>
              <Group justify="flex-end">
                <Button rightSection={<IconArrowRight size={16} />} disabled={!whereReady} onClick={() => answer(2)}>Continue</Button>
              </Group>
            </Panel>
          )}

          {reached >= 3 && source && (
            <Panel n={3} question={source === "issues" ? "Which issues?" : source === "spec" ? "Which spec?" : "What should it do?"} answer={madeAnswer} open={open(3)} done={done(3)} onEdit={() => go(3)}>
              {source === "issues" && (
                <Stack gap="md" data-testid="existing-issues">
                  <Text size="sm" c="dimmed">Give the goal a label, pick issues by hand, or both.</Text>
                  <LabelField value={label} onChange={setLabel} repos={chosen} source={readable ? issueSource : null} required={false} />
                  <LabelIssues label={label} issues={labeled} />
                  <Group gap="sm">
                    <Button variant="default" leftSection={<IconListCheck size={16} />} disabled={!readable} onClick={() => setPicking(true)}>Pick issues</Button>
                    <Text size="sm" c="dimmed">{refs.length ? `${refs.length} picked by hand.` : "Optional."}</Text>
                  </Group>
                  {/* What the tracker asks besides where the issues are: who gets them, and which come in by themselves. */}
                  <GoalTrackerFields trackers={trackers} tracker={tracker} onTracker={chooseTracker} settings={trackerSettings} onSettings={chooseSettings} repos={chosen} settingsOnly shows={(s) => !s.required && s.name !== "parent"} />
                  {plansEvery && <Text size="sm" c="dimmed">A planner reads the issues together first and proposes the order, any splits and anything missing. You approve its plan before anything runs.</Text>}
                </Stack>
              )}
              {source === "spec" && (
                <SpecField
                  value={spec}
                  onChange={setSpec}
                  repos={chosen}
                  tracker={tracker}
                  project={(trackerSettings.project ?? "").trim()}
                  asksProject={asked.some((s) => s.name === "project")}
                  untracked={untracked}
                  parentAsked={parentAsked}
                  parent={parent}
                  open
                  onParent={(ref) => {
                    const { parent: _gone, ...rest } = trackerSettings;
                    setTrackerSettings(ref ? { ...rest, parent: ref } : rest);
                  }}
                />
              )}
              <Textarea
                label={source === "describe" ? "Description" : "Description"}
                description={
                  source === "describe"
                    ? "Ask for anything. The planner splits it into tasks."
                    : source === "spec"
                      ? "Written from the spec. Change it, or add anything the spec leaves out."
                      : "Written from the issues when you continue. Change it, or add anything they leave out."
                }
                required={source === "describe"}
                autosize
                minRows={source === "describe" ? 4 : 2}
                value={statement}
                onChange={(e) => {
                  setStatement(e.currentTarget.value);
                  setStatementOwn(Boolean(e.currentTarget.value.trim()));
                }}
                data-testid="description"
              />
              {source === "spec" && specDraft.element}
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
                onAccept={(next) => {
                  setStatement(next);
                  setStatementOwn(true);
                }}
              />
              <Group justify="flex-end">
                <Button rightSection={<IconArrowRight size={16} />} disabled={!madeReady} onClick={() => answer(3)}>Continue</Button>
              </Group>
            </Panel>
          )}

          {reached >= 4 && source && (
            <Panel n={4} question="How does it run?" answer={runsAnswer} open={open(4)} done={done(4) || reached >= 4} onEdit={() => go(4)}>
              {drafting && (
                <Group gap="xs" data-testid="drafting">
                  <Loader size="xs" />
                  <Text size="sm" c="dimmed">Writing the description from the issues…</Text>
                </Group>
              )}
              <div>
                <Text size="sm" fw={500} mb={4}>How much it does without asking</Text>
                <AutonomyPicker label="How much it does without asking" value={autonomy} onChange={setAutonomy} />
              </div>
              {planned && (
                <Switch
                  label="Adversarial plan review"
                  description="A second AI model tries to find what is wrong with the plan before you approve it. Adds a few minutes and one or two planning runs."
                  checked={reviews}
                  onChange={(event) => setReviewed(event.currentTarget.checked)}
                  data-testid="new-goal-plan-review"
                />
              )}
              <NumberInput
                label="Budget"
                description="What the goal's runs may spend. Left empty, the most one goal may spend."
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
                    <Text size="sm" fw={500}>More</Text>
                    <Text size="sm" c="dimmed">priority, parallel tasks, overage{source !== "issues" ? ", label" : ""}</Text>
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
                    {budget !== "" && (
                      <NumberInput
                        label="Allowed overage"
                        description="How far past its budget the goal may go before it stops, never past the most one goal may spend. 0 stops at the budget exactly."
                        suffix="%"
                        min={0}
                        allowDecimal={false}
                        maw={260}
                        value={overage}
                        onChange={setOverage}
                      />
                    )}
                    {source !== "issues" && !untracked && (
                      <LabelField
                        value={label}
                        onChange={setLabel}
                        repos={chosen}
                        source={readable ? issueSource : null}
                        required={false}
                        fieldProps={{ description: "Optional. Left empty, the goal gets a label of its own for the issues it creates." }}
                      />
                    )}
                  </Stack>
                </Collapse>
              </div>
            </Panel>
          )}

          <IssuesPicker opened={picking} onClose={() => setPicking(false)} repos={chosen} chosen={refs} viaLabel={viaLabel} action="Use these issues" onPick={setRefs} source={issueSource} />

          {reached >= 4 && (
            <Stack gap="xs" maw={960}>
              <Text size="sm" c="dimmed" ta="right" data-testid="plan-note">
                {planned
                  ? `Save and plan starts the planner. Its split into tasks opens on the goal's page for you to edit and approve before anything runs${untracked ? "" : `, and nothing is created in ${trackerTitle} before then`}.`
                  : "Save and start runs the goal's issues now. Save keeps it paused until you start it."}
              </Text>
              {error && (
                <Alert color="red" title="The goal was not saved" data-testid="goal-not-saved">
                  <Text size="sm">{error}</Text>
                  <Text size="sm" mt={4}>Nothing was created. Fix what it names, then save again.</Text>
                </Alert>
              )}
              {lacking && !error && <Text size="sm" c="dimmed" ta="right" data-testid="goal-lacking">{lacking}</Text>}
              <Group justify="flex-end">
                <Button variant="default" onClick={() => navigate(-1)}>Cancel</Button>
                <Button type="submit" variant="default" leftSection={<IconDeviceFloppy size={16} />} loading={saving === false} disabled={saving === true || !!lacking} onClick={() => (start.current = false)}>
                  Save
                </Button>
                <Button type="submit" variant="filled" leftSection={<IconPlayerPlay size={16} />} loading={saving === true} disabled={saving === false || !!lacking} onClick={() => (start.current = true)}>
                  {planned ? "Save and plan" : "Save and start"}
                </Button>
              </Group>
            </Stack>
          )}
        </Stack>
      </form>
    </>
  );
}
