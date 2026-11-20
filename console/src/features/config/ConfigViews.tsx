import { Alert, Stack, Text } from "@mantine/core";
import { InlineField } from "../../components/InlineField";
import { Facts } from "../../components/ui";
import { isChanged, type Path } from "./staging";
import { ReviewersField } from "./ReviewersField";

// A kind's view edits in place against a staged copy of its document. Every commit stages; nothing saves
// until the operator reviews the changes and saves them as one version.
export interface Staging {
  // The document with the staged changes.
  document: Record<string, any>;
  // The active version's document, which the changes are against.
  original: Record<string, any>;
  // Stages a value at a path; undefined removes it.
  stage: (path: Path, value: unknown) => void;
  // A refused save's reasons about one target (see reasonTarget).
  reasons: (target: string) => string[];
  // Shows the document without a way to change it.
  readOnly?: boolean;
}

// A view of a document that stages nothing.
export function viewOnly(document: Record<string, any>): Staging {
  return { document, original: document, stage: () => undefined, reasons: () => [], readOnly: true };
}

export function changed(s: Staging, path: Path): boolean {
  return isChanged(s.original, s.document, path);
}

// Staged as one value: an empty list or text removes the key, so the document says nothing rather than "".
export function stageOptional(s: Staging, path: Path, value: unknown) {
  const empty = value === null || value === "" || (Array.isArray(value) && value.length === 0);
  s.stage(path, empty ? undefined : value);
}

