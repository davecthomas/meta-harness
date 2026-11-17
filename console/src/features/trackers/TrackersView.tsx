import { useEffect, useState } from "react";
import { ActionIcon, Alert, Badge, Button, Group, Paper, Stack, Table, Text, Tooltip } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { api, type Tracker, type TrackerSetting } from "../../api";
import { InlineField } from "../../components/InlineField";
import { ErrorAlert } from "../../components/ui";
import { changed, Reasons, stageOptional, type Staging } from "../config/ConfigViews";

// The Issue trackers configuration: every tracker the meta-harness supports, as its adapter describes it. One
// is set up by filling in its settings, and one of those set up is the default, which a new goal uses unless it
// names another. A goal keeps the tracker it was created with. A sign-in is never here: it is the deployment's
// secrets, and each tracker says whether its sign-in is there.

const STATUSES: [string, string][] = [
  ["todo", "To do"],
  ["in_progress", "In progress"],
  ["in_review", "In review"],
  ["done", "Done"],
];

export function TrackersView({ staging: s }: { staging: Staging }) {
  const [supported, setSupported] = useState<Tracker[] | null>(null);
  const [repos, setRepos] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.trackers().then((found) => setSupported(found.trackers), (err) => setError(err instanceof Error ? err.message : String(err)));
    api.repos().then((found) => setRepos(found.map((r) => r.name)), () => setRepos([]));
  }, []);

  if (error) return <ErrorAlert error={`The supported trackers could not be read: ${error}`} />;
  if (!supported) return null;
  const setUp: Record<string, any> = s.document.trackers ?? {};
  const chosen: string = s.document.default ?? "github";
  return (
    <Stack gap="md">
      <Reasons reasons={s.reasons("default")} />
      {supported.map((tracker) => {
        const settings = setUp[tracker.scheme];
        const isSetUp = settings !== undefined;
        const isDefault = chosen === tracker.scheme;
        return (
          <Paper key={tracker.scheme} p="md" data-testid={`tracker-${tracker.scheme}`}>
            <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
              <Stack gap={4} style={{ flex: "1 1 360px" }}>
                <Group gap="xs">
                  <Text fw={600}>{tracker.title}</Text>
                  {isDefault && <Badge color="indigo" variant="light" tt="none">Default for new goals</Badge>}
                  <Badge color={isSetUp ? "teal" : "gray"} variant="light" tt="none">{isSetUp ? "Set up" : "Not set up"}</Badge>
                  {isSetUp && tracker.credentials.length > 0 && (
                    <Badge color={tracker.signed_in ? "teal" : "orange"} variant="light" tt="none">
                      {tracker.signed_in ? "Signed in" : "Sign-in missing"}
                    </Badge>
                  )}
                </Group>
                <Text size="sm" c="dimmed">{tracker.description}</Text>
              </Stack>
              {!s.readOnly && (
                <Group gap="xs">
                  {!isSetUp && (
                    <Button size="xs" variant="light" onClick={() => s.stage(["trackers", tracker.scheme], blank(tracker))}>
                      Set up {tracker.title}
                    </Button>
                  )}
                  {isSetUp && !isDefault && (
                    <Button size="xs" variant="light" onClick={() => s.stage(["default"], tracker.scheme)}>Make default</Button>
                  )}
                  {isSetUp && !isDefault && (
                    <Tooltip label="Goals that use it keep their issues, and stop reading new ones from it" multiline w={260}>
                      <Button size="xs" variant="subtle" color="red" onClick={() => s.stage(["trackers", tracker.scheme], undefined)}>
                        Remove
                      </Button>
                    </Tooltip>
                  )}
                </Group>
              )}
            </Group>
            {isSetUp && tracker.credentials.length > 0 && !tracker.signed_in && (
              <Alert color="orange" mt="sm" p="xs" title={`${tracker.title} has no sign-in yet`}>
                <Text size="sm">
                  Missing: <Text span ff="monospace">{tracker.missing_credentials.join(", ")}</Text>. A sign-in is a secret, so it
                  is not set here: add each as a secret of the deployment (Secret Manager), then deploy. Until then a goal that
                  uses {tracker.title} reads no issues, and says so.
                </Text>
              </Alert>
            )}
            <Reasons reasons={s.reasons(`tracker:${tracker.scheme}`)} />
            {isSetUp && tracker.settings.length > 0 && (
              <Stack gap="sm" mt="md">
                {tracker.settings.map((setting) => (
                  <div key={setting.name}>
                    <Text size="sm" fw={500}>
                      {setting.label}
                      {setting.required && <Text span c="red"> *</Text>}
                    </Text>
                    <Text size="xs" c="dimmed" mb={4}>{setting.help}</Text>
                    <SettingField staging={s} tracker={tracker} setting={setting} value={settings?.[setting.name]} repos={repos} />
                  </div>
                ))}
              </Stack>
            )}
          </Paper>
        );
      })}
    </Stack>
  );
}

