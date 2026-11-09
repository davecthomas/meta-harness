import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Code,
  Group,
  JsonInput,
  List,
  Loader,
  Modal,
  Paper,
  Popover,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowBackUp, IconCode, IconDeviceFloppy, IconEdit, IconInfoCircle } from "@tabler/icons-react";
import { api, ApiError, type ConfigKind, type ConfigSummary, type ConfigVersion } from "../api";
import { useUnsavedChanges } from "../components/UnsavedChanges";
import { DataTable, ErrorAlert, Facts, PageHeader, SectionTitle, when } from "../components/ui";

const ORIGIN_COLORS: Record<string, string> = { seed: "gray", edit: "indigo", rollback: "orange" };

// Configuration: router tiers, harness profiles and the repo registry. Each kind shows its active
// version laid out for reading, an editor that saves a new version (refused with every reason when
// the service would refuse it), and the version history, where any version can be made active again.
export function ConfigPage() {
  const [params, setParams] = useSearchParams();
  const [kinds, setKinds] = useState<ConfigSummary[] | null>(null);
  const [source, setSource] = useState<"db" | "file">("db");
  const [error, setError] = useState<string | null>(null);
  const kind = (params.get("kind") as ConfigKind) || "tiers";

  const reload = useCallback(() => {
    api.configKinds().then(
      (r) => {
        setKinds(r.kinds);
        setSource(r.source);
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
            <KindPanel summary={k} onSaved={reload} />
          </Tabs.Panel>
        ))}
      </Tabs>
    </>
  );
}

