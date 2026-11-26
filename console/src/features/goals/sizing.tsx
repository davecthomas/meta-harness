// Issue sizing on the goal page: an issue's size and gaps on its card, the actions on one the planner held
// (Run anyway, Split), and the goal's strict switch. The planner sizes each issue before it becomes a task
// (src/meta_harness/planner/sizing.py): a large one is held to split, and one missing something runs with
// the gap shown, unless the goal is strict. A doubt, the model's word that the checks or paths an issue has
// fall short, is shown with its reason and holds nothing.

import { AiTyping, aiWriting } from "../../components/AiWriting";
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Group, Modal, Stack, Text, TextInput, Textarea, Tooltip } from "@mantine/core";
import { IconPlayerPlay, IconPlus, IconScissors, IconTrash } from "@tabler/icons-react";
import { api, type Goal, type IssueDoubt, type IssueGap, type IssueProgress, type IssueSize, type SplitDraft } from "../../api";
import { InlineSwitch } from "./InlineEdit";
import { SIGNED_ISSUES } from "./provenance";

const GAP_WORDS: Record<IssueGap, string> = {
  acceptance_checks: "acceptance checks",
  paths: "a Paths: line",
  testable_outcome: "a testable outcome",
};
const DOUBT_WORDS: Record<IssueDoubt["part"], string> = {
  acceptance_checks: "Its checks may not prove the fix",
  paths: "Its paths may not cover the change",
};
const SIZE_COLORS: Record<IssueSize, string> = { small: "teal", medium: "blue", large: "orange" };
// A body names its paths on a line such as "Paths: console/src, docs", as the planner reads it.
const PATHS_LINE = /^\s*paths:\s*\S/im;
// An item under a "## Acceptance checks" heading, as the planner reads checks from an issue.
const CHECK_ITEM = /^##\s+acceptance checks\s*$(?:\n(?!#).*)*?\n\s*[-*]\s+\S/im;

// What an issue lacks, in words: "Missing acceptance checks and a Paths: line".
export function gapText(missing: IssueGap[] | undefined): string | null {
  const words = (missing ?? []).map((m) => GAP_WORDS[m] ?? m);
  if (!words.length) return null;
  const list = words.length > 1 ? `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}` : words[0];
  return `Missing ${list}`;
}

// A doubt in words, with the model's reason: "Its checks may not prove the fix: they test the old path".
export function doubtText(doubt: IssueDoubt): string {
  const words = DOUBT_WORDS[doubt.part] ?? doubt.part;
  const reason = (doubt.reason ?? "").trim().replace(/\.$/, "");
  return reason ? `${words}: ${reason}` : words;
}

// The sizing's doubts on an issue's card, one line each.
export function SizingDoubts({ issue }: { issue: IssueProgress }) {
  const doubts = issue.doubts ?? [];
  if (!doubts.length) return null;
  return (
    <Stack gap={2} data-testid="issue-doubts">
      {doubts.map((d) => <Text key={d.part} size="xs" c="dimmed">{doubtText(d)}</Text>)}
    </Stack>
  );
}

export function SizeBadge({ issue }: { issue: IssueProgress }) {
  if (!issue.size) return null;
  const badge = <Badge size="xs" variant="light" color={SIZE_COLORS[issue.size]}>{issue.size}</Badge>;
  const reasons = issue.size_reasons ?? [];
  return reasons.length ? <Tooltip label={reasons.join("; ")} multiline maw={320}>{badge}</Tooltip> : badge;
}

function shortRef(ref: string): string {
  return ref.replace(/^[a-z]+:/, "");
}

// The drafts a Split starts from: one per suggested title, each saying where it came from, with the held issue's
// paths on a Paths: line and its checks, for the operator to edit.
export function splitDrafts(issue: IssueProgress): SplitDraft[] {
  const titles = issue.split?.length ? issue.split : [issue.title ?? shortRef(issue.ref)];
  const paths = issue.paths?.length ? issue.paths.join(", ") : "";
  const checks = issue.checks_named?.length ? `\n\n## Acceptance checks\n\n${issue.checks_named.map((c) => `- ${c}`).join("\n")}` : "";
  const checkList = checks || "\n\n## Acceptance checks\n\n- ";
  return titles.map((title) => ({ title, body: `What to change.\n\n## Outcome\n\nWhat a reviewer sees when it is done.\n\nPaths: ${paths}${checkList}` }));
}

