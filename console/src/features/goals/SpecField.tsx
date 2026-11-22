import { useEffect, useState } from "react";
import { Anchor, Checkbox, FileInput, Group, Loader, NativeSelect, Paper, SegmentedControl, Select, Stack, Text, TextInput, Title, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { api } from "../../api";
import { ChoiceField } from "../trackers/GoalTrackerFields";

// The most a specification holds, as the planner accepts it (planner/specs.py).
export const SPEC_MAX_CHARS = 200_000;

// A file's text. FileReader, since not every browser gives a file a text() of its own.
function read(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error(`${file.name} could not be read`));
    reader.readAsText(file);
  });
}

// A goal's specification, one of two ways: a document's text with its name, or an issue of the goal's tracker
// (a Jira epic), whose description is the specification.
export type GoalSpec = { name: string; text: string } | { ref: string };

type From = "repo" | "issue" | "upload";

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

// A document picked from one of the goal's repos: the repo's Markdown and text files are listed, the likeliest
// specifications first, and the one picked is read as the specification. When the list cannot be read, or the
// repo holds more files than it offers, the path is typed.
function RepoFile({ repos, onLoaded }: { repos: string[]; onLoaded: (spec: GoalSpec) => void }) {
  const [repo, setRepo] = useState(repos[0] ?? "");
  const [files, setFiles] = useState<string[] | null>(null);
  const [complete, setComplete] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [reading, setReading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const names = repos.join(",");

  // A repo the goal no longer names is no longer where its specification is picked.
  useEffect(() => {
    setRepo((now) => (repos.includes(now) ? now : repos[0] ?? ""));
  }, [names]);

  useEffect(() => {
    setFiles(null);
    setListError(null);
    setError(null);
    if (!repo) return;
    let stale = false;
    api.specFiles(repo).then(
      (found) => {
        if (stale) return;
        setFiles(found.files.map((f) => f.path));
        setComplete(found.complete);
      },
      (err) => !stale && setListError(message(err)),
    );
    return () => {
      stale = true;
    };
  }, [repo]);

  async function read(path: string) {
    if (!path.trim()) return;
    setError(null);
    setReading(path);
    try {
      const found = await api.specFileText(repo, path.trim());
      onLoaded({ name: found.path, text: found.text });
    } catch (err) {
      setError(message(err));
    } finally {
      setReading(null);
    }
  }

  if (!repos.length) return <Text size="sm" c="dimmed" data-testid="spec-needs-repo">Choose the goal's repository above first: the file is picked from it.</Text>;
  const typedPath = (
    <TextInput
      label={listError ? "File in the repo" : "Or its path"}
      description={listError ? undefined : "For a file the list does not show."}
      placeholder="docs/exports-prd.md"
      value={typed}
      onChange={(e) => setTyped(e.currentTarget.value)}
      onBlur={() => void read(typed)}
      onKeyDown={(e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        void read(typed);
      }}
    />
  );
  return (
    <Stack gap="sm">
      {repos.length > 1 && (
        <NativeSelect label="Repository" value={repo} onChange={(e) => setRepo(e.currentTarget.value)} data={repos} />
      )}
      {listError ? (
        <>
          {typedPath}
          <Text size="xs" c="orange">The repo's files could not be listed, so type the file's path exactly: {listError}</Text>
        </>
      ) : (
        <Select
          label="File in the repo"
          description="A Markdown or text file on the default branch: a PRD, a design document."
          searchable
          disabled={files === null || !!reading}
          placeholder={files === null ? "Reading the repo's files…" : files.length === 0 ? "This repo has no Markdown or text file" : "Choose a file, or type to search"}
          nothingFoundMessage="No file matches"
          value={null}
          onChange={(path) => path && void read(path)}
          data={files ?? []}
          maxDropdownHeight={280}
        />
      )}
      {!listError && files !== null && !complete && typedPath}
      {reading && <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Reading {reading}.</Text></Group>}
      {error && <Text size="sm" c="red" role="alert">{error}</Text>}
    </Stack>
  );
}

// Plan a goal from a specification, for work that has no issues yet. Its three sources, the likeliest first: a
// Markdown or text file in one of the goal's repos, an issue of the goal's tracker whose description is the
// specification (a Jira epic), or a file loaded from this machine. The planner breaks it down into tasks sized
// for one pull request each, and on approval they are created under one epic.
export function SpecField({ value, onChange, repos = [], tracker, project, asksProject = false, untracked = false, parentAsked = false, parent = "", onParent, open = false }: {
  value: GoalSpec | null;
  onChange: (spec: GoalSpec | null) => void;
  // The goal's repos, owner/name: where a file is picked from, and the scope of GitHub's issues.
  repos?: string[];
  // The goal's tracker, and its project where the tracker has projects: where the issue is picked from.
  tracker?: string;
  project?: string;
  asksProject?: boolean;
  // The goal has no tracker, so no issue to plan from.
  untracked?: boolean;
  // The tracker has parents (Jira): the issue picked here is also where the plan's tasks are created, and it
  // can be that without being the specification.
  parentAsked?: boolean;
  parent?: string;
  onParent?: (ref: string) => void;
  // The form asked for a spec, so the choices are shown without a click on the heading.
  open?: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState(false);
  const [from, setFrom] = useState<From>("repo");
  const fromIssue = !!value && "ref" in value;
  // Open once it holds something, so what the goal will be planned from is never out of sight.
  const shown = opened || open || !!value || !!parent;
  const loaded = value && "text" in value ? value : null;
  // The issue picked: the goal's parent where its tracker has parents, else the specification's own.
  const issue = parentAsked ? parent : fromIssue ? (value as { ref: string }).ref : "";
  const source: From = fromIssue || (parent && !loaded) ? "issue" : from;

  async function load(file: File | null) {
    setError(null);
    if (!file) return onChange(null);
    let text: string;
    try {
      text = await read(file);
    } catch {
      return setError(`${file.name} could not be read. Choose it again, or save it as plain text first.`);
    }
    if (!text.trim()) return setError(`${file.name} is empty. Choose the file that holds the specification.`);
    if (text.includes("\u0000")) return setError(`${file.name} is not a text file. Save the specification as Markdown or plain text, then choose that file.`);
    if (text.length > SPEC_MAX_CHARS) {
      return setError(`${file.name} is ${text.length.toLocaleString()} characters, and a specification holds ${SPEC_MAX_CHARS.toLocaleString()} at most. Split it into one file per goal.`);
    }
    onChange({ name: file.name, text });
  }

  function pick(ref: string) {
    if (parentAsked) onParent?.(ref);
    onChange(ref ? { ref } : null);
  }

  function choose(next: string) {
    setError(null);
    setFrom(next as From);
    // One source at a time: leaving the issue lets go of it, as the specification and as the tasks' parent.
    if (next !== "issue" && (fromIssue || parent)) pick("");
  }

  const scope = { ...(project ? { project } : {}), ...(repos.length ? { repos: repos.join(",") } : {}) };
  const waiting = asksProject ? (project ? undefined : "Choose the project above first") : repos.length ? undefined : "Choose a repository above first";

  return (
    <Paper maw={720} mb="lg" data-testid="spec-field">
      <Stack gap="sm">
        {/* Folded to one line until asked for, like the label helper beside it: the form below stays in view. */}
        <UnstyledButton onClick={() => setOpened((now) => !now)} aria-expanded={shown} aria-label="Plan a goal from a spec or an epic">
          <Group gap="sm" wrap="nowrap" align="flex-start">
            {shown ? <IconChevronDown size={18} /> : <IconChevronRight size={18} />}
            <div>
              <Title order={4}>Plan a goal from a spec or an epic</Title>
              <Text size="sm" c="dimmed">
                For work with no issues yet: the planner breaks a document into right-sized tasks under one epic.
              </Text>
            </div>
          </Group>
        </UnstyledButton>
        {shown && (
          <>
            {loaded ? (
              <Text size="sm" data-testid="spec-loaded">
                {loaded.name}, {loaded.text.length.toLocaleString()} characters.{" "}
                <Anchor component="button" type="button" size="sm" onClick={() => onChange(null)}>Remove</Anchor>
              </Text>
            ) : (
              <>
                <SegmentedControl
                  aria-label="Where the specification is"
                  value={source}
                  onChange={choose}
                  data={[
                    { value: "repo", label: "A file in the repo" },
                    ...(untracked ? [] : [{ value: "issue", label: "An epic or issue" }]),
                    { value: "upload", label: "Upload a file" },
                  ]}
                />
                {source === "repo" && <RepoFile repos={repos} onLoaded={onChange} />}
                {source === "issue" && tracker && (
                  <>
                    <ChoiceField
                      scheme={tracker}
                      name="parent"
                      label="Epic or issue"
                      help="Its description is the specification, and the plan's tasks are created under it."
                      required={false}
                      value={issue}
                      onChange={pick}
                      scope={scope}
                      waiting={waiting}
                    />
                    {parentAsked && parent && (
                      <Checkbox
                        label="Plan the goal from its description"
                        description="Off: the goal's tasks are created under it, and planned from the goal's own description."
                        checked={fromIssue}
                        onChange={(e) => onChange(e.currentTarget.checked ? { ref: parent } : null)}
                      />
                    )}
                  </>
                )}
                {source === "upload" && (
                  <FileInput
                    label="Specification file"
                    description="Markdown or plain text, from this machine."
                    placeholder="Choose a .md or .txt file"
                    accept=".md,.markdown,.txt,text/markdown,text/plain"
                    clearable
                    value={null}
                    onChange={(file) => void load(file)}
                  />
                )}
              </>
            )}
            {error && <Text size="sm" c="red" role="alert">{error}</Text>}
          </>
        )}
      </Stack>
    </Paper>
  );
}
