import { useState } from "react";
import { ActionIcon, Alert, Anchor, Badge, Button, Code, Group, Modal, Paper, ScrollArea, SegmentedControl, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconDownload, IconTrash } from "@tabler/icons-react";
import { api, type SkillEntry } from "../../api";
import { DataTable, Empty } from "../../components/ui";
import type { Staging } from "./ConfigViews";

// The skills a harness profile's runs may load. An admin imports one from a git repository or from files on
// their machine, reads all of it, and stages it like any other change; the save makes it a new version.
// A skill is instructions an agent will follow and scripts it will run, so nothing is added unread.

const kb = (files: Record<string, string>) => Math.max(1, Math.round(Object.values(files).reduce((n, t) => n + new Blob([t]).size, 0) / 1024));

export function sourceLine(source: SkillEntry["source"]): string {
  if (source?.kind === "git") {
    const at = source.ref ? ` at ${source.ref}` : "";
    return `${source.url}${source.path ? ` / ${source.path}` : ""}${at}${source.commit ? ` (${String(source.commit).slice(0, 7)})` : ""}`;
  }
  if (source?.kind === "upload") return `Uploaded${source.by ? ` by ${source.by}` : ""}`;
  return "Ships with the meta-harness";
}

// Every file of a skill, each in full: what a run will be given.
function SkillFiles({ skill }: { skill: SkillEntry }) {
  const paths = Object.keys(skill.files).sort((a, b) => (a === "SKILL.md" ? -1 : b === "SKILL.md" ? 1 : a.localeCompare(b)));
  return (
    <Stack gap="md">
      {paths.map((path) => (
        <div key={path}>
          <Text ff="monospace" size="sm" fw={600} mb={4}>{path}</Text>
          <ScrollArea.Autosize mah={360} type="auto">
            <Code block>{skill.files[path]}</Code>
          </ScrollArea.Autosize>
        </div>
      ))}
    </Stack>
  );
}