export function Reasons({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null;
  return (
    <Alert color="red" p="xs">
      <Stack gap={2}>{reasons.map((r) => <Text key={r} size="xs">{r}</Text>)}</Stack>
    </Alert>
  );
}

// The line under the parallel limit: the VM's ceiling, which a save above it is refused by.
function ceilingLine(maxSandboxes: number | null): string {
  return maxSandboxes
    ? `At most ${maxSandboxes}: the deployment's VM holds ${maxSandboxes} sandboxes. A goal can hold itself to fewer.`
    : "This deployment sets no ceiling (MH_MAX_SANDBOXES). A goal can hold itself to fewer.";
}

// profiles and models: the harness profiles and models the configuration has, which a reviewer is picked from.
export function TaskPolicyView({ policy, maxSandboxes, staging, profiles, models }: { policy: Record<string, any>; maxSandboxes: number | null; staging?: Staging; profiles?: string[]; models?: string[] }) {
  const s = staging ?? viewOnly(policy);
  const doc = s.document;
  return (
    <Facts
      items={[
        [
          "Parallel tasks",
          <Stack gap={2}>
            <InlineField
              kind="number"
              label="Parallel tasks"
              value={doc.max_parallel ?? null}
              min={1}
              max={maxSandboxes ?? undefined}
              decimalScale={0}
              display={<Text size="sm" fw={600}>{doc.max_parallel}, across every goal</Text>}
              changed={changed(s, ["max_parallel"])}
              reasons={s.reasons("max_parallel")}
              readOnly={s.readOnly}
              onCommit={(v) => s.stage(["max_parallel"], v)}
            />
            <Text size="sm" c="dimmed">{ceilingLine(maxSandboxes)}</Text>
          </Stack>,
        ],
        [
          "Budget policy",
          <Stack gap={2}>
            <InlineField
              kind="switch"
              label="Budget policy"
              value={doc.budget_policy !== "track"}
              switchLabel={doc.budget_policy !== "track" ? "Stop at budgets and spend limits" : "Track spend only"}
              changed={changed(s, ["budget_policy"])}
              reasons={s.reasons("budget_policy")}
              readOnly={s.readOnly}
              onCommit={(stop) => s.stage(["budget_policy"], stop ? "block" : "track")}
            />
            <Text size="sm" c="dimmed">
              {doc.budget_policy !== "track"
                ? "A goal's budget and a task's spend limit stop work, as each says."
                : "No goal or task is held, refused or ended for its budget or spend limit. Spend is still recorded and shown against them. A task takes this when it starts; one already running keeps its limit."}
            </Text>
          </Stack>,
        ],
        [
          "Writing pass",
          <Stack gap={2}>
            <InlineField
              kind="switch"
              label="Writing pass"
              value={doc.writing_pass !== false}
              switchLabel={doc.writing_pass !== false ? "Check what is posted for people to read" : "Post what a run wrote as it is"}
              changed={changed(s, ["writing_pass"])}
              reasons={s.reasons("writing_pass")}
              readOnly={s.readOnly}
              onCommit={(on) => s.stage(["writing_pass"], on ? undefined : false)}
            />
            <Text size="sm" c="dimmed">
              {doc.writing_pass !== false
                ? "A pull request description, a review, a comment and a plan's issue go through the writing skill's scanner before they are posted. One with findings gets one rewrite, kept only when it is clean and keeps every heading, link, code span and number."
                : "Nothing is scanned or rewritten before it is posted. Runs are still told to apply the writing skill themselves."}
            </Text>
          </Stack>,
        ],
        [
          "Credential redaction",
          <Stack gap={2}>
            <InlineField
              kind="switch"
              label="Credential redaction"
              value={doc.credential_redaction === true}
              switchLabel={doc.credential_redaction === true ? "Replace credentials in what is posted" : "Post text as it is"}
              changed={changed(s, ["credential_redaction"])}
              reasons={s.reasons("credential_redaction")}
              readOnly={s.readOnly}
              onCommit={(on) => s.stage(["credential_redaction"], on ? true : undefined)}
            />
            <Text size="sm" c="dimmed">
              {doc.credential_redaction === true
                ? "Access tokens, API keys, private keys, passwords in a URL and this service's own secrets become [redacted] before a text is posted. Personal data is not looked for: names, email addresses and phone numbers stay as written."
                : "Off. Nothing is replaced in what is posted. Turned on, it replaces tokens, keys and passwords. It never looks for personal data."}
            </Text>
          </Stack>,
        ],
        [
          "Fix rounds",
          <InlineField
            kind="number"
            label="Fix rounds"
            value={doc.review?.max_rounds ?? null}
            min={0}
            decimalScale={0}
            display={<Text size="sm">Up to {doc.review?.max_rounds} before a pull request waits for an operator</Text>}
            changed={changed(s, ["review", "max_rounds"])}
            reasons={s.reasons("max_rounds")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["review", "max_rounds"], v)}
          />,
        ],
        [
          "Stronger retry",
          <InlineField
            kind="number"
            label="Stronger retry"
            value={doc.remedies?.retry_stronger ?? 1}
            min={0}
            max={3}
            decimalScale={0}
            display={
              <Text size="sm">
                {(doc.remedies?.retry_stronger ?? 1) === 0
                  ? "Off: a task that fails on its own waits for you"
                  : `A task that fails on its own runs again on the complex tier, up to ${doc.remedies?.retry_stronger ?? 1} time${(doc.remedies?.retry_stronger ?? 1) === 1 ? "" : "s"}, before it waits for you`}
              </Text>
            }
            changed={changed(s, ["remedies", "retry_stronger"])}
            reasons={s.reasons("retry_stronger")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["remedies", "retry_stronger"], v)}
          />,
        ],
        [
          "Another set of fix rounds",
          <InlineField
            kind="number"
            label="Another set of fix rounds"
            value={doc.remedies?.fix_again ?? 1}
            min={0}
            max={3}
            decimalScale={0}
            display={
              <Text size="sm">
                {(doc.remedies?.fix_again ?? 1) === 0
                  ? "Off: a review its fixes did not settle waits for you"
                  : `A review its fixes did not settle gets another set of fix rounds on the complex tier, at most ${doc.remedies?.fix_again ?? 1} time${(doc.remedies?.fix_again ?? 1) === 1 ? "" : "s"}. Then it waits for you.`}
              </Text>
            }
            changed={changed(s, ["remedies", "fix_again"])}
            reasons={s.reasons("fix_again")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["remedies", "fix_again"], v)}
          />,
        ],
        [
          "Spend limit raise",
          <InlineField
            kind="number"
            label="Spend limit raise"
            value={doc.remedies?.raise_limit ?? 1}
            min={0}
            max={3}
            decimalScale={0}
            display={
              <Text size="sm">
                {(doc.remedies?.raise_limit ?? 1) === 0
                  ? "Off: a task at its spend limit waits for you"
                  : `A task at its own spend limit is raised to what its next run needs, ${doc.remedies?.raise_limit ?? 1} time${(doc.remedies?.raise_limit ?? 1) === 1 ? "" : "s"}, before it waits for you. A raise goes up to twice the limit and stays within the goal's budget. A goal's budget is never raised for you.`}
              </Text>
            }
            changed={changed(s, ["remedies", "raise_limit"])}
            reasons={s.reasons("raise_limit")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["remedies", "raise_limit"], v)}
          />,
        ],
        [
          "Split large issues",
          <InlineField
            kind="number"
            label="Split large issues"
            value={doc.remedies?.auto_split ?? 1}
            min={0}
            max={3}
            decimalScale={0}
            display={
              <Text size="sm">
                {(doc.remedies?.auto_split ?? 1) === 0
                  ? "Off: an issue too large for one pull request waits for you to split it or run it as one"
                  : `An issue too large for one pull request is split by the planner into complete parts, up to ${doc.remedies?.auto_split ?? 1} tr${(doc.remedies?.auto_split ?? 1) === 1 ? "y" : "ies"}, before it waits for you`}
              </Text>
            }
            changed={changed(s, ["remedies", "auto_split"])}
            reasons={s.reasons("auto_split")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["remedies", "auto_split"], v)}
          />,
        ],
        [
          "Re-run failed checks",
          <InlineField
            kind="number"
            label="Re-run failed checks"
            value={doc.remedies?.rerun_checks ?? 1}
            min={0}
            max={3}
            decimalScale={0}
            display={
              <Text size="sm">
                {(doc.remedies?.rerun_checks ?? 1) === 0
                  ? "Off: a failed check goes straight to a fix run"
                  : `A pull request's failed GitHub Actions jobs are re-run ${doc.remedies?.rerun_checks ?? 1} time${(doc.remedies?.rerun_checks ?? 1) === 1 ? "" : "s"} first, so one that failed by chance costs no fix run`}
              </Text>
            }
            changed={changed(s, ["remedies", "rerun_checks"])}
            reasons={s.reasons("rerun_checks")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["remedies", "rerun_checks"], v)}
          />,
        ],
        [
          "Wait before a fix run",
          <InlineField
            kind="number"
            label="Wait before a fix run"
            value={doc.review?.hold_minutes ?? 0}
            min={0}
            decimalScale={0}
            suffix=" minutes"
            display={
              <Text size="sm">
                {doc.review?.hold_minutes
                  ? `${doc.review.hold_minutes} minutes: a review that asks for changes waits for an operator to dismiss findings`
                  : "None: findings go straight to the fix run"}
              </Text>
            }
            changed={changed(s, ["review", "hold_minutes"])}
            reasons={s.reasons("hold_minutes")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["review", "hold_minutes"], v ?? 0)}
          />,
        ],
        [
          "Reviewers, in order",
          <ReviewersField staging={s} profiles={profiles ?? []} models={models ?? []} />,
        ],
      ]}
    />
  );
}
