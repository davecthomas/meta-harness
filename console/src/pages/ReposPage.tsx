import { useCallback, useEffect, useState } from "react";
import { Alert, Anchor, Badge, Button, Code, Group, List, Modal, Select, Stack, Table, Text, Textarea } from "@mantine/core";
import { IconEdit, IconPlus, IconTrash } from "@tabler/icons-react";
import { api, ApiError, type ConfigVersion } from "../api";
import { useUnsavedChanges } from "../components/UnsavedChanges";
import { DataTable, ErrorAlert, PageHeader } from "../components/ui";

type Repo = { tracker?: string; default_checks?: string[] };

function lines(text: string): string[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

// One repo's tracker and default checks, to add or to change. Leaving it with changes asks first.
function RepoForm(props: {
  title: string;
  names: string[] | null;
  repo?: { name: string; spec: Repo };
  onClose: () => void;
  onSave: (name: string, spec: Repo) => Promise<boolean>;
}) {
  const [name, setName] = useState<string | null>(props.repo?.name ?? null);
  const [checks, setChecks] = useState((props.repo?.spec.default_checks ?? []).join("\n"));
  const original = (props.repo?.spec.default_checks ?? []).join("\n");
  const save = () => (name ? props.onSave(name, { ...(props.repo?.spec ?? {}), tracker: "github", default_checks: lines(checks) }) : Promise.resolve(false));
  const unsaved = useUnsavedChanges({
    dirty: checks !== original || (!props.repo && name !== null),
    describe: name ? `the repository ${name}` : "the new repository",
    save,
    discard: () => undefined,
  });
  return (
    <Modal opened onClose={() => unsaved.confirm(props.onClose)} title={props.title} size="lg">
      {unsaved.element}
      <Stack gap="md">
        {props.names ? (
          <Select
            label="Repository"
            description="A repo the deployment's GitHub App is installed on and the registry does not list yet."
            searchable
            data={props.names}
            value={name}
            onChange={setName}
            nothingFoundMessage="No repo left: install the GitHub App on more"
          />
        ) : (
          <Text ff="monospace">{name}</Text>
        )}
        <Text size="sm">Tracker: GitHub</Text>
        <Textarea
          label="Default acceptance checks"
          description="One command per line. A task runs them when its issue names none."
          autosize
          minRows={3}
          value={checks}
          onChange={(e) => setChecks(e.currentTarget.value)}
          styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={() => unsaved.confirm(props.onClose)}>Cancel</Button>
          <Button
            disabled={!name}
            onClick={async () => {
              if (await save()) {
                unsaved.release();
                props.onClose();
              }
            }}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// The repo registry: the repos goals may change. Each change saves a new version of the `repos` kind.
export function ReposPage() {
  const [current, setCurrent] = useState<ConfigVersion | null>(null);
  const [available, setAvailable] = useState<{ repos: { name: string; private: boolean }[]; install_urls: string[] } | null>(null);
  const [form, setForm] = useState<{ mode: "add" } | { mode: "edit"; name: string } | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCurrent(await api.config("repos"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
    api.reposAvailable().then(setAvailable, (err) => setError(`The GitHub App's repos could not be listed: ${err instanceof Error ? err.message : String(err)}`));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveRegistry(repos: Record<string, Repo>, note: string): Promise<boolean> {
    if (!current) return false;
    setReasons([]);
    try {
      await api.saveConfig("repos", { ...current.document, repos }, note);
      await load();
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.reasons.length) setReasons(err.reasons);
      else setError(err instanceof Error ? err.message : String(err));
      return false;
    }
  }

  const repos: Record<string, Repo> = current?.document.repos ?? {};
  return (
    <>
      <PageHeader
        title="Repositories"
        description="The repos a goal may change. Each change is a new version of the repo registry, with its history and rollback under Configuration."
        actions={<Button leftSection={<IconPlus size={16} />} onClick={() => setForm({ mode: "add" })}>Add repository</Button>}
      />
      <ErrorAlert error={error} />
      {reasons.length > 0 && (
        <Alert color="red" title="Not saved" mb="md">
          <List size="sm">{reasons.map((r) => <List.Item key={r}>{r}</List.Item>)}</List>
        </Alert>
      )}
      <DataTable head={["Repository", "Tracker", "Default acceptance checks", ""]}>
        {Object.entries(repos).map(([name, spec]) => (
          <Table.Tr key={name}>
            <Table.Td><Anchor href={`https://github.com/${name}`} target="_blank" ff="monospace" size="sm">{name}</Anchor></Table.Td>
            <Table.Td><Badge variant="light" tt="none">{spec.tracker ?? "github"}</Badge></Table.Td>
            <Table.Td><Stack gap={2}>{(spec.default_checks ?? []).map((c) => <Code key={c}>{c}</Code>)}</Stack></Table.Td>
            <Table.Td>
              <Group gap="xs" justify="flex-end" wrap="nowrap">
                <Button size="compact-sm" variant="default" leftSection={<IconEdit size={14} />} onClick={() => setForm({ mode: "edit", name })}>Edit</Button>
                <Button
                  size="compact-sm"
                  color="red"
                  variant="light"
                  leftSection={<IconTrash size={14} />}
                  onClick={() => void saveRegistry(Object.fromEntries(Object.entries(repos).filter(([n]) => n !== name)), `removed ${name}`)}
                >
                  Remove
                </Button>
              </Group>
            </Table.Td>
          </Table.Tr>
        ))}
      </DataTable>
      {available && available.install_urls.length > 0 && (
        <Text size="sm" mt="md">
          A repo not listed under Add repository needs the GitHub App installed on it:{" "}
          {available.install_urls.map((url) => <Anchor key={url} href={url} target="_blank">install the app</Anchor>)}.
        </Text>
      )}
      {form?.mode === "add" && (
        <RepoForm
          title="Add repository"
          names={(available?.repos ?? []).map((r) => r.name)}
          onClose={() => setForm(null)}
          onSave={(name, spec) => saveRegistry({ ...repos, [name]: spec }, `added ${name}`)}
        />
      )}
      {form?.mode === "edit" && (
        <RepoForm
          title={`Edit ${form.name}`}
          names={null}
          repo={{ name: form.name, spec: repos[form.name] ?? {} }}
          onClose={() => setForm(null)}
          onSave={(name, spec) => saveRegistry({ ...repos, [name]: spec }, `changed ${name}`)}
        />
      )}
    </>
  );
}
