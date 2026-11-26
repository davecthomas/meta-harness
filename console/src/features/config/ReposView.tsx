import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Button, Group, Modal, Paper, Select, Stack, Table, Text, Textarea } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { api } from "../../api";
import { InlineField } from "../../components/InlineField";
import { DataTable } from "../../components/ui";
import { changed, Reasons, type Staging } from "./ConfigViews";
import { RemovedRows, removedEntries } from "./RemovedRows";

// The repositories goals may change: the one place they are added, removed and edited. A repository is added
// from those the deployment's GitHub App is installed on. Every change stages, like the other kinds, and is
// reviewed and saved as one version with its history below.

type Available = { repos: { name: string; private: boolean }[]; install_urls: string[] };

function lines(text: string): string[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

export function ReposView({ staging: s }: { staging: Staging }) {
  const repos: Record<string, any> = s.document.repos ?? {};
  const names = Object.keys(repos);
  const [available, setAvailable] = useState<Available | null>(null);
  const [unlisted, setUnlisted] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    if (s.readOnly) return;
    api.reposAvailable().then(setAvailable, (err) => setUnlisted(err instanceof Error ? err.message : String(err)));
  }, [s.readOnly]);

  // The App's repos the registry does not hold yet, staged ones included.
  const taken = new Set(names.map((n) => n.toLowerCase()));
  const addable = (available?.repos ?? []).map((r) => r.name).filter((n) => !taken.has(n.toLowerCase()));
  const install = available?.install_urls ?? [];
  const add = !s.readOnly && (
    <Button leftSection={<IconPlus size={16} />} variant={names.length ? "default" : "filled"} onClick={() => setAdding(true)}>
      Add repository
    </Button>
  );
  const installLine = install.length > 0 && (
    <Text size="sm" c="dimmed">
      A repository that is not offered needs the GitHub App installed on it:{" "}
      {install.map((url) => <Anchor key={url} href={url} target="_blank" size="sm">install the App</Anchor>)}, then reload this page.
    </Text>
  );

  return (
    <Stack gap="sm">
      {unlisted && (
        <Text size="sm" c="orange">
          The GitHub App's repositories could not be listed, so none can be added now: {unlisted}. Reload to try again; if it
          keeps failing, <Anchor component={Link} to="/admin/logs?q=GitHub%20App&minutes=60" size="sm">Admin, Logs</Anchor> has
          the warning that says why.
        </Text>
      )}
      {names.length === 0 ? (
        <Paper p="lg" data-testid="no-repos">
          <Stack gap="sm" align="flex-start">
            <Text fw={600}>No repository yet</Text>
            <Text size="sm" c="dimmed">
              A goal changes code in a repository listed here, so nothing can run until there is one. Add one, then review and
              save the change.
            </Text>
            {add}
            {installLine}
          </Stack>
        </Paper>
      ) : (
        <>
          <Group justify="space-between" align="flex-end" wrap="wrap">
            {installLine || <span />}
            {add}
          </Group>
          <DataTable head={["Repository", "Default acceptance checks", ""]}>
            {Object.entries(repos).map(([name, r]) => (
              <Table.Tr key={name} data-testid={`repo-${name}`}>
                <Table.Td>
                  <Anchor href={`https://github.com/${name}`} target="_blank" ff="monospace" size="sm">{name}</Anchor>
                  {s.original.repos?.[name] === undefined && <Badge size="xs" color="yellow" variant="light" tt="none">not saved</Badge>}
                  <Reasons reasons={s.reasons(`repo:${name}`)} />
                </Table.Td>
                <Table.Td>
                  <InlineField
                    kind="tags"
                    label={`Default acceptance checks of ${name}`}
                    value={r.default_checks ?? []}
                    monospace
                    placeholder="A command, Enter to add"
                    empty={<Text size="sm" c="dimmed">None: a task runs only the checks its issue names</Text>}
                    changed={changed(s, ["repos", name, "default_checks"])}
                    reasons={s.reasons(`repo:${name}:default_checks`)}
                    readOnly={s.readOnly}
                    onCommit={(v) => s.stage(["repos", name, "default_checks"], v)}
                  />
                </Table.Td>
                <Table.Td>
                  {!s.readOnly && (
                    <Group justify="flex-end">
                      <Button
                        size="compact-sm"
                        color="red"
                        variant="light"
                        leftSection={<IconTrash size={14} />}
                        aria-label={`Remove ${name}`}
                        onClick={() => s.stage(["repos", name], undefined)}
                      >
                        Remove
                      </Button>
                    </Group>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </DataTable>
        </>
      )}
      {!s.readOnly && <RemovedRows what="repository" rows={removedEntries(s, "repos")} />}
      {adding && (
        <AddRepo
          names={addable}
          install={install}
          onClose={() => setAdding(false)}
          onAdd={(name, checks) => {
            s.stage(["repos", name], { default_checks: checks });
            setAdding(false);
          }}
        />
      )}
    </Stack>
  );
}

// Choosing a repository to add, with its default checks. Adding stages it; nothing is saved until the review.
function AddRepo(props: { names: string[]; install: string[]; onClose: () => void; onAdd: (name: string, checks: string[]) => void }) {
  const [name, setName] = useState<string | null>(null);
  const [checks, setChecks] = useState("");
  return (
    <Modal opened onClose={props.onClose} title="Add repository" size="lg">
      <Stack gap="md">
        <Select
          label="Repository"

          searchable
          data={props.names}
          value={name}
          onChange={setName}
          nothingFoundMessage="None left to add: install the GitHub App on the repository first"
        />
        {props.names.length === 0 && props.install.length > 0 && (
          <Text size="sm">
            Every repository the App is installed on is listed already.{" "}
            {props.install.map((url) => <Anchor key={url} href={url} target="_blank" size="sm">Install the App on another</Anchor>)}, then
            reload this page.
          </Text>
        )}
        <Textarea
          label="Default acceptance checks"
          description="Optional. One command per line. A task runs them when its issue names none."
          autosize
          minRows={3}
          value={checks}
          onChange={(e) => setChecks(e.currentTarget.value)}
          styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={props.onClose}>Cancel</Button>
          <Button disabled={!name} onClick={() => name && props.onAdd(name, lines(checks))}>Add</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
