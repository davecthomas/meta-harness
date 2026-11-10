import { useState } from "react";
import { Button, Group, Modal, NumberInput, Stack, Switch, TextInput, Textarea } from "@mantine/core";
import { IconDeviceFloppy } from "@tabler/icons-react";
import { api, type Goal } from "../../api";
import { useUnsavedChanges } from "../../components/UnsavedChanges";
import { ErrorAlert } from "../../components/ui";

// What an operator changes on a goal after creating it: its title, description, budget, priority and auto-merge.
// Saving sends only what changed, so each change is one goal_changed record.
export function GoalEditor(props: { goal: Goal; onClose: () => void; onSaved: () => void }) {
  const g = props.goal;
  const start = {
    title: g.title ?? "",
    statement: g.statement,
    max_usd: g.max_usd ? Number(g.max_usd) : ("" as number | ""),
    priority: g.priority,
    auto_merge: g.auto_merge,
  };
  const [form, setForm] = useState(start);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function changes(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    if (form.title.trim() && form.title.trim() !== (g.title ?? "")) out.title = form.title.trim();
    if (form.statement.trim() !== g.statement) out.statement = form.statement.trim();
    if (form.max_usd !== start.max_usd && form.max_usd !== "") out.max_usd = String(form.max_usd);
    if (form.priority !== g.priority) out.priority = form.priority;
    if (form.auto_merge !== g.auto_merge) out.auto_merge = form.auto_merge;
    return out;
  }

  async function save(): Promise<boolean> {
    const changed = changes();
    if (!Object.keys(changed).length) return true;
    if (!form.statement.trim()) {
      setError("A goal needs a description.");
      return false;
    }
    setSaving(true);
    try {
      await api.patchGoal(g.id, changed);
      unsaved.release();
      props.onSaved();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  const unsaved = useUnsavedChanges({
    dirty: Object.keys(changes()).length > 0,
    describe: `the goal "${(form.title || g.statement).slice(0, 60)}"`,
    save,
    discard: () => setForm(start),
  });

  return (
    <Modal opened onClose={() => unsaved.confirm(props.onClose)} title="Edit goal" size="lg">
      {unsaved.element}
      <Stack gap="md">
        <ErrorAlert error={error} />
        <TextInput label="Title" maxLength={80} value={form.title} onChange={(e) => setForm({ ...form, title: e.currentTarget.value })} />
        <Textarea
          label="Description"
          description="What the goal is. Tasks it already made keep the issues they came from."
          autosize
          minRows={4}
          value={form.statement}
          onChange={(e) => setForm({ ...form, statement: e.currentTarget.value })}
        />
        <Group grow align="flex-start">
          <NumberInput
            label="Budget"
            description="No more of its tasks start once its runs have spent this."
            prefix="$"
            min={0}
            decimalScale={2}
            value={form.max_usd}
            onChange={(v) => setForm({ ...form, max_usd: v === "" ? "" : Number(v) })}
          />
          <NumberInput label="Priority" description="Higher runs first." value={form.priority} onChange={(v) => setForm({ ...form, priority: Number(v) || 0 })} />
        </Group>
        <Switch label="Merge pull requests without me" checked={form.auto_merge} onChange={(e) => setForm({ ...form, auto_merge: e.currentTarget.checked })} />
        <Group justify="flex-end">
          <Button variant="default" onClick={() => unsaved.confirm(props.onClose)}>Cancel</Button>
          <Button
            leftSection={<IconDeviceFloppy size={16} />}
            loading={saving}
            onClick={async () => {
              if (await save()) props.onClose();
            }}
          >
            Save goal
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