function ImportDialog({ names, onClose, onAdd }: { names: string[]; onClose: () => void; onAdd: (name: string, skill: SkillEntry) => void }) {
  const [from, setFrom] = useState<"git" | "files">("git");
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [path, setPath] = useState("");
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  const [read, setRead] = useState<{ name: string; skill: SkillEntry } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function pick(list: FileList | null) {
    if (!list?.length) return;
    // A folder chosen in the browser: each file keeps its path under the folder.
    const found: Record<string, string> = {};
    for (const file of Array.from(list)) {
      const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      found[relative.split("/").slice(1).join("/") || file.name] = await file.text();
    }
    setFiles(found);
  }

  async function readSkill() {
    setBusy(true);
    setError(null);
    try {
      setRead(await api.readSkill(from === "git" ? { url, ref: ref || undefined, path: path || undefined } : { files: files ?? {} }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const replaces = read !== null && names.includes(read.name);
  return (
    <Modal opened onClose={onClose} title="Import a skill" size="xl">
      <Stack gap="md">
        {!read ? (
          <>
            <SegmentedControl value={from} onChange={(v) => setFrom(v as "git" | "files")} data={[{ value: "git", label: "From a git repository" }, { value: "files", label: "From a folder" }]} />
            {from === "git" ? (
              <>
                <TextInput label="Repository" description="An https:// URL. The planner reads it as it is now; later changes there change nothing here." placeholder="https://github.com/owner/skills" value={url} onChange={(e) => setUrl(e.currentTarget.value)} />
                <Group grow>
                  <TextInput label="Branch or tag" placeholder="the default branch" value={ref} onChange={(e) => setRef(e.currentTarget.value)} />
                  <TextInput label="Path to the skill" placeholder="skills/my-skill" value={path} onChange={(e) => setPath(e.currentTarget.value)} />
                </Group>
              </>
            ) : (
              <Stack gap={4}>
                <Text size="sm" fw={500}>The skill's folder</Text>
                {/* webkitdirectory picks a whole folder; React passes it through as an attribute. */}
                <input type="file" aria-label="The skill's folder" {...({ webkitdirectory: "", directory: "" } as object)} multiple onChange={(e) => void pick(e.currentTarget.files)} />
                <Text size="xs" c="dimmed">{files ? `${Object.keys(files).length} files` : "The folder that holds its SKILL.md."}</Text>
              </Stack>
            )}
            {error && <Alert color="red" role="alert">{error}</Alert>}
            <Group justify="flex-end">
              <Button variant="default" onClick={onClose}>Cancel</Button>
              <Button loading={busy} disabled={from === "git" ? !url.trim() : !files} onClick={() => void readSkill()}>Read the skill</Button>
            </Group>
          </>
        ) : (
          <>
            <Group gap="sm">
              <Text fw={650}>{read.name}</Text>
              <Badge variant="light" tt="none">{Object.keys(read.skill.files).length} files, {kb(read.skill.files)} KB</Badge>
              {replaces && <Badge color="orange" variant="light" tt="none">replaces the skill of that name</Badge>}
            </Group>
            <Text size="sm">{read.skill.description}</Text>
            <Text size="xs" c="dimmed">{sourceLine(read.skill.source)}</Text>
            <Alert color="yellow">
              Read all of it before adding it. A run that loads this skill follows its instructions and may run its scripts, inside its sandbox.
            </Alert>
            <SkillFiles skill={read.skill} />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setRead(null)}>Back</Button>
              <Button onClick={() => { onAdd(read.name, read.skill); onClose(); }}>{replaces ? "Replace the skill" : "Add the skill"}</Button>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  );
}

// Which profiles name each skill, so a skill in use says so, and cannot be removed until they stop.
function usedBy(profiles: Record<string, { skills?: string[] }>, name: string): string[] {
  return Object.entries(profiles).filter(([, p]) => (p.skills ?? []).includes(name)).map(([profile]) => profile);
}

export function SkillsView({ staging: s, profiles }: { staging: Staging; profiles: Record<string, { skills?: string[] }> }) {
  const skills: Record<string, SkillEntry> = s.document.skills ?? {};
  const [importing, setImporting] = useState(false);
  const [reading, setReading] = useState<string | null>(null);
  const names = Object.keys(skills);
  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        A harness profile names the skills its runs load. A run is told each one's name and description, and reads the rest when its work needs it. Nothing loads from a task's repo.
      </Text>
      {names.length ? (
        <DataTable head={["Skill", "What it is for", "Source", "Used by", ""]} minWidth={860}>
          {names.map((name) => {
            const skill = skills[name];
            const users = usedBy(profiles, name);
            return (
              <Table.Tr key={name} data-testid={`skill-${name}`}>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    <Anchor component="button" type="button" fw={600} size="sm" onClick={() => setReading(name)}>{name}</Anchor>
                    {s.original.skills?.[name] === undefined && <Badge size="xs" color="yellow" variant="light" tt="none">new</Badge>}
                  </Group>
                  <Text size="xs" c="dimmed">{Object.keys(skill.files).length} files, {kb(skill.files)} KB</Text>
                </Table.Td>
                <Table.Td maw={420}><Text size="sm" lineClamp={3}>{skill.description}</Text></Table.Td>
                <Table.Td maw={260}><Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>{sourceLine(skill.source)}</Text></Table.Td>
                <Table.Td><Text size="sm">{users.length ? users.join(", ") : <Text span size="sm" c="dimmed">No profile yet</Text>}</Text></Table.Td>
                <Table.Td>
                  {!s.readOnly && (
                    <Tooltip label={users.length ? `Named by ${users.join(", ")}: remove it from those profiles first` : `Remove ${name}`}>
                      <ActionIcon variant="subtle" color="red" aria-label={`Remove skill ${name}`} disabled={users.length > 0} onClick={() => s.stage(["skills", name], undefined)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            );
          })}
        </DataTable>
      ) : (
        <Empty>No skills yet.</Empty>
      )}
      {!s.readOnly && (
        <Paper p="sm" withBorder>
          <Group justify="space-between">
            <Text size="sm">Import a skill from a git repository, or from a folder on this machine.</Text>
            <Button size="xs" leftSection={<IconDownload size={14} />} onClick={() => setImporting(true)}>Import a skill</Button>
          </Group>
        </Paper>
      )}
      {importing && <ImportDialog names={names} onClose={() => setImporting(false)} onAdd={(name, skill) => s.stage(["skills", name], skill)} />}
      {reading && skills[reading] && (
        <Modal opened onClose={() => setReading(null)} title={reading} size="xl">
          <Stack gap="sm">
            <Text size="sm">{skills[reading].description}</Text>
            <Text size="xs" c="dimmed">{sourceLine(skills[reading].source)}</Text>
            <SkillFiles skill={skills[reading]} />
          </Stack>
        </Modal>
      )}
    </Stack>
  );
}
