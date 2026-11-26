// Created by the meta-harness in run t-8dd648910614:run-1 on harness:pi-careful/claude:claude-opus-5-5.

// Fix with AI on an issue the sizing marks as missing something or doubted: the planner drafts each marked part
// (POST /v1/goals/{id}/issues/fix-draft), the operator edits the draft beside what the issue has now, and Accept
// writes the edited parts to the issue (fix-accept), which is then sized again. Discard sends nothing.
// While the issue's task has not ended, its run has read the issue already: the card says so, both routes
// answer 409, and an accept refused so is kept by the planner. Once the task ends, fix-draft offers that kept
// draft for the next run, and Draft again asks the model anew (fresh).
// The routes are src/meta_harness/planner/fix_issue.py.

import { useRef, useState } from "react";
import { Alert, Button, Group, Modal, SimpleGrid, Stack, Text, Textarea } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";
import { api, ApiError, type FixDraft, type IssueGap, type IssueProgress, type SizingRecord } from "../../api";
import { doubtText } from "./sizing";

const PART_WORDS: Record<IssueGap, string> = {
  acceptance_checks: "Acceptance checks",
  paths: "Paths",
  testable_outcome: "Testable outcome",
};

// The issue as Fix with AI needs it: its ref, title, marks, and the paths and checks it names now.
export type FixableIssue = Pick<IssueProgress, "ref" | "title" | "state" | "missing" | "doubts" | "paths" | "checks_named"> &
  Partial<Pick<IssueProgress, "task_id" | "task_status">>;

// The task statuses the planner counts as ended (TASK_ENDED in fix_issue.py): only then does a change reach a run.
const TASK_ENDED = new Set(["closed", "failed", "canceled"]);

// The status of the issue's task when it has not ended, else null.
export function runningStatus(issue: FixableIssue): string | null {
  if (!issue.task_id || !issue.task_status) return null;
  return TASK_ENDED.has(issue.task_status) ? null : issue.task_status;
}

// Whether the sizing marks anything on the issue for Fix with AI to draft. A closed issue is not changed.
export function fixable(issue: FixableIssue): boolean {
  if (issue.state === "closed") return false;
  return (issue.missing ?? []).length > 0 || (issue.doubts ?? []).length > 0;
}

// The issue with the sizing fix-accept recorded, so its card shows the new size and marks at once.
export function withSizing<T extends FixableIssue & Partial<IssueProgress>>(issue: T, sizing: SizingRecord): T {
  return {
    ...issue,
    size: sizing.size,
    size_reasons: sizing.reasons ?? [],
    missing: sizing.missing ?? [],
    doubts: sizing.doubts ?? [],
    split: sizing.split ?? [],
    paths: sizing.paths ?? issue.paths,
    checks_named: sizing.checks ?? issue.checks_named,
  };
}

// What the issue has now for a part, to read beside the draft.
function currentText(issue: FixableIssue, part: IssueGap): string | null {
  if (part === "acceptance_checks" && issue.checks_named?.length) return issue.checks_named.map((c) => `- ${c}`).join("\n");
  if (part === "paths" && issue.paths?.length) return `Paths: ${issue.paths.join(", ")}`;
  return null;
}

function message(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  return err instanceof Error ? err.message : String(err);
}

function shortRef(ref: string): string {
  return ref.replace(/^[a-z]+:/, "");
}

