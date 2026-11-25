import { type ReactNode, useEffect, useState } from "react";
import { ActionIcon, Alert, Anchor, Badge, Button, Code, Group, Modal, Paper, ScrollArea, SegmentedControl, Stack, Table, Text, Tooltip } from "@mantine/core";
import { IconDownload, IconLock, IconTrash } from "@tabler/icons-react";
import { api, type SkillEntry } from "../../api";
import { PickedName, Unreadable } from "../../components/PickedName";
import { DataTable, Empty } from "../../components/ui";
import type { Staging } from "./ConfigViews";
import { RemovedRows, removedEntries } from "./RemovedRows";

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
  return "Ships with Muxxy";
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

// A list the import form offers: known is null while it loads, and error says why it could not be read.
type Listed = { known: string[] | null; error: string | null };
const LOADING: Listed = { known: null, error: null };
const why = (err: unknown) => (err instanceof Error ? err.message : String(err));
const loading = (list: Listed) => list.known === null && list.error === null;
// Whether a committed name is one its list offers. An empty name is always offered, and a list that cannot be
// read takes any name. A list that was read, even an empty one, takes only its own names; one still loading
// takes none.
const offered = (list: Listed, name: string) =>
  !name || list.error !== null || (list.known ?? []).some((k) => k.toLowerCase() === name.trim().toLowerCase());

// The registered repositories, as the https addresses a skill is imported from.
function useRegisteredRepos(): Listed {
  const [repos, setRepos] = useState<Listed>(LOADING);
  useEffect(() => {
    let stale = false;
    api.config("repos").then(
      (r) => !stale && setRepos({ known: Object.keys(r.document.repos ?? {}).sort().map((name) => `https://github.com/${name}`), error: null }),
      (err) => !stale && setRepos({ known: null, error: why(err) }),
    );
    return () => {
      stale = true;
    };
  }, []);
  return repos;
}

// A repository's branches and tags, and its directories that hold a SKILL.md at ref (the default branch when
// empty). The refs are listed again only when the repository changes.
function useSkillSource(url: string, ref: string) {
  const [refs, setRefs] = useState<Listed & { fallback: string | null }>({ ...LOADING, fallback: null });
  const [paths, setPaths] = useState<Listed>(LOADING);
  useEffect(() => {
    if (!url) return;
    let stale = false;
    setPaths(LOADING);
    if (!ref) setRefs({ ...LOADING, fallback: null });
    api.skillSource(url, ref || undefined).then(
      (r) => {
        if (stale) return;
        if (!ref) setRefs({ known: [...r.branches, ...r.tags], fallback: r.default_branch, error: null });
        setPaths({ known: r.paths, error: null });
      },
      (err) => {
        if (stale) return;
        if (!ref) setRefs({ known: null, fallback: null, error: why(err) });
        setPaths({ known: null, error: why(err) });
      },
    );
    return () => {
      stale = true;
    };
  }, [url, ref]);
  return { refs, paths };
}

function Labeled({ label, description, children }: { label: string; description: string; children: ReactNode }) {
  return (
    <Stack gap={2}>
      <Text size="sm" fw={500}>{label}</Text>
      <Text size="xs" c="dimmed">{description}</Text>
      {children}
    </Stack>
  );
}