// The suggested issues as drafts to edit, add to or drop, then create. Each carries the goal's label and needs a
// Paths: line.
export function SplitModal(props: {
  opened: boolean;
  onClose: () => void;
  goalId: string;
  label: string | null;
  issue: IssueProgress;
  onSplit: (created: string[]) => void;
}) {
  const [drafts, setDrafts] = useState<SplitDraft[]>(() => splitDrafts(props.issue));
  // The planner writes each part as a complete issue (what to change, its outcome, paths and checks); until it
  // answers, and if it cannot, the outline from the issue's suggested split stands, to fill in by hand.
  const [drafting, setDrafting] = useState(true);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.splitDrafts(props.goalId, props.issue.ref).then(
      (found) => live && found.drafts.length > 0 && setDrafts(found.drafts),
      (err) => live && setDraftError(err instanceof Error ? err.message : String(err)),
    ).finally(() => live && setDrafting(false));
    return () => {
      live = false;
    };
  }, [props.goalId, props.issue.ref]);
  const set = (n: number, fields: Partial<SplitDraft>) => setDrafts(drafts.map((d, i) => (i === n ? { ...d, ...fields } : d)));
  // What a part lacks to be an issue the planner runs, as the split itself checks.
  const lacks = (d: SplitDraft): string | null => {
    const missing = [!PATHS_LINE.test(d.body) && "a Paths: line", !CHECK_ITEM.test(d.body) && "an acceptance check under ## Acceptance checks"].filter(Boolean);
    return missing.length ? `Needs ${missing.join(" and ")}` : null;
  };
  const ok = !drafting && drafts.length > 0 && drafts.every((d) => d.title.trim() && !lacks(d));

  async function create() {
    setSaving(true);
    try {
      const made = await api.splitIssue(props.goalId, props.issue.ref, drafts.map((d) => ({ title: d.title.trim(), body: d.body })));
      setError(null);
      props.onSplit(made.created);
      props.onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal opened={props.opened} onClose={props.onClose} title={`Split ${shortRef(props.issue.ref)}`} size="xl">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Each issue is created in {props.issue.repo}
          {props.label ? <> with the label <Badge variant="outline" tt="none" radius="sm">{props.label}</Badge>, so the goal takes it in</> : ", and the goal takes it in"}.
          The issue you split runs as these parts, and closes itself once they are all done. {SIGNED_ISSUES}
        </Text>
        {drafting && <AiTyping>AI is drafting each part</AiTyping>}
        {draftError && <Alert color="orange" variant="light">The planner could not draft the parts: {draftError}. Fill each one in below.</Alert>}
        {drafts.map((d, n) => (
          <Stack key={n} gap={6} data-testid={`split-draft-${n}`}>
            <Group align="flex-end" gap="xs">
              <TextInput style={{ flex: 1 }} label={`Issue ${n + 1}`} maxLength={200} value={d.title} onChange={(e) => set(n, { title: e.currentTarget.value })} />
              <Button variant="subtle" color="red" aria-label={`Drop issue ${n + 1}`} onClick={() => setDrafts(drafts.filter((_, i) => i !== n))}>
                <IconTrash size={16} />
              </Button>
            </Group>
            <Textarea
              aria-label={`Body of issue ${n + 1}`}
              autosize
              minRows={4}
              maxRows={14}
              value={d.body}
              {...aiWriting(drafting, d.body)}
              error={drafting ? undefined : lacks(d) ?? undefined}
              onChange={(e) => set(n, { body: e.currentTarget.value })}
              styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)", fontSize: 13 } }}
            />
          </Stack>
        ))}
        <Group justify="space-between">
          <Button variant="default" leftSection={<IconPlus size={16} />} onClick={() => setDrafts([...drafts, { title: "", body: `What to change.\n\n## Outcome\n\nWhat a reviewer sees when it is done.\n\nPaths: \n\n## Acceptance checks\n\n- \n` }])}>
            Add an issue
          </Button>
          <Group gap="xs">
            <Button variant="default" onClick={props.onClose}>Cancel</Button>
            <Button disabled={!ok} loading={saving} onClick={() => void create()}>
              Create {drafts.length} issue{drafts.length === 1 ? "" : "s"}
            </Button>
          </Group>
        </Group>
        {error && <Alert color="red" variant="light">{error}</Alert>}
      </Stack>
    </Modal>
  );
}

// What a held issue's card offers: make it a task anyway, or split it into the suggested issues.
export function HeldActions(props: { goalId: string; label: string | null; issue: IssueProgress; readOnly?: boolean; onChanged: () => void }) {
  const [splitting, setSplitting] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!props.issue.held_reason || props.issue.task_id || props.readOnly) return null;
  // Already split: its parts run in its place and it closes when they are done. It offers nothing, since a
  // task of its own beside its parts would do the same work twice.
  const split = Boolean(props.issue.split_into?.length) || props.issue.held_reason.startsWith("split into");
  if (split) return null;

  async function runAnyway() {
    setRunning(true);
    try {
      await api.runAnyway(props.goalId, props.issue.ref);
      setError(null);
      props.onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <Stack gap={4} onClick={(e) => e.stopPropagation()}>
      <Group gap={6}>
        <Button size="compact-xs" variant="default" leftSection={<IconPlayerPlay size={12} />} loading={running} onClick={() => void runAnyway()}>
          Run anyway
        </Button>
        {!split && (
          <Button size="compact-xs" variant="default" color="orange" leftSection={<IconScissors size={12} />} onClick={() => setSplitting(true)}>
            Split
          </Button>
        )}
      </Group>
      {error && <Text size="xs" c="red">{error}</Text>}
      {splitting && (
        <SplitModal opened goalId={props.goalId} label={props.label} issue={props.issue} onClose={() => setSplitting(false)} onSplit={() => props.onChanged()} />
      )}
    </Stack>
  );
}

// The goal's strict switch: on, an issue missing acceptance checks, paths or a testable outcome is held, not run.
export function StrictIssuesEdit(props: { goal: Goal & { strict_issues?: boolean }; readOnly: boolean; save: (fields: { strict_issues: boolean }) => Promise<unknown> }) {
  return (
    <InlineSwitch
      label="Hold incomplete issues"
      checked={Boolean(props.goal.strict_issues)}
      onText="On: an issue without acceptance checks, file paths or a testable result waits for you to fix it"
      offText="Off: an issue missing any of these still runs, and its card says what is missing"
      readOnly={props.readOnly}
      save={(on) => props.save({ strict_issues: on })}
    />
  );
}
