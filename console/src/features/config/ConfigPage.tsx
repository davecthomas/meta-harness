import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Center, Code, Group, List, Loader, Modal, Paper, Popover, Stack, Table, Tabs, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowBackUp, IconCode, IconInfoCircle } from "@tabler/icons-react";
import { api, ApiError, type ConfigKind, type ConfigSummary, type ConfigVersion } from "../../api";
import { useUnsavedChanges } from "../../components/UnsavedChanges";
import { DataTable, ErrorAlert, PageHeader, SectionTitle, when } from "../../components/ui";
import { ReposView, TaskPolicyView, type Staging } from "./ConfigViews";
import { ProfilesView } from "./ProfilesView";
import { JsonDialog, ReviewDialog, StagedBar } from "./StagedChanges";
import { TiersView } from "./TiersView";
import { countChanges, reasonTarget, setIn } from "./staging";

const ORIGIN_COLORS: Record<string, string> = { seed: "gray", edit: "indigo", rollback: "orange" };

// Configuration: router tiers, harness profiles, the repo registry and the task policy. Each kind shows its active
// version laid out for reading, with every value edited in place. An edit stages; the staged changes are
// reviewed as a diff and saved together as one version (refused with every reason, each beside its value,
// when the service would refuse it). The version history makes any version active again.
export function ConfigPage() {
  const [params, setParams] = useSearchParams();
  const [kinds, setKinds] = useState<ConfigSummary[] | null>(null);
  const [source, setSource] = useState<"db" | "file">("db");
  const [maxSandboxes, setMaxSandboxes] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const kind = (params.get("kind") as ConfigKind) || "tiers";

  const reload = useCallback(() => {
    api.configKinds().then(
      (r) => {
        setKinds(r.kinds);
        setSource(r.source);
        setMaxSandboxes(r.max_sandboxes ?? null);
      },
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);

  useEffect(reload, [reload]);

  if (!kinds) return error ? <ErrorAlert error={error} /> : <Center h={240}><Loader /></Center>;
  return (
    <>
      <PageHeader
        title="Configuration"
        description="What tasks run on. A saved version applies to the next task routed; running tasks keep what they started with."
      />
      {source === "file" && (
        <Alert color="yellow" icon={<IconInfoCircle />} mb="lg" title="This deployment reads its configuration from files">
          Versions saved here are stored, but the services use them only once MH_CONFIG_SOURCE is db.
        </Alert>
      )}
      <Tabs value={kind} onChange={(value) => value && setParams({ kind: value })} keepMounted={false}>
        <Tabs.List mb="lg">
          {kinds.map((k) => (
            <Tabs.Tab key={k.kind} value={k.kind} rightSection={<Badge size="xs" variant="light" circle={false}>v{k.version}</Badge>}>
              {k.title}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {kinds.map((k) => (
          <Tabs.Panel key={k.kind} value={k.kind}>
            <KindPanel summary={k} onSaved={reload} maxSandboxes={maxSandboxes} />
          </Tabs.Panel>
        ))}
      </Tabs>
    </>
  );
}

function KindPanel({ summary, onSaved, maxSandboxes }: { summary: ConfigSummary; onSaved: () => void; maxSandboxes: number | null }) {
  const [current, setCurrent] = useState<ConfigVersion | null>(null);
  const [versions, setVersions] = useState<ConfigVersion[]>([]);
  // The document with the staged changes, or null while nothing is staged.
  const [staged, setStaged] = useState<Record<string, any> | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [reviewing, setReviewing] = useState(false);
  const [editingJson, setEditingJson] = useState(false);
  const [viewing, setViewing] = useState<ConfigVersion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [profileNames, setProfileNames] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const [active, history] = await Promise.all([api.config(summary.kind), api.configVersions(summary.kind)]);
      setCurrent(active);
      setVersions(history);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [summary.kind]);

  useEffect(() => {
    void load();
  }, [load]);

  // A tier candidate names a harness profile and a model: the selects offer the active profiles and the priced models.
  useEffect(() => {
    if (summary.kind !== "tiers") return;
    api.config("profiles").then((p) => setProfileNames(Object.keys(p.document.profiles ?? {})), () => setProfileNames([]));
    api.configModels().then((m) => setModels(m.map((x) => x.name)), () => setModels([]));
  }, [summary.kind]);

  const original = current?.document ?? {};
  const document = staged ?? original;
  const count = current ? countChanges(original, document) : 0;

  // Stages a whole document; one that matches the active version leaves nothing staged.
  const replace = (next: Record<string, any>) => setStaged(countChanges(original, next) ? next : null);

  async function changed(saved: ConfigVersion, verb: string) {
    notifications.show({ color: "teal", title: `${summary.title} ${verb}`, message: `Version ${saved.version} is active.` });
    await load();
    onSaved();
  }

  // The checked save of every staged change, as one version. A refusal keeps the changes staged and shows
  // each reason beside the value it is about.
  async function save(note: string): Promise<boolean> {
    if (!current || !staged) return false;
    setReasons([]);
    setError(null);
    try {
      const saved = await api.saveConfig(summary.kind, staged, note);
      unsaved.release();
      setStaged(null);
      await changed(saved, "saved");
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.reasons.length) setReasons(err.reasons);
      else setError(err instanceof Error ? err.message : String(err));
      return false;
    }
  }

  function discard() {
    setStaged(null);
    setReasons([]);
  }

  const unsaved = useUnsavedChanges({
    dirty: count > 0,
    describe: `the ${KIND_NAMES[summary.kind]}, staged on version ${current?.version ?? ""}`,
    save: () => save(""),
    discard,
  });

  // Making another version active replaces the one the staged changes are based on, so staged changes are
  // saved or discarded first, through the same prompt as leaving the page.
  function rollback(version: number) {
    unsaved.confirm(() => void makeActive(version));
  }

  async function makeActive(version: number) {
    try {
      await changed(await api.rollbackConfig(summary.kind, version), `rolled back to version ${version}`);
    } catch (err) {
      const why = err instanceof ApiError && err.reasons.length ? err.reasons : null;
      notifications.show({
        color: "red",
        title: `Version ${version} no longer fits`,
        message: why ? why.join("; ") : err instanceof Error ? err.message : String(err),
        autoClose: false,
      });
    }
  }

  if (!current) return error ? <ErrorAlert error={error} /> : <Center h={160}><Loader /></Center>;
  const staging: Staging = {
    document,
    original,
    stage: (path, value) => replace(setIn(document, path, value)),
    reasons: (target) => reasons.filter((r) => reasonTarget(summary.kind, document, r) === target),
  };
  const loose = reasons.filter((r) => reasonTarget(summary.kind, document, r) === null);

  return (
    <>
      {unsaved.element}
      <Paper mb="lg" p="md">
        <Group justify="space-between" wrap="wrap" gap="md">
          <Stack gap={2} style={{ flex: "1 1 360px" }}>
            <Text c="dimmed" size="sm">{summary.description}</Text>
            <Text size="sm">
              Version {current.version}, {current.origin === "seed" ? "seeded from the deployment's file" : `saved by ${current.edited_by}`},{" "}
              {when(current.created_at)}{current.note ? `: ${current.note}` : ""}
            </Text>
          </Stack>
          <Button variant="default" leftSection={<IconCode size={16} />} onClick={() => setEditingJson(true)}>Advanced: JSON</Button>
        </Group>
      </Paper>

      {count > 0 && (
        <StagedBar count={count} version={current.version} onDiscard={discard} onReview={() => setReviewing(true)} />
      )}
      {loose.length > 0 && (
        <Alert color="red" mb="md" title="Not saved: the service would refuse this version">
          <List size="sm" spacing={4}>{loose.map((r) => <List.Item key={r}>{r}</List.Item>)}</List>
        </Alert>
      )}
      <ErrorAlert error={error} />

      {summary.kind === "tiers" && <TiersView staging={staging} profiles={profileNames} models={models} />}
      {summary.kind === "profiles" && <ProfilesView staging={staging} />}
      {summary.kind === "repos" && <ReposView staging={staging} />}
      {summary.kind === "task_policy" && <TaskPolicyView policy={document} maxSandboxes={maxSandboxes} staging={staging} />}

      <SectionTitle>Version history</SectionTitle>
      <DataTable head={["Version", "Change", "By", "When", "Note", ""]} minWidth={760}>
        {versions.map((v) => (
          <Table.Tr key={v.version}>
            <Table.Td>
              <Group gap="xs">
                <Text fw={600} size="sm">v{v.version}</Text>
                {v.version === current.version && <Badge size="xs" color="teal" variant="light">active</Badge>}
              </Group>
            </Table.Td>
            <Table.Td>
              <Badge size="sm" variant="light" color={ORIGIN_COLORS[v.origin]} tt="none">
                {v.origin === "rollback" ? `rollback to v${v.rolled_back_from}` : v.origin}
              </Badge>
            </Table.Td>
            <Table.Td><Text size="sm">{v.edited_by}</Text></Table.Td>
            <Table.Td><Text size="sm" c="dimmed">{when(v.created_at)}</Text></Table.Td>
            <Table.Td><Text size="sm">{v.note}</Text></Table.Td>
            <Table.Td>
              <Group gap="xs" justify="flex-end" wrap="nowrap">
                <Button size="compact-sm" variant="subtle" onClick={() => setViewing(v)}>View</Button>
                {v.version !== current.version && <RollbackButton version={v.version} onConfirm={() => rollback(v.version)} />}
              </Group>
            </Table.Td>
          </Table.Tr>
        ))}
      </DataTable>

      <Modal opened={viewing !== null} onClose={() => setViewing(null)} size="xl" title={viewing ? `${summary.title}, version ${viewing.version}` : ""}>
        {viewing && <Code block style={{ maxHeight: "70vh", overflow: "auto" }}>{JSON.stringify(viewing.document, null, 2)}</Code>}
      </Modal>
      {reviewing && (
        <ReviewDialog
          version={current.version}
          before={original}
          after={document}
          onClose={() => setReviewing(false)}
          onSave={async (note) => {
            const ok = await save(note);
            setReviewing(false);
            return ok;
          }}
        />
      )}
      {editingJson && (
        <JsonDialog title={`${summary.title}: version ${current.version}${count ? ` with ${count} staged ${count === 1 ? "change" : "changes"}` : ""}`} document={document} onClose={() => setEditingJson(false)} onApply={replace} />
      )}
    </>
  );
}

function RollbackButton({ version, onConfirm }: { version: number; onConfirm: () => void }) {
  const [opened, setOpened] = useState(false);
  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <Button size="compact-sm" variant="light" color="orange" leftSection={<IconArrowBackUp size={14} />} onClick={() => setOpened((o) => !o)}>
          Make active
        </Button>
      </Popover.Target>
      <Popover.Dropdown maw={300}>
        <Text size="sm" mb="sm">Make version {version} active again? It is saved as a new version, and the next task routed uses it.</Text>
        <Group justify="flex-end" gap="xs">
          <Button size="xs" variant="default" onClick={() => setOpened(false)}>Keep current</Button>
          <Button size="xs" color="orange" onClick={() => { setOpened(false); onConfirm(); }}>Make active</Button>
        </Group>
      </Popover.Dropdown>
    </Popover>
  );
}

// What each kind is called in a sentence.
const KIND_NAMES: Record<ConfigKind, string> = {
  tiers: "router tiers",
  profiles: "harness profiles",
  repos: "repo registry",
  task_policy: "task policy",
};

