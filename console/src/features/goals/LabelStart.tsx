import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
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
import { api, type GoalDraft, type LabelCount, type LabelIssue } from "../../api";

type LabeledIssue = LabelIssue & { repo: string };

// An optional helper for the New goal form: pick a label, see its open issues, and have a model draft the
// goal's title and description from them. Picking a label sets the form's Label field (onPick), which
// selects the label's issues there; the draft reads the issues the form has selected. Nothing is saved
// here, and nothing here chooses issues: the form's Label field and Pick issues do that.
export function LabelStart({
  label,
  repos,
  issues,
  selected,
  onPick,
  onDraft,
}: {
  // The form's label and repos, and the open issues that label has in those repos (null while read).
  label: string;
  repos: string[];
  issues: LabeledIssue[] | null;
  // The refs the form has selected: its label's free issues and those picked by hand.
  selected: string[];
  onPick: (row: LabelCount) => void;
  onDraft: (draft: GoalDraft) => void;
}) {
  const [labels, setLabels] = useState<LabelCount[] | null>(null);
  const [allRepos, setAllRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // Closed until asked for: the form below is the goal, and this only helps fill it in.
  const [opened, setOpened] = useState(false);
  const [draft, setDraft] = useState<GoalDraft | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.labels().then(
      (r) => {
        setLabels(r.labels);
        setAllRepos(r.repos);
      },
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);

  const shown = useMemo(
    () =>
      (labels ?? []).filter(
        (l) => (!repo || l.repos[repo]) && l.label.toLowerCase().includes(search.trim().toLowerCase()),
      ),
    [labels, repo, search],
  );

  // The picked label is the form's: picking one here sets it there, and typing one there picks it here.
  const picked = useMemo(() => (labels ?? []).find((l) => l.label === label.trim()) ?? null, [labels, label]);
  const free = (issues ?? []).filter((i) => !i.held_by);
  // The label's repos the goal does not take it from, so the operator can tick them below.
  const outside = picked ? Object.keys(picked.repos).filter((r) => !repos.includes(r)) : [];

  function pick(row: LabelCount) {
    setDraft(null);
    setError(null);
    onPick(row);
  }

  async function makeDraft() {
    if (!picked) return;
    setDrafting(true);
    setError(null);
    try {
      const made = await api.draftGoal({ label: picked.label, repos, refs: selected });
      setDraft(made);
      onDraft(made);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDrafting(false);
    }
  }

  return (
    <Paper maw={720} mb="lg">
      <Stack gap="md">
        <UnstyledButton onClick={() => setOpened((now) => !now)} aria-expanded={opened} aria-label="Draft from a label (optional)">
          <Group gap="xs" wrap="nowrap">
            {opened ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
            <div>
              <Title order={4}>Draft from a label (optional)</Title>
              <Text size="sm" c="dimmed">
                Pick a label, and a model drafts the title and description from its open issues. It fills in the
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
                data={allRepos}
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
                  onClick={() => pick(row)}
                  p="xs"
                  style={{
                    borderRadius: "var(--mantine-radius-sm)",
                    background: picked?.label === row.label ? "var(--mantine-color-default-hover)" : undefined,
                  }}
                  aria-label={`Label ${row.label}`}
                  aria-pressed={picked?.label === row.label}
                >
                  <Group justify="space-between" wrap="nowrap">
                    <Group gap="xs" wrap="nowrap">
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
        {picked && issues === null && <Loader size="sm" />}
        {picked && issues && (
          <Stack gap="sm">
            <Text size="sm" fw={600}>
              {picked.label}: {free.length} open {free.length === 1 ? "issue" : "issues"} for the goal and the draft
              {issues.length > free.length ? `, and ${issues.length - free.length} another goal holds` : ""}
            </Text>
            {outside.length > 0 && (
              <Text size="xs" c="dimmed">
                Not among the goal's repos: {outside.join(", ")}. Tick them under Repos to take their issues too.
              </Text>
            )}
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
                disabled={selected.length === 0}
              >
                Draft the goal
              </Button>
            </Group>
          </Stack>
        )}
        {draft && (
          <Alert color="indigo" title={draft.title}>
            <Text size="sm">{draft.theme}</Text>
            <Text size="xs" c="dimmed" mt={4}>
              Drafted by {draft.model} for ${draft.usd_cost}. The issues read as {draft.modality}. The form below
              has the title, description, label and repos: edit them, then create the goal. The goal takes every
              open issue with that label, now and later; Pick issues adds others.
            </Text>
          </Alert>
        )}
        </Stack>
        )}
      </Stack>
    </Paper>
  );
}
