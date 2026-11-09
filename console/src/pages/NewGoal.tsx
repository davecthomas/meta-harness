import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Checkbox, Group, NumberInput, Paper, Stack, Switch, TextInput, Textarea } from "@mantine/core";
import { IconDeviceFloppy, IconPlayerPlay } from "@tabler/icons-react";
import { api, type GoalDraft } from "../api";
import { LabelField } from "../components/LabelField";
import { LabelStart } from "../components/LabelStart";
import { ErrorAlert, PageHeader } from "../components/ui";

export function NewGoal() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [statement, setStatement] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [label, setLabel] = useState("");
  const [budget, setBudget] = useState<string | number>("");
  const [priority, setPriority] = useState<string | number>(0);
  const [autoMerge, setAutoMerge] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which button submitted the form: Save (false) or Save and start (true). Null while not saving.
  const [saving, setSaving] = useState<boolean | null>(null);
  const start = useRef(true);

  useEffect(() => {
    api.repos().then((r) => setRepos(r.map((x) => x.name)), (err) => setError(String(err.message ?? err)));
  }, []);

  function fromDraft(draft: GoalDraft) {
    setTitle(draft.title);
    setStatement(draft.statement);
    setLabel(draft.label);
    setChosen(draft.repos);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!chosen.length) {
      setError("Pick at least one repo.");
      return;
    }
    setSaving(start.current);
    try {
      const goal = await api.createGoal({
        // Left empty, the planner's model names the goal from its description.
        ...(title.trim() ? { title: title.trim() } : {}),
        statement,
        repos: chosen,
        label,
        max_usd: budget === "" ? null : String(budget),
        priority: Number(priority) || 0,
        auto_merge: autoMerge,
        start: start.current,
      });
      navigate(`/goals/${goal.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(null);
    }
  }

  return (
    <>
      <PageHeader title="New goal" description="A goal takes the open issues with its label, in its repos, and runs each one to a pull request." />
      <ErrorAlert error={error} />
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
            <LabelField value={label} onChange={setLabel} repos={chosen} />
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
                Save and start
              </Button>
            </Group>
          </Stack>
        </form>
      </Paper>
    </>
  );
}