// Where in git a skill is read from: a registered repository, one of its branches or tags, and a directory
// that holds a SKILL.md, each picked from what can be listed. An empty branch is the default branch, and an
// empty path the repository's top. A list that cannot be read says why, and its field takes a typed value.
// A field takes no name while its list loads: a name typed then would be committed before the list could
// refuse it.
function GitSource({ url, gitRef: ref, path, repos, refs, paths, onUrl, onRef, onPath }: { url: string; gitRef: string; path: string; repos: Listed; refs: Listed & { fallback: string | null }; paths: Listed; onUrl: (v: string) => void; onRef: (v: string) => void; onPath: (v: string) => void }) {
  const at = ref || refs.fallback || "the default branch";
  return (
    <Stack gap="sm">
      <Labeled label="Repository" description="A registered repository. The planner reads it as it is now; later changes there change nothing here.">
        <PickedName label="Repository" what="repository" where="The registry" value={url} empty={loading(repos) ? "Loading…" : "Pick a repository"} known={repos.known} unreadable={repos.error} testId="skill-repo-unknown" strict readOnly={loading(repos)} onCommit={onUrl} />
        <Unreadable what="registered repositories" error={repos.error} />
      </Labeled>
      <Group grow align="flex-start">
        <Labeled label="Branch or tag" description="Empty is the default branch.">
          <PickedName label="Branch or tag" what="branch or tag" where={url || "The repository"} value={ref} empty={url && loading(refs) ? "Loading…" : refs.fallback ? `the default branch (${refs.fallback})` : "the default branch"} known={url ? refs.known : []} unreadable={refs.error} testId="skill-ref-unknown" strict readOnly={!url || loading(refs)} onCommit={onRef} />
          <Unreadable what="branches and tags" error={url ? refs.error : null} />
        </Labeled>
        <Labeled label="Path to the skill" description="The directory that holds its SKILL.md. Empty is the repository's top.">
          <PickedName label="Path to the skill" what="skill directory" where={`${url || "The repository"} at ${at}`} value={path} empty={url && loading(paths) ? "Loading…" : "the repository's top"} known={url ? paths.known : []} unreadable={paths.error} testId="skill-path-unknown" strict readOnly={!url || loading(paths)} onCommit={onPath} />
          <Unreadable what="skill directories" error={url ? paths.error : null} />
        </Labeled>
      </Group>
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
  const repos = useRegisteredRepos();
  const { refs, paths } = useSkillSource(url, ref);
  // Each committed name is checked again against its list as it now stands, so none the list lacks is read.
  const gitReady = !!url.trim() && offered(repos, url) && offered(refs, ref) && offered(paths, path) && !loading(refs) && !loading(paths);

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
              <GitSource
                url={url}
                gitRef={ref}
                path={path}
                repos={repos}
                refs={refs}
                paths={paths}
                // A new repository or ref starts its branch and path over: the old ones may not be there.
                onUrl={(v) => { if (v !== url) { setUrl(v); setRef(""); setPath(""); } }}
                onRef={(v) => { if (v !== ref) { setRef(v); setPath(""); } }}
                onPath={setPath}
              />
            ) : (
              <Stack gap={4}>
                <Text size="sm" fw={500}>The skill's folder</Text>
                {/* webkitdirectory picks a whole folder; React passes it through as an attribute. */}
                <input type="file" aria-label="The skill's folder" {...({ webkitdirectory: "", directory: "" } as object)} multiple onChange={(e) => void pick(e.currentTarget.files)} />
                <Text size={files ? "xs" : "sm"} c="dimmed">{files ? `${Object.keys(files).length} files` : "The folder that holds its SKILL.md."}</Text>
              </Stack>
            )}
            {error && <Alert color="red" role="alert">{error}</Alert>}
            <Group justify="flex-end">
              <Button variant="default" onClick={onClose}>Cancel</Button>
              <Button loading={busy} disabled={from === "git" ? !gitReady : !files} onClick={() => void readSkill()}>Read the skill</Button>
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

// What a locked skill's lock says, on its row and where its files are read.
export const LOCKED = "Locked: it ships with Muxxy and changes only by a pull request.";

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
      <Alert variant="light" color="blue" title="How a skill is used" data-testid="skills-how">
        <Text size="sm">
          A skill does nothing until a harness profile lists it. Every run on that profile is then told the skill's name and what it is for, and reads the rest when its work calls for it. Nobody types a command for it in a goal or a task. Nothing loads from a task's repo. A skill with a lock ships with Muxxy and changes only by a pull request.
        </Text>
        <Anchor href="/config?kind=profiles" size="sm">Choose which profiles load which skills</Anchor>
      </Alert>
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
                    {skill.source?.locked && (
                      <Tooltip label={LOCKED}>
                        <IconLock size={14} role="img" aria-label={`${name} is locked`} />
                      </Tooltip>
                    )}
                    {s.original.skills?.[name] === undefined && <Badge size="xs" color="yellow" variant="light" tt="none">not saved</Badge>}
                  </Group>
                  <Text size="xs" c="dimmed">{Object.keys(skill.files).length} files, {kb(skill.files)} KB</Text>
                </Table.Td>
                <Table.Td maw={420}><Text size="sm" lineClamp={3}>{skill.description}</Text></Table.Td>
                <Table.Td maw={260}><Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>{sourceLine(skill.source)}</Text></Table.Td>
                <Table.Td>
                  {users.length ? (
                    <Text size="sm">{users.join(", ")}</Text>
                  ) : (
                    <Stack gap={0}>
                      <Text size="sm" c="orange">No profile loads it, so no run uses it.</Text>
                      <Anchor href="/config?kind=profiles" size="sm">Add it to a profile</Anchor>
                    </Stack>
                  )}
                </Table.Td>
                <Table.Td>
                  {!s.readOnly && !skill.source?.locked && (
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
        <Empty>No skill yet. A skill is packaged instructions an agent loads for a kind of work. Import one above, from a git repository or from files, then name it on a harness profile.</Empty>
      )}
      {!s.readOnly && <RemovedRows what="skill" rows={removedEntries(s, "skills")} />}
      {!s.readOnly && (
        <Paper p="sm" withBorder>
          <Group justify="space-between">
            <Text size="sm">Import a skill from a git repository, or from a folder on this machine. Then add it to a profile, and that profile's runs load it.</Text>
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
            {skills[reading].source?.locked && (
              <Group gap={6} wrap="nowrap">
                <IconLock size={14} aria-hidden />
                <Text size="xs">{LOCKED} Muxxy runs its script and adds its writing rules to prompts.</Text>
              </Group>
            )}
            <SkillFiles skill={skills[reading]} />
          </Stack>
        </Modal>
      )}
    </Stack>
  );
}
