import { useEffect, useState } from "react";
import { Badge, Checkbox, NumberInput, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { api, goalTitle, type Goal } from "../../api";
import { InlineEdit, InlineSwitch } from "./InlineEdit";
import { LabelField } from "./LabelField";
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
    <Text size="sm" c="dimmed">None: the goal takes the issues picked for it</Text>
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
          required={!picked}
          fieldProps={{
            label: undefined,
            description: undefined,
            "aria-label": "Label",
            error: invalid,
            placeholder: picked ? "None: the goal takes the issues picked for it" : "meta-harness:bootstrap",
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
    api.repos().then(
      (r) => setRegistered(r.map((x) => x.name)),
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);
  const names = [...new Set([...(registered ?? []), ...value])];
  return (
    <Stack gap={4}>
      <Checkbox.Group aria-label="Repos" value={value} onChange={onChange}>
        <Stack gap="xs">
          {names.map((name) => (
            <Checkbox key={name} value={name} label={name} styles={{ label: { fontFamily: "var(--mantine-font-family-monospace)" } }} />
          ))}
        </Stack>
      </Checkbox.Group>
      {error && <Text size="xs" c="red">The registered repos could not be loaded: {error}</Text>}
    </Stack>
  );
}

export function ReposEdit({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineEdit<string[]>
      label="repos"
      readOnly={readOnly}
      value={goal.repos}
      display={<Stack gap={2}>{goal.repos.map((r) => <Text ff="monospace" size="sm" key={r}>{r}</Text>)}</Stack>}
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
      display={<Text size="sm">{goal.max_usd ? `$${goal.max_usd}` : "None"}</Text>}
      same={sameAmount}
      check={(v) => (v === "" || Number(v) > 0 ? null : "A budget is more than $0. Leave it empty for none.")}
      // Emptied: the goal has no budget.
      save={(v) => save({ max_usd: v === "" ? null : String(v) })}
      input={({ value, onChange, invalid }) => (
        <NumberInput
          aria-label="Budget"
          description="What its runs may spend; the fields below say what happens when they have. Leave it empty for none."
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
          {goal.priority} <Text span size="xs" c="dimmed">Higher runs first.</Text>
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
          description={`${PARALLEL_HELP} It applies at the next admission pass.`}
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
