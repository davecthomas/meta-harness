import { useState } from "react";
import { Alert, Button, Code, Group, JsonInput, List, Modal, Paper, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { IconDeviceFloppy, IconTrash } from "@tabler/icons-react";
import { describeChanges, lineDiff } from "./staging";

// The bar above a kind's view while changes are staged: how many, against which version, and the way to
// drop them or to review and save them as the next version.
export function StagedBar(props: { count: number; version: number; onDiscard: () => void; onReview: () => void }) {
  return (
    // Stays in view while the page scrolls, under the header: an edit far down the page is still plainly unsaved.
    <Paper withBorder p="sm" mb="md" data-testid="staged-bar" style={{ borderColor: "var(--mantine-color-yellow-5)", position: "sticky", top: 64, zIndex: 5, background: "var(--mantine-color-body)" }}>
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Stack gap={0}>
          <Text size="sm" fw={600}>
            {props.count} {props.count === 1 ? "change" : "changes"} not saved yet
          </Text>
          <Text size="sm" c="dimmed">Nothing applies until you review and save. They are changes to version {props.version}.</Text>
        </Stack>
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
  // Why the service refused the save, when it did: shown here, with the dialog still open on the changes.
  reasons?: string[];
}) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [json, setJson] = useState(false);
  const changes = describeChanges(props.before, props.after);
  const lines = lineDiff(JSON.stringify(props.before, null, 2), JSON.stringify(props.after, null, 2));
  return (
    <Modal opened onClose={props.onClose} title={`Save version ${props.version + 1}`} size="xl">
      <Stack gap="md">
        {(props.reasons ?? []).length > 0 && (
          <Alert color="red" title="Not saved: this version would be refused" data-testid="review-refused">
            <List size="sm" spacing={4}>{props.reasons!.map((r) => <List.Item key={r}>{r}</List.Item>)}</List>
            <Text size="sm" mt="xs">Go back to editing, fix each problem listed above, and save again. Your changes are still here.</Text>
          </Alert>
        )}
        <Text size="sm">What changes against version {props.version}, the active one:</Text>
        <ScrollArea.Autosize mah="35vh">
          <List size="sm" spacing={4} data-testid="review-changes">
            {changes.map((c) => <List.Item key={c}>{c}</List.Item>)}
          </List>
        </ScrollArea.Autosize>
        <div>
          <Button size="compact-xs" variant="subtle" onClick={() => setJson(!json)}>{json ? "Hide the JSON" : "Show the JSON"}</Button>
        </div>
        {json && (
          <ScrollArea.Autosize mah="35vh">
            <Code block>
              {lines
                .filter((l, i, all) => l.sign !== " " || all.slice(Math.max(0, i - 2), i + 3).some((x) => x.sign !== " "))
                .map((l) => `${l.sign} ${l.text}`)
                .join("\n")}
            </Code>
          </ScrollArea.Autosize>
        )}
        <TextInput label="Note" description="Shown in the version history." value={note} onChange={(e) => setNote(e.currentTarget.value)} />
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
export function JsonDialog(props: {
  title: string;
  document: Record<string, any>;
  // Why a parsed document can't be staged; empty when it can.
  check: (document: Record<string, any>) => string[];
  onClose: () => void;
  onApply: (document: Record<string, any>) => void;
}) {
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
    const problems = props.check(parsed as Record<string, any>);
    if (problems.length) {
      setError(`Not applied: ${problems.join("; ")}`);
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
