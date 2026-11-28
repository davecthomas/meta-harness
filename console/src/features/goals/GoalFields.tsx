import { useEffect, useState } from "react";
import { AUTONOMY, AutonomyPicker, autonomyOf } from "./autonomy";
import { Badge, Group, NumberInput, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { api, goalTitle, type Goal } from "../../api";
import { goalSource } from "../trackers/refs";
import { InlineEdit, InlineSwitch } from "./InlineEdit";
import { LabelField } from "./LabelField";
import { RepoPick } from "./RepoPick";
import { RepoMarker } from "../repos/RepoMarker";
import { PARALLEL_HELP, parallelLabel } from "./parallel";

// Each field an operator set on a goal, edited where the goal page shows it. A save sends only that field.
export interface FieldProps {
  // picked: an operator picked the issue by ref, rather than the goal's label sweeping it in.
  goal: Goal & { issues?: { ref: string; picked?: boolean }[] };
  // A met or canceled goal shows its fields read-only until it is reopened.
  readOnly: boolean;
  save: (fields: Partial<Goal>) => Promise<unknown>;
}

const sameText = (a: string, b: string) => a.trim() === b.trim();
// A number input's value: a number, or "" when it is empty.
type Amount = number | string;
const sameAmount = (a: Amount, b: Amount) => String(a) === String(b);

export function TitleField({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineEdit<string>
      label="title"
      heading
      readOnly={readOnly}
      value={goal.title ?? ""}
      display={goalTitle(goal)}
      same={sameText}
      check={(v) => (v.trim() ? null : "Give the goal a title.")}
      save={(v) => save({ title: v.trim() })}
      input={({ value, onChange, invalid }) => (
        <TextInput aria-label="Title" size="md" maxLength={80} value={value} error={invalid} onChange={(e) => onChange(e.currentTarget.value)} />
      )}
    />
  );
}

export function DescriptionField({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineEdit<string>
      label="description"
      multiline
      readOnly={readOnly}
      value={goal.statement}
      display={<Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{goal.statement}</Text>}
      same={sameText}
      check={(v) => (v.trim() ? null : "Describe the goal.")}
      save={(v) => save({ statement: v.trim() })}
      input={({ value, onChange, invalid }) => (
        <Textarea
          aria-label="Description"
          autosize
          minRows={3}
          maxLength={4000}
          value={value}
          error={invalid}
          onChange={(e) => onChange(e.currentTarget.value)}
        />
      )}
    />
  );
}

export function LabelEdit({ goal, readOnly, save }: FieldProps) {
  // The label may be cleared only when the goal holds issues picked for it, which it then takes alone: an
  // issue its label swept in does not count.
  const picked = (goal.issues ?? []).some((i) => i.picked);
  const shown = goal.label ? (
    <Badge variant="outline" tt="none" radius="sm">{goal.label}</Badge>
  ) : (
    <Text size="sm" c="dimmed">None: the goal runs only the issues picked for it</Text>
  );
  return (
    <InlineEdit<string>
      label="label"
      readOnly={readOnly}
      value={goal.label ?? ""}
      display={shown}
      same={sameText}
      check={(v) => (v.trim() || picked ? null : "The goal has no picked issues, so it needs a label.")}
      save={(v) => save({ label: v.trim() || null })}
      input={({ value, onChange, invalid }) => (
        <LabelField
          value={value}
          onChange={onChange}
          repos={goal.repos}
          source={goalSource(goal)}
          required={!picked}
          fieldProps={{
            label: undefined,
            description: undefined,
            "aria-label": "Label",
            error: invalid,
            placeholder: picked ? "None: the goal runs only the issues picked for it" : "meta-harness:bootstrap",
            comboboxProps: { withinPortal: false },
          }}
        />
      )}
    />
  );
}

// The registered repos as checkboxes, with any of the goal's the registry no longer lists.
function RepoChoice({ value, onChange }: { value: string[]; onChange: (repos: string[]) => void }) {
  const [registered, setRegistered] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.goalRepos().then(
      (r) => setRegistered(r.map((x) => x.name)),
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);
  return (
    <Stack gap={4}>
      <RepoPick offered={error ? [] : registered} value={value} onChange={onChange} />
      {error && <Text size="xs" c="red">The repos could not be loaded: {error}. Reload the page to try again.</Text>}
    </Stack>
  );
}

export function ReposEdit({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineEdit<string[]>
      label="repos"
      readOnly={readOnly}
      value={goal.repos}
      display={
        <Stack gap={2}>
          {goal.repos.map((r) => (
            <Group key={r} gap={6} wrap="wrap">
              <Text ff="monospace" size="sm">{r}</Text>
              <RepoMarker repo={r} />
            </Group>
          ))}
        </Stack>
      }
      same={(a, b) => [...a].sort().join("\n") === [...b].sort().join("\n")}
      check={(v) => (v.length ? null : "Pick at least one repo.")}
      save={(v) => save({ repos: v })}
      input={({ value, onChange }) => <RepoChoice value={value} onChange={onChange} />}
    />
  );
}

export function BudgetEdit({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineEdit<Amount>
      label="budget"
      readOnly={readOnly}
      value={goal.max_usd ? Number(goal.max_usd) : ""}
      display={<Text size="sm">{goal.max_usd ? `$${Number(goal.max_usd).toFixed(2)}` : "None"}</Text>}
      same={sameAmount}
      check={(v) => (v === "" || Number(v) > 0 ? null : "A budget is more than $0. Leave it empty for none.")}
      // Emptied: the goal has no budget.
      save={(v) => save({ max_usd: v === "" ? null : String(v) })}
      input={({ value, onChange, invalid }) => (
        <NumberInput
          aria-label="Budget"
          description="The most its runs may spend. Leave empty for no limit."
          placeholder="None"
          prefix="$"
          min={0}
          decimalScale={2}
          value={value}
          error={invalid}
          onChange={onChange}
        />
      )}
    />
  );
}

export function PriorityEdit({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineEdit<Amount>
      label="priority"
      readOnly={readOnly}
      value={goal.priority}
      display={
        <Text size="sm">
          {goal.priority} <Text span size="sm" c="dimmed">Higher runs first.</Text>
        </Text>
      }
      same={(a, b) => (Number(a) || 0) === (Number(b) || 0)}
      save={(v) => save({ priority: Number(v) || 0 })}
      input={({ value, onChange, invalid }) => (
        <NumberInput aria-label="Priority" description="Higher runs first." allowDecimal={false} value={value} error={invalid} onChange={onChange} />
      )}
    />
  );
}

export function ParallelEdit({ goal, readOnly, save, system }: FieldProps & { system: number | null }) {
  return (
    <InlineEdit<Amount>
      label="parallel tasks"
      readOnly={readOnly}
      value={goal.max_parallel ?? ""}
      display={<Text size="sm">{parallelLabel(goal.max_parallel, system)}</Text>}
      same={sameAmount}
      check={(v) => {
        if (v === "") return null;
        if (!Number.isInteger(Number(v)) || Number(v) < 1) return "Parallel tasks is a whole number, 1 or more.";
        if (system && Number(v) > system) return `No more than the system setting, ${system}.`;
        return null;
      }}
      // Emptied: back to the system setting.
      save={(v) => save({ max_parallel: v === "" ? null : Number(v) })}
      input={({ value, onChange, invalid }) => (
        <NumberInput
          aria-label="Parallel tasks"
          description={`${PARALLEL_HELP} It applies the next time the goal starts tasks.`}
          placeholder={system ? `System setting: ${system}` : "System setting"}
          min={1}
          max={system ?? undefined}
          allowDecimal={false}
          value={value}
          error={invalid}
          onChange={onChange}
        />
      )}
    />
  );
}

// How much is done for the goal's tasks without asking (autonomy.tsx): one choice of three, each with hover help.
export function AutonomyEdit({ goal, readOnly, save }: FieldProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = autonomyOf(goal);
  if (readOnly) return <Text size="sm">{AUTONOMY[now].label}: {AUTONOMY[now].says}</Text>;
  return (
    <Stack gap={4}>
      <AutonomyPicker
        size="xs"
        label="What it does without asking"
        disabled={busy}
        value={now}
        onChange={(value) => {
          setBusy(true);
          setError(null);
          void save({ autonomy: value })
            .catch((err: unknown) => setError(`It was not saved: ${err instanceof Error ? err.message : String(err)}. Pick it again.`))
            .finally(() => setBusy(false));
        }}
      />
      {error && <Text size="xs" c="red">{error}</Text>}
    </Stack>
  );
}

// Who the goal's issues are assigned to: its tracker setting `assignee`, picked on the New goal form from the
// tracker's people and fixed then. The person is named as the tracker lists them, and one the tracker no longer
// lists is marked, since its issues then stay unassigned.
export function AssigneeField({ goal }: { goal: FieldProps["goal"] }) {
  const scheme = goal.tracker ?? "github";
  const named = (goal.tracker_settings?.assignee ?? "").trim();
  const project = (goal.tracker_settings?.project ?? "").trim();
  const repos = goal.repos.join(",");
  const [people, setPeople] = useState<{ value: string; label: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    setPeople(null);
    setError(null);
    if (!named) return;
    api.trackerChoices(scheme, "assignee", { ...(project ? { project } : {}), ...(repos ? { repos } : {}) }).then(
      (found) => !stale && setPeople(found),
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [scheme, named, project, repos]);
  if (!named) {
    return <Text size="sm">None: issues go to the goal's owner if the tracker knows their email.</Text>;
  }
  const found = people?.find((p) => p.value === named);
  return (
    <Stack gap={0} data-testid="goal-assignee">
      <Text size="sm">{found ? found.label : named}</Text>
      {error && <Text size="xs" c="orange">The tracker's people could not be read, so this is the saved id: {error}</Text>}
      {people !== null && !found && (
        <Text size="xs" c="orange">
          {named} is not one of the people the tracker can assign now, so the goal's issues stay unassigned. Add them to the tracker, or start a new goal that names another assignee.
        </Text>
      )}
      <Text size="sm" c="dimmed">Set at creation; it can't change.</Text>
    </Stack>
  );
}

// The issue a Jira goal's plan's issues are created under: its tracker setting `parent`, picked on the New goal
// form from the project's open epics and fixed then. It is named as the tracker lists it. One the tracker no
// longer lists is marked, since Jira may refuse it: each issue is then created without it, and the goal says so.
export function ParentField({ goal }: { goal: FieldProps["goal"] }) {
  const scheme = goal.tracker ?? "github";
  const named = (goal.tracker_settings?.parent ?? "").trim();
  const project = (goal.tracker_settings?.project ?? "").trim();
  const [epics, setEpics] = useState<{ value: string; label: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    setEpics(null);
    setError(null);
    if (!named || !project) return;
    api.trackerChoices(scheme, "parent", { project }).then(
      (found) => !stale && setEpics(found),
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [scheme, named, project]);
  if (!named) {
    return <Text size="sm">None: the issues its plan creates have no parent.</Text>;
  }
  const found = epics?.find((e) => e.value === named);
  return (
    <Stack gap={0} data-testid="goal-parent">
      <Text size="sm">{found ? found.label : named.replace(/^jira:/, "")}</Text>
      {error && <Text size="xs" c="orange">The project's epics could not be read, so this is the saved parent: {error}</Text>}
      {epics !== null && !found && (
        <Text size="xs" c="orange">
          {named} is not one of the project's open epics now, so Jira may refuse it. Each issue it refuses is created without a parent, and the goal says which. Reopen the epic, or set each issue's parent in Jira.
        </Text>
      )}
      <Text size="sm" c="dimmed">The issues its plan creates go under it. Set at creation; it can't change.</Text>
    </Stack>
  );
}

export function AutoMergeEdit({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineSwitch
      label="Auto-merge"
      checked={goal.auto_merge}
      onText="On"
      offText="Off: pull requests wait for you"
      readOnly={readOnly}
      save={(on) => save({ auto_merge: on })}
    />
  );
}
