import { useEffect, useMemo, useState } from "react";
import { Anchor, Badge, Button, Checkbox, Group, Modal, Select, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { LabelSuggest, useLabelSuggestions } from "./LabelField";
import { IconPlus } from "@tabler/icons-react";
import { api, issueUrl, type LabelIssue } from "../../api";
import { ErrorAlert } from "../../components/ui";

type Row = LabelIssue & { repo: string };
const PAGE = 50;

// Open issues across the configured repos, to add to a goal: pick a repo (or all), filter by label and
// text, and check rows one at a time or a page at a time. An issue a goal already holds can't be picked.
export function IssuesPicker(props: {
  opened: boolean;
  onClose: () => void;
  // The repos to browse: the goal's, or every registered repo when empty.
  repos: string[];
  // Refs already chosen, shown checked.
  chosen?: string[];
  // The goal the picker adds to, so its own issues read as held by it.
  goalId?: string;
  // Refs the goal's label already takes: shown checked and locked, since unchecking could not leave them out.
  viaLabel?: string[];
  action: string;
  onPick: (refs: string[]) => Promise<void> | void;
}) {
  const [available, setAvailable] = useState<string[]>([]);
  const [repo, setRepo] = useState<string>("all");
  const [label, setLabel] = useState("");
  const [text, setText] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<Set<string>>(new Set(props.chosen ?? []));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!props.opened) return;
    setPicked(new Set(props.chosen ?? []));
    if (props.repos.length) setAvailable(props.repos);
    else api.repos().then((r) => setAvailable(r.map((x) => x.name)), (err) => setError(String(err.message ?? err)));
  }, [props.opened, props.repos, props.chosen]);

  // Read every chosen repo's matching issues, newest first; the table pages through them.
  useEffect(() => {
    if (!props.opened || !available.length) return;
    let stale = false;
    const repos = repo === "all" ? available : [repo];
    setError(null);
    Promise.all(
      repos.map(async (name) => {
        const found = await api.repoIssues(name, { label: label.trim() || undefined, q: text.trim() || undefined, limit: 500 });
        return found.issues.map((issue) => ({ ...issue, repo: name }));
      }),
    ).then(
      (lists) => {
        if (stale) return;
        const all = lists.flat().sort((a, b) => b.updated_at.localeCompare(a.updated_at));
        setRows(all);
        setTotal(all.length);
        setPage(0);
      },
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [props.opened, available, repo, label, text]);

  const shown = useMemo(() => rows.slice(page * PAGE, page * PAGE + PAGE), [rows, page]);
  const labeled = useMemo(() => new Set(props.viaLabel ?? []), [props.viaLabel]);
  const free = (row: Row) => !row.held_by || row.held_by === props.goalId;
  const pickable = shown.filter((row) => free(row) && !labeled.has(row.ref));
  // The picker's Label filter suggests the labels of the repo it shows, as the goal's Label field does,
  // read once when the picker first opens. If they can't be read, the filter is plain text.
  const filterRepos = useMemo(() => (repo === "all" ? available : [repo]), [repo, available]);
  const suggested = useLabelSuggestions(filterRepos, props.opened);
  const allOnPage = pickable.length > 0 && pickable.every((r) => picked.has(r.ref));

  function toggle(ref: string, on: boolean) {
    setPicked((now) => {
      const next = new Set(now);
      if (on) next.add(ref);
      else next.delete(ref);
      return next;
    });
  }

  async function pick() {
    setBusy(true);
    try {
      await props.onPick([...picked]);
      props.onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened={props.opened} onClose={props.onClose} title="Pick issues" size="xl">
      <Stack gap="sm">
        <ErrorAlert error={error} />
        <Group grow align="flex-end">
          <Select label="Repo" value={repo} onChange={(v) => setRepo(v ?? "all")} data={[{ value: "all", label: "Every repo" }, ...available.map((r) => ({ value: r, label: r }))]} />
          <LabelSuggest label="Label" placeholder="Any label" value={label} onChange={setLabel} suggested={suggested} />
          <TextInput label="Search" placeholder="Text in the title" value={text} onChange={(e) => setText(e.currentTarget.value)} />
        </Group>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={36}>
                <Checkbox
                  aria-label="Select every issue on this page"
                  checked={allOnPage}
                  onChange={(e) => pickable.forEach((r) => toggle(r.ref, e.currentTarget.checked))}
                />
              </Table.Th>
              <Table.Th>Issue</Table.Th>
              <Table.Th>Labels</Table.Th>
              <Table.Th>Held by</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((row) => (
              <Table.Tr key={row.ref} opacity={free(row) ? 1 : 0.55}>
                <Table.Td>
                  {labeled.has(row.ref) && free(row) ? (
                    <Tooltip label="The goal's label takes this issue. To leave it out, clear the label and pick issues by hand." multiline w={280}>
                      <Checkbox aria-label={`${row.ref} is in the goal by its label`} checked disabled readOnly />
                    </Tooltip>
                  ) : (
                    <Checkbox aria-label={`Pick ${row.ref}`} disabled={!free(row)} checked={picked.has(row.ref)} onChange={(e) => toggle(row.ref, e.currentTarget.checked)} />
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <Text size="sm">{row.title}</Text>
                    {labeled.has(row.ref) && free(row) && <Badge size="xs" color="gray" variant="light" tt="none">via label</Badge>}
                  </Group>
                  <Anchor href={issueUrl(row.ref) ?? undefined} target="_blank" size="xs" ff="monospace">{row.ref.replace(/^github:/, "")}</Anchor>
                </Table.Td>
                <Table.Td>
                  <Group gap={4}>{row.labels.map((l) => <Badge key={l} size="xs" variant="outline" tt="none">{l}</Badge>)}</Group>
                </Table.Td>
                <Table.Td><Text size="xs" ff="monospace" c="dimmed">{row.held_by && row.held_by !== props.goalId ? row.held_by : ""}</Text></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {total === 0 && <Text size="sm" c="dimmed">No open issue matches.</Text>}
        <Group justify="space-between">
          <Group gap="xs">
            <Button size="xs" variant="default" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <Text size="xs" c="dimmed">{total ? `${page * PAGE + 1}–${Math.min(total, (page + 1) * PAGE)} of ${total}` : ""}</Text>
            <Button size="xs" variant="default" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</Button>
          </Group>
          <Group gap="xs">
            <Text size="sm" c="dimmed">{picked.size} selected</Text>
            <Button leftSection={<IconPlus size={16} />} disabled={!picked.size} loading={busy} onClick={pick}>{props.action}</Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}
