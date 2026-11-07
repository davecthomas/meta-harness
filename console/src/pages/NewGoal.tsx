import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Checkbox, Group, NumberInput, Paper, Stack, Switch, Textarea, TextInput } from "@mantine/core";
import { api } from "../api";
import { ErrorAlert, PageHeader } from "../components/ui";

export function NewGoal() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<string[]>([]);
  const [statement, setStatement] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [label, setLabel] = useState("");
  const [budget, setBudget] = useState<string | number>("");
  const [priority, setPriority] = useState<string | number>(0);
  const [autoMerge, setAutoMerge] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.repos().then((r) => setRepos(r.map((x) => x.name)), (err) => setError(String(err.message ?? err)));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!chosen.length) {
      setError("Pick at least one repo.");
      return;
    }
    setSaving(true);
    try {
      const goal = await api.createGoal({
        statement,
        repos: chosen,
        label,
        max_usd: budget === "" ? null : String(budget),
        priority: Number(priority) || 0,
        auto_merge: autoMerge,
      });
      navigate(`/goals/${goal.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader title="New goal" description="A goal takes the open issues with its label, in its repos, and runs each one to a pull request." />
      <ErrorAlert error={error} />
      <Paper maw={720}>
        <form onSubmit={submit}>
          <Stack gap="lg">
            <Textarea
              label="Statement"
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
            <TextInput
              label="Label"
              description="An open issue with this label, in any of the goal's repos, becomes one of its tasks."
              required
              placeholder="meta-harness:bootstrap"
              value={label}
              onChange={(e) => setLabel(e.currentTarget.value)}
            />
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
              <Button type="submit" loading={saving}>Create goal</Button>
            </Group>
          </Stack>
        </form>
      </Paper>
    </>
  );
}