function KindPanel({ summary, onSaved }: { summary: ConfigSummary; onSaved: () => void }) {
  const [current, setCurrent] = useState<ConfigVersion | null>(null);
  const [versions, setVersions] = useState<ConfigVersion[]>([]);
  const [editing, setEditing] = useState(false);
  const [viewing, setViewing] = useState<ConfigVersion | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  async function changed(saved: ConfigVersion, verb: string) {
    notifications.show({ color: "teal", title: `${summary.title} ${verb}`, message: `Version ${saved.version} is active.` });
    setEditing(false);
    await load();
    onSaved();
  }

  async function rollback(version: number) {
    try {
      await changed(await api.rollbackConfig(summary.kind, version), `rolled back to version ${version}`);
    } catch (err) {
      const reasons = err instanceof ApiError && err.reasons.length ? err.reasons : null;
      notifications.show({
        color: "red",
        title: `Version ${version} no longer fits`,
        message: reasons ? reasons.join("; ") : err instanceof Error ? err.message : String(err),
        autoClose: false,
      });
    }
  }

  if (!current) return error ? <ErrorAlert error={error} /> : <Center h={160}><Loader /></Center>;
  return (
    <>
      <Paper mb="lg" p="md">
        <Group justify="space-between" wrap="wrap" gap="md">
          <Stack gap={2} style={{ flex: "1 1 360px" }}>
            <Text c="dimmed" size="sm">{summary.description}</Text>
            <Text size="sm">
              Version {current.version}, {current.origin === "seed" ? "seeded from the deployment's file" : `saved by ${current.edited_by}`},{" "}
              {when(current.created_at)}{current.note ? `: ${current.note}` : ""}
            </Text>
          </Stack>
          {!editing && (
            <Group gap="sm">
              <Button variant="default" leftSection={<IconCode size={16} />} onClick={() => setViewing(current)}>JSON</Button>
              <Button leftSection={<IconEdit size={16} />} onClick={() => setEditing(true)}>Edit</Button>
            </Group>
          )}
        </Group>
      </Paper>

      {editing ? (
        <Editor kind={summary.kind} current={current} onCancel={() => setEditing(false)} onSaved={(saved) => changed(saved, "saved")} />
      ) : (
        <ActiveView kind={summary.kind} document={current.document} />
      )}

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

function Editor(props: { kind: ConfigKind; current: ConfigVersion; onCancel: () => void; onSaved: (v: ConfigVersion) => void }) {
  const original = JSON.stringify(props.current.document, null, 2);
  const [text, setText] = useState(original);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [reasons, setReasons] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  // The checked save. Resolves true when the version is saved; a refusal stays in the editor with its reasons.
  async function save(): Promise<boolean> {
    let document: unknown;
    try {
      document = JSON.parse(text);
    } catch (err) {
      setError(`The document is not valid JSON: ${err instanceof Error ? err.message : String(err)}`);
      return false;
    }
    setSaving(true);
    setReasons([]);
    setError(null);
    try {
      const saved = await api.saveConfig(props.kind, document, note);
      unsaved.release();
      props.onSaved(saved);
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.reasons.length) setReasons(err.reasons);
      else setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  const unsaved = useUnsavedChanges({
    dirty: text !== original || note.trim() !== "",
    describe: `the ${props.kind === "tiers" ? "router tiers" : props.kind === "profiles" ? "harness profiles" : "repo registry"}, edited from version ${props.current.version}`,
    save,
    discard: () => {
      setText(original);
      setNote("");
    },
  });

  return (
    <Paper mb="lg">
      <Stack gap="md">
        {reasons.length > 0 && (
          <Alert color="red" title="Not saved: the service would refuse this version">
            <List size="sm" spacing={4}>{reasons.map((r) => <List.Item key={r}>{r}</List.Item>)}</List>
          </Alert>
        )}
        <ErrorAlert error={error} />
        {unsaved.element}
        <JsonInput
          label={`Edit version ${props.current.version}`}
          description="Saving checks it the way the service that reads it checks its configuration at startup."
          value={text}
          onChange={setText}
          validationError="Not valid JSON"
          formatOnBlur
          autosize
          minRows={12}
          maxRows={40}
          styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)", fontSize: 13 } }}
        />
        <TextInput label="Note" description="Why you changed it. It shows in the version history." value={note} onChange={(e) => setNote(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={() => unsaved.confirm(props.onCancel)}>Cancel</Button>
          <Button leftSection={<IconDeviceFloppy size={16} />} loading={saving} onClick={() => void save()}>Save as version {props.current.version + 1}</Button>
        </Group>
      </Stack>
    </Paper>
  );
}

function ActiveView({ kind, document }: { kind: ConfigKind; document: Record<string, any> }) {
  if (kind === "tiers") return <TiersView router={document} />;
  if (kind === "profiles") return <ProfilesView profiles={document.profiles ?? {}} />;
  return <ReposView repos={document.repos ?? {}} />;
}

function TiersView({ router }: { router: Record<string, any> }) {
  const tiers: any[] = router.tiers ?? [];
  return (
    <>
      <Facts
        items={[
          ["Classifier", <Text size="sm">{router.classifier?.engine ?? "the library's default"}{router.classifier?.model ? `, ${router.classifier.model}` : ""}{router.classifier?.min_confidence !== undefined ? `, confidence at least ${router.classifier.min_confidence}` : ""}</Text>],
          ["Default tier", <Text size="sm">{router.default_tier ?? "none"}</Text>],
          ["When a tier is used up", <Text size="sm">{router.on_exhausted === "escalate" ? "Escalate one tier up" : router.on_exhausted ?? "fail"}</Text>],
        ]}
      />
      <SimpleGrid cols={{ base: 1, md: 2 }} mt="lg">
        {tiers.map((tier, index) => (
          <Card key={tier.name} withBorder padding="lg">
            <Group justify="space-between" mb="xs">
              <Group gap="xs">
                <Badge variant="light" radius="sm">Tier {index + 1}</Badge>
                <Text fw={650}>{tier.name}</Text>
              </Group>
              {tier.thinking && <Badge variant="outline" tt="none">thinking {tier.thinking}</Badge>}
            </Group>
            <Text size="sm" c="dimmed" mb="md">{tier.description}</Text>
            <Text size="xs" fw={600} c="dimmed" tt="uppercase" mb={6}>Candidates, in order</Text>
            <Stack gap={6}>
              {(tier.models ?? []).map((m: string, i: number) => {
                const [profile, model] = String(m).replace(/^harness:/, "").split("/");
                return (
                  <Group key={m} gap="xs" wrap="nowrap">
                    <Text size="sm" c="dimmed" w={18}>{i + 1}.</Text>
                    <Badge variant="dot" tt="none">{profile}</Badge>
                    <Code>{model}</Code>
                  </Group>
                );
              })}
            </Stack>
          </Card>
        ))}
      </SimpleGrid>
    </>
  );
}

function ProfilesView({ profiles }: { profiles: Record<string, any> }) {
  return (
    <DataTable head={["Profile", "Tools", "Limits", "Resources", "Bash allowlist", "Image"]} minWidth={860}>
      {Object.entries(profiles).map(([name, p]) => (
        <Table.Tr key={name}>
          <Table.Td><Text fw={600} size="sm">{name}</Text></Table.Td>
          <Table.Td maw={260}>
            <Group gap={4}>{(p.tools ?? []).map((t: string) => <Badge key={t} size="sm" variant="light" tt="none">{t}</Badge>)}</Group>
          </Table.Td>
          <Table.Td><Text size="sm">{p.max_turns} turns, {p.max_minutes} min</Text></Table.Td>
          <Table.Td><Text size="sm">{p.resources ? `${p.resources.cpu} vCPU, ${p.resources.memory_gb} GB` : "default"}</Text></Table.Td>
          <Table.Td>
            {p.bash_allow ? (
              <Tooltip multiline w={320} label={p.bash_allow.join(", ")}>
                <Badge variant="outline" tt="none">{p.bash_allow.length} programs</Badge>
              </Tooltip>
            ) : (
              <Text size="sm" c="dimmed">any program</Text>
            )}
          </Table.Td>
          <Table.Td><Code>{p.image ?? "default"}</Code></Table.Td>
        </Table.Tr>
      ))}
    </DataTable>
  );
}

function ReposView({ repos }: { repos: Record<string, any> }) {
  return (
    <DataTable head={["Repo", "Tracker", "Default acceptance checks"]}>
      {Object.entries(repos).map(([name, r]) => (
        <Table.Tr key={name}>
          <Table.Td><Text ff="monospace" size="sm">{name}</Text></Table.Td>
          <Table.Td><Badge variant="light" tt="none">{r.tracker ?? "github"}</Badge></Table.Td>
          <Table.Td><Stack gap={2}>{(r.default_checks ?? []).map((c: string) => <Code key={c}>{c}</Code>)}</Stack></Table.Td>
        </Table.Tr>
      ))}
    </DataTable>
  );
}
