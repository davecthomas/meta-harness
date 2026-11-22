import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconSearch, IconSparkles } from "@tabler/icons-react";
import { api, type IssueSource, type GoalDraft, type LabelCount, type LabelIssue } from "../../api";

// An optional helper for the New goal form: pick one label or several, see their open issues, and have a model
// draft the goal's title and description from them. onDraft fills the form below; nothing is saved here, and nothing
// here chooses the goal's issues. The form's Label field and Pick issues do that.
// source: which tracker's labels and issues, and what it asks of a goal; the default tracker's when left out.
export function LabelStart({ onDraft, source }: { onDraft: (draft: GoalDraft) => void; source?: IssueSource }) {
  const [labels, setLabels] = useState<LabelCount[] | null>(null);
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // The labels to draft from: one, or several whose issues are read together.
  const [picked, setPicked] = useState<LabelCount[]>([]);
  const [issues, setIssues] = useState<(LabelIssue & { repo: string })[] | null>(null);
  // Closed until asked for: the form below is the goal, and this only helps fill it in.
  const [opened, setOpened] = useState(false);
  const [draft, setDraft] = useState<GoalDraft | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // What was shown, and any error, belonged to the source before. An answer for that one is dropped.
    let stale = false;
    setPicked([]);
    setIssues(null);
    setLabels(null);
    setError(null);
    api.labels(undefined, source).then(
      (r) => {
        if (stale) return;
        setLabels(r.labels);
        setRepos(r.repos);
      },
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(source ?? null)]);

  const shown = useMemo(
    () =>
      (labels ?? []).filter(
        (l) => (!repo || l.repos[repo]) && l.label.toLowerCase().includes(search.trim().toLowerCase()),
      ),
    [labels, repo, search],
  );

  // Adds a label to the ones drafted from, or takes it out, and reads the open issues of them all: each
  // issue once, however many of the labels it has.
  async function pick(row: LabelCount) {
    const next = picked.some((p) => p.label === row.label) ? picked.filter((p) => p.label !== row.label) : [...picked, row];
    setPicked(next);
    setIssues(null);
    setDraft(null);
    setError(null);
    if (!next.length) return;
    try {
      const found = await Promise.all(
        next.flatMap((label) =>
          Object.keys(label.repos).map(async (name) => (await api.labelIssues(name, label.label, source)).map((issue) => ({ ...issue, repo: name }))),
        ),
      );
      const once = new Map(found.flat().map((issue) => [issue.ref, issue]));
      setIssues([...once.values()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function makeDraft() {
    if (!picked.length) return;
    setDrafting(true);
    setError(null);
    try {
      const made = await api.draftGoal({ labels: picked.map((p) => p.label), repos: [...new Set(picked.flatMap((p) => Object.keys(p.repos)))], refs: free.map((i) => i.ref), tracker: source?.tracker, tracker_settings: source?.settings });
      setDraft(made);
      onDraft(made);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDrafting(false);
    }
  }

  // The issues the draft reads: the label's open issues that no other goal holds.
  const free = (issues ?? []).filter((i) => !i.held_by);

  return (
    <Paper maw={720} mb="lg">
      <Stack gap="md">
        <UnstyledButton onClick={() => setOpened((now) => !now)} aria-expanded={opened} aria-label="Draft a goal from issue labels">
          <Group gap="xs" wrap="nowrap">
            {opened ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
            <div>
              <Title order={4}>Draft a goal from issue labels</Title>
              <Text size="sm" c="dimmed">
                Pick one label or several, and a model drafts the title and description from its open issues. It fills in the
                form below and saves nothing.
              </Text>
            </div>
          </Group>
        </UnstyledButton>
        {opened && (
        <Stack gap="md">
        {error && <Alert color="red">{error}</Alert>}
        {labels === null && !error ? (
          <Group gap="xs">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">Reading your repos' open issues…</Text>
          </Group>
        ) : (
          <>
            <Group grow>
              <TextInput
                placeholder="Find a label"
                leftSection={<IconSearch size={16} />}
                value={search}
                onChange={(e) => setSearch(e.currentTarget.value)}
                aria-label="Find a label"
              />
              <Select
                placeholder="Every repo"
                data={repos}
                value={repo}
                onChange={setRepo}
                clearable
                aria-label="Repo"
              />
            </Group>
            <Stack gap={4} mah={260} style={{ overflowY: "auto" }}>
              {shown.length === 0 && <Text size="sm" c="dimmed">No label matches.</Text>}
              {shown.map((row) => (
                <UnstyledButton
                  key={row.label}
                  onClick={() => void pick(row)}
                  p="xs"
                  style={{
                    borderRadius: "var(--mantine-radius-sm)",
                    background: picked.some((p) => p.label === row.label) ? "var(--mantine-color-default-hover)" : undefined,
                  }}
                  aria-label={`Label ${row.label}`}
                  aria-pressed={picked.some((p) => p.label === row.label)}
                >
                  <Group justify="space-between" wrap="nowrap">
                    <Group gap="xs" wrap="nowrap">
                      <Checkbox size="xs" readOnly tabIndex={-1} checked={picked.some((p) => p.label === row.label)} aria-hidden />
                      <Badge variant="light">{row.label}</Badge>
                      {Object.entries(row.repos).map(([name, count]) => (
                        <Text key={name} size="xs" c="dimmed" ff="monospace">
                          {name} {count}
                        </Text>
                      ))}
                    </Group>
                    <Text size="sm" fw={600}>{row.total}</Text>
                  </Group>
                </UnstyledButton>
              ))}
            </Stack>
          </>
        )}
        {picked.length > 0 && issues === null && !error && <Loader size="sm" />}
        {picked.length > 0 && issues && (
          <Stack gap="sm">
            <Text size="sm" fw={600}>
              {picked.map((p) => p.label).join(", ")}: {free.length} open {free.length === 1 ? "issue" : "issues"} for the draft
              {issues.length > free.length ? `, and ${issues.length - free.length} another goal holds` : ""}
            </Text>
            <Table>
              <Table.Tbody>
                {issues.map((issue) => (
                  <Table.Tr key={issue.ref}>
                    <Table.Td opacity={issue.held_by ? 0.55 : 1}>
                      <Text size="sm">{issue.title}</Text>
                      <Text size="xs" c="dimmed" ff="monospace">
                        {issue.repo}#{issue.number}
                        {issue.held_by ? ` · held by goal ${issue.held_by}` : ""}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            <Group justify="flex-end">
              <Button
                leftSection={<IconSparkles size={16} />}
                onClick={() => void makeDraft()}
                loading={drafting}
                disabled={free.length === 0}
              >
                Draft the goal
              </Button>
            </Group>
          </Stack>
        )}
        {draft && (
          <Alert color="indigo" title={draft.title}>
            <Text size="sm">{draft.theme}</Text>
            <Text size="sm" c="dimmed" mt={4}>
              Drafted by {draft.model} for ${draft.usd_cost}. The issues read as {draft.modality}.{" "}
              {draft.label
                ? "The form below has the title, description, label and repos: edit them, then create the goal. The goal takes every open issue with that label, now and later; Pick issues adds others."
                : `The form below has the title, description and repos, and the ${draft.refs.length} issues found here, which the goal takes as picked issues. A goal takes one label, so with several labels it holds only these issues and none labeled later. Edit them, then create the goal.`}
            </Text>
          </Alert>
        )}
        </Stack>
        )}
      </Stack>
    </Paper>
  );
}