// The Fix with AI button and its draft. onAccepted gets the issue's new sizing, or null when it was not sized again.
export function FixIssue(props: { goalId: string; issue: FixableIssue; onAccepted?: (sizing: SizingRecord | null) => void }) {
  const { issue } = props;
  const [drafting, setDrafting] = useState(false);
  const [drafts, setDrafts] = useState<FixDraft[] | null>(null);
  // The draft shown is the one an accept kept while the issue's task ran, not a new one.
  const [kept, setKept] = useState(false);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<string | null>(null);
  // A 409 for a draft: the issue's task is running, so there is nothing to draft for until it ends.
  const [later, setLater] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // A 409: the issue's task is running, so the draft waits until it ends.
  const [waiting, setWaiting] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  // Set while an accept is on its way, so a second click before the page redraws sends nothing.
  const sending = useRef(false);
  if (!fixable(issue) && !drafts) return done ? <Text size="xs" c="teal" data-testid="fix-done">{done}</Text> : null;
  const status = runningStatus(issue);

  // fresh: draft with the model again, past a kept draft.
  async function draft(fresh = false) {
    setDrafting(true);
    setFailed(null);
    setLater(null);
    setDone(null);
    try {
      const found = await api.fixDraft(props.goalId, issue.ref, fresh);
      setDrafts(found.drafts);
      setKept(Boolean(found.kept));
      setTexts(Object.fromEntries(found.drafts.map((d) => [d.part, d.text])));
      setWaiting(null);
      setSaveError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && !drafts) setLater(err.detail);
      else if (drafts) setSaveError(`Could not draft again: ${message(err)}. The draft shown is unchanged.`);
      else setFailed(`Fix with AI failed: ${message(err)}. Nothing was changed.`);
    } finally {
      setDrafting(false);
    }
  }

  function discard() {
    setDrafts(null);
    setKept(false);
    setTexts({});
    setWaiting(null);
    setSaveError(null);
  }

  // The parts the operator left text in; a part emptied is left out.
  const parts = (drafts ?? []).map((d) => ({ part: d.part, text: texts[d.part] ?? "" })).filter((p) => p.text.trim());

  async function accept() {
    if (sending.current || !parts.length) return;
    sending.current = true;
    setSaving(true);
    setWaiting(null);
    setSaveError(null);
    try {
      const made = await api.fixAccept(props.goalId, issue.ref, parts);
      setDrafts(null);
      setKept(false);
      setTexts({});
      setDone(
        `Added ${made.added.join(", ") || "nothing"} to the issue.` + (made.sizing_error ? ` ${made.sizing_error}.` : ""),
      );
      props.onAccepted?.(made.sizing);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setWaiting(err.detail);
      else setSaveError(`Could not accept: ${message(err)}`);
    } finally {
      sending.current = false;
      setSaving(false);
    }
  }

  return (
    // The card opens its task on a click or Enter; nothing here should, the draft's window included.
    <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <Stack gap={4}>
        <Group gap={6}>
          <Button size="compact-xs" variant="light" color="grape" leftSection={<IconSparkles size={12} />} loading={drafting} onClick={() => void draft()}>
            Fix with AI
          </Button>
        </Group>
        {status && !later && (
          <Text size="xs" c="orange" data-testid="fix-running">
            Its task is {status.replace(/_/g, " ")}, and its run has read the issue already. A draft accepted now is kept for the next run.
          </Text>
        )}
        {later && <Text size="xs" c="orange" data-testid="fix-later">{later.replace(/\.$/, "")}.</Text>}
        {failed && <Text size="xs" c="red" data-testid="fix-failed">{failed}</Text>}
        {done && <Text size="xs" c="teal" data-testid="fix-done">{done}</Text>}
      </Stack>
      {drafts && (
        <Modal opened onClose={discard} title={`Fix ${shortRef(issue.ref)} with AI`} size="xl">
          <Stack gap="md" data-testid="fix-draft">
            {issue.title && <Text size="sm" fw={500}>{issue.title}</Text>}
            {kept && (
              <Alert color="blue" variant="light" data-testid="fix-kept">
                <Group justify="space-between" gap="xs">
                  <Text size="sm">Saved while the task ran. Accept it to add it to the issue before the next run.</Text>
                  <Button size="compact-xs" variant="default" loading={drafting} onClick={() => void draft(true)}>Draft again</Button>
                </Group>
              </Alert>
            )}
            <Text size="sm" c="dimmed">
              Each part is added to the end of the issue's description under its heading. Edit or empty a part before you accept; an empty part is left out.
            </Text>
            {drafts.map((d) => {
              const now = currentText(issue, d.part);
              return (
                <Stack key={d.part} gap={4} data-testid={`fix-part-${d.part}`}>
                  <Text size="sm" fw={600}>{PART_WORDS[d.part] ?? d.part}, under "{d.heading}"</Text>
                  <Text size="xs" c="dimmed">{d.doubted ? doubtText({ part: d.part as "acceptance_checks" | "paths", reason: d.reason }) : "Missing from the issue"}</Text>
                  <SimpleGrid cols={2} spacing="sm">
                    <Stack gap={2}>
                      <Text size="xs" c="dimmed">The issue has</Text>
                      <Text size="sm" ff="monospace" style={{ whiteSpace: "pre-wrap" }} data-testid={`fix-now-${d.part}`}>{now ?? "Nothing"}</Text>
                    </Stack>
                    <Textarea
                      label="Draft"
                      aria-label={`Draft of ${PART_WORDS[d.part] ?? d.part}`}
                      autosize
                      minRows={3}
                      maxRows={12}
                      value={texts[d.part] ?? ""}
                      onChange={(e) => {
                        const value = e.currentTarget.value;
                        setTexts((all) => ({ ...all, [d.part]: value }));
                      }}
                      styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)", fontSize: 13 } }}
                    />
                  </SimpleGrid>
                </Stack>
              );
            })}
            {waiting && (
              <Alert color="orange" variant="light" data-testid="fix-waiting">
                {waiting.replace(/\.$/, "")}.
              </Alert>
            )}
            {saveError && <Alert color="red" variant="light" data-testid="fix-save-error">{saveError}</Alert>}
            <Group justify="flex-end" gap="xs">
              <Button variant="default" onClick={discard}>Discard</Button>
              <Button disabled={!parts.length} loading={saving} onClick={() => void accept()}>Accept</Button>
            </Group>
          </Stack>
        </Modal>
      )}
    </div>
  );
}
