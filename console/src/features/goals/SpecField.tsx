import { useState } from "react";
import { Anchor, Checkbox, FileInput, Group, Paper, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";

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

// A goal's specification, one of two ways: a document's text with its name, or the goal's parent issue, whose
// description is the specification.
export type GoalSpec = { name: string; text: string } | { ref: string };

// Plan a goal from a specification, for work that has no issues yet: a Markdown or text file loaded here, or
// the description of the issue the goal names as its parent (a Jira epic). The planner breaks it down into
// tasks sized for one pull request each, and on approval they are created under one epic.
export function SpecField({ value, onChange, parent, parentLabel }: {
  value: GoalSpec | null;
  onChange: (spec: GoalSpec | null) => void;
  // The issue the goal names as its parent, by ref, when its tracker has parents and one is chosen.
  parent?: string;
  // How that issue is shown: its key.
  parentLabel?: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState(false);
  // Open once it holds something, so what the goal will be planned from is never out of sight.
  const shown = opened || !!value;
  const loaded = value && "text" in value ? value : null;
  const fromParent = !!value && "ref" in value;

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

  return (
    <Paper maw={720} mb="lg" data-testid="spec-field">
      <Stack gap="sm">
        {/* Folded to one line until asked for, like the label helper above it: the form below stays in view. */}
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
        {!fromParent && (
          <FileInput
            label="Specification file"
            description="Markdown or plain text: a PRD, a design document."
            placeholder="Choose a .md or .txt file"
            accept=".md,.markdown,.txt,text/markdown,text/plain"
            clearable
            value={null}
            onChange={(file) => void load(file)}
          />
        )}
        {loaded && (
          <Text size="sm" data-testid="spec-loaded">
            {loaded.name}, {loaded.text.length.toLocaleString()} characters.{" "}
            <Anchor component="button" type="button" size="sm" onClick={() => onChange(null)}>Remove</Anchor>
          </Text>
        )}
        {parent && !loaded && (
          <Checkbox
            label={`Use ${parentLabel ?? parent}'s description as the specification`}
            description="Its tasks are created under that issue, so no second epic is made."
            checked={fromParent}
            onChange={(e) => onChange(e.currentTarget.checked ? { ref: parent } : null)}
          />
        )}
        {error && <Text size="sm" c="red" role="alert">{error}</Text>}
          </>
        )}
      </Stack>
    </Paper>
  );
}