// A tracker's settings as they start: each required one empty, for the operator to fill in.
function blank(tracker: Tracker): Record<string, unknown> {
  const start: Record<string, unknown> = {};
  for (const setting of tracker.settings) {
    if (setting.required) start[setting.name] = setting.kind === "projects" ? [] : "";
  }
  return start;
}

function SettingField(props: { staging: Staging; tracker: Tracker; setting: TrackerSetting; value: any; repos: string[] }) {
  const { staging: s, tracker, setting, value, repos } = props;
  const path = ["trackers", tracker.scheme, setting.name];
  if (setting.kind === "projects") {
    const projects: { key?: string; repo?: string }[] = Array.isArray(value) ? value : [];
    const put = (next: unknown[]) => s.stage(path, next);
    return (
      <Stack gap="xs">
        {projects.length > 0 && (
          <Table withTableBorder maw={640}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Project key</Table.Th>
                <Table.Th>Repo its issues are worked on in</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {projects.map((project, i) => (
                <Table.Tr key={i} data-testid={`project-${i}`}>
                  <Table.Td>
                    <InlineField
                      kind="text"
                      label={`Key of project ${i + 1}`}
                      value={project.key ?? ""}
                      empty={<Text size="sm" c="dimmed">Set the key, such as PROJ</Text>}
                      monospace
                      placeholder="PROJ"
                      readOnly={s.readOnly}
                      onCommit={(v) => put(projects.map((p, at) => (at === i ? { ...p, key: v.trim().toUpperCase() } : p)))}
                    />
                  </Table.Td>
                  <Table.Td>
                    <InlineField
                      kind="select"
                      label={`Repo of project ${i + 1}`}
                      value={project.repo || null}
                      data={project.repo && !repos.includes(project.repo) ? [project.repo, ...repos] : repos}
                      empty={<Text size="sm" c="dimmed">Choose a repo</Text>}
                      monospace
                      searchable
                      readOnly={s.readOnly}
                      onCommit={(v) => put(projects.map((p, at) => (at === i ? { ...p, repo: v ?? "" } : p)))}
                    />
                  </Table.Td>
                  <Table.Td w={40}>
                    {!s.readOnly && (
                      <ActionIcon variant="subtle" color="red" aria-label={`Remove project ${project.key || i + 1}`} onClick={() => put(projects.filter((_, at) => at !== i))}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {!s.readOnly && (
          <div>
            <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} onClick={() => put([...projects, { key: "", repo: repos.length === 1 ? repos[0] : "" }])}>
              Add a project
            </Button>
          </div>
        )}
      </Stack>
    );
  }
  if (setting.kind === "statuses") {
    const names: Record<string, string> = value && typeof value === "object" ? value : {};
    return (
      <Group gap="lg" align="flex-start">
        {STATUSES.map(([status, label]) => (
          <div key={status}>
            <Text size="xs" c="dimmed">{label}</Text>
            <InlineField
              kind="text"
              label={`${tracker.title}'s status for ${label.toLowerCase()}`}
              value={names[status] ?? ""}
              empty={<Text size="sm" c="dimmed">The usual</Text>}
              changed={changed(s, [...path, status])}
              readOnly={s.readOnly}
              onCommit={(v) => {
                const next = { ...names, [status]: v.trim() };
                if (!v.trim()) delete next[status];
                stageOptional(s, path, Object.keys(next).length ? next : undefined);
              }}
            />
          </div>
        ))}
      </Group>
    );
  }
  return (
    <InlineField
      kind="text"
      label={`${tracker.title}: ${setting.label}`}
      value={typeof value === "string" ? value : ""}
      empty={<Text size="sm" c="dimmed">{setting.required ? "Not set yet" : "The usual"}</Text>}
      changed={changed(s, path)}
      readOnly={s.readOnly}
      onCommit={(v) => (setting.required ? s.stage(path, v.trim()) : stageOptional(s, path, v.trim()))}
    />
  );
}
