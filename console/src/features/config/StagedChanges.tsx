import { useState } from "react";
import { Button, Code, Group, JsonInput, Modal, Paper, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { IconDeviceFloppy, IconTrash } from "@tabler/icons-react";
import { lineDiff } from "./staging";

// The bar above a kind's view while changes are staged: how many, against which version, and the way to
// drop them or to review and save them as the next version.
export function StagedBar(props: { count: number; version: number; onDiscard: () => void; onReview: () => void }) {
  return (
    <Paper withBorder p="sm" mb="md" style={{ borderColor: "var(--mantine-color-yellow-5)" }}>
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Text size="sm" fw={500}>
          {props.count} {props.count === 1 ? "change" : "changes"} to version {props.version}
        </Text>
        <Group gap="xs">
          <Button size="xs" variant="default" leftSection={<IconTrash size={14} />} onClick={props.onDiscard}>Discard</Button>
          <Button size="xs" leftSection={<IconDeviceFloppy size={14} />} onClick={props.onReview}>Review and save</Button>
        </Group>
      </Group>
    </Paper>
  );
}

// The review before a save: the staged changes as a diff against the active version, and the note the
// version history shows.
export function ReviewDialog(props: {
  version: number;
  before: Record<string, any>;
  after: Record<string, any>;
  onClose: () => void;
  onSave: (note: string) => Promise<boolean>;
}) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const lines = lineDiff(JSON.stringify(props.before, null, 2), JSON.stringify(props.after, null, 2));
  return (
    <Modal opened onClose={props.onClose} title={`Save version ${props.version + 1}`} size="xl">
      <Stack gap="md">
        <Text size="sm">The changes against version {props.version}, the active one:</Text>
        <ScrollArea.Autosize mah="45vh">
          <Code block>
            {lines
              .filter((l, i, all) => l.sign !== " " || all.slice(Math.max(0, i - 2), i + 3).some((x) => x.sign !== " "))
              .map((l) => `${l.sign} ${l.text}`)
              .join("\n")}
          </Code>
        </ScrollArea.Autosize>
        <TextInput label="Note" description="Why you changed it. It shows in the version history." value={note} onChange={(e) => setNote(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={props.onClose}>Back to editing</Button>
          <Button
            leftSection={<IconDeviceFloppy size={16} />}
            loading={saving}
            onClick={async () => {
              setSaving(true);
              try {
                await props.onSave(note);
              } finally {
                setSaving(false);
              }
            }}
          >
            Save as version {props.version + 1}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// Advanced: the JSON of the active document with the staged changes. Applying it stages what it holds.
export function JsonDialog(props: { title: string; document: Record<string, any>; onClose: () => void; onApply: (document: Record<string, any>) => void }) {
  const [text, setText] = useState(JSON.stringify(props.document, null, 2));
  const [error, setError] = useState<string | null>(null);
  function apply() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      setError(`Not valid JSON: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setError("The document is a JSON object");
      return;
    }
    props.onApply(parsed as Record<string, any>);
    props.onClose();
  }
  return (
    <Modal opened onClose={props.onClose} title={props.title} size="xl">
      <Stack gap="md">
        <JsonInput
          label="JSON"
          description="Applying stages the changes with the rest; Review and save checks and saves them as one version."
          value={text}
          onChange={(v) => {
            setText(v);
            setError(null);
          }}
          error={error}
          formatOnBlur
          autosize
          minRows={12}
          maxRows={40}
          styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)", fontSize: 13 } }}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={props.onClose}>Cancel</Button>
          <Button onClick={apply}>Apply</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
