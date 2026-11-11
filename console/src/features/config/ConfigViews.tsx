import { Alert, Badge, Stack, Table, Text } from "@mantine/core";
import { InlineField } from "../../components/InlineField";
import { DataTable, Facts } from "../../components/ui";
import { isChanged, type Path } from "./staging";

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

export function TaskPolicyView({ policy, maxSandboxes, staging }: { policy: Record<string, any>; maxSandboxes: number | null; staging?: Staging }) {
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
            <Text size="xs" c="dimmed">{ceilingLine(maxSandboxes)}</Text>
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
          <InlineField
            kind="tags"
            label="Reviewers"
            value={doc.review?.reviewers ?? []}
            monospace
            placeholder="harness:<profile>/<engine>:<model>, Enter to add"
            changed={changed(s, ["review", "reviewers"])}
            reasons={s.reasons("reviewers")}
            readOnly={s.readOnly}
            onCommit={(v) => s.stage(["review", "reviewers"], v)}
          />,
        ],
      ]}
    />
  );
}

const TRACKERS = ["github"];

export function ReposView({ staging: s }: { staging: Staging }) {
  const repos: Record<string, any> = s.document.repos ?? {};
  return (
    <DataTable head={["Repo", "Tracker", "Default acceptance checks"]}>
      {Object.entries(repos).map(([name, r]) => (
        <Table.Tr key={name} data-testid={`repo-${name}`}>
          <Table.Td>
            <Text ff="monospace" size="sm">{name}</Text>
            <Reasons reasons={s.reasons(`repo:${name}`)} />
          </Table.Td>
          <Table.Td>
            <InlineField
              kind="select"
              label={`Tracker of ${name}`}
              value={r.tracker ?? "github"}
              data={TRACKERS.includes(r.tracker ?? "github") ? TRACKERS : [r.tracker, ...TRACKERS]}
              display={<Badge variant="light" tt="none">{r.tracker ?? "github"}</Badge>}
              changed={changed(s, ["repos", name, "tracker"])}
              reasons={s.reasons(`repo:${name}:tracker`)}
              readOnly={s.readOnly}
              onCommit={(v) => s.stage(["repos", name, "tracker"], v ?? "github")}
            />
          </Table.Td>
          <Table.Td>
            <InlineField
              kind="tags"
              label={`Default acceptance checks of ${name}`}
              value={r.default_checks ?? []}
              monospace
              changed={changed(s, ["repos", name, "default_checks"])}
              reasons={s.reasons(`repo:${name}:default_checks`)}
              readOnly={s.readOnly}
              onCommit={(v) => s.stage(["repos", name, "default_checks"], v)}
            />
          </Table.Td>
        </Table.Tr>
      ))}
    </DataTable>
  );
}
