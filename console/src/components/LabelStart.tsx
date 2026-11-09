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
import { IconSearch, IconSparkles } from "@tabler/icons-react";
import { api, type GoalDraft, type LabelCount, type LabelIssue } from "../api";

// Start a goal from a label: the labels on the registered repos' open issues, the chosen label's issues,
// and a goal a model drafts from the issues kept. onDraft fills the New goal form; nothing is saved here.
export function LabelStart({ onDraft }: { onDraft: (draft: GoalDraft) => void }) {
  const [labels, setLabels] = useState<LabelCount[] | null>(null);
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<LabelCount | null>(null);
  const [issues, setIssues] = useState<(LabelIssue & { repo: string })[] | null>(null);
  const [kept, setKept] = useState<string[]>([]);
  const [draft, setDraft] = useState<GoalDraft | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.labels().then(
      (r) => {
        setLabels(r.labels);
        setRepos(r.repos);
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

  async function pick(row: LabelCount) {
    setPicked(row);
    setIssues(null);
    setDraft(null);
    setError(null);
    try {
      const found = await Promise.all(
        Object.keys(row.repos).map(async (name) =>
          (await api.labelIssues(name, row.label)).map((issue) => ({ ...issue, repo: name })),
        ),
      );
      const all = found.flat();
      setIssues(all);
      setKept(all.filter((i) => !i.held_by).map((i) => i.ref));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function makeDraft() {
    if (!picked) return;
    setDrafting(true);
    setError(null);
    try {
      const made = await api.draftGoal({ label: picked.label, repos: Object.keys(picked.repos), refs: kept });
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
        <div>
          <Title order={4}>Start from a label</Title>
          <Text size="sm" c="dimmed">
            The labels on your repos' open issues. Pick one to see its issues and draft a goal from them.
          </Text>
        </div>
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
                    background: picked?.label === row.label ? "var(--mantine-color-default-hover)" : undefined,
                  }}
                  aria-label={`Label ${row.label}`}
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
        {picked && issues === null && !error && <Loader size="sm" />}
        {picked && issues && (
          <Stack gap="sm">
            <Text size="sm" fw={600}>
              {picked.label}: {issues.length} open issues, {kept.length} kept for the draft
            </Text>
            <Table>
              <Table.Tbody>
                {issues.map((issue) => (
                  <Table.Tr key={issue.ref}>
                    <Table.Td w={36}>
                      <Checkbox
                        aria-label={`Keep ${issue.ref}`}
                        checked={kept.includes(issue.ref)}
                        disabled={!!issue.held_by}
                        onChange={(e) =>
                          setKept((now) =>
                            e.currentTarget.checked ? [...now, issue.ref] : now.filter((r) => r !== issue.ref),
                          )
                        }
                      />
                    </Table.Td>
                    <Table.Td>
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
                disabled={kept.length === 0}
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
              Drafted by {draft.model} for ${draft.usd_cost}. The issues read as {draft.modality}
              {draft.modality === "feature" ? "; feature goals aren't built yet, so this one runs as fix" : ""}. The
              form below has the title, description, label and repos: edit them, then create the goal.
            </Text>
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}
